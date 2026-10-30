"""Configuration and persistent state management for Adlos MCP Host Server."""

import os
import re
import json
import socket
import hashlib
import logging
from pathlib import Path
from dotenv import load_dotenv

_LOGGER = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"
STATE_DIR = BASE_DIR / "state"
PAIRED_USERS_FILE = STATE_DIR / "paired_users.json"
RUNTIME_CONFIG_FILE = STATE_DIR / "runtime_config.json"
SCREENSHOTS_DIR = BASE_DIR / "screenshots"

# Ensure directories exist
STATE_DIR.mkdir(parents=True, exist_ok=True)
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

if ENV_FILE.exists():
    load_dotenv(ENV_FILE, override=True)


def derive_host_bot_id(hostname: str) -> str:
    """Derives a deterministic 15-character alphanumeric Adlos Contact ID from a hostname."""
    clean_host = hostname.strip().lower() or "ubuntu"
    digest = hashlib.sha256(f"adlosmcp_host_v1_{clean_host}".encode("utf-8")).hexdigest()
    return re.sub(r"[^a-z0-9]", "", digest).lower()[:15]


class AppConfig:
    """Manages configuration from .env and runtime_config.json."""

    def __init__(self) -> None:
        self._runtime: dict = {}
        self._load_runtime()

    def _load_runtime(self) -> None:
        if RUNTIME_CONFIG_FILE.exists():
            try:
                self._runtime = json.loads(RUNTIME_CONFIG_FILE.read_text(encoding="utf-8"))
            except Exception as err:
                _LOGGER.warning("Could not read runtime_config.json: %s", err)
                self._runtime = {}

    def _save_runtime(self) -> None:
        try:
            RUNTIME_CONFIG_FILE.write_text(
                json.dumps(self._runtime, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            os.chmod(RUNTIME_CONFIG_FILE, 0o600)
        except Exception as err:
            _LOGGER.error("Failed to save runtime_config.json: %s", err)

    def get(self, key: str, default: str = "") -> str:
        if key in self._runtime and self._runtime[key] is not None and str(self._runtime[key]).strip() != "":
            return str(self._runtime[key]).strip()
        val = os.getenv(key, default)
        return str(val).strip() if val is not None else default

    def set_runtime(self, key: str, value: str) -> None:
        self._runtime[key] = value.strip()
        self._save_runtime()
        os.environ[key] = value.strip()

    @property
    def hostname(self) -> str:
        return socket.gethostname()

    @property
    def server_url(self) -> str:
        url = self.get("ADLOS_SERVER_URL", "https://pb.adlos.de")
        if not url.startswith("http://") and not url.startswith("https://"):
            url = f"https://{url}"
        return url.rstrip("/")

    @property
    def bot_id(self) -> str:
        raw = self.get("ADLOS_BOT_ID", "auto")
        if not raw or raw.lower() in ("auto", "ubuntu_host_mcp"):
            return derive_host_bot_id(self.hostname)
        if len(raw) == 15 and raw.isalnum():
            return raw.lower()
        return derive_host_bot_id(raw)

    @property
    def bot_name(self) -> str:
        raw = self.get("ADLOS_BOT_NAME", "auto")
        if not raw or raw.lower() in ("auto", "ubuntu server"):
            return f"Ubuntu ({self.hostname})"
        return raw

    @property
    def web_port(self) -> int:
        try:
            return int(self.get("ADLOS_WEB_PORT", "8787"))
        except ValueError:
            return 8787

    @property
    def adlos_token(self) -> str | None:
        t = self.get("ADLOS_TOKEN", "")
        return t if t else None

    @property
    def notify_on_boot(self) -> bool:
        return self.get("ADLOS_NOTIFY_ON_BOOT", "true").lower() in ("true", "1", "yes", "ja")

    @property
    def ai_provider(self) -> str:
        return self.get("AI_PROVIDER", "gemini").lower()

    @property
    def gemini_api_key(self) -> str:
        return self.get("GEMINI_API_KEY", "") or self.get("GOOGLE_API_KEY", "")

    @property
    def gemini_model(self) -> str:
        return self.get("GEMINI_MODEL", "gemini-3.5-flash-lite")

    @property
    def openai_api_key(self) -> str:
        return self.get("OPENAI_API_KEY", "")

    @property
    def openai_base_url(self) -> str:
        return self.get("OPENAI_BASE_URL", "https://api.openai.com/v1")

    @property
    def openai_model(self) -> str:
        return self.get("OPENAI_MODEL", "gpt-4o-mini")

    @property
    def ollama_base_url(self) -> str:
        return self.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")

    @property
    def ollama_model(self) -> str:
        return self.get("OLLAMA_MODEL", "llama3.1")

    @property
    def host_user_password(self) -> str:
        return self.get("HOST_USER_PASSWORD", "")


config = AppConfig()


def load_paired_users() -> dict[str, dict]:
    """Loads paired Adlos users from state/paired_users.json."""
    if not PAIRED_USERS_FILE.exists():
        return {}
    try:
        data = json.loads(PAIRED_USERS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as err:
        _LOGGER.warning("Error loading paired_users.json: %s", err)
        return {}


def save_paired_user(user_id: str, user_name: str, room_key: str, room_id: str) -> None:
    """Saves or updates a paired Adlos user."""
    users = load_paired_users()
    users[user_id] = {
        "contact_id": user_id,
        "name": user_name,
        "room_key": room_key,
        "room_id": room_id,
    }
    try:
        PAIRED_USERS_FILE.write_text(
            json.dumps(users, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.chmod(PAIRED_USERS_FILE, 0o600)
    except Exception as err:
        _LOGGER.error("Failed to save paired_users.json: %s", err)
