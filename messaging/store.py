"""
Persistence for farmers and alerts.

Shares the analysis pipeline's SQLite file so an advisory can be traced to
the readings it came from, but owns only its own two tables and never writes
to stations/readings/metrics.

PRIVACY CONTRACT
    Raw phone numbers appear in exactly one place: the farmers.phone column.
    They are never logged. Every log line in this module uses phone_hash().
"""

from __future__ import annotations

import logging
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import List, Optional, Tuple

from config import config
from messaging.models import (
    Alert,
    AlertStatus,
    Farmer,
    Language,
    normalise_phone,
    phone_hash,
)

logger = logging.getLogger(__name__)

# An advisory is only meaningful if the station is plausibly measuring the
# same aquifer the farmer draws from. Beyond this, it is someone else's water.
MAX_STATION_DISTANCE_KM = 25.0

EARTH_RADIUS_KM = 6371.0088


class RegistrationError(Exception):
    """Raised when a farmer cannot be registered."""


class NoNearbyStationError(RegistrationError):
    """Raised when the nearest monitoring station is too far to be relevant."""


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two WGS84 points, in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


class MessagingStore:
    """SQLite persistence for farmers and alerts."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or config.db_path
        self._initialise_schema()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _initialise_schema(self) -> None:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS farmers (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    name                TEXT    NOT NULL,
                    phone               TEXT    NOT NULL UNIQUE,
                    village             TEXT    NOT NULL,
                    district            TEXT    NOT NULL,
                    language            TEXT    NOT NULL DEFAULT 'en',
                    consent             INTEGER NOT NULL DEFAULT 0,
                    consent_ts          TIMESTAMP,
                    nearest_station_id  TEXT,
                    distance_km         REAL,
                    registered_by       TEXT,
                    active              INTEGER NOT NULL DEFAULT 1,
                    created_ts          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS alerts (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    farmer_id           INTEGER NOT NULL,
                    station_id          TEXT    NOT NULL,
                    band                TEXT    NOT NULL,
                    reason_code         TEXT,
                    language            TEXT    NOT NULL,
                    template_key        TEXT    NOT NULL,
                    message_text        TEXT    NOT NULL,
                    status              TEXT    NOT NULL DEFAULT 'pending',
                    provider            TEXT,
                    provider_message_id TEXT,
                    error               TEXT,
                    attempts            INTEGER NOT NULL DEFAULT 0,
                    created_ts          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    sent_ts             TIMESTAMP,
                    FOREIGN KEY (farmer_id) REFERENCES farmers(id)
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_farmers_station ON farmers(nearest_station_id, consent, active)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_alerts_farmer ON alerts(farmer_id, created_ts)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status)")

    # ------------------------------------------------------------------
    # Station lookup
    # ------------------------------------------------------------------

    def load_stations(self) -> List[Tuple[str, float, float]]:
        """All stations that have usable coordinates."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT station_id, latitude, longitude FROM stations
                WHERE latitude IS NOT NULL AND longitude IS NOT NULL
                  AND NOT (latitude = 0 AND longitude = 0)
                """
            ).fetchall()
        return [(r["station_id"], r["latitude"], r["longitude"]) for r in rows]

    def find_nearest_station(
        self, latitude: float, longitude: float
    ) -> Tuple[Optional[str], Optional[float]]:
        """Nearest station by great-circle distance. (None, None) if none exist."""
        stations = self.load_stations()
        if not stations:
            return None, None
        best_id, best_km = None, float("inf")
        for station_id, lat, lon in stations:
            d = haversine_km(latitude, longitude, lat, lon)
            if d < best_km:
                best_id, best_km = station_id, d
        return best_id, best_km

    # ------------------------------------------------------------------
    # Farmer registration
    # ------------------------------------------------------------------

    def register_farmer(
        self,
        name: str,
        phone: str,
        village: str,
        district: str,
        language: Language | str = Language.EN,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        consent: bool = False,
        registered_by: Optional[str] = None,
        nearest_station_id: Optional[str] = None,
        distance_km: Optional[float] = None,
    ) -> Farmer:
        """
        Register a farmer, assigning their nearest monitoring station.

        Rejects registration when the nearest station is further than
        MAX_STATION_DISTANCE_KM. An advisory generated from a station 60 km
        away is not about this farmer's water, and sending it anyway would
        train them to ignore the ones that are.

        Raises:
            ValueError: consent not given, or invalid field.
            NoNearbyStationError: nearest station beyond the distance limit.
            RegistrationError: phone already registered.
        """
        # Farmer.__post_init__ enforces consent and normalises the phone.
        farmer = Farmer(
            name=name,
            phone=phone,
            village=village,
            district=district,
            language=language if isinstance(language, Language) else Language(str(language).lower()),
            consent=consent,
            nearest_station_id=nearest_station_id,
            distance_km=distance_km,
            registered_by=registered_by,
        )

        # Resolve station from coordinates when not supplied directly.
        if farmer.nearest_station_id is None:
            if latitude is None or longitude is None:
                raise RegistrationError(
                    "Either latitude/longitude or an explicit nearest_station_id "
                    "must be provided so the farmer can be tied to a station."
                )
            station_id, dist = self.find_nearest_station(latitude, longitude)
            if station_id is None:
                raise NoNearbyStationError(
                    "No monitoring stations with coordinates are loaded. "
                    "Run `python load_dataset.py` before registering farmers."
                )
            farmer.nearest_station_id = station_id
            farmer.distance_km = dist

        if farmer.distance_km is not None and farmer.distance_km > MAX_STATION_DISTANCE_KM:
            raise NoNearbyStationError(
                f"Nearest monitoring station ({farmer.nearest_station_id}) is "
                f"{farmer.distance_km:.1f} km away, beyond the "
                f"{MAX_STATION_DISTANCE_KM:.0f} km limit. An advisory from that "
                f"station would not describe this farmer's groundwater, so "
                f"registration is refused rather than sending misleading advice. "
                f"A closer station must be monitored to cover {farmer.village}."
            )

        try:
            with self._connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO farmers
                        (name, phone, village, district, language, consent, consent_ts,
                         nearest_station_id, distance_km, registered_by, active)
                    VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, 1)
                    """,
                    (
                        farmer.name,
                        farmer.phone,
                        farmer.village,
                        farmer.district,
                        farmer.language.value,
                        farmer.consent_ts,
                        farmer.nearest_station_id,
                        farmer.distance_km,
                        farmer.registered_by,
                    ),
                )
                farmer.id = cur.lastrowid
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc).upper():
                raise RegistrationError(
                    f"This phone number is already registered "
                    f"(hash {farmer.phone_hashed}). Use opt-in to reactivate it "
                    f"instead of registering again."
                ) from exc
            raise

        logger.info(
            "Registered farmer id=%s hash=%s village=%s station=%s dist=%.1fkm",
            farmer.id,
            farmer.phone_hashed,
            farmer.village,
            farmer.nearest_station_id,
            farmer.distance_km or 0.0,
        )
        return farmer

    # ------------------------------------------------------------------
    # Farmer queries
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_farmer(row: sqlite3.Row) -> Farmer:
        # Bypass __post_init__'s consent guard for rows already persisted:
        # historical opt-outs must remain readable for the audit trail.
        farmer = Farmer.__new__(Farmer)
        farmer.id = row["id"]
        farmer.name = row["name"]
        farmer.phone = row["phone"]
        farmer.village = row["village"]
        farmer.district = row["district"]
        farmer.language = Language(row["language"])
        farmer.consent = bool(row["consent"])
        farmer.consent_ts = row["consent_ts"]
        farmer.nearest_station_id = row["nearest_station_id"]
        farmer.distance_km = row["distance_km"]
        farmer.registered_by = row["registered_by"]
        farmer.active = bool(row["active"])
        farmer.created_ts = row["created_ts"]
        return farmer

    def get_farmer(self, farmer_id: int) -> Optional[Farmer]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM farmers WHERE id = ?", (farmer_id,)).fetchone()
        return self._row_to_farmer(row) if row else None

    def get_farmer_by_phone(self, phone: str) -> Optional[Farmer]:
        canonical = normalise_phone(phone)
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM farmers WHERE phone = ?", (canonical,)).fetchone()
        return self._row_to_farmer(row) if row else None

    def all_farmers(self, include_inactive: bool = True) -> List[Farmer]:
        sql = "SELECT * FROM farmers"
        if not include_inactive:
            sql += " WHERE active = 1"
        sql += " ORDER BY id"
        with self._connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [self._row_to_farmer(r) for r in rows]

    def farmers_for_station(self, station_id: str) -> List[Farmer]:
        """
        Consenting, active farmers attached to a station.

        The consent=1 AND active=1 filter lives in SQL rather than in caller
        code so no caller can forget it.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM farmers
                WHERE nearest_station_id = ? AND consent = 1 AND active = 1
                ORDER BY id
                """,
                (station_id,),
            ).fetchall()
        return [self._row_to_farmer(r) for r in rows]

    def stations_with_farmers(self) -> List[str]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT nearest_station_id FROM farmers
                WHERE consent = 1 AND active = 1 AND nearest_station_id IS NOT NULL
                ORDER BY nearest_station_id
                """
            ).fetchall()
        return [r["nearest_station_id"] for r in rows]

    # ------------------------------------------------------------------
    # Opt-out / opt-in
    # ------------------------------------------------------------------

    def opt_out(self, farmer_id: int) -> bool:
        """
        Deactivate a farmer. Never deletes.

        The row is retained so the consent trail stays auditable: we must be
        able to show that consent was given, when, and when it was withdrawn.
        A DELETE would destroy that evidence.
        """
        with self._connect() as conn:
            cur = conn.execute("UPDATE farmers SET active = 0 WHERE id = ?", (farmer_id,))
            changed = cur.rowcount > 0
        if changed:
            logger.info("Farmer id=%s opted out (row retained for audit)", farmer_id)
        return changed

    def opt_in(self, farmer_id: int) -> bool:
        """Reactivate a farmer who previously opted out."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE farmers SET active = 1, consent = 1, consent_ts = ? WHERE id = ?",
                (datetime.now(), farmer_id),
            )
            changed = cur.rowcount > 0
        if changed:
            logger.info("Farmer id=%s opted back in", farmer_id)
        return changed

    # ------------------------------------------------------------------
    # Alerts
    # ------------------------------------------------------------------

    def save_alert(self, alert: Alert) -> Alert:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO alerts
                    (farmer_id, station_id, band, reason_code, language, template_key,
                     message_text, status, provider, provider_message_id, error,
                     attempts, sent_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    alert.farmer_id,
                    alert.station_id,
                    alert.band,
                    alert.reason_code,
                    alert.language,
                    alert.template_key,
                    alert.message_text,
                    alert.status.value,
                    alert.provider,
                    alert.provider_message_id,
                    alert.error,
                    alert.attempts,
                    alert.sent_ts,
                ),
            )
            alert.id = cur.lastrowid
        return alert

    def update_alert(self, alert: Alert) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE alerts
                   SET status = ?, provider = ?, provider_message_id = ?,
                       error = ?, attempts = ?, sent_ts = ?
                 WHERE id = ?
                """,
                (
                    alert.status.value,
                    alert.provider,
                    alert.provider_message_id,
                    alert.error,
                    alert.attempts,
                    alert.sent_ts,
                    alert.id,
                ),
            )

    def last_sent_alert(self, farmer_id: int) -> Optional[Alert]:
        """
        Most recent SENT alert for a farmer.

        Only 'sent' counts: suppressed and failed alerts must not influence
        the band-change decision, or a failed send would wrongly satisfy the
        "already told them" test and the farmer would never be warned.
        """
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM alerts
                WHERE farmer_id = ? AND status = 'sent'
                ORDER BY COALESCE(sent_ts, created_ts) DESC, id DESC
                LIMIT 1
                """,
                (farmer_id,),
            ).fetchone()
        return self._row_to_alert(row) if row else None

    def alerts_for_farmer(self, farmer_id: int, limit: int = 100) -> List[Alert]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM alerts WHERE farmer_id = ? ORDER BY id DESC LIMIT ?",
                (farmer_id, limit),
            ).fetchall()
        return [self._row_to_alert(r) for r in rows]

    def all_alerts(self, limit: int = 500) -> List[Alert]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_alert(r) for r in rows]

    @staticmethod
    def _row_to_alert(row: sqlite3.Row) -> Alert:
        return Alert(
            id=row["id"],
            farmer_id=row["farmer_id"],
            station_id=row["station_id"],
            band=row["band"],
            reason_code=row["reason_code"],
            language=row["language"],
            template_key=row["template_key"],
            message_text=row["message_text"],
            status=AlertStatus(row["status"]),
            provider=row["provider"],
            provider_message_id=row["provider_message_id"],
            error=row["error"],
            attempts=row["attempts"],
            created_ts=row["created_ts"],
            sent_ts=row["sent_ts"],
        )
