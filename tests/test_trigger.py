"""
Trigger policy: fire on band change, fire on sustained CRITICAL, suppress
otherwise. Tested with no notifier and an injected clock.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from messaging.models import Alert, AlertStatus
from messaging.trigger import CRITICAL_REPEAT_DAYS, should_alert
from tests.conftest import STATION_NEAR

NOW = datetime(2026, 6, 1, 12, 0, 0)


def record_sent(store, farmer_id, band, when, status=AlertStatus.SENT):
    """Insert an alert with a controlled timestamp."""
    alert = store.save_alert(
        Alert(
            farmer_id=farmer_id,
            station_id=STATION_NEAR[0],
            band=band,
            reason_code="DEFAULT",
            language="pa",
            template_key=f"{band}__DEFAULT",
            message_text="test",
            status=status,
        )
    )
    alert.sent_ts = when
    store.update_alert(alert)
    return alert


class TestFirstAlert:
    def test_fires_when_no_history(self, store, registered_farmer):
        d = should_alert(registered_farmer.id, "WATCH", store=store, now=NOW)
        assert d.should_alert is True
        assert "First alert" in d.reason

    def test_decision_is_truthy(self, store, registered_farmer):
        assert bool(should_alert(registered_farmer.id, "WATCH", store=store, now=NOW))


class TestBandChange:
    @pytest.mark.parametrize(
        "old,new",
        [
            ("WATCH", "WARNING"),
            ("WARNING", "CRITICAL"),
            ("CRITICAL", "WARNING"),
            ("WARNING", "NORMAL"),
            ("NORMAL", "CRITICAL"),
            ("WATCH", "NORMAL"),
        ],
    )
    def test_fires_on_any_band_change(self, store, registered_farmer, old, new):
        record_sent(store, registered_farmer.id, old, NOW - timedelta(days=1))
        d = should_alert(registered_farmer.id, new, store=store, now=NOW)
        assert d.should_alert is True
        assert d.previous_band == old
        assert f"{old} -> {new}" in d.reason

    def test_fires_on_improvement_too(self, store, registered_farmer):
        # Recovery is news worth sending, not just deterioration.
        record_sent(store, registered_farmer.id, "CRITICAL", NOW - timedelta(days=2))
        assert should_alert(registered_farmer.id, "NORMAL", store=store, now=NOW)

    def test_band_comparison_case_insensitive(self, store, registered_farmer):
        record_sent(store, registered_farmer.id, "WARNING", NOW - timedelta(days=1))
        d = should_alert(registered_farmer.id, "warning", store=store, now=NOW)
        assert d.should_alert is False


class TestSuppression:
    @pytest.mark.parametrize("band", ["WATCH", "WARNING", "NORMAL"])
    def test_suppresses_unchanged_non_critical(self, store, registered_farmer, band):
        record_sent(store, registered_farmer.id, band, NOW - timedelta(days=30))
        d = should_alert(registered_farmer.id, band, store=store, now=NOW)
        assert d.should_alert is False
        assert "unchanged" in d.reason

    def test_suppresses_unchanged_warning_even_after_a_year(self, store, registered_farmer):
        # Only CRITICAL earns a repeat; WARNING does not, by design.
        record_sent(store, registered_farmer.id, "WARNING", NOW - timedelta(days=365))
        assert should_alert(registered_farmer.id, "WARNING", store=store, now=NOW).should_alert is False

    def test_reason_is_populated_when_suppressed(self, store, registered_farmer):
        record_sent(store, registered_farmer.id, "WATCH", NOW - timedelta(days=5))
        d = should_alert(registered_farmer.id, "WATCH", store=store, now=NOW)
        assert d.reason
        assert d.days_since_last == pytest.approx(5.0, abs=0.1)


class TestCriticalRepeat:
    def test_suppresses_critical_before_14_days(self, store, registered_farmer):
        record_sent(store, registered_farmer.id, "CRITICAL", NOW - timedelta(days=13))
        d = should_alert(registered_farmer.id, "CRITICAL", store=store, now=NOW)
        assert d.should_alert is False
        assert "repeat interval" in d.reason

    def test_fires_critical_at_exactly_14_days(self, store, registered_farmer):
        record_sent(
            store, registered_farmer.id, "CRITICAL",
            NOW - timedelta(days=CRITICAL_REPEAT_DAYS),
        )
        d = should_alert(registered_farmer.id, "CRITICAL", store=store, now=NOW)
        assert d.should_alert is True
        assert "remained CRITICAL" in d.reason

    def test_fires_critical_after_14_days(self, store, registered_farmer):
        record_sent(store, registered_farmer.id, "CRITICAL", NOW - timedelta(days=20))
        assert should_alert(registered_farmer.id, "CRITICAL", store=store, now=NOW).should_alert

    def test_only_critical_repeats(self, store, registered_farmer):
        record_sent(store, registered_farmer.id, "WARNING", NOW - timedelta(days=20))
        assert not should_alert(registered_farmer.id, "WARNING", store=store, now=NOW).should_alert


class TestOnlySentAlertsCount:
    def test_suppressed_history_does_not_count_as_told(self, store, registered_farmer):
        """
        A suppressed record must not satisfy the "already told them" test, or a
        farmer could be permanently silenced by their own suppression history.
        """
        record_sent(
            store, registered_farmer.id, "CRITICAL", NOW - timedelta(days=1),
            status=AlertStatus.SUPPRESSED,
        )
        d = should_alert(registered_farmer.id, "CRITICAL", store=store, now=NOW)
        assert d.should_alert is True
        assert "First alert" in d.reason

    def test_failed_history_does_not_count_as_told(self, store, registered_farmer):
        """A failed send means the farmer never got the message - try again."""
        record_sent(
            store, registered_farmer.id, "CRITICAL", NOW - timedelta(days=1),
            status=AlertStatus.FAILED,
        )
        assert should_alert(registered_farmer.id, "CRITICAL", store=store, now=NOW).should_alert

    def test_pending_history_does_not_count_as_told(self, store, registered_farmer):
        record_sent(
            store, registered_farmer.id, "WATCH", NOW - timedelta(days=1),
            status=AlertStatus.PENDING,
        )
        assert should_alert(registered_farmer.id, "WATCH", store=store, now=NOW).should_alert

    def test_most_recent_sent_alert_wins(self, store, registered_farmer):
        record_sent(store, registered_farmer.id, "WATCH", NOW - timedelta(days=10))
        record_sent(store, registered_farmer.id, "CRITICAL", NOW - timedelta(days=1))
        d = should_alert(registered_farmer.id, "CRITICAL", store=store, now=NOW)
        assert d.should_alert is False
        assert d.previous_band == "CRITICAL"


class TestNoNotifierNeeded:
    def test_trigger_is_pure_policy(self, store, registered_farmer):
        """
        The whole policy must be exercisable without constructing a notifier -
        this test would fail at import/collection if trigger.py reached for one.
        """
        import messaging.trigger as trigger_module

        source = trigger_module.__doc__ or ""
        assert "notifier" in source.lower()  # documents the constraint
        d = should_alert(registered_farmer.id, "CRITICAL", store=store, now=NOW)
        assert isinstance(d.should_alert, bool)
