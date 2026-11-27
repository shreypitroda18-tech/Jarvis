"""Voice assistant state management and audio cues."""

import sys
import numpy as np
import sounddevice as sd
from enum import Enum
from typing import Optional
from jarvis.config import config, logger


class VoiceState(str, Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"


class StateManager:
    """Manages visual status in console and auditory cues."""

    def __init__(self):
        self.current_state = VoiceState.IDLE

    def set_state(self, new_state: VoiceState) -> None:
        """Updates voice state and prints a clean console indicator."""
        self.current_state = new_state
        hotkey_name = config.voice.hotkey.upper()
        quit_name = config.voice.quit_hotkey.upper()

        if new_state == VoiceState.IDLE:
            indicator = f"\n[IDLE] (Press {hotkey_name} to speak | {quit_name} to quit | or type below)"
        elif new_state == VoiceState.LISTENING:
            indicator = f"\n[LISTENING] (Speak now...)"
        elif new_state == VoiceState.THINKING:
            indicator = f"\n[THINKING] (Processing request...)"
        elif new_state == VoiceState.SPEAKING:
            indicator = f"\n[SPEAKING] (Press {hotkey_name} or say 'stop' to cut off speech)"
        else:
            indicator = f"\n[{new_state.value}]"

        sys.stdout.write(f"{indicator}\n")
        sys.stdout.flush()

    def play_activation_beep(self) -> None:
        """Plays a short, gentle tone (880 Hz sine wave for 75ms) signaling JARVIS is listening."""
        try:
            sample_rate = 16000
            duration = 0.075  # 75 ms
            t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
            # Gentle envelope to avoid clicks
            envelope = np.sin(np.pi * t / duration)
            tone = (0.2 * np.sin(2 * np.pi * 880 * t) * envelope).astype(np.float32)

            out_device = config.voice.output_device
            sd.play(tone, samplerate=sample_rate, device=out_device)
        except Exception as e:
            logger.debug(f"Could not play activation beep: {e}")


_state_manager: Optional[StateManager] = None


def get_state_manager() -> StateManager:
    global _state_manager
    if _state_manager is None:
        _state_manager = StateManager()
    return _state_manager
