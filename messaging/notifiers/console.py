"""
Console notifier - the default, and the one used in tests and demos.

Prints the message in a box and records it in memory so tests can assert on
what would have been sent without touching a network.
"""

from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass
from typing import List

from messaging.models import mask_phone, phone_hash
from messaging.notifiers.base import HealthResult, Notifier, SendResult
from messaging.textio import ensure_utf8_output, supports_unicode

logger = logging.getLogger(__name__)


def _display_width(text: str) -> int:
    """
    Visual width of a string in terminal cells.

    Devanagari and Gurmukhi combining marks occupy no cell of their own, so
    len() overstates the width and the box borders come out ragged.
    """
    return sum(0 if unicodedata.combining(ch) else 1 for ch in text)


def _wrap(text: str, width: int) -> List[str]:
    lines, current = [], ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if _display_width(candidate) <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


@dataclass
class SentRecord:
    """A message the console notifier was asked to send."""

    to_phone: str
    text: str


class ConsoleNotifier(Notifier):
    """Prints messages to stdout instead of delivering them."""

    name = "console"

    def __init__(self, width: int = 72, quiet: bool = False):
        self.width = width
        self.quiet = quiet
        self.sent: List[SentRecord] = []

    def send(self, to_phone: str, text: str) -> SendResult:
        self.sent.append(SentRecord(to_phone=to_phone, text=text))

        if not self.quiet:
            # Windows consoles default to a code page that cannot encode
            # Gurmukhi; without this the demo dies on its own output.
            ensure_utf8_output()
            fancy = supports_unicode()

            inner = self.width - 4
            tl, tr, bl, br = ("┌", "┐", "└", "┘") if fancy else ("+", "+", "+", "+")
            h, v = ("─", "│") if fancy else ("-", "|")
            ml, mr = ("├", "┤") if fancy else ("+", "+")

            top = tl + h * (self.width - 2) + tr
            bottom = bl + h * (self.width - 2) + br
            sep = ml + h * (self.width - 2) + mr

            def row(content: str) -> str:
                pad = inner - _display_width(content)
                return f"{v} " + content + " " * max(pad, 0) + f" {v}"

            print(top)
            print(row(f"WhatsApp (console)  ->  {mask_phone(to_phone)}"))
            print(sep)
            for line in _wrap(text, inner):
                print(row(line))
            print(sep)
            print(row(f"{len(text)} chars"))
            print(bottom)

        logger.info(
            "console send hash=%s chars=%d", phone_hash(to_phone), len(text)
        )
        # Sequential id so tests can assert on it deterministically.
        return SendResult.success(self.name, message_id=f"console-{len(self.sent)}")

    def healthcheck(self) -> HealthResult:
        return HealthResult(
            ok=True,
            provider=self.name,
            detail="Console notifier always available (no credentials required)",
        )

    def reset(self) -> None:
        self.sent.clear()
