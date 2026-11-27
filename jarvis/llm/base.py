"""Base abstractions for LLM providers, responses, and tool calls."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class ToolCall:
    """Represents a standardized tool execution request from any LLM provider."""
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class ProviderResponse:
    """Standardized response from any LLM provider."""
    content: Optional[str] = None
    tool_calls: List[ToolCall] = field(default_factory=list)
    raw_response: Any = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    finish_reason: Optional[str] = None


class BaseLLMProvider(ABC):
    """Abstract base class for all LLM backend providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the provider (e.g. 'groq', 'ollama', 'gemini')."""
        pass

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Model identifier currently configured."""
        pass

    @abstractmethod
    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> ProviderResponse:
        """
        Sends conversation messages and tool schemas to the model.
        Returns a standardized ProviderResponse with text content and/or tool calls.
        """
        pass

    @abstractmethod
    def list_models(self) -> List[str]:
        """Queries and returns list of available models for this provider."""
        pass

    @abstractmethod
    def is_available(self) -> Tuple[bool, str]:
        """
        Checks connectivity and configuration readiness.
        Returns (is_ready, status_or_error_message).
        """
        pass
