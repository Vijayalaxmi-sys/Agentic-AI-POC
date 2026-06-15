"""
resolver.py

Validates planner output before workflow creation.

PRODUCTION RULE:
- Gmail/Slack connection checks are per logged-in user.
- Gmail/Slack n8n credentials are per logged-in user.
- OpenAI credential is app-level.
- No global Gmail/Slack fallback.

This file checks:
1. Are requested tools supported?
2. Are source operations allowed?
3. Are destination operations allowed?
4. Are required Slack/Gmail connections available for this user?
5. Are required n8n credentials available for this user?
"""

import json
import os
from datetime import datetime

from tool_registry import (
    is_supported_tool,
    supports_source,
    supports_destination,
    is_source_operation_allowed,
    is_destination_operation_allowed,
    requires_connection,
    get_connect_url,
)

# CHANGED:
# Per-user connection checks only. No global fallback.
from connection_manager import is_user_connected

# CHANGED:
# Per-user Gmail/Slack credential checks.
# OpenAI remains app-level inside credential_manager.
from credential_manager import get_required_missing_user_credentials


LOG_DIR = "logs"
RESOLVER_LOG_FILE = os.path.join(LOG_DIR, "resolver_logs.jsonl")


# -----------------------------
# Safe helpers
# -----------------------------

def safe_text(value):
    """
    Converts None or any value into clean string.
    """
    if value is None:
        return ""
    return str(value).strip()


def require_user_id(user_id):
    """
    ADDED:
    Resolver must know which logged-in user is making this request.

    No user_id means we cannot safely check Gmail/Slack.
    """
    clean_user_id = safe_text(user_id)

    if not clean_user_id:
        raise ValueError("user_id is required in resolver. No global Gmail/Slack fallback is allowed.")

    return clean_user_id


# -----------------------------
# Resolver logging
# -----------------------------

def write_resolver_log(plan, result, user_id=""):
    """
    Saves resolver input/output for debugging UI end-to-end issues.
    """
    try:
        os.makedirs(LOG_DIR, exist_ok=True)

        log_record = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "user_id": safe_text(user_id),
            "planner_output": plan,
            "resolver_output": result,
        }

        with open(RESOLVER_LOG_FILE, "a", encoding="utf-8") as file:
            file.write(json.dumps(log_record, ensure_ascii=False) + "\n")

    except Exception:
        # Logging should never break the app
        pass


# -----------------------------
# Collect required tools
# -----------------------------

def collect_tools_from_plan(plan):
    """
    Collects all tools used in planner output.

    Looks at:
    - sources[]
    - destination
    - requires_connection

    Returns unique tool list.
    """
    tools = []

    sources = plan.get("sources", [])
    if isinstance(sources, list):
        for source in sources:
            if isinstance(source, dict):
                tool = safe_text(source.get("tool")).lower()
                if tool and tool != "none":
                    tools.append(tool)

    destination = plan.get("destination", {})
    if isinstance(destination, dict):
        tool = safe_text(destination.get("tool")).lower()
        if tool and tool != "none":
            tools.append(tool)

    required = plan.get("requires_connection", [])
    if isinstance(required, list):
        for tool in required:
            tool = safe_text(tool).lower()
            if tool and tool != "none":
                tools.append(tool)

    return list(dict.fromkeys(tools))


# -----------------------------
# Collect required n8n credentials
# -----------------------------

def collect_required_credentials(plan):
    """
    Collects n8n credentials required for real workflow execution.

    Sources/destinations:
    - slack needs Slack credential
    - gmail needs Gmail credential

    Processing:
    - if LLM processing is required, openai credential is required
    """
    required_credentials = []

    tools = collect_tools_from_plan(plan)

    for tool in tools:
        if tool in {"slack", "gmail"}:
            required_credentials.append(tool)

    processing = plan.get("processing", {})
    if isinstance(processing, dict):
        processing_required = bool(processing.get("required", False))
        processing_operation = safe_text(processing.get("operation")).lower()

        if processing_required and processing_operation and processing_operation != "none":
            required_credentials.append("openai")

    return list(dict.fromkeys(required_credentials))


# -----------------------------
# Validate sources
# -----------------------------

def validate_sources(plan):
    """
    Validates every source in sources[].

    Example:
    Slack source read is allowed.
    Chat source is not allowed.
    """
    errors = []

    sources = plan.get("sources", [])
    if not isinstance(sources, list):
        return ["sources must be a list"]

    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            errors.append(f"sources[{index}] must be an object")
            continue

        tool = safe_text(source.get("tool")).lower()
        operation = safe_text(source.get("operation")).lower()

        if not tool or tool == "none":
            continue

        if not is_supported_tool(tool):
            errors.append(f"Unsupported source tool: {tool}")
            continue

        if not supports_source(tool):
            errors.append(f"{tool} cannot be used as a source")
            continue

        if not is_source_operation_allowed(tool, operation):
            errors.append(f"Operation '{operation}' is not allowed for {tool} as source")

    return errors


# -----------------------------
# Validate destination
# -----------------------------

def validate_destination(plan):
    """
    Validates destination tool and operation.

    Example:
    Slack destination send is allowed.
    Chat destination return is allowed.
    """
    errors = []

    destination = plan.get("destination", {})
    if not isinstance(destination, dict):
        return ["destination must be an object"]

    tool = safe_text(destination.get("tool")).lower()
    operation = safe_text(destination.get("operation")).lower()

    if not tool or tool == "none":
        return errors

    if not is_supported_tool(tool):
        errors.append(f"Unsupported destination tool: {tool}")
        return errors

    if not supports_destination(tool):
        errors.append(f"{tool} cannot be used as a destination")
        return errors

    if not is_destination_operation_allowed(tool, operation):
        errors.append(f"Operation '{operation}' is not allowed for {tool} as destination")

    return errors


# -----------------------------
# Per-user connection check
# -----------------------------

def check_connections(plan, user_id):
    """
    CHANGED:
    Checks required Slack/Gmail connections for this logged-in user.

    No global fallback.
    """
    user_id = require_user_id(user_id)

    required_tools = collect_tools_from_plan(plan)

    required_connections = []
    missing_connections = []
    connect_actions = []

    for tool in required_tools:
        if not is_supported_tool(tool):
            continue

        if requires_connection(tool):
            required_connections.append(tool)

            # CHANGED:
            # Per-user connection check.
            if not is_user_connected(user_id, tool):
                missing_connections.append(tool)
                connect_actions.append({
                    "tool": tool,
                    "connect_url": get_connect_url(tool),
                    "message": f"Please connect {tool} first."
                })

    return {
        "required_connections": list(dict.fromkeys(required_connections)),
        "missing_connections": list(dict.fromkeys(missing_connections)),
        "connect_actions": connect_actions,
    }


# -----------------------------
# Per-user n8n credential check
# -----------------------------

def check_n8n_credentials(plan, user_id):
    """
    CHANGED:
    Checks whether n8n credentials are available.

    Rules:
    - Gmail/Slack credentials are per user.
    - OpenAI credential is app-level.
    """
    user_id = require_user_id(user_id)

    required_credentials = collect_required_credentials(plan)

    # CHANGED:
    # Per-user Gmail/Slack credential check.
    # OpenAI remains app-level inside credential_manager.
    missing_credentials = get_required_missing_user_credentials(
        user_id=user_id,
        required_tools=required_credentials
    )

    credential_actions = []

    for tool in missing_credentials:
        credential_actions.append({
            "tool": tool,
            "message": f"Please connect {tool} credential from the app first."
        })

    return {
        "required_credentials": required_credentials,
        "missing_credentials": missing_credentials,
        "credential_actions": credential_actions,
    }


# -----------------------------
# Main resolver function
# -----------------------------

def resolve_plan(plan, user_id):
    """
    Main resolver function.

    Input:
      planner.py output
      user_id from logged-in authorized user

    Output status:
      - ok
      - connection_required
      - credential_required
      - unsupported
      - not_workflow
      - invalid_plan
    """

    try:
        user_id = require_user_id(user_id)
    except Exception as error:
        result = {
            "status": "authorization_required",
            "can_continue": False,
            "errors": [safe_text(error)],
            "message": "Please log in before creating workflows.",
        }
        write_resolver_log(plan, result, user_id="")
        return result

    if not isinstance(plan, dict):
        result = {
            "status": "invalid_plan",
            "can_continue": False,
            "errors": ["plan must be a dictionary"],
            "message": "Invalid planner output.",
        }
        write_resolver_log(plan, result, user_id=user_id)
        return result

    decision = safe_text(plan.get("decision"))

    # Direct answer and follow-up do not need tool resolving.
    if decision in {"direct_answer", "follow_up", "cannot_execute"}:
        result = {
            "status": "not_workflow",
            "can_continue": False,
            "decision": decision,
            "errors": [],
            "message": "No workflow resolving needed for this decision.",
        }
        write_resolver_log(plan, result, user_id=user_id)
        return result

    errors = []

    # Validate source tools and operations.
    errors.extend(validate_sources(plan))

    # Validate destination tool and operation.
    errors.extend(validate_destination(plan))

    if errors:
        result = {
            "status": "unsupported",
            "can_continue": False,
            "errors": errors,
            "message": "Planner requested unsupported tool or operation.",
        }
        write_resolver_log(plan, result, user_id=user_id)
        return result

    connection_result = check_connections(plan, user_id)

    if connection_result["missing_connections"]:
        result = {
            "status": "connection_required",
            "can_continue": False,
            "errors": [],
            "required_connections": connection_result["required_connections"],
            "missing_connections": connection_result["missing_connections"],
            "connect_actions": connection_result["connect_actions"],
            "message": "One or more required tools are not connected for this user.",
        }
        write_resolver_log(plan, result, user_id=user_id)
        return result

    # After user-level tool connection passes, check n8n credential availability.
    credential_result = check_n8n_credentials(plan, user_id)

    if credential_result["missing_credentials"]:
        result = {
            "status": "credential_required",
            "can_continue": False,
            "errors": [],
            "required_connections": connection_result["required_connections"],
            "missing_connections": [],
            "connect_actions": [],
            "required_credentials": credential_result["required_credentials"],
            "missing_credentials": credential_result["missing_credentials"],
            "credential_actions": credential_result["credential_actions"],
            "message": "One or more required n8n credentials are not available for this user.",
        }
        write_resolver_log(plan, result, user_id=user_id)
        return result

    result = {
        "status": "ok",
        "can_continue": True,
        "errors": [],
        "required_connections": connection_result["required_connections"],
        "missing_connections": [],
        "connect_actions": [],
        "required_credentials": credential_result["required_credentials"],
        "missing_credentials": [],
        "credential_actions": [],
        "message": "Planner output is valid, user tools are connected, and required n8n credentials are available.",
    }

    write_resolver_log(plan, result, user_id=user_id)
    return result


# -----------------------------
# Manual test
# -----------------------------

if __name__ == "__main__":
    sample_user_id = input("USER ID: ").strip()

    sample_plan = {
        "decision": "workflow_required",
        "intent": "read_multiple_sources_and_display",
        "summary": "Read Slack updates and unread Gmail emails",
        "sources": [
            {
                "tool": "slack",
                "operation": "read",
                "target": "#engineering",
                "query": "updates",
                "filters": {}
            },
            {
                "tool": "gmail",
                "operation": "read",
                "target": "inbox",
                "query": "unread emails",
                "filters": {
                    "status": "unread"
                }
            }
        ],
        "processing": {
            "required": True,
            "operation": "summarize",
            "instruction": "Summarize Slack updates and unread Gmail emails"
        },
        "destination": {
            "tool": "chat",
            "operation": "return",
            "target": "",
            "recipient": ""
        },
        "trigger": {
            "type": "manual",
            "value": ""
        },
        "condition": {
            "required": False,
            "field": "",
            "operator": "",
            "value": "",
            "unit": ""
        },
        "missing_fields": [],
        "follow_up_question": "",
        "direct_answer": "",
        "requires_connection": ["slack", "gmail"]
    }

    result = resolve_plan(sample_plan, user_id=sample_user_id)
    print(json.dumps(result, indent=2, ensure_ascii=False))