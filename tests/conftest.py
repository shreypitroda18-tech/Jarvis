"""Global pytest fixtures and test environment isolation for JARVIS."""

import os
import shutil
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from unittest.mock import patch
import pytest

from jarvis.config import config, BASE_DIR
import jarvis.safety
import jarvis.config


@pytest.fixture(scope="session", autouse=True)
def isolate_session_logs_and_settings(tmp_path_factory):
    """Session-wide isolation redirecting all logging, audit logs, and settings to a temp directory.

    Guarantees that running the pytest suite never touches or mutates production logs/jarvis.log,
    logs/audit.log, or jarvis/settings.yaml.
    """
    tmp_dir = tmp_path_factory.mktemp("jarvis_test_env")
    tmp_logs = tmp_dir / "logs"
    tmp_logs.mkdir(parents=True, exist_ok=True)
    tmp_jarvis_log = tmp_logs / "jarvis.log"
    tmp_audit_log = tmp_logs / "audit.log"

    # Copy real settings.yaml to temp settings
    real_settings = jarvis.config.SETTINGS_PATH
    tmp_settings = tmp_dir / "settings.yaml"
    if real_settings.exists():
        shutil.copy2(real_settings, tmp_settings)
    elif jarvis.config.SETTINGS_EXAMPLE_PATH.exists():
        shutil.copy2(jarvis.config.SETTINGS_EXAMPLE_PATH, tmp_settings)
    else:
        tmp_settings.write_text("{}", encoding="utf-8")

    # Save original paths
    orig_audit_path = jarvis.safety.AUDIT_LOG_PATH
    orig_settings_path = jarvis.config.SETTINGS_PATH
    orig_config_log_file = config.safety.log_file
    orig_config_audit_file = config.safety.audit_file

    # Patch modules and config
    jarvis.safety.AUDIT_LOG_PATH = tmp_audit_log
    jarvis.config.SETTINGS_PATH = tmp_settings
    config.safety.log_file = str(tmp_jarvis_log)
    config.safety.audit_file = str(tmp_audit_log)

    # Reconfigure logger handlers
    logger = logging.getLogger("jarvis")
    old_file_handlers = [h for h in logger.handlers if isinstance(h, (logging.FileHandler, RotatingFileHandler))]
    for h in old_file_handlers:
        try:
            h.flush()
            h.close()
        except Exception:
            pass
        logger.removeHandler(h)

    # Attach temporary file handler
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    test_file_handler = RotatingFileHandler(
        tmp_jarvis_log,
        maxBytes=5242880,
        backupCount=2,
        encoding="utf-8"
    )
    test_file_handler.setFormatter(formatter)
    logger.addHandler(test_file_handler)

    yield {
        "tmp_dir": tmp_dir,
        "tmp_logs": tmp_logs,
        "tmp_jarvis_log": tmp_jarvis_log,
        "tmp_audit_log": tmp_audit_log,
        "tmp_settings": tmp_settings,
    }

    # Teardown: close test handler and restore
    try:
        test_file_handler.flush()
        test_file_handler.close()
    except Exception:
        pass
    logger.removeHandler(test_file_handler)
    jarvis.safety.AUDIT_LOG_PATH = orig_audit_path
    jarvis.config.SETTINGS_PATH = orig_settings_path
    config.safety.log_file = orig_config_log_file
    config.safety.audit_file = orig_config_audit_file


@pytest.fixture(autouse=True)
def mock_default_safe_foreground():
    """Default fixture mocking foreground window and process lookup across all tests.

    Ensures control tests (desktop, browser, operator) never inspect or depend on
    the real OS foreground window (such as Windows Terminal or PowerShell when running tests).
    Specific tests can override this by explicitly patching get_foreground_app_info or is_dangerous_foreground.
    """
    from jarvis.control.safety import action_budget
    action_budget.reset()
    safe_info = ("notepad.exe", "Untitled - Notepad", "Notepad")
    with patch("jarvis.control.safety.get_foreground_app_info", return_value=safe_info), \
         patch("jarvis.control.desktop.get_foreground_app_info", return_value=safe_info), \
         patch("jarvis.control.uia_observer.get_foreground_app_info", return_value=safe_info), \
         patch("win32gui.GetForegroundWindow", return_value=12345), \
         patch("win32gui.GetWindowText", return_value="Untitled - Notepad"), \
         patch("win32gui.GetClassName", return_value="Notepad"):
        yield
