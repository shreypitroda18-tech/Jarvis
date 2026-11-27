import pytest
from unittest.mock import MagicMock
from jarvis.voice.hotkey import format_hotkey_for_pynput, HotkeyListener


def test_format_hotkey_for_pynput():
    assert format_hotkey_for_pynput("F8") == "<f8>"
    assert format_hotkey_for_pynput("ctrl+alt+q") == "<ctrl>+<alt>+q"
    assert format_hotkey_for_pynput("<f8>") == "<f8>"
    assert format_hotkey_for_pynput("ctrl+space") == "<ctrl>+<space>"


def test_hotkey_listener_callbacks(mocker):
    on_trigger_mock = MagicMock()
    on_quit_mock = MagicMock()

    listener = HotkeyListener(
        on_trigger=on_trigger_mock,
        on_quit=on_quit_mock,
        talk_hotkey="F8",
        quit_hotkey="ctrl+alt+q"
    )

    listener._handle_trigger()
    on_trigger_mock.assert_called_once()

    listener._handle_quit()
    on_quit_mock.assert_called_once()
