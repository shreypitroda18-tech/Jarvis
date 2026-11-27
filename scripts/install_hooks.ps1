# JARVIS Git Hook Installer for Windows
# Configures Git to use versioned hooks in scripts/git_hooks and verifies Python environment.

$ErrorActionPreference = "Stop"

Write-Host "Installing JARVIS Git hooks..." -ForegroundColor Cyan

# Verify git is available
try {
    $gitVersion = git --version
    Write-Host "Git detected: $gitVersion" -ForegroundColor Green
} catch {
    Write-Error "Git command not found. Please install Git for Windows and ensure it is in PATH."
    exit 1
}

# Ensure we are inside a Git repository
$repoRoot = git rev-parse --show-toplevel 2>$null
if (-not $repoRoot) {
    Write-Error "Not inside a Git repository."
    exit 1
}
Set-Location $repoRoot

# Configure Git hooks path
Write-Host "Configuring git core.hooksPath to scripts/git_hooks..."
git config core.hooksPath scripts/git_hooks
$configuredPath = git config core.hooksPath
if ($configuredPath -ne "scripts/git_hooks") {
    Write-Error "Failed to set core.hooksPath. Current value: $configuredPath"
    exit 1
}
Write-Host "core.hooksPath set to: $configuredPath" -ForegroundColor Green

# Verify virtual environment Python
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    Write-Host "Verified virtual environment Python: $venvPython" -ForegroundColor Green
    $scannerScript = Join-Path $repoRoot "scripts\check_secrets.py"
    if (Test-Path $scannerScript) {
        & $venvPython $scannerScript --help | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Scanner test run passed: $scannerScript" -ForegroundColor Green
        } else {
            Write-Warning "Scanner returned exit code $LASTEXITCODE during help test."
        }
    }
} else {
    Write-Warning "Virtual environment not found at $venvPython. Hook will fall back to system 'python'."
}

Write-Host "`nJARVIS Git hooks installed successfully!" -ForegroundColor Cyan
Write-Host "Pre-commit and pre-push hooks are active and protecting against secret leakage." -ForegroundColor Cyan
