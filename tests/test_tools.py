import os
import unittest
from unittest.mock import MagicMock, patch
from jarvis.tools.system_tools import (
    open_app,
    open_website,
    google_search,
    youtube_search,
    get_time_date,
    system_stats,
    set_volume,
    mute,
    take_screenshot,
    media_control,
)


def test_open_app_whitelisted(mocker):
    mock_start = mocker.patch("jarvis.tools.system_tools.launch_indexed_app", return_value=(True, "Successfully launched Notepad."))
    result = open_app("notepad")
    assert "successfully launched" in result.lower() and "notepad" in result.lower()
    mock_start.assert_called_once()


def test_open_app_non_whitelisted(mocker):
    mock_popen = mocker.patch("subprocess.Popen")
    result = open_app("unapproved_script.exe")
    assert "no installed application matching" in result.lower()
    mock_popen.assert_not_called()


def test_open_website_favorite(mocker):
    mock_open = mocker.patch("webbrowser.open")
    result = open_website("google")
    assert "google.com" in result.lower()
    mock_open.assert_called_once_with("https://www.google.com")


def test_open_website_https(mocker):
    mock_open = mocker.patch("webbrowser.open")
    result = open_website("https://python.org")
    assert "python.org" in result.lower()
    mock_open.assert_called_once_with("https://python.org")


def test_open_website_rejected_prohibited_scheme(mocker):
    mock_open = mocker.patch("webbrowser.open")
    result = open_website("file:///C:/Windows/System32/cmd.exe")
    assert "access blocked" in result.lower()
    assert "scheme 'file' is prohibited" in result.lower()
    mock_open.assert_not_called()


def test_open_website_rejected_non_https(mocker):
    mock_open = mocker.patch("webbrowser.open")
    # By default, mock confirmation input will be 'no'
    result = open_website("http://insecure-site.com")
    assert "cancelled" in result.lower()
    mock_open.assert_not_called()


def test_google_search(mocker):
    mock_open = mocker.patch("webbrowser.open")
    result = google_search("artificial intelligence")
    assert "Opened Google search for 'artificial intelligence'" in result
    mock_open.assert_called_once_with("https://www.google.com/search?q=artificial+intelligence")


def test_youtube_search(mocker):
    mock_open = mocker.patch("webbrowser.open")
    result = youtube_search("lofi hip hop")
    assert "Opened YouTube search for 'lofi hip hop'" in result
    mock_open.assert_called_once_with("https://www.youtube.com/results?search_query=lofi+hip+hop")


def test_get_time_date():
    result = get_time_date()
    assert "Current time is" in result
    assert "202" in result  # Year in 2020s


def test_system_stats(mocker):
    mocker.patch("psutil.cpu_percent", return_value=18.5)
    mock_mem = MagicMock()
    mock_mem.used = 8 * (1024 ** 3)
    mock_mem.total = 16 * (1024 ** 3)
    mock_mem.percent = 50.0
    mocker.patch("psutil.virtual_memory", return_value=mock_mem)

    mock_battery = MagicMock()
    mock_battery.percent = 85.0
    mock_battery.power_plugged = True
    mocker.patch("psutil.sensors_battery", return_value=mock_battery)

    stats = system_stats()
    assert "CPU Usage: 18.5%" in stats
    assert "RAM Usage: 8.0 GB / 16.0 GB (50.0%)" in stats
    assert "85% (plugged in)" in stats


def test_set_volume_clamping(mocker):
    mock_endpoint = MagicMock()
    mock_device = MagicMock()
    mock_device.EndpointVolume = mock_endpoint
    mocker.patch("pycaw.pycaw.AudioUtilities.GetSpeakers", return_value=mock_device)

    # Test negative volume clamped to 0
    res_low = set_volume(-15)
    assert "0%" in res_low
    mock_endpoint.SetMasterVolumeLevelScalar.assert_called_with(0.0, None)

    # Test > 100 clamped to 100
    res_high = set_volume(150)
    assert "100%" in res_high
    mock_endpoint.SetMasterVolumeLevelScalar.assert_called_with(1.0, None)


def test_media_control(mocker):
    mock_press = mocker.patch("pyautogui.press")
    
    # Valid action
    res_play = media_control("play")
    assert "Media control executed: play" in res_play
    mock_press.assert_called_with("playpause")

    # Invalid action
    res_invalid = media_control("explode")
    assert "Unsupported media action" in res_invalid
