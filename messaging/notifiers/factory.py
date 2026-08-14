"""
Notifier selection.

DEFAULTS TO CONSOLE, ALWAYS. Reaching a live provider requires deliberately
setting NOTIFIER=whatsapp. Nothing about a missing or malformed environment
may cause a real message to be sent to a real farmer - the failure mode of a
wrong default here is messaging strangers, so the default is inert.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from messaging.notifiers.base import Notifier
from messaging.notifiers.console import ConsoleNotifier

logger = logging.getLogger(__name__)

DEFAULT_NOTIFIER = "console"
VALID_NOTIFIERS = ("console", "whatsapp", "telegram")


def get_notifier(name: Optional[str] = None) -> Notifier:
    """
    Build a notifier from the NOTIFIER environment variable.

    Args:
        name: overrides NOTIFIER when given (used by the CLI's --notifier).

    Unknown values fall back to console with a warning rather than raising,
    so a typo in NOTIFIER cannot halt a demo - and cannot silently escalate
    to a live channel either.
    """
    # Import here so .env is loaded by the entrypoint before we read os.environ.
    selected = (name or os.getenv("NOTIFIER") or DEFAULT_NOTIFIER).strip().lower()

    if selected not in VALID_NOTIFIERS:
        logger.warning(
            "Unknown NOTIFIER=%r; falling back to console. Valid: %s",
            selected,
            ", ".join(VALID_NOTIFIERS),
        )
        return ConsoleNotifier()

    if selected == "console":
        return ConsoleNotifier()

    if selected == "whatsapp":
        from messaging.notifiers.whatsapp import WhatsAppNotifier

        logger.warning("NOTIFIER=whatsapp - messages will be sent to REAL phone numbers")
        return WhatsAppNotifier()

    if selected == "telegram":
        from messaging.notifiers.telegram import TelegramNotifier

        logger.warning("NOTIFIER=telegram - messages will be sent to REAL chats")
        return TelegramNotifier()

    # Unreachable, but keeps the contract explicit.
    return ConsoleNotifier()
