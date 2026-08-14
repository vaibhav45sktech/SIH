"""
Shared fixtures.

Every test that touches storage uses a temporary SQLite file, so tests never
read or write the real groundwater_data.db.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

# Make the project root importable when pytest is run from anywhere.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from messaging.notifiers.console import ConsoleNotifier  # noqa: E402
from messaging.store import MessagingStore  # noqa: E402

# Two real Punjab stations from the loaded dataset, ~44 km apart.
STATION_NEAR = ("300215074204501", 30.0375, 74.3458)   # Sangrur-area bbox station
STATION_FAR = ("320045075020001", 32.0075, 75.0333)


@pytest.fixture
def db_path(tmp_path) -> str:
    """A temporary DB pre-populated with a stations table."""
    path = tmp_path / "test_groundwater.db"
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE stations (
                station_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                latitude REAL,
                longitude REAL,
                district TEXT,
                state TEXT,
                elevation_m REAL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        for station_id, lat, lon in (STATION_NEAR, STATION_FAR):
            conn.execute(
                "INSERT INTO stations (station_id, name, latitude, longitude, state) "
                "VALUES (?, ?, ?, ?, 'Punjab')",
                (station_id, f"Station {station_id}", lat, lon),
            )
        conn.commit()
    finally:
        conn.close()
    return str(path)


@pytest.fixture
def store(db_path) -> MessagingStore:
    return MessagingStore(db_path=db_path)


@pytest.fixture
def notifier() -> ConsoleNotifier:
    # quiet=True so test output stays readable.
    return ConsoleNotifier(quiet=True)


@pytest.fixture
def registered_farmer(store):
    """A consenting farmer sitting almost on top of STATION_NEAR."""
    return store.register_farmer(
        name="Test Farmer",
        phone="9876543210",
        village="Longowal",
        district="Sangrur",
        language="pa",
        latitude=STATION_NEAR[1] + 0.01,
        longitude=STATION_NEAR[2] + 0.01,
        consent=True,
        registered_by="pytest",
    )
