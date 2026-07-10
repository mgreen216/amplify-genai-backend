import json
import time
import requests
from jose import jwt
import os

ALGORITHMS = ["RS256"]

# Module-level JWKS cache — persists across warm Lambda invocations
_jwks_cache = None
_jwks_cache_time = 0
_JWKS_CACHE_TTL = 3600  # 1 hour


def _get_jwks(jwks_url):
    """Fetch JWKS with caching. Reuses cached keys for up to 1 hour."""
    global _jwks_cache, _jwks_cache_time
    now = time.time()
    if _jwks_cache and (now - _jwks_cache_time) < _JWKS_CACHE_TTL:
        return _jwks_cache
    _jwks_cache = requests.get(jwks_url, timeout=5).json()
    _jwks_cache_time = now
    return _jwks_cache


def lambda_handler(event, context):
    """
    Lambda authorizer for validating Cognito JWT tokens
    """
    try:
        # Extract token from Authorization header
        token = event['authorizationToken']
        if token.lower().startswith('bearer '):
            token = token[7:]  # Remove "Bearer " prefix

        # Get Cognito configuration from environment
        cognito_issuer = os.environ.get('COGNITO_ISSUER', 'https://cognito-idp.us-east-1.amazonaws.com/us-east-1_PgwOR439P')

        # Fetch JWKS from Cognito (cached for 1 hour)
        jwks_url = f'{cognito_issuer}/.well-known/jwks.json'
        jwks = _get_jwks(jwks_url)
        
        # Get the key ID from the token header
        header = jwt.get_unverified_header(token)
        rsa_key = None
        
        for key in jwks["keys"]:
            if key["kid"] == header["kid"]:
                rsa_key = {
                    "kty": key["kty"],
                    "kid": key["kid"],
                    "use": key["use"],
                    "n": key["n"],
                    "e": key["e"]
                }
                break
        
        if not rsa_key:
            raise Exception('Unauthorized: Unable to find appropriate key')
        
        # Decode and verify the token
        payload = jwt.decode(
            token,
            rsa_key,
            algorithms=ALGORITHMS,
            issuer=cognito_issuer
        )
        
        # Extract principal ID (username)
        principal_id = payload.get('cognito:username', payload.get('username', payload.get('sub')))
        
        # Generate IAM policy
        policy = generate_policy(principal_id, 'Allow', event['methodArn'], payload)
        
        return policy
        
    except jwt.ExpiredSignatureError:
        raise Exception('Unauthorized: Token has expired')
    except jwt.InvalidTokenError as e:
        raise Exception(f'Unauthorized: Invalid token - {str(e)}')
    except Exception as e:
        print(f"Authorization error: {str(e)}")
        raise Exception('Unauthorized')

def generate_policy(principal_id, effect, resource, context=None):
    """
    Generate IAM policy for API Gateway
    """
    auth_response = {
        'principalId': principal_id
    }
    
    if effect and resource:
        policy_document = {
            'Version': '2012-10-17',
            'Statement': [
                {
                    'Action': 'execute-api:Invoke',
                    'Effect': effect,
                    'Resource': resource.split('/')[0] + '/*'  # Allow all methods on the API
                }
            ]
        }
        auth_response['policyDocument'] = policy_document
    
    # Add context with user claims
    if context:
        auth_response['context'] = {
            'username': str(context.get('cognito:username', context.get('username', ''))),
            'email': str(context.get('email', '')),
            'sub': str(context.get('sub', '')),
            'groups': json.dumps(context.get('cognito:groups', [])) if context.get('cognito:groups') else '[]'
        }
    
    return auth_response