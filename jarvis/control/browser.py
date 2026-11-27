"""Visible, persistent Google Chrome browser automation via Playwright."""

import time
import urllib.parse
from typing import Optional, Dict, Any
from pathlib import Path

from jarvis.config import config, BROWSER_PROFILE_PATH, logger
from jarvis.safety import RiskLevel, confirm_action
from jarvis.control.safety import (
    validate_browser_domain,
    is_password_element,
    sanitize_typed_text,
    validate_key_combo,
    classify_action_risk,
    wrap_untrusted_content,
    record_action_audit,
    action_budget,
)
from jarvis.control.killswitch import kill_switch, ControlAbortedException


class BrowserManager:
    """Manages headed Google Chrome instance with dedicated JARVIS user profile."""

    def __init__(self):
        self._playwright = None
        self._context = None
        self._page = None

    def _ensure_browser(self):
        """Initializes Playwright persistent context with Google Chrome if configured for jarvis_profile."""
        kill_switch.check_abort()

        if getattr(config.operator, "browser_mode", "normal") != "jarvis_profile":
            raise RuntimeError(
                "Direct Playwright profile session is disabled when browser_mode is 'normal'. "
                "Normal Chrome is automated via the observe-decide-act loop (operate) using Windows UI Automation."
            )

        if self._context is not None and self._page is not None:
            try:
                # Test if page is still responsive
                _ = self._page.url
                return
            except Exception:
                logger.debug("Previous browser page disconnected, re-initializing...")
                self.close()

        BROWSER_PROFILE_PATH.mkdir(parents=True, exist_ok=True)

        try:
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright().start()

            # Launch persistent context using installed Chrome and dedicated profile
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(BROWSER_PROFILE_PATH),
                channel="chrome",
                headless=False,
                viewport={"width": 1280, "height": 800},
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-first-run",
                    "--no-default-browser-check"
                ]
            )

            if self._context.pages:
                self._page = self._context.pages[0]
            else:
                self._page = self._context.new_page()

            logger.info(f"Initialized Google Chrome session with profile '{BROWSER_PROFILE_PATH}'")
        except Exception as e:
            logger.error(f"Failed to launch Chrome via Playwright: {e}")
            self.close()
            raise RuntimeError(f"Could not open Google Chrome via Playwright: {str(e)}")

    def open(self, url: str) -> str:
        """Opens a URL in the browser after safety domain verification."""
        if not action_budget.increment():
            return "Action limit exceeded (maximum 15 actions per request, sir)."

        target_url = url.strip()
        if "://" not in target_url:
            target_url = f"https://{target_url}"

        # 1. Domain Validation
        allowed, reason = validate_browser_domain(target_url, is_opening=True)
        if not allowed:
            return f"Navigation blocked: {reason}"

        if getattr(config, "dry_run", False):
            return f"[DRY-RUN] Would open {target_url} in Google Chrome, sir."

        # 2. Open Page
        if getattr(config.operator, "browser_mode", "normal") != "jarvis_profile":
            with kill_switch.controlling_session(f"Opening {target_url} in normal Chrome"):
                from jarvis.tools.system_tools import _launch_browser
                _launch_browser(target_url)
                record_action_audit("browser_open", target_url, "", "SUCCESS")
                return f"Successfully opened {target_url} in Google Chrome, sir."

        with kill_switch.controlling_session(f"Opening {target_url}"):
            self._ensure_browser()
            try:
                self._page.goto(target_url, wait_until="domcontentloaded", timeout=25000)
                record_action_audit("browser_open", target_url, "", "SUCCESS")
                return f"Successfully opened {target_url} in browser, sir."
            except Exception as e:
                record_action_audit("browser_open", target_url, "", "ERROR", str(e))
                return f"Failed to navigate to {target_url}: {str(e)}"

    def click(self, target: str) -> str:
        """Clicks an element by visible text, aria label, role, or CSS selector."""
        if not action_budget.increment():
            return "Action limit exceeded (maximum 15 actions per request, sir)."

        kill_switch.check_abort()

        if getattr(config, "dry_run", False):
            return f"[DRY-RUN] Would click '{target}' in browser, sir."

        if getattr(config.operator, "browser_mode", "normal") != "jarvis_profile":
            if any(ch in target for ch in ("[", "#", ">", ":")):
                return f"Guessed CSS selector '{target}' is not supported in normal browser mode. Use operate(goal) with numbered UI elements."
            from jarvis.control.desktop import desktop
            return desktop.click_control(target)

        self._ensure_browser()

        # Check action risk (high-risk keywords like buy/pay/delete/submit trigger typed confirmation)
        risk = classify_action_risk("browser_click", target)
        if risk == RiskLevel.HIGH:
            prompt = f"Executing click on '{target}' is classified as HIGH risk. Proceed with click?"
            if not confirm_action(RiskLevel.HIGH, prompt):
                record_action_audit("browser_click", target, "", "DENIED", "User denied confirmation")
                return f"Click on '{target}' was cancelled, sir."
        elif risk == RiskLevel.MEDIUM and config.safety.require_confirmation_for_medium:
            prompt = f"Click on '{target}' in browser?"
            if not confirm_action(RiskLevel.MEDIUM, prompt):
                record_action_audit("browser_click", target, "", "DENIED", "User denied confirmation")
                return f"Click on '{target}' was cancelled, sir."

        with kill_switch.controlling_session(f"Clicking '{target}'"):
            try:
                # Try locating by text first, then role/label, then raw selector
                locator = None
                text_match = self._page.get_by_text(target, exact=False)
                if text_match.count() > 0:
                    locator = text_match.first
                else:
                    role_match = self._page.get_by_role("button", name=target)
                    if role_match.count() > 0:
                        locator = role_match.first
                    else:
                        locator = self._page.locator(target).first

                locator.click(timeout=8000)
                record_action_audit("browser_click", target, "", "SUCCESS")
                return f"Clicked '{target}' in browser, sir."
            except Exception as e:
                record_action_audit("browser_click", target, "", "ERROR", str(e))
                return f"Could not click '{target}': {str(e)}"

    def type_text(self, field: str, text: str) -> str:
        """Types text into an input field, refusing password fields and stripping newlines."""
        if not action_budget.increment():
            return "Action limit exceeded (maximum 15 actions per request, sir)."

        kill_switch.check_abort()

        if getattr(config, "dry_run", False):
            return f"[DRY-RUN] Would type into '{field}' in browser, sir."

        if getattr(config.operator, "browser_mode", "normal") != "jarvis_profile":
            if any(ch in field for ch in ("[", "#", ">", ":")):
                return f"Guessed CSS selector '{field}' is not supported in normal browser mode. Use operate(goal) with numbered UI elements."
            from jarvis.control.desktop import desktop
            return desktop.type_into(field, text)

        self._ensure_browser()

        # Sanitize newlines
        clean_text = sanitize_typed_text(text)

        # Locate input field
        try:
            loc = None
            for candidate in [
                self._page.get_by_label(field, exact=False),
                self._page.get_by_placeholder(field, exact=False),
                self._page.get_by_role("textbox", name=field),
                self._page.locator(f"input[name*='{field}'], textarea[name*='{field}']"),
                self._page.locator(field),
            ]:
                if candidate.count() > 0:
                    loc = candidate.first
                    break

            if loc is None:
                return f"Could not find input field matching '{field}', sir."

            # Inspect field for password protection
            f_type = loc.get_attribute("type") or ""
            f_name = loc.get_attribute("name") or ""
            f_auto = loc.get_attribute("autocomplete") or ""
            f_aria = loc.get_attribute("aria-label") or ""

            if is_password_element({"type": f_type, "name": f_name, "autocomplete": f_auto, "aria_label": f_aria}):
                record_action_audit("browser_type", field, clean_text, "BLOCKED", "Refused password field")
                return "Refusing to type into password field for security reasons, sir."

            # Confirmation gate
            risk = classify_action_risk("browser_type", field, clean_text)
            prompt = f"Type into field '{field}' ({len(clean_text)} chars)?"
            if not confirm_action(risk, prompt):
                record_action_audit("browser_type", field, clean_text, "DENIED", "User denied confirmation")
                return f"Typing into '{field}' was cancelled, sir."

            with kill_switch.controlling_session(f"Typing into '{field}'"):
                loc.fill(clean_text, timeout=8000)
                record_action_audit("browser_type", field, clean_text, "SUCCESS")
                return f"Successfully typed into '{field}', sir."
        except Exception as e:
            record_action_audit("browser_type", field, clean_text, "ERROR", str(e))
            return f"Error typing into '{field}': {str(e)}"

    def press_key(self, key: str) -> str:
        """Presses an allowed key or combination in the browser."""
        if not action_budget.increment():
            return "Action limit exceeded (maximum 15 actions per request, sir)."

        kill_switch.check_abort()

        if getattr(config, "dry_run", False):
            return f"[DRY-RUN] Would press key '{key}' in browser, sir."

        if getattr(config.operator, "browser_mode", "normal") != "jarvis_profile":
            from jarvis.control.desktop import desktop
            return desktop.press_keys(key)

        self._ensure_browser()

        valid, reason = validate_key_combo(key)
        if not valid:
            record_action_audit("browser_press", key, "", "BLOCKED", reason)
            return f"Key press blocked: {reason}"

        # Enter triggers medium risk confirmation
        risk = RiskLevel.MEDIUM if key.lower() == "enter" else RiskLevel.LOW
        if risk == RiskLevel.MEDIUM and config.safety.require_confirmation_for_medium:
            if not confirm_action(RiskLevel.MEDIUM, f"Press key '{key}' in browser?"):
                record_action_audit("browser_press", key, "", "DENIED", "User denied confirmation")
                return f"Key press '{key}' was cancelled, sir."

        with kill_switch.controlling_session(f"Pressing '{key}'"):
            try:
                self._page.keyboard.press(key)
                record_action_audit("browser_press", key, "", "SUCCESS")
                return f"Pressed '{key}' in browser, sir."
            except Exception as e:
                record_action_audit("browser_press", key, "", "ERROR", str(e))
                return f"Error pressing '{key}': {str(e)}"

    def scroll(self, direction: str = "down") -> str:
        """Scrolls the page up or down."""
        if not action_budget.increment():
            return "Action limit exceeded (maximum 15 actions per request, sir)."

        kill_switch.check_abort()

        if getattr(config, "dry_run", False):
            return f"[DRY-RUN] Would scroll page {direction}, sir."

        if getattr(config.operator, "browser_mode", "normal") != "jarvis_profile":
            from jarvis.control.desktop import desktop
            return desktop.scroll(direction)

        self._ensure_browser()

        delta = 600 if direction.lower() == "down" else -600
        with kill_switch.controlling_session(f"Scrolling {direction}"):
            try:
                self._page.evaluate(f"window.scrollBy(0, {delta})")
                record_action_audit("browser_scroll", direction, "", "SUCCESS")
                return f"Scrolled page {direction}, sir."
            except Exception as e:
                return f"Failed to scroll page: {str(e)}"

    def read_page(self) -> str:
        """Reads visible text from the current page and wraps it as untrusted data."""
        kill_switch.check_abort()

        if getattr(config.operator, "browser_mode", "normal") != "jarvis_profile":
            from jarvis.control.uia_observer import UIAObserver
            obs = UIAObserver().observe(max_elements=30)
            return obs.to_prompt_text()

        self._ensure_browser()

        try:
            # Extract main text
            text = self._page.inner_text("body", timeout=8000)
            # Limit length to 3000 chars to avoid overflowing model context
            truncated = text[:3000]
            if len(text) > 3000:
                truncated += "\n[Page content truncated due to length...]"
            record_action_audit("browser_read_page", self._page.url, truncated, "SUCCESS")
            return wrap_untrusted_content(truncated)
        except Exception as e:
            return f"Failed to read page content: {str(e)}"

    def back(self) -> str:
        """Navigates back in browser history."""
        kill_switch.check_abort()
        self._ensure_browser()

        try:
            self._page.go_back(timeout=8000)
            return "Navigated back in browser, sir."
        except Exception as e:
            return f"Failed to navigate back: {str(e)}"

    def close(self) -> str:
        """Closes the browser session."""
        try:
            if self._context:
                self._context.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        finally:
            self._context = None
            self._page = None
            self._playwright = None
        return "Browser session closed, sir."

    # --------------------------------------------------------------------------
    # Reliable Site Skills
    # --------------------------------------------------------------------------

    def youtube_play(self, query: str) -> str:
        """Searches YouTube, clicks the first video result, and plays it."""
        if not query or not query.strip():
            return "Please provide a search query for YouTube, sir."

        kill_switch.check_abort()
        clean_q = query.strip()

        if getattr(config, "dry_run", False):
            return f"[DRY-RUN] Would search and play '{clean_q}' on YouTube, sir."

        encoded = urllib.parse.quote_plus(clean_q)
        search_url = f"https://www.youtube.com/results?search_query={encoded}"

        # Open search page
        open_res = self.open(search_url)
        if "blocked" in open_res.lower() or "failed" in open_res.lower():
            return open_res

        with kill_switch.controlling_session(f"Playing YouTube video: '{clean_q}'"):
            try:
                # Wait for video titles to appear
                video_selector = "ytd-video-renderer a#video-title, #video-title"
                self._page.wait_for_selector(video_selector, timeout=12000)

                first_video = self._page.locator(video_selector).first
                first_video.click(timeout=8000)

                record_action_audit("youtube_play", clean_q, "", "SUCCESS")
                return f"Now playing first YouTube result for '{clean_q}', sir."
            except Exception as e:
                logger.error(f"YouTube play skill failed: {e}")
                record_action_audit("youtube_play", clean_q, "", "FAILED", str(e))
                return f"Unable to play video on YouTube, sir: {str(e)}"

    def netflix_play(self, title: str) -> str:
        """Searches Netflix for a title and begins playback."""
        if not title or not title.strip():
            return "Please specify a movie or series title for Netflix, sir."

        kill_switch.check_abort()
        clean_title = title.strip()

        if getattr(config, "dry_run", False):
            return f"[DRY-RUN] Would search and play '{clean_title}' on Netflix, sir."

        encoded = urllib.parse.quote_plus(clean_title)
        search_url = f"https://www.netflix.com/search?q={encoded}"

        open_res = self.open(search_url)
        if "blocked" in open_res.lower() or "failed" in open_res.lower():
            return open_res

        if getattr(config.operator, "browser_mode", "normal") != "jarvis_profile":
            with kill_switch.controlling_session(f"Playing Netflix title: '{clean_title}' in normal Chrome"):
                try:
                    time.sleep(1.5)
                    from jarvis.control.uia_observer import UIAObserver
                    observer = UIAObserver()
                    obs = observer.observe(max_elements=60)
                    # Check for profile link
                    for elem in obs.elements:
                        if "profile" in elem.name.lower() or "who's watching" in elem.name.lower():
                            if elem.native_element:
                                try:
                                    elem.native_element.click_input()
                                    time.sleep(1.5)
                                    obs = observer.observe(max_elements=60)
                                except Exception:
                                    pass
                            break
                    # Find first title tile
                    title_elem = None
                    for elem in obs.elements:
                        if clean_title.lower() in elem.name.lower() or "title-card" in elem.automation_id.lower():
                            title_elem = elem
                            break
                    if title_elem and title_elem.native_element:
                        try:
                            title_elem.native_element.click_input()
                            time.sleep(1.0)
                            obs = observer.observe(max_elements=30)
                            for elem in obs.elements:
                                if "play" in elem.name.lower() and elem.native_element:
                                    elem.native_element.click_input()
                                    record_action_audit("netflix_play", clean_title, "", "SUCCESS")
                                    return f"Starting playback for '{clean_title}' on Netflix, sir."
                            import pyautogui
                            pyautogui.press("enter")
                            record_action_audit("netflix_play", clean_title, "", "SUCCESS")
                            return f"Starting playback for '{clean_title}' on Netflix, sir."
                        except Exception as e:
                            logger.debug(f"Click failed on title element: {e}")

                    record_action_audit("netflix_play", clean_title, "", "FAILED", "Elements unexposed")
                    return (
                        f"Unable to locate title elements for '{clean_title}' on Netflix, sir. "
                        "If page elements are unexposed, please visit chrome://accessibility to enable accessibility."
                    )
                except Exception as e:
                    record_action_audit("netflix_play", clean_title, "", "FAILED", str(e))
                    return f"Netflix playback attempt halted: {str(e)}"

        with kill_switch.controlling_session(f"Searching and playing Netflix title: '{clean_title}'"):
            try:
                # Check for profile gate
                profile_link = self._page.locator(".profile-link, .avatar-wrapper, .profile-name")
                if profile_link.count() > 0:
                    profile_link.first.click(timeout=5000)
                    time.sleep(1.0)
                    self._page.goto(search_url, timeout=15000)

                # Look for title card
                card_selector = ".title-card, [data-uia='title-card'], .slider-refocus"
                self._page.wait_for_selector(card_selector, timeout=10000)

                first_card = self._page.locator(card_selector).first
                first_card.hover()
                time.sleep(0.5)

                # Click play button or the card itself
                play_btn = self._page.locator("[data-uia='play-button'], button[aria-label*='Play'], .playButton")
                if play_btn.count() > 0:
                    play_btn.first.click(timeout=5000)
                else:
                    first_card.click(timeout=5000)

                record_action_audit("netflix_play", clean_title, "", "SUCCESS")
                return f"Starting playback for '{clean_title}' on Netflix, sir."
            except Exception as e:
                logger.warning(f"Netflix automation encountered an obstacle: {e}")
                record_action_audit("netflix_play", clean_title, "", "FAILED", str(e))
                return (
                    f"Unable to play '{clean_title}' on Netflix, sir. If you are not signed in, "
                    "please run '--setup-browser' to log in to Netflix manually."
                )


# Global browser controller instance
browser = BrowserManager()


# Tool function wrappers with short descriptions
def browser_open(url: str) -> str:
    """Opens a URL in Google Chrome (e.g. 'https://netflix.com')."""
    return browser.open(url)


def browser_click(target: str) -> str:
    """Clicks a button, link, or element by text or selector in the browser."""
    return browser.click(target)


def browser_type(field: str, text: str) -> str:
    """Types text into an input field in the browser (refuses password fields)."""
    return browser.type_text(field, text)


def browser_press(key: str) -> str:
    """Presses a key or combination in the browser (e.g. 'Enter', 'Tab', 'Space')."""
    return browser.press_key(key)


def browser_scroll(direction: str = "down") -> str:
    """Scrolls the current browser page 'up' or 'down'."""
    return browser.scroll(direction)


def browser_read_page() -> str:
    """Reads visible text content from the current active browser webpage."""
    return browser.read_page()


def browser_back() -> str:
    """Navigates back to the previous page in the browser."""
    return browser.back()


def browser_close() -> str:
    """Closes the current browser session."""
    return browser.close()


def youtube_play(query: str) -> str:
    """Searches YouTube and plays the first matching video."""
    return browser.youtube_play(query)


def netflix_play(title: str) -> str:
    """Searches Netflix for a movie or show and starts playback."""
    return browser.netflix_play(title)
