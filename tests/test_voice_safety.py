import io
import sys
import pytest
from unittest.mock import MagicMock
from jarvis.safety import RiskLevel, confirm_action, set_confirmation_handler
from jarvis.voice.voice_loop import voice_confirm_handler
from jarvis.llm.base import ProviderResponse, ToolCall
from jarvis.llm.fallback import FallbackManager


def test_voice_safety_high_risk_requires_typed_confirmation_blocks_on_no(mocker, monkeypatch):
    """
    HIGH risk actions must require typed confirmation by default.
    Spoken confirmation is disallowed. Answering 'no' on the keyboard aborts.
    """
    mock_tts = MagicMock()
    mocker.patch("jarvis.voice.voice_loop.get_tts_engine", return_value=mock_tts)
    monkeypatch.setattr(sys, "stdin", io.StringIO("no\n"))

    set_confirmation_handler(voice_confirm_handler)
    try:
        confirmed = confirm_action(RiskLevel.HIGH, "Initiate system shutdown")
        assert confirmed is False
        mock_tts.speak.assert_any_call("Sir, this is a high risk operation: Initiate system shutdown. Please confirm on the keyboard.")
        mock_tts.speak.assert_any_call("Action cancelled, sir.")
    finally:
        set_confirmation_handler(None)


def test_voice_safety_high_risk_requires_typed_confirmation_allows_on_yes(mocker, monkeypatch):
    """
    Typing 'yes, confirm' on the keyboard approves a HIGH risk action.
    """
    mock_tts = MagicMock()
    mocker.patch("jarvis.voice.voice_loop.get_tts_engine", return_value=mock_tts)
    monkeypatch.setattr(sys, "stdin", io.StringIO("yes, confirm\n"))

    set_confirmation_handler(voice_confirm_handler)
    try:
        confirmed = confirm_action(RiskLevel.HIGH, "Initiate system restart")
        assert confirmed is True
        mock_tts.speak.assert_any_call("Confirmed, sir.")
    finally:
        set_confirmation_handler(None)


def test_voice_safety_medium_risk_exact_phrase_approved(mocker):
    """
    MEDIUM risk actions ask by voice and approve ONLY if the spoken response
    matches 'yes, confirm' exactly.
    """
    mock_tts = MagicMock()
    mock_stt = MagicMock()
    mock_stt.record_and_transcribe.return_value = "yes, confirm"

    mocker.patch("jarvis.voice.voice_loop.get_tts_engine", return_value=mock_tts)
    mocker.patch("jarvis.voice.voice_loop.get_stt_engine", return_value=mock_stt)

    set_confirmation_handler(voice_confirm_handler)
    try:
        confirmed = confirm_action(RiskLevel.MEDIUM, "Open file budget.xlsx")
        assert confirmed is True
        mock_tts.speak.assert_any_call("Confirmed, sir.")
    finally:
        set_confirmation_handler(None)


def test_voice_safety_medium_risk_ambiguous_phrase_denied(mocker):
    """
    Ambiguous answers like 'yes', 'sure', 'ok', or silence must be denied.
    """
    mock_tts = MagicMock()
    mock_stt = MagicMock()
    mocker.patch("jarvis.voice.voice_loop.get_tts_engine", return_value=mock_tts)
    mocker.patch("jarvis.voice.voice_loop.get_stt_engine", return_value=mock_stt)

    ambiguous_responses = ["yes", "yeah", "sure", "ok", "maybe", "", "what", "I think so"]

    set_confirmation_handler(voice_confirm_handler)
    try:
        for resp in ambiguous_responses:
            mock_stt.record_and_transcribe.return_value = resp
            confirmed = confirm_action(RiskLevel.MEDIUM, "Open file credentials.txt")
            assert confirmed is False, f"Expected '{resp}' to be denied, but was approved!"
    finally:
        set_confirmation_handler(None)


def test_voice_safety_medium_risk_explicit_no_denied(mocker):
    mock_tts = MagicMock()
    mock_stt = MagicMock()
    mock_stt.record_and_transcribe.return_value = "no"

    mocker.patch("jarvis.voice.voice_loop.get_tts_engine", return_value=mock_tts)
    mocker.patch("jarvis.voice.voice_loop.get_stt_engine", return_value=mock_stt)

    set_confirmation_handler(voice_confirm_handler)
    try:
        confirmed = confirm_action(RiskLevel.MEDIUM, "Open file README.md")
        assert confirmed is False
        mock_tts.speak.assert_any_call("Action cancelled, sir.")
    finally:
        set_confirmation_handler(None)


def test_voice_transcribed_speech_unregistered_tool_not_executed():
    """
    Even if voice-transcribed speech causes an LLM to hallucinate an unregistered tool,
    the tool is strictly not executed and raw JSON is suppressed.
    """
    executed = []

    def mock_registered_tool(location: str) -> str:
        executed.append(location)
        return "OK"

    mgr = FallbackManager(system_instruction="Butler", tools=[mock_registered_tool])
    mock_provider = MagicMock()
    mock_provider.name = "ollama"
    mock_provider.model_name = "mock"
    mock_provider.chat.return_value = ProviderResponse(
        content=None,
        tool_calls=[ToolCall(id="call_x", name="unregistered_tool", arguments={"action": "harmful", "text": "I cannot perform that action."})]
    )

    mgr.providers = [mock_provider]

    # Simulate turn from transcribed voice command
    reply = mgr.execute_turn("destroy the system")

    # 1. No tool was executed
    assert len(executed) == 0

    # 2. Raw JSON is never returned
    assert "{" not in reply and "}" not in reply
    assert "unregistered_tool" not in reply

    # 3. Clean human text returned
    assert "I cannot perform that action." in reply
