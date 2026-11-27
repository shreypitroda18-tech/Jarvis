from unittest.mock import MagicMock
from jarvis.local_commands import try_handle_local_command
from jarvis.config import config


def test_local_command_time():
    res = try_handle_local_command("what time is it?")
    assert res is not None
    assert "Current time is" in res

    res2 = try_handle_local_command("time")
    assert res2 is not None


def test_local_command_stats(mocker):
    mocker.patch("psutil.cpu_percent", return_value=12.0)
    mock_mem = MagicMock()
    mock_mem.used = 4 * (1024 ** 3)
    mock_mem.total = 16 * (1024 ** 3)
    mock_mem.percent = 25.0
    mocker.patch("psutil.virtual_memory", return_value=mock_mem)
    mocker.patch("psutil.sensors_battery", return_value=None)

    res = try_handle_local_command("system status")
    assert res is not None
    assert "CPU Usage: 12.0%" in res
    assert "RAM Usage" in res


def test_local_command_screenshot(mocker):
    mocker.patch("PIL.ImageGrab.grab")
    res = try_handle_local_command("take a screenshot please")
    assert res is not None
    assert "Screenshot captured" in res


def test_local_command_mute(mocker):
    mock_endpoint = MagicMock()
    mock_dev = MagicMock(EndpointVolume=mock_endpoint)
    mocker.patch("pycaw.pycaw.AudioUtilities.GetSpeakers", return_value=mock_dev)

    res_mute = try_handle_local_command("mute audio")
    assert res_mute is not None
    assert "Audio muted" in res_mute
    mock_endpoint.SetMute.assert_called_with(1, None)

    res_unmute = try_handle_local_command("unmute audio")
    assert res_unmute is not None
    assert "Audio unmuted" in res_unmute
    mock_endpoint.SetMute.assert_called_with(0, None)


def test_local_command_volume(mocker):
    mock_endpoint = MagicMock()
    mock_dev = MagicMock(EndpointVolume=mock_endpoint)
    mocker.patch("pycaw.pycaw.AudioUtilities.GetSpeakers", return_value=mock_dev)

    res = try_handle_local_command("set volume to 65")
    assert res is not None
    assert "65%" in res
    mock_endpoint.SetMasterVolumeLevelScalar.assert_called_with(0.65, None)


def test_local_command_open_whitelisted_app(mocker):
    mock_start = mocker.patch("jarvis.tools.system_tools.launch_indexed_app", return_value=(True, "Successfully launched Notepad."))
    res = try_handle_local_command("open notepad")
    assert res is not None
    assert "successfully launched" in res.lower() and "notepad" in res.lower()
    mock_start.assert_called_once()


def test_non_local_command_falls_through():
    # Complex queries or non-local requests must return None to reach the LLM
    res = try_handle_local_command("explain quantum entanglement in simple terms")
    assert res is None

    res2 = try_handle_local_command("open completely_unknown_software_xyz")
    assert res2 is None
