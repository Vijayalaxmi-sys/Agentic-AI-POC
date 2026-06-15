"""
planner.py

Safe planner for Dynamic AI Workflow v2.

Goal:
- Understand user input.
- Decide direct_answer / follow_up / workflow_required / cannot_execute.
- Detect Slack and Gmail as source OR destination.
- Support MULTIPLE sources using sources[].
- Detect trigger, condition, missing fields, and required connections.
- Return one clean JSON structure only.
"""

import json
import os  # ADDED: used for creating logs folder safely
import re
from datetime import datetime  # ADDED: used for planner debug timestamps
from typing import Any, Dict, List

from openai import OpenAI
from config import OPENAI_API_KEY, PLANNER_MODEL, MAX_OUTPUT_TOKENS, TEMPERATURE


client = OpenAI(api_key=OPENAI_API_KEY)


# -----------------------------
# Standard allowed values
# -----------------------------

ALLOWED_DECISIONS = {
    "direct_answer",
    "follow_up",
    "workflow_required",
    "cannot_execute",
}

ALLOWED_TOOLS = {
    "gmail",
    "slack",
    "chat",
    "web",
    "file",
    "none",
}

ALLOWED_OPERATIONS = {
    "read",
    "search",
    "send",
    "return",
    "generate",
    "summarize",
    "extract",
    "classify",
    "transform",
    "analyze",
    "compare",
    "filter",
    "write",
    "create",
    "update",
    "delete",
    "none",
}

ALLOWED_TRIGGER_TYPES = {
    "manual",
    "schedule",
    "webhook",
    "event",
    "none",
}

TECHNICAL_BLOCKED_WORDS = [
    "api key",
    "apikey",
    "oauth",
    "token",
    "credential",
    "credentials",
    "password",
    "secret",
    "smtp",
    "config.py",
    "backend config",
    "n8n node",
    "authorization",
    "bearer",
]


# -----------------------------
# Default plan structure
# -----------------------------

def empty_source() -> Dict[str, Any]:
    # ADDED: One reusable source object. We now use sources[] instead of one source.
    return {
        "tool": "none",
        "operation": "none",
        "target": "",
        "query": "",
        "filters": {},
    }


def empty_plan() -> Dict[str, Any]:
    return {
        "decision": "direct_answer",
        "intent": "",
        "summary": "",

        # CHANGED: Removed old single "source" field.
        # New standard is sources[] because one user request can read from Gmail + Slack together.
        "sources": [],

        "processing": {
            "required": False,
            "operation": "none",
            "instruction": "",
        },
        "destination": {
            "tool": "chat",
            "operation": "return",
            "target": "",
            "recipient": "",
            # ADDED:
            # Direct send payload text. Example:
            # "send hello to Slack #new-channel" -> message = "hello"
            "message": "",
            "query": "",
        },
        "trigger": {
            "type": "manual",
            "value": "",
        },
        "condition": {
            "required": False,
            "field": "",
            "operator": "",
            "value": "",
            "unit": "",
        },
        "missing_fields": [],
        "follow_up_question": "",
        "direct_answer": "",
        "requires_connection": [],
    }


# -----------------------------
# Prompt
# -----------------------------

def build_prompt(user_message: str) -> str:
    return f"""
You are an intent planner for a real automation application.

Return ONLY valid JSON. No markdown. No explanation outside JSON.

The application supports:
- Slack as source or destination
- Gmail as source or destination
- Multiple sources in one request, for example Slack + Gmail together
- Chat/UI direct answer
- Web source for live/public lookup
- File source/destination later
- AI content generation and processing

Your job:
1. Understand the user request.
2. Decide one of:
   - direct_answer
   - follow_up
   - workflow_required
   - cannot_execute
3. Detect sources, processing, destination, trigger, condition, missing fields, and required connections.
4. Ask follow-up only for missing user-facing information.
5. Never ask for API keys, OAuth, tokens, credentials, password, backend setup, n8n node names, or config.

Important rules:
- If user asks a normal knowledge question and no external/live/app action is needed, decision = direct_answer and destination.tool = chat.
- If user wants Slack/Gmail/web/file/action/automation/schedule/notification, decision = workflow_required unless user-facing details are missing.
- If user-facing details are missing, decision = follow_up.

Source/destination rules:
- Treat "from X", "read X", "get X", "search X", "check X" as source detection.
- Treat "to Y", "send to Y", "post to Y", "share to Y", "email to Y" as destination detection.
- Do not assume one direction. Gmail can be source and Slack can be destination. Slack can be source and Gmail can be destination.
- If destination mentions Slack, Slack channel, channel name after Slack/channel, or #channel, destination.tool = slack and destination.operation = send.
- If Slack channel name is written without #, normalize target with #. Example: "Slack channel new-channel" -> "#new-channel".
- If destination mentions Gmail/email/mail or contains an email address, destination.tool = gmail and destination.operation = send.
- If the user says "from my Gmail", that is a Gmail source, not a Gmail destination.
- Never use "connected_user" as a fake email recipient. Use connected_user only when the user explicitly says "to me", "myself", "to my email", or "to my Gmail".
- If the user asks to send/post/share but destination is not clear, decision = follow_up and ask where to send it.
- If the user asks to read/show/display only and no destination is specified, use destination.tool = chat and operation = return.
- Use sources[] for all input data sources.
- Do not return a top-level source object. Return sources[] only.
- Slack can be a source: read/search Slack messages from a channel.
- Slack can be a destination: send message to a channel or user.
- Gmail can be a source: read/search emails.
- Gmail can be a destination: send/create/reply email.
- If user asks to read Slack and Gmail together, put both inside sources[].
- If user says display here/show here/return here, destination.tool = chat and operation = return.
- Never hardcode #new-channel. Always extract the exact Slack channel from the user message.
- Slack channel examples like #new-channel, #general, #project-updates are examples only.
- If user says Slack but gives no channel/user when sending or reading Slack, ask for slack_channel.
- If user says send email/Gmail but gives no recipient, ask for recipient_email.
- If user says "my Gmail", "my email", "to me", or "myself", set destination.recipient = "connected_user".
- If user says summarize/read latest Gmail emails, recipient is not required because destination can be chat.
- If user specifies how many messages/emails to read, add limit to the matching source object.
- Examples: "read 12 Slack updates" means Slack source limit = 12.
- Examples: "8 unread Gmail emails" means Gmail source limit = 8.
- If user says unread Gmail emails, set Gmail source filters.status = "unread".
- If user asks to send/post/share a direct text message to Slack, put that exact text in destination.message.
- Example: "send hello to Slack #general" means destination.message = "hello".
- Example: "send hello from dynamic workflow to Slack #new-channel" means destination.message = "hello from dynamic workflow".
- Do not drop the content that should be sent.

- If user says alert/notify when something happens but condition is missing, ask for condition.
- If user says recurring/daily/every/scheduled but timing is unclear, ask for schedule.
- If user asks direct answer, do not require Slack/Gmail connection.

Allowed tools:
- gmail
- slack
- chat
- web
- file
- none

Allowed operations:
- read
- search
- send
- return
- generate
- summarize
- extract
- classify
- transform
- analyze
- compare
- filter
- write
- create
- update
- delete
- none

Allowed trigger types:
- manual
- schedule
- webhook
- event
- none

Return exactly this JSON structure:

{{
  "decision": "",
  "intent": "",
  "summary": "",
  "sources": [
    {{
      "tool": "none",
      "operation": "none",
      "target": "",
      "query": "",
      "limit": 0,
      "filters": {{}}
    }}
  ],
  "processing": {{
    "required": false,
    "operation": "none",
    "instruction": ""
  }},
  "destination": {{
    "tool": "chat",
    "operation": "return",
    "target": "",
    "recipient": "",
    "message": "",
    "query": ""
  }},
  "trigger": {{
    "type": "manual",
    "value": ""
  }},
  "condition": {{
    "required": false,
    "field": "",
    "operator": "",
    "value": "",
    "unit": ""
  }},
  "missing_fields": [],
  "follow_up_question": "",
  "direct_answer": "",
  "requires_connection": []
}}

Examples:

User: What is Slack?
Output:
{{
  "decision": "direct_answer",
  "intent": "answer_question",
  "summary": "Explain what Slack is",
  "sources": [],
  "processing": {{"required": false, "operation": "none", "instruction": ""}},
  "destination": {{"tool": "chat", "operation": "return", "target": "", "recipient": ""}},
  "trigger": {{"type": "manual", "value": ""}},
  "condition": {{"required": false, "field": "", "operator": "", "value": "", "unit": ""}},
  "missing_fields": [],
  "follow_up_question": "",
  "direct_answer": "Slack is a team communication platform used for messaging, channels, file sharing, and integrations.",
  "requires_connection": []
}}

User: Create project update and send to Slack #general
Output:
{{
  "decision": "workflow_required",
  "intent": "generate_and_send_slack_message",
  "summary": "Generate a project update and send it to Slack channel #general",
  "sources": [],
  "processing": {{"required": true, "operation": "generate", "instruction": "Create project update"}},
  "destination": {{"tool": "slack", "operation": "send", "target": "#general", "recipient": ""}},
  "trigger": {{"type": "manual", "value": ""}},
  "condition": {{"required": false, "field": "", "operator": "", "value": "", "unit": ""}},
  "missing_fields": [],
  "follow_up_question": "",
  "direct_answer": "",
  "requires_connection": ["slack"]
}}

User: Summarize my latest Gmail emails and send to Slack #project-updates
Output:
{{
  "decision": "workflow_required",
  "intent": "summarize_gmail_and_send_to_slack",
  "summary": "Read latest Gmail emails, summarize them, and send the summary to Slack channel #project-updates",
  "sources": [{{"tool": "gmail", "operation": "read", "target": "inbox", "query": "latest emails", "filters": {{}}}}],
  "processing": {{"required": true, "operation": "summarize", "instruction": "Summarize latest Gmail emails"}},
  "destination": {{"tool": "slack", "operation": "send", "target": "#project-updates", "recipient": ""}},
  "trigger": {{"type": "manual", "value": ""}},
  "condition": {{"required": false, "field": "", "operator": "", "value": "", "unit": ""}},
  "missing_fields": [],
  "follow_up_question": "",
  "direct_answer": "",
  "requires_connection": ["gmail", "slack"]
}}

User: Read Slack updates from #engineering and unread Gmail emails and display here
Output:
{{
  "decision": "workflow_required",
  "intent": "read_multiple_sources_and_display",
  "summary": "Read Slack updates from #engineering and unread Gmail emails, then display the result in chat",
  "sources": [
    {{"tool": "slack", "operation": "read", "target": "#engineering", "query": "updates", "filters": {{}}}},
    {{"tool": "gmail", "operation": "read", "target": "inbox", "query": "unread emails", "filters": {{"status": "unread"}}}}
  ],
  "processing": {{"required": true, "operation": "summarize", "instruction": "Summarize Slack updates and unread Gmail emails"}},
  "destination": {{"tool": "chat", "operation": "return", "target": "", "recipient": ""}},
  "trigger": {{"type": "manual", "value": ""}},
  "condition": {{"required": false, "field": "", "operator": "", "value": "", "unit": ""}},
  "missing_fields": [],
  "follow_up_question": "",
  "direct_answer": "",
  "requires_connection": ["slack", "gmail"]
}}

User: Summarize Slack #engineering messages and email them to Ali
Output:
{{
  "decision": "follow_up",
  "intent": "summarize_slack_and_send_email",
  "summary": "Read Slack channel #engineering messages, summarize them, and email the summary to Ali",
  "sources": [{{"tool": "slack", "operation": "read", "target": "#engineering", "query": "latest messages", "filters": {{}}}}],
  "processing": {{"required": true, "operation": "summarize", "instruction": "Summarize Slack #engineering messages"}},
  "destination": {{"tool": "gmail", "operation": "send", "target": "", "recipient": "Ali"}},
  "trigger": {{"type": "manual", "value": ""}},
  "condition": {{"required": false, "field": "", "operator": "", "value": "", "unit": ""}},
  "missing_fields": ["recipient_email"],
  "follow_up_question": "What is Ali's email address?",
  "direct_answer": "",
  "requires_connection": ["slack", "gmail"]
}}

Now process this user request:
{user_message}
""".strip()


# -----------------------------
# JSON parsing
# -----------------------------

def extract_json(text: str) -> Dict[str, Any]:
    if not text:
        return {}

    text = str(text).strip()

    if text.startswith("```"):
        text = text.replace("```json", "")
        text = text.replace("```JSON", "")
        text = text.replace("```", "")
        text = text.strip()

    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")

    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(text[start:end + 1])
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            pass

    return {}


# -----------------------------
# Small deterministic helpers
# -----------------------------

def safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def unique_list(values: List[Any]) -> List[str]:
    output = []
    seen = set()
    for value in values:
        text = safe_text(value)
        if text and text not in seen:
            output.append(text)
            seen.add(text)
    return output


def contains_any(text: str, keywords: List[str]) -> bool:
    lower = text.lower()
    return any(keyword in lower for keyword in keywords)
def is_plain_knowledge_question(user_message: str) -> bool:
    """
    ADDED:
    Detects normal knowledge questions about tools/apps.

    Examples:
    - what is Slack
    - what is Gmail
    - explain Slack
    - difference between Slack and Gmail

    These should be direct answers and should NOT require Slack/Gmail connection.
    """
    text = user_message.lower().strip()

    question_starts = [
        "what is",
        "what are",
        "what's",
        "explain",
        "define",
        "tell me about",
        "difference between",
        "what is the difference",
    ]

    workflow_action_words = [
        "read",
        "search",
        "check",
        "send",
        "post",
        "summarize",
        "summary",
        "create",
        "generate",
        "write",
        "draft",
        "compose",
        "alert",
        "notify",
        "display here",
        "show here",
        "return here",
        "latest",
        "unread",
        "from #",
        "to #",
    ]

    starts_like_question = any(text.startswith(word) for word in question_starts)
    has_workflow_action = any(word in text for word in workflow_action_words)

    return starts_like_question and not has_workflow_action

def clean_text(value: Any) -> str:
    text = safe_text(value)
    lower = text.lower()
    if any(word in lower for word in TECHNICAL_BLOCKED_WORDS):
        return ""
    return text


# -----------------------------
# Planner debug logging
# -----------------------------

def write_planner_log(user_message: str, plan: Dict[str, Any], raw_model_output: str = "") -> None:
    """
    ADDED:
    Save every planner input and final planner output into logs/planner_logs.jsonl.

    Why:
    - When UI/end-to-end flow breaks later, you can open this file and see
      exactly what planner.py returned.
    - This function must never break the app, so all errors are ignored.
    """
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        logs_dir = os.path.join(current_dir, "logs")
        os.makedirs(logs_dir, exist_ok=True)

        log_record = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "user_input": user_message,
            "planner_output": plan,
        }

        # ADDED: Store raw model output only when available.
        # This is useful if normalized output looks wrong later.
        if raw_model_output:
            log_record["raw_model_output"] = raw_model_output

        log_path = os.path.join(logs_dir, "planner_logs.jsonl")
        with open(log_path, "a", encoding="utf-8") as file:
            file.write(json.dumps(log_record, ensure_ascii=False) + "\n")

    except Exception:
        # ADDED: Logging should never stop planner/API/UI execution.
        pass


def extract_slack_channel(user_message: str) -> str:
    text = user_message or ""

    # Match #general, #project-updates, #team_1
    match = re.search(r"#([A-Za-z0-9_-]+)", text)
    if match:
        return "#" + match.group(1)

    # Match "Slack channel general" or "channel project-updates"
    match = re.search(r"slack\s+channel\s+([A-Za-z0-9_-]+)", text, re.IGNORECASE)
    if match:
        return "#" + match.group(1)

    match = re.search(r"channel\s+([A-Za-z0-9_-]+)", text, re.IGNORECASE)
    if match:
        return "#" + match.group(1)

    return ""



def extract_direct_destination_message(user_message: str, destination_tool: str = "") -> str:
    """
    ADDED:
    Extracts the actual text the user wants to send/post/share.

    Why:
    The model can correctly detect Slack/Gmail destination but forget the
    message text. Example:
        send hello from dynamic workflow to Slack #new-channel
    should produce:
        message = "hello from dynamic workflow"

    This helper is deterministic and only runs for direct send/post/share
    style requests. It does not affect read/summarize workflows.
    """
    original = safe_text(user_message)
    if not original:
        return ""

    text = original

    # Remove common polite prefixes without damaging the real message.
    text = re.sub(r"^\s*(please\s+)?", "", text, flags=re.IGNORECASE).strip()

    # Slack patterns:
    # send HELLO to Slack #channel
    # post HELLO in Slack channel new-channel
    # share HELLO to #channel
    slack_patterns = [
        r"^(?:send|post|share|forward)\s+(.+?)\s+(?:to|into|in|on)\s+(?:my\s+)?slack(?:\s+channel)?(?:\s+#[A-Za-z0-9_-]+|\s+[A-Za-z0-9_-]+)?\s*$",
        r"^(?:send|post|share|forward)\s+(.+?)\s+(?:to|into|in|on)\s+#[A-Za-z0-9_-]+\s*$",
        r"^(?:send|post|share|forward)\s+(.+?)\s+(?:to|into|in|on)\s+channel\s+[A-Za-z0-9_-]+\s*$",
    ]

    # Gmail/email patterns:
    # send HELLO to abc@example.com
    # email HELLO to me
    gmail_patterns = [
        r"^(?:send|email|mail|share|forward)\s+(.+?)\s+to\s+[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\s*$",
        r"^(?:send|email|mail|share|forward)\s+(.+?)\s+to\s+(?:me|myself|my\s+email|my\s+gmail)\s*$",
    ]

    patterns = []
    tool = safe_text(destination_tool).lower()
    if tool == "slack":
        patterns = slack_patterns
    elif tool == "gmail":
        patterns = gmail_patterns
    else:
        patterns = slack_patterns + gmail_patterns

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            message = safe_text(match.group(1))
            message = message.strip(" '\"“”‘’")

            # Avoid treating action/source words as a real direct message.
            # These usually need processing instead of direct send.
            if message.lower() in {"it", "this", "that", "summary", "update", "result", "results"}:
                return ""

            return message

    return ""


def classify_send_content_mode(user_message: str, destination_tool: str, extracted_message: str) -> Dict[str, str]:
    """
    ADDED:
    Smart semantic classifier for ambiguous send requests.

    It decides whether extracted text is already the exact message to send,
    or whether it is an instruction/topic that should be generated by AI first.

    This avoids hardcoded content keyword lists.
    """
    fallback = {
        "mode": "direct_send",
        "instruction": "",
    }

    user_message = safe_text(user_message)
    destination_tool = safe_text(destination_tool).lower()
    extracted_message = safe_text(extracted_message)

    if destination_tool not in {"slack", "gmail"}:
        return fallback

    if not extracted_message:
        return fallback

    classifier_prompt = f"""
Return ONLY valid JSON. No markdown. No explanation.

You are classifying one automation request.

User request:
{user_message}

Text extracted as possible outgoing content:
{extracted_message}

Destination tool:
{destination_tool}

Choose exactly one mode:

1. direct_send
Use this when the extracted text is already the final wording/message the user wants sent.

2. generate_then_send
Use this when the extracted text is not final wording, but a task/topic/request that AI should create into a useful final message before sending.

Important:
- Do not rely on specific topic keywords.
- Judge by meaning and wording.
- If the user clearly says exact/text/message/quote or gives quoted content, prefer direct_send.
- If unsure, choose direct_send to avoid changing the user's meaning.

Return exactly this JSON:
{{
  "mode": "direct_send or generate_then_send",
  "instruction": "If generate_then_send, write a clear instruction for the AI generator. If direct_send, empty string."
}}
""".strip()

    try:
        response = client.responses.create(
            model=PLANNER_MODEL,
            input=classifier_prompt,
            temperature=0,
            max_output_tokens=300,
        )

        parsed = extract_json(response.output_text)
        mode = safe_text(parsed.get("mode")).lower()
        instruction = clean_text(parsed.get("instruction"))

        if mode not in {"direct_send", "generate_then_send"}:
            return fallback

        return {
            "mode": mode,
            "instruction": instruction,
        }

    except Exception:
        return fallback
def is_send_me_generate_request(user_message: str, destination_tool: str = "") -> bool:
    """
    Generic structure-based rule.

    "send me X to Slack/Gmail" means:
        generate X first, then send generated content.

    This does not hardcode content words like weekend, plan, activity, project, etc.
    """
    text = safe_text(user_message).lower()

    if destination_tool not in {"slack", "gmail"}:
        return False

    return bool(re.search(r"\b(send|post|share|email|mail)\s+me\s+.+\s+(to|into|in|on)\s+", text))


def build_send_me_generate_instruction(user_message: str) -> str:
    """
    Convert:
        send me sunday midnight plan activity to my gmail

    Into:
        Create sunday midnight plan activity
    """
    text = safe_text(user_message)

    text = re.sub(
        r"^\s*(please\s+)?(send|post|share|email|mail)\s+me\s+",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"\s+(to|into|in|on)\s+(my\s+)?slack(\s+channel)?(\s+#[A-Za-z0-9_-]+|\s+[A-Za-z0-9_-]+)?\s*$",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"\s+(to|into|in|on)\s+(my\s+)?(gmail|email|mail)\s*$",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"\s+to\s+[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\s*$",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = safe_text(text)

    if not text:
        return "Create the requested content."

    return f"Create {text}"
def extract_email_address(user_message: str) -> str:
    match = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", user_message or "")
    return match.group(0) if match else ""


def extract_schedule_text(user_message: str) -> str:
    # ADDED: Small schedule extractor so simple schedules do not become missing fields.
    text = user_message.lower()

    if "every morning" in text:
        return "every morning"
    if "every evening" in text:
        return "every evening"
    if "every day" in text or "daily" in text:
        return "daily"
    if "weekly" in text or "every week" in text:
        return "weekly"
    if "monthly" in text or "every month" in text:
        return "monthly"

    match = re.search(r"every\s+([0-9]+)\s+(minute|minutes|hour|hours|day|days)", text)
    if match:
        return match.group(0)

    return ""


def extract_limit_from_text(user_message: str, tool_name: str = "") -> int:
    """
    ADDED:
    Extracts simple user-requested limits.

    Examples:
    - read 10 Gmail emails
    - latest 20 Slack messages
    - show 5 unread emails
    - read 12 Slack updates and 8 Gmail emails

    Returns 0 when no clear limit is found.
    """
    text = (user_message or "").lower()

    tool_keywords = {
        "slack": ["slack", "channel", "messages", "updates"],
        "gmail": ["gmail", "email", "emails", "mail", "inbox"],
    }

    # Tool-specific patterns:
    # - "10 gmail emails"
    # - "20 slack messages"
    # - "latest 5 gmail emails"
    if tool_name in tool_keywords:
        keywords = "|".join(tool_keywords[tool_name])

        patterns = [
            rf"\b([0-9]{{1,3}})\s+(?:latest\s+|recent\s+|unread\s+)?(?:{keywords})\b",
            rf"\b(?:latest|recent|last|read|show|display|get)\s+([0-9]{{1,3}})\s+(?:{keywords})\b",
            rf"\b(?:{keywords})\s+([0-9]{{1,3}})\b",
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    number = int(match.group(1))
                    if 0 < number <= 100:
                        return number
                except Exception:
                    pass

    # Generic fallback patterns:
    # - "read 10 messages"
    # - "latest 5 emails"
    generic_patterns = [
        r"\b(?:latest|recent|last|read|show|display|get)\s+([0-9]{1,3})\b",
        r"\b([0-9]{1,3})\s+(?:messages|updates|emails|mails)\b",
    ]

    for pattern in generic_patterns:
        match = re.search(pattern, text)
        if match:
            try:
                number = int(match.group(1))
                if 0 < number <= 100:
                    return number
            except Exception:
                pass

    return 0


def user_means_connected_user(user_message: str) -> bool:
    """
    CHANGED:
    Returns True only when the user explicitly names themselves as the
    destination.

    Important:
    - "from my Gmail" means Gmail is a source.
    - It must NOT become destination.recipient = connected_user.
    """
    text = (user_message or "").lower()

    explicit_self_destination_patterns = [
        r"\bto\s+me\b",
        r"\bto\s+myself\b",
        r"\bmyself\b",
        r"\bsend\s+it\s+to\s+me\b",
        r"\bsend\s+to\s+me\b",
        r"\bto\s+my\s+email\b",
        r"\bto\s+my\s+gmail\b",
        r"\bemail\s+me\b",
    ]

    return any(re.search(pattern, text) for pattern in explicit_self_destination_patterns)


def normalize_slack_channel_target(channel_value: str) -> str:
    """
    ADDED:
    Normalizes a Slack channel target.

    Examples:
    - "new-channel" -> "#new-channel"
    - "#new-channel" -> "#new-channel"
    """
    channel_value = safe_text(channel_value)

    if not channel_value:
        return ""

    if channel_value.startswith("#"):
        return channel_value

    return "#" + channel_value


def detect_explicit_destination(user_message: str) -> Dict[str, str]:
    """
    ADDED:
    Detects destination from the user's wording using generic direction rules.

    This is intentionally deterministic because the model may confuse:
    "from Gmail to Slack" as Gmail send.

    Returns:
    {
      "tool": "slack" | "gmail" | "chat" | "",
      "operation": "send" | "return" | "",
      "target": "",
      "recipient": "",
      "reason": ""
    }
    """
    text_original = user_message or ""
    text = text_original.lower()

    result = {
        "tool": "",
        "operation": "",
        "target": "",
        "recipient": "",
        "reason": "",
    }

    email_address = extract_email_address(text_original)
    slack_channel = extract_slack_channel(text_original)

    display_here = contains_any(text, [
        "display here",
        "show here",
        "return here",
        "display in chat",
        "show in chat",
        "return in chat",
        "display in ui",
        "show in ui",
    ])

    if display_here:
        result.update({
            "tool": "chat",
            "operation": "return",
            "reason": "explicit_chat_destination",
        })
        return result

    # Slack destination.
    # Covers:
    # - send to Slack
    # - post to Slack channel new-channel
    # - to my Slack channel #new-channel
    # - to #new-channel
    slack_destination_patterns = [
        r"\b(to|into|in|on)\s+(?:my\s+)?slack\b",
        r"\b(to|into|in|on)\s+(?:my\s+)?slack\s+channel\b",
        r"\b(post|send|share|forward)\b.*\bslack\b",
        r"\b(post|send|share|forward)\b.*\bchannel\s+[A-Za-z0-9_-]+\b",
        r"\b(to|into|in|on)\s+#[A-Za-z0-9_-]+\b",
    ]

    if any(re.search(pattern, text) for pattern in slack_destination_patterns):
        result.update({
            "tool": "slack",
            "operation": "send",
            "target": slack_channel,
            "reason": "explicit_slack_destination",
        })
        return result

    # Gmail/email destination.
    # Covers:
    # - send email to user@example.com
    # - email them to Ali
    # - send to my Gmail / email me
    gmail_destination_patterns = [
        r"\bemail\s+(them|it|this|summary|update)?\s*(to)?\b",
        r"\b(send|share|forward)\b.*\bto\s+[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        r"\b(send|share|forward)\b.*\b(to\s+my\s+gmail|to\s+my\s+email|to\s+me|to\s+myself)\b",
        r"\b(to\s+gmail|to\s+email|to\s+mail)\b",
    ]

    if email_address or any(re.search(pattern, text) for pattern in gmail_destination_patterns):
        result.update({
            "tool": "gmail",
            "operation": "send",
            "recipient": email_address,
            "reason": "explicit_gmail_destination",
        })

        if not email_address and user_means_connected_user(user_message):
            result["recipient"] = "connected_user"

        return result

    return result


def has_send_intent_without_clear_destination(user_message: str, explicit_destination: Dict[str, str]) -> bool:
    """
    ADDED:
    Detects send/post/share intent when the destination is missing.

    Example:
    - "send top 5 unread Gmail emails" -> missing destination
    - "read top 5 unread Gmail emails" -> destination can be chat
    """
    text = (user_message or "").lower()

    if explicit_destination.get("tool"):
        return False

    send_words = ["send", "post", "share", "forward"]
    has_send_word = any(re.search(rf"\b{word}\b", text) for word in send_words)

    if not has_send_word:
        return False

    # "send email to abc@gmail.com" is handled by explicit destination.
    # If we reach here, no user-facing destination was found.
    return True


# -----------------------------
# Normalization helpers
# -----------------------------

def normalize_tool(value: Any, default: str = "none") -> str:
    tool = safe_text(value).lower()
    return tool if tool in ALLOWED_TOOLS else default


def normalize_operation(value: Any, default: str = "none") -> str:
    operation = safe_text(value).lower()
    return operation if operation in ALLOWED_OPERATIONS else default


def normalize_trigger_type(value: Any, default: str = "manual") -> str:
    trigger_type = safe_text(value).lower()
    return trigger_type if trigger_type in ALLOWED_TRIGGER_TYPES else default


def normalize_source_object(source: Any) -> Dict[str, Any]:
    # ADDED: Clean one source object. This is used for every item in sources[].
    if not isinstance(source, dict):
        source = {}

    filters = source.get("filters", {})
    if not isinstance(filters, dict):
        filters = {}

    normalized = {
        "tool": normalize_tool(source.get("tool"), "none"),
        "operation": normalize_operation(source.get("operation"), "none"),
        "target": clean_text(source.get("target")),
        "query": clean_text(source.get("query")),
        "filters": filters,
    }

    # ADDED:
    # Preserve planner/model limit if present.
    try:
        limit_value = int(source.get("limit", 0))
        if 0 < limit_value <= 100:
            normalized["limit"] = limit_value
    except Exception:
        pass

    return normalized


def upsert_source(sources: List[Dict[str, Any]], new_source: Dict[str, Any]) -> List[Dict[str, Any]]:
    # ADDED: Add source if missing; update existing matching tool/target if already present.
    normalized = normalize_source_object(new_source)

    if normalized["tool"] == "none":
        return sources

    for existing in sources:
        same_tool = existing.get("tool") == normalized["tool"]
        same_target = existing.get("target", "") == normalized.get("target", "")
        target_missing = not existing.get("target") or not normalized.get("target")

        if same_tool and (same_target or target_missing):
            if normalized.get("operation") != "none":
                existing["operation"] = normalized["operation"]
            if normalized.get("target"):
                existing["target"] = normalized["target"]
            if normalized.get("query"):
                existing["query"] = normalized["query"]
            if normalized.get("filters"):
                existing_filters = existing.get("filters", {}) if isinstance(existing.get("filters"), dict) else {}
                existing_filters.update(normalized["filters"])
                existing["filters"] = existing_filters

            # ADDED:
            # Preserve source limit when updating existing source.
            if normalized.get("limit"):
                existing["limit"] = normalized["limit"]

            return sources

    sources.append(normalized)
    return sources


# -----------------------------
# ADDED: source limit application
# -----------------------------

def apply_limits_to_sources(sources: List[Dict[str, Any]], user_message: str) -> List[Dict[str, Any]]:
    """
    ADDED:
    Applies dynamic user-requested limits to Slack/Gmail sources.

    This does not force a limit if user did not mention one.
    Builder will use its own default only when no limit exists.
    """
    if not isinstance(sources, list):
        return []

    for source in sources:
        if not isinstance(source, dict):
            continue

        if source.get("limit"):
            continue

        tool = safe_text(source.get("tool")).lower()

        if tool in {"slack", "gmail"}:
            limit = extract_limit_from_text(user_message, tool)
            if limit:
                source["limit"] = limit

    return sources

def extract_all_email_addresses(text: str) -> List[str]:
    """
    ADDED:
    Extract all email addresses from the message.

    Why:
    Sometimes app.py may replace "my email" with the logged-in Gmail,
    and the user may also type another recipient email.

    Example:
        send a plan to my email gmailautomationtest2026@gmail.com

    Can become:
        send a plan to vijji.m18@gmail.com gmailautomationtest2026@gmail.com

    In that case, we want the explicit typed recipient email,
    which is usually the last email in the message.
    """
    if not text:
        return []

    matches = re.findall(
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        str(text)
    )

    cleaned = []
    for email in matches:
        email = safe_text(email).lower()
        if email and email not in cleaned:
            cleaned.append(email)

    return cleaned


def get_preferred_email_recipient(user_message: str) -> str:
    """
    ADDED:
    Prefer the most explicit recipient email.

    If multiple emails exist, use the last one.

    This protects cases where the logged-in Gmail appears first
    and the user-typed recipient appears after it.
    """
    emails = extract_all_email_addresses(user_message)

    if not emails:
        return ""

    return emails[-1]
# -----------------------------
# Normalization and safety
# -----------------------------

def normalize_plan(plan: Dict[str, Any], user_message: str) -> Dict[str, Any]:
    base = empty_plan()

    if not isinstance(plan, dict):
        plan = {}

    # Top-level values
    decision = safe_text(plan.get("decision"))
    base["decision"] = decision if decision in ALLOWED_DECISIONS else "direct_answer"
    base["intent"] = clean_text(plan.get("intent"))
    base["summary"] = clean_text(plan.get("summary"))
    base["direct_answer"] = clean_text(plan.get("direct_answer"))
    base["follow_up_question"] = clean_text(plan.get("follow_up_question"))

    # CHANGED: Normalize sources[] only. No old single source is used.
    raw_sources = plan.get("sources", [])
    if not isinstance(raw_sources, list):
        raw_sources = []

    normalized_sources: List[Dict[str, Any]] = []
    for item in raw_sources:
        source_obj = normalize_source_object(item)
        if source_obj["tool"] != "none":
            normalized_sources = upsert_source(normalized_sources, source_obj)

    # ADDED:
    # Apply user-requested read limits after source normalization.
    base["sources"] = apply_limits_to_sources(normalized_sources, user_message)

    # Processing
    processing = plan.get("processing", {}) if isinstance(plan.get("processing"), dict) else {}
    required = processing.get("required", False)
    base["processing"] = {
        "required": bool(required),
        "operation": normalize_operation(processing.get("operation"), "none"),
        "instruction": clean_text(processing.get("instruction")),
    }

    # Destination
    destination = plan.get("destination", {}) if isinstance(plan.get("destination"), dict) else {}
    base["destination"] = {
        "tool": normalize_tool(destination.get("tool"), "chat"),
        "operation": normalize_operation(destination.get("operation"), "return"),
        "target": clean_text(destination.get("target")),
        "recipient": clean_text(destination.get("recipient")),
        # ADDED:
        # Preserve direct-send content from model output when present.
        "message": clean_text(destination.get("message")),
        "query": clean_text(destination.get("query")),
    }

    # Trigger
    trigger = plan.get("trigger", {}) if isinstance(plan.get("trigger"), dict) else {}
    base["trigger"] = {
        "type": normalize_trigger_type(trigger.get("type"), "manual"),
        "value": clean_text(trigger.get("value")),
    }

    # Condition
    condition = plan.get("condition", {}) if isinstance(plan.get("condition"), dict) else {}
    base["condition"] = {
        "required": bool(condition.get("required", False)),
        "field": clean_text(condition.get("field")),
        "operator": clean_text(condition.get("operator")),
        "value": clean_text(condition.get("value")),
        "unit": clean_text(condition.get("unit")),
    }

    # Lists
    missing_fields = plan.get("missing_fields", [])
    if not isinstance(missing_fields, list):
        missing_fields = []
    base["missing_fields"] = unique_list([clean_text(x) for x in missing_fields])

    requires_connection = plan.get("requires_connection", [])
    if not isinstance(requires_connection, list):
        requires_connection = []
    base["requires_connection"] = unique_list([
        tool for tool in [normalize_tool(x, "none") for x in requires_connection]
        if tool in {"gmail", "slack"}
    ])

    # Deterministic fixes from user message
    text = user_message.lower()
    slack_channel = extract_slack_channel(user_message)

    # CHANGED:
    # Prefer explicit typed recipient when multiple emails are present.
    # Example:
    #   prepared message may contain:
    #   vijji.m18@gmail.com gmailautomationtest2026@gmail.com
    # We want the last typed destination email.
    email_address = get_preferred_email_recipient(user_message) or extract_email_address(user_message)

    schedule_text = extract_schedule_text(user_message)

    # ADDED:
    # Explicit destination extraction is used as a final guard against
    # wrong direction plans such as:
    # "from Gmail to Slack" becoming Gmail send.
    explicit_destination = detect_explicit_destination(user_message)

    # ADDED:
    # Protect normal knowledge questions from deterministic Slack/Gmail workflow rules.
    # Example: "what is slack" should stay direct_answer and should NOT require Slack login.
    plain_knowledge_question = is_plain_knowledge_question(user_message)

    # ADDED: Detect destination = chat when user says display/show/return here.
    if contains_any(text, ["display here", "show here", "return here", "display in ui", "show in ui"]):
        base["destination"] = {
            "tool": "chat",
            "operation": "return",
            "target": "",
            "recipient": "",
            "message": "",
            "query": "",
        }

    # CHANGED:
    # Slack workflow rules should run only for real Slack actions.
    # This prevents "what is Slack" from becoming a Slack workflow.
    if not plain_knowledge_question and ("slack" in text or slack_channel):
        if "slack" not in base["requires_connection"]:
            base["requires_connection"].append("slack")

        slack_read = contains_any(text, [
            "summarize slack",
            "read slack",
            "search slack",
            "check slack",
            "slack updates",
            "slack messages",
            "from slack",
        ])

        slack_send = contains_any(text, [
            "send to slack",
            "post to slack",
            "slack channel",
            "send it to slack",
            "alert in slack",
            "alert on slack",
            "notify in slack",
            "notify on slack",
        ])

        if slack_read:
            base["sources"] = upsert_source(base["sources"], {
                "tool": "slack",
                "operation": "read",
                "target": slack_channel,
                "query": "updates" if "updates" in text else "latest messages",
                "filters": {},
            })

        if slack_send:
            base["destination"]["tool"] = "slack"
            base["destination"]["operation"] = "send"
            if slack_channel:
                base["destination"]["target"] = slack_channel

    # CHANGED:
    # Gmail/email workflow rules should run only for real Gmail/email actions.
    # This prevents "what is Gmail" from becoming a Gmail workflow.
    if not plain_knowledge_question and contains_any(text, ["gmail", "email", "mail"]):
        if "gmail" not in base["requires_connection"]:
            base["requires_connection"].append("gmail")

        gmail_read = contains_any(text, [
            "summarize my latest gmail",
            "read gmail",
            "search gmail",
            "check gmail",
            "latest gmail emails",
            "gmail emails",
            "unread email",
            "unread gmail",
            "read unread",
        ])

        gmail_send = contains_any(text, [
            "send email",
            "email them",
            "email it",
            "email to",
            "send gmail",
            "mail to",
            "send it to my gmail",
            "send to my gmail",
        ])

        if gmail_read:
            filters = {}
            query = "latest emails"
            if contains_any(text, ["unread email", "unread gmail", "read unread"]):
                filters["status"] = "unread"
                query = "unread emails"

            base["sources"] = upsert_source(base["sources"], {
                "tool": "gmail",
                "operation": "read",
                "target": "inbox",
                "query": query,
                "filters": filters,
            })

        if gmail_send:
            base["destination"]["tool"] = "gmail"
            base["destination"]["operation"] = "send"

            # CHANGED:
            # Explicit email must win before connected_user.
            if email_address:
                base["destination"]["recipient"] = email_address
            elif user_means_connected_user(user_message):
                base["destination"]["recipient"] = "connected_user"

    # ADDED:
    # Final deterministic destination override.
    #
    # Why:
    # The model can sometimes confuse direction:
    #   "from my Gmail to my Slack channel new-channel"
    # should be:
    #   source = Gmail, destination = Slack
    # not:
    #   destination = Gmail
    #
    # This rule is generic direction handling and does not hardcode one flow.
    # It applies to Gmail -> Slack, Slack -> Gmail, and future tool directions
    # as long as the tool is in the supported registry.
    if not plain_knowledge_question and explicit_destination.get("tool"):
        destination_tool = normalize_tool(explicit_destination.get("tool"), "")

        if destination_tool:
            base["destination"]["tool"] = destination_tool
            base["destination"]["operation"] = normalize_operation(
                explicit_destination.get("operation"),
                "send" if destination_tool != "chat" else "return"
            )

            if destination_tool == "slack":
                target = safe_text(explicit_destination.get("target"))
                if target:
                    base["destination"]["target"] = normalize_slack_channel_target(target)
                elif slack_channel:
                    base["destination"]["target"] = normalize_slack_channel_target(slack_channel)

            elif destination_tool == "gmail":
                recipient = safe_text(explicit_destination.get("recipient"))
                if recipient:
                    base["destination"]["recipient"] = recipient

            elif destination_tool == "chat":
                base["destination"]["target"] = ""
                base["destination"]["recipient"] = ""

    # ADDED:
    # Preserve the actual direct-send payload for Slack/Gmail destinations.
    # Example:
    #   "send hello from dynamic workflow to Slack #new-channel"
    # Planner/model may put the text only in summary and leave destination.message empty.
    # This deterministic extraction prevents Slack/Gmail from receiving fallback text.
    if not plain_knowledge_question and base["destination"].get("tool") in {"slack", "gmail"}:
        direct_message = extract_direct_destination_message(
            user_message,
            destination_tool=safe_text(base["destination"].get("tool"))
        )
        if direct_message and not base["destination"].get("message"):
            base["destination"]["message"] = direct_message
        if direct_message and not base["destination"].get("query"):
            base["destination"]["query"] = direct_message

    # ADDED:
    # Generic structure-based generate-then-send rule.
    #
    # This avoids topic keyword hardcoding. It only checks the user's structure:
    #   "send me X to Slack/Gmail"
    # Meaning:
    #   create/generate X first, then send the generated output.
    #
    # Direct messages still stay direct:
    #   "send hello to Slack #new-channel" -> Slack send only
    if (
        not plain_knowledge_question
        and not base["sources"]
        and base["destination"].get("tool") in {"slack", "gmail"}
        and base["destination"].get("operation") in {"send", "create"}
        and not base["processing"].get("required")
        and is_send_me_generate_request(
            user_message,
            destination_tool=safe_text(base["destination"].get("tool"))
        )
    ):
        base["processing"]["required"] = True
        base["processing"]["operation"] = "generate"
        base["processing"]["instruction"] = build_send_me_generate_instruction(user_message)

        # Do not send the raw phrase directly. The composer/builder should route
        # the generated LLM output into Slack/Gmail.
        base["destination"]["message"] = ""
        base["destination"]["query"] = ""

    # ADDED:
    # If the model produced Gmail connected_user only because the user said
    # "from my Gmail", remove that fake recipient unless the user explicitly
    # asked to send to themselves.
    if (
        not user_means_connected_user(user_message)
        and base["destination"]["tool"] == "gmail"
        and base["destination"].get("recipient") == "connected_user"
    ):
        base["destination"]["recipient"] = ""

    # ADDED:
    # Final Gmail recipient safety guard.
    # If user typed an explicit email address, it must win over logged-in Gmail.
    # This is important when app.py preprocessing inserts current user's Gmail
    # before the typed recipient.
    explicit_email_recipient = get_preferred_email_recipient(user_message)

    if (
        not plain_knowledge_question
        and base["destination"].get("tool") == "gmail"
        and base["destination"].get("operation") in {"send", "create"}
        and explicit_email_recipient
    ):
        base["destination"]["recipient"] = explicit_email_recipient

    # ADDED:
    # If the user uses send/post/share/forward but no clear destination was
    # detected, ask a follow-up instead of guessing Gmail or Slack.
    missing_destination = has_send_intent_without_clear_destination(
        user_message,
        explicit_destination
    )

    # ADDED:
    # Final safety guard for plain knowledge questions.
    # If the model accidentally returned Slack/Gmail connection fields for a question like
    # "what is Slack", remove them before workflow decision is calculated.
    if plain_knowledge_question:
        base["sources"] = []
        base["destination"] = {
            "tool": "chat",
            "operation": "return",
            "target": "",
            "recipient": "",
            "message": "",
            "query": "",
        }
        base["requires_connection"] = []
        base["missing_fields"] = []
        base["trigger"] = {
            "type": "manual",
            "value": "",
        }
        base["condition"] = {
            "required": False,
            "field": "",
            "operator": "",
            "value": "",
            "unit": "",
        }

    # ADDED: Web/live lookup becomes a source in sources[].
    if contains_any(text, ["latest", "search web", "look up", "lookup", "find online", "news", "weather", "temperature", "stock", "currency"]):
        has_app_source = any(source["tool"] in {"gmail", "slack"} for source in base["sources"])
        if not has_app_source and not contains_any(text, ["gmail", "slack"]):
            base["sources"] = upsert_source(base["sources"], {
                "tool": "web",
                "operation": "search",
                "target": "",
                "query": user_message,
                "filters": {},
            })

    # Processing hints
    if contains_any(text, ["summarize", "summary"]):
        base["processing"]["required"] = True
        base["processing"]["operation"] = "summarize"
        if not base["processing"]["instruction"]:
            base["processing"]["instruction"] = user_message

    if contains_any(text, ["create", "generate", "write", "draft", "prepare", "compose"]):
        base["processing"]["required"] = True
        if base["processing"]["operation"] == "none":
            base["processing"]["operation"] = "generate"
        if not base["processing"]["instruction"]:
            base["processing"]["instruction"] = user_message

    if contains_any(text, ["extract"]):
        base["processing"]["required"] = True
        base["processing"]["operation"] = "extract"
        if not base["processing"]["instruction"]:
            base["processing"]["instruction"] = user_message

    if contains_any(text, ["classify"]):
        base["processing"]["required"] = True
        base["processing"]["operation"] = "classify"
        if not base["processing"]["instruction"]:
            base["processing"]["instruction"] = user_message

    # Trigger/schedule hints
    if contains_any(text, ["every", "daily", "weekly", "monthly", "schedule", "recurring"]):
        base["trigger"]["type"] = "schedule"
        if schedule_text:
            base["trigger"]["value"] = schedule_text

    if contains_any(text, ["webhook"]):
        base["trigger"]["type"] = "webhook"

    if contains_any(text, ["when email arrives", "new email", "when message arrives", "new slack message"]):
        base["trigger"]["type"] = "event"

    # Alert/condition hints
    if contains_any(text, ["alert", "notify me when", "when", "if"]):
        if contains_any(text, ["below", "drops below", "less than"]):
            base["condition"]["required"] = True
            base["condition"]["operator"] = "below"
        elif contains_any(text, ["above", "greater than", "more than"]):
            base["condition"]["required"] = True
            base["condition"]["operator"] = "above"
        elif contains_any(text, ["equals", "equal to"]):
            base["condition"]["required"] = True
            base["condition"]["operator"] = "equals"

    # ADDED:
    # Apply limits again after deterministic Slack/Gmail source insertion.
    # This handles sources created by rules below, not only sources returned by the model.
    base["sources"] = apply_limits_to_sources(base["sources"], user_message)

    # Missing field inference
    missing = list(base["missing_fields"])

    # ADDED:
    # Do not guess the destination for send/post/share/forward requests.
    if not plain_knowledge_question and missing_destination:
        missing.append("destination")

    # Slack source/destination needs channel/user.
    for source in base["sources"]:
        if source["tool"] == "slack" and not source["target"]:
            missing.append("slack_channel")

    if base["destination"]["tool"] == "slack" and not base["destination"]["target"] and not base["destination"]["recipient"]:
        missing.append("slack_channel")

    # Gmail send needs recipient email unless connected_user is explicitly requested.
    if base["destination"]["tool"] == "gmail":
        recipient = base["destination"]["recipient"]
        if recipient != "connected_user" and "@" not in recipient:
            missing.append("recipient_email")

    # Schedule trigger needs schedule value.
    if base["trigger"]["type"] == "schedule" and not base["trigger"]["value"]:
        missing.append("schedule")

    # Alert needs condition if condition not captured.
    if contains_any(text, ["alert", "notify me when"]) and not base["condition"]["required"]:
        missing.append("condition")

    # Remove technical/non-user-facing missing fields.
    cleaned_missing = []
    for field in missing:
        field_text = safe_text(field)
        field_lower = field_text.lower()
        if not field_text:
            continue
        if any(blocked in field_lower for blocked in TECHNICAL_BLOCKED_WORDS):
            continue
        if field_lower in {"api_key", "oauth", "token", "credentials", "n8n_node", "backend_config"}:
            continue
        cleaned_missing.append(field_text)
    base["missing_fields"] = unique_list(cleaned_missing)

    # ADDED:
    # Normalize Slack destination channel even when it came from the model.
    # User should not be forced to type "#".
    if base["destination"]["tool"] == "slack" and base["destination"].get("target"):
        base["destination"]["target"] = normalize_slack_channel_target(
            base["destination"]["target"]
        )

    # Required connections must be based on all sources + destination.
    for source in base["sources"]:
        if source["tool"] in {"gmail", "slack"} and source["tool"] not in base["requires_connection"]:
            base["requires_connection"].append(source["tool"])

    if base["destination"]["tool"] in {"gmail", "slack"} and base["destination"]["tool"] not in base["requires_connection"]:
        base["requires_connection"].append(base["destination"]["tool"])

    # Decision finalization
    needs_workflow = (
        any(source["tool"] in {"gmail", "slack", "web", "file"} for source in base["sources"])
        or base["destination"]["tool"] in {"gmail", "slack", "file"}
        or base["trigger"]["type"] in {"schedule", "webhook", "event"}
        or bool(base["requires_connection"])
    )

    if base["missing_fields"]:
        base["decision"] = "follow_up"
        base["direct_answer"] = ""
        if not base["follow_up_question"]:
            base["follow_up_question"] = build_follow_up_question(base["missing_fields"], base)
    elif needs_workflow:
        base["decision"] = "workflow_required"
        base["follow_up_question"] = ""
        base["direct_answer"] = ""
    else:
        base["decision"] = "direct_answer"
        base["sources"] = []
        base["destination"] = empty_plan()["destination"]
        base["requires_connection"] = []
        base["follow_up_question"] = ""

    # For workflow to UI/chat, destination must be chat return.
    if base["decision"] == "workflow_required" and base["destination"]["tool"] in {"none", ""}:
        base["destination"] = {
            "tool": "chat",
            "operation": "return",
            "target": "",
            "recipient": "",
            "message": "",
            "query": "",
        }

    base["requires_connection"] = unique_list(base["requires_connection"])
    return base
def build_follow_up_question(missing_fields: List[str], plan: Dict[str, Any]) -> str:
    if not missing_fields:
        return ""

    first = missing_fields[0]

    if first == "slack_channel":
        return "Which Slack channel or user should I use?"

    if first == "recipient_email":
        return "What email address should I send it to?"

    if first == "schedule":
        return "How often should this run?"

    if first == "condition":
        return "What condition should trigger this alert?"

    if first == "destination":
        return "Where should I send it — Slack, Gmail/email, or chat?"

    if first == "source_file":
        return "Which file should I use?"

    return f"Please provide the missing detail: {first}."


# -----------------------------
# Public planner function
# -----------------------------

def fallback_plan(user_message: str, error_message: str = "") -> Dict[str, Any]:
    plan = empty_plan()
    plan["decision"] = "direct_answer"
    plan["intent"] = "fallback_answer"
    plan["summary"] = "Planner failed, returned safe fallback"
    plan["direct_answer"] = f"I understood your request: {user_message}"
    if error_message:
        plan["debug_error"] = error_message
    return plan


def plan_user_request(user_message: str) -> Dict[str, Any]:
    if not safe_text(user_message):
        plan = empty_plan()
        plan["decision"] = "follow_up"
        plan["missing_fields"] = ["user_message"]
        plan["follow_up_question"] = "What would you like me to do?"

        # ADDED: Log empty-input follow-up result for UI debugging.
        write_planner_log(user_message, plan)
        return plan

    prompt = build_prompt(user_message)

    try:
        response = client.responses.create(
            model=PLANNER_MODEL,
            input=prompt,
            temperature=TEMPERATURE,
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
        raw_text = response.output_text
    except Exception as exc:
        plan = fallback_plan(user_message, str(exc))

        # ADDED: Log API/model errors as fallback planner output.
        write_planner_log(user_message, plan)
        return plan

    parsed = extract_json(raw_text)
    if not parsed:
        plan = fallback_plan(user_message, "Model returned invalid JSON")

        # ADDED: Log invalid JSON fallback and raw model text for debugging.
        write_planner_log(user_message, plan, raw_model_output=raw_text)
        return plan

    final_plan = normalize_plan(parsed, user_message)

    # ADDED: Log final normalized planner output.
    # This is the exact structure resolver/composer/UI should use later.
    write_planner_log(user_message, final_plan, raw_model_output=raw_text)

    return final_plan


# -----------------------------
# Manual test
# -----------------------------

if __name__ == "__main__":
    user_input = input("USER: ").strip()

    if not user_input:
        print("No input provided.")
    else:
        result = plan_user_request(user_input)
        print(json.dumps(result, indent=2, ensure_ascii=False))
