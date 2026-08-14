"""
Notifier interface.

Every provider returns a SendResult rather than raising, so dispatch can make
one decision - retry or do not retry - without knowing anything about the
provider's exception hierarchy.

The `retryable` flag is the important field. Retrying a permanently bad
number burns provider credit and never succeeds, so providers must classify
their own errors rather than leaving dispatch to guess.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SendResult:
    """Outcome of a single send attempt."""

    ok: bool
    provider: str
    message_id: Optional[str] = None
    error: Optional[str] = None
    retryable: bool = False

    @classmethod
    def success(cls, provider: str, message_id: Optional[str] = None) -> "SendResult":
        return cls(ok=True, provider=provider, message_id=message_id)

    @classmethod
    def failure(
        cls, provider: str, error: str, retryable: bool = False
    ) -> "SendResult":
        return cls(ok=False, provider=provider, error=error, retryable=retryable)


@dataclass(frozen=True)
class HealthResult:
    """Outcome of a provider healthcheck."""

    ok: bool
    provider: str
    detail: str


class Notifier(ABC):
    """Abstract delivery channel."""

    name: str = "base"

    @abstractmethod
    def send(self, to_phone: str, text: str) -> SendResult:
        """
        Deliver `text` to `to_phone` (E.164, e.g. +919876543210).

        Must not raise for delivery failures - return SendResult.failure()
        with `retryable` set appropriately.
        """

    def healthcheck(self) -> HealthResult:
        """Verify the channel is usable. Overridden by real providers."""
        return HealthResult(ok=True, provider=self.name, detail="No healthcheck implemented")
