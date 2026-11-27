"""Core system tools for JARVIS (apps, websites, search, system stats, audio, media)."""

import os
import shutil
import ipaddress
import subprocess
import urllib.parse
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

import psutil

from jarvis.config import config, BASE_DIR, logger
from jarvis.safety import RiskLevel, log_audit_entry, mark_untrusted, confirm_action
from jarvis.tools.app_discovery import find_matching_apps, launch_indexed_app

SCREENSHOTS_DIR = BASE_DIR / "screenshots"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)


def _launch_browser(url: str) -> bool:
    """Launches URL using the configured browser preference (e.g. Chrome or system default)."""
    if getattr(config, "dry_run", False):
        return True

    selected_browser = config.browser.lower()
    if selected_browser == "chrome":
        chrome_candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
            shutil.which("chrome") or "",
        ]
        for c in chrome_candidates:
            if c and os.path.exists(c):
                try:
                    subprocess.Popen([c, url])
                    return True
                except Exception as e:
                    logger.warning(f"Failed to launch Chrome at '{c}': {e}")
                    break

    # Fallback to default browser
    return webbrowser.open(url)


def open_app(app_name: str) -> str:
    """Launches an installed desktop or Store application (e.g. 'chrome', 'calculator', 'notepad', 'spotify').

    Args:
        app_name: Name of the application to launch.
    """
    cleaned = app_name.strip()
    if not cleaned:
        return "Please specify an application name, sir."

    lower_query = cleaned.lower()

    # 1. Blocked Tools Check
    for blocked in config.safety.blocked_apps:
        if blocked in lower_query or lower_query in blocked:
            msg = f"Access denied: Launching '{cleaned}' is strictly prohibited by security policy."
            log_audit_entry("open_app", RiskLevel.HIGH, {"app_name": cleaned}, "BLOCKED", msg)
            return msg

    # 2. Administrative Tools / Confirm Apps Check
    is_confirm_app = any(
        c == lower_query or lower_query.startswith(c) or c in lower_query.split()
        for c in config.safety.confirm_apps
    )
    if is_confirm_app:
        prompt = f"Administrative tool '{cleaned}' requires explicit confirmation."
        confirmed = confirm_action(RiskLevel.HIGH, prompt)
        if not confirmed:
            log_audit_entry("open_app", RiskLevel.HIGH, {"app_name": cleaned}, "DENIED", "User denied confirmation")
            return f"Launch of '{cleaned}' was cancelled, sir."

    # 3. Fuzzy Match against Discovered Index & Whitelist
    status, matches = find_matching_apps(cleaned)

    if status == "multiple":
        options = ", ".join(f"{idx+1}) {m['name']}" for idx, m in enumerate(matches))
        msg = f"I found multiple applications matching '{cleaned}', sir: {options}. Which one would you like to open?"
        log_audit_entry("open_app", RiskLevel.LOW, {"app_name": cleaned, "candidates": len(matches)}, "AMBIGUOUS", msg)
        return msg

    if status == "none" or not matches:
        msg = f"No installed application matching '{cleaned}' was found on this system, sir."
        log_audit_entry("open_app", RiskLevel.LOW, {"app_name": cleaned}, "NOT_FOUND", msg)
        return msg

    target_app = matches[0]

    # Re-check target name against blocked/confirm apps
    target_name_lower = target_app.get("name", "").lower()
    for blocked in config.safety.blocked_apps:
        if blocked in target_name_lower:
            msg = f"Access denied: Launching '{target_app['name']}' is blocked by security policy."
            log_audit_entry("open_app", RiskLevel.HIGH, {"app_name": target_app["name"]}, "BLOCKED", msg)
            return msg

    if not is_confirm_app and any(c in target_name_lower.split() for c in config.safety.confirm_apps):
        prompt = f"Administrative tool '{target_app['name']}' requires explicit confirmation."
        confirmed = confirm_action(RiskLevel.HIGH, prompt)
        if not confirmed:
            log_audit_entry("open_app", RiskLevel.HIGH, {"app_name": target_app["name"]}, "DENIED", "User denied confirmation")
            return f"Launch of '{target_app['name']}' was cancelled, sir."

    # 4. Launch safely without model arguments
    if getattr(config, "dry_run", False):
        msg = f"[DRY-RUN] Would launch '{target_app['name']}', sir."
        log_audit_entry("open_app", RiskLevel.LOW, {"app_name": target_app["name"], "target": target_app.get("target")}, "SUCCESS", msg)
        return msg

    success, launch_msg = launch_indexed_app(target_app)
    status_str = "SUCCESS" if success else "ERROR"
    log_audit_entry("open_app", RiskLevel.LOW, {"app_name": target_app["name"], "target": target_app.get("target")}, status_str, launch_msg)
    return launch_msg


def open_website(query_or_url: str) -> str:
    """Opens a website URL or favorite site name (e.g. 'netflix', 'amazon', or any https:// URL).

    Args:
        query_or_url: Web address (https://...) or site name.
    """
    raw = query_or_url.strip()
    if not raw:
        return "Please specify a website or URL, sir."

    target_lower = raw.lower()

    # Parse scheme if present
    parsed = urllib.parse.urlparse(raw)
    scheme = parsed.scheme.lower() if parsed.scheme else ""

    # 1. Plain Name Check (no scheme provided)
    if not scheme:
        if "." not in raw:
            # Resolves via favorites dictionary
            if target_lower in config.favorite_sites:
                target_url = config.favorite_sites[target_lower]
                _launch_browser(target_url)
                log_audit_entry("open_website", RiskLevel.LOW, {"query": raw, "url": target_url}, "SUCCESS", f"Opened favorite {raw}")
                return f"Opening {raw.title()} ({target_url}), sir."
            else:
                # Safe Search Fallback
                search_url = f"https://www.google.com/search?q={urllib.parse.quote_plus(raw)}"
                _launch_browser(search_url)
                log_audit_entry("open_website", RiskLevel.LOW, {"query": raw, "url": search_url}, "SUCCESS", "Safe search fallback")
                return f"Opening search results for '{raw}', sir: {search_url}"
        else:
            # Domain without scheme (e.g. 'github.com' or 'www.netflix.com') -> upgrade to https://
            raw = f"https://{raw}"
            parsed = urllib.parse.urlparse(raw)
            scheme = parsed.scheme.lower()

    # 2. Scheme & Security Validation
    # Strictly block dangerous schemes
    if scheme not in ("http", "https"):
        msg = f"Access blocked: URL scheme '{scheme}' is prohibited for security."
        log_audit_entry("open_website", RiskLevel.HIGH, {"url": raw, "scheme": scheme}, "BLOCKED", msg)
        return msg

    # 3. Raw IP Address Check
    if parsed.hostname:
        is_raw_ip = False
        try:
            ipaddress.ip_address(parsed.hostname)
            is_raw_ip = True
        except ValueError:
            pass

        if is_raw_ip:
            prompt = f"Security warning: URL '{raw}' connects directly to a raw IP address ({parsed.hostname}). Proceed?"
            confirmed = confirm_action(RiskLevel.MEDIUM, prompt)
            if not confirmed:
                log_audit_entry("open_website", RiskLevel.MEDIUM, {"url": raw}, "DENIED", "Raw IP connection denied")
                return f"Navigation to '{raw}' was cancelled, sir."

    # 4. Unencrypted HTTP Check
    if scheme == "http":
        prompt = f"Security warning: URL '{raw}' uses unencrypted HTTP. Proceed?"
        confirmed = confirm_action(RiskLevel.MEDIUM, prompt)
        if not confirmed:
            log_audit_entry("open_website", RiskLevel.MEDIUM, {"url": raw}, "DENIED", "Unencrypted HTTP connection denied")
            return f"Navigation to '{raw}' was cancelled, sir."

    # 5. Domain & Blocklist Validation (opening blocklisted domains requires typed confirmation)
    from jarvis.control.safety import validate_browser_domain
    domain_ok, domain_msg = validate_browser_domain(raw, is_opening=True)
    if not domain_ok:
        log_audit_entry("open_website", RiskLevel.HIGH, {"url": raw}, "DENIED", domain_msg)
        return domain_msg

    # 6. Launch URL in configured browser
    if getattr(config, "dry_run", False):
        log_audit_entry("open_website", RiskLevel.LOW, {"url": raw}, "SUCCESS", f"[DRY-RUN] Would open {raw}")
        return f"[DRY-RUN] Would open website: {raw}"

    try:
        _launch_browser(raw)
        log_audit_entry("open_website", RiskLevel.LOW, {"url": raw}, "SUCCESS", f"Opened {raw}")
        return f"Opened website: {raw}"
    except Exception as e:
        msg = f"Failed to open website '{raw}': {str(e)}"
        log_audit_entry("open_website", RiskLevel.LOW, {"url": raw}, "ERROR", msg)
        return msg


def open_site_search(site: str, query: str) -> str:
    """Searches a specific website directly (e.g. youtube, netflix, google, amazon, wikipedia, github, spotify).

    Args:
        site: The site to search on (e.g. 'netflix', 'youtube', 'amazon', 'github', 'wikipedia').
        query: The term or title to search for.
    """
    site_clean = site.strip().lower()
    query_clean = query.strip()
    if not query_clean:
        return f"Please provide a search query for {site}, sir."

    encoded_query = urllib.parse.quote_plus(query_clean)
    template = config.site_search_templates.get(site_clean)

    if template:
        search_url = template.format(query=encoded_query)
    else:
        # Fallback to site-targeted search
        search_url = f"https://www.google.com/search?q=site:{urllib.parse.quote_plus(site_clean)}+{encoded_query}"

    try:
        _launch_browser(search_url)
        log_audit_entry("open_site_search", RiskLevel.LOW, {"site": site_clean, "query": query_clean, "url": search_url}, "SUCCESS", "Site search opened")
        return f"Searching {site_clean.title()} for '{query_clean}', sir: {search_url}"
    except Exception as e:
        msg = f"Failed to search {site_clean}: {str(e)}"
        log_audit_entry("open_site_search", RiskLevel.LOW, {"site": site_clean, "query": query_clean}, "ERROR", msg)
        return msg


def google_search(query: str) -> str:
    """Searches Google for the specified query and opens the results in the web browser.

    Args:
        query: The search term or question to look up.

    Returns:
        A confirmation message that the search was executed.
    """
    encoded_query = urllib.parse.quote_plus(query.strip())
    search_url = f"https://www.google.com/search?q={encoded_query}"
    try:
        _launch_browser(search_url)
        msg = f"Opened Google search for '{query}'."
        log_audit_entry("google_search", RiskLevel.LOW, {"query": query}, "SUCCESS", msg)
        return msg
    except Exception as e:
        msg = f"Failed to perform Google search: {str(e)}"
        log_audit_entry("google_search", RiskLevel.LOW, {"query": query}, "ERROR", msg)
        return msg


def youtube_search(query: str) -> str:
    """Searches YouTube for the specified query and opens the results in the web browser.

    Args:
        query: The video, artist, or topic to search on YouTube.

    Returns:
        A confirmation message that the YouTube search was executed.
    """
    encoded_query = urllib.parse.quote_plus(query.strip())
    search_url = f"https://www.youtube.com/results?search_query={encoded_query}"
    try:
        _launch_browser(search_url)
        msg = f"Opened YouTube search for '{query}'."
        log_audit_entry("youtube_search", RiskLevel.LOW, {"query": query}, "SUCCESS", msg)
        return msg
    except Exception as e:
        msg = f"Failed to perform YouTube search: {str(e)}"
        log_audit_entry("youtube_search", RiskLevel.LOW, {"query": query}, "ERROR", msg)
        return msg


def get_time_date() -> str:
    """Retrieves the current local time, date, and day of the week.

    Returns:
        A string formatted with the current time, date, and day.
    """
    now = datetime.now()
    result = now.strftime("Current time is %I:%M %p on %A, %B %d, %Y.")
    log_audit_entry("get_time_date", RiskLevel.LOW, {}, "SUCCESS", result)
    return result


def system_stats() -> str:
    """Checks the system resource metrics including CPU utilization, RAM usage, and battery status.

    Returns:
        A summary of CPU percentage, used/total RAM in GB, and battery charge status.
    """
    try:
        cpu_pct = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        mem_used_gb = mem.used / (1024 ** 3)
        mem_total_gb = mem.total / (1024 ** 3)
        mem_pct = mem.percent

        battery = psutil.sensors_battery()
        if battery:
            plugged_str = "plugged in" if battery.power_plugged else "on battery"
            battery_str = f"{battery.percent:.0f}% ({plugged_str})"
        else:
            battery_str = "No battery detected (Desktop/AC powered)"

        stats = (
            f"System Status:\n"
            f"- CPU Usage: {cpu_pct}%\n"
            f"- RAM Usage: {mem_used_gb:.1f} GB / {mem_total_gb:.1f} GB ({mem_pct}%)\n"
            f"- Battery: {battery_str}"
        )
        log_audit_entry("system_stats", RiskLevel.LOW, {}, "SUCCESS", stats)
        return stats
    except Exception as e:
        msg = f"Unable to retrieve system statistics: {str(e)}"
        log_audit_entry("system_stats", RiskLevel.LOW, {}, "ERROR", msg)
        return msg


def set_volume(level: int) -> str:
    """Sets the master system volume to a percentage between 0 and 100.

    Args:
        level: Target volume percentage integer between 0 and 100.

    Returns:
        A confirmation string stating the new volume level.
    """
    clamped_level = max(0, min(100, int(level)))
    if getattr(config, "dry_run", False):
        msg = f"[DRY-RUN] Would set master volume to {clamped_level}%."
        log_audit_entry("set_volume", RiskLevel.LOW, {"level": clamped_level}, "SUCCESS", msg)
        return msg

    scalar = clamped_level / 100.0
    try:
        from pycaw.pycaw import AudioUtilities
        device = AudioUtilities.GetSpeakers()
        endpoint = getattr(device, "EndpointVolume", None)
        if endpoint is None:
            # Fallback for alternative pycaw architectures
            from ctypes import cast, POINTER
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import IAudioEndpointVolume
            interface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            endpoint = cast(interface, POINTER(IAudioEndpointVolume))

        endpoint.SetMasterVolumeLevelScalar(scalar, None)
        # Unmute automatically if volume is increased above 0
        if clamped_level > 0 and endpoint.GetMute():
            endpoint.SetMute(0, None)

        msg = f"Master volume set to {clamped_level}%."
        log_audit_entry("set_volume", RiskLevel.LOW, {"level": clamped_level}, "SUCCESS", msg)
        return msg
    except Exception as e:
        msg = f"Failed to set volume: {str(e)}"
        log_audit_entry("set_volume", RiskLevel.LOW, {"level": level}, "ERROR", msg)
        return msg


def mute(state: bool) -> str:
    """Mutes or unmutes the master system audio.

    Args:
        state: True to mute system audio, False to unmute.

    Returns:
        Confirmation message of audio mute status.
    """
    if getattr(config, "dry_run", False):
        msg = f"[DRY-RUN] Would {'mute' if state else 'unmute'} master audio."
        log_audit_entry("mute", RiskLevel.LOW, {"state": state}, "SUCCESS", msg)
        return msg

    try:
        from pycaw.pycaw import AudioUtilities
        device = AudioUtilities.GetSpeakers()
        endpoint = getattr(device, "EndpointVolume", None)
        if endpoint is None:
            from ctypes import cast, POINTER
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import IAudioEndpointVolume
            interface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            endpoint = cast(interface, POINTER(IAudioEndpointVolume))

        endpoint.SetMute(1 if state else 0, None)
        msg = "Master audio muted." if state else "Master audio unmuted."
        log_audit_entry("mute", RiskLevel.LOW, {"state": state}, "SUCCESS", msg)
        return msg
    except Exception as e:
        msg = f"Failed to alter mute state: {str(e)}"
        log_audit_entry("mute", RiskLevel.LOW, {"state": state}, "ERROR", msg)
        return msg


def take_screenshot(filename: str = "") -> str:
    """Captures a screenshot of the entire primary display and saves it to the screenshots directory.

    Args:
        filename: Optional custom filename (e.g. 'capture.png'). If omitted, a timestamped name is generated.

    Returns:
        A message containing the absolute path of the saved screenshot.
    """
    if getattr(config, "dry_run", False):
        msg = "[DRY-RUN] Would capture screenshot (no file written in dry-run mode)."
        log_audit_entry("take_screenshot", RiskLevel.LOW, {"filename": filename, "dry_run": True}, "SUCCESS", msg)
        return msg

    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if not filename or not filename.strip():
            target_name = f"screenshot_{timestamp}.png"
        else:
            clean_name = os.path.basename(filename.strip())
            if not clean_name.lower().endswith((".png", ".jpg", ".jpeg")):
                clean_name += ".png"
            target_name = clean_name

        save_path = SCREENSHOTS_DIR / target_name
        from PIL import ImageGrab
        img = ImageGrab.grab()
        img.save(str(save_path))

        msg = f"Screenshot captured and saved to: {save_path}"
        log_audit_entry("take_screenshot", RiskLevel.LOW, {"file": str(save_path)}, "SUCCESS", msg)
        return msg
    except Exception as e:
        msg = f"Failed to capture screenshot: {str(e)}"
        log_audit_entry("take_screenshot", RiskLevel.LOW, {"filename": filename}, "ERROR", msg)
        return msg


def media_control(action: str) -> str:
    """Controls media playback (play/pause, next track, previous track, stop).

    Args:
        action: The playback action to perform: 'play_pause' (or 'toggle'), 'next', 'previous', or 'stop'.

    Returns:
        Confirmation message of the executed media action.
    """
    act = action.strip().lower()
    mapping = {
        "play": "playpause",
        "pause": "playpause",
        "play_pause": "playpause",
        "toggle": "playpause",
        "next": "nexttrack",
        "next_track": "nexttrack",
        "previous": "prevtrack",
        "prev": "prevtrack",
        "prev_track": "prevtrack",
        "stop": "stop"
    }
    key = mapping.get(act)
    if not key:
        valid_actions = "play_pause, next, previous, stop"
        msg = f"Unsupported media action '{action}'. Valid actions are: {valid_actions}."
        log_audit_entry("media_control", RiskLevel.LOW, {"action": action}, "BLOCKED", msg)
        return msg

    if getattr(config, "dry_run", False):
        msg = f"[DRY-RUN] Would execute media control: {act}."
        log_audit_entry("media_control", RiskLevel.LOW, {"action": act, "key": key}, "SUCCESS", msg)
        return msg

    try:
        import pyautogui
        pyautogui.press(key)
        msg = f"Media control executed: {act}."
        log_audit_entry("media_control", RiskLevel.LOW, {"action": act, "key": key}, "SUCCESS", msg)
        return msg
    except Exception as e:
        msg = f"Failed to execute media control: {str(e)}"
        log_audit_entry("media_control", RiskLevel.LOW, {"action": action}, "ERROR", msg)
        return msg
