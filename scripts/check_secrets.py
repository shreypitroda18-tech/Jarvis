#!/usr/bin/env python3
"""JARVIS Git Secret & Protected Path Scanner (Standard Library Only).

Scans Git staged changes or outgoing commits to prevent accidental leakage
of API keys, credentials, tokens, private keys, and protected project files.
"""

import sys
import os
import re
import fnmatch
import subprocess
import argparse
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any


# -----------------------------------------------------------------------------
# Rules Configuration
# -----------------------------------------------------------------------------

SKIP_MARKER = "fake-secret-ok"

SECRET_PATTERNS: List[Tuple[str, re.Pattern, str]] = [
    (
        "Groq API Key (gsk_ followed by 40+ alphanumerics)",
        re.compile(r"gsk_[A-Za-z0-9]{40,}"),
        "Move secret to .env and load via os.getenv(), or append '# fake-secret-ok' if this is a test fixture."
    ),
    (
        "Google / Gemini API Key (AIza followed by 35 characters)",
        re.compile(r"AIza[A-Za-z0-9_\-]{35}"),
        "Move secret to .env and load via os.getenv(), or append '# fake-secret-ok' if this is a test fixture."
    ),
    (
        "OpenAI API Key (sk- followed by 40+ characters)",
        re.compile(r"sk-[A-Za-z0-9_\-]{40,}"),
        "Move secret to .env and load via os.getenv(), or append '# fake-secret-ok' if this is a test fixture."
    ),
    (
        "Authorization Bearer Token",
        re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{20,}"),
        "Remove hardcoded Bearer token from source, or append '# fake-secret-ok' if this is a test fixture."
    ),
    (
        "Private Key Header",
        re.compile(r"-----BEGIN[ A-Z0-9_-]*PRIVATE KEY-----"),
        "Store private keys in a secure vault outside git, or append '# fake-secret-ok' if this is a test fixture."
    ),
]


class Violation:
    def __init__(self, file_path: str, line_no: Optional[int], rule: str, fix: str):
        self.file_path = file_path
        self.line_no = line_no
        self.rule = rule
        self.fix = fix

    def __str__(self) -> str:
        loc = f"{self.file_path}:{self.line_no}" if self.line_no is not None else self.file_path
        return f"  File: {loc}\n  Rule: {self.rule}\n  Fix:  {self.fix}"


def is_protected_path(file_path: str) -> Optional[Tuple[str, str]]:
    """Checks whether a staged/committed file path violates protected-path policies.

    Returns (rule_name, fix_suggestion) or None if allowed.
    """
    clean_path = file_path.replace("\\", "/").strip().strip('"').strip("'")
    if not clean_path:
        return None

    path_obj = Path(clean_path)
    base = path_obj.name.lower()
    parts = [p.lower() for p in path_obj.parts]

    # Exemption: .env.example is explicitly allowed
    if base == ".env.example":
        return None

    # 1. .env and .env.* (e.g. .env.local, .env.prod)
    if base == ".env" or base.startswith(".env."):
        return (
            "Protected path (.env*)",
            f"Remove with 'git rm --cached \"{clean_path}\"' and add to .gitignore."
        )

    # 2. 'API key' folder
    if "api key" in parts:
        return (
            "Protected folder ('API key')",
            f"Remove with 'git rm -r --cached \"{clean_path}\"' and store keys in .env."
        )

    # 3. browser_profile folder
    if "browser_profile" in parts:
        return (
            "Protected folder ('browser_profile')",
            f"Remove with 'git rm -r --cached \"{clean_path}\"' and add browser_profile/ to .gitignore."
        )

    # 4. logs folder
    if "logs" in parts:
        return (
            "Protected folder ('logs')",
            f"Remove with 'git rm -r --cached \"{clean_path}\"' and add logs/ to .gitignore."
        )

    # 5. screenshots folder
    if "screenshots" in parts:
        return (
            "Protected folder ('screenshots')",
            f"Remove with 'git rm -r --cached \"{clean_path}\"' and add screenshots/ to .gitignore."
        )

    # 6. Specific credential & catalog files
    if base == "apps_index.json":
        return (
            "Protected file (apps_index.json)",
            f"Remove with 'git rm --cached \"{clean_path}\"' (cached index should not be tracked)."
        )

    if base == "credentials.json":
        return (
            "Protected file (credentials.json)",
            f"Remove with 'git rm --cached \"{clean_path}\"' and add to .gitignore."
        )

    if fnmatch.fnmatch(base, "token*.json"):
        return (
            "Protected token file (token*.json)",
            f"Remove with 'git rm --cached \"{clean_path}\"' and add to .gitignore."
        )

    # 7. Certificate & key extensions
    if base.endswith((".pem", ".key", ".pfx")):
        ext = path_obj.suffix.lower()
        return (
            f"Protected key/certificate file (*{ext})",
            f"Remove with 'git rm --cached \"{clean_path}\"' and store certificates outside repository."
        )

    return None


def run_git_command(args: List[str], repo_root: Optional[str] = None) -> Tuple[int, str, str]:
    """Runs a git command and returns (exit_code, stdout, stderr)."""
    try:
        proc = subprocess.run(
            ["git"] + args,
            cwd=repo_root or os.getcwd(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace"
        )
        return proc.returncode, proc.stdout, proc.stderr
    except Exception as e:
        return 1, "", str(e)


def get_staged_files(repo_root: Optional[str] = None) -> List[Tuple[str, str]]:
    """Returns list of (status, file_path) for staged files."""
    code, stdout, _ = run_git_command(["diff", "--cached", "--name-status", "-z"], repo_root)
    if code != 0 or not stdout:
        return []

    entries = stdout.split("\0")
    files: List[Tuple[str, str]] = []
    idx = 0
    while idx < len(entries):
        status_token = entries[idx].strip()
        if not status_token:
            idx += 1
            continue
        status = status_token[0]
        idx += 1
        if idx >= len(entries):
            break
        path1 = entries[idx]
        idx += 1

        # For renames (R) or copies (C), git emits old_path then new_path
        if status in ("R", "C") and idx < len(entries):
            path2 = entries[idx]
            idx += 1
            files.append((status, path2))
        else:
            files.append((status, path1))

    return files


def scan_diff_content(diff_text: str) -> List[Violation]:
    """Scans git diff text for secret patterns in ADDED lines only."""
    violations: List[Violation] = []
    current_file = ""
    current_line = 0

    hunk_header_re = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

    for raw_line in diff_text.splitlines():
        if raw_line.startswith("diff --git "):
            parts = raw_line.split(" ")
            if len(parts) >= 4:
                current_file = parts[3].lstrip("b/")
            continue

        if raw_line.startswith("+++ "):
            continue

        if raw_line.startswith("--- "):
            continue

        if raw_line.startswith("@@"):
            match = hunk_header_re.match(raw_line)
            if match:
                current_line = int(match.group(2))
            continue

        # Check added lines
        if raw_line.startswith("+"):
            line_content = raw_line[1:]

            # Exemption marker check
            if SKIP_MARKER in line_content:
                current_line += 1
                continue

            # Check secret regexes
            for rule_name, pattern, fix_msg in SECRET_PATTERNS:
                if pattern.search(line_content):
                    violations.append(Violation(
                        file_path=current_file,
                        line_no=current_line,
                        rule=rule_name,
                        fix=fix_msg
                    ))
                    # Only report first matching rule per line
                    break

            current_line += 1
        elif not raw_line.startswith("-"):
            current_line += 1

    return violations


def scan_staged_changes(repo_root: Optional[str] = None) -> List[Violation]:
    """Scans staged files and diff for pre-commit verification."""
    violations: List[Violation] = []

    # 1. Check staged file paths
    staged = get_staged_files(repo_root)
    for status, file_path in staged:
        # If file is deleted, path rule doesn't block removal
        if status == "D":
            continue
        path_violation = is_protected_path(file_path)
        if path_violation:
            rule, fix = path_violation
            violations.append(Violation(
                file_path=file_path,
                line_no=None,
                rule=rule,
                fix=fix
            ))

    # 2. Check staged content diff
    code, stdout, _ = run_git_command(["diff", "--cached", "-U0", "--no-color"], repo_root)
    if code == 0 and stdout:
        violations.extend(scan_diff_content(stdout))

    return violations


def scan_commit_range(commit_range: str, repo_root: Optional[str] = None) -> List[Violation]:
    """Scans file paths and diff content across a specified commit range."""
    violations: List[Violation] = []

    # 1. Check modified/added file paths in commit range
    code, stdout, _ = run_git_command(["diff", "--name-status", "-z", commit_range], repo_root)
    if code == 0 and stdout:
        entries = stdout.split("\0")
        idx = 0
        while idx < len(entries):
            st = entries[idx].strip()
            if not st:
                idx += 1
                continue
            status = st[0]
            idx += 1
            if idx >= len(entries):
                break
            path1 = entries[idx]
            idx += 1
            target_path = path1
            if status in ("R", "C") and idx < len(entries):
                target_path = entries[idx]
                idx += 1
            if status != "D":
                path_violation = is_protected_path(target_path)
                if path_violation:
                    rule, fix = path_violation
                    violations.append(Violation(target_path, None, rule, fix))

    # 2. Check diff content in commit range
    code, stdout, _ = run_git_command(["diff", "-U0", "--no-color", commit_range], repo_root)
    if code == 0 and stdout:
        violations.extend(scan_diff_content(stdout))

    return violations


def scan_pre_push(repo_root: Optional[str] = None) -> List[Violation]:
    """Scans outgoing commits during git pre-push hook execution."""
    violations: List[Violation] = []

    # Standard git pre-push reads ref updates from stdin:
    # <local_ref> <local_sha> <remote_ref> <remote_sha>
    stdin_content = ""
    if not sys.stdin.isatty():
        try:
            stdin_content = sys.stdin.read().strip()
        except Exception:
            stdin_content = ""

    if stdin_content:
        for line in stdin_content.splitlines():
            parts = line.strip().split()
            if len(parts) >= 4:
                local_ref, local_sha, remote_ref, remote_sha = parts[:4]
                # If local_sha is all 0s, branch is being deleted
                if local_sha == "0" * 40:
                    continue

                # If remote_sha is all 0s, new branch is being pushed
                if remote_sha == "0" * 40:
                    # Scan commits reachable from local_sha but not on remote branches
                    rev_cmd = ["rev-list", local_sha, "--not", "--remotes"]
                    code, revs, _ = run_git_command(rev_cmd, repo_root)
                    if code == 0 and revs.strip():
                        # Scan commits found
                        commits = revs.strip().splitlines()
                        oldest_commit = commits[-1]
                        code_range, _, _ = run_git_command(["rev-parse", f"{oldest_commit}~1"], repo_root)
                        if code_range == 0:
                            commit_range = f"{oldest_commit}~1..{local_sha}"
                        else:
                            # Root commit push - compare against empty tree
                            code_empty, empty_tree, _ = run_git_command(["hash-object", "-t", "tree", os.devnull], repo_root)
                            empty_sha = empty_tree.strip() if (code_empty == 0 and empty_tree.strip()) else "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
                            commit_range = f"{empty_sha}..{local_sha}"
                        violations.extend(scan_commit_range(commit_range, repo_root))
                else:
                    violations.extend(scan_commit_range(f"{remote_sha}..{local_sha}", repo_root))
    else:
        # Fallback when run manually or without stdin pipe:
        # Check outgoing commits against upstream tracking branch or HEAD~1
        code, upstream, _ = run_git_command(["rev-parse", "--abbrev-ref", "@{upstream}"], repo_root)
        if code == 0 and upstream.strip():
            violations.extend(scan_commit_range(f"{upstream.strip()}..HEAD", repo_root))
        else:
            # Check last commit
            code_head, _, _ = run_git_command(["rev-parse", "HEAD~1"], repo_root)
            if code_head == 0:
                violations.extend(scan_commit_range("HEAD~1..HEAD", repo_root))
            else:
                code_empty, empty_tree, _ = run_git_command(["hash-object", "-t", "tree", os.devnull], repo_root)
                empty_sha = empty_tree.strip() if (code_empty == 0 and empty_tree.strip()) else "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
                violations.extend(scan_commit_range(f"{empty_sha}..HEAD", repo_root))

    return violations


# -----------------------------------------------------------------------------
# Main Entry Point
# -----------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="JARVIS Git Secret & Protected Path Scanner")
    parser.add_argument("--staged", action="store_true", help="Scan Git staged changes (pre-commit)")
    parser.add_argument("--pre-push", action="store_true", help="Scan Git outgoing commits (pre-push)")
    parser.add_argument("--range", dest="commit_range", type=str, help="Scan an explicit Git commit range")
    parser.add_argument("--repo-root", type=str, default=None, help="Repository root directory")

    args = parser.parse_args()

    repo_root = args.repo_root or os.getcwd()

    if args.commit_range:
        violations = scan_commit_range(args.commit_range, repo_root)
    elif args.pre_push:
        violations = scan_pre_push(repo_root)
    else:
        # Default to staged changes (pre-commit)
        violations = scan_staged_changes(repo_root)

    if violations:
        print("\n=======================================================================", file=sys.stderr)
        print(" [SECURITY ERROR] Git Secret-Scanning Safety Net Refused Operation", file=sys.stderr)
        print("=======================================================================", file=sys.stderr)
        print(f"Found {len(violations)} security violation(s) in changes:\n", file=sys.stderr)
        for idx, v in enumerate(violations, 1):
            print(f"[{idx}]\n{v}\n", file=sys.stderr)
        print("=======================================================================", file=sys.stderr)
        print("Action refused: Sensitive credentials or protected files detected.", file=sys.stderr)
        print("Fix the issues above or append '# fake-secret-ok' to test fixture lines.\n", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
