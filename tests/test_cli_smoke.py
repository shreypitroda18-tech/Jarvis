"""Comprehensive smoke and safety tests for JARVIS CLI flags and --dry-run guarantees."""

import io
import sys
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from jarvis.config import config, BASE_DIR
from jarvis.main import main, run_setup_browser, run_refresh_apps
from jarvis.control.operator import GeneralComputerOperator
from jarvis.control.uia_observer import WindowObservation, ObservedElement
from jarvis.control.desktop import desktop
from jarvis.control.browser import browser
from jarvis.tools.system_tools import (
    open_app,
    open_website,
    take_screenshot,
    set_volume,
    mute,
    media_control,
)
from jarvis.tools.power_tools import system_power, cancel_shutdown
from jarvis.timing import tracker


@pytest.fixture(autouse=True)
def reset_config_flags():
    """Resets CLI-driven config flags and action budget before and after every test."""
    from jarvis.control.safety import action_budget
    action_budget.reset()
    orig_dry = getattr(config, "dry_run", False)
    orig_debug = getattr(config, "operate_debug", False)
    orig_timing = tracker.show_timing_summary
    yield
    action_budget.reset()
    config.dry_run = orig_dry
    config.operate_debug = orig_debug
    tracker.show_timing_summary = orig_timing


# =============================================================================
# 1. CLI Smoke Tests: Every flag and required combination
# =============================================================================

@pytest.mark.parametrize("flag,patch_target,expected_dry,expected_debug,expected_timing", [
    ("--text", "jarvis.main.run_text_loop", False, False, False),
    ("--voice", "jarvis.voice.voice_loop.run_voice_loop", False, False, False),
    ("--doctor", "jarvis.main.run_doctor", False, False, False),
    ("--mic-test", "jarvis.main.run_mic_test", False, False, False),
    ("--timing", "jarvis.main.run_text_loop", False, False, True),
    ("--list-apps", "jarvis.main.run_list_apps", False, False, False),
    ("--refresh-apps", "jarvis.main.run_refresh_apps", False, False, False),
    ("--list-models", "jarvis.main.run_list_models", False, False, False),
    ("--list-audio-devices", "jarvis.voice.stt.list_audio_devices", False, False, False),
    ("--setup-browser", "jarvis.main.run_setup_browser", False, False, False),
    ("--operate-debug", "jarvis.main.run_text_loop", False, True, False),
    ("--dry-run", "jarvis.main.run_text_loop", True, False, False),
])
def test_cli_individual_flags_smoke(flag, patch_target, expected_dry, expected_debug, expected_timing):
    """Verifies that each individual CLI flag parses cleanly, sets config, and dispatches without error."""
    with patch("sys.argv", ["jarvis", flag]):
        with patch(patch_target, return_value=None if "list_audio_devices" not in patch_target else "Mock Devices") as mocked:
            with patch("jarvis.brain.JarvisBrain"):
                with patch("jarvis.tools.app_discovery.load_apps_index", return_value={}):
                    main()
                    assert mocked.called
                    assert getattr(config, "dry_run", False) == expected_dry
                    assert getattr(config, "operate_debug", False) == expected_debug
                    assert tracker.show_timing_summary == expected_timing


@pytest.mark.parametrize("flags,patch_target,expected_dry,expected_debug", [
    (["--text", "--dry-run"], "jarvis.main.run_text_loop", True, False),
    (["--text", "--operate-debug"], "jarvis.main.run_text_loop", False, True),
    (["--voice", "--dry-run"], "jarvis.voice.voice_loop.run_voice_loop", True, False),
])
def test_cli_flag_combinations_smoke(flags, patch_target, expected_dry, expected_debug):
    """Verifies that combined CLI flags configure settings and execute without exceptions."""
    with patch("sys.argv", ["jarvis"] + flags):
        with patch(patch_target, return_value=None) as mocked:
            with patch("jarvis.brain.JarvisBrain"):
                with patch("jarvis.tools.app_discovery.load_apps_index", return_value={}):
                    main()
                    assert mocked.called
                    assert getattr(config, "dry_run", False) == expected_dry
                    assert getattr(config, "operate_debug", False) == expected_debug


def test_exact_crash_unbound_local_error_regression():
    """Exact regression test catching the UnboundLocalError at main.py:331.
    Simulates: python -m jarvis.main --text --dry-run
    """
    with patch("sys.argv", ["jarvis", "--text", "--dry-run"]):
        with patch("sys.stdin.readline", return_value=""):  # EOF immediately exits loop
            with patch("jarvis.brain.JarvisBrain"):
                with patch("jarvis.tools.app_discovery.load_apps_index", return_value={}):
                    # Must NOT raise UnboundLocalError
                    main()
                    assert getattr(config, "dry_run", False) is True


def test_setup_browser_and_refresh_apps_respect_dry_run():
    """Verifies that --setup-browser --dry-run and --refresh-apps --dry-run skip execution and file writes."""
    config.dry_run = True

    # Setup browser in dry-run
    with patch("playwright.sync_api.sync_playwright") as mock_pw:
        run_setup_browser()
        mock_pw.assert_not_called()

    # Refresh apps in dry-run
    with patch("jarvis.tools.app_discovery.load_apps_index") as mock_load:
        run_refresh_apps()
        mock_load.assert_not_called()


# =============================================================================
# 2. Strict Fake UI & Dry-Run Never Acts Guarantees
# =============================================================================

class StrictFakeUIElement:
    """Fake UI element that immediately fails if any mutating method is invoked."""

    def __init__(self, name="Test Button", elem_id=1, auto_id="btn_test"):
        self.name = name
        self.element_id = elem_id
        self.automation_id = auto_id

    def click_input(self, *args, **kwargs):
        raise AssertionError("Action executed during dry-run: click_input")

    def click(self, *args, **kwargs):
        raise AssertionError("Action executed during dry-run: click")

    def invoke(self, *args, **kwargs):
        raise AssertionError("Action executed during dry-run: invoke")

    def double_click_input(self, *args, **kwargs):
        raise AssertionError("Action executed during dry-run: double_click_input")

    def right_click_input(self, *args, **kwargs):
        raise AssertionError("Action executed during dry-run: right_click_input")

    def type_keys(self, *args, **kwargs):
        raise AssertionError("Action executed during dry-run: type_keys")

    def set_focus(self, *args, **kwargs):
        raise AssertionError("Action executed during dry-run: set_focus")

    def select(self, *args, **kwargs):
        raise AssertionError("Action executed during dry-run: select")

    def select_option(self, *args, **kwargs):
        raise AssertionError("Action executed during dry-run: select_option")


def test_operator_actions_strictly_guarantee_no_physical_actions():
    """Tests that operator._execute_action never touches native UI elements or system in dry-run."""
    fake_native = StrictFakeUIElement()
    observed_elem = ObservedElement(
        element_id=1,
        role="Button",
        name="Save File",
        automation_id="saveBtn",
        is_enabled=True,
        is_password=False,
        state="normal",
        text="",
        native_element=fake_native
    )
    obs = WindowObservation(
        window_title="Sample App",
        process_name="app.exe",
        hwnd=12345,
        elements=[observed_elem]
    )

    operator = GeneralComputerOperator()
    operator.dry_run = True
    config.dry_run = True

    # Patches to assert that global input injectors and process launchers are NEVER called
    with patch("pyautogui.write") as mock_write, \
         patch("pyautogui.press") as mock_press, \
         patch("pyautogui.hotkey") as mock_hotkey, \
         patch("pyautogui.scroll") as mock_scroll, \
         patch("subprocess.Popen") as mock_popen, \
         patch("subprocess.run") as mock_subrun, \
         patch("os.startfile", create=True) as mock_startfile:

        # 1. Click
        res = operator._execute_action("click", {"id": 1}, obs)
        assert "[DRY-RUN]" in res
        assert "Would click" in res

        # 2. Double Click
        res = operator._execute_action("double_click", {"id": 1}, obs)
        assert "[DRY-RUN]" in res
        assert "Would double click" in res

        # 3. Right Click
        res = operator._execute_action("right_click", {"id": 1}, obs)
        assert "[DRY-RUN]" in res
        assert "Would right click" in res

        # 4. Type
        res = operator._execute_action("type", {"id": 1, "text": "hello jarvis"}, obs)
        assert "[DRY-RUN]" in res
        assert "Would type" in res
        mock_write.assert_not_called()

        # 5. Press
        res = operator._execute_action("press", {"keys": "enter"}, obs)
        assert "[DRY-RUN]" in res
        assert "Would press keys" in res
        mock_press.assert_not_called()

        # 6. Scroll
        res = operator._execute_action("scroll", {"direction": "down"}, obs)
        assert "[DRY-RUN]" in res
        assert "Would scroll" in res
        mock_scroll.assert_not_called()

        # 7. Select
        res = operator._execute_action("select", {"id": 1, "option": "Item 1"}, obs)
        assert "[DRY-RUN]" in res
        assert "Would select option" in res

        # 8. Open App
        res = operator._execute_action("open_app", {"name": "notepad"}, obs)
        assert "[DRY-RUN]" in res
        assert "Would launch app" in res
        mock_popen.assert_not_called()

        # 9. Open URL
        res = operator._execute_action("open_url", {"url": "https://www.google.com"}, obs)
        assert "[DRY-RUN]" in res
        assert "Would open URL" in res
        mock_popen.assert_not_called()

        # 10. Focus Window
        res = operator._execute_action("focus_window", {"name": "Calculator"}, obs)
        assert "[DRY-RUN]" in res
        assert "Would focus window" in res

        # Confirm zero external mutations occurred
        mock_write.assert_not_called()
        mock_press.assert_not_called()
        mock_hotkey.assert_not_called()
        mock_scroll.assert_not_called()
        mock_popen.assert_not_called()
        mock_subrun.assert_not_called()
        mock_startfile.assert_not_called()


def test_direct_tools_guarantee_dry_run_safety():
    """Verifies that direct desktop, browser, and system tools strictly honor dry-run."""
    config.dry_run = True

    # Desktop Manager direct calls
    with patch("pyautogui.press") as mock_p, \
         patch("pyautogui.write") as mock_w, \
         patch("pyautogui.scroll") as mock_s:
        assert "[DRY-RUN]" in desktop.click_control("Submit")
        assert "[DRY-RUN]" in desktop.type_into("Search", "Jarvis")
        assert "[DRY-RUN]" in desktop.press_keys("ctrl+c")
        assert "[DRY-RUN]" in desktop.focus_window("Chrome")
        assert "[DRY-RUN]" in desktop.scroll("up")
        mock_p.assert_not_called()
        mock_w.assert_not_called()
        mock_s.assert_not_called()

    # Browser Manager direct calls
    assert "[DRY-RUN]" in browser.open("https://www.google.com")
    assert "[DRY-RUN]" in browser.click("button")
    assert "[DRY-RUN]" in browser.type_text("search", "test")
    assert "[DRY-RUN]" in browser.press_key("Enter")
    assert "[DRY-RUN]" in browser.scroll("down")
    assert "[DRY-RUN]" in browser.youtube_play("classic rock")
    assert "[DRY-RUN]" in browser.netflix_play("the crown")

    # System tools direct calls
    with patch("subprocess.Popen") as mock_pop, patch("subprocess.run") as mock_sub:
        assert "[DRY-RUN]" in open_app("notepad")
        assert "[DRY-RUN]" in open_website("https://www.google.com")
        assert "[DRY-RUN]" in set_volume(50)
        assert "[DRY-RUN]" in mute(True)
        assert "[DRY-RUN]" in media_control("play")
        assert "[DRY-RUN]" in system_power("shutdown")
        assert "[DRY-RUN]" in cancel_shutdown()
        mock_pop.assert_not_called()
        mock_sub.assert_not_called()


def test_dry_run_guarantees_no_file_writes_except_logs(tmp_path):
    """Guarantees that --dry-run writes NO files to disk (screenshots, apps index), allowing only logs."""
    config.dry_run = True

    # Take screenshot in dry run
    screenshot_res = take_screenshot("test_capture.png")
    assert "[DRY-RUN]" in screenshot_res
    target_file = BASE_DIR / "screenshots" / "test_capture.png"
    assert not target_file.exists(), "Screenshot file was written to disk during dry run!"

    # Refresh app discovery index in dry run
    with patch("jarvis.tools.app_discovery.APPS_INDEX_PATH", tmp_path / "apps_index_dry.json"):
        from jarvis.tools.app_discovery import build_apps_index
        build_apps_index()
        assert not (tmp_path / "apps_index_dry.json").exists(), "apps_index.json was written to disk during dry run!"


# =============================================================================
# 3. Explicit Foreground Isolation & Safety vs Dry-Run Tests
# =============================================================================

def test_dry_run_with_safe_notepad_foreground_returns_dry_run_and_calls_no_actions():
    """Explicit Test 1: With a fake foreground of notepad.exe, dry-run returns '[DRY-RUN]'
    and calls no action methods (no physical typing, clicks, keys, or scrolls).
    """
    config.dry_run = True
    notepad_info = ("notepad.exe", "Untitled - Notepad", "Notepad")

    with patch("jarvis.control.safety.get_foreground_app_info", return_value=notepad_info), \
         patch("jarvis.control.desktop.get_foreground_app_info", return_value=notepad_info), \
         patch("pyautogui.write") as mock_w, \
         patch("pyautogui.press") as mock_p, \
         patch("pyautogui.hotkey") as mock_hk, \
         patch("pyautogui.scroll") as mock_s:

        # 1. Desktop type_into
        res_type = desktop.type_into("Edit", "hello world")
        assert "[DRY-RUN]" in res_type
        assert "Would type into 'Edit'" in res_type

        # 2. Desktop click_control
        res_click = desktop.click_control("File")
        assert "[DRY-RUN]" in res_click
        assert "Would click desktop control 'File'" in res_click

        # 3. Desktop press_keys
        res_press = desktop.press_keys("ctrl+s")
        assert "[DRY-RUN]" in res_press
        assert "Would press keys 'ctrl+s'" in res_press

        # 4. Desktop scroll
        res_scroll = desktop.scroll("down")
        assert "[DRY-RUN]" in res_scroll
        assert "Would scroll active window down" in res_scroll

        # Strict guarantee: zero action methods executed
        mock_w.assert_not_called()
        mock_p.assert_not_called()
        mock_hk.assert_not_called()
        mock_s.assert_not_called()


@pytest.mark.parametrize("blocked_proc,blocked_title", [
    ("windowsterminal.exe", "Windows Terminal"),
    ("powershell.exe", "Windows PowerShell"),
])
def test_action_blocked_even_in_dry_run_when_foreground_is_terminal(blocked_proc, blocked_title):
    """Explicit Test 2: With a fake foreground of windowsterminal.exe or powershell.exe,
    the action is blocked even in dry-run, never returning '[DRY-RUN]', and calling no action methods.
    """
    config.dry_run = True
    terminal_info = (blocked_proc, blocked_title, "ConsoleWindowClass")

    with patch("jarvis.control.safety.get_foreground_app_info", return_value=terminal_info), \
         patch("jarvis.control.desktop.get_foreground_app_info", return_value=terminal_info), \
         patch("pyautogui.write") as mock_w, \
         patch("pyautogui.press") as mock_p, \
         patch("pyautogui.hotkey") as mock_hk, \
         patch("pyautogui.scroll") as mock_s:

        # 1. Desktop type_into must be blocked by safety policy, NOT return [DRY-RUN]
        res_type = desktop.type_into("Terminal Window", "dangerous command")
        assert "access denied" in res_type.lower()
        assert "administrative shell" in res_type.lower()
        assert "[DRY-RUN]" not in res_type

        # 2. Desktop click_control must be blocked
        res_click = desktop.click_control("Submit")
        assert "access denied" in res_click.lower()
        assert "administrative shell" in res_click.lower()
        assert "[DRY-RUN]" not in res_click

        # 3. Desktop press_keys must be blocked
        res_press = desktop.press_keys("enter")
        assert "access denied" in res_press.lower()
        assert "administrative shell" in res_press.lower()
        assert "[DRY-RUN]" not in res_press

        # 4. Desktop scroll must be blocked
        res_scroll = desktop.scroll("down")
        assert "access denied" in res_scroll.lower()
        assert "administrative shell" in res_scroll.lower()
        assert "[DRY-RUN]" not in res_scroll

        # Strict guarantee: zero action methods executed
        mock_w.assert_not_called()
        mock_p.assert_not_called()
        mock_hk.assert_not_called()
        mock_s.assert_not_called()
