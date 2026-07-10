import json
import os
import boto3
import logging
from typing import Dict, Any, Optional
import requests
from datetime import datetime
import time

# Import provider-specific clients
from botocore.exceptions import ClientError

# Import caching layer
try:
    from .cache import llm_cache
except ImportError:
    logger.warning("Cache module not available, running without caching")
    llm_cache = None

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
secrets_client = boto3.client('secretsmanager')
bedrock_client = boto3.client('bedrock-runtime')
dynamodb = boto3.resource('dynamodb')

# Environment variables
STAGE = os.environ.get('STAGE', 'dev')
REGION = os.environ.get('REGION', 'us-east-1')
SECRET_NAME = os.environ.get('SECRET_NAME', f'{STAGE}-llm-api-keys')
USAGE_TABLE_NAME = f"{STAGE}-usage"

class LLMRouter:
    def __init__(self):
        self.api_keys = self._load_api_keys()
        self.usage_table = dynamodb.Table(USAGE_TABLE_NAME)
        
    def _load_api_keys(self) -> Dict[str, str]:
        """Load API keys from AWS Secrets Manager"""
        try:
            response = secrets_client.get_secret_value(SecretId=SECRET_NAME)
            return json.loads(response['SecretString'])
        except ClientError as e:
            logger.error(f"Error loading API keys: {str(e)}")
            return {}
    
    def _track_usage(self, user_id: str, provider: str, model: str, tokens: int):
        """Track usage in DynamoDB"""
        try:
            timestamp = datetime.utcnow().isoformat()
            self.usage_table.put_item(
                Item={
                    'pk': f"USER#{user_id}",
                    'sk': f"USAGE#{timestamp}",
                    'provider': provider,
                    'model': model,
                    'tokens': tokens,
                    'timestamp': timestamp
                }
            )
        except Exception as e:
            logger.error(f"Error tracking usage: {str(e)}")
    
    def _check_canvas_context(self, messages: list, user_id: str) -> Optional[Dict[str, Any]]:
        """Check if Canvas context is needed and get MCP configuration"""
        # Keywords that suggest Canvas-related questions
        canvas_keywords = ['assignment', 'course', 'grade', 'canvas', 'homework', 
                          'class', 'announcement', 'due date', 'submission']
        
        # Check last user message for Canvas keywords
        if messages:
            last_message = messages[-1].get('content', '').lower()
            if any(keyword in last_message for keyword in canvas_keywords):
                # Check if user has Canvas connected
                try:
                    # Import here to avoid circular dependency
                    from boto3.dynamodb.conditions import Key
                    oauth_table = dynamodb.Table(f"{STAGE}-canvas-oauth-tokens")
                    response = oauth_table.get_item(Key={'user_id': user_id})
                    
                    if 'Item' in response:
                        # User has Canvas connected, return MCP configuration
                        return {
                            "type": "url",
                            "url": f"https://api.hfu-amplify.org/{STAGE}/api/mcp/canvas/sse",
                            "name": "canvas_tools",
                            "authorization_token": user_id  # Use user_id as token for now
                        }
                except Exception as e:
                    logger.error(f"Error checking Canvas status: {str(e)}")
        
        return None
    
    def route_to_bedrock(self, model: str, messages: list, user_id: str) -> Dict[str, Any]:
        """Route request to AWS Bedrock"""
        try:
            # Convert messages to Bedrock format
            prompt = "\n".join([f"{msg['role']}: {msg['content']}" for msg in messages])
            
            # Map model names to Bedrock model IDs
            model_map = {
                # Claude models - Frontend names and API names
                # All Claude 3.x models are LEGACY on Bedrock — remap to Claude 4 family
                "Claude Haiku 4.5": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
                "Claude 3 Haiku": "us.anthropic.claude-haiku-4-5-20251001-v1:0",  # Backward compat
                "Claude 3.5 Haiku": "us.anthropic.claude-haiku-4-5-20251001-v1:0",  # Backward compat
                "Claude 3 Sonnet": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",  # 3.x Legacy -> Sonnet 4
                "Claude 3.5 Sonnet": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",  # 3.5 V2 Legacy -> Sonnet 4
                "Claude 3.5 Sonnet V2": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",  # Legacy -> Sonnet 4
                "Claude 3.7 Sonnet": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",  # 3.7 Legacy -> Sonnet 4
                "Claude 4 Sonnet": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
                "Claude 4.5 Sonnet": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
                "Claude 4.6 Sonnet": "us.anthropic.claude-sonnet-4-6",
                "claude-haiku-4.5": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
                "claude-3-haiku": "us.anthropic.claude-haiku-4-5-20251001-v1:0",  # Backward compat
                "claude-3.5-haiku": "us.anthropic.claude-haiku-4-5-20251001-v1:0",  # Backward compat
                "claude-3-sonnet": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",  # 3.x Legacy -> Sonnet 4
                "claude-3.5-sonnet": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",  # Legacy -> Sonnet 4
                "claude-3.7-sonnet": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",  # Legacy -> Sonnet 4
                "claude-4-sonnet": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
                "claude-4.5-sonnet": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
                "claude-4.6-sonnet": "us.anthropic.claude-sonnet-4-6",
                
                # Mistral models - Frontend names and API names
                "Mistral Large": "mistral.mistral-large-2402-v1:0",
                "Mistral 7B Instruct": "mistral.mistral-7b-instruct-v0:2",
                "Mixtral 8x7B Instruct": "mistral.mixtral-8x7b-instruct-v0:1",
                "mistral-large": "mistral.mistral-large-2402-v1:0",
                "mistral-7b": "mistral.mistral-7b-instruct-v0:2",
                "mixtral-8x7b": "mistral.mixtral-8x7b-instruct-v0:1",
                "mistral-small": "mistral.mistral-small-2402-v1:0",
                
                # Amazon Titan models - Frontend names and API names
                "Titan Text G1 - Lite": "amazon.titan-text-lite-v1",
                "Titan Text G1 - Express": "amazon.titan-text-express-v1",
                "titan-lite": "amazon.titan-text-lite-v1",
                "titan-express": "amazon.titan-text-express-v1"
            }
            
            bedrock_model = model_map.get(model, model)
            
            # Check for Canvas context for Claude models
            mcp_config = None
            if "claude" in bedrock_model and "claude-2" not in bedrock_model:
                mcp_config = self._check_canvas_context(messages, user_id)
            
            # Prepare request based on model provider
            if "claude" in bedrock_model and "claude-2" not in bedrock_model:
                # Claude 3 uses messages format
                body = {
                    "messages": messages,
                    "max_tokens": 4096,
                    "temperature": 0.7,
                    "anthropic_version": "bedrock-2023-05-31"
                }
                
                # Add MCP configuration if Canvas context is needed
                if mcp_config:
                    body["mcp_servers"] = [mcp_config]
                    logger.info(f"Added Canvas MCP configuration for user {user_id}")
            elif "claude" in bedrock_model:
                # Claude 2 uses prompt format
                body = {
                    "prompt": f"\n\nHuman: {prompt}\n\nAssistant:",
                    "max_tokens_to_sample": 4096,
                    "temperature": 0.7
                }
            elif "titan" in bedrock_model:
                # Amazon Titan format
                body = {
                    "inputText": prompt,
                    "textGenerationConfig": {
                        "maxTokenCount": 4096,
                        "temperature": 0.7,
                        "topP": 0.9
                    }
                }
            elif "mistral" in bedrock_model or "mixtral" in bedrock_model:
                # Mistral/Mixtral format
                body = {
                    "prompt": f"<s>[INST] {prompt} [/INST]",
                    "max_tokens": 4096,
                    "temperature": 0.7
                }
            else:
                # Generic format
                body = {
                    "prompt": prompt,
                    "max_tokens": 4096,
                    "temperature": 0.7
                }
            
            response = bedrock_client.invoke_model(
                modelId=bedrock_model,
                body=json.dumps(body),
                contentType='application/json'
            )
            
            response_body = json.loads(response['body'].read())
            
            # Extract response based on model
            if "claude" in bedrock_model and "claude-2" not in bedrock_model:
                # Claude 3/4 response format
                content = response_body.get('content', [{}])[0].get('text', '')
            elif "claude-2" in bedrock_model:
                # Claude 2 response format
                content = response_body.get('completion', '')
            elif "titan" in bedrock_model:
                # Titan response format
                results = response_body.get('results', [])
                content = results[0].get('outputText', '') if results else ''
            elif "mistral" in bedrock_model or "mixtral" in bedrock_model:
                # Mistral/Mixtral response format
                outputs = response_body.get('outputs', [])
                content = outputs[0].get('text', '') if outputs else ''
            else:
                # Generic format
                content = response_body.get('outputs', [{}])[0].get('text', '')
            
            # Track usage (approximate)
            tokens = len(content.split()) * 1.3
            self._track_usage(user_id, "bedrock", bedrock_model, int(tokens))
            
            return {
                "success": True,
                "response": content,
                "model": bedrock_model,
                "provider": "bedrock"
            }
            
        except Exception as e:
            logger.error(f"Bedrock error: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "provider": "bedrock"
            }
    
    def route_to_gemini(self, model: str, messages: list, user_id: str) -> Dict[str, Any]:
        """Route request to Google Gemini using REST API"""
        try:
            if not self.api_keys.get('gemini_api_key'):
                raise ValueError("Gemini API key not configured")
            
            # Map model names to current Gemini models (match frontend dropdown)
            # Gemini 1.5 models have been upgraded to 2.5
            model_map = {
                "Gemini 1.5 Pro": "gemini-2.5-pro",
                "Gemini 1.5 Flash": "gemini-2.5-flash",
                "gemini-pro": "gemini-2.5-pro",
                "gemini-1.5-pro": "gemini-2.5-pro",
                "gemini-1.5-flash": "gemini-2.5-flash",
                "gemini-flash": "gemini-2.5-flash"
            }
            
            gemini_model = model_map.get(model, "gemini-pro")
            api_key = self.api_keys['gemini_api_key']
            
            # Gemini REST API endpoint - all models now use v1beta
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{gemini_model}:generateContent?key={api_key}"
            
            # Convert messages to Gemini format
            contents = []
            for msg in messages:
                contents.append({
                    "role": "user" if msg['role'] == "user" else "model",
                    "parts": [{"text": msg['content']}]
                })
            
            # Prepare request payload
            payload = {
                "contents": contents,
                "generationConfig": {
                    "temperature": 0.7,
                    "maxOutputTokens": 4096
                }
            }
            
            # Make request
            response = requests.post(url, json=payload)
            response.raise_for_status()
            
            result = response.json()
            content = result['candidates'][0]['content']['parts'][0]['text']
            
            # Track usage (approximate)
            tokens = len(content.split()) * 1.3
            self._track_usage(user_id, "gemini", model, int(tokens))
            
            return {
                "success": True,
                "response": content,
                "model": model,
                "provider": "gemini"
            }
            
        except Exception as e:
            logger.error(f"Gemini error: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "provider": "gemini"
            }
    
    def route_to_openai(self, model: str, messages: list, user_id: str) -> Dict[str, Any]:
        """Route request to OpenAI using REST API"""
        try:
            if not self.api_keys.get('openai_api_key'):
                raise ValueError("OpenAI API key not configured")
            
            api_key = self.api_keys['openai_api_key']
            
            # Map model names (match frontend dropdown)
            model_map = {
                "GPT-4": "gpt-4",
                "GPT-3.5 Turbo": "gpt-3.5-turbo",
                "gpt-4": "gpt-4",
                "gpt-4-turbo": "gpt-4-turbo-preview",
                "gpt-3.5-turbo": "gpt-3.5-turbo"
            }
            
            openai_model = model_map.get(model, "gpt-4")
            
            # OpenAI API endpoint
            url = "https://api.openai.com/v1/chat/completions"
            
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "model": openai_model,
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 4096
            }
            
            response = requests.post(url, json=payload, headers=headers)
            response.raise_for_status()
            
            result = response.json()
            content = result['choices'][0]['message']['content']
            usage = result.get('usage', {})
            total_tokens = usage.get('total_tokens', 0)
            
            # Track usage
            self._track_usage(user_id, "openai", openai_model, total_tokens)
            
            return {
                "success": True,
                "response": content,
                "model": openai_model,
                "provider": "openai",
                "usage": {
                    "prompt_tokens": usage.get('prompt_tokens', 0),
                    "completion_tokens": usage.get('completion_tokens', 0),
                    "total_tokens": total_tokens
                }
            }
            
        except Exception as e:
            logger.error(f"OpenAI error: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "provider": "openai"
            }
    
    def route_request(self, provider: str, model: str, messages: list, user_id: str) -> Dict[str, Any]:
        """Route request to appropriate provider"""
        provider_map = {
            "bedrock": self.route_to_bedrock,
            "gemini": self.route_to_gemini,
            "openai": self.route_to_openai
        }
        
        if provider not in provider_map:
            return {
                "success": False,
                "error": f"Unknown provider: {provider}",
                "provider": provider
            }
        
        return provider_map[provider](model, messages, user_id)


def handler(event, context):
    """Main Lambda handler"""
    try:
        # Parse request
        body = json.loads(event.get('body', '{}'))
        
        # Extract parameters
        model = body.get('model', 'Claude 3.5 Sonnet')  # Default to Claude 3.5 Sonnet
        messages = body.get('messages', [])
        prompt = body.get('prompt')
        
        # Determine provider based on model name if not specified
        provider = body.get('provider')
        if not provider:
            # Auto-detect provider based on model name
            if model.startswith('Claude') or model.startswith('claude') or model.startswith('Mistral') or model.startswith('mistral') or model.startswith('titan'):
                provider = 'bedrock'
            elif model.startswith('GPT') or model.startswith('gpt'):
                provider = 'openai'
            elif model.startswith('Gemini') or model.startswith('gemini'):
                provider = 'gemini'
            else:
                provider = 'bedrock'  # Default to bedrock
        
        # Convert single prompt to messages format
        if prompt and not messages:
            messages = [{"role": "user", "content": prompt}]
        
        if not messages:
            return {
                'statusCode': 400,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps({
                    'error': 'No messages or prompt provided'
                })
            }
        
        # Extract user ID from authorizer
        user_id = event.get('requestContext', {}).get('authorizer', {}).get('principalId', 'unknown')
        
        # Initialize router and process request
        router = LLMRouter()
        result = router.route_request(provider, model, messages, user_id)
        
        if result['success']:
            return {
                'statusCode': 200,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps(result)
            }
        else:
            return {
                'statusCode': 500,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps(result)
            }
            
    except Exception as e:
        logger.error(f"Handler error: {str(e)}")
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'error': str(e),
                'success': False
            })
        }