import json
import requests
from jose import jwt
import os

ALGORITHMS = ["RS256"]

def lambda_handler(event, context):
    """
    Simple JWT validation endpoint that returns user claims
    """
    try:
        # Get token from body
        body = json.loads(event.get('body', '{}'))
        token = body.get('token', '')
        
        if not token:
            return {
                "statusCode": 401,
                "headers": {
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Headers": "*"
                },
                "body": json.dumps({"error": "No token provided"})
            }
        
        # Get Cognito configuration from environment
        cognito_issuer = os.environ.get('COGNITO_ISSUER', 'https://cognito-idp.us-east-1.amazonaws.com/us-east-1_PgwOR439P')
        
        # Fetch JWKS from Cognito
        jwks_url = f'{cognito_issuer}/.well-known/jwks.json'
        jwks = requests.get(jwks_url).json()
        
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
            return {
                "statusCode": 401,
                "headers": {
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Headers": "*"
                },
                "body": json.dumps({"error": "Unable to find appropriate key"})
            }
        
        # Decode and verify the token
        payload = jwt.decode(
            token,
            rsa_key,
            algorithms=ALGORITHMS,
            issuer=cognito_issuer
        )
        
        # Return the claims
        return {
            "statusCode": 200,
            "headers": {
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "*"
            },
            "body": json.dumps({
                "valid": True,
                "claims": {
                    "username": payload.get('cognito:username', payload.get('username', payload.get('sub'))),
                    "email": payload.get('email', ''),
                    "sub": payload.get('sub', ''),
                    "groups": payload.get('cognito:groups', [])
                }
            })
        }
        
    except jwt.ExpiredSignatureError:
        return {
            "statusCode": 401,
            "headers": {
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "*"
            },
            "body": json.dumps({"error": "Token has expired"})
        }
    except jwt.InvalidTokenError as e:
        return {
            "statusCode": 401,
            "headers": {
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "*"
            },
            "body": json.dumps({"error": f"Invalid token: {str(e)}"})
        }
    except Exception as e:
        print(f"Validation error: {str(e)}")
        return {
            "statusCode": 500,
            "headers": {
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "*"
            },
            "body": json.dumps({"error": "Internal server error"})
        }