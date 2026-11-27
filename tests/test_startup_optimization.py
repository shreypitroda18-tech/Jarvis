"""Unit tests for startup optimization, lazy loading, app index caching, and timing stages."""

import sys
import time
import subprocess
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

from jarvis.timing import StartupTracker
from jarvis.llm.base import ProviderResponse
from jarvis.llm.fallback import FallbackManager
from jarvis.voice.stt import STTEngine
import jarvis.tools.app_discovery as app_discovery


def test_startup_tracker_record_and_summary(capsys):
    """Verifies that StartupTracker records stages and formats summary table properly."""
    tracker = StartupTracker()
    tracker.start_stage("test_stage_1")
    time.sleep(0.01)
    dur = tracker.end_stage("test_stage_1", notes="details_1")
    assert dur > 0

    tracker.record_stage("test_stage_2", 0.042, notes="details_2")
    assert len(tracker._stages) == 2
    assert tracker._stages[0][0] == "test_stage_1"
    assert tracker._stages[1][0] == "test_stage_2"
    assert tracker._stages[1][1] == 0.042

    # Should not print when show_timing_summary is False
    tracker.show_timing_summary = False
    tracker.print_summary_table()
    out, _ = capsys.readouterr()
    assert out == ""

    # Should print when show_timing_summary is True
    tracker.show_timing_summary = True
    tracker.print_summary_table()
    out, _ = capsys.readouterr()
    assert "JARVIS Startup Timing Summary" in out
    assert "test_stage_1" in out
    assert "test_stage_2" in out
    assert "0.042s" in out
    assert "Total Startup Elapsed" in out


def test_lazy_imports_in_text_mode():
    """Confirms that importing main and initializing brain does NOT load heavy voice/browser/automation modules."""
    code = (
        "import sys\n"
        "from jarvis.config import config\n"
        "import jarvis.main\n"
        "from jarvis.brain import JarvisBrain\n"
        "b = JarvisBrain()\n"
        "heavy = ['faster_whisper', 'sounddevice', 'playwright', 'pywinauto', 'openwakeword']\n"
        "loaded = [m for m in heavy if m in sys.modules]\n"
        "if loaded:\n"
        "    print(f'ERROR: Heavy modules imported: {loaded}')\n"
        "    sys.exit(1)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Subprocess failed with stdout: {result.stdout}, stderr: {result.stderr}"


def test_app_index_caching(tmp_path, monkeypatch):
    """Confirms load_apps_index uses cached disk file if fresh, and spawns background refresh if expired."""
    fake_index_path = tmp_path / "apps_index.json"
    fake_catalog = {
        "notepad": {"name": "Notepad", "type": "exe", "target": "notepad.exe"}
    }
    import json
    fake_index_path.write_text(json.dumps(fake_catalog), encoding="utf-8")

    monkeypatch.setattr(app_discovery, "APPS_INDEX_PATH", fake_index_path)
    monkeypatch.setattr(app_discovery, "_IN_MEMORY_APPS_INDEX", None)

    # 1. Fresh file should load directly from disk without rebuilding
    with patch.object(app_discovery, "discover_installed_apps") as mock_discover:
        apps = app_discovery.load_apps_index(force_refresh=False)
        assert len(apps) == 1
        assert "notepad" in apps
        assert apps["notepad"]["name"] == "Notepad"
        mock_discover.assert_not_called()

    # 2. File older than 7 days triggers background refresh
    import os
    old_time = time.time() - (8 * 86400)
    os.utime(fake_index_path, (old_time, old_time))
    monkeypatch.setattr(app_discovery, "_IN_MEMORY_APPS_INDEX", None)

    with patch("threading.Thread") as mock_thread_cls:
        mock_thread = MagicMock()
        mock_thread_cls.return_value = mock_thread
        apps = app_discovery.load_apps_index(force_refresh=False)
        assert len(apps) == 1
        mock_thread_cls.assert_called_once()
        mock_thread.start.assert_called_once()

    # 3. force_refresh=True synchronously discovers apps
    with patch.object(app_discovery, "discover_installed_apps", return_value={"calculator": {"name": "Calculator", "type": "uwp", "target": "calc"}}) as mock_discover:
        apps = app_discovery.load_apps_index(force_refresh=True)
        assert len(apps) == 1
        assert "calculator" in apps
        mock_discover.assert_called_once()


def test_stt_background_loading():
    """Verifies that STTEngine can preload the Whisper model in a background thread without blocking main thread."""
    stt = STTEngine()
    assert stt.model is None
    assert not stt._ready_event.is_set()

    # Mock WhisperModel constructor
    mock_model = MagicMock()
    with patch("faster_whisper.WhisperModel", return_value=mock_model):
        stt.start_background_load()
        assert stt.wait_until_ready(timeout=3.0) is True
        assert stt.model == mock_model
        assert stt._ready_event.is_set()


def test_ollama_warmup_timing_on_fallback():
    """Verifies Ollama is not warmed at initialization and records timing on first fallback."""
    from tests.test_llm_fallback import MockProvider
    from openai import RateLimitError

    mock_groq = MockProvider("groq")
    mock_ollama = MockProvider("ollama")

    mock_groq.chat_side_effect = RateLimitError(
        message="Daily requests per day (RPD) limit exceeded",
        response=MagicMock(status_code=429),
        body={}
    )

    mgr = FallbackManager(system_instruction="Butler", tools=[])
    mgr.providers = [mock_groq, mock_ollama]
    assert mgr._ollama_warmed is False

    from jarvis.timing import tracker
    tracker._stages.clear()

    reply = mgr.execute_turn("Hello")
    assert reply == "Response from ollama"
    assert mgr._ollama_warmed is True

    # Check stage timing recorded
    recorded_stages = [s[0] for s in tracker._stages]
    assert "ollama_warmup" in recorded_stages

    # On second turn, ollama_warmup is not re-recorded
    tracker._stages.clear()
    reply2 = mgr.execute_turn("Another query")
    assert reply2 == "Response from ollama"
    assert "ollama_warmup" not in [s[0] for s in tracker._stages]
