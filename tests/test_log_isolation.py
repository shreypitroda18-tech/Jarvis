"""Verification that test execution never modifies production logs or audit trails."""

import os
import time
from pathlib import Path
from jarvis.config import config, BASE_DIR, logger
from jarvis.safety import log_audit_entry, RiskLevel


def get_logs_directory_snapshot() -> dict[str, tuple[int, float]]:
    """Returns a dictionary of filename -> (size, mtime) for production logs/."""
    prod_logs = BASE_DIR / "logs"
    snapshot = {}
    if prod_logs.exists():
        for f in prod_logs.iterdir():
            if f.is_file():
                stat = f.stat()
                snapshot[f.name] = (stat.st_size, stat.st_mtime)
    return snapshot


def test_logs_directory_untouched_by_tests(isolate_session_logs_and_settings):
    """Asserts that running tests and logging operations leaves production logs/ unmodified."""
    before_snapshot = get_logs_directory_snapshot()

    # Perform logging and audit operations during test execution
    logger.info("Test isolation verification: writing test log entry")
    logger.warning("Test isolation verification: simulated warning")
    log_audit_entry(
        tool_name="test_tool",
        risk_level=RiskLevel.LOW,
        arguments={"action": "test_isolation"},
        status="SUCCESS",
        details="Verifying test isolation"
    )

    time.sleep(0.05)

    after_snapshot = get_logs_directory_snapshot()

    # Assert exact match of files, sizes, and modification times in production logs/
    assert before_snapshot == after_snapshot, (
        f"Production logs/ was modified during test run! Before: {before_snapshot}, After: {after_snapshot}"
    )

    # Verify that the test output went to the isolated temporary files
    tmp_jarvis_log = isolate_session_logs_and_settings["tmp_jarvis_log"]
    tmp_audit_log = isolate_session_logs_and_settings["tmp_audit_log"]

    assert tmp_jarvis_log.exists()
    assert tmp_audit_log.exists()
    assert "Test isolation verification" in tmp_jarvis_log.read_text(encoding="utf-8")
    assert "test_isolation" in tmp_audit_log.read_text(encoding="utf-8")
