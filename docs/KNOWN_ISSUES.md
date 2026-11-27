# JARVIS — Known Issues & Architectural Constraints

This document details known architectural constraints, verified failure modes, and open engineering challenges in JARVIS, backed by diagnostic evidence from production logs (`logs/jarvis.log`) and local test runs.

---

## 1. Groq HTTP 400 & Mislabeled Provider Failures

### Evidence
During multi-step UI reasoning and heavy tool execution, calls to the Groq API (`https://api.groq.com/openai/v1/chat/completions`) occasionally failed with `BadRequestError (HTTP 400)`. In earlier versions, this was caught by a generic catch-all that logged:
```text
Groq connection error (BadRequestError), using Ollama
```
This misclassification placed the primary cloud provider on an unnecessary cooldown and triggered premature fallback.

### Root Causes
1. **History Inconsistency**: Trimming conversation history to meet token limits previously severed assistant `tool_calls` from their corresponding tool response messages, which the Groq API strictly rejects with HTTP 400.
2. **Reasoning Token Depletion**: Extended thinking / reasoning models (`openai/gpt-oss-20b`) exhausted max completion tokens during internal reasoning steps, returning `finish_reason: length` with empty text content.
3. **Complex Tool Schemas**: Passing full sets of large JSON schemas during high-concurrency tool turns occasionally triggered `tool_use_failed` or `failed_generation` errors.

### Mitigation & Current Status
- Error classification now inspects exact HTTP status codes and response bodies (scrubbed, truncated to 300 chars).
- HTTP 400 errors with code `tool_use_failed` trigger an immediate retry on the same provider with low temperature (`T=0.2`), followed by a retry using a compact core tool subset before falling back.
- While significantly more resilient, Groq tool-calling remains subject to upstream service-side generation errors under heavy prompt loads.

---

## 2. Operator Fallback Gating on Local Ollama Models

### Architectural Constraint
When the primary cloud provider (Groq) is unavailable or rate-limited, JARVIS automatically falls back to a local Ollama instance (`llama3.2:3b`).

### Rationale & Evidence
Small local language models (such as 3B-parameter models) lack the spatial grounding, coordinate perception, and rigorous multi-step JSON schema discipline required for safe desktop operation. In testing, unconstrained local models would hallucinate nonexistent UI automation IDs, select wrong controls, or type unintended text into active windows.

### Current Behavior
- Standard conversational queries, local system commands, and knowledge retrieval execute seamlessly on Ollama.
- Computer control tools (`operate`, desktop clicks, typing) are **strictly omitted and gated** when Ollama is the active provider.
- If Groq encounters a rate limit or failure mid-task during an `operate()` workflow, JARVIS halts and informs the user rather than switching to Ollama mid-operation.

---

## 3. Chrome Automation & Dedicated Profile Locking

### The Problem
Automating Google Chrome via browser automation frameworks (like Playwright) while preserving existing cookies and logins requires accessing the user's Chrome profile. However, Chromium strictly enforces an exclusive single-process lock on `%LOCALAPPDATA%\Google\Chrome\User Data`. Launching Playwright against the active user data directory while Chrome is open fails immediately with an OS file-locking exception.

### Architecture & Workaround
1. **Default Mode (`browser_mode: normal`)**: JARVIS targets the running Chrome window directly via Windows UI Automation (pywinauto UIA). It inspects native accessibility nodes (tabs, address bar, buttons, links) without requiring debugging ports or profile locks.
2. **Dedicated Profile Mode (`browser_mode: jarvis_profile`)**: A separate profile directory (`browser_profile/`) can be launched via `--setup-browser` for users who wish to authenticate into sites specifically for headless/headed Playwright workflows.

---

## 4. Action Risk Classification & Confirmation Friction

### The Problem
Early prototypes required explicit user confirmation before every individual mouse click. When navigating a web page or dialog, requiring 5-10 consecutive confirmations made automated workflows impractical.

### Current Architecture
Actions are classified into three distinct risk tiers:
- **LOW Risk**: Reading windows, listing UI controls, scrolling, window focus, waiting, and ordinary clicks on pre-approved applications or domains. These execute autonomously once initial app/domain approval is granted.
- **MEDIUM Risk**: Typing arbitrary text into input controls and text fields. Prompts the user (`Proceed? (y/yes) [unclear answer = no]: `).
- **HIGH Risk**: Sensitive operations matching destructive keywords (`buy`, `pay`, `delete`, `order`, `transfer`, `download`, `install`, `submit`). These mandate explicit typed confirmation on the keyboard and **cannot be approved by voice**.
- **Financial/Banking Blocklist**: Sensitive domains (`paypal.com`, `chase.com`, `stripe.com`, `1password.com`, etc.) require typed confirmation to open in a browser, and automated clicking/typing inside them is unconditionally refused.

---

## 5. Microphone Zero Signal (RMS 0.0000)

### Evidence
During `--doctor` diagnostics, the audio subsystem measures ambient microphone energy over a 2-second sampling window. On certain systems, the measurement returned:
```text
RMS: 0.0000, Peak: -100.0 dB
```

### Causes
1. **Windows Privacy Settings**: Windows 10/11 "Allow desktop apps to access your microphone" was toggled off, blocking Python from reading the audio stream.
2. **Physical / Hardware Mute**: Headset or microphone inline hardware mute switches engaged.
3. **Default Device Misconfiguration**: Windows defaulting to a virtual audio cable or disconnected stereo mix.

### Current Mitigation
- `--doctor` explicitly flags `[WARNING] Microphone returned zero signal (RMS 0.0000)` and provides remediation instructions.
- `--mic-test` provides an interactive 3-second recording with a visual ASCII loudness bar and real-time Whisper transcription to quickly diagnose mic health.
- Audio capture discards near-silence buffers and filters common Whisper silence filler hallucinations (`"Thank you."`, `"You"`, `"Bye"`, `"Thank you for watching."`).

---

## 6. Application UI Idiosyncrasies (Calculator & Netflix)

### Windows Calculator
- **Issue**: The modern Windows 11 Calculator does not name its keypad buttons `"5"`, `"+"`, or `"="`. Its Windows UI Automation tree names them `"Five"`, `"Plus"`, and `"Equals"`, with AutomationIds such as `num5Button`, `plusButton`, and `equalButton`.
- **Solution**: Handled via `jarvis/control/calculator_mapping.py`, which provides a bidirectional alias dictionary mapping arithmetic symbols and words to native UIA control names.

### Netflix & Dynamic Media Players
- **Issue**: Netflix uses canvas rendering, delayed video player control overlays, and profile selection interstitials that do not always expose standard UIA accessibility trees.
- **Solution**: Dedicated fast paths (`netflix_play`, `youtube_play`) provide direct URL navigation to reduce reliance on heuristic DOM navigation.

---

## 7. Test Suite Log Isolation

### Historical Issue
Previously, running `pytest` would append test warning logs (such as simulated rate limits or fallback triggers) directly into the production `logs/jarvis.log` and `logs/audit.log` files, making it difficult to distinguish real user runtime issues from synthetic test assertions.

### Current Status
**Resolved.** A session-level fixture in `tests/conftest.py` redirects all logging handlers, audit log writers, and settings paths to an isolated temporary directory during testing. Production `logs/` remains 100% untouched during test runs.
