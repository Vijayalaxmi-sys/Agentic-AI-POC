"""
main.py

Safe pipeline controller.

PRODUCTION RULE:
- Every workflow request must come from a logged-in authorized user.
- user_id is required for resolver.py and builder.py.
- Gmail/Slack connection and credential checks are per user.
- OpenAI credential remains app-level.
- No global Gmail/Slack fallback.

Flow:
1. User enters request
2. planner.py creates plan
3. resolver.py checks tools/connections/credentials for this user
4. composer.py creates workflow_steps only if resolver says OK
5. builder_preview.py creates safe n8n node mapping preview
6. builder.py generates n8n workflow JSON using this user's credentials
7. n8n_client.py creates workflow in n8n

No workflow execution here.
No workflow activation here.
No Slack/Gmail API calls directly here.
"""

import json

from planner import plan_user_request
from resolver import resolve_plan
from composer import compose_workflow
from builder_preview import build_preview
from builder import build_n8n_workflow

# Creates workflow in n8n only after resolver/composer/builder succeed.
# It does not execute or activate workflow.
from n8n_client import create_workflow


def safe_text(value):
    """
    ADDED:
    Converts any value into clean string.
    """
    if value is None:
        return ""
    return str(value).strip()


def build_missing_credential_message(missing_credentials):
    """
    Creates clean user-facing message for missing credentials.
    """
    if not missing_credentials:
        return "Please connect required credentials from the app first."

    pretty_names = [tool.capitalize() for tool in missing_credentials]

    if len(pretty_names) == 1:
        return f"Please connect {pretty_names[0]} credential from the app first."

    return f"Please connect {', '.join(pretty_names)} credentials from the app first."


def build_missing_connection_message(missing_connections):
    """
    ADDED:
    Creates clean user-facing message for missing user tool connections.
    """
    if not missing_connections:
        return "Please connect required tools from the app first."

    pretty_names = [tool.capitalize() for tool in missing_connections]

    if len(pretty_names) == 1:
        return f"Please connect {pretty_names[0]} from the app first."

    return f"Please connect {', '.join(pretty_names)} from the app first."


def run_pipeline(user_message, user_id):
    """
    CHANGED:
    Main safe pipeline now requires user_id.

    Why:
    - resolver.py must check Gmail/Slack connections for this logged-in user.
    - builder.py must attach Gmail/Slack n8n credentials for this logged-in user.
    - No global Gmail/Slack fallback is allowed.

    ADDED:
    Safe workflow_name handling before sending to builder.py.
    n8n requires workflow names to be 1 to 128 characters.
    builder.py also has safety, but this keeps main.py output cleaner.
    """

    def safe_workflow_name(name, max_length=120):
        """
        ADDED:
        Keeps workflow name inside n8n limit.
        """
        cleaned = safe_text(name)

        if not cleaned:
            return "Generated Dynamic Workflow"

        cleaned = " ".join(cleaned.split())

        if not cleaned:
            return "Generated Dynamic Workflow"

        if len(cleaned) <= max_length:
            return cleaned

        return cleaned[:max_length].rstrip() + "..."

    user_id = safe_text(user_id)

    if not user_id:
        return {
            "stage": "authorization",
            "status": "login_required",
            "message": "Please log in before creating workflows.",
            "can_continue": False,
        }

    plan = plan_user_request(user_message)

    # Direct answer: return immediately.
    # Direct answers do not need Gmail/Slack resolving.
    if plan.get("decision") == "direct_answer":
        return {
            "stage": "planner",
            "status": "direct_answer",
            "message": plan.get("direct_answer", ""),
            "planner_output": plan,
        }

    # Follow-up: return question immediately.
    # Follow-up does not create workflow yet.
    if plan.get("decision") == "follow_up":
        return {
            "stage": "planner",
            "status": "follow_up",
            "message": plan.get("follow_up_question", ""),
            "missing_fields": plan.get("missing_fields", []),
            "planner_output": plan,
        }

    # Cannot execute.
    if plan.get("decision") == "cannot_execute":
        return {
            "stage": "planner",
            "status": "cannot_execute",
            "message": plan.get("summary", "Cannot execute this request."),
            "planner_output": plan,
        }

    # CHANGED:
    # Workflow required: check resolver using current logged-in user_id.
    resolver_result = resolve_plan(plan, user_id=user_id)

    if resolver_result.get("status") == "authorization_required":
        return {
            "stage": "resolver",
            "status": "authorization_required",
            "message": resolver_result.get("message", "Please log in before creating workflows."),
            "errors": resolver_result.get("errors", []),
            "planner_output": plan,
            "resolver_output": resolver_result,
        }

    if resolver_result.get("status") == "connection_required":
        missing_connections = resolver_result.get("missing_connections", [])

        return {
            "stage": "resolver",
            "status": "connection_required",
            "message": resolver_result.get("message") or build_missing_connection_message(missing_connections),
            "missing_connections": missing_connections,
            "connect_actions": resolver_result.get("connect_actions", []),
            "planner_output": plan,
            "resolver_output": resolver_result,
        }

    # Cleanly stop when n8n credentials are missing.
    # Gmail/Slack are user-level credentials. OpenAI is app-level credential.
    if resolver_result.get("status") == "credential_required":
        missing_credentials = resolver_result.get("missing_credentials", [])

        return {
            "stage": "resolver",
            "status": "credential_required",
            "message": build_missing_credential_message(missing_credentials),
            "missing_credentials": missing_credentials,
            "credential_actions": resolver_result.get("credential_actions", []),
            "planner_output": plan,
            "resolver_output": resolver_result,
        }

    if resolver_result.get("status") == "unsupported":
        return {
            "stage": "resolver",
            "status": "unsupported",
            "message": resolver_result.get("message", ""),
            "errors": resolver_result.get("errors", []),
            "planner_output": plan,
            "resolver_output": resolver_result,
        }

    if resolver_result.get("status") != "ok":
        return {
            "stage": "resolver",
            "status": "unknown",
            "message": "Unexpected resolver result.",
            "planner_output": plan,
            "resolver_output": resolver_result,
        }

    # Composer runs only after resolver says OK.
    composer_result = compose_workflow(plan)

    if composer_result.get("status") != "ok":
        return {
            "stage": "composer",
            "status": composer_result.get("status", "composer_failed"),
            "message": composer_result.get("message", "Composer failed."),
            "errors": composer_result.get("errors", []),
            "planner_output": plan,
            "resolver_output": resolver_result,
            "composer_output": composer_result,
        }

    workflow_steps = composer_result.get("workflow_steps", [])

    # Builder preview runs only after composer succeeds.
    # This preview still does not attach real credentials.
    builder_preview_result = build_preview(workflow_steps)

    if builder_preview_result.get("status") != "ok":
        return {
            "stage": "builder_preview",
            "status": builder_preview_result.get("status", "builder_preview_failed"),
            "message": builder_preview_result.get("message", "Builder preview failed."),
            "errors": builder_preview_result.get("errors", []),
            "planner_output": plan,
            "resolver_output": resolver_result,
            "composer_output": composer_result,
            "builder_preview_output": builder_preview_result,
            "workflow_steps": workflow_steps,
        }

    # ADDED:
    # Keep workflow name short before sending it to builder.
    # n8n rejects workflow names longer than 128 characters.
    workflow_name = safe_workflow_name(
        plan.get("summary", "Generated Dynamic Workflow")
    )

    # CHANGED:
    # Builder generates n8n workflow JSON using current logged-in user_id.
    # builder.py will attach Gmail/Slack credentials for this user only.
    builder_result = build_n8n_workflow(
        workflow_steps,
        workflow_name=workflow_name,
        user_id=user_id,
    )

    if builder_result.get("status") != "ok":
        return {
            "stage": "builder",
            "status": builder_result.get("status", "builder_failed"),
            "message": builder_result.get("message", "Builder failed."),
            "errors": builder_result.get("errors", []),
            "planner_output": plan,
            "resolver_output": resolver_result,
            "composer_output": composer_result,
            "builder_preview_output": builder_preview_result,
            "builder_output": builder_result,
            "workflow_steps": workflow_steps,
            "nodes_preview": builder_preview_result.get("nodes_preview", []),
        }

    workflow_json = builder_result.get("workflow_json", {})
    nodes = workflow_json.get("nodes", [])
    connections = workflow_json.get("connections", {})

    # Create workflow in n8n automatically.
    # This still does NOT activate or execute it.
    create_result = create_workflow(workflow_json)

    if create_result.get("status") != "ok":
        return {
            "stage": "n8n_create",
            "status": create_result.get("status", "n8n_create_failed"),
            "message": create_result.get("message", "Failed to create workflow in n8n."),
            "created_workflow": create_result.get("created_workflow"),
            "create_output": create_result,
            "planner_output": plan,
            "resolver_output": resolver_result,
            "composer_output": composer_result,
            "builder_preview_output": builder_preview_result,
            "builder_output": builder_result,
            "workflow_steps": workflow_steps,
            "nodes_preview": builder_preview_result.get("nodes_preview", []),
            "workflow_json": workflow_json,
        }

    return {
        "stage": "n8n_create",
        "status": "workflow_created",
        "message": "Workflow created successfully in n8n. It is inactive and not executed.",
        "summary": {
            "workflow_name": workflow_json.get("name", ""),
            "total_nodes": len(nodes),
            "total_connections": len(connections),
            "node_names": [node.get("name", "") for node in nodes],
        },
        "created_workflow": create_result.get("created_workflow"),
        "planner_output": plan,
        "resolver_output": resolver_result,
        "composer_output": composer_result,
        "builder_preview_output": builder_preview_result,
        "builder_output": builder_result,
        "create_output": create_result,
        "workflow_steps": workflow_steps,
        "nodes_preview": builder_preview_result.get("nodes_preview", []),
        "workflow_json": workflow_json,
    }


if __name__ == "__main__":
    user_input = input("USER: ").strip()
    user_id = input("USER ID: ").strip()

    if not user_input:
        print("No input provided.")
    elif not user_id:
        print("No user_id provided. Per-user pipeline requires logged-in user_id.")
    else:
        result = run_pipeline(user_input, user_id=user_id)
        print(json.dumps(result, indent=2, ensure_ascii=False))
