"""Global push-to-talk and quit hotkey listener using pynput (no admin rights required)."""

import threading
import logging
from typing import Callable, Optional
from pynput import keyboard
from jarvis.config import config, logger


def format_hotkey_for_pynput(key_str: str) -> str:
    """Formats strings like 'F8' or 'ctrl+alt+q' into '<f8>' or '<ctrl>+<alt>+q' for pynput."""
    parts = [p.strip().lower() for p in key_str.replace("-", "+").split("+")]
    special_keys = {
        "ctrl", "alt", "shift", "cmd", "f1", "f2", "f3", "f4", "f5", "f6",
        "f7", "f8", "f9", "f10", "f11", "f12", "space", "enter", "esc", "tab"
    }
    formatted = []
    for p in parts:
        clean = p.strip("<>")
        if clean in special_keys or len(clean) > 1:
            formatted.append(f"<{clean}>")
        else:
            formatted.append(clean)
    return "+".join(formatted)


class HotkeyListener:
    """Manages global hotkeys for push-to-talk activation and clean exit without admin rights."""

    def __init__(
        self,
        on_trigger: Optional[Callable[[], None]] = None,
        on_quit: Optional[Callable[[], None]] = None,
        talk_hotkey: Optional[str] = None,
        quit_hotkey: Optional[str] = None
    ):
        self.on_trigger = on_trigger
        self.on_quit = on_quit
        self.talk_hotkey_str = format_hotkey_for_pynput(talk_hotkey or config.voice.hotkey or "F8")
        self.quit_hotkey_str = format_hotkey_for_pynput(quit_hotkey or config.voice.quit_hotkey or "ctrl+alt+q")

        self._hotkeys = None
        self._is_running = False

    def _handle_trigger(self):
        if self.on_trigger:
            try:
                self.on_trigger()
            except Exception as e:
                logger.error(f"Error in hotkey trigger callback: {e}")

    def _handle_quit(self):
        if self.on_quit:
            try:
                self.on_quit()
            except Exception as e:
                logger.error(f"Error in hotkey quit callback: {e}")

    def start(self) -> None:
        """Starts listening for global hotkeys in a daemon thread."""
        if self._is_running:
            return

        mapping = {
            self.talk_hotkey_str: self._handle_trigger,
            self.quit_hotkey_str: self._handle_quit
        }

        try:
            self._hotkeys = keyboard.GlobalHotKeys(mapping)
            self._hotkeys.daemon = True
            self._hotkeys.start()
            self._is_running = True
            logger.info(f"Global hotkeys active: Push-to-Talk={self.talk_hotkey_str}, Quit={self.quit_hotkey_str}")
        except Exception as e:
            logger.error(f"Failed to register global hotkeys: {e}. Push-to-talk key may be unavailable.")

    def stop(self) -> None:
        """Stops the global hotkey listener."""
        if self._hotkeys and self._is_running:
            try:
                self._hotkeys.stop()
            except Exception:
                pass
            self._is_running = False
            self._hotkeys = None
