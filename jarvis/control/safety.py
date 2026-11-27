"""Strict code-enforced safety model for browser and desktop computer control."""

import re
import urllib.parse
from typing import Tuple, Optional
import win32gui
import win32process
import psutil

from jarvis.config import config, logger
from jarvis.safety import RiskLevel, confirm_action, log_audit_entry, is_path_protected

DANGEROUS_PROCESSES = {
    "cmd.exe",
    "powershell.exe",
    "pwsh.exe",
    "windowsterminal.exe",
    "wt.exe",
    "taskmgr.exe",
    "regedit.exe",
}

DANGEROUS_WINDOW_KEYWORDS = {
    "command prompt",
    "powershell",
    "windows terminal",
    "task manager",
    "registry editor",
    "setup",
    "installer",
    "uninstaller",
}


class ActionBudget:
    """Tracks and limits the number of automated computer actions per request turn."""
    def __init__(self, max_actions: int = 15):
        self.max_actions = max_actions
        self.current_count = 0

    def reset(self):
        self.current_count = 0

    def increment(self) -> bool:
        if self.current_count >= self.max_actions:
            return False
        self.current_count += 1
        return True

    @property
    def remaining(self) -> int:
        return max(0, self.max_actions - self.current_count)


# Global budget instance
action_budget = ActionBudget(max_actions=15)


def wrap_untrusted_content(content: str) -> str:
    """Wraps external browser or desktop content in untrusted delimiters."""
    return f"[UNTRUSTED_CONTENT_START]\n{content.strip()}\n[UNTRUSTED_CONTENT_END]"


def get_foreground_app_info() -> Tuple[str, str, str]:
    """Returns (process_name, window_title, window_class) for active foreground window."""
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return "", "", ""
    try:
        title = win32gui.GetWindowText(hwnd)
        wnd_class = win32gui.GetClassName(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        proc = psutil.Process(pid)
        return proc.name().lower(), title, wnd_class
    except Exception as e:
        logger.debug(f"Could not inspect foreground window: {e}")
        return "", "", ""


def is_dangerous_foreground() -> Tuple[bool, str]:
    """
    Checks if the active foreground window is an administrative shell,
    system utility, installer, or Run dialog.
    """
    proc_name, title, wnd_class = get_foreground_app_info()
    title_lower = title.lower()

    # 1. Process Name Check
    if proc_name in DANGEROUS_PROCESSES:
        return True, f"Foreground process '{proc_name}' is a protected administrative shell."

    # 2. Run Dialog Check (explorer.exe with class #32770 and title 'Run')
    if proc_name == "explorer.exe" and (wnd_class == "#32770" or title_lower == "run"):
        return True, "Windows Run dialog is strictly protected from automated input."

    # 3. Installer / Uninstaller Check
    for kw in ("setup", "installer", "unins000", "msiexec"):
        if kw in proc_name:
            return True, f"Process '{proc_name}' appears to be an installer/setup utility."

    # 4. Window Title Keyword Check
    for kw in DANGEROUS_WINDOW_KEYWORDS:
        if kw in title_lower:
            return True, f"Active window title '{title}' is classified as a protected administrative utility."

    return False, ""


def extract_domain(url_or_domain: str) -> str:
    """Extracts clean base domain (e.g. 'netflix.com' or 'youtube.com')."""
    raw = url_or_domain.strip().lower()
    if "://" not in raw:
        raw = f"https://{raw}"
    try:
        parsed = urllib.parse.urlparse(raw)
        host = parsed.hostname or ""
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


def validate_browser_domain(url_or_domain: str, is_opening: bool = False) -> Tuple[bool, str]:
    """
    Validates browser domain against allowlist and blocklist.
    - If is_opening is True: blocklisted domains ask for typed confirmation before opening.
    - If is_opening is False (automation/operation): operating inside blocklisted domains is ALWAYS refused.
    - Asks for typed confirmation if an allowed domain is encountered for the first time.
    """
    domain = extract_domain(url_or_domain)
    if not domain:
        return False, "Invalid or empty domain specified."

    # 1. Check Hard Blocklist (Banking, Payment, Email, Messaging, Password Managers)
    all_blocked = set(config.automation.automation_blocked_domains)
    if hasattr(config, "operator"):
        all_blocked.update(config.operator.blocked_domains)

    for blocked in all_blocked:
        if domain == blocked or domain.endswith(f".{blocked}"):
            if is_opening:
                prompt = f"Opening blocklisted sensitive domain '{domain}' (banking/payment/messaging). Confirm to open in browser?"
                confirmed = confirm_action(RiskLevel.HIGH, prompt)
                if confirmed:
                    log_audit_entry("validate_domain", RiskLevel.HIGH, {"domain": domain}, "APPROVED", "User authorized opening blocklisted domain")
                    return True, "Opening blocklisted domain authorized by user."
                else:
                    log_audit_entry("validate_domain", RiskLevel.HIGH, {"domain": domain}, "DENIED", "User denied opening blocklisted domain")
                    return False, f"Opening sensitive domain '{domain}' was cancelled, sir."
            else:
                msg = f"Operation refused: Domain '{domain}' is blocked by financial/credential security policy."
                log_audit_entry("validate_domain", RiskLevel.HIGH, {"domain": domain}, "BLOCKED", msg)
                return False, msg

    if is_opening:
        # Merely opening a non-blocklisted URL in browser does not require automation authorization
        return True, "Domain allowed for browsing."

    # 2. Check Allowed Domains List
    all_allowed = set(config.automation.automation_allowed_domains)
    if hasattr(config, "operator"):
        all_allowed.update(config.operator.approved_domains)

    for allowed in all_allowed:
        if domain == allowed or domain.endswith(f".{allowed}"):
            return True, "Domain allowed."

    # 3. First-Time Domain: Requires explicit TYPED approval
    prompt = f"First-time automation requested on domain '{domain}'. Authorize browser automation on this site?"
    confirmed = confirm_action(RiskLevel.HIGH, prompt)
    if confirmed:
        # Add to runtime allowed list so subsequent actions on this site proceed
        config.automation.automation_allowed_domains.append(domain)
        if hasattr(config, "operator") and domain not in config.operator.approved_domains:
            config.operator.approved_domains.append(domain)
        log_audit_entry("validate_domain", RiskLevel.HIGH, {"domain": domain}, "APPROVED", "User authorized domain")
        return True, "Domain authorized by user."
    else:
        log_audit_entry("validate_domain", RiskLevel.HIGH, {"domain": domain}, "DENIED", "User rejected domain")
        return False, f"Authorization for domain '{domain}' was denied, sir."


def validate_app_name(app_name: str) -> Tuple[bool, str]:
    """
    Validates an application before automation interaction.
    Asks for typed approval if encountering a new app for the first time.
    """
    clean_app = app_name.strip().lower()
    if not clean_app:
        return False, "Empty application name."

    # 1. Blocked apps check
    blocked_apps = getattr(config.operator, "blocked_apps", []) if hasattr(config, "operator") else []
    for blocked in blocked_apps:
        if clean_app == blocked.lower() or clean_app.startswith(blocked.lower()):
            msg = f"Application '{app_name}' is blocked by security policy."
            log_audit_entry("validate_app", RiskLevel.HIGH, {"app": app_name}, "BLOCKED", msg)
            return False, msg

    # 2. Pre-approved / Whitelisted check
    approved_apps = getattr(config.operator, "approved_apps", []) if hasattr(config, "operator") else []
    if clean_app in [a.lower() for a in approved_apps]:
        return True, "App pre-approved."

    # Whitelist from config.apps (e.g. notepad, calc) are treated as safe standard apps
    if clean_app in config.apps or clean_app in ("notepad", "calculator", "calc", "settings"):
        return True, "Standard app approved."

    # 3. First-time app prompt
    prompt = f"First-time automation requested for application '{app_name}'. Authorize automation on this app?"
    confirmed = confirm_action(RiskLevel.HIGH, prompt)
    if confirmed:
        if hasattr(config, "operator"):
            config.operator.approved_apps.append(clean_app)
        log_audit_entry("validate_app", RiskLevel.HIGH, {"app": app_name}, "APPROVED", "User authorized app")
        return True, "App authorized by user."
    else:
        log_audit_entry("validate_app", RiskLevel.HIGH, {"app": app_name}, "DENIED", "User rejected app")
        return False, f"Authorization for application '{app_name}' was denied, sir."


def validate_typed_text(text: str) -> Tuple[bool, str]:
    """
    Verifies that text about to be typed does NOT contain protected paths or secrets.
    Reuses is_path_protected to reject credential and key leakage.
    """
    if not text:
        return True, ""

    text_lower = text.lower()

    # Direct sensitive tokens check
    sensitive_tokens = [
        ".env", "credentials.json", "token.json", "id_rsa", ".pem", ".key",
        "browser_profile", "api key", ".git", "logs/", "screenshots/"
    ]
    for token in sensitive_tokens:
        if token in text_lower:
            msg = f"Typing blocked: Text contains protected credential token '{token}'."
            log_audit_entry("validate_typed_text", RiskLevel.HIGH, {"token": token}, "BLOCKED", msg)
            return False, msg

    # Path check via is_path_protected
    is_prot, prot_reason = is_path_protected(text)
    if is_prot:
        msg = f"Typing blocked: '{text}' is a protected path ({prot_reason})."
        log_audit_entry("validate_typed_text", RiskLevel.HIGH, {"text": text[:30]}, "BLOCKED", msg)
        return False, msg

    return True, ""


def is_password_element(element_info: dict) -> bool:
    """Detects if a DOM element or desktop control represents a password field."""
    elem_type = str(element_info.get("type", "")).lower()
    elem_name = str(element_info.get("name", "")).lower()
    elem_id = str(element_info.get("id", "")).lower()
    elem_aria = str(element_info.get("aria_label", "")).lower()
    is_pwd_prop = bool(element_info.get("is_password", False))

    if is_pwd_prop or elem_type == "password":
        return True

    for pattern in ("password", "passwd", "pwd", "secret", "cvv", "passcode"):
        if pattern in elem_name or pattern in elem_id or pattern in elem_aria:
            return True

    return False


def sanitize_typed_text(text: str, allow_newlines: bool = False) -> str:
    """Strips newlines from typed text unless explicitly confirmed."""
    if allow_newlines:
        return text
    # Replace newlines with space to prevent accidental command execution
    cleaned = re.sub(r"[\r\n]+", " ", text)
    return cleaned.strip()


def validate_key_combo(keys: str) -> Tuple[bool, str]:
    """Validates keystrokes against allowed and forbidden combinations."""
    norm = keys.strip().lower().replace(" ", "")
    if not norm:
        return False, "Empty key combo specified."

    # 1. Strictly Forbidden Combinations
    for forbidden in config.automation.forbidden_key_combos:
        if norm == forbidden.replace(" ", ""):
            return False, f"Key combo '{keys}' is strictly blocked by security policy."

    # 2. Check Allowed Whitelist
    for allowed in config.automation.allowed_key_combos:
        if norm == allowed.replace(" ", ""):
            return True, "Key combo allowed."

    # Single character keys are allowed (e.g. 'a', '1', 'f')
    if len(norm) == 1:
        return True, "Single key allowed."

    return False, f"Key combo '{keys}' is not permitted by automation policy."


def classify_action_risk(action_name: str, target: str, text: str = "") -> RiskLevel:
    """
    Classifies the operational risk of a computer action:
    - HIGH: keywords matching buy, pay, delete, submit, download, install, subscribe, etc.
    - MEDIUM: typing text (typing into controls, browser inputs)
    - LOW: ordinary clicks (click, click_control, browser_click), scroll, focus, wait, read
    """
    combined = f"{action_name} {target} {text}".lower()

    high_risk_words = [
        "send", "post", "buy", "pay", "subscribe", "submit", "delete",
        "remove", "download", "install", "uninstall", "confirm purchase", "sign out",
        "order", "transfer"
    ]
    for kw in high_risk_words:
        if re.search(rf"\b{re.escape(kw)}\b", combined):
            return RiskLevel.HIGH

    for kw in config.automation.high_risk_keywords:
        if re.search(rf"\b{re.escape(kw)}\b", combined):
            return RiskLevel.HIGH

    # Typing text into controls or inputs is MEDIUM risk (requires confirmation)
    if action_name in ("type", "type_into", "type_text", "browser_type") or text:
        return RiskLevel.MEDIUM

    # Ordinary clicks, navigation, and inspection are LOW risk once domain/app is authorized
    return RiskLevel.LOW


def record_action_audit(tool_name: str, target_desc: str, text: str = "", status: str = "SUCCESS", details: str = ""):
    """Logs action to audit log with text length only (never raw sensitive text)."""
    log_audit_entry(
        tool_name=tool_name,
        risk_level=RiskLevel.LOW,
        arguments={
            "target": target_desc[:80],
            "text_length": len(text) if text else 0,
            "status": status,
            "info": details[:100],
        },
        status=status,
        details=details
    )
