"""
Pre-demo healthcheck.

Run this before the demo:

    python -m messaging.healthcheck

Verifies templates load and validate, the database is reachable with the
tables and data it needs, and the configured notifier is healthy. Exits
non-zero if anything is wrong, so it can gate a launch script.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from typing import List, Tuple

from dotenv import load_dotenv

load_dotenv()

CHECK = "  [ OK ]"
CROSS = "  [FAIL]"
WARN = "  [WARN]"


class Check:
    """One healthcheck result."""

    def __init__(self, name: str, ok: bool, detail: str, fatal: bool = True):
        self.name = name
        self.ok = ok
        self.detail = detail
        self.fatal = fatal

    def render(self) -> str:
        marker = CHECK if self.ok else (CROSS if self.fatal else WARN)
        return f"{marker}  {self.name}\n         {self.detail}"


def check_templates() -> List[Check]:
    checks = []
    try:
        from messaging.composer import (
            MAX_TEMPLATE_CHARS,
            REQUIRED_KEYS,
            REQUIRED_LANGUAGES,
            TEMPLATES,
        )

        checks.append(
            Check(
                "Template library loads and validates",
                True,
                f"{len(TEMPLATES)} keys x {len(REQUIRED_LANGUAGES)} languages "
                f"({', '.join(REQUIRED_LANGUAGES)}), all <= {MAX_TEMPLATE_CHARS} chars",
            )
        )

        missing = [k for k in REQUIRED_KEYS if k not in TEMPLATES]
        checks.append(
            Check(
                "All required template keys present",
                not missing,
                "none missing" if not missing else f"MISSING: {missing}",
            )
        )

        # Prove composition actually works in every language for every band.
        from messaging.composer import compose

        failures = []
        for key in TEMPLATES:
            band, _, reason = key.partition("__")
            for lang in REQUIRED_LANGUAGES:
                try:
                    compose(
                        band,
                        reason,
                        lang,
                        {
                            "village": "Test",
                            "district": "Test",
                            "decline_m_per_year": "0.50",
                        },
                    )
                except Exception as exc:  # noqa: BLE001
                    failures.append(f"{key}/{lang}: {exc}")
        checks.append(
            Check(
                "Every template composes in every language",
                not failures,
                f"{len(TEMPLATES) * len(REQUIRED_LANGUAGES)} combinations composed"
                if not failures
                else "FAILURES: " + "; ".join(failures[:5]),
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(Check("Template library loads and validates", False, str(exc)))
    return checks


def check_database() -> List[Check]:
    checks = []
    try:
        from config import config

        db_path = config.db_path
        if not os.path.exists(db_path):
            checks.append(
                Check(
                    "Database reachable",
                    False,
                    f"{db_path} does not exist. Run: python load_dataset.py",
                )
            )
            return checks

        conn = sqlite3.connect(db_path)
        try:
            cur = conn.cursor()
            tables = {
                r[0]
                for r in cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            checks.append(
                Check("Database reachable", True, f"{db_path} ({len(tables)} tables)")
            )

            for table in ("stations", "readings"):
                present = table in tables
                count = (
                    cur.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                    if present
                    else 0
                )
                checks.append(
                    Check(
                        f"Analysis table '{table}' populated",
                        present and count > 0,
                        f"{count} rows"
                        if present
                        else "table missing - run: python load_dataset.py",
                    )
                )

            # Messaging tables are created on demand by MessagingStore.
            from messaging.store import MessagingStore

            store = MessagingStore()
            farmers = store.all_farmers()
            active = [f for f in farmers if f.active and f.consent]
            checks.append(
                Check(
                    "Messaging tables present",
                    True,
                    f"farmers={len(farmers)} (consenting+active={len(active)})",
                )
            )
            checks.append(
                Check(
                    "At least one farmer registered",
                    len(active) > 0,
                    f"{len(active)} consenting active farmer(s)"
                    if active
                    else "none - register one in the Streamlit app before the demo",
                    fatal=False,
                )
            )

            stations_with = store.stations_with_farmers()
            checks.append(
                Check(
                    "Farmers attached to stations",
                    bool(stations_with),
                    f"{len(stations_with)} station(s): {stations_with[:5]}"
                    if stations_with
                    else "no station has farmers attached",
                    fatal=False,
                )
            )
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        checks.append(Check("Database reachable", False, str(exc)))
    return checks


def check_notifier() -> List[Check]:
    checks = []
    selected = os.getenv("NOTIFIER", "console").strip().lower()
    try:
        from messaging.notifiers.factory import get_notifier

        notifier = get_notifier()
        checks.append(
            Check(
                "Notifier constructed",
                True,
                f"NOTIFIER={selected!r} -> {notifier.name}"
                + (
                    "  (console is the safe default; nothing leaves this machine)"
                    if notifier.name == "console"
                    else "  *** LIVE PROVIDER - real messages will be sent ***"
                ),
            )
        )
        health = notifier.healthcheck()
        checks.append(
            Check(f"Notifier '{health.provider}' healthy", health.ok, health.detail)
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(
            Check(
                "Notifier constructed",
                False,
                f"NOTIFIER={selected!r} failed to initialise: {exc}",
            )
        )
    return checks


def run() -> Tuple[bool, List[Check]]:
    checks: List[Check] = []
    checks += check_templates()
    checks += check_database()
    checks += check_notifier()
    ok = all(c.ok for c in checks if c.fatal)
    return ok, checks


def main() -> int:
    from messaging.textio import ensure_utf8_output

    ensure_utf8_output()

    print("=" * 78)
    print(" GROUNDWATER ADVISORY - PRE-DEMO HEALTHCHECK ".center(78, "="))
    print("=" * 78)

    ok, checks = run()

    print("\nTEMPLATES")
    for c in checks[:3]:
        print(c.render())
    print("\nDATABASE")
    for c in checks[3:-2]:
        print(c.render())
    print("\nNOTIFIER")
    for c in checks[-2:]:
        print(c.render())

    failures = [c for c in checks if not c.ok and c.fatal]
    warnings = [c for c in checks if not c.ok and not c.fatal]

    print("\n" + "=" * 78)
    if ok:
        print("RESULT: READY" + (f"  ({len(warnings)} warning(s))" if warnings else ""))
    else:
        print(f"RESULT: NOT READY - {len(failures)} fatal problem(s)")
        for c in failures:
            print(f"  - {c.name}: {c.detail}")
    print("=" * 78)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
