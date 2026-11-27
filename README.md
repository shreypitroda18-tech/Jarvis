# JARVIS — Windows AI Voice & Desktop Assistant

JARVIS (*Just A Rather Very Intelligent System*) is a modular, privacy-conscious voice and desktop assistant built specifically for Windows 10/11 in Python 3.13. Designed with a calm British butler persona ("sir"), JARVIS combines zero-latency local execution for common Windows commands with a dual-brain LLM architecture (**Groq Cloud** as the primary brain with **local Ollama** as an automatic offline fallback) and code-enforced safety gates.

> [!CAUTION]
> **SAFETY NOTICE**:
> JARVIS has the ability to open applications, type text, click UI elements, and navigate web pages on your computer.
> - **Run JARVIS only on a machine you own or control.** Never run on shared or untrusted workstations.
> - **Keep the autonomous computer operator disabled** (`operator.enabled: false`) unless you understand the risks and are actively monitoring execution.
> - **Never commit `.env`, credentials, or private API keys.** Always install the repository's git secret-scanning hooks and **never use `--no-verify`**.
> - For responsible disclosure and reporting vulnerabilities, see [SECURITY.md](SECURITY.md).

---

## What Works Today (Reliable Core)

- **Interactive Text Interface (`--text`)**: Clean terminal chat loop with natural conversation and instant tool dispatch.
- **Push-to-Talk Voice Loop (`--voice`)**:
  - Activation via `F8` hotkey with audible audio chimes.
  - Offline Speech-to-Text powered by local `faster-whisper` (`small` model in `int8` on CPU/CUDA).
  - Fast text-to-speech powered by Windows SAPI (`pyttsx3`) with code and markdown filtering.
  - Zero idle microphone recording: the microphone stream is opened strictly during push-to-talk and closed immediately.
  - Voice safety gating: MEDIUM-risk actions require the exact spoken phrase `"yes, confirm"`, while HIGH-risk actions mandate typed keyboard confirmation.
- **Zero-Latency Local Command Layer**:
  - Core queries (current time and date, master volume level, mute/unmute, CPU/RAM/battery stats, screen captures, and media playback keys) execute deterministically in **<5 milliseconds** with zero LLM API calls and zero token cost.
- **Application Discovery & Launching**:
  - Dynamic discovery of Start Menu shortcuts and Microsoft Store / UWP apps with fuzzy matching (e.g. `"calc"` -> Calculator).
  - Strict security blocking against malware vectors (format, diskpart, setup, uninstallers).
- **Secure Web Navigation & Search**:
  - Direct opening of HTTPS URLs and common web favorites (YouTube, Netflix, GitHub, Spotify, Amazon, Wikipedia, etc.).
  - Direct site searches (`open_site_search`) across popular platforms.
  - Unconditional blocking of dangerous schemes (`file:`, `javascript:`, `data:`, `vbscript:`, `ftp:`).
- **Multi-Provider Dual Brain with Graceful Fallback**:
  - Primary: Ultra-fast cloud inference on Groq (`llama-3.1-8b-instant`).
  - Fallback: Local offline inference on Ollama (`llama3.2:3b`).
  - Truthful HTTP error classification: HTTP 400 tool errors retry at low temperature (`T=0.2`), HTTP 413 shrinks history, HTTP 429 parses `Retry-After` headers, and configuration errors (401/404) report clearly without false cooldowns.
- **Code-Enforced Safety Architecture**:
  - Protected paths (`.env`, credentials, browser profiles, private keys) can never be opened, read, or typed into.
  - Protected administrative shells (`cmd`, `powershell`, `wt`, `regedit`) are blocked from automated inputs.
  - Ordinary clicks on pre-approved apps/domains require no repetitive confirmations; typing is MEDIUM risk; destructive actions (delete, pay, buy, order, transfer) mandate typed confirmation.
- **Git Secret-Scanning Safety Net**:
  - Code-enforced pre-commit and pre-push Git hooks that block accidental commits of API keys, tokens, or credentials.

---

## What is Experimental (Computer Operator)

JARVIS includes a prototype autonomous Observe-Decide-Act loop (`operate`) that perceives active Windows UI Automation (pywinauto UIA) trees and executes clicks and keystrokes.

> [!WARNING]
> **The General Computer Operator is currently EXPERIMENTAL and DISABLED by default.**
> 
> **Why it is experimental:**
> 1. **Upstream LLM Tool Fragility**: Complex multi-step UI reasoning over deep accessibility trees occasionally triggers Groq HTTP 400 errors (`tool_use_failed` or `failed_generation`).
> 2. **Ollama Safety Gating**: Small local models (`llama3.2:3b`) lack reliable spatial grounding and structured action discipline. To protect your system, computer control tools are strictly disabled when running on Ollama. If Groq hits cooldown mid-task, the operator halts rather than risking ungrounded local actions.
> 3. **Dynamic UI Overlays**: Certain modern web applications (like Netflix video player overlays and custom canvas widgets) do not reliably expose standard UIA accessibility trees.

### How to Enable the Operator
If you wish to test or develop the operator loop, open `jarvis/settings.yaml` and set:
```yaml
operator:
  enabled: true
```
You can then run in debug mode (`--operate-debug`) to inspect live element perception and action decisions, or in dry-run mode (`--dry-run`) to plan actions without physical mouse/keyboard execution. See [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md) for detailed technical evidence.

---

## Windows Setup Guide

### 1. Prerequisites
- **Operating System**: Windows 10 or Windows 11 (64-bit).
- **Python**: Python 3.11, 3.12, or 3.13 installed and added to your `PATH`.
- **Git**: Git for Windows installed.

### 2. Clone and Setup Environment
Open PowerShell and clone the repository:
```powershell
git clone https://github.com/your-username/jarvis.git
cd jarvis
```

Create and activate a dedicated virtual environment:
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install runtime dependencies:
```powershell
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Configure API Credentials
Copy the environment template:
```powershell
Copy-Item .env.example .env
```
Open `.env` in your text editor and insert your **Groq API Key**:
```env
LLM_PROVIDER=groq
LLM_FALLBACKS=ollama

GROQ_API_KEY=gsk_your_actual_groq_api_key_here
GROQ_MODEL=llama-3.1-8b-instant
```
*(Get a free Groq key at [console.groq.com](https://console.groq.com)).*

### 4. Configure Application Settings
Copy the settings template:
```powershell
Copy-Item jarvis/settings.example.yaml jarvis/settings.yaml
```
Customize `jarvis/settings.yaml` with your preferred apps, whitelisted paths, browser preference, and voice settings.
> [!NOTE]
> If `jarvis/settings.yaml` is not present, JARVIS automatically falls back to `jarvis/settings.example.yaml` with safe defaults (`operator.enabled: false`) and displays a clear one-line notice.

### 5. Install Local Ollama (Recommended Fallback)
1. Download and install Ollama from [ollama.com](https://ollama.com).
2. Open a terminal and pull the default lightweight model:
   ```powershell
   ollama pull llama3.2:3b
   ```
3. Start the Ollama server (or let the background taskbar app run):
   ```powershell
   ollama serve
   ```

### 6. Install Git Secret-Scanning Hooks
To ensure you never accidentally commit credentials or sensitive files:
```powershell
powershell -ExecutionPolicy Bypass -File scripts/install_hooks.ps1
```

---

## Execution Commands

| Mode / Tool | Command | Description |
| :--- | :--- | :--- |
| **Interactive Text Mode** | `python -m jarvis.main --text` | Interactive terminal conversation (Default). |
| **Voice Mode** | `python -m jarvis.main --voice` | Push-to-Talk voice interface (`F8` to speak). |
| **System Diagnostics** | `python -m jarvis.main --doctor` | Full audit of providers, audio devices, and policies. |
| **Microphone Test** | `python -m jarvis.main --mic-test` | 3s recording with visual loudness bar and Whisper test. |
| **List Discovered Apps** | `python -m jarvis.main --list-apps` | Displays catalog of indexed Windows and Store apps. |
| **Refresh App Index** | `python -m jarvis.main --refresh-apps` | Re-scans Start Menu shortcuts and Store applications. |
| **List Audio Devices** | `python -m jarvis.main --list-audio-devices` | Lists all audio input and output devices with indices. |
| **List Configured Models** | `python -m jarvis.main --list-models` | Displays models available on Groq, Ollama, and Gemini. |
| **Operator Debugging** | `python -m jarvis.main --text --operate-debug` | Prints live UIA observations and LLM decisions. |
| **Dry-Run Planning** | `python -m jarvis.main --text --dry-run` | Runs full reasoning loops without physical clicks or typing. |

### Running Automated Tests
The repository includes comprehensive unit and integration tests with complete log and setting isolation:
```powershell
pytest
```
To run tests with full per-test verbosity:
```powershell
pytest -v
```

---

## The Safety Model in Plain Words

JARVIS adheres to strict code-level guardrails designed to prevent accidental damage, credential leakage, or unauthorized actions:

1. **Read-Only vs Destructive Actions**:
   - Diagnostic queries, time checks, volume readings, and UI observations are classified as **LOW risk** and execute freely.
   - Text entry into active fields is classified as **MEDIUM risk** and requests permission (`Proceed? (y/yes) [unclear answer = no]: `).
   - Destructive keywords (`delete`, `buy`, `pay`, `order`, `transfer`, `install`, `shutdown`) are classified as **HIGH risk** and mandate **TYPED confirmation**. High-risk actions cannot be approved by voice.
2. **Strict Administrative Shell Blocking**:
   - JARVIS detects active windows belonging to administrative shells (`cmd.exe`, `powershell.exe`, `windowsterminal.exe`, `regedit.exe`, `taskmgr.exe`, and installers).
   - Automated clicks, typing, or key combinations targeting administrative tools are **unconditionally blocked**.
3. **Protected Path Denylist**:
   - Any file operation or operator typing directed at protected project assets (`.env`, `credentials.json`, `token*.json`, `*.pem`, `*.key`, `browser_profile/`, `logs/`) is rejected at the API boundary.
4. **Sensitive Domain Blocklist**:
   - Financial, banking, and credential services (`paypal.com`, `chase.com`, `stripe.com`, `1password.com`, `bitwarden.com`, etc.) require typed confirmation to open in a browser, and autonomous clicking/typing inside them is strictly refused.
5. **Emergency Kill Switches**:
   - **Spoken stop**: Say `"stop"` during voice execution to immediately cut audio and cancel operations.
   - **Emergency hotkey**: Press `Ctrl+Alt+S` at any time to instantly abort automated operations.
   - **Failsafe corner**: Move the mouse cursor to the upper-left corner of the primary display (`pyautogui.FAILSAFE`).

---

## Daily GitHub Routine & Secret Scanner

JARVIS enforces zero credential leakage through versioned pre-commit and pre-push hooks located in `scripts/git_hooks/`.

### Routine Workflow
1. Make your changes and write unit tests.
2. Run pytest to ensure all tests pass:
   ```powershell
   pytest
   ```
3. Stage changes and commit:
   ```powershell
   git add .
   git commit -m "Your descriptive commit message"
   ```
   *The pre-commit scanner automatically checks staged files and line diffs for API keys, tokens, and protected paths.*
4. Push to remote:
   ```powershell
   git push origin main
   ```
   *The pre-push scanner automatically verifies the outgoing commit range before upload.*

### Strict Rule: Never Use `--no-verify`
> [!CAUTION]
> **NEVER USE `git commit --no-verify` OR `git push --no-verify`**.
> The `--no-verify` flag disables the secret scanner and bypasses security protections. Commits or pushes using `--no-verify` violate repository security policy.

### Test Fixtures Marker (`# fake-secret-ok`)
When authoring unit tests that require synthetic dummy keys or authorization headers, append the marker `# fake-secret-ok` to the line:
```python
fake_key = "gsk_DummySecretKey12345678901234567890123456789012"  # fake-secret-ok
```
The scanner detects this marker and permits harmless test fixtures while preventing any real secret leakage.

---

## Additional Documentation

- [Security Policy & Guidelines](SECURITY.md): Full safety architecture, capability warnings, and vulnerability reporting.
- [Known Issues & Constraints](docs/KNOWN_ISSUES.md): Real failure modes, log evidence, and technical bottlenecks.
- [Engineering Roadmap](docs/ROADMAP.md): Future architectural phases (Memory, Routines, Integrations, HUD, and Vision).

---

## License

This project is licensed under the [MIT License](LICENSE) — Copyright (c) 2026 shreypitroda18-tech.
