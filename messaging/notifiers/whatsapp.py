"""
WhatsApp delivery via Twilio.

Twilio error codes are mapped to actionable messages here rather than being
allowed to surface as stack traces. The one that matters most in a live demo
is 63016: the 24-hour WhatsApp session has lapsed and the recipient must
re-send the sandbox join phrase. That is a recipient action, not a bug, and
the operator needs to be told exactly that.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from messaging.models import phone_hash
from messaging.notifiers.base import HealthResult, Notifier, SendResult

logger = logging.getLogger(__name__)


# Transient / rate-limit conditions worth another attempt.
RETRYABLE_CODES = {
    20429,  # Too many requests
    20500,  # Internal server error
    20503,  # Service unavailable
    30001,  # Queue overflow
    30002,  # Account suspended (may be transient during billing sync)
}

# Permanent conditions. Retrying these only burns credit.
ERROR_GUIDANCE = {
    63016: (
        "The 24-hour WhatsApp session with this recipient has lapsed. Twilio "
        "cannot send a free-form message outside that window. The RECIPIENT "
        "must send the sandbox join phrase (e.g. 'join <your-sandbox-word>') "
        "to the Twilio WhatsApp number again, or you must use an approved "
        "WhatsApp template message. This is a recipient action - re-running "
        "dispatch will not fix it."
    ),
    63003: (
        "Twilio could not find this recipient on WhatsApp. Check the number is "
        "correct and is registered with WhatsApp."
    ),
    63007: (
        "The TWILIO_WHATSAPP_FROM number is not a valid WhatsApp sender. In the "
        "sandbox this must be 'whatsapp:+14155238886'; in production it must be "
        "your approved WhatsApp sender."
    ),
    63015: (
        "The recipient has not accepted the WhatsApp opt-in for this sender."
    ),
    21211: (
        "Twilio rejected the destination number as invalid. Check the stored "
        "number is a real, reachable Indian mobile."
    ),
    21608: (
        "This number is not verified on your Twilio trial account. Verify it in "
        "the Twilio console, or upgrade the account."
    ),
    20003: (
        "Twilio authentication failed. Check TWILIO_ACCOUNT_SID and "
        "TWILIO_AUTH_TOKEN."
    ),
}


class WhatsAppNotifier(Notifier):
    """Sends advisories over WhatsApp using Twilio's REST API."""

    name = "whatsapp"

    def __init__(
        self,
        account_sid: Optional[str] = None,
        auth_token: Optional[str] = None,
        from_: Optional[str] = None,
    ):
        self.account_sid = account_sid or os.getenv("TWILIO_ACCOUNT_SID")
        self.auth_token = auth_token or os.getenv("TWILIO_AUTH_TOKEN")
        self.from_ = from_ or os.getenv("TWILIO_WHATSAPP_FROM")

        missing = [
            var
            for var, val in (
                ("TWILIO_ACCOUNT_SID", self.account_sid),
                ("TWILIO_AUTH_TOKEN", self.auth_token),
                ("TWILIO_WHATSAPP_FROM", self.from_),
            )
            if not val
        ]
        if missing:
            raise RuntimeError(
                f"WhatsApp notifier requires {', '.join(missing)}. Set them in "
                f".env, or leave NOTIFIER unset to use the console notifier."
            )

        try:
            from twilio.rest import Client
        except ImportError as exc:
            raise RuntimeError(
                "The 'twilio' package is required for NOTIFIER=whatsapp. "
                "Install it with: pip install twilio"
            ) from exc

        self._client = Client(self.account_sid, self.auth_token)

    @staticmethod
    def _to_whatsapp(phone: str) -> str:
        return phone if phone.startswith("whatsapp:") else f"whatsapp:{phone}"

    def _from_whatsapp(self) -> str:
        return self._to_whatsapp(self.from_)

    def send(self, to_phone: str, text: str) -> SendResult:
        try:
            message = self._client.messages.create(
                from_=self._from_whatsapp(),
                to=self._to_whatsapp(to_phone),
                body=text,
            )
        except Exception as exc:  # noqa: BLE001 - classified below
            return self._classify(exc, to_phone)

        logger.info(
            "whatsapp send hash=%s sid=%s status=%s",
            phone_hash(to_phone),
            message.sid,
            getattr(message, "status", "?"),
        )
        return SendResult.success(self.name, message_id=message.sid)

    def _classify(self, exc: Exception, to_phone: str) -> SendResult:
        """Turn a Twilio exception into a SendResult with a usable message."""
        code = getattr(exc, "code", None)
        base = getattr(exc, "msg", None) or str(exc)

        guidance = ERROR_GUIDANCE.get(code)
        retryable = code in RETRYABLE_CODES

        if guidance:
            error = f"[Twilio {code}] {guidance}"
        elif code is not None:
            error = f"[Twilio {code}] {base}"
        else:
            # No code at all usually means a transport-level problem.
            error = f"[transport] {base}"
            retryable = True

        logger.warning(
            "whatsapp send failed hash=%s code=%s retryable=%s",
            phone_hash(to_phone),
            code,
            retryable,
        )
        return SendResult.failure(self.name, error=error, retryable=retryable)

    def healthcheck(self) -> HealthResult:
        """Verify the Twilio account is reachable and active."""
        try:
            account = self._client.api.accounts(self.account_sid).fetch()
        except Exception as exc:  # noqa: BLE001
            code = getattr(exc, "code", None)
            hint = ERROR_GUIDANCE.get(code, "")
            return HealthResult(
                ok=False,
                provider=self.name,
                detail=f"Could not reach Twilio: {exc}. {hint}".strip(),
            )

        status = getattr(account, "status", "unknown")
        if status != "active":
            return HealthResult(
                ok=False,
                provider=self.name,
                detail=f"Twilio account status is '{status}', expected 'active'.",
            )
        return HealthResult(
            ok=True,
            provider=self.name,
            detail=f"Twilio account active; sender {self.from_}",
        )
