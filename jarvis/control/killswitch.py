"""Emergency kill switch and visual/audio status feedback for computer control."""

import sys
import threading
import contextlib
import winsound

from jarvis.config import config, logger


def format_hotkey_for_pynput(key_str: str) -> str:
    """Formats strings like 'F8' or 'ctrl+alt+s' into '<f8>' or '<ctrl>+<alt>+s' for pynput."""
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


class ControlAbortedException(Exception):
    """Raised when an active control action is terminated by the user via killswitch."""
    pass


class KillSwitchManager:
    """Manages emergency abort flags, Ctrl+Alt+S hotkey listener, and audio/console cues."""

    def __init__(self):
        self._abort_flag = threading.Event()
        self._hotkey_listener = None
        self._listener_lock = threading.Lock()
        self._is_active = False

    def request_abort(self):
        """Sets the abort flag immediately."""
        self._abort_flag.set()
        logger.warning("KILLSWITCH TRIGGERED: Aborting active computer control actions.")
        print("\n[KILLSWITCH] Computer control was interrupted by user.", flush=True)

    def is_aborted(self) -> bool:
        return self._abort_flag.is_set()

    def check_abort(self):
        """Raises ControlAbortedException if an abort has been requested."""
        if self._abort_flag.is_set():
            raise ControlAbortedException("Computer control was aborted by user killswitch, sir.")

    def reset(self):
        """Resets the abort flag for a new request turn."""
        self._abort_flag.clear()

    def play_sound_cue(self, start: bool = True):
        """Plays gentle start (550Hz) or end (880Hz) frequency cues."""
        def _beep():
            try:
                freq = 550 if start else 880
                winsound.Beep(freq, 80)
            except Exception:
                pass

        threading.Thread(target=_beep, daemon=True).start()

    def start_hotkey_listener(self):
        """Starts global Ctrl+Alt+S listener if not already running."""
        with self._listener_lock:
            if self._hotkey_listener is not None:
                return

            try:
                import pyautogui
                pyautogui.FAILSAFE = True
                from pynput import keyboard

                hotkey_str = format_hotkey_for_pynput(config.automation.killswitch_hotkey or "ctrl+alt+s")
                self._hotkey_listener = keyboard.GlobalHotKeys({
                    hotkey_str: self.request_abort
                })
                self._hotkey_listener.daemon = True
                self._hotkey_listener.start()
                logger.debug(f"Killswitch hotkey listener started for '{hotkey_str}'")
            except Exception as e:
                logger.error(f"Failed to start killswitch listener: {e}")

    @contextlib.contextmanager
    def controlling_session(self, action_desc: str = ""):
        """Context manager displaying [CONTROLLING] banner and playing cues."""
        self.reset()
        self.start_hotkey_listener()
        self.check_abort()

        print(f"\n[CONTROLLING] {action_desc} (Press Ctrl+Alt+S or say 'stop' to abort)...", flush=True)
        self.play_sound_cue(start=True)
        try:
            yield
        finally:
            self.play_sound_cue(start=False)
            print("[CONTROLLING COMPLETE]\n", flush=True)


# Global singleton
kill_switch = KillSwitchManager()
