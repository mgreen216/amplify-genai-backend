
#Copyright (c) 2024 Vanderbilt University
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import os
import json
import logging

logger = logging.getLogger(__name__)

ENFORCE_PERMISSIONS = os.environ.get('ENFORCE_PERMISSIONS', 'false').lower() == 'true'


def _check_ownership(user, data, operation):
    """Core ownership check: verify the authenticated user matches the resource owner.

    Returns True if the user is authorized, False otherwise.
    When ENFORCE_PERMISSIONS is False, logs violations but returns True.
    """
    # Extract owner from request data — check common patterns
    inner_data = data.get('data', {}) if isinstance(data.get('data'), dict) else {}
    owner = (
        inner_data.get('user') or
        inner_data.get('owner') or
        data.get('user') or
        data.get('owner')
    )

    # If no owner field is present in the request, allow the operation.
    # The handler itself scopes data access by current_user (e.g., S3 key prefix).
    if not owner:
        return True

    if user != owner:
        logger.warning(
            "PERMISSION_VIOLATION: user=%s attempted %s on resource owned by %s | enforce=%s",
            user, operation, owner, ENFORCE_PERMISSIONS
        )
        if ENFORCE_PERMISSIONS:
            return False
        # Log-only mode: allow through but record the violation
        return True

    return True


def can_share(user, data):
    return _check_ownership(user, data, "share")

def can_save(user, data):
    return _check_ownership(user, data, "save")

def can_delete_item(user, data):
    return _check_ownership(user, data, "delete_item")

def can_upload(user, data):
    return _check_ownership(user, data, "upload")

def can_create_assistant(user, data):
    return _check_ownership(user, data, "create_assistant")

def can_create_assistant_thread(user, data):
    return _check_ownership(user, data, "create_assistant_thread")

def can_read(user, data):
    return _check_ownership(user, data, "read")

def can_chat(user, data):
    # Chat access: verify user has chat in their allowed_access if present
    allowed = data.get('allowed_access', [])
    if allowed and 'chat' not in allowed and 'full_access' not in allowed:
        logger.warning(
            "PERMISSION_VIOLATION: user=%s attempted chat without chat access | enforce=%s",
            user, ENFORCE_PERMISSIONS
        )
        if ENFORCE_PERMISSIONS:
            return False
        return True
    return _check_ownership(user, data, "chat")

def can_delete_file(user, data):
    return _check_ownership(user, data, "delete_file")


def get_permission_checker(user, type, op, data):
    logger.info("Checking permissions for user: %s and type: %s and op: %s", user, type, op)
    return permissions_by_state_type.get(type, {}).get(op, lambda user, data: False)

def get_user(event, data):
    return data['user']

def get_data_owner(event, data):
    return data['user']


permissions_by_state_type = {
  "/state/share": {
    "append": can_share,
    "read": can_share
  },
  "/state/share/load": {
    "load": can_share
  },
  "/datasource/metadata/set": {
    "set": can_upload
  },
  "/files/upload": {
    "upload": can_upload
  },
  "/files/set_tags": {
    "set_tags": can_upload
  },
  "/files/tags/create": {
    "create": can_save
  },
  "/files/tags/delete": {
    "delete": can_delete_item
  },
  "/files/tags/list": {
    "list": can_read
  },
  "/files/query": {
    "query": can_read
  },
  "/files/download": {
    "download": can_read
  },
  "/files/delete": {
    "delete": can_delete_file
  },
  "/chat/convert": {
    "convert": can_save
  },
  "/state/accounts/charge": {
    "create_charge": can_save
    },
  "/state/accounts/get": {
    "get": can_read
  },
  "/state/accounts/save": {
    "save": can_save
  },
  "/state/conversation/upload": {
    "conversation_upload": can_upload
  },
    "/state/conversation/register" : {
    "conversation_upload": can_upload
  },
  "/state/conversation/get/multiple": {
    "get_multiple_conversations": can_read
  },
  "/state/conversation/get": {
    "read": can_read
  },
  "/state/conversation/get/all": {
    "read": can_read
  },
  "/state/conversation/get/empty": {
        "read" : can_read
  },
  "/state/conversation/get/metadata": {
        "read" : can_read
  },
  "/state/conversation/get/since/{timestamp}": {
        "read" : can_read
  },
  "/state/conversation/delete": {
    "delete": can_delete_item
  },
  "/state/conversation/delete_multiple": {
    "delete_multiple_conversations": can_delete_item
  },
  "/chat": {
    "chat": can_chat
  },
   "/state/settings/save": {
        "save": can_save
  },
    "/state/settings/get": {
        "get": can_read
  },
  "/files/reprocess/rag": {
    "upload": can_upload
  }
}
