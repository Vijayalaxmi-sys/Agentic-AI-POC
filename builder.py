"""
builder.py

Builds real n8n-style workflow JSON from composer workflow_steps.

PRODUCTION RULE:
- Gmail/Slack n8n credentials are attached per logged-in user_id.
- Slack channel resolution uses the logged-in user's Slack OAuth token.
- OpenAI credential remains app-level.
- No global Gmail/Slack fallback.

This file does NOT:
- call n8n API
- create workflow in n8n
- execute workflow
- call Gmail API directly
- call OpenAI API directly

It can call Slack conversations.list only to resolve channel name -> channel ID,
using the logged-in user's Slack token.
"""

import json
import os
import uuid
from datetime import datetime

import requests

# CHANGED:
# Per-user connection lookup only. No global load_connections fallback.
from connection_manager import load_user_connections

# CHANGED:
# Per-user Gmail/Slack credentials, app-level OpenAI credential.
from credential_manager import get_user_credential, get_app_openai_credential

from config import PLANNER_MODEL


LOG_DIR = "logs"
BUILDER_LOG_FILE = os.path.join(LOG_DIR, "builder_logs.jsonl")


# -----------------------------
# Helpers
# -----------------------------

def safe_text(value):
    """
    Converts any value into clean text.
    """
    if value is None:
        return ""
    return str(value).strip()


def require_user_id(user_id):
    """
    ADDED:
    Builder must know which logged-in authorized user owns this workflow.

    No user_id means we cannot safely attach Gmail/Slack credentials.
    """
    clean_user_id = safe_text(user_id)

    if not clean_user_id:
        raise ValueError("user_id is required in builder. No global Gmail/Slack fallback is allowed.")

    return clean_user_id


def make_id():
    """
    Creates unique node id for n8n-style nodes.
    """
    return str(uuid.uuid4())


def safe_int(value, default_value=5):
    """
    Converts value to safe positive integer.
    """
    try:
        number = int(value)

        if number <= 0:
            return default_value

        return number

    except Exception:
        return default_value


def get_step_limit(step, default_value=5):
    """
    Gets dynamic limit from workflow step.
    """
    if not isinstance(step, dict):
        return default_value

    if "limit" in step:
        return safe_int(step.get("limit"), default_value)

    filters = step.get("filters", {})

    if isinstance(filters, dict) and "limit" in filters:
        return safe_int(filters.get("limit"), default_value)

    return default_value


def write_builder_log(workflow_steps, result, user_id=""):
    """
    Saves builder input/output for debugging.
    """
    try:
        os.makedirs(LOG_DIR, exist_ok=True)

        log_record = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "user_id": safe_text(user_id),
            "workflow_steps": workflow_steps,
            "builder_output": result
        }

        with open(BUILDER_LOG_FILE, "a", encoding="utf-8") as file:
            file.write(json.dumps(log_record, ensure_ascii=False) + "\n")

    except Exception:
        pass


def get_node_credentials(tool_name, user_id):
    """
    CHANGED:
    Returns n8n node credentials format.

    Rules:
    - gmail -> per-user Gmail credential
    - slack -> per-user Slack credential
    - openai/llm -> app-level OpenAI credential
    """
    tool_name = safe_text(tool_name).lower()

    if tool_name in ["gmail", "slack"]:
        user_id = require_user_id(user_id)
        credential = get_user_credential(user_id, tool_name)
    elif tool_name in ["openai", "llm"]:
        credential = get_app_openai_credential()
    else:
        credential = {}

    if not isinstance(credential, dict) or not credential.get("connected"):
        return {}

    credential_type = safe_text(credential.get("credential_type"))
    credential_id = safe_text(credential.get("credential_id"))
    credential_name = safe_text(credential.get("credential_name"))

    if not credential_type or not credential_id or not credential_name:
        return {}

    return {
        credential_type: {
            "id": credential_id,
            "name": credential_name
        }
    }


def attach_credentials(node, tool_name, user_id):
    """
    CHANGED:
    Attaches n8n credentials to a node.

    Gmail/Slack use the logged-in user's n8n credential.
    OpenAI uses the app-level credential.
    """
    credentials = get_node_credentials(tool_name, user_id)

    if credentials:
        node["credentials"] = credentials

    return node


# -----------------------------
# n8n expression helpers
# -----------------------------

def has_previous_input(step):
    input_from = step.get("input_from", [])
    return isinstance(input_from, list) and len(input_from) > 0


def build_current_json_text_expression(fallback_text=""):
    """
    Creates an n8n expression that extracts useful text from the incoming item.

    The leading '=' is required by n8n so the expression is evaluated.
    """
    fallback_text = safe_text(fallback_text) or "No generated content was available."
    fallback_json = json.dumps(fallback_text)

    return f"""={{{{ (() => {{
  const j = $json || {{}};

  const candidates = [
    j.output_text,
    j.text,
    j.content,
    j.message,
    j.message?.content,
    j.response,
    j.response?.text,
    j.result,
    j.data,
    j.data?.text,
    j.choices?.[0]?.message?.content,
    j.output?.[0]?.content?.[0]?.text,
    j.output?.[0]?.content?.[0]?.content,
    j.output?.[0]?.text
  ];

  for (const value of candidates) {{
    if (typeof value === 'string' && value.trim()) {{
      return value;
    }}

    if (value && typeof value === 'object') {{
      const asJson = JSON.stringify(value, null, 2);
      if (asJson && asJson !== '{{}}') {{
        return asJson;
      }}
    }}
  }}

  const fullJson = JSON.stringify(j, null, 2);
  if (fullJson && fullJson !== '{{}}') {{
    return fullJson;
  }}

  return {fallback_json};
}})() }}}}"""


def build_llm_prompt_expression(instruction):
    """
    Creates a dynamic n8n expression for the OpenAI node prompt.
    """
    instruction = safe_text(instruction)

    if not instruction:
        instruction = "Process and summarize the previous workflow results."

    instruction_json = json.dumps(instruction)

    return f"""={{{{ (() => {{
  const instruction = {instruction_json};
  const inputData = $input.all().map(item => item.json);

  return [
    "Instruction:",
    instruction,
    "",
    "Workflow input data:",
    JSON.stringify(inputData, null, 2),
    "",
    "Return only the final user-facing message.",
    "Do not mention JSON unless needed.",
    "Do not say you do not have access; use the workflow input data above."
  ].join("\\n");
}})() }}}}"""


# -----------------------------
# n8n trigger node creators
# -----------------------------

def create_manual_trigger_node():
    return {
        "parameters": {},
        "type": "n8n-nodes-base.manualTrigger",
        "typeVersion": 1,
        "position": [-500, 0],
        "id": make_id(),
        "name": "When clicking ‘Execute workflow’"
    }


def create_webhook_trigger_node(webhook_path=""):
    clean_path = safe_text(webhook_path)

    if not clean_path:
        clean_path = f"dynamic-ai-{uuid.uuid4().hex[:12]}"

    clean_path = clean_path.replace("/", "").replace(":", "").strip()

    return {
        "parameters": {
            "httpMethod": "POST",
            "path": clean_path,
            "responseMode": "lastNode",
            "options": {
                "responseData": "firstEntryJson"
            }
        },
        "type": "n8n-nodes-base.webhook",
        "typeVersion": 2.1,
        "position": [-500, 0],
        "id": make_id(),
        "name": "Webhook Trigger",
        "webhookId": make_id()
    }


# -----------------------------
# Slack helpers
# -----------------------------

def build_slack_search_query(step):
    target = safe_text(step.get("target"))
    query = safe_text(step.get("query"))

    channel = target.replace("#", "").strip()

    if channel and query:
        return f"in:{channel} {query}"

    if channel:
        return f"in:{channel}"

    if query:
        return query

    return ""


def normalize_slack_channel_name(channel_value):
    channel_value = safe_text(channel_value)

    if not channel_value:
        return ""

    return channel_value.replace("#", "").strip()


def looks_like_slack_channel_id(channel_value):
    channel_value = safe_text(channel_value).upper()

    return (
        len(channel_value) >= 8
        and channel_value[0] in ["C", "G", "D"]
        and "#" not in channel_value
        and " " not in channel_value
    )


def resolve_slack_channel_id(channel_target, user_id):
    """
    CHANGED:
    Resolves Slack channel name using this user's Slack OAuth token.
    """
    user_id = require_user_id(user_id)
    original_target = safe_text(channel_target)

    if not original_target:
        return {
            "channel_id": "",
            "resolved": False,
            "reason": "missing_channel_target"
        }

    if looks_like_slack_channel_id(original_target):
        return {
            "channel_id": original_target,
            "resolved": True,
            "reason": "already_channel_id"
        }

    channel_name = normalize_slack_channel_name(original_target)

    if not channel_name:
        return {
            "channel_id": original_target,
            "resolved": False,
            "reason": "invalid_channel_target"
        }

    try:
        connections = load_user_connections(user_id)
        slack_connection = connections.get("slack", {})
        access_token = safe_text(slack_connection.get("access_token"))

        if not access_token:
            return {
                "channel_id": original_target,
                "resolved": False,
                "reason": "missing_user_slack_access_token"
            }

        headers = {
            "Authorization": f"Bearer {access_token}"
        }

        cursor = ""

        while True:
            params = {
                "exclude_archived": "true",
                "limit": 200,
                "types": "public_channel,private_channel"
            }

            if cursor:
                params["cursor"] = cursor

            response = requests.get(
                "https://slack.com/api/conversations.list",
                headers=headers,
                params=params,
                timeout=30
            )

            data = response.json()

            if not data.get("ok"):
                return {
                    "channel_id": original_target,
                    "resolved": False,
                    "reason": safe_text(data.get("error")) or "slack_api_error"
                }

            channels = data.get("channels", [])

            for channel in channels:
                if not isinstance(channel, dict):
                    continue

                if safe_text(channel.get("name")).lower() == channel_name.lower():
                    channel_id = safe_text(channel.get("id"))

                    if channel_id:
                        return {
                            "channel_id": channel_id,
                            "resolved": True,
                            "reason": "matched_channel_name"
                        }

            metadata = data.get("response_metadata", {})
            cursor = safe_text(metadata.get("next_cursor"))

            if not cursor:
                break

        return {
            "channel_id": original_target,
            "resolved": False,
            "reason": "channel_not_found"
        }

    except Exception as error:
        return {
            "channel_id": original_target,
            "resolved": False,
            "reason": f"resolver_exception: {safe_text(error)}"
        }


# -----------------------------
# n8n action node creators
# -----------------------------

def create_slack_read_node(step, position, user_id):
    user_id = require_user_id(user_id)

    target = safe_text(step.get("target"))
    limit = get_step_limit(step, default_value=5)

    channel_resolution = resolve_slack_channel_id(target, user_id)
    channel_id = safe_text(channel_resolution.get("channel_id")) or target

    node = {
        "parameters": {
            "authentication": "oAuth2",
            "resource": "channel",
            "operation": "history",
            "channelId": {
                "__rl": True,
                "mode": "id",
                "value": channel_id
            },
            "returnAll": False,
            "limit": limit,
            "filters": {}
        },
        "type": "n8n-nodes-base.slack",
        "typeVersion": 2.4,
        "position": position,
        "id": make_id(),
        "name": f"Slack read {step.get('step')}"
    }

    return attach_credentials(node, "slack", user_id)


def create_slack_send_node(step, position, user_id):
    user_id = require_user_id(user_id)

    target = safe_text(step.get("target"))

    channel_resolution = resolve_slack_channel_id(target, user_id)
    channel_id = safe_text(channel_resolution.get("channel_id")) or target
    channel_mode = "id" if channel_resolution.get("resolved") else "list"

    direct_message_text = (
        safe_text(step.get("message"))
        or safe_text(step.get("query"))
    )

    if has_previous_input(step):
        message_text = build_current_json_text_expression(
            fallback_text=direct_message_text or "Generated message was empty."
        )
    else:
        message_text = direct_message_text or "Generated message was empty."

    node = {
        "parameters": {
            "authentication": "oAuth2",
            "select": "channel",
            "channelId": {
                "__rl": True,
                "mode": channel_mode,
                "value": channel_id
            },
            "text": message_text,
            "otherOptions": { "includeLinkToWorkflow": False}
        },
        "type": "n8n-nodes-base.slack",
        "typeVersion": 2.4,
        "position": position,
        "id": make_id(),
        "name": f"Slack send {step.get('step')}"
    }

    return attach_credentials(node, "slack", user_id)


def create_gmail_read_node(step, position, user_id):
    user_id = require_user_id(user_id)

    filters = step.get("filters", {})

    if not isinstance(filters, dict):
        filters = {}

    limit = get_step_limit(step, default_value=5)

    clean_filters = dict(filters)
    clean_filters.pop("limit", None)

    node = {
        "parameters": {
            "operation": "getAll",
            "limit": limit,
            "filters": clean_filters
        },
        "type": "n8n-nodes-base.gmail",
        "typeVersion": 2.2,
        "position": position,
        "id": make_id(),
        "name": f"Gmail read {step.get('step')}"
    }

    return attach_credentials(node, "gmail", user_id)


def create_gmail_send_node(step, position, user_id):
    user_id = require_user_id(user_id)

    recipient = safe_text(step.get("recipient")) or safe_text(step.get("target"))
    subject = safe_text(step.get("subject")) or "Generated email"

    direct_message = safe_text(step.get("message"))

    if has_previous_input(step):
        message = build_current_json_text_expression(
            fallback_text=direct_message or "Generated email body was empty."
        )
    else:
        message = direct_message or "Generated email body was empty."

    node = {
        "parameters": {
            "sendTo": recipient,
            "subject": subject,
            "emailType": "text",
            "message": message,
            "options": {"appendAttribution": False}
        },
        "type": "n8n-nodes-base.gmail",
        "typeVersion": 2.2,
        "position": position,
        "id": make_id(),
        "name": f"Gmail send {step.get('step')}"
    }

    return attach_credentials(node, "gmail", user_id)


def create_llm_node(step, position, user_id):
    instruction = safe_text(step.get("instruction"))

    if not instruction:
        instruction = "Process and summarize the previous workflow results."

    if has_previous_input(step):
        prompt_content = build_llm_prompt_expression(instruction)
    else:
        prompt_content = instruction

    node = {
        "parameters": {
            "modelId": {
                "__rl": True,
                "mode": "list",
                "value": PLANNER_MODEL
            },
            "responses": {
                "values": [
                    {
                        "content": prompt_content
                    }
                ]
            },
            "builtInTools": {},
            "options": { "appendAttribution": False}
        },
        "type": "@n8n/n8n-nodes-langchain.openAi",
        "typeVersion": 2.3,
        "position": position,
        "id": make_id(),
        "name": f"LLM {step.get('action')} {step.get('step')}",
        "executeOnce": True,
        "retryOnFail": True,
        "maxTries": 3,
        "waitBetweenTries": 3000
    }

    return attach_credentials(node, "openai", user_id)


def create_node_from_step(step, position, user_id):
    user_id = require_user_id(user_id)

    tool = safe_text(step.get("tool")).lower()
    action = safe_text(step.get("action")).lower()

    if tool == "slack" and action in ["read", "search"]:
        return create_slack_read_node(step, position, user_id)

    if tool == "slack" and action == "send":
        return create_slack_send_node(step, position, user_id)

    if tool == "gmail" and action in ["read", "search"]:
        return create_gmail_read_node(step, position, user_id)

    if tool == "gmail" and action in ["send", "create"]:
        return create_gmail_send_node(step, position, user_id)

    if tool == "llm":
        return create_llm_node(step, position, user_id)

    if tool == "chat":
        return None

    return {
        "parameters": {
            "error": f"Unsupported step: tool={tool}, action={action}"
        },
        "type": "internal-unsupported-placeholder",
        "typeVersion": 1,
        "position": position,
        "id": make_id(),
        "name": f"Unsupported {tool} {action}"
    }


# -----------------------------
# Connections builder
# -----------------------------

def build_linear_connections(nodes):
    connections = {}

    for index in range(len(nodes) - 1):
        current_node = nodes[index]["name"]
        next_node = nodes[index + 1]["name"]

        connections[current_node] = {
            "main": [
                [
                    {
                        "node": next_node,
                        "type": "main",
                        "index": 0
                    }
                ]
            ]
        }

    return connections


# -----------------------------
# Main builder function
# -----------------------------

def build_n8n_workflow(
    workflow_steps,
    workflow_name="Generated Dynamic Workflow",
    trigger_mode="webhook",
    webhook_path="",
    user_id=""
):
    """
    Converts workflow_steps into n8n-style workflow JSON.

    CHANGED:
    user_id is required so builder can:
    - resolve Slack channel IDs using that user's Slack token
    - attach that user's Gmail/Slack n8n credentials
    - avoid global/admin fallback

    ADDED:
    Safe workflow name handling.
    n8n requires workflow name to be 1 to 128 characters.
    Long planner summaries can break workflow creation, so we truncate safely.
    """

    def safe_workflow_name(name, max_length=120):
        """
        Keeps workflow name inside n8n limit.
        n8n requires workflow name to be 1 to 128 characters.
        """
        cleaned = safe_text(name)

        if not cleaned:
            return "Generated Dynamic Workflow"

        # Remove extra spaces/newlines
        cleaned = " ".join(cleaned.split())

        if not cleaned:
            return "Generated Dynamic Workflow"

        if len(cleaned) <= max_length:
            return cleaned

        return cleaned[:max_length].rstrip() + "..."

    try:
        user_id = require_user_id(user_id)
    except Exception as error:
        result = {
            "status": "authorization_required",
            "can_continue": False,
            "errors": [safe_text(error)],
            "workflow_json": {},
            "message": "Logged-in user is required before building a workflow."
        }
        write_builder_log(workflow_steps, result, user_id="")
        return result

    if not isinstance(workflow_steps, list):
        result = {
            "status": "invalid_input",
            "can_continue": False,
            "errors": ["workflow_steps must be a list"],
            "workflow_json": {}
        }
        write_builder_log(workflow_steps, result, user_id=user_id)
        return result

    nodes = []
    skipped_steps = []

    resolved_trigger_mode = safe_text(trigger_mode).lower() or "webhook"
    resolved_webhook_path = safe_text(webhook_path)

    if resolved_trigger_mode == "webhook":
        trigger_node = create_webhook_trigger_node(resolved_webhook_path)
        resolved_webhook_path = trigger_node["parameters"]["path"]
    else:
        trigger_node = create_manual_trigger_node()
        resolved_webhook_path = ""

    nodes.append(trigger_node)

    x = -250
    y = 0

    for step in workflow_steps:
        if not isinstance(step, dict):
            continue

        position = [x, y]
        node = create_node_from_step(step, position, user_id)

        if node is not None:
            nodes.append(node)
            x += 250
        else:
            skipped_steps.append({
                "step": step.get("step"),
                "tool": step.get("tool"),
                "action": step.get("action"),
                "reason": "Handled by app/backend, not created as n8n node"
            })

    connections = build_linear_connections(nodes)

    # ADDED:
    # n8n rejects workflow names longer than 128 characters.
    # Planner summaries can be long, especially when emails are included.
    cleaned_workflow_name = safe_workflow_name(workflow_name)

    workflow_json = {
        "name": cleaned_workflow_name,
        "nodes": nodes,
        "pinData": {},
        "connections": connections,
        "active": False,
        "settings": {
            "executionOrder": "v1",
            "binaryMode": "separate"
        },
        "tags": []
    }

    result = {
        "status": "ok",
        "can_continue": True,
        "errors": [],
        "workflow_json": workflow_json,
        "trigger_mode": resolved_trigger_mode,
        "webhook_path": resolved_webhook_path if resolved_trigger_mode == "webhook" else "",
        "skipped_steps": skipped_steps,
        "message": "n8n workflow JSON generated successfully."
    }

    write_builder_log(workflow_steps, result, user_id=user_id)
    return result

# -----------------------------
# Manual test
# -----------------------------

if __name__ == "__main__":
    sample_user_id = input("USER ID: ").strip()

    sample_workflow_steps = [
        {
            "step": 1,
            "type": "source",
            "tool": "slack",
            "action": "read",
            "target": "#new-channel",
            "query": "updates",
            "limit": 12,
            "filters": {},
            "description": "read from slack",
            "input_from": [],
            "output_key": "slack_read_1"
        },
        {
            "step": 2,
            "type": "source",
            "tool": "gmail",
            "action": "read",
            "target": "inbox",
            "query": "unread emails",
            "filters": {
                "limit": 8
            },
            "description": "read from gmail",
            "input_from": [],
            "output_key": "gmail_read_2"
        },
        {
            "step": 3,
            "type": "processing",
            "tool": "llm",
            "action": "summarize",
            "instruction": "Summarize Slack updates and unread Gmail emails",
            "description": "summarize using AI",
            "input_from": [
                "slack_read_1",
                "gmail_read_2"
            ],
            "output_key": "llm_summarize_3"
        },
        {
            "step": 4,
            "type": "destination",
            "tool": "chat",
            "action": "return",
            "target": "",
            "recipient": "",
            "description": "return to chat",
            "input_from": [
                "llm_summarize_3"
            ],
            "output_key": "chat_return_4"
        }
    ]

    result = build_n8n_workflow(
        sample_workflow_steps,
        trigger_mode="webhook",
        user_id=sample_user_id
    )

    print(json.dumps(result, indent=2, ensure_ascii=False))
