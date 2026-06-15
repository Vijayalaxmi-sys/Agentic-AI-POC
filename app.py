"""
app.py

FastAPI backend for Dynamic AI Workflow app.

Version 2.1:
- Google Login + allowed user check
- Cookie/session-based current user
- Per-user Gmail/Slack app connections
- Per-user Gmail/Slack n8n credential mappings
- App-level OpenAI credential
- Existing Option B workflow execution preserved:
  create workflow -> activate workflow -> call production webhook -> return response to UI

PRODUCTION RULE:
- No global Gmail/Slack fallback.
- Every Gmail/Slack connection belongs to the logged-in authorized user.
- Every Gmail/Slack n8n credential belongs to the logged-in authorized user.
"""

import json
import os
import re
import secrets
from urllib.parse import urlencode
from typing import Optional, List, Dict, Any

import requests
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel

from main import run_pipeline

# CHANGED:
# Per-user connection helpers. No global Gmail/Slack fallback.
from connection_manager import (
    ensure_user,
    load_user_connections,
    save_user_connection,
    get_user_connection,
)

# CHANGED:
# Per-user Gmail/Slack n8n credentials. OpenAI remains app-level.
from credential_manager import (
    load_credentials,
    load_user_credentials,
    update_user_credential,
)

# Existing Option B n8n helpers.
from n8n_client import (
    activate_workflow,
    call_production_webhook,
    find_webhook_path_from_workflow_json,
    sync_gmail_oauth_credential_to_n8n,
)

# ADDED SAFELY:
# Slack n8n credential sync uses the generic n8n credential helper if your
# current n8n_client.py has it. If not, Slack connection still saves, but
# credential sync returns a clear error instead of breaking app startup.
try:
    from n8n_client import create_or_update_credential
except Exception:
    create_or_update_credential = None

from config import (
    SLACK_CLIENT_ID,
    SLACK_CLIENT_SECRET,
    SLACK_REDIRECT_URI,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    GOOGLE_REDIRECT_URI,
)


app = FastAPI(
    title="Dynamic AI Workflow API",
    version="2.1.0"
)


# -----------------------------
# Login/session configuration
# -----------------------------
# This cookie session lets the backend remember which Google user logged in.
# For production, set SESSION_SECRET in environment variables.
SESSION_SECRET = os.getenv(
    "SESSION_SECRET",
    "dev-change-this-session-secret-before-production"
)

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=False
)


# -----------------------------
# Frontend static files
# -----------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
DATA_DIR = os.path.join(BASE_DIR, "data")
ALLOWED_USERS_FILE = os.path.join(DATA_DIR, "allowed_users.json")

app.mount("/frontend", StaticFiles(directory=FRONTEND_DIR), name="frontend")


class ChatRequest(BaseModel):
    """
    Request body for /chat endpoint.

    history is optional. Existing UI can keep sending only message.
    """
    message: str
    history: Optional[List[Dict[str, Any]]] = None


# -----------------------------
# Google login + allowed-user helpers
# -----------------------------

def safe_text(value):
    """
    Converts None/any value into stripped string.
    """
    if value is None:
        return ""
    return str(value).strip()


def load_allowed_access():
    """
    Reads data/allowed_users.json.

    Example:
    {
      "allowed_users": ["user1@gmail.com"],
      "allowed_domains": ["company.com"]
    }
    """
    try:
        if not os.path.exists(ALLOWED_USERS_FILE):
            return {
                "allowed_users": [],
                "allowed_domains": []
            }

        with open(ALLOWED_USERS_FILE, "r", encoding="utf-8-sig") as file:
            data = json.load(file)

        allowed_users = data.get("allowed_users", [])
        allowed_domains = data.get("allowed_domains", [])

        if not isinstance(allowed_users, list):
            allowed_users = []

        if not isinstance(allowed_domains, list):
            allowed_domains = []

        return {
            "allowed_users": [str(email).strip().lower() for email in allowed_users if str(email).strip()],
            "allowed_domains": [str(domain).strip().lower() for domain in allowed_domains if str(domain).strip()]
        }

    except Exception as error:
        # Safety: if the access file is broken, do not allow everyone accidentally.
        return {
            "allowed_users": [],
            "allowed_domains": [],
            "error": str(error)
        }


def is_email_authorized(email):
    """
    Checks whether the logged-in Google email is allowed to use the app.
    """
    email = str(email or "").strip().lower()

    if not email or "@" not in email:
        return False

    access = load_allowed_access()
    allowed_users = access.get("allowed_users", [])
    allowed_domains = access.get("allowed_domains", [])

    if email in allowed_users:
        return True

    domain = email.split("@", 1)[1]

    if domain in allowed_domains:
        return True

    return False


def get_current_user(request: Request):
    """
    Returns the current logged-in and authorized user from browser session.
    """
    user = request.session.get("user")

    if not isinstance(user, dict):
        return None

    if not user.get("authorized"):
        return None

    if not user.get("email"):
        return None

    if not user.get("user_id"):
        return None

    return user


def require_authorized_user(request: Request):
    """
    Protects workflow, Gmail connect, Slack connect, and connection status routes.
    """
    user = get_current_user(request)

    if user:
        return {
            "ok": True,
            "user": user
        }

    return {
        "ok": False,
        "stage": "auth",
        "status": "login_required",
        "message": "Please login with an authorized Google account before using the app.",
        "login_url": "/login/google"
    }


# -----------------------------
# Gmail recipient safety helpers
# -----------------------------

def get_connected_gmail_email(user_id):
    """
    Returns the currently connected Gmail TOOL account for this app user.

    Important:
    - This is not the app-login email.
    - The app-login email only identifies who is using the app.
    - The Gmail tool account is the external Gmail account selected in /connect/gmail.

    Current storage supports one active Gmail tool account per app user.
    """
    gmail_connection = get_user_connection(user_id, "gmail")

    if not gmail_connection.get("connected"):
        return ""

    return safe_text(gmail_connection.get("email"))


def get_connected_gmail_accounts(user_id):
    """
    Returns Gmail TOOL accounts connected under the logged-in app user.

    This is written to support both:
    1. Your current storage shape:
        {"gmail": {"connected": true, "email": "abc@gmail.com", ...}}

    2. A future multi-account storage shape:
        {"gmail": {"accounts": [{"connected": true, "email": "abc@gmail.com"}]}}

    This does NOT return the app-login email unless that same email was also
    connected through the Gmail OAuth tool flow.
    """
    connected_accounts = []

    try:
        user_connections = load_user_connections(user_id)
    except Exception:
        user_connections = {}

    gmail_connection = {}

    if isinstance(user_connections, dict):
        gmail_connection = user_connections.get("gmail", {}) or {}

    # Current single Gmail tool account storage.
    if isinstance(gmail_connection, dict):
        if gmail_connection.get("connected") and gmail_connection.get("email"):
            email = safe_text(gmail_connection.get("email")).lower()
            if email and email not in connected_accounts:
                connected_accounts.append(email)

        # Future multi-Gmail storage support.
        accounts = gmail_connection.get("accounts")

        if isinstance(accounts, list):
            for account in accounts:
                if not isinstance(account, dict):
                    continue

                if account.get("connected") and account.get("email"):
                    email = safe_text(account.get("email")).lower()

                    if email and email not in connected_accounts:
                        connected_accounts.append(email)

    return connected_accounts


def build_gmail_connect_url(requested_email=""):
    """
    Builds Gmail OAuth connect URL.

    requested_email is optional. It helps the backend remember which Gmail
    tool account the user wanted to connect. Google still lets the user pick
    the account during OAuth because prompt=select_account is used.
    """
    requested_email = safe_text(requested_email).lower()

    if requested_email:
        return "/connect/gmail?" + urlencode({"requested_email": requested_email})

    return "/connect/gmail"


def message_needs_my_gmail_resolution(user_message):
    """
    Detects ambiguous Gmail destination wording for SEND requests.

    Standard rule:
    - App login email is never used as workflow Gmail automatically.
    - If the user says "my Gmail", "my email", or "email me" in a send-style
      request, the backend should ask for the recipient email and store a
      pending action.
    - This helper intentionally avoids hardcoding every preposition such as
      "to/on/in/from". It looks for meaning:
        send-style action + ambiguous personal email destination.
    - Gmail READ/source requests are excluded.
    """
    message = str(user_message or "").lower().strip()

    if not message:
        return False

    # READ/source wording should not be treated as "send to my Gmail".
    source_patterns = [
        r"\bread\b.*\bgmail\b",
        r"\bsearch\b.*\bgmail\b",
        r"\bsummarize\b.*\bgmail\b",
        r"\bunread\b.*\bgmail\b",
        r"\binbox\b",
        r"\bemails?\b.*\bfrom\b",
        r"\bfrom\s+my\s+gmail\b",
    ]

    if any(re.search(pattern, message) for pattern in source_patterns):
        return False

    # Ambiguous personal destination wording.
    has_ambiguous_gmail_destination = bool(
        re.search(r"\bmy\s+(gmail|gamil|email)\b", message)
        or re.search(r"\bemail\s+me\b", message)
    )

    if not has_ambiguous_gmail_destination:
        return False

    # Send-style language. Includes natural forms like:
    # "send me hi on my gmail", "say hi on my gmail",
    # "share it to my email", "mail me the report".
    send_intent = bool(
        re.search(r"\b(send|email|mail|share|forward)\b", message)
        or re.search(r"^\s*(say|write|draft|create|generate)\b", message)
    )

    return send_intent


def prepare_message_for_gmail_recipient(user_message, user_id):
    """
    Handles ambiguous Gmail recipient wording.

    Standard rule:
    - App login is only authentication.
    - If user types an exact email address, continue.
    - If user says "my gmail" / "my email" / "email me", ask for the email
      address and store pending action in /chat.
    - Do not silently use the app-login email.
    - Do not silently use the last connected Gmail account.
    """
    original_message = str(user_message or "").strip()

    explicit_emails = re.findall(
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        original_message
    )

    if explicit_emails:
        return {
            "can_continue": True,
            "message_for_pipeline": original_message,
            "connected_gmail_email": "",
            "needs_gmail_selection": False
        }

    if not message_needs_my_gmail_resolution(original_message):
        return {
            "can_continue": True,
            "message_for_pipeline": original_message,
            "connected_gmail_email": "",
            "needs_gmail_selection": False
        }

    return {
        "can_continue": False,
        "message_for_pipeline": original_message,
        "connected_gmail_email": "",
        "needs_gmail_selection": True,
        "message": "Which email address should I send it to?",
        "pending_action": {
            "type": "gmail_recipient_resolution",
            "original_message": original_message
        },
        "connect_actions": []
    }
def extract_email_addresses_from_text(text):
    """
    Extract email addresses from user text.
    Data extraction only.
    """
    if not text:
        return []

    emails = re.findall(
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        str(text)
    )

    cleaned = []

    for email in emails:
        normalized = safe_text(email).lower()

        if normalized and normalized not in cleaned:
            cleaned.append(normalized)

    return cleaned


def planner_has_gmail_read_source(planner_output):
    """
    Returns True only when planner decided Gmail is a READ source.
    No raw keyword intent guessing.
    """
    if not isinstance(planner_output, dict):
        return False

    sources = planner_output.get("sources", [])

    if not isinstance(sources, list):
        return False

    for source in sources:
        if not isinstance(source, dict):
            continue

        tool = safe_text(source.get("tool")).lower()
        operation = safe_text(source.get("operation")).lower()

        if tool == "gmail" and operation in {"read", "search", "get", "getall", "get_all"}:
            return True

    return False


def is_clear_sender_search_request(user_message):
    """
    Allows explicit sender-search wording only.

    Example sender-filter requests:
        emails sent by vijji.m18@gmail.com
        emails where sender is vijji.m18@gmail.com
        emails from sender vijji.m18@gmail.com

    Important for your app design:
    - "summarize unread emails from vijji.m18@gmail.com" means the Gmail
      SOURCE/tool account is vijji.m18@gmail.com.
    - It does NOT mean the logged-in app user email.
    - It should run only after vijji.m18@gmail.com is connected as a Gmail tool.
    """
    message = str(user_message or "").lower()

    sender_patterns = [
        r"\bsent\s+by\s+[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        r"\bsender\s+is\s+[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        r"\bfrom\s+sender\s+[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        r"\bwhere\s+sender\s+is\s+[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
    ]

    return any(re.search(pattern, message, flags=re.IGNORECASE) for pattern in sender_patterns)


def get_requested_gmail_read_accounts(user_message, planner_output):
    """
    Finds explicit Gmail account/mailbox references in Gmail READ requests.

    Important rule:
    - If user says 'from email@example.com' in Gmail read request, treat it as
      mailbox/account request.
    - If user clearly says 'sent by' or 'sender is', do NOT treat it as mailbox.
    """
    if not planner_has_gmail_read_source(planner_output):
        return []

    if is_clear_sender_search_request(user_message):
        return []

    requested_emails = extract_email_addresses_from_text(user_message)

    if not requested_emails:
        return []

    destination = planner_output.get("destination") or {}

    # Do not block valid Gmail SEND recipient cases.
    if isinstance(destination, dict):
        destination_tool = safe_text(destination.get("tool")).lower()
        destination_operation = safe_text(destination.get("operation")).lower()
        recipient = safe_text(destination.get("recipient")).lower()

        if destination_tool == "gmail" and destination_operation in {"send", "create"} and recipient:
            requested_emails = [
                email for email in requested_emails
                if email != recipient
            ]

    return requested_emails


def validate_gmail_read_account_matches_connection(user_message, user_id, planner_output):
    """
    Validates Gmail READ source account without confusing it with app login.

    App login = who is allowed to use this app.
    Gmail tool connection = which Gmail mailbox the workflow can read/send.

    Example:
        App login can be: vijayalaxmi.boddu1994@gmail.com
        User request can be: summarize unread emails from vijji.m18@gmail.com

    Correct behavior:
        - Do NOT use app-login email as the Gmail source automatically.
        - Check whether vijji.m18@gmail.com is connected as a Gmail TOOL account.
        - If not connected, ask user to connect that Gmail tool account.
    """
    requested_accounts = get_requested_gmail_read_accounts(
        user_message=user_message,
        planner_output=planner_output
    )

    if not requested_accounts:
        return {
            "can_continue": True
        }

    connected_gmail_accounts = get_connected_gmail_accounts(user_id)
    connected_gmail_email = safe_text(get_connected_gmail_email(user_id)).lower()

    if not connected_gmail_accounts:
        requested_email = requested_accounts[0]

        return {
            "can_continue": False,
            "stage": "tool_account_validation",
            "status": "gmail_connection_required",
            "message": (
                f"To read Gmail emails from {requested_email}, please connect that Gmail account as a tool first. "
                "Your app login is only for authentication and will not be used as the Gmail source automatically."
            ),
            "missing_connections": ["gmail"],
            "requested_tool": "gmail",
            "requested_gmail_email": requested_email,
            "connected_gmail_email": "",
            "connected_gmail_accounts": [],
            "connect_actions": [
                {
                    "tool": "gmail",
                    "label": f"Connect Gmail: {requested_email}",
                    "url": build_gmail_connect_url(requested_email)
                }
            ]
        }

    for requested_email in requested_accounts:
        requested_email = safe_text(requested_email).lower()

        if requested_email and requested_email not in connected_gmail_accounts:
            return {
                "can_continue": False,
                "stage": "tool_account_validation",
                "status": "gmail_account_not_connected",
                "message": (
                    f"This request needs Gmail tool account {requested_email}. "
                    f"Currently connected Gmail tool account: {connected_gmail_email or 'none'}. "
                    f"Please connect {requested_email} as the Gmail tool account, then run the request again."
                ),
                "requested_tool": "gmail",
                "requested_gmail_email": requested_email,
                "connected_gmail_email": connected_gmail_email,
                "connected_gmail_accounts": connected_gmail_accounts,
                "connect_actions": [
                    {
                        "tool": "gmail",
                        "label": f"Connect Gmail: {requested_email}",
                        "url": build_gmail_connect_url(requested_email)
                    }
                ]
            }

    return {
        "can_continue": True
    }

def pipeline_has_unresolved_connected_user(pipeline_result):
    """
    Final safety check before activation/webhook execution.
    """
    if not isinstance(pipeline_result, dict):
        return False

    builder_output = pipeline_result.get("builder_output") or {}
    workflow_json = builder_output.get("workflow_json") or pipeline_result.get("workflow_json") or {}
    nodes = workflow_json.get("nodes", [])

    if not isinstance(nodes, list):
        return False

    invalid_recipients = {"", "connected_user", "my_gmail", "my gmail", "my email"}

    for node in nodes:
        if not isinstance(node, dict):
            continue

        if node.get("type") != "n8n-nodes-base.gmail":
            continue

        parameters = node.get("parameters", {})
        send_to = str(parameters.get("sendTo", "")).strip().lower()

        if send_to in invalid_recipients:
            return True

    return False


def build_chat_context_message(user_message, history):
    """
    Optional helper for current chat memory.
    """
    if not isinstance(history, list) or not history:
        return user_message

    recent_items = history[-6:]
    lines = ["Recent chat context:"]

    for item in recent_items:
        if not isinstance(item, dict):
            continue

        role = str(item.get("role", "")).strip() or "unknown"
        text = str(item.get("text", item.get("message", ""))).strip()

        if text:
            lines.append(f"{role}: {text}")

    lines.append("")
    lines.append("Current user message:")
    lines.append(user_message)

    return "\n".join(lines)


# -----------------------------
# Slack n8n credential sync helper
# -----------------------------

def sync_slack_oauth_credential_to_n8n(slack_connection, credential_name="Slack account"):
    """
    ADDED SAFELY:
    Tries to create/update an n8n Slack OAuth2 credential from the Slack OAuth token.

    If n8n rejects this schema on your local version, this returns a clear failure.
    It does not crash login or break saved Slack connection.
    """
    if create_or_update_credential is None:
        return {
            "status": "failed",
            "can_continue": False,
            "message": "n8n_client.create_or_update_credential is not available. Slack n8n credential was not synced.",
            "credential": None
        }

    if not isinstance(slack_connection, dict):
        return {
            "status": "invalid_input",
            "can_continue": False,
            "message": "slack_connection must be a dictionary.",
            "credential": None
        }

    access_token = safe_text(slack_connection.get("access_token"))
    scope = safe_text(slack_connection.get("scope"))

    if not access_token:
        return {
            "status": "invalid_input",
            "can_continue": False,
            "message": "Slack access_token is missing. Reconnect Slack from app first.",
            "credential": None
        }

    # CHANGED:
    # Exact correction based on your n8n Slack credential schema output:
    # - oauthTokenData type = json, so keep it as a dict/object, NOT a string.
    # - additionalBodyProperties type = json, so keep it as a dict/object, NOT a string.
    # - userScope type = string, so send Slack scopes as text, NOT boolean.
    #
    # We keep only the fields shown by your n8n schema for slackOAuth2Api.
    slack_user_scope = scope or "channels:read channels:history chat:write"

    credential_data = {
        "serverUrl": "https://slack.com/api",
        "clientId": SLACK_CLIENT_ID,
        "clientSecret": SLACK_CLIENT_SECRET,
        "sendAdditionalBodyProperties": False,
        "additionalBodyProperties": {},
        "oauthTokenData": {
            "access_token": access_token,
            "token_type": safe_text(slack_connection.get("token_type")) or "Bearer",
            "scope": slack_user_scope,
            "bot_user_id": safe_text(slack_connection.get("bot_user_id")),
            "team": {
                "id": safe_text(slack_connection.get("team_id")),
                "name": safe_text(slack_connection.get("workspace_name"))
            }
        },
        "customScopes": True,
        "customScopesNotice": "",
        "userScope": slack_user_scope
    }

    try:
        return create_or_update_credential(
            credential_name=credential_name,
            credential_type="slackOAuth2Api",
            credential_data=credential_data
        )
    except Exception as error:
        return {
            "status": "error",
            "can_continue": False,
            "message": "Error while syncing Slack credential to n8n.",
            "error": str(error),
            "credential": None
        }


# -----------------------------
# Login routes
# -----------------------------

@app.get("/login/google")
def login_google(request: Request):
    """
    Starts Google Login for app authentication.

    Different from /connect/gmail:
    - /login/google identifies who is using the app.
    - /connect/gmail grants Gmail workflow permissions.
    """
    state = secrets.token_urlsafe(24)
    request.session["google_login_state"] = state

    redirect_uri = str(request.url_for("google_login_callback"))

    scopes = [
        "openid",
        "email",
        "profile"
    ]

    query_params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "online",
        "prompt": "select_account",
        "state": state
    }

    google_authorize_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(query_params)

    return RedirectResponse(google_authorize_url)


@app.get("/auth/google/login/callback")
def google_login_callback(request: Request):
    """
    Handles Google Login callback.
    """
    code = request.query_params.get("code")
    error = request.query_params.get("error")
    state = request.query_params.get("state")

    if error:
        request.session.clear()
        return {
            "status": "failed",
            "stage": "google_login",
            "message": "Google login failed.",
            "error": error
        }

    if not code:
        request.session.clear()
        return {
            "status": "failed",
            "stage": "google_login",
            "message": "Google login callback missing code."
        }

    expected_state = request.session.get("google_login_state")

    if expected_state and state != expected_state:
        request.session.clear()
        return {
            "status": "failed",
            "stage": "google_login",
            "message": "Invalid Google login state. Please try logging in again."
        }

    redirect_uri = str(request.url_for("google_login_callback"))
    token_url = "https://oauth2.googleapis.com/token"

    response = requests.post(
        token_url,
        data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code"
        },
        timeout=30
    )

    token_data = response.json()

    if "access_token" not in token_data:
        request.session.clear()
        return {
            "status": "failed",
            "stage": "google_login",
            "message": "Google login token exchange failed.",
            "google_response": token_data
        }

    access_token = token_data.get("access_token", "")

    userinfo_response = requests.get(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={
            "Authorization": f"Bearer {access_token}"
        },
        timeout=30
    )

    userinfo = userinfo_response.json()

    email = str(userinfo.get("email", "")).strip().lower()
    name = userinfo.get("name", "")
    google_user_id = str(userinfo.get("id", "")).strip()

    if not is_email_authorized(email):
        request.session.clear()
        return {
            "status": "access_denied",
            "stage": "authorization",
            "message": "You do not have access to this AI Workflow app. Please contact admin.",
            "email": email
        }

    user_id = google_user_id or email

    request.session["user"] = {
        "user_id": user_id,
        "email": email,
        "name": name,
        "authorized": True
    }

    request.session.pop("google_login_state", None)

    # ADDED:
    # Create/update this user's connection record immediately after login.
    ensure_user(
        user_id,
        profile={
            "email": email,
            "name": name
        }
    )

    return RedirectResponse("/")


@app.get("/me")
def me(request: Request):
    """
    Frontend can call this route to know whether the user is logged in.
    """
    user = get_current_user(request)

    if not user:
        return {
            "logged_in": False,
            "authorized": False,
            "login_url": "/login/google"
        }

    return {
        "logged_in": True,
        "authorized": True,
        "user": {
            "user_id": user.get("user_id", ""),
            "email": user.get("email", ""),
            "name": user.get("name", "")
        }
    }


@app.post("/logout")
def logout(request: Request):
    """
    Clears login session and disconnects user tool connections for testing.
    """
    user = get_current_user(request)

    if user:
        user_id = safe_text(user.get("user_id"))

        # Clear saved tool connections for this app user
        save_user_connection(user_id, "gmail", {"connected": False})
        save_user_connection(user_id, "slack", {"connected": False})

    request.session.clear()

    return {
        "status": "ok",
        "message": "Logged out successfully."
    }


@app.get("/logout")
def logout_get(request: Request):
    """
    Browser-friendly logout route.
    Clears login session and disconnects user tool connections for testing.
    """
    user = get_current_user(request)

    if user:
        user_id = safe_text(user.get("user_id"))

        # Clear saved tool connections for this app user
        save_user_connection(user_id, "gmail", {"connected": False})
        save_user_connection(user_id, "slack", {"connected": False})

    request.session.clear()

    return RedirectResponse("/")

# -----------------------------
# UI and health
# -----------------------------

@app.get("/")
def serve_frontend():
    """
    Opens the UI at http://127.0.0.1:8000.
    """
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


@app.get("/health")
def health_check():
    """
    Backend health check.
    """
    return {
        "status": "ok",
        "message": "Dynamic AI Workflow API is running."
    }

@app.get("/debug/allowed-users")
def debug_allowed_users():
    return {
        "allowed_users_file": ALLOWED_USERS_FILE,
        "file_exists": os.path.exists(ALLOWED_USERS_FILE),
        "allowed_access": load_allowed_access()
    }
# -----------------------------
# Chat / workflow execution
# -----------------------------
def replace_my_gmail_with_recipient_email(original_message, recipient_email):
    """
    Converts a pending ambiguous Gmail destination request into a clear
    pipeline-friendly instruction.

    Example:
        Original: "send me the butter chicken recipe to my gmail"
        Reply:    "vijji.m18@gmail.com"
        Result:   "send the butter chicken recipe to vijji.m18@gmail.com"

        Original: "say hi on my gmail"
        Reply:    "vijayalaxmi.boddu1994@gmail.com"
        Result:   "send hi to vijayalaxmi.boddu1994@gmail.com"

    This avoids relying on hardcoded prepositions such as "to/on/in/from".
    """
    message = str(original_message or "").strip()
    email = safe_text(recipient_email).lower()

    if not message or not email:
        return message

    # Remove ambiguous personal destination wording anywhere in the message.
    cleaned = re.sub(
        r"\b(my\s+(gmail|gamil|email)(\s+account)?|gmail|gamil)\b",
        "",
        message,
        flags=re.IGNORECASE
    )

    # Remove common connector words left around the removed phrase.
    cleaned = re.sub(
        r"\b(to|on|in|into|at|via|through|using)\s*$",
        "",
        cleaned,
        flags=re.IGNORECASE
    )

    # Normalize leading send-style words into a clear send instruction.
    cleaned = cleaned.strip(" .,-")
    cleaned = re.sub(r"^\s*send\s+me\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^\s*send\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^\s*email\s+me\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^\s*email\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^\s*mail\s+me\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^\s*mail\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^\s*say\s+", "", cleaned, flags=re.IGNORECASE)

    # If only a tiny message remains, still make a valid send request.
    content = cleaned.strip(" .,-") or "hi"

    return f"send {content} to {email}"

@app.post("/chat")
def chat(request: Request, chat_request: ChatRequest):
    """
    Main chat endpoint.

    Option B flow:
    1. Check authorized logged-in user.
    2. Handle pending follow-up memory.
    3. Prepare Gmail recipient only when user says "my Gmail" / "my email".
    4. Run planner/resolver/composer/builder/create pipeline for that user.
    5. Validate Gmail read/source account when needed.
    6. If n8n workflow is created, activate it.
    7. Find webhook path from generated workflow_json.
    8. Call production webhook.
    9. Return webhook response to UI.
    """
    auth_result = require_authorized_user(request)

    if not auth_result.get("ok"):
        return auth_result

    current_user = auth_result.get("user", {})
    current_user_id = safe_text(current_user.get("user_id"))

    user_message = chat_request.message.strip()

    if not user_message:
        return {
            "stage": "input",
            "status": "empty_message",
            "message": "Please enter a request."
        }

    # -------------------------------------------------
    # Pending follow-up memory:
    # Example:
    # User first says:
    #   send me the butter chicken recipe to my gmail
    # App asks:
    #   Which email address should I send it to?
    # User replies:
    #   vijji.m18@gmail.com
    # Then backend continues using:
    #   send me the butter chicken recipe to vijji.m18@gmail.com
    # -------------------------------------------------
    pending_action = request.session.get("pending_action")

    if isinstance(pending_action, dict):
        pending_type = safe_text(pending_action.get("type"))

        if pending_type == "gmail_recipient_resolution":
            provided_emails = extract_email_addresses_from_text(user_message)

            if not provided_emails:
                return {
                    "can_continue": False,
                    "stage": "gmail_recipient_resolution",
                    "status": "follow_up",
                    "message": "Please provide the Gmail/email address I should send it to.",
                    "follow_up_question": "Please provide the Gmail/email address I should send it to.",
                    "connect_actions": []
                }

            recipient_email = provided_emails[0]
            original_pending_message = safe_text(
                pending_action.get("original_message")
            )

            resolved_message = replace_my_gmail_with_recipient_email(
                original_pending_message,
                recipient_email
            )

            request.session.pop("pending_action", None)

            # Continue this request using the resolved previous message.
            user_message = resolved_message

    # -------------------------------------------------
    # Gmail recipient preparation:
    # Only handles ambiguous send wording like:
    #   "to my gmail"
    #   "to my email"
    #   "email me"
    #
    # It should NOT use app login email automatically.
    # It should ask for the recipient email and store pending_action.
    # -------------------------------------------------
    gmail_prepare_result = prepare_message_for_gmail_recipient(
        user_message,
        user_id=current_user_id
    )

    if not gmail_prepare_result.get("can_continue"):
        pending_action = gmail_prepare_result.get("pending_action")

        if isinstance(pending_action, dict):
            request.session["pending_action"] = pending_action

        return {
            "can_continue": False,
            "stage": "gmail_account_selection",
            "status": "follow_up",
            "message": gmail_prepare_result.get("message"),
            "follow_up_question": gmail_prepare_result.get("message"),
            "connect_actions": gmail_prepare_result.get("connect_actions", [])
        }

    prepared_current_message = (
        gmail_prepare_result.get("message_for_pipeline") or user_message
    )

    message_for_pipeline = prepared_current_message

    # -------------------------------------------------
    # Run pipeline for current logged-in app user.
    # current_user_id is app/session owner.
    # Tool account selection must be handled separately.
    # -------------------------------------------------
    pipeline_result = run_pipeline(
        message_for_pipeline,
        user_id=current_user_id
    )

    if not isinstance(pipeline_result, dict):
        return {
            "stage": "pipeline",
            "status": "invalid_pipeline_result",
            "message": "Pipeline returned invalid result.",
            "pipeline_result": pipeline_result
        }
    # -------------------------------------------------
    # Resolver failure / missing tool connection handling.
    # Example:
    #   User says "send hi on my gmail"
    #   App asks recipient
    #   User gives vijji.m18@gmail.com
    #   Pipeline now needs Gmail SEND tool connected.
    #
    # If Gmail is missing, return a top-level connect_actions response
    # so frontend can show Connect Gmail button.
    # -------------------------------------------------
    resolver_output = pipeline_result.get("resolver_output") or {}

    if isinstance(resolver_output, dict):
        resolver_can_continue = resolver_output.get("can_continue", True)
        missing_connections = resolver_output.get("missing_connections") or []
        missing_credentials = resolver_output.get("missing_credentials") or []
        resolver_connect_actions = resolver_output.get("connect_actions") or []

        if (
            resolver_can_continue is False
            or missing_connections
            or missing_credentials
        ):
            connect_actions = []

            if isinstance(resolver_connect_actions, list):
                connect_actions.extend(resolver_connect_actions)

            # Safety fallback: if resolver says Gmail missing but does not provide button
            if "gmail" in missing_connections and not any(
                action.get("tool") == "gmail"
                for action in connect_actions
                if isinstance(action, dict)
            ):
                connect_actions.append({
                    "tool": "gmail",
                    "label": "Connect Gmail",
                    "url": "/connect/gmail"
                })

            if "slack" in missing_connections and not any(
                action.get("tool") == "slack"
                for action in connect_actions
                if isinstance(action, dict)
            ):
                connect_actions.append({
                    "tool": "slack",
                    "label": "Connect Slack",
                    "url": "/connect/slack"
                })

            return {
                "can_continue": False,
                "stage": "tool_connection_required",
                "status": "missing_connections",
                "message": resolver_output.get(
                    "message",
                    "One or more required tools are not connected for this user."
                ),
                "missing_connections": missing_connections,
                "missing_credentials": missing_credentials,
                "connect_actions": connect_actions,
                "pipeline_result": pipeline_result
            }
    # -------------------------------------------------
    # Gmail READ/source account validation.
    # Example:
    #   summarize unread emails in vijji.m18@gmail.com inbox
    #
    # This should check whether requested Gmail tool account is connected.
    # It should not use app login email automatically.
    # -------------------------------------------------
    
    planner_output = pipeline_result.get("planner_output") or {}

    # -------------------------------------------------
    # Store pending recipient memory when the planner itself asks for a
    # recipient. This covers wordings the deterministic helper did not catch.
    # Example:
    #   User: "say hi on my gmail"
    #   Planner: "Who should I send the email to?"
    #   User: "vijayalaxmi.boddu1994@gmail.com"
    # -------------------------------------------------
    planner_decision = safe_text(planner_output.get("decision")).lower()
    follow_up_question = safe_text(planner_output.get("follow_up_question"))

    if planner_decision == "follow_up" or follow_up_question:
        follow_up_text = follow_up_question.lower()
        current_text = prepared_current_message.lower()

        asks_for_email_recipient = (
            ("who" in follow_up_text and "send" in follow_up_text)
            or "email address" in follow_up_text
            or "recipient" in follow_up_text
            or ("send" in follow_up_text and "email" in follow_up_text)
        )

        looks_like_email_send_request = (
            "gmail" in current_text
            or "email" in current_text
            or "send" in current_text
            or "mail" in current_text
        )

        if asks_for_email_recipient and looks_like_email_send_request:
            request.session["pending_action"] = {
                "type": "gmail_recipient_resolution",
                "original_message": prepared_current_message
            }

        return pipeline_result

    gmail_read_account_check = validate_gmail_read_account_matches_connection(
        user_message=user_message,
        user_id=current_user_id,
        planner_output=planner_output
    )

    if not gmail_read_account_check.get("can_continue"):
        return gmail_read_account_check

    # -------------------------------------------------
    # Gmail SEND unresolved recipient safety.
    # This prevents sending when builder leaves recipient as:
    #   connected_user / my_gmail / my email
    # -------------------------------------------------
    destination = planner_output.get("destination") or {}

    is_gmail_send_destination = (
        isinstance(destination, dict)
        and safe_text(destination.get("tool")).lower() == "gmail"
        and safe_text(destination.get("operation")).lower() in {"send", "create"}
    )

    if is_gmail_send_destination and pipeline_has_unresolved_connected_user(
        pipeline_result
    ):
        return {
            "can_continue": False,
            "stage": "gmail_recipient_resolution",
            "status": "follow_up",
            "message": "Which Gmail/email address should I send it to?",
            "follow_up_question": "Which Gmail/email address should I send it to?",
            "connect_actions": [],
            "pipeline_result": pipeline_result
        }

    # -------------------------------------------------
    # Get created workflow.
    # -------------------------------------------------
    created_workflow = pipeline_result.get("created_workflow") or {}

    if not created_workflow:
        create_output = pipeline_result.get("create_output") or {}
        created_workflow = create_output.get("created_workflow") or {}

    workflow_id = created_workflow.get("id", "")

    builder_output = pipeline_result.get("builder_output") or {}
    workflow_json = (
        builder_output.get("workflow_json")
        or pipeline_result.get("workflow_json")
        or {}
    )

    if not workflow_id:
        return pipeline_result

    webhook_path = find_webhook_path_from_workflow_json(workflow_json)

    if not webhook_path:
        return {
            "stage": "webhook_path",
            "status": "missing_webhook_path",
            "message": "Workflow was created, but no webhook path was found.",
            "pipeline_result": pipeline_result
        }

    # -------------------------------------------------
    # Activate workflow.
    # -------------------------------------------------
    activate_result = activate_workflow(workflow_id)

    if not activate_result.get("can_continue"):
        return {
            "stage": "activate_workflow",
            "status": "failed",
            "message": "Workflow was created, but activation failed.",
            "workflow_id": workflow_id,
            "webhook_path": webhook_path,
            "activate_result": activate_result,
            "pipeline_result": pipeline_result
        }

    # -------------------------------------------------
    # Call production webhook.
    # -------------------------------------------------
    webhook_payload = {
        "message": user_message,
        "message_for_pipeline": message_for_pipeline,
        "workflow_id": workflow_id,
        "current_user": {
            "user_id": current_user.get("user_id", ""),
            "email": current_user.get("email", "")
        }
    }

    webhook_result = call_production_webhook(
        webhook_path,
        webhook_payload,
        timeout=180
    )

    if not webhook_result.get("can_continue"):
        return {
            "stage": "call_webhook",
            "status": "failed",
            "message": "Workflow activated, but production webhook call failed.",
            "workflow_id": workflow_id,
            "webhook_path": webhook_path,
            "activate_result": activate_result,
            "webhook_result": webhook_result,
            "pipeline_result": pipeline_result
        }

    return {
        "stage": "workflow_executed",
        "status": "ok",
        "message": "Workflow created, activated, executed, and response returned.",
        "workflow_id": workflow_id,
        "webhook_path": webhook_path,
        "webhook_response": webhook_result.get("webhook_response"),
        "debug": {
            "created_workflow": created_workflow,
            "activate_result": activate_result,
            "webhook_result": webhook_result,
            "pipeline_result": pipeline_result
        }
    }

@app.post("/chat/reset")
def reset_chat_state(request: Request):
    """
    Clears only temporary chat workflow state.

    Does NOT logout user.
    Does NOT disconnect Gmail/Slack OAuth.
    Does NOT delete n8n credentials.

    Clears:
    - pending follow-up action
    - pending workflow memory
    """
    request.session.pop("pending_action", None)
    request.session.pop("pending_workflow", None)

    return {
        "status": "ok",
        "message": "Temporary chat state cleared."
    }

# -----------------------------
# Slack OAuth
# -----------------------------

@app.get("/connect/slack")
def connect_slack(request: Request):
    """
    Starts Slack OAuth flow for the current logged-in user.
    """
    auth_result = require_authorized_user(request)

    if not auth_result.get("ok"):
        return RedirectResponse("/login/google")

    state = secrets.token_urlsafe(24)
    request.session["slack_oauth_state"] = state

    scopes = [
        "channels:read",
        "channels:history",
        "chat:write"
    ]

    query_params = {
        "client_id": SLACK_CLIENT_ID,
        "scope": ",".join(scopes),
        "redirect_uri": SLACK_REDIRECT_URI,
        "state": state,
    }

    slack_authorize_url = "https://slack.com/oauth/v2/authorize?" + urlencode(query_params)

    return RedirectResponse(slack_authorize_url)


@app.get("/auth/slack/callback")
def slack_oauth_callback(request: Request):
    """
    Handles Slack OAuth callback and saves Slack connection under current user.
    """
    auth_result = require_authorized_user(request)

    if not auth_result.get("ok"):
        return auth_result

    current_user = auth_result.get("user", {})
    current_user_id = safe_text(current_user.get("user_id"))

    code = request.query_params.get("code")
    error = request.query_params.get("error")
    state = request.query_params.get("state")

    expected_state = request.session.get("slack_oauth_state")

    if expected_state and state != expected_state:
        return {
            "status": "failed",
            "tool": "slack",
            "message": "Invalid Slack OAuth state. Please try connecting Slack again."
        }

    if error:
        return {
            "status": "failed",
            "tool": "slack",
            "message": "Slack OAuth failed.",
            "error": error
        }

    if not code:
        return {
            "status": "failed",
            "tool": "slack",
            "message": "Slack OAuth callback missing code."
        }

    token_url = "https://slack.com/api/oauth.v2.access"

    response = requests.post(
        token_url,
        auth=(SLACK_CLIENT_ID, SLACK_CLIENT_SECRET),
        data={
            "code": code,
            "redirect_uri": SLACK_REDIRECT_URI
        },
        timeout=30
    )

    token_data = response.json()

    if not token_data.get("ok"):
        return {
            "status": "failed",
            "tool": "slack",
            "message": "Slack token exchange failed.",
            "slack_response": token_data
        }

    access_token = token_data.get("access_token", "")
    team = token_data.get("team", {})
    authed_user = token_data.get("authed_user", {})

    slack_connection = {
        "connected": True,
        "access_token": access_token,
        "token_type": token_data.get("token_type", ""),
        "scope": token_data.get("scope", ""),
        "bot_user_id": token_data.get("bot_user_id", ""),
        "oauth_token_data": token_data,
        "team_id": team.get("id", ""),
        "workspace_name": team.get("name", ""),
        "user_id": authed_user.get("id", "")
    }

    save_user_connection(current_user_id, "slack", slack_connection)
    request.session.pop("slack_oauth_state", None)

    # ADDED:
    # Try to create/update this user's n8n Slack credential.
    slack_credential_name = f"Slack account - {team.get('name', 'workspace')} - {current_user_id[:8]}"
    slack_credential_sync_result = sync_slack_oauth_credential_to_n8n(
        slack_connection,
        credential_name=slack_credential_name
    )

    slack_n8n_credential_mapping = None

    if slack_credential_sync_result.get("can_continue"):
        synced_credential = slack_credential_sync_result.get("credential") or {}

        slack_n8n_credential_mapping = update_user_credential(
            user_id=current_user_id,
            tool_name="slack",
            credential_type=synced_credential.get("type", "slackOAuth2Api"),
            credential_id=synced_credential.get("id", ""),
            credential_name=synced_credential.get("name", slack_credential_name)
        )

    return {
        "status": "ok",
        "tool": "slack",
        "message": "Slack connected successfully from app OAuth.",
        "workspace": team.get("name", ""),
        "team_id": team.get("id", ""),
        "n8n_credential_sync": slack_credential_sync_result,
        "n8n_credential_mapping": slack_n8n_credential_mapping
    }


# -----------------------------
# Gmail OAuth
# -----------------------------

@app.get("/connect/gmail")
def connect_gmail(request: Request):
    """
    Starts Gmail OAuth flow for the current logged-in app user.

    Important:
    - This connects a Gmail TOOL account.
    - It does not change the app-login user.
    - Optional query param requested_email helps when the user asked for a
      specific Gmail source, for example /connect/gmail?requested_email=abc@gmail.com.
    """
    auth_result = require_authorized_user(request)

    if not auth_result.get("ok"):
        return RedirectResponse("/login/google")

    requested_email = safe_text(request.query_params.get("requested_email")).lower()

    state = secrets.token_urlsafe(24)
    request.session["gmail_oauth_state"] = state

    if requested_email:
        request.session["requested_gmail_email"] = requested_email
    else:
        request.session.pop("requested_gmail_email", None)

    scopes = [
        "openid",
        "email",
        "profile",
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.modify"
    ]

    query_params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "prompt": "consent select_account",
        "state": state
    }

    if requested_email:
        # Helps Google show/select the Gmail source the user asked for.
        query_params["login_hint"] = requested_email

    google_authorize_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(query_params)

    return RedirectResponse(google_authorize_url)


@app.get("/auth/gmail/callback")
def gmail_oauth_callback(request: Request):
    """
    Handles Gmail OAuth callback and saves Gmail connection under current user.
    """
    auth_result = require_authorized_user(request)

    if not auth_result.get("ok"):
        return auth_result

    current_user = auth_result.get("user", {})
    current_user_id = safe_text(current_user.get("user_id"))

    code = request.query_params.get("code")
    error = request.query_params.get("error")
    state = request.query_params.get("state")

    expected_state = request.session.get("gmail_oauth_state")
    requested_gmail_email = safe_text(request.session.get("requested_gmail_email")).lower()

    if expected_state and state != expected_state:
        return {
            "status": "failed",
            "tool": "gmail",
            "message": "Invalid Gmail OAuth state. Please try connecting Gmail again."
        }

    if error:
        return {
            "status": "failed",
            "tool": "gmail",
            "message": "Gmail OAuth failed.",
            "error": error
        }

    if not code:
        return {
            "status": "failed",
            "tool": "gmail",
            "message": "Gmail OAuth callback missing code."
        }

    token_url = "https://oauth2.googleapis.com/token"

    response = requests.post(
        token_url,
        data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code"
        },
        timeout=30
    )

    token_data = response.json()

    if "access_token" not in token_data:
        return {
            "status": "failed",
            "tool": "gmail",
            "message": "Gmail token exchange failed.",
            "google_response": token_data
        }

    access_token = token_data.get("access_token", "")
    refresh_token = token_data.get("refresh_token", "")
    expires_in = token_data.get("expires_in", "")

    userinfo_response = requests.get(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={
            "Authorization": f"Bearer {access_token}"
        },
        timeout=30
    )

    userinfo = userinfo_response.json()
    email = userinfo.get("email", "")

    gmail_connection = {
        "connected": True,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_in": expires_in,
        "scope": token_data.get("scope", ""),
        "token_type": token_data.get("token_type", "Bearer"),
        "oauth_token_data": token_data,
        "email": email
    }

    save_user_connection(current_user_id, "gmail", gmail_connection)
    request.session.pop("gmail_oauth_state", None)
    request.session.pop("requested_gmail_email", None)

    # CHANGED:
    # Create/update this user's n8n Gmail credential.
    gmail_credential_name = f"Gmail account - {email} - {current_user_id[:8]}"
    gmail_credential_sync_result = sync_gmail_oauth_credential_to_n8n(
        gmail_connection,
        credential_name=gmail_credential_name
    )

    gmail_n8n_credential_mapping = None

    if gmail_credential_sync_result.get("can_continue"):
        synced_credential = gmail_credential_sync_result.get("credential") or {}

        gmail_n8n_credential_mapping = update_user_credential(
            user_id=current_user_id,
            tool_name="gmail",
            credential_type=synced_credential.get("type", "gmailOAuth2"),
            credential_id=synced_credential.get("id", ""),
            credential_name=synced_credential.get("name", gmail_credential_name)
        )

    redirect_params = {
        "gmail_connected": "true",
        "gmail_email": email
    }

    if requested_gmail_email:
        redirect_params["requested_gmail_email"] = requested_gmail_email
        redirect_params["gmail_account_match"] = str(
            safe_text(email).lower() == requested_gmail_email
        ).lower()

    return RedirectResponse("/?" + urlencode(redirect_params))


# -----------------------------
# Connections status
# -----------------------------

@app.get("/connections")
def get_connections(request: Request):
    """
    Shows current logged-in user's connection and credential status.
    """
    auth_result = require_authorized_user(request)

    if not auth_result.get("ok"):
        return auth_result

    current_user = auth_result.get("user", {})
    current_user_id = safe_text(current_user.get("user_id"))

    user_connections = load_user_connections(current_user_id)
    user_credentials = load_user_credentials(current_user_id)
    app_credentials = load_credentials()

    gmail_connection = user_connections.get("gmail", {})
    slack_connection = user_connections.get("slack", {})

    return {
        "status": "ok",
        "user": {
            "user_id": current_user_id,
            "email": current_user.get("email", ""),
            "name": current_user.get("name", "")
        },
        "connections": {
            "slack": bool(slack_connection.get("connected", False)),
            "gmail": bool(gmail_connection.get("connected", False))
        },
        "connection_details": {
            "gmail_email": gmail_connection.get("email", ""),
            "gmail_accounts": get_connected_gmail_accounts(current_user_id),
            "slack_workspace": slack_connection.get("workspace_name", ""),
            "slack_team_id": slack_connection.get("team_id", "")
        },
        "credentials": {
            "slack": bool(user_credentials.get("slack", {}).get("connected", False)),
            "gmail": bool(user_credentials.get("gmail", {}).get("connected", False)),
            "openai": bool(app_credentials.get("openai", {}).get("connected", False))
        }
    }
