"""Function-tool definitions shared by the text chat and the voice (Realtime) sessions."""

from typing import Any


def function_tool(name: str, description: str, properties: dict[str, Any]) -> dict[str, Any]:
    """A function tool in the Responses API's strict format: every property required."""
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
        "strict": True,
    }


def realtime_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The same tools in the Realtime API's format, which has no 'strict' field."""
    return [{key: value for key, value in tool.items() if key != "strict"} for tool in tools]
