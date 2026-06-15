"""
composer.py

Converts validated planner output into simple workflow steps.

This file does NOT:
- call Slack API
- call Gmail API
- call n8n
- create n8n workflow JSON
- execute anything

Input:
- planner.py output

Output:
- workflow_steps

Flow:
sources[] -> processing -> destination
"""

import json
import os
from datetime import datetime


LOG_DIR = "logs"
COMPOSER_LOG_FILE = os.path.join(LOG_DIR, "composer_logs.jsonl")


# -----------------------------
# ADDED: safe helpers
# -----------------------------

def safe_text(value):
    """
    ADDED:
    Converts None or any value into clean string.
    """
    if value is None:
        return ""
    return str(value).strip()


def safe_int(value, default_value=None):
    """
    ADDED:
    Converts value into positive integer.

    Used for dynamic user limits:
    - read 10 emails
    - latest 20 Slack messages
    """
    try:
        number = int(value)

        if number <= 0:
            return default_value

        return number

    except Exception:
        return default_value


def get_limit_from_object(data):
    """
    ADDED:
    Extracts limit from planner object.

    Priority:
    1. data["limit"]
    2. data["filters"]["limit"]

    Returns None if no valid limit exists.
    """
    if not isinstance(data, dict):
        return None

    if "limit" in data:
        return safe_int(data.get("limit"), None)

    filters = data.get("filters", {})

    if isinstance(filters, dict) and "limit" in filters:
        return safe_int(filters.get("limit"), None)

    return None


# -----------------------------
# ADDED: composer logging
# -----------------------------

def write_composer_log(plan, result):
    """
    ADDED:
    Saves composer input/output for debugging end-to-end flow.
    """
    try:
        os.makedirs(LOG_DIR, exist_ok=True)

        log_record = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "planner_output": plan,
            "composer_output": result,
        }

        with open(COMPOSER_LOG_FILE, "a", encoding="utf-8") as file:
            file.write(json.dumps(log_record, ensure_ascii=False) + "\n")

    except Exception:
        # Logging should never break app
        pass


# -----------------------------
# ADDED: create source step
# -----------------------------

def create_source_step(step_number, source):
    """
    CHANGED:
    Converts one source object into one workflow step.

    Dynamic fields passed forward:
    - limit
    - filters
    """
    tool = safe_text(source.get("tool")).lower()
    operation = safe_text(source.get("operation")).lower()

    filters = source.get("filters", {})
    if not isinstance(filters, dict):
        filters = {}

    step = {
        "step": step_number,
        "type": "source",
        "tool": tool,
        "action": operation,
        "target": safe_text(source.get("target")),
        "query": safe_text(source.get("query")),
        "filters": filters,
        "description": f"{operation} from {tool}",
        "input_from": [],
        "output_key": f"{tool}_{operation}_{step_number}"
    }

    # ADDED:
    # Pass user/planner limit forward to builder.
    limit = get_limit_from_object(source)
    if limit is not None:
        step["limit"] = limit

    return step


# -----------------------------
# ADDED: create processing step
# -----------------------------

def create_processing_step(step_number, processing, previous_output_keys):
    """
    ADDED:
    Converts processing object into one AI/LLM workflow step.

    Example:
    summarize Slack + Gmail results
    generate project update
    """
    operation = safe_text(processing.get("operation")).lower()
    instruction = safe_text(processing.get("instruction"))

    return {
        "step": step_number,
        "type": "processing",
        "tool": "llm",
        "action": operation,
        "instruction": instruction,
        "description": f"{operation} using AI",
        "input_from": previous_output_keys,
        "output_key": f"llm_{operation}_{step_number}"
    }


# -----------------------------
# ADDED: create destination step
# -----------------------------

def create_destination_step(step_number, destination, previous_output_keys):
    """
    CHANGED:
    Converts destination object into final delivery step.

    Dynamic fields passed forward:
    - subject
    - message
    - query
    - limit if present

    FIXED:
    Direct Slack/Gmail send requests can place the user text in different
    planner fields depending on model output. Example:
        send hello from dynamic workflow to Slack #new-channel

    Sometimes planner returns the text as destination["query"] instead of
    destination["message"]. If composer does not pass that forward, builder.py
    sees no message and sends the fallback:
        Generated message was empty.
    """
    tool = safe_text(destination.get("tool")).lower()
    operation = safe_text(destination.get("operation")).lower()

    destination_query = safe_text(destination.get("query"))
    destination_message = safe_text(destination.get("message"))
    destination_instruction = safe_text(destination.get("instruction"))

    step = {
        "step": step_number,
        "type": "destination",
        "tool": tool,
        "action": operation,
        "target": safe_text(destination.get("target")),
        "recipient": safe_text(destination.get("recipient")),
        "description": f"{operation} to {tool}",
        "input_from": previous_output_keys,
        "output_key": f"{tool}_{operation}_{step_number}"
    }

    # ADDED:
    # Pass query forward because builder.py already checks step["query"]
    # for direct Slack send fallback.
    if destination_query:
        step["query"] = destination_query

    # ADDED:
    # These are useful for Gmail send / Slack send later.
    subject = safe_text(destination.get("subject"))

    if subject:
        step["subject"] = subject

    # FIXED:
    # Preserve the best available direct-send text.
    # Priority:
    # 1. destination.message
    # 2. destination.query
    # 3. destination.instruction
    #
    # This only affects destination steps. If previous_output_keys exist,
    # builder.py will still use previous node/LLM output for the body/text.
    message = destination_message or destination_query or destination_instruction

    if message:
        step["message"] = message

    limit = get_limit_from_object(destination)
    if limit is not None:
        step["limit"] = limit

    return step


# -----------------------------
# Main composer function
# -----------------------------

def compose_workflow(plan):
    """
    ADDED:
    Main composer function.

    Converts planner output into workflow_steps.

    It follows this order:
    1. source steps
    2. processing step
    3. destination step
    """

    if not isinstance(plan, dict):
        result = {
            "status": "invalid_plan",
            "can_continue": False,
            "errors": ["planner output must be a dictionary"],
            "workflow_steps": []
        }
        write_composer_log(plan, result)
        return result

    decision = safe_text(plan.get("decision"))

    if decision != "workflow_required":
        result = {
            "status": "not_workflow",
            "can_continue": False,
            "errors": [],
            "message": "Composer only runs for workflow_required plans.",
            "workflow_steps": []
        }
        write_composer_log(plan, result)
        return result

    workflow_steps = []
    previous_output_keys = []

    # 1. Source steps
    sources = plan.get("sources", [])
    if isinstance(sources, list):
        for source in sources:
            if not isinstance(source, dict):
                continue

            tool = safe_text(source.get("tool")).lower()
            if not tool or tool == "none":
                continue

            step = create_source_step(
                step_number=len(workflow_steps) + 1,
                source=source
            )
            workflow_steps.append(step)
            previous_output_keys.append(step["output_key"])

    # 2. Processing step
    processing = plan.get("processing", {})
    if isinstance(processing, dict) and processing.get("required") is True:
        operation = safe_text(processing.get("operation")).lower()

        if operation and operation != "none":
            step = create_processing_step(
                step_number=len(workflow_steps) + 1,
                processing=processing,
                previous_output_keys=previous_output_keys.copy()
            )
            workflow_steps.append(step)
            previous_output_keys = [step["output_key"]]

    # 3. Destination step
    destination = plan.get("destination", {})
    if isinstance(destination, dict):
        tool = safe_text(destination.get("tool")).lower()
        operation = safe_text(destination.get("operation")).lower()

        if tool and tool != "none" and operation and operation != "none":
            step = create_destination_step(
                step_number=len(workflow_steps) + 1,
                destination=destination,
                previous_output_keys=previous_output_keys.copy()
            )
            workflow_steps.append(step)

    result = {
        "status": "ok",
        "can_continue": True,
        "workflow_steps": workflow_steps,
        "message": "Workflow steps composed successfully."
    }

    write_composer_log(plan, result)
    return result


# -----------------------------
# Manual test
# -----------------------------

if __name__ == "__main__":
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
                "limit": 12,
                "filters": {}
            },
            {
                "tool": "gmail",
                "operation": "read",
                "target": "inbox",
                "query": "unread emails",
                "filters": {
                    "status": "unread",
                    "limit": 8
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
        }
    }

    result = compose_workflow(sample_plan)
    print(json.dumps(result, indent=2, ensure_ascii=False))