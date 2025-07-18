import json
import os
import boto3
import logging
from typing import Dict, Any, Optional
import requests
from datetime import datetime

# Import provider-specific clients
import google.generativeai as genai
import openai
from botocore.exceptions import ClientError

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
    
    def route_to_bedrock(self, model: str, messages: list, user_id: str) -> Dict[str, Any]:
        """Route request to AWS Bedrock"""
        try:
            # Convert messages to Bedrock format
            prompt = "\n".join([f"{msg['role']}: {msg['content']}" for msg in messages])
            
            # Map model names to Bedrock model IDs
            model_map = {
                "claude-3-sonnet": "anthropic.claude-3-sonnet-20240229-v1:0",
                "claude-3-haiku": "anthropic.claude-3-haiku-20240307-v1:0",
                "mistral-large": "mistral.mistral-large-2402-v1:0"
            }
            
            bedrock_model = model_map.get(model, model)
            
            # Prepare request based on model provider
            if "claude" in bedrock_model:
                body = {
                    "prompt": f"\n\nHuman: {prompt}\n\nAssistant:",
                    "max_tokens_to_sample": 4096,
                    "temperature": 0.7
                }
            else:
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
            if "claude" in bedrock_model:
                content = response_body.get('completion', '')
            else:
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
        """Route request to Google Gemini"""
        try:
            if not self.api_keys.get('gemini_api_key'):
                raise ValueError("Gemini API key not configured")
            
            genai.configure(api_key=self.api_keys['gemini_api_key'])
            
            # Map model names
            model_map = {
                "gemini-pro": "gemini-pro",
                "gemini-pro-vision": "gemini-pro-vision"
            }
            
            gemini_model = genai.GenerativeModel(model_map.get(model, "gemini-pro"))
            
            # Convert messages to Gemini format
            prompt = "\n".join([f"{msg['role']}: {msg['content']}" for msg in messages])
            
            response = gemini_model.generate_content(prompt)
            
            # Track usage
            tokens = len(response.text.split()) * 1.3
            self._track_usage(user_id, "gemini", model, int(tokens))
            
            return {
                "success": True,
                "response": response.text,
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
        """Route request to OpenAI"""
        try:
            if not self.api_keys.get('openai_api_key'):
                raise ValueError("OpenAI API key not configured")
            
            openai.api_key = self.api_keys['openai_api_key']
            
            # Map model names
            model_map = {
                "gpt-4": "gpt-4",
                "gpt-4-turbo": "gpt-4-turbo-preview",
                "gpt-3.5-turbo": "gpt-3.5-turbo"
            }
            
            openai_model = model_map.get(model, "gpt-4")
            
            response = openai.ChatCompletion.create(
                model=openai_model,
                messages=messages,
                temperature=0.7,
                max_tokens=4096
            )
            
            content = response.choices[0].message.content
            tokens = response.usage.total_tokens
            
            # Track usage
            self._track_usage(user_id, "openai", openai_model, tokens)
            
            return {
                "success": True,
                "response": content,
                "model": openai_model,
                "provider": "openai",
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": tokens
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
        provider = body.get('provider', 'bedrock')
        model = body.get('model', 'claude-3-sonnet')
        messages = body.get('messages', [])
        prompt = body.get('prompt')
        
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