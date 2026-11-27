"""Optional Google Gemini LLM provider using google-genai SDK."""

import json
from typing import Any, Dict, List, Optional, Tuple
from jarvis.config import config, logger
from jarvis.llm.base import BaseLLMProvider, ProviderResponse, ToolCall


class GeminiProvider(BaseLLMProvider):
    """Integrates Gemini via google-genai SDK when specified in providers."""

    def __init__(self, api_key: str, model_name: str):
        self._api_key = api_key
        self._model_name = model_name or "gemini-2.5-flash"
        self._client = None
        self._init_client()

    def _init_client(self) -> None:
        if not self._api_key:
            return
        try:
            from google import genai
            self._client = genai.Client(api_key=self._api_key)
        except Exception as e:
            logger.warning(f"Failed to initialize Gemini client: {e}")
            self._client = None

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def model_name(self) -> str:
        return self._model_name

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> ProviderResponse:
        if not self._client:
            self._init_client()
            if not self._client:
                raise RuntimeError("Gemini client is not configured or GEMINI_API_KEY is missing.")

        from google.genai import types

        # Extract system prompt if present
        system_instruction = None
        gemini_contents = []

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            if role == "system":
                system_instruction = content
            elif role == "user":
                gemini_contents.append(types.Content(role="user", parts=[types.Part.from_text(text=content or "")]))
            elif role == "assistant":
                parts = []
                if content:
                    parts.append(types.Part.from_text(text=content))
                if msg.get("tool_calls"):
                    for tc in msg["tool_calls"]:
                        fn = tc.get("function", {})
                        parts.append(types.Part.from_function_call(
                            name=fn.get("name"),
                            args=json.loads(fn.get("arguments", "{}")) if isinstance(fn.get("arguments"), str) else fn.get("arguments")
                        ))
                if parts:
                    gemini_contents.append(types.Content(role="model", parts=parts))
            elif role == "tool":
                gemini_contents.append(types.Content(role="user", parts=[
                    types.Part.from_function_response(
                        name=msg.get("name", "tool"),
                        response={"result": msg.get("content")}
                    )
                ]))

        # Gemini tool schemas or functions
        gen_config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.7,
        )

        response = self._client.models.generate_content(
            model=self._model_name,
            contents=gemini_contents,
            config=gen_config,
        )

        content_text = response.text if hasattr(response, "text") and response.text else None
        tool_calls = []

        # Check for function calls in candidates
        if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if hasattr(part, "function_call") and part.function_call:
                    fc = part.function_call
                    tool_calls.append(
                        ToolCall(id=f"call_{fc.name}", name=fc.name, arguments=dict(fc.args))
                    )

        return ProviderResponse(
            content=content_text,
            tool_calls=tool_calls,
            raw_response=response,
        )

    def list_models(self) -> List[str]:
        if not self._client:
            return []
        try:
            return [m.name for m in self._client.models.list()]
        except Exception:
            return []

    def is_available(self) -> Tuple[bool, str]:
        if not self._api_key:
            return False, "GEMINI_API_KEY is not set."
        try:
            if not self._client:
                self._init_client()
            return True, f"Online (Model: {self._model_name})"
        except Exception as e:
            return False, f"Gemini error: {e}"
