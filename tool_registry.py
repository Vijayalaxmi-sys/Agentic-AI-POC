"""
tool_registry.py

Defines what tools this application supports.

This file does NOT:
- check login
- call Slack/Gmail
- call n8n
- execute anything

It only answers:
- Is this tool supported?
- Can this tool be used as source?
- Can this tool be used as destination?
- Which operations are allowed for source/destination?
- Does this tool require connection?
"""


TOOLS = {
    "slack": {
        "display_name": "Slack",
        "supports_source": True,
        "supports_destination": True,

        # Slack as source: reading/searching channel messages
        "source_operations": ["read", "search"],

        # Slack as destination: sending/posting messages
        "destination_operations": ["send"],

        "requires_connection": True,
        "connect_url": "/connect/slack",
    },

    "gmail": {
        "display_name": "Gmail",
        "supports_source": True,
        "supports_destination": True,

        # Gmail as source: reading/searching emails
        "source_operations": ["read", "search"],

        # Gmail as destination: sending email or creating draft later
        "destination_operations": ["send", "create"],

        "requires_connection": True,
        "connect_url": "/connect/gmail",
    },

    "chat": {
        "display_name": "Chat UI",
        "supports_source": False,
        "supports_destination": True,

        "source_operations": [],
        "destination_operations": ["return"],

        "requires_connection": False,
        "connect_url": "",
    },

    "web": {
        "display_name": "Web",
        "supports_source": True,
        "supports_destination": False,

        "source_operations": ["search", "read"],
        "destination_operations": [],

        "requires_connection": False,
        "connect_url": "",
    },

    "file": {
        "display_name": "File",
        "supports_source": True,
        "supports_destination": True,

        "source_operations": ["read"],
        "destination_operations": ["write", "create"],

        "requires_connection": False,
        "connect_url": "",
    },
}


def get_tool(tool_name):
    return TOOLS.get(tool_name, {})


def is_supported_tool(tool_name):
    return tool_name in TOOLS


def requires_connection(tool_name):
    tool = get_tool(tool_name)
    return bool(tool.get("requires_connection", False))


def get_connect_url(tool_name):
    tool = get_tool(tool_name)
    return tool.get("connect_url", "")


def supports_source(tool_name):
    tool = get_tool(tool_name)
    return bool(tool.get("supports_source", False))


def supports_destination(tool_name):
    tool = get_tool(tool_name)
    return bool(tool.get("supports_destination", False))


def is_source_operation_allowed(tool_name, operation):
    tool = get_tool(tool_name)
    return operation in tool.get("source_operations", [])


def is_destination_operation_allowed(tool_name, operation):
    tool = get_tool(tool_name)
    return operation in tool.get("destination_operations", [])


def list_tools():
    return TOOLS


if __name__ == "__main__":
    import json
    print(json.dumps(list_tools(), indent=2))