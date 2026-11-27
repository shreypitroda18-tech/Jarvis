import os
import sys
import subprocess
from pathlib import Path
import pytest

# Ensure scripts folder is importable
REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
SCANNER_SCRIPT = SCRIPTS_DIR / "check_secrets.py"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import check_secrets


@pytest.fixture
def temp_git_repo(tmp_path):
    """Creates an isolated temporary Git repository for testing."""
    repo = tmp_path / "test_repo"
    repo.mkdir()

    # Initialize git repo
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=repo, check=True, capture_output=True)

    # Initial commit so HEAD exists
    initial_file = repo / "init.txt"
    initial_file.write_text("initial commit\n", encoding="utf-8")
    subprocess.run(["git", "add", "init.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo, check=True, capture_output=True)

    return repo


def run_scanner(repo_path: Path, args: list[str], stdin_input: str = ""):
    """Runs check_secrets.py in subprocess with provided arguments."""
    cmd = [sys.executable, str(SCANNER_SCRIPT)] + args
    result = subprocess.run(
        cmd,
        cwd=repo_path,
        input=stdin_input,
        capture_output=True,
        text=True,
        encoding="utf-8"
    )
    return result


def test_clean_commit_passes(temp_git_repo):
    """Verifies that clean changes pass scanner with exit code 0."""
    clean_file = temp_git_repo / "clean_code.py"
    clean_file.write_text("def hello():\n    return 'world'\n", encoding="utf-8")

    subprocess.run(["git", "add", "clean_code.py"], cwd=temp_git_repo, check=True, capture_output=True)

    res = run_scanner(temp_git_repo, ["--staged"])
    assert res.returncode == 0
    assert "SECURITY ERROR" not in res.stderr


@pytest.mark.parametrize("protected_file", [
    ".env",
    ".env.local",
    ".env.production",
    "credentials.json",
    "token.json",
    "token_oauth.json",
    "apps_index.json",
    "server.pem",
    "id_rsa.key",
    "cert.pfx",
    "API key/groq.txt",
    "browser_profile/Cookies",
    "logs/jarvis.log",
    "screenshots/shot.png",
])
def test_staged_protected_paths_blocked(temp_git_repo, protected_file):
    """Verifies that staging any protected path is blocked."""
    file_path = temp_git_repo / protected_file
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text("dummy content\n", encoding="utf-8")

    subprocess.run(["git", "add", protected_file], cwd=temp_git_repo, check=True, capture_output=True)

    res = run_scanner(temp_git_repo, ["--staged"])
    assert res.returncode == 1
    assert "SECURITY ERROR" in res.stderr
    assert "Protected" in res.stderr


def test_env_example_is_allowed(temp_git_repo):
    """Verifies that .env.example is explicitly allowed."""
    example_env = temp_git_repo / ".env.example"
    example_env.write_text("GROQ_API_KEY=your_key_here\nGEMINI_API_KEY=your_key_here\n", encoding="utf-8")

    subprocess.run(["git", "add", ".env.example"], cwd=temp_git_repo, check=True, capture_output=True)

    res = run_scanner(temp_git_repo, ["--staged"])
    assert res.returncode == 0
    assert "SECURITY ERROR" not in res.stderr


@pytest.mark.parametrize("secret_name,secret_val", [
    ("Groq Key", "gsk_" + "X" * 45),
    ("Gemini Key", "AIza" + "Y" * 35),
    ("OpenAI Key", "sk-" + "Z" * 45),
    ("Bearer Token", "Bearer " + "A" * 25),
    ("Private Key Header", "-----BEGIN RSA PRIVATE KEY-----"),  # fake-secret-ok
])
def test_staged_secrets_blocked_and_never_leaked(temp_git_repo, secret_name, secret_val):
    """Verifies secrets on added lines are blocked and secret values are never printed."""
    secret_file = temp_git_repo / "secrets.py"
    secret_file.write_text(f"API_KEY = '{secret_val}'\n", encoding="utf-8")

    subprocess.run(["git", "add", "secrets.py"], cwd=temp_git_repo, check=True, capture_output=True)

    res = run_scanner(temp_git_repo, ["--staged"])
    assert res.returncode == 1
    assert "SECURITY ERROR" in res.stderr

    # Strict privacy requirement: The secret value itself must NEVER appear in output
    assert secret_val not in res.stdout
    assert secret_val not in res.stderr


def test_skip_marker_allows_test_fixtures(temp_git_repo):
    """Verifies that lines containing 'fake-secret-ok' bypass the scanner."""
    fake_secret = "gsk_" + "F" * 45
    fixture_file = temp_git_repo / "test_fixture.py"
    fixture_file.write_text(
        f"dummy_key = '{fake_secret}'  # fake-secret-ok\n",
        encoding="utf-8"
    )

    subprocess.run(["git", "add", "test_fixture.py"], cwd=temp_git_repo, check=True, capture_output=True)

    res = run_scanner(temp_git_repo, ["--staged"])
    assert res.returncode == 0
    assert "SECURITY ERROR" not in res.stderr


def test_pre_push_catches_outgoing_secret(temp_git_repo):
    """Verifies pre-push scan catches secrets in committed outgoing commits."""
    secret_val = "gsk_" + "P" * 45
    leaky_file = temp_git_repo / "leaky.py"
    leaky_file.write_text(f"secret = '{secret_val}'\n", encoding="utf-8")

    # Stage and commit directly (simulating pre-commit skipped or committed on another branch)
    subprocess.run(["git", "add", "leaky.py"], cwd=temp_git_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Leaked secret commit"], cwd=temp_git_repo, check=True, capture_output=True)

    # Get local HEAD commit SHA
    res_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=temp_git_repo, capture_output=True, text=True, check=True)
    local_sha = res_sha.stdout.strip()

    # Pre-push stdin format: <local_ref> <local_sha> <remote_ref> <remote_sha>
    # Simulating new branch push (remote_sha is 40 zeros)
    push_stdin = f"refs/heads/main {local_sha} refs/heads/main {'0'*40}\n"

    res = run_scanner(temp_git_repo, ["--pre-push"], stdin_input=push_stdin)
    assert res.returncode == 1
    assert "SECURITY ERROR" in res.stderr
    assert secret_val not in res.stdout
    assert secret_val not in res.stderr


def test_pre_push_catches_outgoing_protected_file(temp_git_repo):
    """Verifies pre-push scan catches protected files committed to outgoing branch."""
    env_file = temp_git_repo / ".env.production"
    env_file.write_text("PORT=8000\n", encoding="utf-8")

    subprocess.run(["git", "add", ".env.production"], cwd=temp_git_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Added prod env"], cwd=temp_git_repo, check=True, capture_output=True)

    res_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=temp_git_repo, capture_output=True, text=True, check=True)
    local_sha = res_sha.stdout.strip()

    push_stdin = f"refs/heads/main {local_sha} refs/heads/main {'0'*40}\n"

    res = run_scanner(temp_git_repo, ["--pre-push"], stdin_input=push_stdin)
    assert res.returncode == 1
    assert "SECURITY ERROR" in res.stderr
    assert "Protected path (.env*)" in res.stderr


def test_git_hook_integration_blocks_commit(temp_git_repo):
    """Verifies that Git hooks configured via core.hooksPath actually block git commit."""
    # Create git_hooks directory inside temp_repo
    hooks_dir = temp_git_repo / ".githooks"
    hooks_dir.mkdir()

    pre_commit_hook = hooks_dir / "pre-commit"
    # Write pre-commit hook that calls our check_secrets.py with sys.executable
    pre_commit_hook.write_text(
        f'#!/bin/sh\n"{sys.executable.replace("\\", "/")}" "{str(SCANNER_SCRIPT).replace("\\", "/")}" --staged\nexit $?\n',
        encoding="utf-8"
    )

    # Configure core.hooksPath
    subprocess.run(
        ["git", "config", "core.hooksPath", str(hooks_dir)],
        cwd=temp_git_repo,
        check=True,
        capture_output=True
    )

    # Stage a secret file
    secret_file = temp_git_repo / "forbidden.py"
    secret_file.write_text(f"KEY = 'AIza{'K'*35}'\n", encoding="utf-8")
    subprocess.run(["git", "add", "forbidden.py"], cwd=temp_git_repo, check=True, capture_output=True)

    # Attempt commit - should fail via pre-commit hook
    commit_res = subprocess.run(
        ["git", "commit", "-m", "Should fail"],
        cwd=temp_git_repo,
        capture_output=True,
        text=True
    )
    assert commit_res.returncode != 0
    assert "SECURITY ERROR" in commit_res.stderr or "SECURITY ERROR" in commit_res.stdout
