"""Local command layer executing deterministic commands locally without any LLM API calls."""

import re
from typing import Optional, Tuple
from jarvis.config import config, logger
from jarvis.tools.system_tools import (
    get_time_date,
    system_stats,
    open_app,
    open_site_search,
    set_volume,
    mute,
    take_screenshot,
    media_control,
)


def try_handle_local_command(user_input: str) -> Optional[str]:
    """
    Checks if user_input matches a fast, deterministic local command.
    If matched, executes the tool directly and returns a butler-style response.
    If not matched, returns None so execution proceeds to the LLM layer.
    """
    cleaned = user_input.strip().lower()
    if not cleaned:
        return None

    # Strip conversational pleasantries and trailing punctuation
    prefix_patterns = [
        r"^(jarvis|please|can you|could you|would you kindly|hey jarvis|ok jarvis)[,\s]+",
        r"\s+(please|jarvis)$",
    ]
    norm = cleaned
    for pat in prefix_patterns:
        norm = re.sub(pat, "", norm, flags=re.IGNORECASE).strip()
    norm = re.sub(r"[?!.,]+$", "", norm).strip()

    # 1. Time & Date
    time_keywords = {
        "what time is it", "what's the time", "whats the time", "current time",
        "time", "time now", "tell me the time", "get time", "what date is it",
        "today's date", "todays date", "what day is it", "current date"
    }
    if (
        norm in time_keywords
        or re.search(r"^what\s+time\s+is\s+it$", norm)
        or re.search(r"^(what('s| is)?\s+the\s+)?(current\s+)?(time|date)(\s+now|\s+today)?$", norm)
    ):
        res = get_time_date()
        logger.info(f"Local command handled: time/date query -> {res}")
        return f"{res}"

    # 2. System Stats
    stats_keywords = {
        "system stats", "system status", "system metrics", "check system",
        "cpu usage", "ram usage", "battery status", "system info", "computer status"
    }
    if norm in stats_keywords or re.search(r"^(check\s+)?(system\s+)?(stats|status|metrics|health)$", norm):
        res = system_stats()
        logger.info("Local command handled: system stats query")
        return f"{res}"

    # 3. Screenshot
    screenshot_keywords = {
        "take screenshot", "take a screenshot", "screenshot", "capture screen",
        "capture display", "take snapshot", "screen capture"
    }
    if norm in screenshot_keywords or re.search(r"^(take\s+(a\s+)?)?screenshot$", norm):
        res = take_screenshot()
        logger.info("Local command handled: take screenshot")
        return f"Right away, sir. {res}"

    # 4. Mute / Unmute
    if norm in {"mute", "mute audio", "mute sound", "mute system", "silence audio", "mute volume"}:
        res = mute(True)
        logger.info("Local command handled: mute")
        return f"Audio muted, sir."
    if norm in {"unmute", "unmute audio", "unmute sound", "unmute system", "restore audio", "unmute volume"}:
        res = mute(False)
        logger.info("Local command handled: unmute")
        return f"Audio unmuted, sir."

    # 5. Volume Control (e.g. 'set volume to 40', 'volume 50', 'turn volume to 70%')
    vol_match = re.search(r"^(?:set\s+)?volume\s+(?:to\s+)?(\d{1,3})\s*%?$", norm)
    if not vol_match:
        vol_match = re.search(r"^turn\s+(?:the\s+)?volume\s+(?:to\s+)?(\d{1,3})\s*%?$", norm)
    if vol_match:
        val = int(vol_match.group(1))
        res = set_volume(val)
        logger.info(f"Local command handled: volume set to {val}")
        return f"{res}, sir."

    # 6. Media Control
    media_actions = {
        "play": "play",
        "pause": "pause",
        "play pause": "play_pause",
        "pause music": "pause",
        "play music": "play",
        "resume music": "play",
        "next track": "next",
        "next song": "next",
        "skip track": "next",
        "skip song": "next",
        "previous track": "previous",
        "previous song": "previous",
        "prev track": "previous",
        "stop music": "stop",
        "stop playback": "stop",
    }
    if norm in media_actions:
        res = media_control(media_actions[norm])
        logger.info(f"Local command handled: media {media_actions[norm]}")
        return f"{res}, sir."

    # 7. Search on specific site (e.g. 'search stranger things on netflix', 'search tutorial on youtube')
    site_search_match = re.search(r"^search\s+(.+?)\s+(?:on|in)\s+([a-zA-Z0-9_\-]+)$", norm)
    if site_search_match:
        query_val = site_search_match.group(1).strip()
        site_val = site_search_match.group(2).strip().lower()
        res = open_site_search(site_val, query_val)
        logger.info(f"Local command handled: site search '{query_val}' on {site_val}")
        return res

    # 8. Open Application (e.g. 'open notepad', 'launch calculator', 'start vscode')
    # Compound instructions (e.g. 'open notepad and type ...') should proceed to operator/brain
    if " and " not in norm and " then " not in norm:
        open_match = re.search(r"^(?:open|launch|start|run)\s+([a-zA-Z0-9_\-\.\s]+)$", norm)
        if open_match:
            app_target = open_match.group(1).strip().lower()
            if not app_target.startswith(("file ", "website ", "url ")):
                res = open_app(app_target)
                if any(k in res for k in ("Successfully launched", "multiple applications", "requires", "denied", "Access denied")):
                    logger.info(f"Local command handled: open app '{app_target}'")
                    return res

    return None
