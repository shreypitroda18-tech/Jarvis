# Security Policy

## Capabilities & Safety Notice

JARVIS is an autonomous AI desktop assistant designed to execute system queries, launch applications, browse the web, and interact directly with the Windows desktop through Windows UI Automation (UIA) and input simulation.

Because this software can interact with your operating system, please adhere to the following safety precautions:

1. **Run Only on Machines You Control**: Never run JARVIS on shared, multi-tenant, production, or untrusted machines. Run it only on your personal workstation or a dedicated virtual machine where you have full administrative oversight.
2. **Keep the Operator Disabled by Default**: The autonomous computer control loop (`operator`) is experimental and is **disabled by default** (`operator.enabled: false` in `settings.example.yaml`). Only enable it if you understand the risks and are prepared to supervise actions.
3. **Emergency Abort Controls**:
   - **Emergency Kill Switch**: Press `Ctrl+Alt+S` at any time to immediately abort ongoing automation loops.
   - **Failsafe Corner**: Slam your mouse cursor into the upper-left corner of your primary display to trigger PyAutoGUI's hardware failsafe.
   - **Spoken Interrupt**: Say `"stop"` during voice playback to instantly mute audio and halt command handling.
4. **Never Commit Secrets or Private Credentials**:
   - Never commit `.env`, `credentials.json`, `token*.json`, `*.pem`, or private API keys to version control.
   - Install the repository's git hooks (`powershell -ExecutionPolicy Bypass -File scripts/install_hooks.ps1`) to enable pre-commit and pre-push secret scanning.
   - **Never bypass the scanner using `git commit --no-verify` or `git push --no-verify`**.

---

## Built-In Security Guardrails

JARVIS implements defense-in-depth security layers enforced in Python code:

- **Administrative Shell Gating**: JARVIS detects and unconditionally blocks automated clicks, typing, or key combinations targeting administrative tools and command interpreters (`cmd.exe`, `powershell.exe`, `wt.exe`, `regedit.exe`, `taskmgr.exe`, and installers).
- **Protected File System Paths**: File system tools and keyboard simulation strictly refuse any operation targeting sensitive files (`.env`, `credentials.json`, `*.key`, `*.pem`, browser profiles, or audit logs).
- **Financial & Identity Domain Blocklist**: High-risk domains (banking portals, PayPal, Stripe, password managers, identity providers) require typed confirmation to open and are strictly blocked from automated clicking or typing.
- **Action Risk Classification**:
  - **LOW Risk**: Read-only queries, diagnostic tools, and ordinary clicks on pre-approved apps/domains execute without repetitive confirmations.
  - **MEDIUM Risk**: Text entry into input fields prompts for confirmation (`Proceed? (y/yes) [unclear answer = no]: `).
  - **HIGH Risk**: Actions matching destructive keywords (`delete`, `buy`, `pay`, `order`, `transfer`, `download`, `install`, `shutdown`) require explicit keyboard-typed confirmation and **cannot be approved by voice**.

---

## Reporting a Vulnerability

If you discover a security vulnerability, credential exposure, or safety loophole in JARVIS, please report it responsibly:

- **Preferred Method**: Use **GitHub Private Vulnerability Reporting** by navigating to the repository's **Security** tab, selecting **Advisories**, and clicking **Report a vulnerability**.
- **Alternative**: If private vulnerability reporting is unavailable, reach out directly to the repository maintainer through private communication.
- **Do NOT**: Please do **not** open public GitHub issues, discussions, or pull requests for undisclosed security vulnerabilities.

### What to Include in a Report
- A description of the vulnerability and its potential impact.
- Clear reproduction steps or a minimal proof-of-concept.
- Suggestions or mitigation ideas (if available).

We will review and acknowledge vulnerability reports promptly and work with you to release a coordinated security fix.
