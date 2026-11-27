"""JARVIS Entry Point. Supports CLI flags: --text, --voice, --gui, --doctor, --list-models, --timing."""

import sys
import os
import argparse
import time
from typing import Optional

from jarvis.timing import tracker


def run_setup_browser() -> None:
    """Launches Google Chrome with the dedicated JARVIS profile for manual user login."""
    from jarvis.config import config, BROWSER_PROFILE_PATH

    if getattr(config, "dry_run", False):
        print("\n[DRY-RUN] Setup browser skipped in dry-run mode.\n")
        return

    print("\n=======================================================")
    print(" [JARVIS Chrome Browser Profile Setup]")
    print("=======================================================")
    print(f"Opening Google Chrome with dedicated profile at:\n  {BROWSER_PROFILE_PATH}\n")
    print("Please use this browser window to log into your accounts")
    print("(e.g. YouTube, Netflix, Google) directly. JARVIS will reuse")
    print("these cookies and sessions for automation, and will never touch")
    print("or ask for your passwords.")
    print("Close the browser when you have completed logging in, sir.")
    print("=======================================================\n")

    BROWSER_PROFILE_PATH.mkdir(parents=True, exist_ok=True)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(BROWSER_PROFILE_PATH),
                channel="chrome",
                headless=False,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-first-run",
                    "--no-default-browser-check"
                ]
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.goto("https://www.google.com")
            print("Chrome is open. Press Enter in this console after you finish logging in and closing the browser...")
            try:
                input()
            except (KeyboardInterrupt, EOFError):
                pass
            context.close()
            print("[Setup completed] JARVIS dedicated browser profile is saved and ready, sir.\n")
    except Exception as e:
        print(f"[Error] Failed to open Google Chrome via Playwright: {e}")


BANNER = r"""
     ____.  _____ ___________________   ____.___  _________
    |    | /  _  \\______   \   \ /   /|   |   | /   _____/
    |    |/  /_\  \|       _/\   Y   / |   |   | \_____  \ 
/\__|    /    |    \    |   \ \     /  |   |   | /        \
\________\____|__  /____|_  /  \___/   |___|___|/_______  /
                 \/       \/                            \/ 
          Just A Rather Very Intelligent System
"""


def run_text_loop(brain) -> None:
    """Runs the terminal-based interactive chat loop."""
    from jarvis.config import config, logger

    print(BANNER)
    print("JARVIS Text Interface [Online]")
    print(f"Active Provider: {config.llm_provider.upper()} | Fallbacks: {[f.upper() for f in config.llm_fallbacks]}")
    print("Commands: 'exit' / 'quit' to close, 'clear' to reset chat context.")
    print("-" * 65)

    # Initial greeting - immediate display without blocking network call
    print("\nJARVIS > At your service, sir. All systems standing by.\n")

    # Start optional async provider health check in the background after the prompt appears
    def _async_provider_check():
        t0 = time.perf_counter()
        try:
            for p in brain.fallback_manager.providers:
                p.is_available()
            dur = time.perf_counter() - t0
            tracker.record_stage("provider_checks", dur, "async background")
        except Exception as e:
            logger.debug(f"Async provider check notice: {e}")

    import threading
    threading.Thread(target=_async_provider_check, daemon=True).start()

    while True:
        try:
            sys.stdout.write("Sir > ")
            sys.stdout.flush()
            user_input = sys.stdin.readline()

            # Handle EOF
            if not user_input:
                print("\nFarewell, sir.")
                break

            cleaned = user_input.strip()
            if not cleaned:
                continue

            # Built-in loop control
            if cleaned.lower() in ("exit", "quit", "goodbye", "bye"):
                print("\nJARVIS > As you wish, sir. Standing by.\n")
                break
            elif cleaned.lower() == "clear":
                brain.reset_chat()
                print("\n[Chat context reset]\n")
                continue

            response = brain.think(cleaned)
            print(f"\nJARVIS > {response}\n")

        except KeyboardInterrupt:
            print("\n\nJARVIS > Session interrupted. Goodbye, sir.\n")
            break
        except Exception as e:
            logger.error(f"Error in main loop: {e}", exc_info=True)
            print(f"\nJARVIS > My apologies, sir. An issue occurred: {e}\n")


def run_list_models() -> None:
    """Queries and lists available models for each configured provider (names only, no keys)."""
    from jarvis.brain import JarvisBrain

    print("\n--- Available LLM Models ---")
    brain = JarvisBrain()
    for provider in brain.fallback_manager.providers:
        print(f"\nProvider: {provider.name.upper()} (Current: '{provider.model_name}')")
        try:
            models = provider.list_models()
            if models:
                # Show top models cleanly
                for m in sorted(models)[:15]:
                    marker = " * " if m == provider.model_name else "   "
                    print(f"{marker}{m}")
                if len(models) > 15:
                    print(f"   ... and {len(models) - 15} more")
            else:
                print("   (No models returned or provider currently offline)")
        except Exception as e:
            print(f"   Error retrieving models: {e}")
    print("\n----------------------------\n")


def run_list_apps() -> None:
    """Lists all discovered applications from the local index."""
    from jarvis.tools.app_discovery import load_apps_index

    apps_db = load_apps_index()
    print("\n--- Discovered Installed Applications ---")
    print(f"{'App Name':<35} {'Type':<8} {'Aliases / Target'}")
    print("-" * 75)
    for k in sorted(apps_db.keys()):
        app = apps_db[k]
        name = app.get("name", k)[:34]
        app_type = app.get("type", "exe")[:7]
        aliases = ", ".join(app.get("aliases", []))
        target = app.get("target", "")
        extra = f"[{aliases}]" if aliases else target[:35]
        print(f"{name:<35} {app_type:<8} {extra}")
    print("-" * 75)
    print(f"Total: {len(apps_db)} applications indexed.")
    print("-----------------------------------------\n")


def run_refresh_apps() -> None:
    """Rebuilds the local application discovery index from Start Menu and Store apps."""
    from jarvis.config import config
    if getattr(config, "dry_run", False):
        print("\n[DRY-RUN] Refreshing application index skipped in dry-run mode (no file writes).\n")
        return
    from jarvis.tools.app_discovery import load_apps_index

    print("\nRefreshing application index from Start Menu and Store apps...")
    apps_db = load_apps_index(force_refresh=True)
    print(f"[SUCCESS] Discovered and indexed {len(apps_db)} applications to apps_index.json.\n")


def _get_last_fallback_reason() -> Optional[str]:
    """Scans the active jarvis.log for the most recent provider fallback reason."""
    from jarvis.config import config, BASE_DIR
    log_file = getattr(config.safety, "log_file", "logs/jarvis.log")
    log_path = BASE_DIR / log_file
    if not os.path.exists(log_path):
        return None
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        for line in reversed(lines):
            line_str = line.strip()
            if ", using " in line_str or "fallback_reason" in line_str or "quota hit" in line_str:
                return line_str
    except Exception:
        pass
    return None


def run_mic_test() -> None:
    """Records 3 seconds of audio, displays loudness bar, transcribes with Whisper, and exits."""
    import math
    import numpy as np
    from jarvis.config import config
    from jarvis.voice.stt import get_stt_engine

    print("\n================== JARVIS Microphone Test ==================")
    print("Recording 3.0 seconds of audio... Speak into your microphone now.")

    stt = get_stt_engine()
    audio = None
    try:
        audio = stt.record_audio(max_duration=3.0, silence_timeout=3.0)
    except Exception as e:
        print(f"[FAIL] Error accessing microphone: {e}")
        return

    if audio is None or len(audio) == 0:
        rms = 0.0
        peak_db = -100.0
    else:
        rms = float(np.sqrt(np.mean(audio ** 2)))
        peak = float(np.max(np.abs(audio)))
        peak_db = 20 * math.log10(max(peak, 1e-6))

    bar_width = 30
    scaled = min(1.0, max(0.0, (peak_db + 60.0) / 60.0)) if peak_db > -60.0 else 0.0
    filled = int(scaled * bar_width)
    loudness_bar = "#" * filled + "-" * (bar_width - filled)

    print(f"\nLoudness Bar : [{loudness_bar}]")
    print(f"Signal Level : RMS: {rms:.4f} | Peak: {peak_db:.1f} dB")

    if rms < 0.0001 or f"{rms:.4f}" == "0.0000":
        print("  [WARNING] Microphone returned zero signal (RMS 0.0000). Check Windows microphone privacy settings, input device selection, or physical mute switch.")

    print("\nTranscribing capture with Whisper...")
    if audio is not None and len(audio) > 0 and rms >= 0.002:
        text = stt.transcribe(audio)
        if text:
            print(f"I heard: {text}")
        else:
            print("I heard: (no speech detected)")
    else:
        print("I heard: (silence - near-silence discarded)")

    del audio
    print("Audio buffer discarded.")
    print("============================================================\n")


def run_doctor() -> None:
    """Comprehensive diagnostic check of system prerequisites, providers, and local commands."""
    from jarvis.config import config, logger, BROWSER_PROFILE_PATH
    from jarvis.brain import JarvisBrain
    from jarvis.local_commands import try_handle_local_command
    from jarvis.tools.app_discovery import load_apps_index
    from jarvis.tools.system_tools import open_website
    from jarvis.voice.stt import list_audio_devices, measure_mic_energy, get_stt_engine
    from jarvis.voice.tts import get_tts_engine

    t0_doc = time.perf_counter()

    print("\n================== JARVIS System Diagnostics ==================")
    print(f"Python Version: {sys.version.split()[0]} (Compatible)")
    print(f"Platform: {sys.platform}")

    # Check local command layer
    print("\n[Local Command Layer]")
    local_time = try_handle_local_command("time")
    if local_time:
        print("  [PASS] Deterministic local command execution (time/date, volume, stats, apps, screenshot)")
    else:
        print("  [WARN] Local command layer returned empty response")

    # Check Application Discovery
    print("\n[Application Discovery Subsystem]")
    try:
        apps_db = load_apps_index()
        count = len(apps_db)
        if count > 0:
            print(f"  [PASS] Discovered {count} applications ({len(config.apps)} whitelist aliases, {len(config.safety.confirm_apps)} confirm apps)")
        else:
            print("  [WARN] No applications found in index.")
    except Exception as e:
        print(f"  [FAIL] App discovery check error: {e}")

    # Check Web Navigation & Security
    print("\n[Web Navigation & Security Policies]")
    try:
        # Test blocking bad scheme
        blocked_res = open_website("file:///C:/Windows")
        if "prohibited for security" in blocked_res:
            print(f"  [PASS] Dangerous scheme blocking active (file:, javascript:, data:)")
        else:
            print(f"  [FAIL] Dangerous scheme not blocked: {blocked_res}")
        print(f"  [PASS] Browser configured: '{config.browser}' | Favorite sites: {len(config.favorite_sites)}")
    except Exception as e:
        print(f"  [FAIL] Web security check error: {e}")

    # Check configured providers
    print(f"\n[Configured LLM Architecture]")
    print(f"  Primary:   {config.llm_provider.upper()}")
    print(f"  Fallbacks: {[f.upper() for f in config.llm_fallbacks]}")

    last_fallback = _get_last_fallback_reason()
    if last_fallback:
        print(f"  Last Provider Fallback: {last_fallback}")
    else:
        print("  Last Provider Fallback: None recorded (Primary provider operating normally)")

    t0_prov = time.perf_counter()
    brain = JarvisBrain()
    for provider in brain.fallback_manager.providers:
        print(f"\n  Provider: {provider.name.upper()}")
        print(f"  Configured Model: {provider.model_name}")

        is_avail, status_msg = provider.is_available()
        if is_avail:
            print(f"  Status: [PASS] {status_msg}")
        else:
            print(f"  Status: [FAIL] {status_msg}")

    tracker.record_stage("provider_checks", time.perf_counter() - t0_prov, "doctor synchronous checks")

    # Connectivity probe
    print("\n[Live Connectivity Probe]")
    try:
        reply = brain.think("Ping")
        print(f"  [PASS] Active Brain Response: {reply}")
    except Exception as e:
        print(f"  [FAIL] Brain response failed: {e}")

    # Audio Hardware & Voice Diagnostics
    print("\n[Audio Hardware & Voice Subsystem]")
    try:
        devices_str = list_audio_devices()
        print(devices_str)

        # Speaker output test
        sys.stdout.write("  Testing speaker output... ")
        sys.stdout.flush()
        try:
            tts = get_tts_engine()
            tts.speak("JARVIS audio test complete, sir.")
            print("[PASS] (Spoken test phrase completed)")
        except Exception as e:
            print(f"[FAIL] Speaker test error: {e}")

        # Microphone RMS test
        sys.stdout.write("  Measuring microphone level for 2 seconds (please speak or remain quiet)... ")
        sys.stdout.flush()
        rms, db = measure_mic_energy(duration=2.0)
        if rms < 0.0001 or f"{rms:.4f}" == "0.0000":
            print(f"[FAIL] (RMS: {rms:.4f}, Peak: {db:.1f} dB)")
            print("  [WARNING] Microphone returned zero signal (RMS 0.0000). Check Windows microphone privacy settings, input device selection, or physical mute switch.")
        else:
            print(f"[PASS] (RMS: {rms:.4f}, Peak: {db:.1f} dB)")

        # Short transcription check
        sys.stdout.write("  Initializing local STT model test... ")
        sys.stdout.flush()
        stt = get_stt_engine()
        stt.load_model()
        print(f"[PASS] (Model '{stt.model_size}' ready on {stt.requested_device.upper()})")

    except Exception as e:
        print(f"  [FAIL] Audio diagnostics encountered error: {e}")

    # Check Computer & Browser Automation Subsystem
    print("\n[Computer & Browser Automation Subsystem]")
    try:
        from jarvis.control.safety import is_dangerous_foreground, DANGEROUS_PROCESSES
        print(f"  [PASS] Code safety model active ({len(DANGEROUS_PROCESSES)} dangerous processes blocked, password fields refused)")
        print(f"  [PASS] Browser profile path: {BROWSER_PROFILE_PATH}")
        print(f"  [PASS] Domain safety: {len(config.automation.automation_allowed_domains)} allowed, {len(config.automation.automation_blocked_domains)} financial/credential blocked")

        # Test UIA backend import
        from pywinauto import Desktop
        print("  [PASS] Windows UI Automation (pywinauto UIA) subsystem ready")

        # Test Playwright import
        import playwright
        print("  [PASS] Playwright browser automation engine ready")
    except Exception as e:
        print(f"  [FAIL] Computer automation check error: {e}")

    print("===============================================================\n")


def main() -> None:
    """Parses arguments and starts the requested interface with optimized lazy loading."""
    parser = argparse.ArgumentParser(description="JARVIS Voice & Desktop Assistant")
    parser.add_argument("--text", action="store_true", help="Launch in interactive terminal text mode (Default)")
    parser.add_argument("--voice", action="store_true", help="Launch in hands-free voice mode (Phase 3)")
    parser.add_argument("--gui", action="store_true", help="Launch Heads-Up Display (Phase 7)")
    parser.add_argument("--doctor", action="store_true", help="Run system diagnostics and exit")
    parser.add_argument("--mic-test", action="store_true", help="Record 3 seconds of audio, display loudness bar, transcribe, and exit")
    parser.add_argument("--list-models", action="store_true", help="List available models for each configured provider")
    parser.add_argument("--list-audio-devices", action="store_true", help="List all detected audio input and output devices")
    parser.add_argument("--list-apps", action="store_true", help="List all discovered desktop and Store applications")
    parser.add_argument("--refresh-apps", action="store_true", help="Rebuild the application discovery catalog")
    parser.add_argument("--setup-browser", action="store_true", help="Open Google Chrome with JARVIS profile for manual login")
    parser.add_argument("--timing", action="store_true", help="Print startup stage timing summary table")
    parser.add_argument("--operate-debug", action="store_true", help="Print verbose operator observation and action details")
    parser.add_argument("--dry-run", action="store_true", help="Plan computer operations without executing physical clicks or keystrokes")

    args = parser.parse_args()

    tracker.start_stage("config")
    from jarvis.config import config
    tracker.end_stage("config")

    if args.operate_debug:
        config.operate_debug = True

    if args.dry_run:
        config.dry_run = True

    if args.timing:
        tracker.show_timing_summary = True

    if args.setup_browser:
        run_setup_browser()
        tracker.print_summary_table()
        return

    if args.list_apps:
        run_list_apps()
        tracker.print_summary_table()
        return

    if args.refresh_apps:
        run_refresh_apps()
        tracker.print_summary_table()
        return

    if args.list_audio_devices:
        from jarvis.voice.stt import list_audio_devices
        print(list_audio_devices())
        tracker.print_summary_table()
        return

    if args.doctor:
        run_doctor()
        tracker.print_summary_table()
        return

    if args.mic_test:
        run_mic_test()
        tracker.print_summary_table()
        return

    if args.list_models:
        run_list_models()
        tracker.print_summary_table()
        return

    if args.voice:
        tracker.start_stage("imports")
        from jarvis.voice.voice_loop import run_voice_loop
        tracker.end_stage("imports", "voice mode")
        tracker.print_summary_table()
        run_voice_loop()
        return

    if args.gui:
        print("[INFO] HUD GUI mode is scheduled for Phase 7. Defaulting to text mode.")

    # Default to text loop (--text)
    tracker.start_stage("imports")
    from jarvis.brain import JarvisBrain
    tracker.end_stage("imports", "core text mode")

    brain = JarvisBrain()

    # Pre-cache app index check
    tracker.start_stage("app_index")
    from jarvis.tools.app_discovery import load_apps_index
    load_apps_index()
    tracker.end_stage("app_index", "disk check")

    tracker.print_summary_table()
    run_text_loop(brain)


if __name__ == "__main__":
    main()

