"""Optional wake-word detector using openwakeword."""

import logging
from typing import Optional
from jarvis.config import config, logger


class WakeWordDetector:
    """Detects 'hey jarvis' wake word when wakeword_enabled is True in settings."""

    def __init__(self, wake_word: Optional[str] = None):
        self.wake_word = wake_word or config.voice.wake_word or "hey jarvis"
        self.model = None
        self._is_available = False
        self._init_detector()

    def _init_detector(self) -> None:
        if not config.voice.wakeword_enabled:
            return

        try:
            import openwakeword
            from openwakeword.model import Model

            # Pre-trained models include 'hey_jarvis' or 'hey jarvis'
            self.model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
            self._is_available = True
            logger.info("openwakeword model 'hey_jarvis' loaded successfully.")
        except Exception as e:
            logger.warning(
                f"openwakeword could not be initialized ({e}). Defaulting to push-to-talk hotkey ({config.voice.hotkey})."
            )
            self._is_available = False

    @property
    def is_available(self) -> bool:
        return self._is_available

    def process_chunk(self, audio_chunk) -> bool:
        """Processes 16kHz audio chunk. Returns True if wake word detected with confidence > 0.5."""
        if not self._is_available or self.model is None:
            return False

        try:
            prediction = self.model.predict(audio_chunk)
            for md, score in prediction.items():
                if score >= 0.5:
                    return True
            return False
        except Exception as e:
            logger.debug(f"Wake word prediction error: {e}")
            return False
