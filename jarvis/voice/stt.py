"""Local Speech-to-Text (STT) layer powered by faster-whisper and sounddevice."""

import time
import math
import logging
import threading
import re
from typing import Optional, Tuple, List, Dict, Any
import numpy as np

from jarvis.config import config, logger

SILENCE_HALLUCINATIONS = {
    "thank you", "thank you.", "thank you!", "thank you very much", "thank you very much.",
    "you", "you.",
    "bye", "bye.", "goodbye", "goodbye.",
    "thank you for watching", "thank you for watching.", "thanks for watching", "thanks for watching.",
    "well that is it", "that is it", "thats it", "that's it", "that's it.",
    "subtitles by the amara.org community",
}


class STTEngine:
    """Local faster-whisper STT engine with CPU int8 default, CUDA auto-fallback, and silence detection."""

    def __init__(
        self,
        model_size: Optional[str] = None,
        device: Optional[str] = None,
        compute_type: Optional[str] = None
    ):
        self.model_size = model_size or config.voice.stt_model or "small"
        self.requested_device = (device or config.voice.stt_device or "cpu").lower()
        self.compute_type = compute_type or config.voice.stt_compute_type or "int8"
        self.sample_rate = 16000
        self.model = None
        self._lock = threading.Lock()
        self._ready_event = threading.Event()
        self._is_loading = False
        self._loading_thread: Optional[threading.Thread] = None

    def load_model(self) -> None:
        """Loads and preloads the Whisper model into memory with graceful fallback."""
        if self.model is not None:
            self._ready_event.set()
            return

        with self._lock:
            if self.model is not None:
                self._ready_event.set()
                return

            t0 = time.perf_counter()
            try:
                from faster_whisper import WhisperModel

                if self.requested_device in ("cuda", "auto"):
                    try:
                        logger.info(f"Attempting faster-whisper on CUDA with model '{self.model_size}'...")
                        c_type = "float16" if self.compute_type == "float16" else "int8_float16"
                        self.model = WhisperModel(self.model_size, device="cuda", compute_type=c_type)
                        logger.info("faster-whisper successfully initialized on CUDA GPU.")
                        return
                    except Exception as e:
                        logger.warning(
                            f"CUDA initialization failed for faster-whisper ({e}). Automatically falling back to CPU int8."
                        )

                # CPU fallback / default
                logger.info(f"Loading faster-whisper on CPU with compute_type='int8' (model: '{self.model_size}')...")
                self.model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
                logger.info("faster-whisper model preloaded successfully on CPU.")
            finally:
                dur = time.perf_counter() - t0
                try:
                    from jarvis.timing import tracker
                    tracker.record_stage("speech_model_load", dur, f"model={self.model_size} device={self.requested_device}")
                except Exception:
                    pass
                self._ready_event.set()

    def start_background_load(self) -> None:
        """Starts asynchronous background thread to preload the speech model."""
        if self.model is not None or self._is_loading:
            return
        self._is_loading = True

        def _worker():
            try:
                self.load_model()
            finally:
                self._is_loading = False

        self._loading_thread = threading.Thread(target=_worker, daemon=True)
        self._loading_thread.start()

    def wait_until_ready(self, timeout: Optional[float] = None) -> bool:
        """Waits until background model loading finishes."""
        if self._ready_event.is_set():
            return True
        return self._ready_event.wait(timeout=timeout)

    def record_audio(
        self,
        stop_event: Optional[threading.Event] = None,
        max_duration: Optional[float] = None,
        silence_timeout: Optional[float] = None,
        energy_threshold: Optional[float] = None,
        device: Optional[Any] = None
    ) -> np.ndarray:
        """
        Records audio from the microphone until silence is detected or max duration is reached.
        Never records when idle; the stream is opened strictly for the duration of this call and closed.
        Returns a 1D float32 numpy array sampled at 16kHz.
        """
        max_dur = max_duration or config.voice.max_utterance_seconds or 15.0
        silence_dur = silence_timeout or config.voice.silence_duration_seconds or 1.2
        threshold = energy_threshold or config.voice.energy_threshold or 0.015
        in_device = device if device is not None else config.voice.input_device

        chunk_duration = 0.1  # 100ms per block
        block_size = int(self.sample_rate * chunk_duration)
        chunks: List[np.ndarray] = []

        speech_started = False
        silence_start_time = None
        start_time = time.time()
        import sounddevice as sd

        try:
            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=block_size,
                device=in_device
            ) as stream:
                while True:
                    if stop_event and stop_event.is_set():
                        break

                    data, overflowed = stream.read(block_size)
                    if overflowed:
                        logger.debug("Audio buffer overflowed during capture.")

                    audio_chunk = data[:, 0]
                    chunks.append(audio_chunk)

                    # Compute RMS energy
                    rms = float(np.sqrt(np.mean(audio_chunk ** 2)))

                    now = time.time()
                    elapsed = now - start_time

                    if rms >= threshold:
                        speech_started = True
                        silence_start_time = None
                    else:
                        if speech_started:
                            if silence_start_time is None:
                                silence_start_time = now
                            elif now - silence_start_time >= silence_dur:
                                # Detected end of speech
                                break

                    # Hard duration limit
                    if elapsed >= max_dur:
                        logger.debug(f"Reached max utterance duration of {max_dur}s.")
                        break

        except Exception as e:
            logger.error(f"Error accessing microphone stream: {e}")
            raise RuntimeError(f"Microphone capture error: {e}. Check input device in settings.yaml or run --doctor.")

        if not chunks:
            return np.array([], dtype=np.float32)

        return np.concatenate(chunks, axis=0)

    def transcribe(self, audio: np.ndarray) -> str:
        """
        Transcribes a 1D float32 audio numpy array using faster-whisper.
        Audio array is processed in-memory and immediately released.
        """
        if audio is None or len(audio) == 0:
            return ""

        if self.model is None:
            if self._is_loading:
                sys_stdout = True
                try:
                    import sys
                    sys.stdout.write("\n[Waiting for speech model to finish loading...]\n")
                    sys.stdout.flush()
                except Exception:
                    pass
                self.wait_until_ready()
            else:
                self.load_model()

        try:
            segments, info = self.model.transcribe(
                audio,
                beam_size=5,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=500),
                language="en"
            )

            text_parts = [segment.text.strip() for segment in segments]
            result = " ".join(t for t in text_parts if t).strip()

            # Ignore known Whisper silence filler hallucinations
            norm_res = re.sub(r"[^\w\s]", "", result.lower()).strip()
            if norm_res in SILENCE_HALLUCINATIONS or result.lower().strip() in SILENCE_HALLUCINATIONS:
                logger.info(f"Filtered Whisper silence filler hallucination: '{result}'")
                return ""

            return result
        except Exception as e:
            logger.error(f"Transcription error: {e}")
            return ""

    def record_and_transcribe(
        self,
        stop_event: Optional[threading.Event] = None,
        max_duration: Optional[float] = None
    ) -> str:
        """Convenience method to capture utterance and transcribe directly."""
        audio = self.record_audio(stop_event=stop_event, max_duration=max_duration)
        if len(audio) < self.sample_rate * 0.3:  # Discard if under 300ms
            return ""
        rms = float(np.sqrt(np.mean(audio ** 2))) if len(audio) > 0 else 0.0
        threshold = config.voice.energy_threshold or 0.015
        if rms < threshold * 0.3 or rms < 0.002:
            logger.debug(f"Discarding near-silence audio recording (RMS: {rms:.5f})")
            return ""
        return self.transcribe(audio)


def measure_mic_energy(duration: float = 2.0, device: Optional[Any] = None) -> Tuple[float, float]:
    """
    Measures microphone RMS energy and peak dB over a short duration.
    Used by --doctor to verify microphone input health.
    """
    sample_rate = 16000
    in_device = device if device is not None else config.voice.input_device
    num_samples = int(sample_rate * duration)

    try:
        import sounddevice as sd
        recording = sd.rec(
            num_samples,
            samplerate=sample_rate,
            channels=1,
            dtype="float32",
            device=in_device
        )
        sd.wait()
        rms = float(np.sqrt(np.mean(recording ** 2)))
        peak = float(np.max(np.abs(recording)))
        peak_db = 20 * math.log10(max(peak, 1e-6))
        return rms, peak_db
    except Exception as e:
        logger.error(f"Failed to measure mic level: {e}")
        return 0.0, -100.0


def list_audio_devices() -> str:
    """Returns a formatted string listing all audio input and output devices."""
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        default_in, default_out = sd.default.device

        lines = ["\n--- Audio Hardware Devices ---"]
        lines.append(f"{'Idx':<4} {'Type':<8} {'Name':<42} {'Channels':<10}")
        lines.append("-" * 68)

        for idx, dev in enumerate(devices):
            is_in = dev.get("max_input_channels", 0) > 0
            is_out = dev.get("max_output_channels", 0) > 0

            dev_type = "In/Out" if (is_in and is_out) else ("Input" if is_in else "Output")
            name = dev.get("name", "Unknown")[:40]
            chans = f"In:{dev.get('max_input_channels', 0)} Out:{dev.get('max_output_channels', 0)}"

            marker = ""
            if idx == default_in and is_in:
                marker += " [Default In]"
            if idx == default_out and is_out:
                marker += " [Default Out]"

            lines.append(f"{idx:<4} {dev_type:<8} {name:<42} {chans:<10}{marker}")

        lines.append("-" * 68)
        return "\n".join(lines)
    except Exception as e:
        return f"Error querying audio devices: {e}"


_stt_instance: Optional[STTEngine] = None


def get_stt_engine() -> STTEngine:
    """Returns the singleton STTEngine instance."""
    global _stt_instance
    if _stt_instance is None:
        _stt_instance = STTEngine()
    return _stt_instance
