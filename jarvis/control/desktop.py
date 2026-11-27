"""Desktop UI automation wrapper using Windows UI Automation (pywinauto) and pyautogui."""

import re
import time
from typing import Optional, List, Dict, Any
import win32gui
import pyautogui

from jarvis.config import config, logger
from jarvis.safety import RiskLevel, confirm_action
from jarvis.control.safety import (
    is_dangerous_foreground,
    get_foreground_app_info,
    is_password_element,
    sanitize_typed_text,
    validate_key_combo,
    classify_action_risk,
    wrap_untrusted_content,
    record_action_audit,
    action_budget,
)
from jarvis.control.killswitch import kill_switch, ControlAbortedException


class DesktopManager:
    """Manages Windows UI Automation and safe desktop input injection."""

    def __init__(self):
        self._uia = None

    def _get_desktop(self):
        if self._uia is None:
            from pywinauto import Desktop
            self._uia = Desktop(backend="uia")
        return self._uia

    def get_active_window(self) -> str:
        """Returns the title and process name of the current active window."""
        kill_switch.check_abort()
        proc, title, _ = get_foreground_app_info()
        if not title and not proc:
            return "No foreground window detected, sir."
        res = f"Active window: '{title}' (Process: {proc})"
        record_action_audit("get_active_window", res, "", "SUCCESS")
        return res

    def list_controls(self) -> str:
        """Lists interactive controls (buttons, edit boxes, tabs) in the active window."""
        kill_switch.check_abort()

        # Check dangerous window
        is_bad, reason = is_dangerous_foreground()
        if is_bad:
            record_action_audit("list_controls", reason, "", "BLOCKED", reason)
            return f"Access denied: {reason}"

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return "No foreground window is currently active, sir."

        try:
            from pywinauto import Application
            app = Application(backend="uia").connect(handle=hwnd)
            window = app.window(handle=hwnd)

            controls = []
            seen = set()

            for child in window.descendants():
                kill_switch.check_abort()
                try:
                    text = child.window_text().strip()
                    ctrl_type = child.friendly_class_name()
                    if text and len(text) < 60 and text not in seen:
                        # Only show actionable controls (Button, Edit, TabItem, ListItem, CheckBox)
                        if ctrl_type in ("Button", "Edit", "TabItem", "ListItem", "CheckBox", "RadioButton", "MenuItem"):
                            controls.append(f"[{ctrl_type}] {text}")
                            seen.add(text)
                            if len(controls) >= 25:
                                break
                except Exception:
                    continue

            if not controls:
                return "No accessible interactive controls found in active window, sir."

            formatted = "\n".join(controls)
            record_action_audit("list_controls", window.window_text(), formatted, "SUCCESS")
            return wrap_untrusted_content(f"Controls in '{window.window_text()}':\n{formatted}")
        except Exception as e:
            logger.error(f"Failed to list controls: {e}")
            return f"Could not list controls for active window: {str(e)}"

    def click_control(self, name: str) -> str:
        """Finds a control by name in the active window and clicks it."""
        if not action_budget.increment():
            return "Action limit exceeded (maximum 15 actions per request, sir)."

        kill_switch.check_abort()

        # 1. Check dangerous process
        is_bad, reason = is_dangerous_foreground()
        if is_bad:
            record_action_audit("click_control", name, "", "BLOCKED", reason)
            return f"Access denied: {reason}"

        if getattr(config, "dry_run", False):
            return f"[DRY-RUN] Would click desktop control '{name}', sir."

        # 2. Risk Evaluation & Confirmation Gate
        risk = classify_action_risk("click_control", name)
        if risk == RiskLevel.HIGH:
            prompt = f"Action on '{name}' involves high-risk operation. Confirm click?"
            if not confirm_action(RiskLevel.HIGH, prompt):
                record_action_audit("click_control", name, "", "DENIED", "User denied confirmation")
                return f"Click on '{name}' was cancelled, sir."
        elif risk == RiskLevel.MEDIUM and config.safety.require_confirmation_for_medium:
            prompt = f"Click control '{name}' in active window?"
            if not confirm_action(RiskLevel.MEDIUM, prompt):
                record_action_audit("click_control", name, "", "DENIED", "User denied confirmation")
                return f"Click on '{name}' was cancelled, sir."

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return "No foreground window active, sir."

        with kill_switch.controlling_session(f"Clicking desktop control '{name}'"):
            try:
                from pywinauto import Application
                app = Application(backend="uia").connect(handle=hwnd)
                window = app.window(handle=hwnd)

                # Search descendants
                target_ctrl = None
                name_lower = name.lower()

                for child in window.descendants():
                    kill_switch.check_abort()
                    try:
                        c_text = child.window_text().lower()
                        if name_lower == c_text or name_lower in c_text:
                            target_ctrl = child
                            break
                    except Exception:
                        continue

                if target_ctrl is None:
                    # Fallback to direct child_window regex search
                    try:
                        target_ctrl = window.child_window(title_re=f".*{re.escape(name)}.*")
                    except Exception:
                        target_ctrl = None

                if target_ctrl is not None:
                    try:
                        target_ctrl.click_input()
                    except Exception:
                        target_ctrl.invoke()
                    record_action_audit("click_control", name, "", "SUCCESS")
                    return f"Successfully clicked control '{name}', sir."
                else:
                    return f"Could not locate control matching '{name}' in active window, sir."
            except Exception as e:
                record_action_audit("click_control", name, "", "ERROR", str(e))
                return f"Failed to click control '{name}': {str(e)}"

    def type_into(self, control: str, text: str) -> str:
        """Types text into a named control or active input area (refuses password fields)."""
        if not action_budget.increment():
            return "Action limit exceeded (maximum 15 actions per request, sir)."

        kill_switch.check_abort()

        # 1. Dangerous Foreground Check
        is_bad, reason = is_dangerous_foreground()
        if is_bad:
            record_action_audit("type_into", control, text, "BLOCKED", reason)
            return f"Access denied: {reason}"

        if getattr(config, "dry_run", False):
            return f"[DRY-RUN] Would type into '{control}', sir."

        # 2. Sanitize Text (strip newlines unless confirmed)
        clean_text = sanitize_typed_text(text)

        # 3. Locate Target Control
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return "No foreground window active, sir."

        from pywinauto import Application
        app = Application(backend="uia").connect(handle=hwnd)
        window = app.window(handle=hwnd)

        target_ctrl = None
        control_lower = control.lower()

        for child in window.descendants():
            kill_switch.check_abort()
            try:
                c_text = child.window_text().lower()
                if control_lower in c_text or child.friendly_class_name() == "Edit":
                    target_ctrl = child
                    if control_lower in c_text:
                        break
            except Exception:
                continue

        # 4. Check for Password Field Refusal
        is_pwd = False
        if target_ctrl is not None:
            try:
                elem = target_ctrl.element_info
                is_pwd = getattr(elem, "CurrentIsPassword", False) or elem.name.lower().find("password") != -1
            except Exception:
                is_pwd = False

        if is_pwd or "password" in control_lower:
            record_action_audit("type_into", control, clean_text, "BLOCKED", "Refused password field")
            return "Refusing to type into password field for security reasons, sir."

        # 5. Risk & Confirmation Gate
        risk = classify_action_risk("type_into", control, clean_text)
        prompt = f"Type text into '{control}' ({len(clean_text)} chars)?"
        if not confirm_action(risk, prompt):
            record_action_audit("type_into", control, clean_text, "DENIED", "User denied confirmation")
            return f"Typing into '{control}' was cancelled, sir."

        with kill_switch.controlling_session(f"Typing into '{control}'"):
            try:
                if target_ctrl is not None:
                    try:
                        target_ctrl.set_focus()
                        target_ctrl.type_keys(clean_text, with_spaces=True, with_newlines=False)
                    except Exception:
                        pyautogui.write(clean_text, interval=0.02)
                else:
                    pyautogui.write(clean_text, interval=0.02)

                record_action_audit("type_into", control, clean_text, "SUCCESS")
                return f"Successfully typed into '{control}', sir."
            except Exception as e:
                record_action_audit("type_into", control, clean_text, "ERROR", str(e))
                return f"Failed to type into '{control}': {str(e)}"

    def press_keys(self, keys: str) -> str:
        """Sends allowed keyboard shortcuts or keys to the active window."""
        if not action_budget.increment():
            return "Action limit exceeded (maximum 15 actions per request, sir)."

        kill_switch.check_abort()

        # 1. Dangerous Foreground Check
        is_bad, reason = is_dangerous_foreground()
        if is_bad:
            record_action_audit("press_keys", keys, "", "BLOCKED", reason)
            return f"Access denied: {reason}"

        # 2. Key Combo Validation
        valid, reason = validate_key_combo(keys)
        if not valid:
            record_action_audit("press_keys", keys, "", "BLOCKED", reason)
            return f"Key press blocked: {reason}"

        if getattr(config, "dry_run", False):
            return f"[DRY-RUN] Would press keys '{keys}', sir."

        # 3. Enter/Tab triggers confirmation
        risk = RiskLevel.MEDIUM if keys.lower() in ("enter", "tab") else RiskLevel.LOW
        if risk == RiskLevel.MEDIUM and config.safety.require_confirmation_for_medium:
            if not confirm_action(RiskLevel.MEDIUM, f"Press key combo '{keys}' in desktop?"):
                record_action_audit("press_keys", keys, "", "DENIED", "User denied confirmation")
                return f"Key press '{keys}' was cancelled, sir."

        with kill_switch.controlling_session(f"Pressing '{keys}'"):
            try:
                parts = [p.strip().lower() for p in keys.split("+")]
                if len(parts) > 1:
                    pyautogui.hotkey(*parts)
                else:
                    pyautogui.press(parts[0])

                record_action_audit("press_keys", keys, "", "SUCCESS")
                return f"Pressed '{keys}' in active window, sir."
            except Exception as e:
                record_action_audit("press_keys", keys, "", "ERROR", str(e))
                return f"Failed to press '{keys}': {str(e)}"

    def focus_window(self, name: str) -> str:
        """Brings the window matching the given name to the foreground."""
        kill_switch.check_abort()
        if not name or not name.strip():
            return "Please specify a window title to focus, sir."

        if getattr(config, "dry_run", False):
            return f"[DRY-RUN] Would focus window '{name}', sir."

        clean_name = name.strip().lower()
        desktop = self._get_desktop()

        with kill_switch.controlling_session(f"Focusing window '{name}'"):
            try:
                for win in desktop.windows():
                    kill_switch.check_abort()
                    title = win.window_text()
                    if clean_name in title.lower():
                        win.set_focus()
                        record_action_audit("focus_window", title, "", "SUCCESS")
                        return f"Focused window '{title}', sir."
                return f"No open window matching '{name}' was found, sir."
            except Exception as e:
                return f"Could not focus window '{name}': {str(e)}"

    def scroll(self, direction: str = "down") -> str:
        """Scrolls the mouse wheel in the active window ('up' or 'down')."""
        if not action_budget.increment():
            return "Action limit exceeded (maximum 15 actions per request, sir)."

        kill_switch.check_abort()

        is_bad, reason = is_dangerous_foreground()
        if is_bad:
            record_action_audit("desktop_scroll", direction, "", "BLOCKED", reason)
            return f"Access denied: {reason}"

        if getattr(config, "dry_run", False):
            return f"[DRY-RUN] Would scroll active window {direction}, sir."
        clicks = -300 if direction.lower() == "down" else 300
        with kill_switch.controlling_session(f"Desktop scrolling {direction}"):
            try:
                pyautogui.scroll(clicks)
                record_action_audit("desktop_scroll", direction, "", "SUCCESS")
                return f"Scrolled active window {direction}, sir."
            except Exception as e:
                return f"Failed to scroll window: {str(e)}"


# Global desktop manager instance
desktop = DesktopManager()


# Tool function wrappers with short descriptions
def get_active_window() -> str:
    """Returns the title and process name of the current active window."""
    return desktop.get_active_window()


def list_controls() -> str:
    """Lists interactive controls (buttons, textboxes, tabs) in the active window."""
    return desktop.list_controls()


def click_control(name: str) -> str:
    """Clicks an interactive control matching name in the active window."""
    return desktop.click_control(name)


def type_into(control: str, text: str) -> str:
    """Types text into an input control in the active window (refuses password fields)."""
    return desktop.type_into(control, text)


def press_keys(keys: str) -> str:
    """Sends keyboard shortcuts or keys to the active window (e.g. 'ctrl+c', 'enter')."""
    return desktop.press_keys(keys)


def focus_window(name: str) -> str:
    """Focuses and brings an open application window to the foreground."""
    return desktop.focus_window(name)


def desktop_scroll(direction: str = "down") -> str:
    """Scrolls the mouse wheel up or down in the active window."""
    return desktop.scroll(direction)
