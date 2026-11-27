import os
import logging
from jarvis.config import load_config, SecretFilter, AppConfig


def test_config_loads_defaults_and_yaml():
    cfg = load_config()
    assert isinstance(cfg, AppConfig)
    assert cfg.assistant.name == "JARVIS"
    assert "notepad" in cfg.apps
    assert "google" in cfg.favorite_sites
    assert len(cfg.allowed_paths) > 0
    assert cfg.llm_provider in ("groq", "ollama", "gemini")
    assert isinstance(cfg.llm_fallbacks, list)


def test_secret_filter_masks_keys():
    fake_gemini = "AIzaSySecretTestKey1234567890123456"  # fake-secret-ok
    fake_groq = "gsk_abcdef1234567890abcdef1234567890"  # fake-secret-ok
    filter_obj = SecretFilter(secrets=[fake_gemini, fake_groq])
    
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg=f"Connecting with keys {fake_gemini} and {fake_groq}",
        args=(),
        exc_info=None
    )
    filter_obj.filter(record)
    assert fake_gemini not in record.msg
    assert fake_groq not in record.msg
    assert "[REDACTED" in record.msg


def test_voice_config_settings():
    cfg = load_config()
    assert cfg.voice.mic_enabled is True
    assert cfg.voice.hotkey.upper() == "F8"
    assert "ctrl" in cfg.voice.quit_hotkey.lower()
    assert cfg.voice.stt_model == "small"
    assert cfg.voice.tts_engine in ("windows", "pyttsx3")
    assert cfg.voice.confirmations_by_voice is True
    assert cfg.voice.allow_high_risk_voice_confirmation is False

