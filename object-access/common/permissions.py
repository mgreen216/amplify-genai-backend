
#Copyright (c) 2024 Vanderbilt University
#Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

import os
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()

ENFORCE_PERMISSIONS = os.environ.get('ENFORCE_PERMISSIONS', 'false').lower() == 'true'


def _check_ownership(user, data, operation):
    """Core ownership check for object-access operations.

    Verifies the authenticated user has authority over the resource.
    When ENFORCE_PERMISSIONS is False, logs violations but returns True.
    """
    inner_data = data.get('data', {}) if isinstance(data.get('data'), dict) else {}
    owner = (
        inner_data.get('user') or
        inner_data.get('owner') or
        inner_data.get('createdBy') or
        data.get('user') or
        data.get('owner')
    )

    if not owner:
        return True

    if user != owner:
        logger.warning(
            "PERMISSION_VIOLATION: user=%s attempted %s on resource owned by %s | enforce=%s",
            user, operation, owner, ENFORCE_PERMISSIONS
        )
        if ENFORCE_PERMISSIONS:
            return False
        return True

    return True


def can_update_permissions(user, data):
    return _check_ownership(user, data, "update_permissions")

def can_get_permissions(user, data):
    return _check_ownership(user, data, "get_permissions")

def can_create(user, data):
    return _check_ownership(user, data, "create")

def can_update(user, data):
    return _check_ownership(user, data, "update")

def can_delete(user, data):
    return _check_ownership(user, data, "delete")

def can_add_path(user, data):
    return _check_ownership(user, data, "add_path")

def can_read(user, data):
    return _check_ownership(user, data, "read")


def get_permission_checker(user, type, op, data):
    logger.info("Checking permissions for user: %s, type: %s, op: %s", user, type, op)
    checker = permissions_by_state_type.get(type, {}).get(op)
    if not checker:
        logger.warning("No permission checker found for type: %s and op: %s", type, op)
    return checker or (lambda user, data: False)


def get_user(event, data):
    return data['user']


def get_data_owner(event, data):
    return data['user']


permissions_by_state_type = {

    "/utilities/update_object_permissions": {
        "update_object_permissions": can_update_permissions
    },
    "/utilities/can_access_objects": {
        "can_access_objects": can_get_permissions
    },
    "/utilities/simulate_access_to_objects": {
        "simulate_access_to_objects": can_get_permissions
    },
    "/utilities/create_cognito_group": {
        "create_cognito_group": can_create
    },
    "/utilities/get_user_groups": {
        "read": can_read
    },
    "/utilities/in_cognito_amp_groups" : {
        "in_group" : can_read
    },
    "/utilities/emails": {
        "read": can_read
    },
    "/groups/create": {
        "create": can_create
    }, "/groups/update/members" : {
        "update": can_update
    },
    "/groups/update/members/permissions" : {
        "update": can_update
    },
    "/groups/update/assistants" : {
        "update": can_update
    },
    "/groups/update/types": {
        'update' : can_update
    },
    "/groups/update" : {
        "update": can_update
    },
    "/groups/update/amplify_groups" : {
        "update": can_update
    },
    "/groups/update/system_users" : {
        "update": can_update
    },
    "/groups/delete" : {
        "delete": can_delete
    },
     "/groups/list" : {
        'list': can_read
    },
    "/groups/list_all" : {
        'list': can_read
    },
     "/groups/members/list" : {
        'list': can_read
    },
    "/groups/replace_key" : {
        "update" : can_update
    },
    "/groups/assistants/amplify": {
        "create": can_create
    },
    "/groups/assistant/add_path": {
        "add_assistant_path": can_add_path
    },
    "/groups/verify_ast_group_member": {
        "verify_member": can_read
    }
}
