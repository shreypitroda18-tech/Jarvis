"""Unit tests for Windows App Discovery, fuzzy matching, and administrative safety gates."""

import pytest
from unittest.mock import MagicMock
from jarvis.safety import RiskLevel
from jarvis.tools.app_discovery import (
    find_matching_apps,
    launch_indexed_app,
)
from jarvis.tools.system_tools import open_app


@pytest.fixture
def mock_apps_index():
    return {
        "notepad": {
            "name": "Notepad",
            "target": "notepad.exe",
            "type": "exe",
            "source": "whitelist"
        },
        "calculator": {
            "name": "Calculator",
            "target": "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App",
            "type": "uwp",
            "source": "store"
        },
        "google chrome": {
            "name": "Google Chrome",
            "target": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            "type": "exe",
            "source": "shortcut"
        },
        "blackmagic raw speed test": {
            "name": "Blackmagic RAW Speed Test",
            "target": r"C:\Program Files\Blackmagic Design\Blackmagic RAW Speed Test.exe",
            "type": "exe",
            "source": "shortcut"
        },
        "blackmagic proxy generator": {
            "name": "Blackmagic Proxy Generator",
            "target": r"C:\Program Files\Blackmagic Design\Blackmagic Proxy Generator.exe",
            "type": "exe",
            "source": "shortcut"
        },
        "command prompt": {
            "name": "Command Prompt",
            "target": "cmd.exe",
            "type": "exe",
            "source": "shortcut"
        },
        "windows powershell": {
            "name": "Windows PowerShell",
            "target": "powershell.exe",
            "type": "exe",
            "source": "shortcut"
        },
        "registry editor": {
            "name": "Registry Editor",
            "target": "regedit.exe",
            "type": "exe",
            "source": "shortcut"
        }
    }


def test_find_matching_apps_exact(mock_apps_index):
    status, matches = find_matching_apps("notepad", index=mock_apps_index)
    assert status == "exact"
    assert len(matches) == 1
    assert matches[0]["name"] == "Notepad"


def test_find_matching_apps_substring(mock_apps_index):
    status, matches = find_matching_apps("calc", index=mock_apps_index)
    assert status in ("single", "exact")
    assert matches[0]["name"] == "Calculator"


def test_find_matching_apps_ambiguous(mock_apps_index):
    status, matches = find_matching_apps("blackmagic", index=mock_apps_index)
    assert status == "multiple"
    assert len(matches) == 2
    names = [m["name"] for m in matches]
    assert "Blackmagic RAW Speed Test" in names
    assert "Blackmagic Proxy Generator" in names


def test_find_matching_apps_none(mock_apps_index):
    status, matches = find_matching_apps("xyz123_nonexistent_software", index=mock_apps_index)
    assert status == "none"
    assert len(matches) == 0


def test_open_app_blocked_policy(mocker):
    # 'uninstall' or 'format' are blocked apps in settings.yaml
    mock_start = mocker.patch("jarvis.tools.system_tools.launch_indexed_app")
    res = open_app("uninstaller")
    assert "strictly prohibited by security policy" in res
    mock_start.assert_not_called()

    res_format = open_app("format")
    assert "strictly prohibited by security policy" in res_format
    mock_start.assert_not_called()


def test_open_app_confirm_app_denied(mocker, mock_apps_index):
    mocker.patch("jarvis.tools.system_tools.find_matching_apps", return_value=("exact", [mock_apps_index["command prompt"]]))
    mock_confirm = mocker.patch("jarvis.tools.system_tools.confirm_action", return_value=False)
    mock_start = mocker.patch("jarvis.tools.system_tools.launch_indexed_app")

    res = open_app("cmd")
    assert "cancelled" in res.lower()
    mock_confirm.assert_called_once()
    assert mock_confirm.call_args[0][0] == RiskLevel.HIGH
    mock_start.assert_not_called()


def test_open_app_confirm_app_approved(mocker, mock_apps_index):
    mocker.patch("jarvis.tools.system_tools.find_matching_apps", return_value=("exact", [mock_apps_index["command prompt"]]))
    mock_confirm = mocker.patch("jarvis.tools.system_tools.confirm_action", return_value=True)
    mock_start = mocker.patch("jarvis.tools.system_tools.launch_indexed_app", return_value=(True, "Successfully launched Command Prompt."))

    res = open_app("cmd")
    assert "successfully launched" in res.lower()
    mock_confirm.assert_called_once()
    mock_start.assert_called_once()


def test_open_app_ambiguous_prompts_user(mocker, mock_apps_index):
    bm_matches = [mock_apps_index["blackmagic raw speed test"], mock_apps_index["blackmagic proxy generator"]]
    mocker.patch("jarvis.tools.system_tools.find_matching_apps", return_value=("multiple", bm_matches))
    mock_start = mocker.patch("jarvis.tools.system_tools.launch_indexed_app")

    res = open_app("blackmagic")
    assert "multiple applications matching" in res.lower()
    assert "Blackmagic RAW Speed Test" in res
    assert "Blackmagic Proxy Generator" in res
    assert "Which one would you like to open?" in res
    mock_start.assert_not_called()


def test_launch_indexed_app_uwp(mocker):
    mock_startfile = mocker.patch("os.startfile", create=True)
    app_info = {
        "name": "Calculator",
        "target": "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App",
        "type": "uwp"
    }
    success, msg = launch_indexed_app(app_info)
    assert success is True
    assert "Successfully launched Calculator" in msg
    mock_startfile.assert_called_once_with(r"shell:AppsFolder\Microsoft.WindowsCalculator_8wekyb3d8bbwe!App")


def test_launch_indexed_app_never_passes_args(mocker):
    mock_startfile = mocker.patch("os.startfile", create=True)
    app_info = {
        "name": "Notepad",
        "target": "notepad.exe --malicious-flag secret.txt",
        "type": "exe"
    }
    success, msg = launch_indexed_app(app_info)
    assert success is True
    # Verify that arguments were stripped and only the binary was targeted
    mock_startfile.assert_called_once_with("notepad.exe")
