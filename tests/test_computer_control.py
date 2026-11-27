"""Unit tests for Step B: Controlled Computer Control, strict safety model, and provider gating."""

import pytest
from unittest.mock import MagicMock, patch

from jarvis.safety import RiskLevel
from jarvis.config import config
from jarvis.control.safety import (
    is_dangerous_foreground,
    validate_browser_domain,
    is_password_element,
    sanitize_typed_text,
    validate_key_combo,
    classify_action_risk,
    wrap_untrusted_content,
    action_budget,
)
from jarvis.control.browser import (
    browser_open,
    browser_click,
    browser_type,
    browser_press,
    browser_read_page,
)
from jarvis.control.desktop import (
    get_active_window,
    click_control,
    type_into,
    press_keys,
)
from jarvis.llm.fallback import FallbackManager, COMPUTER_CONTROL_TOOL_NAMES
from jarvis.llm.base import ToolCall, ProviderResponse


# -----------------------------------------------------------------------------
# Safety Model & Domain Tests
# -----------------------------------------------------------------------------

def test_blocked_terminal_processes(mocker):
    """Verifies that terminal and administrative processes are blocked from input."""
    # Test cmd.exe
    mocker.patch("jarvis.control.safety.get_foreground_app_info", return_value=("cmd.exe", "Command Prompt", "ConsoleWindowClass"))
    is_bad, reason = is_dangerous_foreground()
    assert is_bad is True
    assert "administrative shell" in reason

    # Test powershell.exe
    mocker.patch("jarvis.control.safety.get_foreground_app_info", return_value=("powershell.exe", "Windows PowerShell", ""))
    is_bad, reason = is_dangerous_foreground()
    assert is_bad is True

    # Test Windows Terminal
    mocker.patch("jarvis.control.safety.get_foreground_app_info", return_value=("windowsterminal.exe", "Windows Terminal", ""))
    is_bad, reason = is_dangerous_foreground()
    assert is_bad is True

    # Test Run dialog
    mocker.patch("jarvis.control.safety.get_foreground_app_info", return_value=("explorer.exe", "Run", "#32770"))
    is_bad, reason = is_dangerous_foreground()
    assert is_bad is True
    assert "Run dialog" in reason

    # Test Task Manager
    mocker.patch("jarvis.control.safety.get_foreground_app_info", return_value=("taskmgr.exe", "Task Manager", ""))
    is_bad, reason = is_dangerous_foreground()
    assert is_bad is True


def test_safe_process_permitted(mocker):
    """Verifies safe everyday apps (e.g. notepad.exe, chrome.exe) are permitted."""
    mocker.patch("jarvis.control.safety.get_foreground_app_info", return_value=("notepad.exe", "Untitled - Notepad", "Notepad"))
    is_bad, reason = is_dangerous_foreground()
    assert is_bad is False
    assert reason == ""


def test_banking_and_payment_domains_blocked():
    """Verifies banking, payment, and password manager domains are unconditionally blocked."""
    blocked_domains = [
        "https://www.paypal.com/signin",
        "https://chase.com/dashboard",
        "https://stripe.com/pay",
        "https://vault.bitwarden.com",
        "https://my.1password.com",
    ]
    for url in blocked_domains:
        allowed, reason = validate_browser_domain(url)
        assert allowed is False
        assert "financial/credential security policy" in reason


def test_unknown_domain_requires_typed_confirmation(mocker):
    """Verifies first-time/unknown domains prompt for explicit typed confirmation."""
    mock_confirm = mocker.patch("jarvis.control.safety.confirm_action", return_value=False)
    allowed, reason = validate_browser_domain("https://unknown-news-site.org/article")
    assert allowed is False
    assert "denied" in reason.lower()
    mock_confirm.assert_called_once()
    assert mock_confirm.call_args[0][0] == RiskLevel.HIGH

    # If user confirms, domain should be approved
    mocker.patch("jarvis.control.safety.confirm_action", return_value=True)
    allowed_approved, reason_appr = validate_browser_domain("https://another-new-blog.io")
    assert allowed_approved is True


def test_pre_approved_domains_allowed():
    """Verifies whitelisted domains (YouTube, Netflix, Wikipedia) are allowed directly."""
    for url in ["https://www.youtube.com", "https://netflix.com/browse", "https://en.wikipedia.org"]:
        allowed, reason = validate_browser_domain(url)
        assert allowed is True


# -----------------------------------------------------------------------------
# Password Refusal & Text Sanitization Tests
# -----------------------------------------------------------------------------

def test_password_element_refusal():
    """Verifies password fields are detected across DOM types and UIA properties."""
    assert is_password_element({"type": "password", "name": "user_pass"}) is True
    assert is_password_element({"type": "text", "name": "password_input"}) is True
    assert is_password_element({"type": "text", "aria_label": "Enter your secret passcode"}) is True
    assert is_password_element({"is_password": True}) is True
    assert is_password_element({"type": "text", "name": "search_query"}) is False


def test_sanitize_typed_text_strips_newlines():
    """Verifies newlines and carriage returns are stripped from input text."""
    raw = "rmdir /s /q temp\r\nformat c:\n"
    sanitized = sanitize_typed_text(raw)
    assert "\n" not in sanitized
    assert "\r" not in sanitized
    assert sanitized == "rmdir /s /q temp format c:"


def test_key_combo_validation():
    """Verifies allowed key whitelist and strictly forbidden key combinations."""
    # Whitelisted
    ok, _ = validate_key_combo("ctrl+c")
    assert ok is True
    ok_enter, _ = validate_key_combo("enter")
    assert ok_enter is True

    # Forbidden
    bad_win_r, reason_r = validate_key_combo("win+r")
    assert bad_win_r is False
    assert "strictly blocked" in reason_r

    bad_alt_f4, _ = validate_key_combo("alt+f4")
    assert bad_alt_f4 is False

    bad_cad, _ = validate_key_combo("ctrl+alt+del")
    assert bad_cad is False


# -----------------------------------------------------------------------------
# High-Risk Keywords & Confirmation Gates
# -----------------------------------------------------------------------------

def test_classify_action_risk_high_keywords():
    """Verifies sensitive keywords elevate actions to HIGH risk (typed confirmation)."""
    assert classify_action_risk("click", "Confirm & Pay Now") == RiskLevel.HIGH
    assert classify_action_risk("click", "Delete Account") == RiskLevel.HIGH
    assert classify_action_risk("click", "Submit Order") == RiskLevel.HIGH
    assert classify_action_risk("click", "Download Installer") == RiskLevel.HIGH
    assert classify_action_risk("click", "Subscribe to plan") == RiskLevel.HIGH

    # Safe interactions
    assert classify_action_risk("browser_read_page", "") == RiskLevel.LOW
    assert classify_action_risk("desktop_scroll", "down") == RiskLevel.LOW
    assert classify_action_risk("type_into", "search_box", "cat videos") == RiskLevel.MEDIUM


def test_high_risk_desktop_click_requires_typed_confirmation(mocker):
    """Verifies clicking a 'Delete' button requires HIGH confirmation."""
    mocker.patch("jarvis.control.desktop.is_dangerous_foreground", return_value=(False, ""))
    mock_confirm = mocker.patch("jarvis.control.desktop.confirm_action", return_value=False)

    res = click_control("Delete All Files")
    assert "cancelled" in res.lower()
    mock_confirm.assert_called_once()
    assert mock_confirm.call_args[0][0] == RiskLevel.HIGH


def test_desktop_action_blocked_in_terminal(mocker):
    """Verifies type_into or click_control is blocked if foreground is a terminal."""
    mocker.patch("jarvis.control.desktop.is_dangerous_foreground", return_value=(True, "Protected administrative shell"))
    res = type_into("Terminal Window", "dangerous command")
    assert "access denied" in res.lower()
    assert "protected administrative shell" in res.lower()


def test_action_budget_limit():
    """Verifies that exceeding the 15-action budget stops further operations."""
    action_budget.reset()
    for _ in range(15):
        assert action_budget.increment() is True
    assert action_budget.increment() is False


# -----------------------------------------------------------------------------
# Fallback Provider & Ollama Gating Tests
# -----------------------------------------------------------------------------

def test_computer_control_tools_omitted_for_ollama(mocker):
    """Verifies that Ollama receives a tool schema without computer control tools."""
    dummy_tools = [browser_open, click_control, type_into]
    fb_manager = FallbackManager(system_instruction="test", tools=dummy_tools)

    # Cloud schema includes browser and desktop tools
    cloud_tool_names = {t["function"]["name"] for t in fb_manager.cloud_tools_schema}
    assert "browser_open" in cloud_tool_names
    assert "click_control" in cloud_tool_names

    # Local Ollama schema strictly excludes them
    local_tool_names = {t["function"]["name"] for t in fb_manager.local_tools_schema}
    for tool_name in COMPUTER_CONTROL_TOOL_NAMES:
        assert tool_name not in local_tool_names


def test_computer_control_tool_execution_blocked_on_ollama_provider(mocker):
    """
    Verifies that if an Ollama provider outputs a computer control tool call,
    execution is blocked and the required explanatory message is returned.
    """
    dummy_tools = [browser_open, get_active_window]
    fb_manager = FallbackManager(system_instruction="test", tools=dummy_tools)

    # Mock Ollama provider
    mock_ollama = MagicMock()
    mock_ollama.name = "ollama"
    mock_ollama.is_available.return_value = (True, "Ready")
    mock_ollama.chat.return_value = ProviderResponse(
        content=None,
        tool_calls=[ToolCall(id="call_1", name="browser_open", arguments={"url": "https://example.com"})]
    )

    fb_manager.providers = [mock_ollama]
    mocker.patch.object(fb_manager, "_call_provider_with_retry", return_value=mock_ollama.chat.return_value)

    # Execute turn
    fb_manager.execute_turn("open example.com")

    # Inspect tool response recorded in history
    tool_msgs = [m for m in fb_manager.history.messages if m.role == "tool"]
    assert len(tool_msgs) == 1
    content = tool_msgs[0].content
    assert "disabled on local Ollama models" in content
    assert "spatial grounding" in content
