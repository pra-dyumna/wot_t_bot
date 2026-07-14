"""
Configuration — mirrors wat-a-bot's env-based setup.

WhatsApp session folders follow LocalAuth-style naming:
  config/wa-auth/session-<WA_PHONE>
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]


def _load_env() -> None:
    load_dotenv(ROOT / ".env")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _digits_only(value: str) -> str:
    return re.sub(r"\D", "", value or "")


class Config:
    def __init__(self) -> None:
        _load_env()

    @property
    def root(self) -> Path:
        return ROOT

    def get_wa_phone(self) -> str | None:
        """
        Account phone from .env only (never hardcoded).
        Used as session folder label — same role as wat-a-bot WWEBJS_CLIENT_ID.
        Does NOT tell WhatsApp which number to login as; QR scan does that.
        """
        raw = (os.getenv("WA_PHONE") or "").strip()
        digits = _digits_only(raw)
        if not digits:
            return None
        # If user pasted local 10-digit Indian mobile, prefix country code
        if len(digits) == 10 and digits.startswith(("6", "7", "8", "9")):
            return f"91{digits}"
        return digits

    def get_wa_client_id(self) -> str:
        """
        Session folder id — same as wat-a-bot LocalAuth clientId.
        Priority: WA_CLIENT_ID → WA_PHONE → "default"
        Folder: config/wa-auth/session-<id>/  (or session/ when default)
        """
        explicit = (os.getenv("WA_CLIENT_ID") or "").strip()
        if explicit:
            return explicit
        phone = self.get_wa_phone()
        if phone:
            return phone
        return "default"

    def get_wa_auth_base(self) -> Path:
        custom = (os.getenv("WA_AUTH_PATH") or "").strip()
        if custom:
            return Path(custom)
        return ROOT / "config" / "wa-auth"

    def get_wa_session_dir(self) -> Path:
        """Persistent Chromium profile (QR / LocalAuth-style, like wat-a-bot)."""
        client_id = self.get_wa_client_id()
        # Match wat-a-bot: default → "session", named → "session-<id>"
        folder = "session" if client_id == "default" else f"session-{client_id}"
        return self.get_wa_auth_base() / folder

    def get_gpt_auth_dir(self) -> Path:
        custom = (os.getenv("GPT_AUTH_PATH") or "").strip()
        if custom:
            return Path(custom)
        return ROOT / "config" / "gpt-auth"

    def get_data_dir(self) -> Path:
        return ROOT / "data"

    def get_qa_results_path(self) -> Path:
        return self.get_data_dir() / "qa_results.json"

    def get_wa_headless(self) -> bool:
        return _env_bool("WA_HEADLESS", False)

    def get_gpt_headless(self) -> bool:
        return _env_bool("GPT_HEADLESS", False)

    def get_auto_reply(self) -> bool:
        return _env_bool("AUTO_REPLY", True)

    def get_allow_group_messages(self) -> bool:
        """Allow reading and replying to group chats when true (env: ALLOW_GROUP_MESSAGES)."""
        return _env_bool("ALLOW_GROUP_MESSAGES", False)

    def get_poll_interval_sec(self) -> float:
        try:
            return float(os.getenv("POLL_INTERVAL_SEC", "2.5"))
        except ValueError:
            return 2.5

    def log_startup(self) -> None:
        env_path = ROOT / ".env"
        print("[config] --- startup ---")
        print(f"[config] envFile={'found' if env_path.exists() else '(missing)'}")
        print(f"[config] root={ROOT}")
        phone = self.get_wa_phone()
        print(f"[config] wa.phone={phone or '(not set in .env)'}")
        print(f"[config] wa.clientId={self.get_wa_client_id()}")
        print(f"[config] wa.sessionDir={self.get_wa_session_dir()}")
        print(f"[config] wa.headless={self.get_wa_headless()}")
        print(f"[config] gpt.authDir={self.get_gpt_auth_dir()}")
        print(f"[config] gpt.headless={self.get_gpt_headless()}")
        print(f"[config] autoReply={self.get_auto_reply()}")
        print(f"[config] pollIntervalSec={self.get_poll_interval_sec()}")
        print("[config] connect=QR scan (same idea as wat-a-bot LocalAuth)")
        print("[config] ----------------\n")


config = Config()
