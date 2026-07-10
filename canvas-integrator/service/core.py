import json
import os
import boto3
import logging
import uuid
import time
from typing import Dict, Any, Optional
from urllib.parse import urlencode
from cryptography.fernet import Fernet
from botocore.exceptions import ClientError

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
secrets_client = boto3.client('secretsmanager')
dynamodb = boto3.resource('dynamodb')
kms_client = boto3.client('kms')

# Environment variables
STAGE = os.environ.get('STAGE', 'dev')
REGION = os.environ.get('REGION', 'us-east-1')
OAUTH_TABLE_NAME = os.environ.get('OAUTH_TABLE_NAME', f'{STAGE}-canvas-oauth-tokens')
SECRET_NAME = os.environ.get('SECRET_NAME', f'{STAGE}-canvas-oauth-credentials')

class CanvasOAuthManager:
    def __init__(self):
        self.oauth_table = dynamodb.Table(OAUTH_TABLE_NAME)
        self.oauth_credentials = self._load_oauth_credentials()
        self.encryption_key = self._get_or_create_encryption_key()
        self.fernet = Fernet(self.encryption_key)
        
    def _load_oauth_credentials(self) -> Dict[str, str]:
        """Load Canvas OAuth credentials from AWS Secrets Manager"""
        try:
            response = secrets_client.get_secret_value(SecretId=SECRET_NAME)
            return json.loads(response['SecretString'])
        except ClientError as e:
            if e.response['Error']['Code'] == 'ResourceNotFoundException':
                # Create default secret structure
                default_secret = {
                    "canvas_client_id": "REPLACE_WITH_CANVAS_CLIENT_ID",
                    "canvas_client_secret": "REPLACE_WITH_CANVAS_CLIENT_SECRET",
                    "canvas_base_url": "https://canvas.instructure.com",
                    "redirect_uri": f"https://hfu-amplify.org/api/canvas/oauth/callback",
                    "encryption_key": Fernet.generate_key().decode()
                }
                secrets_client.create_secret(
                    Name=SECRET_NAME,
                    SecretString=json.dumps(default_secret)
                )
                logger.info(f"Created new secret: {SECRET_NAME}")
                return default_secret
            else:
                logger.error(f"Error loading OAuth credentials: {str(e)}")
                raise
                
    def _get_or_create_encryption_key(self) -> bytes:
        """Get or create encryption key for token storage"""
        key = self.oauth_credentials.get('encryption_key')
        if not key:
            # Generate and update the secret
            key = Fernet.generate_key().decode()
            self.oauth_credentials['encryption_key'] = key
            secrets_client.update_secret(
                SecretId=SECRET_NAME,
                SecretString=json.dumps(self.oauth_credentials)
            )
        return key.encode()
    
    def initiate_oauth(self, user_id: str, state: Optional[str] = None) -> Dict[str, str]:
        """Initiate Canvas OAuth flow"""
        if state is None:
            state = str(uuid.uuid4())
            
        # Store state in DynamoDB for validation
        self.oauth_table.put_item(
            Item={
                'user_id': f"STATE#{state}",
                'state': state,
                'actual_user_id': user_id,
                'ttl': int(time.time()) + 3600  # 1 hour TTL
            }
        )
        
        # Build OAuth authorization URL
        params = {
            'client_id': self.oauth_credentials['canvas_client_id'],
            'response_type': 'code',
            'redirect_uri': self.oauth_credentials['redirect_uri'],
            'state': state,
            'scope': 'url:GET|/api/v1/*'  # Read access to Canvas API
        }
        
        auth_url = f"{self.oauth_credentials['canvas_base_url']}/login/oauth2/auth?{urlencode(params)}"
        
        return {
            'auth_url': auth_url,
            'state': state
        }
    
    def complete_oauth(self, code: str, state: str) -> Dict[str, Any]:
        """Complete OAuth flow and store encrypted tokens"""
        import requests
        
        # Validate state
        response = self.oauth_table.get_item(Key={'user_id': f"STATE#{state}"})
        if 'Item' not in response:
            raise ValueError("Invalid state parameter")
            
        state_data = response['Item']
        user_id = state_data['actual_user_id']
        
        # Delete state record
        self.oauth_table.delete_item(Key={'user_id': f"STATE#{state}"})
        
        # Exchange code for token
        token_url = f"{self.oauth_credentials['canvas_base_url']}/login/oauth2/token"
        token_data = {
            'grant_type': 'authorization_code',
            'client_id': self.oauth_credentials['canvas_client_id'],
            'client_secret': self.oauth_credentials['canvas_client_secret'],
            'redirect_uri': self.oauth_credentials['redirect_uri'],
            'code': code
        }
        
        response = requests.post(token_url, data=token_data)
        response.raise_for_status()
        
        token_response = response.json()
        access_token = token_response['access_token']
        refresh_token = token_response.get('refresh_token', '')
        
        # Encrypt tokens
        encrypted_access = self.fernet.encrypt(access_token.encode()).decode()
        encrypted_refresh = self.fernet.encrypt(refresh_token.encode()).decode() if refresh_token else ''
        
        # Store encrypted tokens
        self.oauth_table.put_item(
            Item={
                'user_id': user_id,
                'access_token': encrypted_access,
                'refresh_token': encrypted_refresh,
                'canvas_user_id': token_response.get('user', {}).get('id', ''),
                'expires_at': int(time.time()) + token_response.get('expires_in', 3600),
                'created_at': int(time.time()),
                'canvas_base_url': self.oauth_credentials['canvas_base_url']
            }
        )
        
        return {
            'success': True,
            'user_id': user_id,
            'canvas_connected': True
        }
    
    def get_token(self, user_id: str) -> Optional[str]:
        """Retrieve and decrypt access token for user"""
        try:
            response = self.oauth_table.get_item(Key={'user_id': user_id})
            if 'Item' not in response:
                return None
                
            item = response['Item']
            
            # Check if token is expired
            if int(item.get('expires_at', 0)) < int(time.time()):
                # TODO: Implement token refresh
                logger.warning(f"Token expired for user {user_id}")
                return None
                
            # Decrypt and return token
            encrypted_token = item['access_token']
            return self.fernet.decrypt(encrypted_token.encode()).decode()
            
        except Exception as e:
            logger.error(f"Error retrieving token for user {user_id}: {str(e)}")
            return None
    
    def delete_tokens(self, user_id: str) -> bool:
        """Delete user's Canvas tokens"""
        try:
            self.oauth_table.delete_item(Key={'user_id': user_id})
            return True
        except Exception as e:
            logger.error(f"Error deleting tokens for user {user_id}: {str(e)}")
            return False
    
    def get_status(self, user_id: str) -> Dict[str, Any]:
        """Check Canvas connection status for user"""
        try:
            response = self.oauth_table.get_item(Key={'user_id': user_id})
            if 'Item' in response:
                item = response['Item']
                return {
                    'connected': True,
                    'canvas_user_id': item.get('canvas_user_id', ''),
                    'expires_at': item.get('expires_at', 0),
                    'canvas_base_url': item.get('canvas_base_url', '')
                }
            else:
                return {'connected': False}
        except Exception as e:
            logger.error(f"Error checking status for user {user_id}: {str(e)}")
            return {'connected': False, 'error': str(e)}


def _get_user_id(event: Dict) -> str:
    """Extract user ID from Lambda event"""
    return event.get('requestContext', {}).get('authorizer', {}).get('principalId', 'unknown')


def oauth_initiate(event, context):
    """Lambda handler for initiating OAuth flow"""
    try:
        user_id = _get_user_id(event)
        manager = CanvasOAuthManager()
        result = manager.initiate_oauth(user_id)
        
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps(result)
        }
    except Exception as e:
        logger.error(f"OAuth initiate error: {str(e)}")
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'error': str(e)
            })
        }


def oauth_callback(event, context):
    """Lambda handler for OAuth callback"""
    try:
        query_params = event.get('queryStringParameters', {})
        code = query_params.get('code')
        state = query_params.get('state')
        error = query_params.get('error')
        
        if error:
            # User denied authorization
            return {
                'statusCode': 302,
                'headers': {
                    'Location': 'https://hfu-amplify.org/settings?canvas_error=denied'
                }
            }
        
        if not code or not state:
            return {
                'statusCode': 400,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps({
                    'error': 'Missing code or state parameter'
                })
            }
        
        manager = CanvasOAuthManager()
        result = manager.complete_oauth(code, state)
        
        # Redirect back to app with success
        return {
            'statusCode': 302,
            'headers': {
                'Location': 'https://hfu-amplify.org/settings?canvas_connected=true'
            }
        }
        
    except Exception as e:
        logger.error(f"OAuth callback error: {str(e)}")
        return {
            'statusCode': 302,
            'headers': {
                'Location': f'https://hfu-amplify.org/settings?canvas_error={str(e)}'
            }
        }


def get_status(event, context):
    """Lambda handler for checking Canvas connection status"""
    try:
        user_id = _get_user_id(event)
        manager = CanvasOAuthManager()
        status = manager.get_status(user_id)
        
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps(status)
        }
    except Exception as e:
        logger.error(f"Status check error: {str(e)}")
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'error': str(e)
            })
        }


def disconnect(event, context):
    """Lambda handler for disconnecting Canvas"""
    try:
        user_id = _get_user_id(event)
        manager = CanvasOAuthManager()
        success = manager.delete_tokens(user_id)
        
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'success': success,
                'message': 'Canvas disconnected successfully' if success else 'Failed to disconnect'
            })
        }
    except Exception as e:
        logger.error(f"Disconnect error: {str(e)}")
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'error': str(e)
            })
        }