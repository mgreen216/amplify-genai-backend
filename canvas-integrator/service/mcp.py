import json
import os
import logging
import time
from typing import Dict, Any, List, Optional
from .core import CanvasOAuthManager
from .canvas_api import CanvasAPIHandler

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

class CanvasMCPServer:
    """MCP Server implementation for Canvas tools"""
    
    TOOLS = {
        'get_courses': {
            'description': 'Get list of user\'s Canvas courses',
            'parameters': {}
        },
        'get_assignments': {
            'description': 'Get assignments from Canvas',
            'parameters': {
                'course_id': {
                    'type': 'integer',
                    'description': 'Optional course ID to filter assignments',
                    'required': False
                }
            }
        },
        'get_upcoming_assignments': {
            'description': 'Get upcoming assignments due within specified days',
            'parameters': {
                'days': {
                    'type': 'integer',
                    'description': 'Number of days to look ahead (default: 7)',
                    'required': False
                }
            }
        },
        'get_grades': {
            'description': 'Get grades for courses',
            'parameters': {
                'course_id': {
                    'type': 'integer',
                    'description': 'Optional course ID to get grades for specific course',
                    'required': False
                }
            }
        },
        'get_announcements': {
            'description': 'Get recent announcements from Canvas',
            'parameters': {
                'limit': {
                    'type': 'integer',
                    'description': 'Number of announcements to retrieve (default: 10)',
                    'required': False
                }
            }
        }
    }
    
    def __init__(self, access_token: str, canvas_base_url: str):
        self.api = CanvasAPIHandler(access_token, canvas_base_url)
        
    def handle_tool_call(self, tool_name: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Handle a tool call from Claude"""
        if tool_name not in self.TOOLS:
            return {
                'error': f'Unknown tool: {tool_name}',
                'success': False
            }
        
        try:
            # Call the appropriate API method
            if tool_name == 'get_courses':
                result = self.api.get_courses()
            elif tool_name == 'get_assignments':
                course_id = parameters.get('course_id')
                result = self.api.get_assignments(course_id)
            elif tool_name == 'get_upcoming_assignments':
                days = parameters.get('days', 7)
                result = self.api.get_upcoming_assignments(days)
            elif tool_name == 'get_grades':
                course_id = parameters.get('course_id')
                result = self.api.get_grades(course_id)
            elif tool_name == 'get_announcements':
                limit = parameters.get('limit', 10)
                result = self.api.get_announcements(limit)
            else:
                return {
                    'error': f'Tool {tool_name} not implemented',
                    'success': False
                }
                
            return {
                'success': True,
                'data': result,
                'tool': tool_name
            }
            
        except Exception as e:
            logger.error(f"Error in tool call {tool_name}: {str(e)}")
            return {
                'error': str(e),
                'success': False,
                'tool': tool_name
            }
    
    def get_capabilities(self) -> Dict[str, Any]:
        """Return MCP server capabilities"""
        return {
            'name': 'canvas_tools',
            'version': '1.0.0',
            'tools': self.TOOLS
        }


def format_sse_message(event_type: str, data: Any) -> str:
    """Format a message for Server-Sent Events"""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


def sse_handler(event, context):
    """Lambda handler for MCP SSE endpoint.

    DISABLED 2026-07 (security): this handler has no real authentication —
    it trusted the bearer token as a raw user_id, letting anyone who knows a
    Cognito UUID probe that user's Canvas connection. Its API Gateway route
    has been removed from serverless.yml. Before re-enabling: put the route
    behind the Cognito authorizer AND replace the user_id-as-token scheme
    below with validation of the JWT claims from the authorizer context.
    """
    return {
        'statusCode': 410,
        'headers': {'Content-Type': 'text/plain'},
        'body': 'This endpoint has been disabled.'
    }


def _sse_handler_disabled(event, context):
    """Original implementation, kept for reference until Canvas chat ships."""
    try:
        # Extract authorization token from headers
        headers = event.get('headers', {})
        auth_token = headers.get('authorization', '').replace('Bearer ', '')

        if not auth_token:
            return {
                'statusCode': 401,
                'headers': {
                    'Content-Type': 'text/plain',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': 'Missing authorization token'
            }

        # Validate token and get user info
        # In production, this should validate against your auth system
        # For now, we'll treat the token as the user_id
        user_id = auth_token
        
        # Get Canvas token for user
        manager = CanvasOAuthManager()
        access_token = manager.get_token(user_id)
        
        if not access_token:
            return {
                'statusCode': 401,
                'headers': {
                    'Content-Type': 'text/plain',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': 'Canvas not connected'
            }
        
        # Get Canvas connection info
        status = manager.get_status(user_id)
        canvas_base_url = status.get('canvas_base_url', 'https://canvas.instructure.com')
        
        # Initialize MCP server
        mcp_server = CanvasMCPServer(access_token, canvas_base_url)
        
        # For Lambda, we can't maintain true SSE connections
        # Instead, return capabilities and handle tool calls via separate endpoints
        # This is a simplified implementation
        
        # Send capabilities
        capabilities = mcp_server.get_capabilities()
        
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'text/event-stream',
                'Cache-Control': 'no-cache',
                'Access-Control-Allow-Origin': '*'
            },
            'body': format_sse_message('capabilities', capabilities)
        }
        
    except Exception as e:
        logger.error(f"SSE handler error: {str(e)}")
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'text/plain',
                'Access-Control-Allow-Origin': '*'
            },
            'body': str(e)
        }


def tool_handler(event, context):
    """Lambda handler for MCP tool calls.

    DISABLED 2026-07 (security): never mapped to an API Gateway route, but
    the implementation below trusts a caller-supplied authorization_token as
    a raw user_id and then executes real Canvas API calls — including grades
    (FERPA data) — with that user's stored token. Fail closed so a future
    serverless.yml change can't expose it by accident. Re-enable only with
    real JWT validation (see sse_handler note).
    """
    return {
        'statusCode': 410,
        'headers': {'Content-Type': 'application/json'},
        'body': json.dumps({'error': 'This endpoint has been disabled.'})
    }


def _tool_handler_disabled(event, context):
    """Original implementation, kept for reference until Canvas chat ships."""
    try:
        # Parse request
        body = json.loads(event.get('body', '{}'))
        tool_name = body.get('tool')
        parameters = body.get('parameters', {})
        auth_token = body.get('authorization_token')
        
        if not tool_name or not auth_token:
            return {
                'statusCode': 400,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps({
                    'error': 'Missing tool name or authorization token'
                })
            }
        
        # Use token as user_id for now
        user_id = auth_token
        
        # Get Canvas token
        manager = CanvasOAuthManager()
        access_token = manager.get_token(user_id)
        
        if not access_token:
            return {
                'statusCode': 401,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps({
                    'error': 'Canvas not connected'
                })
            }
        
        # Get Canvas connection info
        status = manager.get_status(user_id)
        canvas_base_url = status.get('canvas_base_url', 'https://canvas.instructure.com')
        
        # Initialize MCP server and handle tool call
        mcp_server = CanvasMCPServer(access_token, canvas_base_url)
        result = mcp_server.handle_tool_call(tool_name, parameters)
        
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps(result)
        }
        
    except Exception as e:
        logger.error(f"Tool handler error: {str(e)}")
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