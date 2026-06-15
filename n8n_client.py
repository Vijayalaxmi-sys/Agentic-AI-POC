"""
n8n_client.py

Handles safe communication with local n8n API.

Version 2.3:
- Test n8n connection
- List workflows
- Create workflow from generated workflow_json
- Activate workflow for Option B
- Call production webhook for Option B

This file does NOT:
- build workflow JSON
- call Slack API directly
- call Gmail API directly
- call OpenAI API directly
"""

import copy
import requests

from config import N8N_BASE_URL, N8N_API_KEY


# -----------------------------
# Helpers
# -----------------------------

def get_base_url():
    """
    ADDED:
    Returns clean n8n base URL without trailing slash.

    Example:
    http://localhost:5678
    """
    return str(N8N_BASE_URL).rstrip("/")


def get_headers():
    """
    ADDED:
    Builds n8n API headers.
    """
    return {
        "X-N8N-API-KEY": N8N_API_KEY,
        "Content-Type": "application/json"
    }


def parse_response_safely(response):
    """
    ADDED:
    Safely parses response.

    Some endpoints/webhooks may return:
    - JSON
    - plain text
    - empty body
    """
    try:
        return response.json()
    except Exception:
        return {
            "text": response.text
        }


# -----------------------------
# n8n API checks
# -----------------------------

def test_n8n_connection():
    """
    ADDED:
    Tests whether n8n API is reachable.
    """
    try:
        url = f"{get_base_url()}/api/v1/workflows"

        response = requests.get(
            url,
            headers=get_headers(),
            timeout=30
        )

        if response.status_code == 200:
            data = response.json()
            return {
                "status": "ok",
                "can_continue": True,
                "message": "n8n API connection successful.",
                "workflow_count": len(data.get("data", [])),
                "raw": data
            }

        return {
            "status": "failed",
            "can_continue": False,
            "message": "n8n API connection failed.",
            "status_code": response.status_code,
            "response_text": response.text
        }

    except Exception as error:
        return {
            "status": "error",
            "can_continue": False,
            "message": "Could not connect to n8n API.",
            "error": str(error)
        }


def list_workflows():
    """
    ADDED:
    Lists workflows from n8n.
    """
    try:
        url = f"{get_base_url()}/api/v1/workflows"

        response = requests.get(
            url,
            headers=get_headers(),
            timeout=30
        )

        if response.status_code != 200:
            return {
                "status": "failed",
                "can_continue": False,
                "status_code": response.status_code,
                "response_text": response.text,
                "workflows": []
            }

        data = response.json()
        workflows = []

        for workflow in data.get("data", []):
            workflows.append({
                "id": workflow.get("id", ""),
                "name": workflow.get("name", ""),
                "active": workflow.get("active", False),
                "createdAt": workflow.get("createdAt", ""),
                "updatedAt": workflow.get("updatedAt", "")
            })

        return {
            "status": "ok",
            "can_continue": True,
            "workflow_count": len(workflows),
            "workflows": workflows
        }

    except Exception as error:
        return {
            "status": "error",
            "can_continue": False,
            "error": str(error),
            "workflows": []
        }


# -----------------------------
# Workflow preparation
# -----------------------------

def ensure_test_workflow_name(workflow_json):
    """
    CHANGED:
    Adds TEST prefix to workflow name for safety.

    Also guarantees the final workflow name sent to n8n is <= 128 characters.
    n8n rejects workflow names longer than 128 characters.
    """
    if not isinstance(workflow_json, dict):
        return workflow_json

    max_length = 128
    prefix = "TEST - "

    workflow_name = str(
        workflow_json.get("name", "Generated Dynamic Workflow") or ""
    ).strip()

    workflow_name = " ".join(workflow_name.split())

    if not workflow_name:
        workflow_name = "Generated Dynamic Workflow"

    if workflow_name.startswith(prefix):
        final_name = workflow_name
    else:
        final_name = f"{prefix}{workflow_name}"

    if len(final_name) > max_length:
        final_name = final_name[:max_length - 3].rstrip() + "..."

    workflow_json["name"] = final_name
    return workflow_json

def prepare_workflow_for_create(workflow_json):
    """
    ADDED:
    Prepares workflow_json before sending to n8n API.

    Safety:
    - makes a copy
    - adds TEST prefix
    - removes read-only/system fields
    - keeps only API-safe settings
    """
    if not isinstance(workflow_json, dict):
        return {}

    prepared = copy.deepcopy(workflow_json)

    # ADDED:
    # Make generated workflows easy to identify/delete during development.
    prepared = ensure_test_workflow_name(prepared)

    # CHANGED:
    # Remove fields that n8n create API does not accept.
    fields_to_remove = [
        "id",
        "active",
        "tags",
        "versionId",
        "activeVersionId",
        "createdAt",
        "updatedAt",
        "shared",
        "activeVersion",
        "triggerCount",
        "meta",
        "staticData"
    ]

    for field in fields_to_remove:
        prepared.pop(field, None)

    # ADDED:
    # Ensure required top-level fields exist.
    prepared.setdefault("nodes", [])
    prepared.setdefault("connections", {})
    prepared.setdefault("pinData", {})

    # CHANGED:
    # n8n create API rejects exported settings like "binaryMode".
    # Keep only safe setting for create.
    prepared["settings"] = {
        "executionOrder": "v1"
    }

    return prepared


def find_webhook_path_from_workflow_json(workflow_json):
    """
    ADDED:
    Finds webhook path from generated workflow_json.

    Priority:
    1. workflow_json["webhook_path"] if available
    2. First n8n webhook node parameters.path

    Returns:
    "dynamic-test" or "dynamic-ai-xxxx"
    """
    if not isinstance(workflow_json, dict):
        return ""

    # If builder adds top-level webhook_path later, use it.
    webhook_path = workflow_json.get("webhook_path", "")
    if webhook_path:
        return str(webhook_path).strip().strip("/")

    nodes = workflow_json.get("nodes", [])

    if not isinstance(nodes, list):
        return ""

    for node in nodes:
        if not isinstance(node, dict):
            continue

        if node.get("type") == "n8n-nodes-base.webhook":
            parameters = node.get("parameters", {})
            if isinstance(parameters, dict):
                path = parameters.get("path", "")
                return str(path).strip().strip("/")

    return ""


# -----------------------------
# Workflow read/create/activate
# -----------------------------

def get_workflow(workflow_id):
    """
    ADDED:
    Reads one workflow from n8n by workflow ID.

    This function does NOT:
    - activate workflow
    - execute workflow
    - modify workflow
    """
    try:
        if not workflow_id:
            return {
                "status": "invalid_input",
                "can_continue": False,
                "message": "workflow_id is required.",
                "workflow": None
            }

        url = f"{get_base_url()}/api/v1/workflows/{workflow_id}"

        response = requests.get(
            url,
            headers=get_headers(),
            timeout=30
        )

        if response.status_code == 200:
            data = response.json()
            nodes = data.get("nodes", [])

            return {
                "status": "ok",
                "can_continue": True,
                "workflow": {
                    "id": data.get("id", ""),
                    "name": data.get("name", ""),
                    "active": data.get("active", False),
                    "node_count": len(nodes),
                    "node_names": [node.get("name", "") for node in nodes],
                    "createdAt": data.get("createdAt", ""),
                    "updatedAt": data.get("updatedAt", "")
                },
                "raw": data
            }

        return {
            "status": "failed",
            "can_continue": False,
            "message": "Failed to read workflow from n8n.",
            "status_code": response.status_code,
            "response_text": response.text,
            "workflow": None
        }

    except Exception as error:
        return {
            "status": "error",
            "can_continue": False,
            "message": "Error while reading workflow from n8n.",
            "error": str(error),
            "workflow": None
        }


def create_workflow(workflow_json):
    """
    ADDED:
    Creates an inactive workflow in n8n.

    This function:
    - sends workflow_json to n8n
    - creates workflow
    - returns created workflow id

    This function does NOT:
    - activate workflow
    - execute workflow
    """
    try:
        prepared_workflow = prepare_workflow_for_create(workflow_json)

        if not prepared_workflow:
            return {
                "status": "invalid_input",
                "can_continue": False,
                "message": "workflow_json must be a dictionary.",
                "created_workflow": None
            }

        url = f"{get_base_url()}/api/v1/workflows"

        response = requests.post(
            url,
            headers=get_headers(),
            json=prepared_workflow,
            timeout=30
        )

        if response.status_code in [200, 201]:
            data = response.json()

            return {
                "status": "ok",
                "can_continue": True,
                "message": "Workflow created successfully in n8n. It should be inactive by default.",
                "created_workflow": {
                    "id": data.get("id", ""),
                    "name": data.get("name", ""),
                    "active": data.get("active", False)
                },
                "raw": data
            }

        return {
            "status": "failed",
            "can_continue": False,
            "message": "Failed to create workflow in n8n.",
            "status_code": response.status_code,
            "response_text": response.text,
            "created_workflow": None
        }

    except Exception as error:
        return {
            "status": "error",
            "can_continue": False,
            "message": "Error while creating workflow in n8n.",
            "error": str(error),
            "created_workflow": None
        }


def activate_workflow(workflow_id):
    """
    ADDED:
    Activates workflow in n8n for Option B.

    Required for production webhook URL:
    http://localhost:5678/webhook/{path}

    This function:
    - calls POST /api/v1/workflows/{id}/activate
    """
    try:
        if not workflow_id:
            return {
                "status": "invalid_input",
                "can_continue": False,
                "message": "workflow_id is required.",
                "activated_workflow": None
            }

        url = f"{get_base_url()}/api/v1/workflows/{workflow_id}/activate"

        response = requests.post(
            url,
            headers=get_headers(),
            timeout=30
        )

        if response.status_code in [200, 201]:
            data = parse_response_safely(response)

            return {
                "status": "ok",
                "can_continue": True,
                "message": "Workflow activated successfully in n8n.",
                "activated_workflow": {
                    "id": workflow_id,
                    "active": True
                },
                "raw": data
            }

        return {
            "status": "failed",
            "can_continue": False,
            "message": "Failed to activate workflow in n8n.",
            "status_code": response.status_code,
            "response_text": response.text,
            "activated_workflow": None
        }

    except Exception as error:
        return {
            "status": "error",
            "can_continue": False,
            "message": "Error while activating workflow in n8n.",
            "error": str(error),
            "activated_workflow": None
        }


def deactivate_workflow(workflow_id):
    """
    ADDED:
    Deactivates workflow in n8n.

    Useful for cleanup during testing.
    """
    try:
        if not workflow_id:
            return {
                "status": "invalid_input",
                "can_continue": False,
                "message": "workflow_id is required.",
                "deactivated_workflow": None
            }

        url = f"{get_base_url()}/api/v1/workflows/{workflow_id}/deactivate"

        response = requests.post(
            url,
            headers=get_headers(),
            timeout=30
        )

        if response.status_code in [200, 201]:
            data = parse_response_safely(response)

            return {
                "status": "ok",
                "can_continue": True,
                "message": "Workflow deactivated successfully in n8n.",
                "deactivated_workflow": {
                    "id": workflow_id,
                    "active": False
                },
                "raw": data
            }

        return {
            "status": "failed",
            "can_continue": False,
            "message": "Failed to deactivate workflow in n8n.",
            "status_code": response.status_code,
            "response_text": response.text,
            "deactivated_workflow": None
        }

    except Exception as error:
        return {
            "status": "error",
            "can_continue": False,
            "message": "Error while deactivating workflow in n8n.",
            "error": str(error),
            "deactivated_workflow": None
        }


# -----------------------------
# Option B helpers
# -----------------------------

def call_production_webhook(webhook_path, payload=None, timeout=120):
    """
    ADDED:
    Calls active workflow production webhook.

    This is NOT n8n API.
    This calls the workflow webhook URL directly.

    Example:
    http://localhost:5678/webhook/dynamic-ai-xxxx

    Important:
    - workflow must be active
    - webhook_path must match Webhook node path
    """
    try:
        if not webhook_path:
            return {
                "status": "invalid_input",
                "can_continue": False,
                "message": "webhook_path is required.",
                "webhook_response": None
            }

        if payload is None:
            payload = {}

        clean_path = str(webhook_path).strip().strip("/")
        url = f"{get_base_url()}/webhook/{clean_path}"

        response = requests.post(
            url,
            json=payload,
            timeout=timeout
        )

        data = parse_response_safely(response)

        if response.status_code in [200, 201]:
            return {
                "status": "ok",
                "can_continue": True,
                "message": "Production webhook called successfully.",
                "webhook_url": url,
                "status_code": response.status_code,
                "webhook_response": data
            }

        return {
            "status": "failed",
            "can_continue": False,
            "message": "Production webhook call failed.",
            "webhook_url": url,
            "status_code": response.status_code,
            "response_text": response.text,
            "webhook_response": data
        }

    except Exception as error:
        return {
            "status": "error",
            "can_continue": False,
            "message": "Error while calling production webhook.",
            "error": str(error),
            "webhook_response": None
        }


def create_and_activate_workflow(workflow_json):
    """
    ADDED:
    Option B helper.

    Steps:
    1. Create workflow in n8n
    2. Activate workflow
    3. Return workflow id and webhook path

    This function does NOT call webhook yet.
    """
    create_result = create_workflow(workflow_json)

    if not create_result.get("can_continue"):
        return {
            "status": "failed",
            "can_continue": False,
            "stage": "create_workflow",
            "message": "Workflow creation failed.",
            "create_result": create_result,
            "activate_result": None,
            "created_workflow": None,
            "webhook_path": ""
        }

    created_workflow = create_result.get("created_workflow") or {}
    workflow_id = created_workflow.get("id", "")

    activate_result = activate_workflow(workflow_id)

    if not activate_result.get("can_continue"):
        return {
            "status": "failed",
            "can_continue": False,
            "stage": "activate_workflow",
            "message": "Workflow created but activation failed.",
            "create_result": create_result,
            "activate_result": activate_result,
            "created_workflow": created_workflow,
            "webhook_path": find_webhook_path_from_workflow_json(workflow_json)
        }

    webhook_path = find_webhook_path_from_workflow_json(workflow_json)

    return {
        "status": "ok",
        "can_continue": True,
        "stage": "create_and_activate_workflow",
        "message": "Workflow created and activated successfully.",
        "create_result": create_result,
        "activate_result": activate_result,
        "created_workflow": created_workflow,
        "webhook_path": webhook_path
    }



# -----------------------------
# Credential API helpers
# -----------------------------

def list_credentials():
    """
    ADDED:
    Lists credentials from n8n API.

    Why added:
    - Needed for standard OAuth sync.
    - Lets the app find an existing Gmail/Slack credential before creating a new one.

    Safety:
    - This function only reads credentials metadata.
    - It does NOT modify credentials.
    - Existing workflow functions are not changed.
    """
    try:
        url = f"{get_base_url()}/api/v1/credentials"

        response = requests.get(
            url,
            headers=get_headers(),
            timeout=30
        )

        data = parse_response_safely(response)

        if response.status_code == 200:
            credential_items = data.get("data", data)

            if not isinstance(credential_items, list):
                credential_items = []

            credentials = []
            for item in credential_items:
                if not isinstance(item, dict):
                    continue

                credentials.append({
                    "id": item.get("id", ""),
                    "name": item.get("name", ""),
                    "type": item.get("type", ""),
                    "createdAt": item.get("createdAt", ""),
                    "updatedAt": item.get("updatedAt", "")
                })

            return {
                "status": "ok",
                "can_continue": True,
                "credentials": credentials,
                "raw": data
            }

        return {
            "status": "failed",
            "can_continue": False,
            "message": "Failed to list credentials from n8n.",
            "status_code": response.status_code,
            "response_text": response.text,
            "credentials": []
        }

    except Exception as error:
        return {
            "status": "error",
            "can_continue": False,
            "message": "Error while listing credentials from n8n.",
            "error": str(error),
            "credentials": []
        }


def find_credential_by_name_and_type(credential_name, credential_type):
    """
    ADDED:
    Finds an existing n8n credential by name and type.

    Why added:
    - Prevents duplicate Gmail credentials when user reconnects Gmail.

    Safety:
    - Read-only helper.
    - Returns empty credential if not found.
    """
    credential_name = str(credential_name or "").strip()
    credential_type = str(credential_type or "").strip()

    if not credential_name or not credential_type:
        return {
            "status": "invalid_input",
            "can_continue": False,
            "message": "credential_name and credential_type are required.",
            "credential": None
        }

    list_result = list_credentials()

    if not list_result.get("can_continue"):
        return {
            "status": "failed",
            "can_continue": False,
            "message": "Could not list credentials while searching.",
            "list_result": list_result,
            "credential": None
        }

    for credential in list_result.get("credentials", []):
        if (
            credential.get("name") == credential_name
            and credential.get("type") == credential_type
        ):
            return {
                "status": "ok",
                "can_continue": True,
                "message": "Credential found.",
                "credential": credential
            }

    return {
        "status": "not_found",
        "can_continue": True,
        "message": "Credential not found.",
        "credential": None
    }


def create_credential(credential_name, credential_type, credential_data):
    """
    ADDED:
    Creates one n8n credential through the n8n API.

    Why added:
    - Needed for standard flow:
      app OAuth -> n8n credential -> builder attaches latest credential id.

    Safety:
    - This function is NOT called automatically by existing workflow creation.
    - It only runs when app.py explicitly calls it after OAuth callback.
    """
    try:
        credential_name = str(credential_name or "").strip()
        credential_type = str(credential_type or "").strip()

        if not credential_name or not credential_type or not isinstance(credential_data, dict):
            return {
                "status": "invalid_input",
                "can_continue": False,
                "message": "credential_name, credential_type, and credential_data are required.",
                "credential": None
            }

        payload = {
            "name": credential_name,
            "type": credential_type,
            "data": credential_data
        }

        url = f"{get_base_url()}/api/v1/credentials"

        response = requests.post(
            url,
            headers=get_headers(),
            json=payload,
            timeout=30
        )

        data = parse_response_safely(response)

        if response.status_code in [200, 201]:
            return {
                "status": "ok",
                "can_continue": True,
                "message": "Credential created successfully in n8n.",
                "credential": {
                    "id": data.get("id", ""),
                    "name": data.get("name", credential_name),
                    "type": data.get("type", credential_type)
                },
                "raw": data
            }

        return {
            "status": "failed",
            "can_continue": False,
            "message": "Failed to create credential in n8n.",
            "status_code": response.status_code,
            "response_text": response.text,
            "credential": None
        }

    except Exception as error:
        return {
            "status": "error",
            "can_continue": False,
            "message": "Error while creating credential in n8n.",
            "error": str(error),
            "credential": None
        }


def update_credential(credential_id, credential_name, credential_type, credential_data):
    """
    ADDED:
    Updates one existing n8n credential.

    Why added:
    - If Gmail credential already exists, we should refresh/update it instead
      of creating duplicates.

    Safety:
    - This function is NOT called by current workflow creation unless app.py
      explicitly calls credential sync.
    - Tries PATCH first, then PUT as fallback because n8n versions can differ.
    """
    try:
        credential_id = str(credential_id or "").strip()
        credential_name = str(credential_name or "").strip()
        credential_type = str(credential_type or "").strip()

        if not credential_id or not credential_name or not credential_type or not isinstance(credential_data, dict):
            return {
                "status": "invalid_input",
                "can_continue": False,
                "message": "credential_id, credential_name, credential_type, and credential_data are required.",
                "credential": None
            }

        payload = {
            "name": credential_name,
            "type": credential_type,
            "data": credential_data
        }

        url = f"{get_base_url()}/api/v1/credentials/{credential_id}"

        # ADDED:
        # Try PATCH first. If this n8n version does not support PATCH for
        # credentials, try PUT as a fallback.
        response = requests.patch(
            url,
            headers=get_headers(),
            json=payload,
            timeout=30
        )

        if response.status_code == 405:
            response = requests.put(
                url,
                headers=get_headers(),
                json=payload,
                timeout=30
            )

        data = parse_response_safely(response)

        if response.status_code in [200, 201]:
            return {
                "status": "ok",
                "can_continue": True,
                "message": "Credential updated successfully in n8n.",
                "credential": {
                    "id": data.get("id", credential_id),
                    "name": data.get("name", credential_name),
                    "type": data.get("type", credential_type)
                },
                "raw": data
            }

        return {
            "status": "failed",
            "can_continue": False,
            "message": "Failed to update credential in n8n.",
            "status_code": response.status_code,
            "response_text": response.text,
            "credential": None
        }

    except Exception as error:
        return {
            "status": "error",
            "can_continue": False,
            "message": "Error while updating credential in n8n.",
            "error": str(error),
            "credential": None
        }


def create_or_update_credential(credential_name, credential_type, credential_data):
    """
    ADDED:
    Creates a credential if missing, otherwise updates the existing credential.

    Why added:
    - Standard product behavior should avoid duplicate credentials.
    - User reconnecting Gmail should refresh the same mapped credential.
    """
    find_result = find_credential_by_name_and_type(credential_name, credential_type)

    if not find_result.get("can_continue"):
        return {
            "status": "failed",
            "can_continue": False,
            "message": "Could not check existing credential.",
            "find_result": find_result,
            "credential": None
        }

    existing_credential = find_result.get("credential")

    if existing_credential and existing_credential.get("id"):
        return update_credential(
            existing_credential.get("id"),
            credential_name,
            credential_type,
            credential_data
        )

    return create_credential(
        credential_name,
        credential_type,
        credential_data
    )


def sync_gmail_oauth_credential_to_n8n(gmail_connection, credential_name="Gmail account"):
    """
    ADDED:
    Builds/updates an n8n Gmail OAuth2 credential using the app Gmail OAuth token.

    Standard target flow:
    1. User connects Gmail in app.py
    2. app.py saves Gmail token in data/connections.json
    3. app.py calls this function
    4. This function creates/updates n8n Gmail credential
    5. Later app.py/credential_manager saves returned credential id

    Safety:
    - This function does not run automatically by importing n8n_client.py.
    - It must be explicitly called from app.py after Gmail OAuth succeeds.
    - It does not modify workflows.

    Important:
    - n8n credential API behavior can differ by version.
    - If your n8n version rejects direct OAuth credential creation, this function
      will return a failed result instead of breaking existing flow.
    """
    try:
        from config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET

        if not isinstance(gmail_connection, dict):
            return {
                "status": "invalid_input",
                "can_continue": False,
                "message": "gmail_connection must be a dictionary.",
                "credential": None
            }

        access_token = str(gmail_connection.get("access_token", "")).strip()
        refresh_token = str(gmail_connection.get("refresh_token", "")).strip()
        expires_in = gmail_connection.get("expires_in", "")
        scope = str(gmail_connection.get("scope", "")).strip()

        if not scope:
            scope = "openid email profile https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.send"

        if not access_token:
            return {
                "status": "invalid_input",
                "can_continue": False,
                "message": "Gmail access_token is missing. Reconnect Gmail from app first.",
                "credential": None
            }

        if not refresh_token:
            return {
                "status": "invalid_input",
                "can_continue": False,
                "message": "Gmail refresh_token is missing. Reconnect Gmail with prompt=consent first.",
                "credential": None
            }

        # CHANGED:
        # n8n validates Gmail OAuth2 credential data against its credential
        # schema. Your n8n response showed these fields are required:
        # - serverUrl
        # - sendAdditionalBodyProperties
        # - additionalBodyProperties
        #
        # Keep existing token fields and add the required schema fields.
        credential_data = {
            "clientId": GOOGLE_CLIENT_ID,
            "clientSecret": GOOGLE_CLIENT_SECRET,
            "serverUrl": "https://gmail.googleapis.com",
            "sendAdditionalBodyProperties": False,
            "additionalBodyProperties": {},
            "oauthTokenData": {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "scope": scope,
                "token_type": "Bearer",
                "expires_in": expires_in
            }
        }

        return create_or_update_credential(
            credential_name=credential_name,
            credential_type="gmailOAuth2",
            credential_data=credential_data
        )

    except Exception as error:
        return {
            "status": "error",
            "can_continue": False,
            "message": "Error while syncing Gmail credential to n8n.",
            "error": str(error),
            "credential": None
        }

# -----------------------------
# Manual test
# -----------------------------

if __name__ == "__main__":
    import json

    print("Testing n8n API connection...")
    result = test_n8n_connection()
    print(json.dumps(result, indent=2, ensure_ascii=False))

    print("\nListing workflows...")
    workflows_result = list_workflows()
    print(json.dumps(workflows_result, indent=2, ensure_ascii=False))

    print("\nNote:")
    print("- create_workflow() is available but not called automatically.")
    print("- activate_workflow() is available but not called automatically.")
    print("- call_production_webhook() is available but not called automatically.")