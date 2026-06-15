"""
builder_preview.py

Converts composer workflow_steps into a safe n8n mapping preview.

This file does NOT:
- call n8n API
- create n8n workflows
- call Slack API
- call Gmail API
- execute anything

Input:
- workflow_steps from composer.py

Output:
- n8n node preview only
"""

import json
import os
from datetime import datetime


LOG_DIR = "logs"
BUILDER_PREVIEW_LOG_FILE = os.path.join(LOG_DIR, "builder_preview_logs.jsonl")


# -----------------------------
# ADDED: basic n8n node mapping
# -----------------------------

N8N_NODE_MAPPING = {
    "slack": {
        "node_type": "n8n-nodes-base.slack",
        # ADDED:
        # This version came from your exported n8n Slack workflow.
        "type_version": 2.4,
        "credential_type": "slackOAuth2Api",
        "supported_actions": {
            # CHANGED:
            # Your exported n8n Slack node uses operation: "search"
            # for reading/searching messages.
            "read": "search",
            "send": "postMessage",
            "search": "search"
        }
    },
    "gmail": {
        "node_type": "n8n-nodes-base.gmail",
        "type_version": 2.2,
        "credential_type": "gmailOAuth2",
        "supported_actions": {
            "read": "getAll",
            "send": "send",
            "create": "createDraft",
            "search": "getAll"
        }
    },
    "llm": {
        # CHANGED:
        # Your exported n8n OpenAI node uses LangChain OpenAI node.
        "node_type": "@n8n/n8n-nodes-langchain.openAi",

        # CHANGED:
        # Your exported OpenAI node uses typeVersion 2.3.
        "type_version": 2.3,

        "credential_type": "openAiApi",
        "supported_actions": {
            # CHANGED:
            # All LLM processing actions map to the same n8n OpenAI
            # "Message a model" operation.
            #
            # Why:
            # - planner may choose summarize, generate, transform, analyze,
            #   classify, extract, compare, filter, write, or create
            # - builder.py already sends all llm steps to create_llm_node()
            # - preview should not block a valid LLM action like "transform"
            "summarize": "message",
            "generate": "message",
            "transform": "message",
            "analyze": "message",
            "classify": "message",
            "extract": "message",
            "compare": "message",
            "filter": "message",
            "write": "message",
            "create": "message"
        }
    },
    "chat": {
        "node_type": "internal-chat-response",
        "type_version": 1,
        "credential_type": "",
        "supported_actions": {
            "return": "return"
        }
    }
}


def safe_text(value):
    """
    ADDED:
    Converts any value into clean text.
    """
    if value is None:
        return ""
    return str(value).strip()


def write_builder_preview_log(workflow_steps, result):
    """
    ADDED:
    Saves builder preview input/output for debugging.
    """
    try:
        os.makedirs(LOG_DIR, exist_ok=True)

        log_record = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "workflow_steps": workflow_steps,
            "builder_preview_output": result
        }

        with open(BUILDER_PREVIEW_LOG_FILE, "a", encoding="utf-8") as file:
            file.write(json.dumps(log_record, ensure_ascii=False) + "\n")

    except Exception:
        # Logging should never break app
        pass


def get_mapping_for_step(step):
    """
    ADDED:
    Finds the n8n mapping for one workflow step.
    """
    tool = safe_text(step.get("tool")).lower()
    action = safe_text(step.get("action")).lower()

    tool_mapping = N8N_NODE_MAPPING.get(tool)

    if not tool_mapping:
        return {
            "mapped": False,
            "error": f"No n8n mapping found for tool: {tool}"
        }

    supported_actions = tool_mapping.get("supported_actions", {})
    n8n_operation = supported_actions.get(action)

    if not n8n_operation:
        return {
            "mapped": False,
            "error": f"Action '{action}' is not mapped for tool: {tool}"
        }

    return {
        "mapped": True,
        "node_type": tool_mapping.get("node_type", ""),
        "type_version": tool_mapping.get("type_version", 1),
        "credential_type": tool_mapping.get("credential_type", ""),
        "n8n_operation": n8n_operation
    }


def create_node_preview(step):
    """
    ADDED:
    Converts one workflow step into one n8n node preview.
    """
    mapping = get_mapping_for_step(step)

    node_preview = {
        "step": step.get("step"),
        "workflow_step_type": step.get("type"),
        "tool": step.get("tool"),
        "action": step.get("action"),
        "description": step.get("description"),
        "input_from": step.get("input_from", []),
        "output_key": step.get("output_key", ""),
        "mapped": mapping.get("mapped", False)
    }

    if mapping.get("mapped") is True:
        node_preview.update({
            "n8n_node_name": f"{step.get('tool')}_{step.get('action')}_{step.get('step')}",
            "n8n_node_type": mapping.get("node_type", ""),
            "n8n_type_version": mapping.get("type_version", 1),
            "n8n_operation": mapping.get("n8n_operation", ""),
            "credential_type": mapping.get("credential_type", ""),
            "status": "mapped"
        })
    else:
        node_preview.update({
            "status": "mapping_missing",
            "error": mapping.get("error", "Unknown mapping error")
        })

    return node_preview


def build_preview(workflow_steps):
    """
    ADDED:
    Main builder preview function.

    Converts workflow_steps into n8n node preview only.
    """

    if not isinstance(workflow_steps, list):
        result = {
            "status": "invalid_input",
            "can_continue": False,
            "errors": ["workflow_steps must be a list"],
            "nodes_preview": []
        }
        write_builder_preview_log(workflow_steps, result)
        return result

    nodes_preview = []
    errors = []

    for step in workflow_steps:
        if not isinstance(step, dict):
            errors.append("Invalid workflow step found. Step must be a dictionary.")
            continue

        node_preview = create_node_preview(step)
        nodes_preview.append(node_preview)

        if node_preview.get("mapped") is not True:
            errors.append(node_preview.get("error", "Unknown mapping error"))

    if errors:
        result = {
            "status": "mapping_failed",
            "can_continue": False,
            "errors": errors,
            "nodes_preview": nodes_preview,
            "message": "Some workflow steps could not be mapped to n8n preview nodes."
        }
    else:
        result = {
            "status": "ok",
            "can_continue": True,
            "errors": [],
            "nodes_preview": nodes_preview,
            "message": "Builder preview created successfully."
        }

    write_builder_preview_log(workflow_steps, result)
    return result


# -----------------------------
# Manual test
# -----------------------------

if __name__ == "__main__":
    sample_workflow_steps = [
        {
            "step": 1,
            "type": "source",
            "tool": "slack",
            "action": "read",
            "target": "#engineering",
            "query": "updates",
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
                "status": "unread"
            },
            "description": "read from gmail",
            "input_from": [],
            "output_key": "gmail_read_2"
        },
        {
            "step": 3,
            "type": "processing",
            "tool": "llm",
            "action": "transform",
            "instruction": "Format unread Gmail emails into a Slack-friendly message",
            "description": "transform using AI",
            "input_from": [
                "gmail_read_2"
            ],
            "output_key": "llm_transform_3"
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
                "llm_transform_3"
            ],
            "output_key": "chat_return_4"
        }
    ]

    result = build_preview(sample_workflow_steps)
    print(json.dumps(result, indent=2, ensure_ascii=False))
