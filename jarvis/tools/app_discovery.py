"""Automated Windows application discovery, local caching, fuzzy resolution, and execution."""

import os
import json
import time
import threading
import difflib
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional

from jarvis.config import config, BASE_DIR, APPS_INDEX_PATH, logger


def scan_start_menu_shortcuts() -> Dict[str, Dict[str, Any]]:
    """Scans common and user Start Menu directories for .lnk shortcuts."""
    shortcuts: Dict[str, Dict[str, Any]] = {}

    search_dirs = []
    prog_data = os.environ.get("ProgramData", r"C:\ProgramData")
    p1 = Path(prog_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    if p1.exists():
        search_dirs.append(p1)

    app_data = os.environ.get("APPDATA")
    if app_data:
        p2 = Path(app_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        if p2.exists():
            search_dirs.append(p2)

    for base_dir in search_dirs:
        try:
            for item in base_dir.rglob("*.lnk"):
                name = item.stem.strip()
                norm_name = name.lower()
                if norm_name not in shortcuts:
                    shortcuts[norm_name] = {
                        "name": name,
                        "target": str(item.resolve()),
                        "type": "lnk",
                        "aliases": []
                    }
        except Exception as e:
            logger.debug(f"Error scanning shortcuts in {base_dir}: {e}")

    return shortcuts


def scan_start_apps_powershell() -> Dict[str, Dict[str, Any]]:
    """Queries PowerShell Get-StartApps to discover Store (UWP) apps and modern AUMID entries."""
    store_apps: Dict[str, Dict[str, Any]] = {}
    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", "Get-StartApps | ConvertTo-Json -Compress"]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if proc.returncode == 0 and proc.stdout.strip():
            raw_data = json.loads(proc.stdout)
            if isinstance(raw_data, dict):
                raw_data = [raw_data]

            for item in raw_data:
                name = item.get("Name", "").strip()
                app_id = item.get("AppID", "").strip()
                if not name or not app_id:
                    continue

                norm_name = name.lower()
                app_type = "uwp" if ("!" in app_id or "Microsoft." in app_id) else "app"

                store_apps[norm_name] = {
                    "name": name,
                    "target": f"shell:AppsFolder\\{app_id}" if not app_id.startswith("shell:") and "\\" not in app_id else app_id,
                    "type": app_type,
                    "aliases": []
                }
    except Exception as e:
        logger.warning(f"PowerShell Get-StartApps query failed or timed out: {e}")

    return store_apps


def discover_installed_apps() -> Dict[str, Dict[str, Any]]:
    """
    Discovers all installed applications on Windows:
    - Start Menu .lnk shortcuts
    - Microsoft Store / UWP apps via Get-StartApps
    - Whitelist aliases defined in settings.yaml
    Caches the combined catalog to apps_index.json.
    """
    logger.info("Scanning system for installed applications and Start Menu entries...")
    index: Dict[str, Dict[str, Any]] = {}

    # 1. Scan Start Menu .lnk shortcuts
    lnk_apps = scan_start_menu_shortcuts()
    index.update(lnk_apps)

    # 2. Scan modern / UWP apps via Get-StartApps
    uwp_apps = scan_start_apps_powershell()
    for k, v in uwp_apps.items():
        if k not in index:
            index[k] = v
        else:
            # If shortcut already exists, preserve UWP AppID as alternative target
            index[k]["uwp_target"] = v["target"]

    # 3. Merge configured whitelist aliases from settings.yaml
    for alias, cmd in config.apps.items():
        alias_norm = alias.strip().lower()
        # Find if alias matches an existing app or add standalone entry
        matched_key = None
        for k in index:
            if alias_norm == k or alias_norm in k.split():
                matched_key = k
                break

        if matched_key:
            if alias_norm not in index[matched_key]["aliases"]:
                index[matched_key]["aliases"].append(alias_norm)
        else:
            index[alias_norm] = {
                "name": alias.title(),
                "target": cmd,
                "type": "cmd",
                "aliases": [alias_norm]
            }

    # Save to disk cache
    if getattr(config, "dry_run", False):
        logger.info("[DRY-RUN] Skipped saving apps index to disk.")
        return index

    try:
        with open(APPS_INDEX_PATH, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2)
        logger.info(f"Indexed {len(index)} applications successfully to '{APPS_INDEX_PATH}'.")
    except Exception as e:
        logger.error(f"Failed to save apps index to '{APPS_INDEX_PATH}': {e}")

    return index


build_apps_index = discover_installed_apps

_IN_MEMORY_APPS_INDEX: Optional[Dict[str, Dict[str, Any]]] = None


def load_apps_index(force_refresh: bool = False) -> Dict[str, Dict[str, Any]]:
    """Loads apps index from disk cache, discovering them if missing or forced.
    If cached index is older than 7 days, refreshes asynchronously in background."""
    global _IN_MEMORY_APPS_INDEX
    t0 = time.perf_counter()

    if not force_refresh and _IN_MEMORY_APPS_INDEX is not None:
        return _IN_MEMORY_APPS_INDEX

    if not force_refresh and APPS_INDEX_PATH.exists():
        try:
            with open(APPS_INDEX_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and data:
                    _IN_MEMORY_APPS_INDEX = data

                    # Check age: refresh in background if older than 7 days
                    file_age = time.time() - APPS_INDEX_PATH.stat().st_mtime
                    if file_age > 7 * 86400:
                        logger.info("Application index is older than 7 days. Refreshing in background...")
                        threading.Thread(target=discover_installed_apps, daemon=True).start()

                    dur = time.perf_counter() - t0
                    try:
                        from jarvis.timing import tracker
                        tracker.record_stage("app_index", dur, f"cached ({len(data)} apps)")
                    except Exception:
                        pass
                    return data
        except Exception as e:
            logger.warning(f"Could not read cached apps_index.json ({e}). Rebuilding...")

    res = discover_installed_apps()
    _IN_MEMORY_APPS_INDEX = res
    dur = time.perf_counter() - t0
    try:
        from jarvis.timing import tracker
        tracker.record_stage("app_index", dur, f"freshly indexed ({len(res)} apps)")
    except Exception:
        pass
    return res


def find_matching_apps(
    query: str,
    index: Optional[Dict[str, Dict[str, Any]]] = None
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Fuzzy-matches a query string against the application index:
    - Tier 1: Exact alias or exact name match
    - Tier 2: Substring / token boundary match
    - Tier 3: Fuzzy sequence matching (difflib)

    Returns:
        (status, matches)
        status can be: 'exact', 'single', 'multiple', or 'none'
    """
    if not query or not query.strip():
        return "none", []

    apps_db = index if index is not None else load_apps_index()
    q = query.strip().lower()

    # 1. Tier 1: Exact key or alias match
    for k, v in apps_db.items():
        if q == k or q in [a.lower() for a in v.get("aliases", [])]:
            return "exact", [v]

    # 2. Tier 2: Whole-word or substring match
    sub_matches: List[Dict[str, Any]] = []
    for k, v in apps_db.items():
        name_lower = v.get("name", "").lower()
        if q in name_lower or any(q == token for token in name_lower.split()):
            if v not in sub_matches:
                sub_matches.append(v)

    if len(sub_matches) == 1:
        return "single", sub_matches
    elif len(sub_matches) > 1:
        # Check if one match is an exact word prefix/match
        exact_tokens = [m for m in sub_matches if q in [t.lower() for t in m["name"].split()]]
        if len(exact_tokens) == 1:
            return "single", exact_tokens
        return "multiple", sub_matches[:6]

    # 3. Tier 3: Fuzzy sequence matching using difflib
    names_map = {v.get("name", "").lower(): v for v in apps_db.values()}
    close_keys = difflib.get_close_matches(q, list(names_map.keys()), n=4, cutoff=0.65)

    if len(close_keys) == 1:
        return "single", [names_map[close_keys[0]]]
    elif len(close_keys) > 1:
        return "multiple", [names_map[k] for k in close_keys]

    return "none", []


def launch_indexed_app(app_info: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Launches an application target using the appropriate Windows mechanism:
    - Never passes arguments to launched apps from model output.
    - UWP / Store apps via shell:AppsFolder
    - Direct executables / .lnk via os.startfile
    """
    target = app_info.get("target", "").strip()
    app_name = app_info.get("name", "Application")
    app_type = app_info.get("type", "exe")

    if not target:
        return False, f"Target path for '{app_name}' is empty."

    try:
        if app_type == "uwp" or target.startswith("shell:") or "!" in target:
            # UWP / Store application launch
            shell_target = target if target.startswith("shell:AppsFolder\\") else f"shell:AppsFolder\\{target}"
            os.startfile(shell_target)
        elif target.endswith(".lnk") or os.path.isabs(target):
            # Direct path or shortcut
            os.startfile(target)
        else:
            # Named executable without arguments (e.g. 'notepad.exe', 'calc.exe')
            # Strictly do not pass arbitrary arguments
            safe_binary = target.split()[0] if " " in target and not Path(target).exists() else target
            os.startfile(safe_binary)

        return True, f"Successfully launched {app_name}."
    except Exception as e:
        logger.error(f"Failed to launch '{app_name}' via target '{target}': {e}")
        return False, f"Error launching {app_name}: {str(e)}"
