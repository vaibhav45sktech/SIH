"""
Alert triggering policy - decides whether a farmer should be messaged.

The policy exists to prevent alert fatigue. A farmer who receives the same
warning every week stops reading them, and then the one that matters is
ignored too. So:

    fire when the band CHANGES from the last SENT alert
    fire when the band has been CRITICAL for CRITICAL_REPEAT_DAYS since the
        last sent alert (sustained crisis deserves a reminder)
    suppress otherwise

Suppressed decisions are written to the alerts table with status='suppressed'
so there is a record of what was deliberately not sent. That record is the
point: it demonstrates the system is exercising judgement rather than
spraying messages.

This module deliberately takes no notifier and performs no I/O beyond the
store, so the policy is unit-testable on its own.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Union

from messaging.models import Band
from messaging.store import MessagingStore

logger = logging.getLogger(__name__)

# A sustained CRITICAL band earns a repeat message after this long.
CRITICAL_REPEAT_DAYS = 14


@dataclass(frozen=True)
class Decision:
    """Whether to alert, and the reason - which is logged either way."""

    should_alert: bool
    reason: str
    previous_band: Optional[str] = None
    days_since_last: Optional[float] = None

    def __bool__(self) -> bool:
        return self.should_alert


def _parse_ts(value: Union[str, datetime, None]) -> Optional[datetime]:
    """Parse a SQLite timestamp, which may arrive as str or datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        logger.warning("Could not parse timestamp %r", value)
        return None


def should_alert(
    farmer_id: int,
    new_band: Union[str, Band],
    store: Optional[MessagingStore] = None,
    now: Optional[datetime] = None,
) -> Decision:
    """
    Decide whether to send an alert to a farmer for a newly evaluated band.

    Args:
        farmer_id: the farmer under consideration
        new_band: the freshly evaluated band
        store: MessagingStore; constructed if not supplied
        now: injectable clock, so the 14-day rule is testable without waiting

    Returns:
        Decision(should_alert, reason, previous_band, days_since_last)
    """
    store = store or MessagingStore()
    now = now or datetime.now()
    band = (new_band.value if isinstance(new_band, Band) else str(new_band)).upper()

    last = store.last_sent_alert(farmer_id)

    # Never messaged before: the first evaluation is always worth sending.
    if last is None:
        return Decision(
            should_alert=True,
            reason="First alert for this farmer - no previously sent alert exists.",
        )

    previous_band = (last.band or "").upper()
    last_ts = _parse_ts(last.sent_ts) or _parse_ts(last.created_ts)
    days_since = (now - last_ts).total_seconds() / 86400.0 if last_ts else None

    # Band changed: this is new information for the farmer.
    if band != previous_band:
        return Decision(
            should_alert=True,
            reason=f"Band changed {previous_band} -> {band}.",
            previous_band=previous_band,
            days_since_last=days_since,
        )

    # Same band. Only a sustained CRITICAL earns a repeat.
    if band == Band.CRITICAL.value:
        if days_since is None:
            return Decision(
                should_alert=True,
                reason=(
                    "Band is CRITICAL and the last sent alert has no usable "
                    "timestamp - re-sending rather than risk staying silent "
                    "through a crisis."
                ),
                previous_band=previous_band,
            )
        if days_since >= CRITICAL_REPEAT_DAYS:
            return Decision(
                should_alert=True,
                reason=(
                    f"Band has remained CRITICAL for {days_since:.1f} days "
                    f"(>= {CRITICAL_REPEAT_DAYS}-day repeat interval)."
                ),
                previous_band=previous_band,
                days_since_last=days_since,
            )
        return Decision(
            should_alert=False,
            reason=(
                f"Band unchanged at CRITICAL and only {days_since:.1f} days since "
                f"the last alert (repeat interval is {CRITICAL_REPEAT_DAYS} days)."
            ),
            previous_band=previous_band,
            days_since_last=days_since,
        )

    return Decision(
        should_alert=False,
        reason=(
            f"Band unchanged at {band}"
            + (f" for {days_since:.1f} days" if days_since is not None else "")
            + " - nothing new to tell this farmer."
        ),
        previous_band=previous_band,
        days_since_last=days_since,
    )
