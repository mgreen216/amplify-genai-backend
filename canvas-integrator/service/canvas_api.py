import json
import os
import logging
from typing import Dict, Any, List, Optional
from canvasapi import Canvas
from .core import CanvasOAuthManager, _get_user_id

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

class CanvasAPIHandler:
    def __init__(self, access_token: str, canvas_base_url: str):
        self.canvas = Canvas(canvas_base_url, access_token)
        
    def get_courses(self) -> List[Dict[str, Any]]:
        """Get user's courses"""
        courses = []
        for course in self.canvas.get_courses():
            courses.append({
                'id': course.id,
                'name': getattr(course, 'name', 'Unknown'),
                'course_code': getattr(course, 'course_code', ''),
                'workflow_state': getattr(course, 'workflow_state', '')
            })
        return courses
    
    def get_assignments(self, course_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get assignments (all courses or specific course)"""
        assignments = []
        
        if course_id:
            courses = [self.canvas.get_course(course_id)]
        else:
            courses = self.canvas.get_courses()
            
        for course in courses:
            try:
                for assignment in course.get_assignments():
                    assignments.append({
                        'id': assignment.id,
                        'course_id': course.id,
                        'course_name': getattr(course, 'name', 'Unknown'),
                        'name': assignment.name,
                        'due_at': getattr(assignment, 'due_at', None),
                        'points_possible': getattr(assignment, 'points_possible', 0),
                        'submission_types': getattr(assignment, 'submission_types', []),
                        'has_submitted': getattr(assignment, 'has_submitted_submissions', False)
                    })
            except Exception as e:
                logger.error(f"Error getting assignments for course {course.id}: {str(e)}")
                
        return assignments
    
    def get_upcoming_assignments(self, days: int = 7) -> List[Dict[str, Any]]:
        """Get upcoming assignments within specified days"""
        from datetime import datetime, timedelta
        
        assignments = self.get_assignments()
        now = datetime.now()
        cutoff = now + timedelta(days=days)
        
        upcoming = []
        for assignment in assignments:
            if assignment['due_at']:
                # Parse due date
                due_date = datetime.fromisoformat(assignment['due_at'].replace('Z', '+00:00'))
                if now <= due_date <= cutoff and not assignment['has_submitted']:
                    assignment['days_until_due'] = (due_date - now).days
                    upcoming.append(assignment)
                    
        return sorted(upcoming, key=lambda x: x['due_at'])
    
    def get_grades(self, course_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get grades for courses"""
        grades = []
        
        if course_id:
            courses = [self.canvas.get_course(course_id)]
        else:
            courses = self.canvas.get_courses()
            
        for course in courses:
            try:
                enrollment = course.get_enrollments(user_id='self')[0]
                if hasattr(enrollment, 'grades'):
                    grades.append({
                        'course_id': course.id,
                        'course_name': getattr(course, 'name', 'Unknown'),
                        'current_score': enrollment.grades.get('current_score', 'N/A'),
                        'current_grade': enrollment.grades.get('current_grade', 'N/A'),
                        'final_score': enrollment.grades.get('final_score', 'N/A'),
                        'final_grade': enrollment.grades.get('final_grade', 'N/A')
                    })
            except Exception as e:
                logger.error(f"Error getting grades for course {course.id}: {str(e)}")
                
        return grades
    
    def get_announcements(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent announcements"""
        announcements = []
        
        for announcement in self.canvas.get_announcements()[:limit]:
            announcements.append({
                'id': announcement.id,
                'title': announcement.title,
                'message': announcement.message[:200] + '...' if len(announcement.message) > 200 else announcement.message,
                'posted_at': getattr(announcement, 'posted_at', ''),
                'course_name': getattr(announcement, 'context_name', 'Unknown')
            })
            
        return announcements


def handler(event, context):
    """Lambda handler for Canvas API operations"""
    try:
        user_id = _get_user_id(event)
        manager = CanvasOAuthManager()
        
        # Get user's Canvas token
        access_token = manager.get_token(user_id)
        if not access_token:
            return {
                'statusCode': 401,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps({
                    'error': 'Canvas not connected. Please connect Canvas first.'
                })
            }
        
        # Get Canvas connection info
        status = manager.get_status(user_id)
        canvas_base_url = status.get('canvas_base_url', 'https://canvas.instructure.com')
        
        # Initialize API handler
        api = CanvasAPIHandler(access_token, canvas_base_url)
        
        # Parse request path
        path_parts = event['pathParameters']['proxy'].split('/')
        operation = path_parts[0] if path_parts else ''
        
        # Handle different operations
        if operation == 'courses':
            result = api.get_courses()
        elif operation == 'assignments':
            course_id = int(path_parts[1]) if len(path_parts) > 1 else None
            result = api.get_assignments(course_id)
        elif operation == 'upcoming-assignments':
            days = int(event.get('queryStringParameters', {}).get('days', 7))
            result = api.get_upcoming_assignments(days)
        elif operation == 'grades':
            course_id = int(path_parts[1]) if len(path_parts) > 1 else None
            result = api.get_grades(course_id)
        elif operation == 'announcements':
            limit = int(event.get('queryStringParameters', {}).get('limit', 10))
            result = api.get_announcements(limit)
        else:
            return {
                'statusCode': 400,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps({
                    'error': f'Unknown operation: {operation}'
                })
            }
        
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'success': True,
                'data': result
            })
        }
        
    except Exception as e:
        logger.error(f"Canvas API handler error: {str(e)}")
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