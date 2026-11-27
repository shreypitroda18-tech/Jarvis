"""Power management tools (shutdown, restart, sleep) with mandatory HIGH-risk confirmation gate."""

import subprocess
import sys
from jarvis.config import config, logger
from jarvis.safety import RiskLevel, confirm_action, log_audit_entry


def system_power(action: str) -> str:
    """Manages system power states: shutdown, restart, or sleep. HIGH RISK - Requires mandatory user confirmation.

    Args:
        action: The power command to execute: 'shutdown', 'restart', or 'sleep'.

    Returns:
        Status message confirming the power state change or reporting cancellation.
    """
    act = action.strip().lower()
    valid_actions = {"shutdown", "restart", "sleep"}

    if act not in valid_actions:
        msg = f"Invalid power action '{action}'. Valid options are: {', '.join(sorted(valid_actions))}."
        log_audit_entry("system_power", RiskLevel.HIGH, {"action": action}, "BLOCKED", msg)
        return msg

    if getattr(config, "dry_run", False):
        msg = f"[DRY-RUN] Would execute system {act}."
        log_audit_entry("system_power", RiskLevel.HIGH, {"action": act, "dry_run": True}, "SUCCESS", msg)
        return msg

    # Confirmation Gate (HIGH Risk)
    prompt = f"CRITICAL: Are you absolutely certain you wish to {act.upper()} the computer now, sir?"
    confirmed = confirm_action(RiskLevel.HIGH, prompt)

    if not confirmed:
        msg = f"Action aborted: System {act} was cancelled because confirmation was denied."
        log_audit_entry("system_power", RiskLevel.HIGH, {"action": act}, "BLOCKED", msg)
        return msg

    try:
        if act == "shutdown":
            # Schedule shutdown with a 60-second grace period so it can be aborted if needed
            subprocess.run(["shutdown.exe", "/s", "/t", "60", "/c", "JARVIS scheduled shutdown (run 'shutdown /a' to abort)"], check=True)
            msg = "System shutdown initiated. Power off scheduled in 60 seconds (run 'shutdown /a' in command prompt to cancel)."
        elif act == "restart":
            subprocess.run(["shutdown.exe", "/r", "/t", "60", "/c", "JARVIS scheduled restart (run 'shutdown /a' to abort)"], check=True)
            msg = "System restart initiated. Reboot scheduled in 60 seconds (run 'shutdown /a' to cancel)."
        elif act == "sleep":
            subprocess.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], check=True)
            msg = "System entering sleep mode now."
        else:
            msg = f"Unhandled action {act}"

        log_audit_entry("system_power", RiskLevel.HIGH, {"action": act}, "SUCCESS", msg)
        return msg
    except Exception as e:
        msg = f"Failed to execute system {act}: {str(e)}"
        log_audit_entry("system_power", RiskLevel.HIGH, {"action": act}, "ERROR", msg)
        return msg


def cancel_shutdown() -> str:
    """Aborts any scheduled system shutdown or restart operation.

    Returns:
        Confirmation message that scheduled shutdown/restart was cancelled.
    """
    if getattr(config, "dry_run", False):
        msg = "[DRY-RUN] Would cancel scheduled shutdown."
        log_audit_entry("cancel_shutdown", RiskLevel.LOW, {"dry_run": True}, "SUCCESS", msg)
        return msg

    try:
        subprocess.run(["shutdown.exe", "/a"], check=True)
        msg = "Scheduled shutdown or restart has been successfully cancelled."
        log_audit_entry("cancel_shutdown", RiskLevel.LOW, {}, "SUCCESS", msg)
        return msg
    except subprocess.CalledProcessError:
        msg = "No scheduled shutdown or restart was in progress to cancel."
        log_audit_entry("cancel_shutdown", RiskLevel.LOW, {}, "INFO", msg)
        return msg
    except Exception as e:
        msg = f"Failed to cancel shutdown: {str(e)}"
        log_audit_entry("cancel_shutdown", RiskLevel.LOW, {}, "ERROR", msg)
        return msg
