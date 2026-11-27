import os
import re
import sys
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Locate base directories
BASE_DIR = Path(__file__).resolve().parent.parent
JARVIS_DIR = BASE_DIR / "jarvis"
LOGS_DIR = BASE_DIR / "logs"
ENV_PATH = BASE_DIR / ".env"
SETTINGS_PATH = JARVIS_DIR / "settings.yaml"
SETTINGS_EXAMPLE_PATH = JARVIS_DIR / "settings.example.yaml"
APPS_INDEX_PATH = BASE_DIR / "apps_index.json"
BROWSER_PROFILE_PATH = BASE_DIR / "browser_profile"

# Ensure essential runtime directories exist
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# Load environment variables from .env
load_dotenv(dotenv_path=ENV_PATH)


class SecretFilter(logging.Filter):
    """Logging filter that scrubs sensitive keys and tokens from log output."""
    def __init__(self, secrets: Optional[List[str]] = None):
        super().__init__()
        self.secrets = [s for s in (secrets or []) if s and len(s) > 5]

    def _scrub_text(self, text: str) -> str:
        if not isinstance(text, str):
            return text
        for secret in self.secrets:
            if secret in text:
                text = text.replace(secret, "[REDACTED]")
        # Generic API key pattern scrubbing (Google, Groq, OpenAI, Bearer tokens)
        text = re.sub(r'gsk_[0-9A-Za-z_-]{20,}', '[REDACTED_GROQ_KEY]', text)
        text = re.sub(r'sk-[0-9A-Za-z_-]{20,}', '[REDACTED_OPENAI_KEY]', text)
        text = re.sub(r'AIza[0-9A-Za-z-_]{30,45}', '[REDACTED_API_KEY]', text)
        text = re.sub(r'Bearer\s+[A-Za-z0-9._\-]{20,}', 'Bearer [REDACTED_TOKEN]', text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._scrub_text(str(record.msg))
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._scrub_text(v) if isinstance(v, str) else v for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(self._scrub_text(v) if isinstance(v, str) else v for v in record.args)
        return True


class ConsoleOutputFilter(logging.Filter):
    """
    Controls console logging output:
    - Allows ERROR and CRITICAL logs to reach the console (e.g. 'all providers failed').
    - Allows fallback/provider-switch log lines ONLY if config.llm.notify_on_fallback is True.
    - Suppresses all standard INFO and WARNING logs from the console.
    """
    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.ERROR:
            return True

        msg_str = str(record.msg).lower()
        is_fallback = getattr(record, "is_fallback", False) or ", using " in msg_str or "quota hit" in msg_str
        if is_fallback and config.llm.notify_on_fallback:
            return True

        return False


class AssistantSettings(BaseModel):
    name: str = "JARVIS"
    persona: str = "butler"
    max_spoken_sentences: int = 3
    address_user_as: str = "sir"


class VoiceSettings(BaseModel):
    mic_enabled: bool = True
    hotkey: str = "F8"
    quit_hotkey: str = "ctrl+alt+q"
    input_device: Optional[Any] = None
    output_device: Optional[Any] = None
    stt_model: str = "small"
    stt_device: str = "cpu"
    stt_compute_type: str = "int8"
    tts_engine: str = "windows"
    tts_voice: Optional[str] = None
    tts_rate: int = 185
    tts_volume: float = 1.0
    confirmations_by_voice: bool = True
    allow_high_risk_voice_confirmation: bool = False
    max_utterance_seconds: float = 15.0
    silence_duration_seconds: float = 1.2
    energy_threshold: float = 0.015
    wakeword_enabled: bool = False
    wake_word: str = "hey jarvis"

    # Backward compatibility aliases
    @property
    def rate(self) -> int:
        return self.tts_rate

    @property
    def volume(self) -> float:
        return self.tts_volume


class SafetySettings(BaseModel):
    require_confirmation_for_medium: bool = True
    require_confirmation_for_high: bool = True
    allow_high_risk_voice_confirmation: bool = False
    confirm_apps: List[str] = Field(default_factory=lambda: [
        "cmd", "powershell", "pwsh", "terminal", "wt", "regedit",
        "taskschd", "gpedit", "services", "mmc"
    ])
    blocked_apps: List[str] = Field(default_factory=lambda: [
        "format", "diskpart", "unins000", "uninstall", "setup", "installer"
    ])
    log_level: str = "INFO"
    log_file: str = "logs/jarvis.log"
    audit_file: str = "logs/audit.log"
    max_log_bytes: int = 5242880
    backup_count: int = 5


class LLMSettings(BaseModel):
    notify_on_fallback: bool = False
    per_minute_max_retries: int = 2
    per_minute_backoff_seconds: float = 1.5
    default_cooldown_seconds: int = 60
    daily_quota_cooldown_seconds: int = 3600
    ollama_context_tokens: int = 8192
    ollama_keep_alive: str = "15m"


class AutomationSettings(BaseModel):
    browser_profile_dir: str = "browser_profile"
    max_actions_per_turn: int = 15
    killswitch_hotkey: str = "ctrl+alt+s"
    automation_allowed_domains: List[str] = Field(default_factory=lambda: [
        "youtube.com", "netflix.com", "google.com", "wikipedia.org",
        "github.com", "spotify.com", "amazon.com"
    ])
    automation_blocked_domains: List[str] = Field(default_factory=lambda: [
        "paypal.com", "chase.com", "bankofamerica.com", "wellsfargo.com",
        "citi.com", "capitalone.com", "stripe.com", "1password.com",
        "bitwarden.com", "lastpass.com", "dashlane.com", "login.live.com",
        "accounts.google.com"
    ])
    allowed_key_combos: List[str] = Field(default_factory=lambda: [
        "enter", "tab", "escape", "backspace", "space", "up", "down", "left", "right",
        "home", "end", "pageup", "pagedown", "ctrl+c", "ctrl+v", "ctrl+a", "ctrl+z", "ctrl+s", "ctrl+f"
    ])
    forbidden_key_combos: List[str] = Field(default_factory=lambda: [
        "win+r", "alt+f4", "ctrl+alt+del", "win+l", "ctrl+w", "ctrl+shift+w"
    ])
    high_risk_keywords: List[str] = Field(default_factory=lambda: [
        "send", "post", "buy", "pay", "subscribe", "delete", "submit",
        "download", "install", "purchase", "order", "transfer"
    ])


class OperatorSettings(BaseModel):
    enabled: bool = False
    max_steps: int = 25
    max_seconds: int = 120
    vision_provider: str = "none"
    voice_confirm_medium: bool = True
    voice_confirm_high: bool = False
    spoken_updates: str = "sparse"
    wake_word_enabled: bool = False
    browser_mode: str = "normal"  # "normal" or "jarvis_profile"
    providers: List[str] = Field(default_factory=lambda: ["groq"])
    blocked_apps: List[str] = Field(default_factory=lambda: [
        "cmd", "powershell", "pwsh", "windowsterminal", "wt",
        "taskmgr", "regedit", "format", "diskpart",
        "1password", "bitwarden", "lastpass", "dashlane", "keepass"
    ])
    blocked_domains: List[str] = Field(default_factory=lambda: [
        "paypal.com", "chase.com", "bankofamerica.com", "wellsfargo.com",
        "citi.com", "capitalone.com", "stripe.com", "1password.com",
        "bitwarden.com", "lastpass.com", "dashlane.com", "login.live.com",
        "accounts.google.com"
    ])
    approved_apps: List[str] = Field(default_factory=list)
    approved_domains: List[str] = Field(default_factory=list)


class AppConfig(BaseModel):
    # Provider selection
    llm_provider: str = Field(default_factory=lambda: os.getenv("LLM_PROVIDER", "groq").strip().lower())
    llm_fallbacks: List[str] = Field(default_factory=lambda: [
        p.strip().lower() for p in os.getenv("LLM_FALLBACKS", "ollama").split(",") if p.strip()
    ])

    # Groq credentials
    groq_api_key: str = Field(default_factory=lambda: os.getenv("GROQ_API_KEY", "").strip())
    groq_model: str = Field(default_factory=lambda: os.getenv("GROQ_MODEL", "llama-3.1-8b-instant").strip())

    # Ollama credentials
    ollama_base_url: str = Field(default_factory=lambda: os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1").strip())
    ollama_api_key: str = Field(default_factory=lambda: os.getenv("OLLAMA_API_KEY", "ollama").strip())
    ollama_model: str = Field(default_factory=lambda: os.getenv("OLLAMA_MODEL", "llama3.2:3b").strip())

    # Optional Gemini credentials
    gemini_api_key: str = Field(default_factory=lambda: os.getenv("GEMINI_API_KEY", "").strip())
    gemini_model: str = Field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip())

    # Browser and site searches
    browser: str = "default"
    site_search_templates: Dict[str, str] = Field(default_factory=dict)

    # Nested configurations
    assistant: AssistantSettings = Field(default_factory=AssistantSettings)
    apps: Dict[str, str] = Field(default_factory=dict)
    favorite_sites: Dict[str, str] = Field(default_factory=dict)
    allowed_paths: List[str] = Field(default_factory=list)
    voice: VoiceSettings = Field(default_factory=VoiceSettings)
    safety: SafetySettings = Field(default_factory=SafetySettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    automation: AutomationSettings = Field(default_factory=AutomationSettings)
    operator: OperatorSettings = Field(default_factory=OperatorSettings)
    operate_debug: bool = False
    dry_run: bool = False


def load_config() -> AppConfig:
    """Loads configuration from environment variables and settings.yaml."""
    settings_data = {}
    if SETTINGS_PATH.exists():
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            settings_data = yaml.safe_load(f) or {}
    elif SETTINGS_EXAMPLE_PATH.exists():
        print("[NOTICE] settings.yaml not found. Falling back to default settings.example.yaml.")
        with open(SETTINGS_EXAMPLE_PATH, "r", encoding="utf-8") as f:
            settings_data = yaml.safe_load(f) or {}

    provider = os.getenv("LLM_PROVIDER", "groq").strip().lower()
    fallbacks_raw = os.getenv("LLM_FALLBACKS", "ollama").strip()
    fallbacks = [p.strip().lower() for p in fallbacks_raw.split(",") if p.strip()]

    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    groq_mod = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant").strip()

    ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1").strip()
    ollama_key = os.getenv("OLLAMA_API_KEY", "ollama").strip()
    ollama_mod = os.getenv("OLLAMA_MODEL", "llama3.2:3b").strip()

    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    gemini_mod = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()

    raw_allowed = [os.path.normpath(p) for p in settings_data.get("allowed_paths", [])]
    base_dir_norm = os.path.normpath(str(BASE_DIR))
    if base_dir_norm not in raw_allowed:
        raw_allowed.insert(0, base_dir_norm)

    config = AppConfig(
        llm_provider=provider,
        llm_fallbacks=fallbacks,
        groq_api_key=groq_key,
        groq_model=groq_mod,
        ollama_base_url=ollama_url,
        ollama_api_key=ollama_key,
        ollama_model=ollama_mod,
        gemini_api_key=gemini_key,
        gemini_model=gemini_mod,
        browser=settings_data.get("browser", "default").lower(),
        site_search_templates=settings_data.get("site_search_templates", {}),
        assistant=AssistantSettings(**settings_data.get("assistant", {})),
        apps={k.lower(): v for k, v in settings_data.get("apps", {}).items()},
        favorite_sites={k.lower(): v for k, v in settings_data.get("favorite_sites", {}).items()},
        allowed_paths=raw_allowed,
        voice=VoiceSettings(**settings_data.get("voice", {})),
        safety=SafetySettings(**settings_data.get("safety", {})),
        llm=LLMSettings(**settings_data.get("llm", {})),
        automation=AutomationSettings(**settings_data.get("automation", {})),
        operator=OperatorSettings(**settings_data.get("operator", {})),
    )
    return config


# Global config instance
config = load_config()


def setup_logger(name: str = "jarvis") -> logging.Logger:
    """Configures rotating file handler and console logging with secret scrubbing."""
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, config.safety.log_level.upper(), logging.INFO))

    # Clear existing handlers to ensure clean configuration
    logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    # Scrub active keys from logs
    sensitive_keys = [
        config.groq_api_key,
        config.gemini_api_key,
        config.ollama_api_key,
    ]
    secret_filter = SecretFilter(secrets=[k for k in sensitive_keys if k and k != "ollama"])

    # Rotating file handler (receives all logs: INFO, WARNING, ERROR)
    raw_log = Path(config.safety.log_file)
    log_file_path = raw_log if raw_log.is_absolute() else (BASE_DIR / raw_log)
    log_file_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_file_path,
        maxBytes=config.safety.max_log_bytes,
        backupCount=config.safety.backup_count,
        encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(secret_filter)
    logger.addHandler(file_handler)

    # Console handler (filtered: ERRORs always; fallback WARNINGs only if notify_on_fallback is True)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(secret_filter)
    console_handler.addFilter(ConsoleOutputFilter())
    logger.addHandler(console_handler)

    return logger


logger = setup_logger()
