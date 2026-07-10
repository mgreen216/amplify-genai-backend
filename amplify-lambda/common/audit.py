
#Copyright (c) 2024 Vanderbilt University

import json
import logging
import time
import os

logger = logging.getLogger("audit")
logger.setLevel(logging.INFO)


def log_audit_event(user, action, resource_type, outcome, resource_id=None, metadata=None):
    """Write a structured audit event to CloudWatch Logs.

    Args:
        user: Authenticated user email/ID from JWT claims
        action: The operation attempted (e.g., "delete", "share", "chat")
        resource_type: The API path/resource type (e.g., "/state/conversation/delete")
        outcome: "allow" or "deny"
        resource_id: Optional identifier for the specific resource
        metadata: Optional dict of additional context
    """
    event = {
        "audit": True,
        "timestamp": time.time(),
        "user": user,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "outcome": outcome,
        "enforce_mode": os.environ.get('ENFORCE_PERMISSIONS', 'false'),
    }

    if metadata:
        event["metadata"] = metadata

    # Structured JSON log — CloudWatch Logs Insights can query these fields directly
    logger.info(json.dumps(event))


# Sensitive operations that always get audit logged
AUDITED_OPERATIONS = {
    "delete", "delete_item", "delete_file", "delete_multiple_conversations",
    "append",  # share operations use "append"
    "load",    # share load
    "update_object_permissions",
    "create_cognito_group",
    "update",  # group updates
}


def should_audit(op):
    """Check if an operation should be audit logged."""
    return op in AUDITED_OPERATIONS
