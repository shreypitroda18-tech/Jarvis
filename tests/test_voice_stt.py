import pytest
import numpy as np
from unittest.mock import MagicMock, patch
from jarvis.voice.stt import STTEngine, measure_mic_energy, list_audio_devices


def test_stt_hardware_fallback_to_cpu(mocker):
    """
    Verifies that requesting CUDA when cuDNN/CUDA DLLs fail automatically
    falls back to CPU with int8 without crashing.
    """
    engine = STTEngine(model_size="tiny", device="cuda", compute_type="float16")

    def mock_whisper_init(model_size, device, compute_type):
        if device == "cuda":
            raise RuntimeError("Library cublas64_12.dll not found")
        # CPU initialization succeeds
        mock_model = MagicMock()
        mock_model.device = device
        mock_model.compute_type = compute_type
        return mock_model

    mocker.patch("faster_whisper.WhisperModel", side_effect=mock_whisper_init)

    engine.load_model()
    assert engine.model is not None
    assert engine.model.device == "cpu"
    assert engine.model.compute_type == "int8"


def test_stt_transcribe_mocked_audio(mocker):
    engine = STTEngine(model_size="small", device="cpu", compute_type="int8")

    mock_segment_1 = MagicMock()
    mock_segment_1.text = "Hello JARVIS"
    mock_segment_2 = MagicMock()
    mock_segment_2.text = "what time is it"

    mock_model = MagicMock()
    mock_model.transcribe.return_value = ([mock_segment_1, mock_segment_2], MagicMock())
    engine.model = mock_model

    fake_audio = np.zeros(16000, dtype=np.float32)
    result = engine.transcribe(fake_audio)

    assert result == "Hello JARVIS what time is it"
    mock_model.transcribe.assert_called_once()
    args, kwargs = mock_model.transcribe.call_args
    assert kwargs["vad_filter"] is True


def test_stt_transcribe_empty_audio_returns_empty():
    engine = STTEngine()
    assert engine.transcribe(np.array([], dtype=np.float32)) == ""
    assert engine.transcribe(None) == ""


def test_list_audio_devices_returns_string(mocker):
    fake_devices = [
        {"name": "Mic Array", "max_input_channels": 2, "max_output_channels": 0},
        {"name": "Speakers", "max_input_channels": 0, "max_output_channels": 2}
    ]
    mocker.patch("sounddevice.query_devices", return_value=fake_devices)
    mocker.patch.object(mocker.patch("sounddevice.default"), "device", (0, 1))

    dev_str = list_audio_devices()
    assert "Audio Hardware Devices" in dev_str
    assert "Mic Array" in dev_str
    assert "Speakers" in dev_str
    assert "[Default In]" in dev_str
    assert "[Default Out]" in dev_str


def test_measure_mic_energy(mocker):
    fake_recording = np.full((16000, 1), 0.05, dtype=np.float32)
    mocker.patch("sounddevice.rec", return_value=fake_recording)
    mocker.patch("sounddevice.wait")

    rms, db = measure_mic_energy(duration=1.0)
    assert rms > 0.0
    assert isinstance(db, float)


@pytest.mark.parametrize("hallucination", [
    "Thank you.",
    "Thank you!",
    "Thank you for watching.",
    "Thanks for watching",
    "You",
    "Bye",
    "That's it.",
])
def test_stt_transcribe_filters_silence_hallucinations(mocker, hallucination):
    engine = STTEngine(model_size="small", device="cpu", compute_type="int8")
    mock_segment = MagicMock()
    mock_segment.text = hallucination
    mock_model = MagicMock()
    mock_model.transcribe.return_value = ([mock_segment], MagicMock())
    engine.model = mock_model

    fake_audio = np.full(16000, 0.05, dtype=np.float32)
    result = engine.transcribe(fake_audio)
    assert result == ""


def test_record_and_transcribe_discards_near_silence(mocker):
    engine = STTEngine()
    # Mock record_audio returning near-silence (RMS ~ 0.0001)
    silent_audio = np.full(16000, 0.0001, dtype=np.float32)
    mocker.patch.object(engine, "record_audio", return_value=silent_audio)
    mock_transcribe = mocker.patch.object(engine, "transcribe")

    res = engine.record_and_transcribe()
    assert res == ""
    mock_transcribe.assert_not_called()


def test_doctor_warns_on_zero_rms_microphone(mocker, capsys):
    from jarvis.main import run_doctor
    mocker.patch("jarvis.voice.stt.measure_mic_energy", return_value=(0.0, -100.0))
    mocker.patch("jarvis.voice.stt.list_audio_devices", return_value="Mock Devices")
    mocker.patch("jarvis.voice.tts.get_tts_engine")
    mocker.patch("jarvis.voice.stt.get_stt_engine")
    mocker.patch("jarvis.brain.JarvisBrain")
    mocker.patch("jarvis.tools.app_discovery.load_apps_index", return_value={})
    mocker.patch("jarvis.tools.system_tools.open_website", return_value="prohibited for security")

    run_doctor()
    captured = capsys.readouterr().out
    assert "Microphone returned zero signal (RMS 0.0000)" in captured


def test_mic_test_records_and_outputs(mocker, capsys):
    from jarvis.main import run_mic_test
    mock_stt = MagicMock()
    mock_stt.record_audio.return_value = np.full(16000, 0.05, dtype=np.float32)
    mock_stt.transcribe.return_value = "testing one two"
    mocker.patch("jarvis.voice.stt.get_stt_engine", return_value=mock_stt)

    run_mic_test()
    captured = capsys.readouterr().out
    assert "JARVIS Microphone Test" in captured
    assert "Loudness Bar" in captured
    assert "I heard: testing one two" in captured
    assert "Audio buffer discarded." in captured

