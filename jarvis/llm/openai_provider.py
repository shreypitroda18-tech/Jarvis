"""OpenAI-compatible LLM provider implementing Groq and Ollama backends."""

import json
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional, Tuple
from openai import OpenAI, APIError, APIStatusError, APIConnectionError, RateLimitError, InternalServerError

from jarvis.config import config, logger
from jarvis.llm.base import BaseLLMProvider, ProviderResponse, ToolCall


class OpenAICompatibleProvider(BaseLLMProvider):
    """Handles both Groq and Ollama via the OpenAI API protocol."""

    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str,
        model_name: str,
        extra_options: Optional[Dict[str, Any]] = None,
    ):
        self._name = name.lower()
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key or "not-needed"
        self._model_name = model_name
        self._extra_options = extra_options or {}
        self._client: Optional[OpenAI] = None
        self._init_client()

    def _init_client(self) -> None:
        """Initializes the OpenAI client instance."""
        try:
            self._client = OpenAI(
                base_url=self._base_url,
                api_key=self._api_key,
                timeout=30.0,
            )
        except Exception as e:
            logger.error(f"Failed to create OpenAI client for {self._name}: {e}")
            self._client = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def model_name(self) -> str:
        return self._model_name

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_completion_tokens: Optional[int] = None,
    ) -> ProviderResponse:
        """
        Executes a completion request with tools over the OpenAI-compatible API.
        """
        if not self._client:
            self._init_client()
            if not self._client:
                raise APIConnectionError(f"Client for provider {self._name} is not initialized.")

        temp = temperature if temperature is not None else 0.7
        kwargs: Dict[str, Any] = {
            "model": self._model_name,
            "messages": messages,
            "temperature": temp,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        # Reasoning models support (ensure sufficient completion tokens for reasoning chain)
        is_reasoning_model = any(k in self._model_name.lower() for k in ("gpt-oss", "r1", "o1", "o3", "reasoning"))
        if is_reasoning_model or max_completion_tokens is not None:
            kwargs["max_completion_tokens"] = max(1024, max_completion_tokens or 1024)

        # Apply provider-specific optimizations (Ollama 8192 context, keep_alive)
        if self._name == "ollama":
            kwargs["extra_body"] = {
                "options": {
                    "num_ctx": config.llm.ollama_context_tokens,
                },
                "keep_alive": config.llm.ollama_keep_alive,
            }

        response = self._client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        message = choice.message
        finish_reason = getattr(choice, "finish_reason", None)

        usage = getattr(response, "usage", None)
        p_tokens = getattr(usage, "prompt_tokens", None) if usage else None
        c_tokens = getattr(usage, "completion_tokens", None) if usage else None
        t_tokens = getattr(usage, "total_tokens", None) if usage else None

        # Detect empty response caused by reasoning length exhaustion
        if finish_reason == "length" and not message.content and not message.tool_calls:
            raise APIStatusError(
                message="Model reached completion token limit during reasoning (finish_reason=length).",
                response=response,
                body={"error": {"message": "Completion token limit reached during reasoning", "code": "reasoning_length_limit"}}
            )

        parsed_tool_calls: List[ToolCall] = []
        if message.tool_calls:
            for tc in message.tool_calls:
                fn_name = tc.function.name
                raw_args = tc.function.arguments
                try:
                    args_dict = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except Exception:
                    args_dict = {}
                parsed_tool_calls.append(
                    ToolCall(id=tc.id or f"call_{fn_name}", name=fn_name, arguments=args_dict)
                )

        return ProviderResponse(
            content=message.content,
            tool_calls=parsed_tool_calls,
            raw_response=response,
            prompt_tokens=p_tokens,
            completion_tokens=c_tokens,
            total_tokens=t_tokens,
            finish_reason=finish_reason,
        )

    def list_models(self) -> List[str]:
        """Queries and returns list of models for this provider."""
        if self._name == "ollama":
            # Direct Ollama HTTP check avoids auth headers
            try:
                # e.g. base_url is http://localhost:11434/v1 -> host is http://localhost:11434
                host = self._base_url.split("/v1")[0]
                req = urllib.request.Request(f"{host}/api/tags")
                with urllib.request.urlopen(req, timeout=3.0) as resp:
                    data = json.loads(resp.read().decode())
                    return [m["name"] for m in data.get("models", [])]
            except Exception as e:
                logger.debug(f"Direct Ollama /api/tags call failed: {e}")

        if not self._client:
            self._init_client()
        if not self._client:
            return []

        try:
            res = self._client.models.list()
            return [m.id for m in res.data]
        except Exception as e:
            logger.warning(f"Failed to list models for {self._name}: {e}")
            return []

    def is_available(self) -> Tuple[bool, str]:
        """Validates configuration and checks endpoint availability."""
        if self._name == "groq":
            if not config.groq_api_key:
                return False, "GROQ_API_KEY is not configured in .env."
            try:
                models = self.list_models()
                if models:
                    return True, f"Online ({len(models)} models available, using '{self._model_name}')"
                return True, f"Configured with model '{self._model_name}'"
            except Exception as e:
                return False, f"Groq connection error: {str(e)}"

        elif self._name == "ollama":
            # Check Ollama connectivity
            host = self._base_url.split("/v1")[0]
            try:
                req = urllib.request.Request(f"{host}/api/tags")
                with urllib.request.urlopen(req, timeout=2.0) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode())
                        model_names = [m["name"] for m in data.get("models", [])]
                        # Check if configured model is pulled
                        target = self._model_name
                        target_short = target.split(":")[0]
                        has_model = any(target in m or target_short in m for m in model_names)
                        if not has_model and model_names:
                            return False, (
                                f"Ollama is running, but model '{self._model_name}' is not downloaded. "
                                f"Available: {model_names}. Run: 'ollama pull {self._model_name}'"
                            )
                        elif not model_names:
                            return False, f"Ollama is running, but has no models. Run: 'ollama pull {self._model_name}'"
                        return True, f"Online ({self._model_name} loaded, 8192 ctx)"
            except urllib.error.URLError as url_err:
                return False, (
                    f"Ollama is not running at {self._base_url}. "
                    f"Start it with 'ollama serve' and run 'ollama pull {self._model_name}'."
                )
            except Exception as e:
                return False, f"Ollama check failed: {str(e)}"

        return True, "Ready"
