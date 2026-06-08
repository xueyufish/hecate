"""Agent tools for L4 knowledge memory interaction.

Defines JSON Schema tool definitions for knowledge_insert and knowledge_search,
enabling agents to actively store and retrieve long-term knowledge.
"""

from __future__ import annotations

KNOWLEDGE_INSERT_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "knowledge_insert",
        "description": (
            "Store a piece of knowledge for long-term retrieval. "
            "Use this when you learn something worth remembering across conversations."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The knowledge fact to store",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags for categorization",
                },
                "importance": {
                    "type": "number",
                    "description": "Importance score 0.0-1.0, default 0.5",
                },
            },
            "required": ["content"],
        },
    },
}

KNOWLEDGE_SEARCH_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "knowledge_search",
        "description": ("Search your long-term knowledge archive for relevant information."),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Max results, default 5",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tag filter",
                },
            },
            "required": ["query"],
        },
    },
}

KNOWLEDGE_TOOLS: list[dict] = [KNOWLEDGE_INSERT_TOOL, KNOWLEDGE_SEARCH_TOOL]
