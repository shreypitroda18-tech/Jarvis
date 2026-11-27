import io
import sys
from unittest.mock import MagicMock
from jarvis.tools.file_tools import list_files, find_file, open_file
from jarvis.tools.power_tools import system_power, cancel_shutdown
from jarvis.config import BASE_DIR


def test_list_files_allowed():
    res = list_files(str(BASE_DIR))
    assert "Directory contents" in res
    assert "jarvis" in res


def test_list_files_disallowed():
    res = list_files(r"C:\Windows\System32")
    assert "outside approved directories" in res


def test_find_file_allowed():
    res = find_file("*.yaml", str(BASE_DIR))
    assert "settings.yaml" in res


def test_find_file_disallowed():
    res = find_file("*.dll", r"C:\Windows")
    assert "Search aborted" in res
    assert "outside approved directories" in res


def test_open_file_gate_blocks_on_no(mocker, monkeypatch):
    mock_startfile = mocker.patch("os.startfile")
    # Simulate user answering "no" to the confirmation prompt
    monkeypatch.setattr(sys, "stdin", io.StringIO("no\n"))

    test_file = BASE_DIR / "README.md"
    result = open_file(str(test_file))

    assert "Action cancelled" in result
    assert "declined by user" in result
    mock_startfile.assert_not_called()


def test_open_file_gate_allows_on_yes(mocker, monkeypatch):
    mock_startfile = mocker.patch("os.startfile")
    # Simulate user answering "yes" to the confirmation prompt
    monkeypatch.setattr(sys, "stdin", io.StringIO("yes\n"))

    test_file = BASE_DIR / "README.md"
    result = open_file(str(test_file))

    assert "Opened file 'README.md' successfully" in result
    mock_startfile.assert_called_once_with(str(test_file.resolve()))


def test_system_power_blocks_on_no(mocker, monkeypatch):
    mock_run = mocker.patch("subprocess.run")
    # Simulate user answering "no" to the HIGH-risk shutdown prompt
    monkeypatch.setattr(sys, "stdin", io.StringIO("no\n"))

    result = system_power("shutdown")

    assert "Action aborted" in result
    assert "confirmation was denied" in result
    # Crucial: verify that shutdown command was NEVER called
    mock_run.assert_not_called()


def test_system_power_allows_on_yes(mocker, monkeypatch):
    mock_run = mocker.patch("subprocess.run")
    # Simulate user answering "yes"
    monkeypatch.setattr(sys, "stdin", io.StringIO("yes\n"))

    result = system_power("shutdown")

    assert "System shutdown initiated" in result
    mock_run.assert_called_once()
    args, kwargs = mock_run.call_args
    assert "shutdown.exe" in args[0]
    assert "/s" in args[0]


def test_system_power_invalid_action():
    result = system_power("self_destruct")
    assert "Invalid power action" in result


def test_cancel_shutdown(mocker):
    mock_run = mocker.patch("subprocess.run")
    result = cancel_shutdown()
    assert "cancelled" in result
    mock_run.assert_called_once()
    args, _ = mock_run.call_args
    assert args[0] == ["shutdown.exe", "/a"]


def test_project_dir_in_allowed_paths():
    from jarvis.config import config
    norm_base = str(BASE_DIR.resolve()).lower()
    allowed_norms = [str(p).lower() for p in config.allowed_paths]
    assert norm_base in allowed_norms or str(BASE_DIR).lower() in allowed_norms


def test_find_file_bare_word_wildcard():
    # 'settings' should automatically become '*settings*'
    res = find_file("settings")
    assert "settings.yaml" in res
    assert "Found" in res


def test_find_file_natural_language_phrase():
    # 'my settings file' should have filler stripped and match settings.yaml
    res = find_file("my settings file")
    assert "settings.yaml" in res
    assert "Found" in res

    # 'Find my settings file' should also have 'Find' stripped and match
    res2 = find_file("Find my settings file")
    assert "settings.yaml" in res2
    assert "Found" in res2


def test_find_file_case_insensitive():
    res = find_file("SETTINGS")
    assert "settings.yaml" in res


def test_find_file_max_10_matches(tmp_path, monkeypatch):
    from jarvis.config import config
    # Create a temporary directory inside allowed paths with 15 matching files
    test_dir = tmp_path / "test_folder"
    test_dir.mkdir()
    for i in range(15):
        (test_dir / f"test_item_{i:02d}.txt").write_text("hello")

    monkeypatch.setattr(config, "allowed_paths", [test_dir])
    res = find_file("test_item", str(test_dir))
    assert "Found 10 match(es)" in res
    # Ensure not more than 10 lines of items
    matches_count = len([line for line in res.splitlines() if line.strip().startswith("- ")])
    assert matches_count == 10


def test_list_files_omits_protected_paths():
    res = list_files(str(BASE_DIR))
    # Must contain normal files/dirs
    assert "[DIR] jarvis" in res
    assert "README.md" in res or "settings.yaml" in res
    # Must NOT contain protected directories
    assert "[DIR] .git" not in res
    assert "[DIR] logs" not in res
    assert "[DIR] screenshots" not in res
    assert "[DIR] browser_profile" not in res
    assert "[DIR] API key" not in res
    # Must NOT contain protected files (.env, credentials.json)
    lines = [line.strip() for line in res.splitlines()]
    file_names = [line.split()[0] for line in lines if line and not line.startswith("[DIR]") and not line.startswith("---") and not line.startswith("Directory")]
    assert ".env" not in file_names
    assert "credentials.json" not in file_names


def test_list_files_blocks_protected_directories():
    res = list_files("logs")
    assert "Unable to list directory" in res
    assert "Access denied" in res

    res_traversal = list_files("jarvis/../logs")
    assert "Unable to list directory" in res_traversal
    assert "Access denied" in res_traversal


def test_find_file_omits_protected_files():
    # Wildcard search for .env should never return .env or .env.local
    res = find_file(".env*")
    lines = [line.strip() for line in res.splitlines() if line.strip().startswith("- ")]
    for line in lines:
        assert not line.endswith("\\.env") and not line.endswith("/.env")
        assert not line.endswith("\\.env.local") and not line.endswith("/.env.local")

    # Wildcard search for keys should not return protected .key files
    res_key = find_file("*.key")
    key_lines = [line.strip() for line in res_key.splitlines() if line.strip().startswith("- ")]
    for line in key_lines:
        assert not line.endswith(".key")


def test_find_file_blocks_protected_search_directory():
    res = find_file("*.log", search_directory="logs")
    assert "Search aborted" in res
    assert "Access denied" in res

    res_traversal = find_file("*.log", search_directory="jarvis/../logs")
    assert "Search aborted" in res_traversal
    assert "Access denied" in res_traversal


def test_open_file_blocks_protected_targets(mocker):
    mock_startfile = mocker.patch("os.startfile")

    # Direct sensitive files
    res = open_file(".env")
    assert "Cannot open file" in res
    assert "Access denied" in res

    res_trav = open_file("jarvis/../.env")
    assert "Cannot open file" in res_trav
    assert "Access denied" in res_trav

    res_cred = open_file("credentials.json")
    assert "Cannot open file" in res_cred
    assert "Access denied" in res_cred

    res_log = open_file("logs/jarvis.log")
    assert "Cannot open file" in res_log
    assert "Access denied" in res_log

    res_key = open_file("API key/token.txt")
    assert "Cannot open file" in res_key
    assert "Access denied" in res_key

    mock_startfile.assert_not_called()


