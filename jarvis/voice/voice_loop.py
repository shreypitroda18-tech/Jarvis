"""Interactive voice loop orchestrating STT, TTS, state transitions, hotkeys, and safety gates."""

import sys
import time
import re
import queue
import threading
from typing import Optional

from jarvis.config import config, logger
from jarvis.brain import JarvisBrain
from jarvis.local_commands import try_handle_local_command
from jarvis.safety import RiskLevel, set_confirmation_handler, log_audit_entry
from jarvis.voice.stt import get_stt_engine, STTEngine
from jarvis.voice.tts import get_tts_engine, BaseTTSEngine, clean_speech_text
from jarvis.voice.state import get_state_manager, VoiceState
from jarvis.voice.hotkey import HotkeyListener

BANNER = r"""
     ____.  _____ ___________________   ____.___  _________
    |    | /  _  \\______   \   \ /   /|   |   | /   _____/
    |    |/  /_\  \|       _/\   Y   / |   |   | \_____  \ 
/\__|    /    |    \    |   \ \     /  |   |   | /        \
\________\____|__  /____|_  /  \___/   |___|___|/_______  /
                 \/       \/                            \/ 
          Just A Rather Very Intelligent System [VOICE]
"""


def voice_confirm_handler(risk_level: RiskLevel, prompt: str) -> bool:
    """
    Voice-specific confirmation handler registered with jarvis.safety.
    - MEDIUM risk: Prompts by voice and on screen, requiring exact phrase 'yes, confirm'.
    - HIGH risk: Prompts by voice, but requires typed keyboard confirmation by default.
    """
    tts = get_tts_engine()
    stt = get_stt_engine()
    state = get_state_manager()

    sys.stdout.write(f"\n=======================================================\n")
    sys.stdout.write(f" [VOICE SAFETY GATE - {risk_level.value} RISK ACTION]\n")
    sys.stdout.write(f" {prompt}\n")
    sys.stdout.write(f"=======================================================\n")
    sys.stdout.flush()

    # HIGH risk actions require typed confirmation by default
    allow_high_voice = getattr(config.operator, "voice_confirm_high", False) or config.safety.allow_high_risk_voice_confirmation
    if risk_level == RiskLevel.HIGH and not allow_high_voice:
        msg = f"Sir, this is a high risk operation: {prompt}. Please confirm on the keyboard."
        print(f"JARVIS > {msg}")
        tts.speak(msg)
        sys.stdout.write("High risk requires keyboard confirmation. Type 'yes, confirm' or 'yes' to proceed: ")
        sys.stdout.flush()
        try:
            typed = sys.stdin.readline().strip().lower()
        except Exception:
            typed = "no"

        confirmed = typed in ("yes, confirm", "yes", "y", "confirm")
        if confirmed:
            tts.speak("Confirmed, sir.")
            return True
        else:
            tts.speak("Action cancelled, sir.")
            return False

    # MEDIUM risk (or HIGH if explicitly allowed by configuration)
    spoken_prompt = f"Sir, do you want to proceed with: {prompt}? Please say: yes, confirm."
    print(f"JARVIS > {spoken_prompt}")
    tts.speak(spoken_prompt)

    sys.stdout.write("Listening for confirmation (say 'yes, confirm')... [or type yes/no]: ")
    sys.stdout.flush()

    state.set_state(VoiceState.LISTENING)
    state.play_activation_beep()

    spoken_reply = stt.record_and_transcribe(max_duration=6.0)
    state.set_state(VoiceState.IDLE)

    norm = re.sub(r"[^\w\s]", "", spoken_reply.lower()).strip()
    logger.info(f"Voice confirmation response heard: '{spoken_reply}' (normalized: '{norm}')")

    # Strict check: MUST match 'yes confirm'
    if norm == "yes confirm":
        print(f"\nConfirmed: '{spoken_reply}'")
        tts.speak("Confirmed, sir.")
        return True
    else:
        print(f"\nDenied or ambiguous: '{spoken_reply}' (Action aborted)")
        tts.speak("Action cancelled, sir.")
        return False


def run_voice_loop() -> None:
    """Runs the primary hands-free, push-to-talk voice assistant loop."""
    print(BANNER)
    print("JARVIS Voice Interface [Online]")
    print(f"Push-to-Talk Hotkey : [{config.voice.hotkey.upper()}] (Press to activate)")
    print(f"Quit Hotkey         : [{config.voice.quit_hotkey.upper()}] (Press to quit)")
    print(f"Speech Engine       : {config.voice.tts_engine.upper()} (rate: {config.voice.tts_rate})")
    print(f"STT Model           : {config.voice.stt_model.upper()} ({config.voice.stt_device.upper()} {config.voice.stt_compute_type})")
    print("Voice Commands      : Say 'stop' or press hotkey to interrupt speech.")
    print("                      Say 'exit jarvis' or type 'exit' to quit.")
    print("-" * 65)

    # Initialize components
    stt = get_stt_engine()
    tts = get_tts_engine()
    state = get_state_manager()
    brain = JarvisBrain()

    # Load speech model in a background thread so the interface is immediately available
    print("[LOADING speech model in background... You may type commands in the console meanwhile.]\n")
    stt.start_background_load()

    # Hook voice confirmation handler
    set_confirmation_handler(voice_confirm_handler)
    try:
        from jarvis.voice.voice_operator import set_voice_mode_active
        set_voice_mode_active(True)
    except Exception:
        pass

    # Threading events
    activation_event = threading.Event()
    quit_event = threading.Event()
    interrupt_event = threading.Event()
    input_queue: queue.Queue[str] = queue.Queue()

    def on_hotkey_trigger():
        if state.current_state == VoiceState.SPEAKING:
            # Cut off speech immediately
            interrupt_event.set()
            tts.stop()
        elif state.current_state == VoiceState.IDLE:
            activation_event.set()

    def on_hotkey_quit():
        quit_event.set()

    # Start hotkey listener
    hotkey_listener = HotkeyListener(
        on_trigger=on_hotkey_trigger,
        on_quit=on_hotkey_quit,
        talk_hotkey=config.voice.hotkey,
        quit_hotkey=config.voice.quit_hotkey
    )
    hotkey_listener.start()

    # Background thread for typed input (so user can type while in voice mode)
    def console_reader():
        while not quit_event.is_set():
            try:
                line = sys.stdin.readline()
                if not line:
                    break
                cleaned = line.strip()
                if cleaned:
                    input_queue.put(cleaned)
            except Exception:
                break

    input_thread = threading.Thread(target=console_reader, daemon=True)
    input_thread.start()

    # Initial greeting
    state.set_state(VoiceState.IDLE)
    initial_greeting = "Voice interface ready, sir. Press your hotkey to speak, or type commands here."
    print(f"\nJARVIS > {initial_greeting}\n")

    try:
        while not quit_event.is_set():
            # Check for queued typed commands
            typed_cmd = None
            try:
                typed_cmd = input_queue.get_nowait()
            except queue.Empty:
                typed_cmd = None

            if typed_cmd:
                if typed_cmd.lower() in ("exit", "quit", "exit jarvis"):
                    print("\nJARVIS > As you wish, sir. Goodbye.\n")
                    tts.speak("As you wish, sir. Goodbye.")
                    break

                # Process typed command
                state.set_state(VoiceState.THINKING)
                response = try_handle_local_command(typed_cmd) or brain.think(typed_cmd)
                state.set_state(VoiceState.SPEAKING)
                print(f"\nJARVIS > {response}\n")
                interrupt_event.clear()
                tts.speak(response, interrupt_event=interrupt_event)
                state.set_state(VoiceState.IDLE)
                continue

            # Check for push-to-talk activation
            if activation_event.is_set():
                activation_event.clear()
                interrupt_event.clear()

                state.set_state(VoiceState.LISTENING)
                state.play_activation_beep()

                t_record_start = time.time()
                try:
                    audio_data = stt.record_audio()
                except Exception as e:
                    state.set_state(VoiceState.IDLE)
                    print(f"\nJARVIS > Audio capture error: {e}")
                    tts.speak("Microphone error, sir.")
                    continue
                record_time_ms = int((time.time() - t_record_start) * 1000)

                # Discard near-silence audio captures
                import numpy as np
                rms = float(np.sqrt(np.mean(audio_data ** 2))) if len(audio_data) > 0 else 0.0
                threshold = config.voice.energy_threshold or 0.015
                if len(audio_data) < stt.sample_rate * 0.3 or rms < threshold * 0.3 or rms < 0.002:
                    logger.debug(f"Discarded near-silence capture (RMS: {rms:.5f})")
                    del audio_data
                    state.set_state(VoiceState.IDLE)
                    continue

                state.set_state(VoiceState.THINKING)
                t_trans_start = time.time()
                transcribed = stt.transcribe(audio_data)
                transcribe_time_ms = int((time.time() - t_trans_start) * 1000)

                # Immediately release in-memory audio array
                del audio_data

                cleaned_cmd = transcribed.strip()
                if not cleaned_cmd:
                    state.set_state(VoiceState.IDLE)
                    continue

                print(f"I heard: {cleaned_cmd}")
                print(f"\nSir (Voice) > {cleaned_cmd}")

                # Check for spoken exit or stop
                if cleaned_cmd.lower() in ("exit jarvis", "exit", "quit", "goodbye jarvis"):
                    state.set_state(VoiceState.SPEAKING)
                    tts.speak("As you wish, sir. Standing down.")
                    print("\nJARVIS > As you wish, sir. Standing down.\n")
                    break
                elif cleaned_cmd.lower() == "stop":
                    tts.stop()
                    state.set_state(VoiceState.IDLE)
                    continue

                # Echo recognized command if potentially sensitive
                cmd_lower = cleaned_cmd.lower()
                if any(w in cmd_lower for w in ("delete", "shutdown", "restart", "power", "open file", "remove", "send")):
                    echo_msg = f"I heard: {cleaned_cmd}"
                    print(f"JARVIS > {echo_msg}")
                    tts.speak(echo_msg)

                # Execute command
                t_llm_start = time.time()
                local_result = try_handle_local_command(cleaned_cmd)
                if local_result:
                    response = local_result
                else:
                    response = brain.think(cleaned_cmd)
                llm_time_ms = int((time.time() - t_llm_start) * 1000)

                state.set_state(VoiceState.SPEAKING)
                print(f"\nJARVIS > {response}\n")

                t_tts_start = time.time()
                tts.speak(response, interrupt_event=interrupt_event)
                tts_time_ms = int((time.time() - t_tts_start) * 1000)

                # Log performance timings to logs/jarvis.log only
                logger.info(
                    f"Timing (ms): record={record_time_ms}, transcribe={transcribe_time_ms}, "
                    f"llm={llm_time_ms}, tts={tts_time_ms} | cmd='{cleaned_cmd}'"
                )

                state.set_state(VoiceState.IDLE)

            # Short poll interval to prevent CPU spinning while idle
            time.sleep(0.05)

    except KeyboardInterrupt:
        print("\nJARVIS > Voice session interrupted. Standing down, sir.\n")
    finally:
        hotkey_listener.stop()
        set_confirmation_handler(None)
        state.set_state(VoiceState.IDLE)
