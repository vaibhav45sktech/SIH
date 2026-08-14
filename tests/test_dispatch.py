"""
Dispatch end to end against ConsoleNotifier, plus the retry policy and the
notifier factory's safe default.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from messaging.dispatch import MAX_ATTEMPTS, _send_with_retries, dispatch
from messaging.models import AlertStatus, Evaluation, Language
from messaging.notifiers.base import Notifier, SendResult
from messaging.notifiers.console import ConsoleNotifier
from messaging.notifiers.factory import get_notifier
from tests.conftest import STATION_NEAR

NOW = datetime(2026, 6, 1, 12, 0, 0)

VARS = {"village": "Longowal", "district": "Sangrur", "decline_m_per_year": "0.58"}


def evaluation(band="CRITICAL", reason="DEFAULT", rate=0.58):
    return Evaluation(
        station_id=STATION_NEAR[0],
        band=band,
        reason_code=reason,
        metrics={"decline_m_per_year": rate},
    )


class FlakyNotifier(Notifier):
    """Fails `fail_times` times with a retryable error, then succeeds."""

    name = "flaky"

    def __init__(self, fail_times: int, retryable: bool = True):
        self.fail_times = fail_times
        self.retryable = retryable
        self.calls = 0

    def send(self, to_phone, text):
        self.calls += 1
        if self.calls <= self.fail_times:
            return SendResult.failure(self.name, "temporary", retryable=self.retryable)
        return SendResult.success(self.name, message_id=f"ok-{self.calls}")


class TestDispatchEndToEnd:
    def test_sends_to_registered_farmer(self, store, notifier, registered_farmer):
        report = dispatch(evaluation(), store=store, notifier=notifier, now=NOW)
        assert len(report.outcomes) == 1
        assert report.sent == 1
        assert len(notifier.sent) == 1

    def test_message_goes_to_the_canonical_number(self, store, notifier, registered_farmer):
        dispatch(evaluation(), store=store, notifier=notifier, now=NOW)
        assert notifier.sent[0].to_phone == "+919876543210"

    def test_message_is_in_the_farmers_language(self, store, notifier, registered_farmer):
        # registered_farmer is Punjabi.
        dispatch(evaluation(), store=store, notifier=notifier, now=NOW)
        text = notifier.sent[0].text
        assert any("਀" <= ch <= "੿" for ch in text), "expected Gurmukhi"

    def test_variables_interpolated_into_sent_text(self, store, notifier, registered_farmer):
        dispatch(evaluation(rate=0.58), store=store, notifier=notifier, now=NOW)
        text = notifier.sent[0].text
        assert "Longowal" in text
        assert "Sangrur" in text
        assert "0.58" in text
        assert "{" not in text

    def test_negative_rate_presented_as_positive_magnitude(self, store, notifier, registered_farmer):
        # Template supplies direction in words; a minus sign would double-negate.
        dispatch(evaluation(rate=-0.58), store=store, notifier=notifier, now=NOW)
        assert "0.58" in notifier.sent[0].text
        assert "-0.58" not in notifier.sent[0].text

    def test_alert_row_written_as_sent(self, store, notifier, registered_farmer):
        dispatch(evaluation(), store=store, notifier=notifier, now=NOW)
        alerts = store.alerts_for_farmer(registered_farmer.id)
        assert len(alerts) == 1
        assert alerts[0].status is AlertStatus.SENT
        assert alerts[0].provider == "console"
        assert alerts[0].provider_message_id
        assert alerts[0].sent_ts is not None
        assert alerts[0].attempts == 1

    def test_second_identical_dispatch_is_suppressed(self, store, notifier, registered_farmer):
        dispatch(evaluation(), store=store, notifier=notifier, now=NOW)
        report = dispatch(evaluation(), store=store, notifier=notifier, now=NOW)
        assert report.suppressed == 1
        assert report.sent == 0
        assert len(notifier.sent) == 1  # nothing new was delivered

    def test_suppressed_row_is_persisted_for_the_audit_trail(
        self, store, notifier, registered_farmer
    ):
        dispatch(evaluation(), store=store, notifier=notifier, now=NOW)
        dispatch(evaluation(), store=store, notifier=notifier, now=NOW)
        alerts = store.alerts_for_farmer(registered_farmer.id)
        statuses = [a.status for a in alerts]
        assert AlertStatus.SUPPRESSED in statuses
        suppressed = next(a for a in alerts if a.status is AlertStatus.SUPPRESSED)
        assert suppressed.error  # carries the suppression reason

    def test_band_change_sends_again(self, store, notifier, registered_farmer):
        dispatch(evaluation(band="WATCH"), store=store, notifier=notifier, now=NOW)
        report = dispatch(
            evaluation(band="CRITICAL"), store=store, notifier=notifier,
            now=NOW + timedelta(days=1),
        )
        assert report.sent == 1
        assert len(notifier.sent) == 2

    def test_no_farmers_yields_empty_report(self, store, notifier):
        report = dispatch(evaluation(), store=store, notifier=notifier, now=NOW)
        assert report.outcomes == []
        assert notifier.sent == []

    def test_opted_out_farmer_not_messaged(self, store, notifier, registered_farmer):
        store.opt_out(registered_farmer.id)
        report = dispatch(evaluation(), store=store, notifier=notifier, now=NOW)
        assert report.outcomes == []
        assert notifier.sent == []

    def test_multiple_farmers_each_in_own_language(self, store, notifier, registered_farmer):
        store.register_farmer(
            name="Hindi Farmer", phone="9876500011", village="Dhuri",
            district="Sangrur", language="hi",
            latitude=STATION_NEAR[1] + 0.01, longitude=STATION_NEAR[2] + 0.01,
            consent=True,
        )
        store.register_farmer(
            name="English Farmer", phone="9876500012", village="Bhawanigarh",
            district="Sangrur", language="en",
            latitude=STATION_NEAR[1] + 0.01, longitude=STATION_NEAR[2] + 0.01,
            consent=True,
        )
        report = dispatch(evaluation(), store=store, notifier=notifier, now=NOW)
        assert report.sent == 3
        langs = {o.language for o in report.outcomes}
        assert langs == {"pa", "hi", "en"}
        assert len({r.text for r in notifier.sent}) == 3


class TestDryRun:
    def test_dry_run_does_not_send(self, store, notifier, registered_farmer):
        report = dispatch(evaluation(), dry_run=True, store=store, notifier=notifier, now=NOW)
        assert report.would_send == 1
        assert notifier.sent == []

    def test_dry_run_writes_nothing_to_the_database(self, store, notifier, registered_farmer):
        dispatch(evaluation(), dry_run=True, store=store, notifier=notifier, now=NOW)
        assert store.alerts_for_farmer(registered_farmer.id) == []

    def test_dry_run_still_composes_the_real_message(self, store, notifier, registered_farmer):
        report = dispatch(evaluation(), dry_run=True, store=store, notifier=notifier, now=NOW)
        outcome = report.outcomes[0]
        assert outcome.message_text
        assert "{" not in outcome.message_text
        assert outcome.template_key == "CRITICAL__DEFAULT"

    def test_dry_run_is_repeatable_because_it_leaves_no_history(
        self, store, notifier, registered_farmer
    ):
        first = dispatch(evaluation(), dry_run=True, store=store, notifier=notifier, now=NOW)
        second = dispatch(evaluation(), dry_run=True, store=store, notifier=notifier, now=NOW)
        assert first.would_send == second.would_send == 1


class TestRetryPolicy:
    def test_retryable_failure_is_retried_until_success(self):
        notifier = FlakyNotifier(fail_times=2, retryable=True)
        result, attempts = _send_with_retries(
            notifier, "+919876543210", "text", sleep=lambda s: None
        )
        assert result.ok is True
        assert attempts == 3

    def test_gives_up_after_max_attempts(self):
        notifier = FlakyNotifier(fail_times=99, retryable=True)
        result, attempts = _send_with_retries(
            notifier, "+919876543210", "text", sleep=lambda s: None
        )
        assert result.ok is False
        assert attempts == MAX_ATTEMPTS

    def test_non_retryable_failure_is_not_retried(self):
        """A bad number must be attempted exactly once - retrying burns credit."""
        notifier = FlakyNotifier(fail_times=99, retryable=False)
        result, attempts = _send_with_retries(
            notifier, "+919876543210", "text", sleep=lambda s: None
        )
        assert result.ok is False
        assert attempts == 1
        assert notifier.calls == 1

    def test_success_first_time_makes_one_attempt(self):
        notifier = FlakyNotifier(fail_times=0)
        _, attempts = _send_with_retries(
            notifier, "+919876543210", "t", sleep=lambda s: None
        )
        assert attempts == 1

    def test_backoff_is_exponential(self):
        delays = []
        notifier = FlakyNotifier(fail_times=99, retryable=True)
        _send_with_retries(notifier, "+919876543210", "t", sleep=delays.append)
        assert delays == [2.0, 4.0]  # no sleep after the final attempt

    def test_failed_send_recorded_as_failed(self, store, registered_farmer):
        notifier = FlakyNotifier(fail_times=99, retryable=False)
        report = dispatch(
            evaluation(), store=store, notifier=notifier, now=NOW, sleep=lambda s: None
        )
        assert report.failed == 1
        alert = store.alerts_for_farmer(registered_farmer.id)[0]
        assert alert.status is AlertStatus.FAILED
        assert alert.error
        assert alert.attempts == 1


class TestNotifierFactory:
    def test_defaults_to_console_when_unset(self, monkeypatch):
        monkeypatch.delenv("NOTIFIER", raising=False)
        assert get_notifier().name == "console"

    def test_console_when_explicitly_console(self, monkeypatch):
        monkeypatch.setenv("NOTIFIER", "console")
        assert get_notifier().name == "console"

    def test_unknown_value_falls_back_to_console_not_a_live_provider(self, monkeypatch):
        monkeypatch.setenv("NOTIFIER", "wthasapp")  # typo
        assert get_notifier().name == "console"

    def test_empty_value_falls_back_to_console(self, monkeypatch):
        monkeypatch.setenv("NOTIFIER", "")
        assert get_notifier().name == "console"

    def test_explicit_argument_overrides_env(self, monkeypatch):
        monkeypatch.setenv("NOTIFIER", "console")
        assert get_notifier("console").name == "console"

    def test_whatsapp_without_credentials_raises_rather_than_silently_degrading(
        self, monkeypatch
    ):
        for var in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WHATSAPP_FROM"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("NOTIFIER", "whatsapp")
        with pytest.raises(RuntimeError, match="TWILIO_"):
            get_notifier()


class TestConsoleNotifier:
    def test_records_sends(self):
        n = ConsoleNotifier(quiet=True)
        n.send("+919876543210", "hello")
        assert len(n.sent) == 1
        assert n.sent[0].text == "hello"

    def test_reset_clears(self):
        n = ConsoleNotifier(quiet=True)
        n.send("+919876543210", "hello")
        n.reset()
        assert n.sent == []

    def test_healthcheck_ok(self):
        assert ConsoleNotifier(quiet=True).healthcheck().ok is True

    def test_prints_a_box(self, capsys):
        ConsoleNotifier().send("+919876543210", "hello world")
        out = capsys.readouterr().out
        assert "┌" in out and "└" in out
        assert "hello world" in out

    def test_masks_the_phone_in_output(self, capsys):
        ConsoleNotifier().send("+919876543210", "hi")
        out = capsys.readouterr().out
        assert "9876543210" not in out
        assert "+9198XXXXX210" in out


class TestOutputEncoding:
    """
    Regression guard: printing Gurmukhi must not crash the CLI.

    On Windows stdout defaults to cp1252, which cannot encode Devanagari or
    Gurmukhi, and the dispatch report died on its own output. For a system
    whose whole purpose is non-Latin delivery this is a real defect.
    """

    def test_ensure_utf8_output_is_safe_to_call(self):
        from messaging.textio import ensure_utf8_output

        ensure_utf8_output()
        ensure_utf8_output()  # idempotent

    def test_supports_unicode_returns_bool(self):
        from messaging.textio import supports_unicode

        assert isinstance(supports_unicode(), bool)

    def test_console_notifier_prints_gurmukhi_without_raising(self, capsys):
        from messaging.composer import compose

        text = compose("CRITICAL", "DEFAULT", "pa", VARS).text
        ConsoleNotifier().send("+919876543210", text)
        assert capsys.readouterr().out

    def test_console_notifier_prints_devanagari_without_raising(self, capsys):
        from messaging.composer import compose

        text = compose("CRITICAL", "DEFAULT", "hi", VARS).text
        ConsoleNotifier().send("+919876543210", text)
        assert capsys.readouterr().out

    def test_dispatch_report_renders_all_languages(self, store, notifier, registered_farmer, capsys):
        from messaging.dispatch import _print_report

        store.register_farmer(
            name="Hindi", phone="9876500021", village="Dhuri", district="Sangrur",
            language="hi", latitude=STATION_NEAR[1] + 0.01,
            longitude=STATION_NEAR[2] + 0.01, consent=True,
        )
        report = dispatch(evaluation(), dry_run=True, store=store, notifier=notifier, now=NOW)
        _print_report(report)  # must not raise
        assert capsys.readouterr().out
