"""
Telegram notifier - fallback channel.

Useful when WhatsApp's 24-hour session window makes a live demo fragile:
Telegram has no equivalent restriction. Telegram addresses chats by chat_id,
not phone number, so a phone -> chat_id map must be supplied.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Dict, Optional

import requests

from messaging.models import normalise_phone, phone_hash
from messaging.notifiers.base import HealthResult, Notifier, SendResult

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"

# Telegram uses HTTP status codes; 429 and 5xx are worth retrying.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class TelegramNotifier(Notifier):
    """Sends advisories via the Telegram Bot API."""

    name = "telegram"

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_map: Optional[Dict[str, str]] = None,
        timeout: int = 15,
    ):
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
        if not self.bot_token:
            raise RuntimeError(
                "Telegram notifier requires TELEGRAM_BOT_TOKEN. Set it in .env, "
                "or leave NOTIFIER unset to use the console notifier."
            )
        self.timeout = timeout
        self.chat_map = chat_map if chat_map is not None else self._load_chat_map()

    @staticmethod
    def _load_chat_map() -> Dict[str, str]:
        """
        Load the phone -> chat_id map.

        From TELEGRAM_CHAT_MAP as inline JSON, or TELEGRAM_CHAT_MAP_FILE as a
        path to a JSON file. Keys are normalised so lookup is format-agnostic.
        """
        raw = os.getenv("TELEGRAM_CHAT_MAP")
        if not raw:
            path = os.getenv("TELEGRAM_CHAT_MAP_FILE")
            if path and Path(path).exists():
                raw = Path(path).read_text(encoding="utf-8")
        if not raw:
            return {}

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"TELEGRAM_CHAT_MAP is not valid JSON: {exc}. Expected "
                f'{{"+919876543210": "123456789"}}'
            ) from exc

        normalised = {}
        for phone, chat_id in parsed.items():
            try:
                normalised[normalise_phone(phone)] = str(chat_id)
            except Exception:
                normalised[str(phone)] = str(chat_id)
        return normalised

    def _url(self, method: str) -> str:
        return f"{API_BASE}/bot{self.bot_token}/{method}"

    def send(self, to_phone: str, text: str) -> SendResult:
        try:
            canonical = normalise_phone(to_phone)
        except Exception:
            canonical = to_phone

        chat_id = self.chat_map.get(canonical)
        if not chat_id:
            return SendResult.failure(
                self.name,
                error=(
                    f"No Telegram chat_id mapped for phone hash "
                    f"{phone_hash(canonical)}. Telegram addresses chats by "
                    f"chat_id, not phone number - add an entry to "
                    f"TELEGRAM_CHAT_MAP. The farmer must message the bot once "
                    f"before a chat_id exists."
                ),
                retryable=False,
            )

        try:
            response = requests.post(
                self._url("sendMessage"),
                json={"chat_id": chat_id, "text": text},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            return SendResult.failure(
                self.name, error=f"[transport] {exc}", retryable=True
            )

        if response.status_code == 200:
            try:
                message_id = str(response.json()["result"]["message_id"])
            except (ValueError, KeyError):
                message_id = None
            logger.info(
                "telegram send hash=%s msg=%s", phone_hash(canonical), message_id
            )
            return SendResult.success(self.name, message_id=message_id)

        detail = response.text[:300]
        return SendResult.failure(
            self.name,
            error=f"[telegram {response.status_code}] {detail}",
            retryable=response.status_code in RETRYABLE_STATUS,
        )

    def healthcheck(self) -> HealthResult:
        try:
            response = requests.get(self._url("getMe"), timeout=self.timeout)
        except requests.RequestException as exc:
            return HealthResult(
                ok=False, provider=self.name, detail=f"Could not reach Telegram: {exc}"
            )

        if response.status_code != 200:
            return HealthResult(
                ok=False,
                provider=self.name,
                detail=f"getMe returned {response.status_code}: {response.text[:200]}",
            )

        try:
            username = response.json()["result"].get("username", "?")
        except (ValueError, KeyError):
            username = "?"
        return HealthResult(
            ok=True,
            provider=self.name,
            detail=f"Bot @{username} reachable; {len(self.chat_map)} chat_id(s) mapped",
        )
