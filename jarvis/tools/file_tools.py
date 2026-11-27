"""File management tools restricted strictly to whitelisted directories."""

import os
import re
import fnmatch
from pathlib import Path
from typing import Optional

from jarvis.config import config, BASE_DIR, logger
from jarvis.safety import (
    RiskLevel,
    log_audit_entry,
    confirm_action,
    mark_untrusted,
    validate_path,
    is_path_protected,
)


def list_files(directory_path: str = "") -> str:
    """Lists files and directories located within an approved folder.

    Args:
        directory_path: Absolute or relative directory path to list. If empty, the project root directory is used.

    Returns:
        A list of contained files and folders with sizes, or an access error message.
    """
    target = directory_path.strip() if directory_path else str(BASE_DIR)
    is_valid, resolved_path, err = validate_path(target, must_exist=True)

    if not is_valid or not resolved_path:
        log_audit_entry("list_files", RiskLevel.LOW, {"path": target}, "BLOCKED", err)
        return f"Unable to list directory: {err}"

    if not resolved_path.is_dir():
        msg = f"Path '{resolved_path.name}' is a file, not a directory."
        log_audit_entry("list_files", RiskLevel.LOW, {"path": str(resolved_path)}, "ERROR", msg)
        return msg

    try:
        entries = []
        # Filter out protected paths from directory listing
        valid_items = [p for p in resolved_path.iterdir() if not is_path_protected(p)[0]]
        items = sorted(valid_items, key=lambda p: (not p.is_dir(), p.name.lower()))[:50]
        for item in items:
            prefix = "[DIR] " if item.is_dir() else "      "
            size_str = ""
            if item.is_file():
                try:
                    size_kb = item.stat().st_size / 1024
                    size_str = f" ({size_kb:.1f} KB)"
                except Exception:
                    size_str = ""
            entries.append(f"{prefix}{item.name}{size_str}")

        count_total = len(valid_items)
        summary = f"Directory contents of '{resolved_path}' ({len(entries)} of {count_total} items shown):\n"
        summary += "\n".join(entries) if entries else "(Directory is empty)"

        log_audit_entry("list_files", RiskLevel.LOW, {"path": str(resolved_path)}, "SUCCESS", f"{len(entries)} items listed")
        return mark_untrusted(summary, f"Filesystem directory: {resolved_path.name}")
    except Exception as e:
        msg = f"Error reading directory '{resolved_path}': {str(e)}"
        log_audit_entry("list_files", RiskLevel.LOW, {"path": str(resolved_path)}, "ERROR", msg)
        return msg


def find_file(filename_pattern: str, search_directory: str = "") -> str:
    """Searches for files matching a wildcard pattern (e.g. 'settings', '*.txt', 'report*') within approved directories.

    Args:
        filename_pattern: Filename or pattern to search for (e.g. 'settings', '*.py', '*resume*'). Bare words are matched as wildcards.
        search_directory: Directory to search inside. If empty, all approved directories (including project folder) are searched.

    Returns:
        A list of matching file paths, or an error if unauthorized.
    """
    raw_pattern = filename_pattern.strip().strip("'\"")
    if not raw_pattern:
        return "Please specify a filename pattern to search for."

    # Clean conversational filler like 'find my settings file' -> 'settings'
    clean_keyword = re.sub(
        r"\b(find|search|locate|look\s+for|where\s+is|show\s+me|get|my|the|a|an|file|files)\b",
        "",
        raw_pattern,
        flags=re.IGNORECASE
    ).strip()
    clean_keyword = re.sub(r"\s+", " ", clean_keyword).strip()
    if not clean_keyword:
        clean_keyword = raw_pattern

    # Treat a bare word as a wildcard (e.g. 'settings' -> '*settings*')
    if "*" not in clean_keyword and "?" not in clean_keyword:
        pattern = f"*{clean_keyword}*"
    else:
        pattern = clean_keyword

    # Determine directories to search
    target_dirs = []
    if search_directory and search_directory.strip():
        req_dir = search_directory.strip()
        is_valid, resolved_dir, err = validate_path(req_dir, must_exist=True)
        if not is_valid or not resolved_dir:
            log_audit_entry("find_file", RiskLevel.LOW, {"pattern": pattern, "directory": req_dir}, "BLOCKED", err)
            return f"Search aborted: {err}"
        if not resolved_dir.is_dir():
            msg = f"Search path '{resolved_dir}' is not a directory."
            log_audit_entry("find_file", RiskLevel.LOW, {"pattern": pattern, "directory": str(resolved_dir)}, "ERROR", msg)
            return msg
        target_dirs.append(resolved_dir)
    else:
        # Search all configured allowed paths, project root first
        seen = set()
        for p in config.allowed_paths:
            path_obj = Path(p).resolve()
            if path_obj.exists() and path_obj.is_dir() and str(path_obj).lower() not in seen:
                seen.add(str(path_obj).lower())
                target_dirs.append(path_obj)

    matches = []
    ignored_dirs = {".venv", ".git", "__pycache__", "node_modules", ".idea", ".vscode"}

    try:
        for search_root in target_dirs:
            for root, dirs, files in os.walk(search_root):
                # Prune ignored and protected directories in-place
                dirs[:] = [
                    d for d in dirs
                    if d not in ignored_dirs and not is_path_protected(os.path.join(root, d))[0]
                ]
                for file in files:
                    full_path = os.path.join(root, file)
                    if is_path_protected(full_path)[0]:
                        continue
                    if fnmatch.fnmatch(file.lower(), pattern.lower()):
                        if full_path not in matches:
                            matches.append(full_path)
                            if len(matches) >= 10:
                                break
                if len(matches) >= 10:
                    break
            if len(matches) >= 10:
                break

        if not matches:
            res = f"No files matching '{pattern}' were found in approved directories."
        else:
            res = f"Found {len(matches)} match(es) for '{pattern}':\n" + "\n".join(f"- {m}" for m in matches)

        log_audit_entry("find_file", RiskLevel.LOW, {"pattern": pattern, "matches": len(matches)}, "SUCCESS", f"{len(matches)} matches")
        return mark_untrusted(res, f"File search: {pattern}")
    except Exception as e:
        msg = f"Error searching for files: {str(e)}"
        log_audit_entry("find_file", RiskLevel.LOW, {"pattern": pattern}, "ERROR", msg)
        return msg


def open_file(filepath: str) -> str:
    """Opens a document or file with its default system application (MEDIUM risk - requires confirmation).

    Args:
        filepath: The path of the file to open. Must reside within approved directories.

    Returns:
        Confirmation message of file launch, or an error/cancellation message.
    """
    is_valid, resolved_path, err = validate_path(filepath, must_exist=True)

    if not is_valid or not resolved_path:
        log_audit_entry("open_file", RiskLevel.MEDIUM, {"filepath": filepath}, "BLOCKED", err)
        return f"Cannot open file: {err}"

    if not resolved_path.is_file():
        msg = f"'{resolved_path}' is a directory, not a file. Use list_files instead."
        log_audit_entry("open_file", RiskLevel.MEDIUM, {"filepath": str(resolved_path)}, "ERROR", msg)
        return msg

    # Confirmation Gate (MEDIUM Risk)
    prompt = f"Open file '{resolved_path.name}' ({resolved_path}) in its default program?"
    confirmed = confirm_action(RiskLevel.MEDIUM, prompt)

    if not confirmed:
        msg = f"Action cancelled: Opening '{resolved_path.name}' was declined by user."
        log_audit_entry("open_file", RiskLevel.MEDIUM, {"filepath": str(resolved_path)}, "BLOCKED", msg)
        return msg

    if getattr(config, "dry_run", False):
        msg = f"[DRY-RUN] Would open file '{resolved_path.name}' in its default application."
        log_audit_entry("open_file", RiskLevel.MEDIUM, {"filepath": str(resolved_path), "dry_run": True}, "SUCCESS", msg)
        return msg

    try:
        os.startfile(str(resolved_path))
        msg = f"Opened file '{resolved_path.name}' successfully in its default application."
        log_audit_entry("open_file", RiskLevel.MEDIUM, {"filepath": str(resolved_path)}, "SUCCESS", msg)
        return msg
    except Exception as e:
        msg = f"Failed to open '{resolved_path.name}': {str(e)}"
        log_audit_entry("open_file", RiskLevel.MEDIUM, {"filepath": str(resolved_path)}, "ERROR", msg)
        return msg
