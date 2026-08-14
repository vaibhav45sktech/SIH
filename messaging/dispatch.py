"""
Dispatch - the send path.

    farmers_for_station -> trigger check -> compose in the farmer's language
    -> save as pending -> notifier.send() -> update to sent/failed

Retries happen ONLY when SendResult.retryable is True. A bad number is not
retryable; retrying it three times just burns provider credit and still
fails. Classification is the provider's job (see notifiers/), so this module
only has to honour the flag.

Run as a CLI:
    python -m messaging.dispatch --station AAXI067 --dry-run
    python -m messaging.dispatch --all --dry-run
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from dotenv import load_dotenv

# Load .env before anything reads os.environ (notably the notifier factory).
load_dotenv()

from messaging.composer import ComposedMessage, TemplateError, compose  # noqa: E402
from messaging.models import (  # noqa: E402
    Alert,
    AlertStatus,
    Evaluation,
    Farmer,
    phone_hash,
)
from messaging.notifiers.base import Notifier, SendResult  # noqa: E402
from messaging.notifiers.factory import get_notifier  # noqa: E402
from messaging.store import MessagingStore  # noqa: E402
from messaging.textio import ensure_utf8_output  # noqa: E402
from messaging.trigger import Decision, should_alert  # noqa: E402

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 2.0


@dataclass
class FarmerOutcome:
    """What happened for one farmer."""

    farmer_id: int
    phone_hash: str
    village: str
    language: str
    decision: Decision
    status: str
    template_key: Optional[str] = None
    message_text: Optional[str] = None
    provider: Optional[str] = None
    provider_message_id: Optional[str] = None
    error: Optional[str] = None
    attempts: int = 0
    alert_id: Optional[int] = None


@dataclass
class DispatchReport:
    """Aggregate result of a dispatch run."""

    station_id: str
    band: str
    reason_code: str
    dry_run: bool
    outcomes: List[FarmerOutcome] = field(default_factory=list)

    @property
    def sent(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "sent")

    @property
    def suppressed(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "suppressed")

    @property
    def failed(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "failed")

    @property
    def would_send(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "would_send")

    @property
    def errored(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "error")

    def summary(self) -> str:
        parts = [f"{len(self.outcomes)} farmer(s)"]
        for label, n in (
            ("sent", self.sent),
            ("would_send", self.would_send),
            ("suppressed", self.suppressed),
            ("failed", self.failed),
            ("error", self.errored),
        ):
            if n:
                parts.append(f"{label}={n}")
        return ", ".join(parts)


def _build_variables(farmer: Farmer, evaluation: Evaluation) -> dict:
    """
    Assemble template variables for one farmer.

    decline_m_per_year is formatted to two decimals and always presented as a
    positive magnitude - the template supplies the direction in words, so a
    minus sign would read as a double negative to the farmer.
    """
    metrics = evaluation.metrics or {}
    rate = metrics.get("decline_m_per_year")
    if rate is None:
        rate = metrics.get("slope_m_per_year")

    variables = {
        "village": farmer.village,
        "district": farmer.district,
    }
    if rate is not None:
        variables["decline_m_per_year"] = f"{abs(float(rate)):.2f}"
    return variables


def _send_with_retries(
    notifier: Notifier, to_phone: str, text: str, sleep=time.sleep
) -> tuple[SendResult, int]:
    """
    Send, retrying only retryable failures, with exponential backoff.

    Returns the final SendResult and the number of attempts made.
    """
    attempts = 0
    result = SendResult.failure("none", "No attempt made")

    while attempts < MAX_ATTEMPTS:
        attempts += 1
        result = notifier.send(to_phone, text)

        if result.ok:
            return result, attempts

        if not result.retryable:
            logger.info(
                "Not retrying hash=%s - provider marked error as permanent: %s",
                phone_hash(to_phone),
                result.error,
            )
            return result, attempts

        if attempts < MAX_ATTEMPTS:
            delay = BACKOFF_BASE_SECONDS ** attempts
            logger.warning(
                "Retryable failure for hash=%s (attempt %d/%d), retrying in %.1fs: %s",
                phone_hash(to_phone),
                attempts,
                MAX_ATTEMPTS,
                delay,
                result.error,
            )
            sleep(delay)

    return result, attempts


def dispatch(
    evaluation: Evaluation,
    dry_run: bool = False,
    store: Optional[MessagingStore] = None,
    notifier: Optional[Notifier] = None,
    now: Optional[datetime] = None,
    sleep=time.sleep,
) -> DispatchReport:
    """
    Run the full send path for one station evaluation.

    Args:
        evaluation: the analysis pipeline's verdict for a station
        dry_run: do everything except call notifier.send(). Suppressions are
            still evaluated and reported, but nothing is written to the DB.
        store / notifier: injectable for tests
        now: injectable clock for the trigger
        sleep: injectable so tests do not actually wait through backoff

    Returns:
        DispatchReport with one FarmerOutcome per farmer considered.
    """
    store = store or MessagingStore()
    notifier = notifier or get_notifier()

    report = DispatchReport(
        station_id=evaluation.station_id,
        band=evaluation.band,
        reason_code=evaluation.reason_code,
        dry_run=dry_run,
    )

    farmers = store.farmers_for_station(evaluation.station_id)
    logger.info(
        "Dispatch station=%s band=%s reason=%s farmers=%d dry_run=%s",
        evaluation.station_id,
        evaluation.band,
        evaluation.reason_code,
        len(farmers),
        dry_run,
    )

    for farmer in farmers:
        decision = should_alert(farmer.id, evaluation.band, store=store, now=now)

        # --- Suppressed ------------------------------------------------
        if not decision.should_alert:
            outcome = FarmerOutcome(
                farmer_id=farmer.id,
                phone_hash=farmer.phone_hashed,
                village=farmer.village,
                language=farmer.language.value,
                decision=decision,
                status="suppressed",
            )
            # Record the suppression so it is visible in the operator UI.
            if not dry_run:
                try:
                    composed = compose(
                        evaluation.band,
                        evaluation.reason_code,
                        farmer.language,
                        _build_variables(farmer, evaluation),
                    )
                    template_key, text = composed.template_key, composed.text
                except TemplateError:
                    template_key, text = "(suppressed)", decision.reason
                alert = store.save_alert(
                    Alert(
                        farmer_id=farmer.id,
                        station_id=evaluation.station_id,
                        band=evaluation.band,
                        reason_code=evaluation.reason_code,
                        language=farmer.language.value,
                        template_key=template_key,
                        message_text=text,
                        status=AlertStatus.SUPPRESSED,
                        error=decision.reason,
                    )
                )
                outcome.alert_id = alert.id
                outcome.template_key = template_key
                outcome.message_text = text
            report.outcomes.append(outcome)
            continue

        # --- Compose ---------------------------------------------------
        try:
            composed: ComposedMessage = compose(
                evaluation.band,
                evaluation.reason_code,
                farmer.language,
                _build_variables(farmer, evaluation),
            )
        except TemplateError as exc:
            # Composition failure must never be silently skipped - it means a
            # farmer who should have been warned was not.
            logger.error(
                "Composition failed for farmer hash=%s lang=%s: %s",
                farmer.phone_hashed,
                farmer.language.value,
                exc,
            )
            report.outcomes.append(
                FarmerOutcome(
                    farmer_id=farmer.id,
                    phone_hash=farmer.phone_hashed,
                    village=farmer.village,
                    language=farmer.language.value,
                    decision=decision,
                    status="error",
                    error=str(exc),
                )
            )
            continue

        # --- Dry run ---------------------------------------------------
        if dry_run:
            report.outcomes.append(
                FarmerOutcome(
                    farmer_id=farmer.id,
                    phone_hash=farmer.phone_hashed,
                    village=farmer.village,
                    language=farmer.language.value,
                    decision=decision,
                    status="would_send",
                    template_key=composed.template_key,
                    message_text=composed.text,
                    provider=notifier.name,
                )
            )
            continue

        # --- Persist as pending, then send -----------------------------
        alert = store.save_alert(
            Alert(
                farmer_id=farmer.id,
                station_id=evaluation.station_id,
                band=evaluation.band,
                reason_code=evaluation.reason_code,
                language=farmer.language.value,
                template_key=composed.template_key,
                message_text=composed.text,
                status=AlertStatus.PENDING,
                provider=notifier.name,
            )
        )

        result, attempts = _send_with_retries(
            notifier, farmer.phone, composed.text, sleep=sleep
        )

        alert.attempts = attempts
        alert.provider = result.provider
        if result.ok:
            alert.status = AlertStatus.SENT
            alert.provider_message_id = result.message_id
            alert.sent_ts = datetime.now()
            alert.error = None
        else:
            alert.status = AlertStatus.FAILED
            alert.error = result.error
        store.update_alert(alert)

        report.outcomes.append(
            FarmerOutcome(
                farmer_id=farmer.id,
                phone_hash=farmer.phone_hashed,
                village=farmer.village,
                language=farmer.language.value,
                decision=decision,
                status="sent" if result.ok else "failed",
                template_key=composed.template_key,
                message_text=composed.text,
                provider=result.provider,
                provider_message_id=result.message_id,
                error=result.error,
                attempts=attempts,
                alert_id=alert.id,
            )
        )

    logger.info("Dispatch complete for %s: %s", evaluation.station_id, report.summary())
    return report


# ---------------------------------------------------------------------------
# Evaluation adapter
# ---------------------------------------------------------------------------


def evaluate_station(station_id: str) -> Optional[Evaluation]:
    """
    Adapter from the existing analysis pipeline to an Evaluation.

    NOTE ON REASON CODES: this dataset carries no extraction measurements, so
    "high extraction" is INFERRED from the decline rate and "low rain" from the
    seasonal deviation. These are proxies, not observations. A production
    system should take real abstraction data rather than inferring intent from
    a slope.

    Does not modify the analysis pipeline - it only reads from it.
    """
    from data_store import DataStore
    from processing_engine import DAYS_PER_YEAR, ProcessingEngine

    data_store = DataStore()
    readings = data_store.get_readings(station_id)
    if not readings:
        return None

    reference_date = data_store.get_max_reading_date(station_id)
    metrics = ProcessingEngine().calculate_metrics(
        readings, calculation_date=datetime.combine(reference_date, datetime.min.time())
    )

    risk_to_band = {
        "Critical Risk": "CRITICAL",
        "High Risk": "WARNING",
        "Moderate Risk": "WATCH",
        "Low Risk": "NORMAL",
    }
    band = risk_to_band.get(metrics.risk_level.value if metrics.risk_level else "", "WATCH")

    rate = (
        metrics.trend_metrics.slope * DAYS_PER_YEAR
        if metrics.trend_metrics
        else 0.0
    )
    deviation = metrics.seasonal_deviation

    high_extraction = rate >= 0.5
    low_rain = deviation is not None and deviation <= -1.0

    if band == "NORMAL":
        # The only NORMAL template is RECOVERED, and the trigger only fires on a
        # transition into NORMAL, which is precisely a recovery.
        reason_code = "RECOVERED"
    elif band == "CRITICAL":
        reason_code = (
            "DECLINE_LOW_RAIN_HIGH_EXTRACTION"
            if (high_extraction and low_rain)
            else "DEFAULT"
        )
    elif band == "WARNING":
        if high_extraction:
            reason_code = "DECLINE_HIGH_EXTRACTION"
        elif low_rain:
            reason_code = "DECLINE_LOW_RAIN"
        else:
            reason_code = "DEFAULT"
    else:
        reason_code = "DEFAULT"

    return Evaluation(
        station_id=station_id,
        band=band,
        reason_code=reason_code,
        metrics={
            "decline_m_per_year": rate,
            "trend": metrics.trend_indicator.value,
            "risk_index": metrics.risk_index,
            "risk_level": metrics.risk_level.value if metrics.risk_level else None,
            "seasonal_deviation": deviation,
            "data_points_used": metrics.data_points_used,
        },
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_report(report: DispatchReport) -> None:
    header = f" STATION {report.station_id} "
    print("\n" + "=" * 78)
    print(header.center(78, "="))
    print("=" * 78)
    print(f"  band        : {report.band}")
    print(f"  reason_code : {report.reason_code}")
    print(f"  mode        : {'DRY RUN (nothing sent, nothing written)' if report.dry_run else 'LIVE'}")
    print(f"  farmers     : {len(report.outcomes)}")

    if not report.outcomes:
        print("\n  No consenting, active farmers attached to this station.")
        return

    for outcome in report.outcomes:
        print("\n  " + "-" * 74)
        print(f"  farmer id={outcome.farmer_id}  hash={outcome.phone_hash}  "
              f"village={outcome.village}  lang={outcome.language}")
        print(f"  decision : {'ALERT' if outcome.decision.should_alert else 'SUPPRESS'}"
              f"  ({outcome.decision.reason})")
        print(f"  status   : {outcome.status.upper()}")
        if outcome.template_key:
            print(f"  template : {outcome.template_key}")
        if outcome.message_text:
            print(f"  message  : {outcome.message_text}")
        if outcome.provider_message_id:
            print(f"  msg id   : {outcome.provider_message_id}")
        if outcome.attempts:
            print(f"  attempts : {outcome.attempts}")
        if outcome.error:
            print(f"  error    : {outcome.error}")

    print("\n  " + "-" * 74)
    print(f"  SUMMARY: {report.summary()}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m messaging.dispatch",
        description="Dispatch groundwater advisories to registered farmers.",
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--station", help="Dispatch for a single station id")
    target.add_argument(
        "--all", action="store_true", help="Dispatch for every station with farmers"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do everything except send. Nothing is written to the database.",
    )
    parser.add_argument(
        "--notifier",
        choices=("console", "whatsapp", "telegram"),
        help="Override the NOTIFIER environment variable.",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Debug logging")
    args = parser.parse_args(argv)

    # Must happen before any print(): advisories are Gurmukhi/Devanagari and
    # Windows stdout defaults to a code page that cannot encode them.
    ensure_utf8_output()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-8s %(name)s: %(message)s",
    )

    store = MessagingStore()
    notifier = get_notifier(args.notifier)

    if not args.dry_run and notifier.name != "console":
        print(
            f"\n*** LIVE MODE: messages will be delivered via {notifier.name} "
            f"to real recipients. ***"
        )

    station_ids = [args.station] if args.station else store.stations_with_farmers()
    if not station_ids:
        print(
            "No stations have consenting, active farmers registered. "
            "Register a farmer in the Streamlit app first."
        )
        return 0

    total_missing = 0
    reports = []
    for station_id in station_ids:
        evaluation = evaluate_station(station_id)
        if evaluation is None:
            print(f"\nStation {station_id}: no readings in the database - skipped.")
            total_missing += 1
            continue
        report = dispatch(
            evaluation, dry_run=args.dry_run, store=store, notifier=notifier
        )
        reports.append(report)
        _print_report(report)

    if len(reports) > 1:
        print("\n" + "=" * 78)
        print(" OVERALL ".center(78, "="))
        print("=" * 78)
        print(f"  stations processed : {len(reports)}")
        print(f"  would send         : {sum(r.would_send for r in reports)}")
        print(f"  sent               : {sum(r.sent for r in reports)}")
        print(f"  suppressed         : {sum(r.suppressed for r in reports)}")
        print(f"  failed             : {sum(r.failed for r in reports)}")
        print(f"  errors             : {sum(r.errored for r in reports)}")
    if total_missing:
        print(f"  stations skipped   : {total_missing} (no readings)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
