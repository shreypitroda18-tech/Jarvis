"""Windows UI Automation observer and element tree extractor."""

import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple, Any
from pathlib import Path
import win32gui
import win32process
import psutil

from jarvis.config import config, logger
from jarvis.safety import is_path_protected
from jarvis.control.safety import (
    is_dangerous_foreground,
    get_foreground_app_info,
    extract_domain,
    validate_browser_domain,
    is_password_element,
    wrap_untrusted_content,
)
from jarvis.control.calculator_mapping import extract_calculator_display


@dataclass
class ObservedElement:
    element_id: int
    role: str
    name: str
    automation_id: str
    is_enabled: bool
    is_password: bool = False
    state: str = "normal"
    text: str = ""
    native_element: Any = None

    def to_compact_string(self) -> str:
        """Renders element as a concise single line for LLM prompts."""
        if self.is_password:
            return f"[{self.element_id}] {self.role}: [PASSWORD_FIELD - HIDDEN] (auto_id: '{self.automation_id}') [PROTECTED - NO TYPING]"

        parts = [f"[{self.element_id}] {self.role}: '{self.name[:45]}'"]
        if self.automation_id:
            parts.append(f"(auto_id: '{self.automation_id[:35]}')")
        state_flags = []
        if not self.is_enabled:
            state_flags.append("disabled")
        if self.state and self.state != "normal":
            state_flags.append(self.state)
        if state_flags:
            parts.append(f"[{', '.join(state_flags)}]")
        if self.text and self.text != self.name:
            clean_txt = self.text.replace("\n", " ").strip()
            if clean_txt:
                parts.append(f"text: '{clean_txt[:40]}'")
        return " ".join(parts)


@dataclass
class WindowObservation:
    window_title: str
    process_name: str
    hwnd: int
    is_blocked: bool = False
    blocked_reason: str = ""
    current_url: Optional[str] = None
    current_domain: Optional[str] = None
    calculator_display: Optional[str] = None
    accessibility_notice: Optional[str] = None
    elements: List[ObservedElement] = field(default_factory=list)

    def to_prompt_text(self) -> str:
        """Formats the observation into a secure, compact prompt block."""
        if self.is_blocked:
            return f"[BLOCKED WINDOW] {self.blocked_reason}"

        lines = [
            f"Active Window: '{self.window_title}' (Process: {self.process_name})"
        ]
        if self.current_url:
            lines.append(f"Browser URL: {self.current_url} (Domain: {self.current_domain})")
        if self.calculator_display:
            lines.append(f"Calculator Display: {self.calculator_display}")
        if self.accessibility_notice:
            lines.append(f"Notice: {self.accessibility_notice}")

        lines.append(f"\nVisible Actionable Controls ({len(self.elements)} elements):")
        if not self.elements:
            lines.append("(No accessible interactive controls found in this window)")
        else:
            for elem in self.elements:
                lines.append(elem.to_compact_string())

        raw_block = "\n".join(lines)
        return wrap_untrusted_content(raw_block)


def is_file_explorer_protected_path(hwnd: int, title: str) -> Tuple[bool, str]:
    """Inspects File Explorer address bar or title to prevent access to protected paths."""
    title_lower = title.lower()
    for prot in (".env", ".git", "logs", "screenshots", "browser_profile", "api key"):
        if prot in title_lower:
            return True, f"File Explorer is displaying protected directory '{prot}'"
    return False, ""


def is_dialog_blocked(wnd_class: str, title: str) -> Tuple[bool, str]:
    """Checks if window is an Open/Save dialog or credential dialog."""
    t_lower = title.lower()
    if wnd_class == "#32770":
        for kw in ("open", "save", "save as", "browse", "credential", "security"):
            if kw in t_lower:
                return True, f"File dialog or security window '{title}' is protected from automation."
    return False, ""


class UIAObserver:
    """Performs safe, compact observation of foreground Windows application elements."""

    def __init__(self):
        self._uia_app = None

    def observe(self, max_elements: int = 60) -> WindowObservation:
        """Inspects active window and returns structured observation."""
        hwnd = win32gui.GetForegroundWindow()
        proc_name, title, wnd_class = get_foreground_app_info()
        if not proc_name and not title:
            if not hwnd:
                return WindowObservation(
                    window_title="",
                    process_name="",
                    hwnd=0,
                    is_blocked=True,
                    blocked_reason="No active foreground window found."
                )
            try:
                title = win32gui.GetWindowText(hwnd).strip()
                wnd_class = win32gui.GetClassName(hwnd)
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                proc_name = psutil.Process(pid).name().lower()
            except Exception as e:
                return WindowObservation(
                    window_title="Unknown",
                    process_name="unknown",
                    hwnd=hwnd,
                    is_blocked=True,
                    blocked_reason=f"Failed to inspect foreground process: {e}"
                )

        # 1. Blocked Process & Window Checks
        is_bad, bad_reason = is_dangerous_foreground()
        if is_bad:
            return WindowObservation(
                window_title=title,
                process_name=proc_name,
                hwnd=hwnd,
                is_blocked=True,
                blocked_reason=bad_reason
            )

        # 2. Blocked Dialogs (File Open/Save, Credential prompts)
        is_dlg, dlg_reason = is_dialog_blocked(wnd_class, title)
        if is_dlg:
            return WindowObservation(
                window_title=title,
                process_name=proc_name,
                hwnd=hwnd,
                is_blocked=True,
                blocked_reason=dlg_reason
            )

        # 3. File Explorer Protected Path Check
        if proc_name == "explorer.exe":
            is_prot, prot_reason = is_file_explorer_protected_path(hwnd, title)
            if is_prot:
                return WindowObservation(
                    window_title=title,
                    process_name=proc_name,
                    hwnd=hwnd,
                    is_blocked=True,
                    blocked_reason=prot_reason
                )

        # 4. Check operator blocked_apps from settings
        for b_app in config.operator.blocked_apps:
            if b_app.lower() in proc_name or proc_name.startswith(b_app.lower()):
                return WindowObservation(
                    window_title=title,
                    process_name=proc_name,
                    hwnd=hwnd,
                    is_blocked=True,
                    blocked_reason=f"Application '{proc_name}' is on the operator blocked apps list."
                )

        obs = WindowObservation(
            window_title=title,
            process_name=proc_name,
            hwnd=hwnd
        )

        try:
            from pywinauto import Application
            app = Application(backend="uia").connect(handle=hwnd)
            window = app.window(handle=hwnd)
        except Exception as e:
            logger.debug(f"pywinauto connect failed on hwnd {hwnd}: {e}")
            obs.accessibility_notice = f"Could not connect UIA: {e}"
            return obs

        # 5. Extract Browser URL if Browser (Chrome / Edge / Brave)
        is_browser = proc_name in ("chrome.exe", "msedge.exe", "brave.exe")
        if is_browser:
            self._extract_browser_info(window, obs)
            if obs.is_blocked:
                return obs

        # 6. Extract Elements
        self._extract_elements(window, obs, max_elements=max_elements)

        # 7. Check Calculator Display if Calculator
        if "calc" in proc_name or "calculator" in title.lower():
            self._extract_calculator_info(obs)

        return obs

    def _extract_browser_info(self, window, obs: WindowObservation) -> None:
        """Locates address bar to read current URL and validate domain."""
        try:
            for child in window.descendants():
                try:
                    c_name = child.window_text().strip()
                    c_type = child.friendly_class_name()
                    c_id = getattr(child.element_info, "automation_id", "")
                    if c_type == "Edit" and ("address" in c_name.lower() or "search or enter address" in c_name.lower() or c_id == "view_1020"):
                        raw_url = c_name or child.legacy_properties().get("Value", "")
                        if raw_url and "." in raw_url:
                            obs.current_url = raw_url
                            domain = extract_domain(raw_url)
                            obs.current_domain = domain
                            valid, reason = validate_browser_domain(domain)
                            if not valid:
                                obs.is_blocked = True
                                obs.blocked_reason = reason
                                return
                        break
                except Exception:
                    continue
        except Exception as e:
            logger.debug(f"Failed to inspect browser address bar: {e}")

    def _extract_elements(self, window, obs: WindowObservation, max_elements: int = 60) -> None:
        """Collects up to max_elements interactive controls."""
        seen = set()
        element_id = 1
        actionable_classes = {
            "Button", "Edit", "ListItem", "TabItem", "CheckBox", "RadioButton",
            "MenuItem", "ComboBox", "Hyperlink", "Document", "Text"
        }

        try:
            for child in window.descendants():
                try:
                    elem_info = child.element_info
                    ctrl_type = child.friendly_class_name()
                    name = child.window_text().strip()
                    auto_id = getattr(elem_info, "automation_id", "") or ""

                    # Filter non-actionable or empty elements
                    if not name and not auto_id:
                        continue
                    if ctrl_type not in actionable_classes and not auto_id:
                        continue

                    # Deduplicate identical items
                    key = (ctrl_type, name, auto_id)
                    if key in seen:
                        continue
                    seen.add(key)

                    # Password detection
                    is_pwd = False
                    try:
                        elem_dict = {
                            "name": name,
                            "id": auto_id,
                            "is_password": getattr(elem_info, "CurrentIsPassword", False)
                        }
                        is_pwd = is_password_element(elem_dict)
                    except Exception:
                        is_pwd = False

                    is_enabled = getattr(elem_info, "enabled", True)
                    text_val = ""
                    try:
                        text_val = child.legacy_properties().get("Value", "") or ""
                    except Exception:
                        pass

                    obs_elem = ObservedElement(
                        element_id=element_id,
                        role=ctrl_type,
                        name=name,
                        automation_id=auto_id,
                        is_enabled=is_enabled,
                        is_password=is_pwd,
                        state="normal",
                        text=text_val,
                        native_element=child
                    )
                    obs.elements.append(obs_elem)
                    element_id += 1

                    if len(obs.elements) >= max_elements:
                        break
                except Exception:
                    continue
        except Exception as e:
            logger.debug(f"Error enumerating descendants: {e}")

        # Check if Chrome accessibility seems unexposed
        if obs.process_name in ("chrome.exe", "msedge.exe") and len(obs.elements) <= 3:
            obs.accessibility_notice = (
                "Browser elements appear unexposed. If pages fail to interact, open chrome://accessibility "
                "and ensure 'Native accessibility API' and 'Web accessibility' are enabled."
            )

    def _extract_calculator_info(self, obs: WindowObservation) -> None:
        """Inspects observed elements to extract the current Calculator display string."""
        for elem in obs.elements:
            if "calculatorresults" in elem.automation_id.lower() or elem.name.lower().startswith("display is"):
                val = extract_calculator_display(elem.name or elem.text)
                if val:
                    obs.calculator_display = val
                    return
