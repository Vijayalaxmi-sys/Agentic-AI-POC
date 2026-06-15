"""
connection_manager.py

Manages per-user app connection status for tools like Slack and Gmail.

PRODUCTION RULE:
- No global Gmail/Slack fallback.
- Every connection belongs to a logged-in authorized user_id.
- If user_id is missing, connection access is blocked.

This file does NOT:
- call n8n
- execute workflows

It reads/writes:
data/connections.json

New storage structure:

{
  "users": {
    "google_user_id_123": {
      "profile": {
        "email": "user@gmail.com",
        "name": "User Name"
      },
      "connections": {
        "slack": {...},
        "gmail": {...}
      }
    }
  }
}
"""

import json
import os
from datetime import datetime


DATA_DIR = "data"
LOG_DIR = "logs"

CONNECTIONS_FILE = os.path.join(DATA_DIR, "connections.json")
CONNECTION_LOG_FILE = os.path.join(LOG_DIR, "connection_logs.jsonl")


# -----------------------------
# Safe JSON helpers
# -----------------------------

def read_json_file(file_path):
    """
    Safely read JSON file.

    Uses utf-8-sig so files saved from PowerShell/Notepad
    with BOM do not fail json.load().
    """
    try:
        if not os.path.exists(file_path):
            return {}

        with open(file_path, "r", encoding="utf-8-sig") as file:
            data = json.load(file)

        return data if isinstance(data, dict) else {}

    except Exception:
        return {}


def write_json_file(file_path, data):
    """
    Safely write JSON file.
    """
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    with open(file_path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


# -----------------------------
# Default structures
# -----------------------------

def default_slack_connection():
    """
    Default Slack connection structure for one logged-in user.
    """
    return {
        "connected": False,
        "access_token": "",
        "token_type": "",
        "scope": "",
        "bot_user_id": "",
        "oauth_token_data": {},
        "team_id": "",
        "user_id": "",
        "workspace_name": ""
    }


def default_gmail_connection():
    """
    Default Gmail connection structure for one logged-in user.
    """
    return {
        "connected": False,
        "access_token": "",
        "refresh_token": "",
        "expires_in": "",
        "scope": "",
        "token_type": "",
        "oauth_token_data": {},
        "email": ""
    }


def default_user_connections():
    """
    Default connection object for one logged-in user.
    """
    return {
        "slack": default_slack_connection(),
        "gmail": default_gmail_connection()
    }


def default_store():
    """
    Default top-level connections store.

    IMPORTANT:
    We no longer store global:
        connections["gmail"]
        connections["slack"]

    Everything must be under:
        connections["users"][user_id]
    """
    return {
        "users": {}
    }


# -----------------------------
# User id safety
# -----------------------------

def require_user_id(user_id):
    """
    ADDED:
    Blocks connection access when no logged-in user_id is provided.

    This prevents accidental use of old global/admin Gmail or Slack.
    """
    clean_user_id = str(user_id or "").strip()

    if not clean_user_id:
        raise ValueError("user_id is required for per-user connections. No global fallback is allowed.")

    return clean_user_id


# -----------------------------
# Store helpers
# -----------------------------

def load_connection_store():
    """
    ADDED:
    Loads full per-user connection store.

    Returns:
    {
      "users": {...}
    }
    """
    store = read_json_file(CONNECTIONS_FILE)

    if not isinstance(store, dict):
        store = {}

    if "users" not in store or not isinstance(store.get("users"), dict):
        store["users"] = {}

    return store


def save_connection_store(store):
    """
    ADDED:
    Saves full per-user connection store.
    """
    if not isinstance(store, dict):
        store = default_store()

    if "users" not in store or not isinstance(store.get("users"), dict):
        store["users"] = {}

    write_json_file(CONNECTIONS_FILE, store)


def ensure_user_record(store, user_id, profile=None):
    """
    ADDED:
    Ensures the user has a record in connections.json.

    profile example:
    {
      "email": "user@gmail.com",
      "name": "User Name"
    }
    """
    user_id = require_user_id(user_id)

    if "users" not in store or not isinstance(store.get("users"), dict):
        store["users"] = {}

    users = store["users"]

    if user_id not in users or not isinstance(users.get(user_id), dict):
        users[user_id] = {
            "profile": {},
            "connections": default_user_connections()
        }

    user_record = users[user_id]

    if "profile" not in user_record or not isinstance(user_record.get("profile"), dict):
        user_record["profile"] = {}

    if "connections" not in user_record or not isinstance(user_record.get("connections"), dict):
        user_record["connections"] = default_user_connections()

    # Merge missing default tool fields without using any global fallback.
    current_connections = user_record["connections"]
    defaults = default_user_connections()

    for tool_name, default_value in defaults.items():
        if tool_name not in current_connections or not isinstance(current_connections.get(tool_name), dict):
            current_connections[tool_name] = default_value
        else:
            merged = default_value.copy()
            merged.update(current_connections[tool_name])
            current_connections[tool_name] = merged

    if isinstance(profile, dict):
        for key, value in profile.items():
            if value is not None:
                user_record["profile"][key] = value

    return user_record


# -----------------------------
# Per-user connection API
# -----------------------------

def ensure_user(user_id, profile=None):
    """
    ADDED:
    Public helper to create/update a user record.

    Used after Google Login succeeds.
    """
    store = load_connection_store()
    user_record = ensure_user_record(store, user_id, profile=profile)
    save_connection_store(store)

    write_connection_log({
        "action": "ensure_user",
        "user_id": str(user_id),
        "email": user_record.get("profile", {}).get("email", "")
    })

    return user_record


def load_user_connections(user_id):
    """
    ADDED:
    Loads Gmail/Slack connections for one logged-in user only.

    No global fallback.
    """
    user_id = require_user_id(user_id)

    store = load_connection_store()
    user_record = ensure_user_record(store, user_id)
    save_connection_store(store)

    return user_record.get("connections", default_user_connections())


def save_user_connections(user_id, connections):
    """
    ADDED:
    Saves full Gmail/Slack connection object for one logged-in user only.

    No global fallback.
    """
    user_id = require_user_id(user_id)

    if not isinstance(connections, dict):
        connections = default_user_connections()

    store = load_connection_store()
    user_record = ensure_user_record(store, user_id)

    defaults = default_user_connections()
    clean_connections = {}

    for tool_name, default_value in defaults.items():
        tool_data = connections.get(tool_name, {})

        if not isinstance(tool_data, dict):
            tool_data = {}

        merged = default_value.copy()
        merged.update(tool_data)
        clean_connections[tool_name] = merged

    user_record["connections"] = clean_connections
    save_connection_store(store)

    write_connection_log({
        "action": "save_user_connections",
        "user_id": user_id
    })


def get_user_connection(user_id, tool_name):
    """
    ADDED:
    Returns one tool connection for one logged-in user.

    Example:
    get_user_connection(user_id, "gmail")
    get_user_connection(user_id, "slack")
    """
    user_id = require_user_id(user_id)
    tool_name = str(tool_name or "").strip().lower()

    connections = load_user_connections(user_id)

    if tool_name not in connections:
        return {}

    return connections.get(tool_name, {})


def save_user_connection(user_id, tool_name, connection_data):
    """
    ADDED:
    Saves one tool connection for one logged-in user.

    Example:
    save_user_connection(user_id, "gmail", {...})
    save_user_connection(user_id, "slack", {...})
    """
    user_id = require_user_id(user_id)
    tool_name = str(tool_name or "").strip().lower()

    if tool_name not in ["gmail", "slack"]:
        raise ValueError(f"Unsupported connection tool: {tool_name}")

    if not isinstance(connection_data, dict):
        connection_data = {}

    connections = load_user_connections(user_id)

    defaults = default_user_connections()
    merged = defaults[tool_name].copy()
    merged.update(connection_data)

    connections[tool_name] = merged
    save_user_connections(user_id, connections)

    write_connection_log({
        "action": "save_user_connection",
        "user_id": user_id,
        "tool": tool_name,
        "connected": bool(merged.get("connected", False)),
        "label": get_connected_account_label(user_id, tool_name)
    })

    return merged


def is_user_connected(user_id, tool_name):
    """
    ADDED:
    Checks if one logged-in user has connected one tool.
    """
    connection = get_user_connection(user_id, tool_name)
    return connection.get("connected") is True


def get_connected_account_label(user_id, tool_name):
    """
    CHANGED:
    Human-readable account label for UI/debugging.

    Requires user_id now.
    """
    connection = get_user_connection(user_id, tool_name)

    if tool_name == "slack":
        return connection.get("workspace_name", "") or connection.get("team_id", "")

    if tool_name == "gmail":
        return connection.get("email", "")

    return ""


def set_user_connection_status(user_id, tool_name, connected):
    """
    ADDED:
    Temporary helper for testing one user's connection status.
    """
    user_id = require_user_id(user_id)
    tool_name = str(tool_name or "").strip().lower()

    connections = load_user_connections(user_id)

    if tool_name not in connections:
        connections[tool_name] = {
            "connected": False
        }

    connections[tool_name]["connected"] = bool(connected)
    save_user_connections(user_id, connections)

    write_connection_log({
        "action": "set_user_connection_status",
        "user_id": user_id,
        "tool": tool_name,
        "connected": bool(connected)
    })


# -----------------------------
# Compatibility blockers
# -----------------------------

def load_connections():
    """
    BLOCKED BY DESIGN:
    Old global connection access is intentionally disabled.

    Why:
    - Global fallback can accidentally use admin Gmail/Slack.
    - Production app must always load connections by logged-in user_id.

    Use:
        load_user_connections(user_id)
    """
    raise ValueError("Global load_connections() is disabled. Use load_user_connections(user_id).")


def save_connections(connections):
    """
    BLOCKED BY DESIGN:
    Old global connection saving is intentionally disabled.

    Use:
        save_user_connections(user_id, connections)
    """
    raise ValueError("Global save_connections() is disabled. Use save_user_connections(user_id, connections).")


def is_connected(tool_name):
    """
    BLOCKED BY DESIGN:
    Old global is_connected(tool_name) is intentionally disabled.

    Use:
        is_user_connected(user_id, tool_name)
    """
    raise ValueError("Global is_connected(tool_name) is disabled. Use is_user_connected(user_id, tool_name).")


def get_connection(tool_name):
    """
    BLOCKED BY DESIGN:
    Old global get_connection(tool_name) is intentionally disabled.

    Use:
        get_user_connection(user_id, tool_name)
    """
    raise ValueError("Global get_connection(tool_name) is disabled. Use get_user_connection(user_id, tool_name).")


def set_connection_status(tool_name, connected):
    """
    BLOCKED BY DESIGN:
    Old global set_connection_status(tool_name, connected) is intentionally disabled.

    Use:
        set_user_connection_status(user_id, tool_name, connected)
    """
    raise ValueError("Global set_connection_status(tool_name, connected) is disabled. Use set_user_connection_status(user_id, tool_name, connected).")


# -----------------------------
# Logging
# -----------------------------

def write_connection_log(record):
    """
    Writes connection manager activity to logs/connection_logs.jsonl.
    """
    try:
        os.makedirs(LOG_DIR, exist_ok=True)

        log_record = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            **record
        }

        with open(CONNECTION_LOG_FILE, "a", encoding="utf-8") as file:
            file.write(json.dumps(log_record, ensure_ascii=False) + "\n")

    except Exception:
        # Logging should never break app
        pass


# -----------------------------
# Manual test
# -----------------------------

if __name__ == "__main__":
    print("Connection store:")
    print(json.dumps(load_connection_store(), indent=2, ensure_ascii=False))

    print("\nNOTE:")
    print("Global connection functions are disabled.")
    print("Use load_user_connections(user_id) for per-user app flow.")