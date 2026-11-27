"""Modular Text-to-Speech (TTS) layer with engine abstraction, text cleaning, and interruption support."""

import re
import threading
import logging
from abc import ABC, abstractmethod
from typing import Optional, Tuple
from jarvis.config import config, logger


def clean_speech_text(text: str) -> str:
    """
    Cleans text for spoken output:
    - Strips markdown formatting (bold, italic, headers, backticks).
    - Replaces code blocks with a short spoken note.
    - Replaces URLs with 'link'.
    - Strips emojis and control characters.
    - Scrubs API keys, secrets, and raw untrusted data wrappers.
    - Summarizes long structured outputs (e.g. file lists, multi-line stats) into 1 sentence.
    """
    if not text or not text.strip():
        return ""

    cleaned = text.strip()

    # 1. Strip untrusted data markers if present
    cleaned = re.sub(r"--- UNTRUSTED DATA START.*?---\s*", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"--- UNTRUSTED DATA END ---\s*\[NOTE:.*?\]", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"\[REDACTED(?:_API_KEY|_TOKEN)?\]", "", cleaned)

    # 2. Replace code blocks with concise spoken notice
    cleaned = re.sub(r"```[\w]*\n[\s\S]*?```", "Code omitted, displayed on screen.", cleaned)
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)

    # 3. Replace URLs with 'link'
    cleaned = re.sub(r"https?://\S+", "link", cleaned)

    # 4. Remove Markdown formatting
    cleaned = re.sub(r"^#{1,6}\s+", "", cleaned, flags=re.MULTILINE)  # headers
    cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)              # bold
    cleaned = re.sub(r"\*([^*]+)\*", r"\1", cleaned)                  # italic
    cleaned = re.sub(r"__([^_]+)__", r"\1", cleaned)                  # bold
    cleaned = re.sub(r"_([^_]+)_", r"\1", cleaned)                    # italic
    cleaned = re.sub(r"~~([^~]+)~~", r"\1", cleaned)                  # strikethrough

    # 5. Remove emojis and uncommon non-ASCII symbols
    cleaned = re.sub(r"[\U00010000-\U0010ffff]", "", cleaned)
    cleaned = re.sub(r"[^\x00-\x7F]+", " ", cleaned)

    # 6. Normalize whitespace
    cleaned = re.sub(r"[ \t]+", " ", cleaned).strip()

    # 7. Check if this is a long structured output (file list, multi-line stats)
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    bullet_items = [l for l in lines if l.startswith("- ") or l.startswith("* ")]

    if bullet_items:
        count = len(bullet_items)
        if "Found" in cleaned and "match" in cleaned:
            return f"I found {count} matching files. The full list is displayed on your screen, sir."
        elif "Directory contents" in cleaned:
            return f"I found {count} items in the directory, displayed on your screen, sir."
        else:
            return f"I have gathered the requested details with {count} items, shown on your screen, sir."

    # If output is too long (over 300 characters or more than 3 sentences), summarize spoken text
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    if len(sentences) > config.assistant.max_spoken_sentences or len(cleaned) > 280:
        spoken_parts = sentences[:config.assistant.max_spoken_sentences]
        spoken_summary = " ".join(spoken_parts).strip()
        if not spoken_summary.endswith((".", "!", "?")):
            spoken_summary += "."
        return spoken_summary

    return cleaned


class BaseTTSEngine(ABC):
    """Abstract interface for swappable text-to-speech engines."""

    @abstractmethod
    def speak(self, text: str, interrupt_event: Optional[threading.Event] = None) -> None:
        """Speaks the provided text. Aborts immediately if interrupt_event is set."""
        pass

    @abstractmethod
    def stop(self) -> None:
        """Immediately interrupts and halts any active speech."""
        pass


class WindowsTTSEngine(BaseTTSEngine):
    """Local Windows SAPI TTS engine via pyttsx3."""

    def __init__(
        self,
        rate: Optional[int] = None,
        volume: Optional[float] = None,
        voice: Optional[str] = None
    ):
        self.rate = rate or config.voice.tts_rate
        self.volume = volume if volume is not None else config.voice.tts_volume
        self.voice_name = voice or config.voice.tts_voice
        self._lock = threading.Lock()
        self._current_engine = None
        self._is_speaking = False

    def _get_configured_engine(self):
        import pyttsx3
        engine = pyttsx3.init()
        engine.setProperty("rate", self.rate)
        engine.setProperty("volume", self.volume)

        if self.voice_name:
            voices = engine.getProperty("voices")
            for v in voices:
                if self.voice_name.lower() in v.name.lower() or self.voice_name.lower() in v.id.lower():
                    engine.setProperty("voice", v.id)
                    break
        return engine

    def speak(self, text: str, interrupt_event: Optional[threading.Event] = None) -> None:
        """
        Speaks text cleanly through Windows SAPI.
        Checks interrupt_event to allow instant cut-off.
        """
        cleaned_text = clean_speech_text(text)
        if not cleaned_text:
            return

        if interrupt_event and interrupt_event.is_set():
            return

        with self._lock:
            try:
                import comtypes
                # Initialize COM for the calling thread
                try:
                    comtypes.CoInitialize()
                except Exception:
                    pass

                engine = self._get_configured_engine()
                self._current_engine = engine
                self._is_speaking = True

                # If an interrupt monitor event is provided, monitor in a helper thread
                interrupted = False
                if interrupt_event:
                    def monitor_interrupt():
                        nonlocal interrupted
                        while self._is_speaking and not interrupt_event.is_set():
                            threading.Event().wait(0.05)
                        if interrupt_event.is_set() and self._is_speaking:
                            interrupted = True
                            try:
                                engine.stop()
                            except Exception:
                                pass

                    monitor_thread = threading.Thread(target=monitor_interrupt, daemon=True)
                    monitor_thread.start()

                engine.say(cleaned_text)
                engine.runAndWait()

            except Exception as e:
                logger.error(f"Error in WindowsTTSEngine.speak: {e}")
            finally:
                self._is_speaking = False
                self._current_engine = None
                try:
                    comtypes.CoUninitialize()
                except Exception:
                    pass

    def stop(self) -> None:
        """Instantly stops active speech."""
        self._is_speaking = False
        if self._current_engine:
            try:
                self._current_engine.stop()
            except Exception:
                pass


class PiperTTSEngine(BaseTTSEngine):
    """Placeholder architecture stub for local neural Piper TTS."""

    def speak(self, text: str, interrupt_event: Optional[threading.Event] = None) -> None:
        raise NotImplementedError("Piper TTS engine is not yet implemented. Use 'windows'.")

    def stop(self) -> None:
        pass


class EdgeTTSEngine(BaseTTSEngine):
    """Placeholder architecture stub for cloud Edge TTS."""

    def speak(self, text: str, interrupt_event: Optional[threading.Event] = None) -> None:
        raise NotImplementedError("Edge TTS engine is not yet implemented. Use 'windows'.")

    def stop(self) -> None:
        pass


_tts_instance: Optional[BaseTTSEngine] = None


def get_tts_engine(engine_name: Optional[str] = None) -> BaseTTSEngine:
    """Factory function returning the active TTS engine singleton."""
    global _tts_instance
    if _tts_instance is None:
        selected = (engine_name or config.voice.tts_engine or "windows").lower()
        if selected in ("windows", "pyttsx3", "sapi"):
            _tts_instance = WindowsTTSEngine()
        elif selected == "piper":
            _tts_instance = PiperTTSEngine()
        elif selected == "edge":
            _tts_instance = EdgeTTSEngine()
        else:
            logger.warning(f"Unknown TTS engine '{selected}', defaulting to 'windows'.")
            _tts_instance = WindowsTTSEngine()

    return _tts_instance
