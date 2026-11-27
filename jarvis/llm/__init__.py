"""LLM module containing multi-provider abstractions, automatic tool schemas, and fallbacks."""

from jarvis.llm.base import BaseLLMProvider, ProviderResponse, ToolCall
from jarvis.llm.fallback import FallbackManager
from jarvis.llm.schema import function_to_tool_schema, generate_tools_schema
from jarvis.llm.history import NeutralHistoryManager

__all__ = [
    "BaseLLMProvider",
    "ProviderResponse",
    "ToolCall",
    "FallbackManager",
    "function_to_tool_schema",
    "generate_tools_schema",
    "NeutralHistoryManager",
]
