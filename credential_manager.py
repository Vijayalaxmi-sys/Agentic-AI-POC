"""
credential_manager.py

Manages n8n credential mappings.

PRODUCTION RULE:
- Gmail credential must be per logged-in user.
- Slack credential must be per logged-in user.
- No global Gmail/Slack fallback.
- OpenAI credential can remain app-level because the app owns the OpenAI key.

This file does NOT:
- execute workflows
- call Gmail directly
- call Slack directly

It reads/writes:
data/n8n_credentials.json

New structure:

{
  "app": {
    "openai": {
      "connected": true,
      "credential_type": "openAiApi",
      "credential_id": "...",
      "credential_name": "OpenAI account",
      "model": "..."
    }
  },
  "users": {
    "google_user_id_123": {
      "credentials": {
        "gmail": {
          "connected": true,
          "credential_type": "gmailOAuth2",
          "credential_id": "...",
          "credential_name": "Gmail account - user@gmail.com"
        },
        "slack": {
          "connected": true,
          "credential_type": "slackOAuth2Api",
          "credential_id": "...",
          "credential_name": "Slack account - workspace"
        }
      }
    }
  }
}
"""

import json
import os


CREDENTIALS_FILE = os.path.join("data", "n8n_credentials.json")


# -----------------------------
# Safe JSON helpers
# -----------------------------

def read_json_file(file_path):
    """
    ADDED:
    Safely reads JSON file.

    utf-8-sig protects against BOM from Notepad/PowerShell.
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
    ADDED:
    Safely writes JSON file.
    """
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    with open(file_path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


# -----------------------------
# Default structures
# -----------------------------

def default_tool_credential():
    """
    ADDED:
    Default n8n credential structure for Gmail/Slack.
    """
    return {
        "connected": False,
        "credential_type": "",
        "credential_id": "",
        "credential_name": ""
    }


def default_openai_credential():
    """
    ADDED:
    Default app-level OpenAI credential structure.

    OpenAI remains app-level because end users do not connect their own OpenAI.
    """
    return {
        "connected": False,
        "credential_type": "",
        "credential_id": "",
        "credential_name": "",
        "model": ""
    }


def default_user_credentials():
    """
    ADDED:
    Default per-user Gmail/Slack credential mappings.
    """
    return {
        "gmail": default_tool_credential(),
        "slack": default_tool_credential()
    }


def default_store():
    """
    ADDED:
    Default credential store.

    No global Gmail/Slack fallback.
    """
    return {
        "app": {
            "openai": default_openai_credential()
        },
        "users": {}
    }


# -----------------------------
# User id safety
# -----------------------------

def require_user_id(user_id):
    """
    ADDED:
    Blocks Gmail/Slack credential access when user_id is missing.

    This prevents accidental use of global/admin Gmail or Slack.
    """
    clean_user_id = str(user_id or "").strip()

    if not clean_user_id:
        raise ValueError("user_id is required for per-user credentials. No Gmail/Slack global fallback is allowed.")

    return clean_user_id


# -----------------------------
# Store helpers
# -----------------------------

def load_credential_store():
    """
    ADDED:
    Loads full credential store.

    Also supports migration from your old flat format for OpenAI only.

    Old format example:
    {
      "gmail": {...},
      "slack": {...},
      "openai": {...}
    }

    New format:
    {
      "app": {"openai": {...}},
      "users": {...}
    }
    """
    data = read_json_file(CREDENTIALS_FILE)

    if not isinstance(data, dict):
        data = {}

    store = default_store()

    # ADDED:
    # If file is already in new format, load it.
    if isinstance(data.get("app"), dict) or isinstance(data.get("users"), dict):
        store.update(data)

        if "app" not in store or not isinstance(store.get("app"), dict):
            store["app"] = {}

        if "users" not in store or not isinstance(store.get("users"), dict):
            store["users"] = {}

        # Merge OpenAI defaults.
        openai_current = store["app"].get("openai", {})
        if not isinstance(openai_current, dict):
            openai_current = {}

        merged_openai = default_openai_credential()
        merged_openai.update(openai_current)
        store["app"]["openai"] = merged_openai

        return store

    # ADDED:
    # One-time safe migration for old OpenAI credential only.
    # We intentionally do NOT migrate old global Gmail/Slack as user credentials.
    # Reason: that could accidentally assign admin Gmail/Slack to real users.
    old_openai = data.get("openai", {})

    if isinstance(old_openai, dict):
        merged_openai = default_openai_credential()
        merged_openai.update(old_openai)
        store["app"]["openai"] = merged_openai

    return store


def save_credential_store(store):
    """
    ADDED:
    Saves full credential store.
    """
    if not isinstance(store, dict):
        store = default_store()

    if "app" not in store or not isinstance(store.get("app"), dict):
        store["app"] = {}

    if "users" not in store or not isinstance(store.get("users"), dict):
        store["users"] = {}

    if "openai" not in store["app"] or not isinstance(store["app"].get("openai"), dict):
        store["app"]["openai"] = default_openai_credential()

    write_json_file(CREDENTIALS_FILE, store)


def ensure_user_credential_record(store, user_id):
    """
    ADDED:
    Ensures a user credential record exists.
    """
    user_id = require_user_id(user_id)

    if "users" not in store or not isinstance(store.get("users"), dict):
        store["users"] = {}

    users = store["users"]

    if user_id not in users or not isinstance(users.get(user_id), dict):
        users[user_id] = {
            "credentials": default_user_credentials()
        }

    user_record = users[user_id]

    if "credentials" not in user_record or not isinstance(user_record.get("credentials"), dict):
        user_record["credentials"] = default_user_credentials()

    current_credentials = user_record["credentials"]
    defaults = default_user_credentials()

    for tool_name, default_value in defaults.items():
        if tool_name not in current_credentials or not isinstance(current_credentials.get(tool_name), dict):
            current_credentials[tool_name] = default_value
        else:
            merged = default_value.copy()
            merged.update(current_credentials[tool_name])
            current_credentials[tool_name] = merged

    return user_record


# -----------------------------
# App-level OpenAI credential API
# -----------------------------

def get_app_openai_credential():
    """
    ADDED:
    Returns app-level OpenAI n8n credential mapping.

    This is allowed because OpenAI is owned by your app, not by individual users.
    """
    store = load_credential_store()
    return store.get("app", {}).get("openai", default_openai_credential())


def update_app_openai_credential(credential_type, credential_id, credential_name, extra_fields=None):
    """
    ADDED:
    Updates app-level OpenAI credential mapping.
    """
    store = load_credential_store()

    if "app" not in store or not isinstance(store.get("app"), dict):
        store["app"] = {}

    openai_credential = default_openai_credential()
    openai_credential.update({
        "connected": True,
        "credential_type": str(credential_type or "").strip(),
        "credential_id": str(credential_id or "").strip(),
        "credential_name": str(credential_name or "").strip()
    })

    if isinstance(extra_fields, dict):
        openai_credential.update(extra_fields)

    store["app"]["openai"] = openai_credential
    save_credential_store(store)

    return openai_credential


def is_app_openai_connected():
    """
    ADDED:
    Checks if app-level OpenAI credential is connected.
    """
    credential = get_app_openai_credential()
    return bool(credential.get("connected", False))


# -----------------------------
# Per-user Gmail/Slack credential API
# -----------------------------

def load_user_credentials(user_id):
    """
    ADDED:
    Loads Gmail/Slack credentials for one logged-in user only.

    No global fallback.
    """
    user_id = require_user_id(user_id)

    store = load_credential_store()
    user_record = ensure_user_credential_record(store, user_id)
    save_credential_store(store)

    return user_record.get("credentials", default_user_credentials())


def save_user_credentials(user_id, credentials):
    """
    ADDED:
    Saves full Gmail/Slack credentials for one logged-in user.

    No global fallback.
    """
    user_id = require_user_id(user_id)

    if not isinstance(credentials, dict):
        credentials = default_user_credentials()

    store = load_credential_store()
    user_record = ensure_user_credential_record(store, user_id)

    defaults = default_user_credentials()
    clean_credentials = {}

    for tool_name, default_value in defaults.items():
        tool_data = credentials.get(tool_name, {})

        if not isinstance(tool_data, dict):
            tool_data = {}

        merged = default_value.copy()
        merged.update(tool_data)
        clean_credentials[tool_name] = merged

    user_record["credentials"] = clean_credentials
    save_credential_store(store)

    return clean_credentials


def get_user_credential(user_id, tool_name):
    """
    ADDED:
    Gets one user's Gmail/Slack credential mapping.

    Example:
    get_user_credential(user_id, "gmail")
    get_user_credential(user_id, "slack")
    """
    user_id = require_user_id(user_id)
    tool_name = str(tool_name or "").strip().lower()

    if tool_name not in ["gmail", "slack"]:
        raise ValueError(f"Unsupported per-user credential tool: {tool_name}")

    credentials = load_user_credentials(user_id)
    return credentials.get(tool_name, default_tool_credential())


def update_user_credential(user_id, tool_name, credential_type, credential_id, credential_name, extra_fields=None):
    """
    ADDED:
    Updates one user's Gmail/Slack n8n credential mapping.

    Example:
    update_user_credential(
        user_id="google_user_id_123",
        tool_name="gmail",
        credential_type="gmailOAuth2",
        credential_id="new_n8n_credential_id",
        credential_name="Gmail account - user@gmail.com"
    )
    """
    user_id = require_user_id(user_id)
    tool_name = str(tool_name or "").strip().lower()

    if tool_name not in ["gmail", "slack"]:
        raise ValueError(f"Unsupported per-user credential tool: {tool_name}")

    credentials = load_user_credentials(user_id)

    credentials[tool_name].update({
        "connected": True,
        "credential_type": str(credential_type or "").strip(),
        "credential_id": str(credential_id or "").strip(),
        "credential_name": str(credential_name or "").strip()
    })

    if isinstance(extra_fields, dict):
        credentials[tool_name].update(extra_fields)

    save_user_credentials(user_id, credentials)

    return credentials[tool_name]


def disconnect_user_credential(user_id, tool_name):
    """
    ADDED:
    Marks one user's Gmail/Slack credential as disconnected.
    """
    user_id = require_user_id(user_id)
    tool_name = str(tool_name or "").strip().lower()

    if tool_name not in ["gmail", "slack"]:
        raise ValueError(f"Unsupported per-user credential tool: {tool_name}")

    credentials = load_user_credentials(user_id)

    credentials[tool_name].update({
        "connected": False,
        "credential_type": "",
        "credential_id": "",
        "credential_name": ""
    })

    save_user_credentials(user_id, credentials)

    return credentials[tool_name]


def is_user_credential_connected(user_id, tool_name):
    """
    ADDED:
    Checks if one user's Gmail/Slack n8n credential is connected.
    """
    credential = get_user_credential(user_id, tool_name)
    return bool(credential.get("connected", False))


def get_required_missing_user_credentials(user_id, required_tools):
    """
    ADDED:
    Returns missing credentials for one logged-in user.

    Rules:
    - chat does not need n8n credential.
    - gmail/slack need per-user credentials.
    - openai uses app-level credential.
    """
    user_id = require_user_id(user_id)
    missing = []

    for tool_name in required_tools:
        tool_name = str(tool_name or "").strip().lower()

        if tool_name == "chat":
            continue

        if tool_name == "openai" or tool_name == "llm":
            if not is_app_openai_connected():
                missing.append("openai")
            continue

        if tool_name in ["gmail", "slack"]:
            if not is_user_credential_connected(user_id, tool_name):
                missing.append(tool_name)
            continue

    return missing


# -----------------------------
# Compatibility blockers
# -----------------------------

def load_credentials():
    """
    CHANGED SAFELY:
    Returns app-level OpenAI only.

    Old global Gmail/Slack credential access is intentionally not returned.

    Why:
    - Returning old global Gmail/Slack could accidentally use admin credentials.
    - Production flow must use load_user_credentials(user_id) for Gmail/Slack.

    This keeps /connections and simple app-level checks from crashing,
    while preventing global Gmail/Slack fallback.
    """
    store = load_credential_store()

    return {
        "gmail": default_tool_credential(),
        "slack": default_tool_credential(),
        "openai": store.get("app", {}).get("openai", default_openai_credential())
    }


def save_credentials(credentials):
    """
    BLOCKED BY DESIGN:
    Old global credential saving is disabled.

    Use:
    - update_user_credential(user_id, "gmail", ...)
    - update_user_credential(user_id, "slack", ...)
    - update_app_openai_credential(...)
    """
    raise ValueError("Global save_credentials() is disabled. Use per-user credential helpers.")


def update_credential(tool_name, credential_type, credential_id, credential_name, extra_fields=None):
    """
    CHANGED SAFELY:
    Old global update_credential is allowed only for OpenAI.

    Gmail/Slack must use:
        update_user_credential(user_id, tool_name, ...)
    """
    tool_name = str(tool_name or "").strip().lower()

    if tool_name == "openai":
        return update_app_openai_credential(
            credential_type=credential_type,
            credential_id=credential_id,
            credential_name=credential_name,
            extra_fields=extra_fields
        )

    raise ValueError(
        f"Global update_credential('{tool_name}') is disabled. "
        "Use update_user_credential(user_id, tool_name, ...)."
    )


def disconnect_credential(tool_name):
    """
    CHANGED SAFELY:
    Old global disconnect is blocked for Gmail/Slack.

    OpenAI app-level disconnect can be handled later if needed.
    """
    raise ValueError("Global disconnect_credential() is disabled. Use per-user disconnect helpers.")


def is_credential_connected(tool_name):
    """
    CHANGED SAFELY:
    Supports only app-level OpenAI check globally.
    Gmail/Slack require user_id.
    """
    tool_name = str(tool_name or "").strip().lower()

    if tool_name == "openai" or tool_name == "llm":
        return is_app_openai_connected()

    raise ValueError(
        f"Global is_credential_connected('{tool_name}') is disabled. "
        "Use is_user_credential_connected(user_id, tool_name)."
    )


def get_credential(tool_name):
    """
    CHANGED SAFELY:
    Supports only app-level OpenAI globally.
    Gmail/Slack require user_id.
    """
    tool_name = str(tool_name or "").strip().lower()

    if tool_name == "openai" or tool_name == "llm":
        return get_app_openai_credential()

    raise ValueError(
        f"Global get_credential('{tool_name}') is disabled. "
        "Use get_user_credential(user_id, tool_name)."
    )


def get_required_missing_credentials(required_tools):
    """
    BLOCKED BY DESIGN:
    Old global missing-credential check is disabled.

    Use:
        get_required_missing_user_credentials(user_id, required_tools)
    """
    raise ValueError(
        "Global get_required_missing_credentials(required_tools) is disabled. "
        "Use get_required_missing_user_credentials(user_id, required_tools)."
    )


if __name__ == "__main__":
    print(json.dumps(load_credential_store(), indent=2, ensure_ascii=False))
    print("OpenAI app credential connected:", is_app_openai_connected())
    print("")
    print("NOTE:")
    print("Global Gmail/Slack credentials are disabled.")
    print("Use load_user_credentials(user_id) for per-user app flow.")