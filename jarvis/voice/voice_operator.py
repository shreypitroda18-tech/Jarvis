"""Voice operator integration: sparse speech, background spoken stop listener, and ambiguity handling."""

import re
import sys
import time
import threading
from typing import Optional, Callable

from jarvis.config import config, logger
from jarvis.control.killswitch import kill_switch
from jarvis.voice.tts import get_tts_engine
from jarvis.voice.stt import get_stt_engine


class OperatingAudioListener:
    """
    Background listener active ONLY during computer control operations.
    Listens for spoken 'stop', 'cancel', or 'never mind' to trip the killswitch immediately.
    Displays a visible indicator on screen while listening.
    """

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._is_running = False

    def start(self):
        if self._is_running:
            return
        self._stop_event.clear()
        self._is_running = True

        def _worker():
            stt = get_stt_engine()
            sys.stdout.write("\n[MIC: LISTENING FOR 'STOP'] (Say 'stop', 'cancel', or press Ctrl+Alt+S to abort)\n")
            sys.stdout.flush()

            stop_words = {"stop", "cancel", "never mind", "nevermind", "abort", "halt"}

            while not self._stop_event.is_set() and not kill_switch.is_aborted():
                try:
                    # Record short 1.2s audio segment
                    audio = stt.record_audio(stop_event=self._stop_event, max_duration=1.5, silence_timeout=0.6)
                    if self._stop_event.is_set() or kill_switch.is_aborted():
                        break
                    if audio is not None and len(audio) > 1600:
                        text = stt.transcribe(audio).strip().lower()
                        norm = re.sub(r"[^\w\s]", "", text).strip()
                        if any(w in norm for w in stop_words):
                            print(f"\n[VOICE ABORT] Spoken stop command detected ('{text}'). Aborting immediately...")
                            kill_switch.request_abort()
                            break
                except Exception as e:
                    logger.debug(f"OperatingAudioListener exception: {e}")
                    time.sleep(0.2)

            self._is_running = False

        self._thread = threading.Thread(target=_worker, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive() and self._thread != threading.current_thread():
            try:
                self._thread.join(timeout=0.6)
            except Exception:
                pass
        self._is_running = False


_voice_mode_active: bool = False


def set_voice_mode_active(active: bool = True) -> None:
    """Sets whether voice loop is currently active."""
    global _voice_mode_active
    _voice_mode_active = active


def is_voice_mode_active() -> bool:
    """Checks if voice mode is currently active."""
    return _voice_mode_active


# Global listener instance
operating_audio_listener = OperatingAudioListener()


def notify_operate_start(goal: str, speak: bool = True):
    """Prints 'I heard: ...' and provides sparse voice start announcement."""
    print(f"I heard: {goal}")
    if speak and config.operator.spoken_updates == "sparse":
        tts = get_tts_engine()
        tts.speak("Operating the computer, sir.")


def notify_operate_end(result: str, speak: bool = True):
    """Provides sparse spoken completion feedback."""
    if speak and config.operator.spoken_updates == "sparse":
        tts = get_tts_engine()
        # Clean response to 1-2 spoken sentences
        clean = result.replace("[CONTROLLING]", "").strip()
        lines = [line.strip() for line in clean.split("\n") if line.strip() and not line.startswith("Step")]
        spoken_text = lines[-1] if lines else "Done, sir."
        tts.speak(spoken_text)
