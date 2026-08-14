"""Delivery channels. get_notifier() defaults to console, never a live provider."""

from messaging.notifiers.base import HealthResult, Notifier, SendResult
from messaging.notifiers.console import ConsoleNotifier
from messaging.notifiers.factory import get_notifier

__all__ = [
    "HealthResult",
    "Notifier",
    "SendResult",
    "ConsoleNotifier",
    "get_notifier",
]
