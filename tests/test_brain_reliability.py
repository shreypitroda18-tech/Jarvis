"""Unit tests verifying brain reliability, truthful HTTP status error handling, retry recovery, and operator isolation."""

import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
import pytest
import httpx

from jarvis.config import config
from jarvis.llm.base import BaseLLMProvider, ProviderResponse, ToolCall
from jarvis.llm.fallback import (
    FallbackManager,
    extract_error_info,
    extract_retry_after,
)
from jarvis.control.operator import GeneralComputerOperator


class MockProvider(BaseLLMProvider):
    def __init__(self, name: str = "groq", model_name: str = "mock-model"):
        self._name = name
        self._model_name = model_name
        self.call_history = []
        self.canned_responses = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def model_name(self) -> str:
        return self._model_name

    def list_models(self):
        return [self._model_name]

    def is_available(self):
        return True, "Mock operational"

    def chat(self, messages, tools=None, temperature=0.7, **kwargs):
        self.call_history.append({
            "messages": messages,
            "tools": tools,
            "temperature": temperature,
            "kwargs": kwargs
        })
        if self.canned_responses:
            resp = self.canned_responses.pop(0)
            if isinstance(resp, Exception):
                raise resp
            return resp
        return ProviderResponse(content="Default mock response")


def _make_http_error(status_code: int, message: str, code: str = None, headers: dict = None):
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    body = {"error": {"message": message, "code": code}}
    resp = httpx.Response(status_code=status_code, request=req, json=body, headers=headers or {})
    from openai import APIStatusError
    return APIStatusError(message=message, response=resp, body=body)


def test_400_tool_failure_retry_low_temp_and_succeeds():
    """Verify HTTP 400 with tool failure retries once with low temp (0.2) and succeeds."""
    prov = MockProvider(name="groq")
    err = _make_http_error(400, "Failed to call a function: tool_use_failed", code="tool_use_failed")
    success = ProviderResponse(content="Recovered after low temperature")
    prov.canned_responses = [err, success]

    mgr = FallbackManager(system_instruction="You are JARVIS", tools=[])
    mgr.providers = [prov]

    res = mgr._call_provider_with_retry(prov, [{"role": "user", "content": "test tool call"}])

    assert res.content == "Recovered after low temperature"
    assert len(prov.call_history) == 2
    assert prov.call_history[0]["temperature"] == 0.7
    assert prov.call_history[1]["temperature"] == 0.2


def test_400_tool_failure_persists_falls_back_cleanly():
    """Verify HTTP 400 that persists through retries puts provider on cooldown and falls back."""
    prov_groq = MockProvider(name="groq")
    prov_groq.canned_responses = [
        _make_http_error(400, "tool_use_failed", code="tool_use_failed"),
        _make_http_error(400, "tool_use_failed", code="tool_use_failed"),
        _make_http_error(400, "tool_use_failed", code="tool_use_failed"),
    ]

    prov_fallback = MockProvider(name="ollama")
    prov_fallback.canned_responses = [ProviderResponse(content="Fallback answer from Ollama")]

    mgr = FallbackManager(system_instruction="You are JARVIS", tools=[])
    mgr.providers = [prov_groq, prov_fallback]

    res = mgr.execute_turn("hello")

    assert "Fallback answer from Ollama" in res
    assert mgr.is_provider_blocked("groq") is True
    assert mgr.is_provider_blocked("ollama") is False


def test_429_per_minute_with_retry_after_header(mocker):
    """Verify 429 with retry-after header sleeps for the specified duration and retries."""
    mock_sleep = mocker.patch("time.sleep")
    prov = MockProvider(name="groq")
    err = _make_http_error(
        429,
        "Rate limit reached for requests per minute (RPM)",
        code="rate_limit_exceeded",
        headers={"retry-after": "3"}
    )
    success = ProviderResponse(content="Recovered after rate limit pause")
    prov.canned_responses = [err, success]

    mgr = FallbackManager(system_instruction="You are JARVIS", tools=[])
    mgr.providers = [prov]

    res = mgr._call_provider_with_retry(prov, [{"role": "user", "content": "test 429"}])

    assert res.content == "Recovered after rate limit pause"
    assert len(prov.call_history) == 2
    mock_sleep.assert_called_once_with(3)


def test_429_daily_quota_long_cooldown():
    """Verify daily quota 429 error marks a long cooldown and falls back."""
    prov_groq = MockProvider(name="groq")
    err = _make_http_error(
        429,
        "You have exceeded your daily requests per day (RPD) quota.",
        code="rate_limit_exceeded",
        headers={"x-ratelimit-reset-requests": "7200s"}
    )
    prov_groq.canned_responses = [err]

    prov_fallback = MockProvider(name="ollama")
    prov_fallback.canned_responses = [ProviderResponse(content="Fallback response")]

    mgr = FallbackManager(system_instruction="You are JARVIS", tools=[])
    mgr.providers = [prov_groq, prov_fallback]

    res = mgr.execute_turn("test daily quota")

    assert "Fallback response" in res
    assert mgr.is_provider_blocked("groq") is True
    # Verify cooldown is long (around 7200s)
    blocked_time = mgr.blocked_until["groq"]
    assert blocked_time > datetime.now() + timedelta(seconds=7000)


def test_413_payload_too_large_shrinks_history():
    """Verify 413 Payload Too Large shrinks history and retries successfully."""
    prov = MockProvider(name="groq")
    err = _make_http_error(413, "Request entity too large", code="payload_too_large")
    success = ProviderResponse(content="Success after shrinking")
    prov.canned_responses = [err, success]

    mgr = FallbackManager(system_instruction="You are JARVIS", tools=[])
    mgr.providers = [prov]

    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "msg 1"},
        {"role": "assistant", "content": "reply 1"},
        {"role": "user", "content": "msg 2"},
        {"role": "assistant", "content": "reply 2"},
        {"role": "user", "content": "msg 3"},
    ]

    res = mgr._call_provider_with_retry(prov, messages)

    assert res.content == "Success after shrinking"
    assert len(prov.call_history) == 2
    # Verify second call had fewer messages (system + last 2)
    assert len(prov.call_history[1]["messages"]) == 3


def test_401_and_404_configuration_errors_no_cooldown():
    """Verify 401 and 404 configuration errors do NOT trigger cooldown."""
    for code in (401, 404):
        prov_groq = MockProvider(name="groq")
        prov_groq.canned_responses = [_make_http_error(code, f"Config error {code}", code="invalid_auth")]

        prov_fallback = MockProvider(name="ollama")
        prov_fallback.canned_responses = [ProviderResponse(content="Recovered via fallback")]

        mgr = FallbackManager(system_instruction="You are JARVIS", tools=[])
        mgr.providers = [prov_groq, prov_fallback]

        res = mgr.execute_turn("test config error")

        assert "Recovered via fallback" in res
        # Must NOT be blocked on cooldown
        assert mgr.is_provider_blocked("groq") is False


def test_timeout_error_short_cooldown():
    """Verify connection timeout sets default short cooldown and falls back."""
    prov_groq = MockProvider(name="groq")
    prov_groq.canned_responses = [TimeoutError("Gateway Connection Timed Out")]

    prov_fallback = MockProvider(name="ollama")
    prov_fallback.canned_responses = [ProviderResponse(content="Ollama answer")]

    mgr = FallbackManager(system_instruction="You are JARVIS", tools=[])
    mgr.providers = [prov_groq, prov_fallback]

    res = mgr.execute_turn("test timeout")

    assert "Ollama answer" in res
    assert mgr.is_provider_blocked("groq") is True
    blocked_time = mgr.blocked_until["groq"]
    # Cooldown should be short (around 60s)
    assert blocked_time <= datetime.now() + timedelta(seconds=65)


def test_operator_never_switches_to_ollama_mid_task(mocker):
    """Verify operator loop NEVER switches to Ollama when Groq fails mid-task."""
    op = GeneralComputerOperator()
    op.goal = "calculate numbers"

    # Mock brain with Groq failing and Ollama available
    mock_brain = MagicMock()
    prov_groq = MockProvider(name="groq")
    prov_groq.canned_responses = [_make_http_error(400, "Groq model failed")]
    prov_ollama = MockProvider(name="ollama")
    prov_ollama.canned_responses = [ProviderResponse(content="click(1)")]

    mock_fallback = MagicMock()
    mock_fallback.providers = [prov_groq, prov_ollama]
    mock_fallback.is_provider_blocked.return_value = False
    mock_brain.fallback_manager = mock_fallback
    op.brain = mock_brain

    # Attempt action decision
    action = op._decide_action("Observation text...", mock_fallback)

    # Must STOP and report Groq failure, never call or switch to Ollama
    assert "Computer operation stopped" in action
    assert "Provider groq failed" in action
    assert len(prov_ollama.call_history) == 0


def test_operator_providers_setting_excludes_ollama():
    """Verify operator.providers configuration default excludes Ollama."""
    allowed = config.operator.providers
    assert "groq" in allowed
    assert "ollama" not in allowed
