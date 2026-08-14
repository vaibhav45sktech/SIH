"""Haversine, the 25 km rejection, consent filtering, and the opt-out trail."""

from __future__ import annotations

import sqlite3

import pytest

from messaging.models import Language, PhoneError
from messaging.store import (
    MAX_STATION_DISTANCE_KM,
    MessagingStore,
    NoNearbyStationError,
    RegistrationError,
    haversine_km,
)
from tests.conftest import STATION_FAR, STATION_NEAR


class TestHaversine:
    def test_zero_distance(self):
        assert haversine_km(30.0, 75.0, 30.0, 75.0) == pytest.approx(0.0, abs=1e-9)

    def test_symmetric(self):
        a = haversine_km(30.0, 75.0, 31.0, 76.0)
        b = haversine_km(31.0, 76.0, 30.0, 75.0)
        assert a == pytest.approx(b)

    def test_one_degree_latitude_is_about_111km(self):
        # A degree of latitude is ~111.2 km anywhere on the globe.
        assert haversine_km(30.0, 75.0, 31.0, 75.0) == pytest.approx(111.2, abs=1.0)

    def test_known_distance_amritsar_to_ludhiana(self):
        # Amritsar (31.63, 74.87) -> Ludhiana (30.90, 75.85): ~127 km.
        d = haversine_km(31.63, 74.87, 30.90, 75.85)
        assert 120 < d < 135

    def test_longitude_degree_shorter_at_higher_latitude(self):
        assert haversine_km(30.0, 75.0, 30.0, 76.0) > haversine_km(
            60.0, 75.0, 60.0, 76.0
        )


class TestNearestStation:
    def test_picks_the_closer_station(self, store):
        station_id, dist = store.find_nearest_station(
            STATION_NEAR[1] + 0.01, STATION_NEAR[2] + 0.01
        )
        assert station_id == STATION_NEAR[0]
        assert dist < 2.0

    def test_returns_none_when_no_stations(self, tmp_path):
        empty = tmp_path / "empty.db"
        conn = sqlite3.connect(empty)
        conn.execute(
            "CREATE TABLE stations (station_id TEXT PRIMARY KEY, name TEXT, "
            "latitude REAL, longitude REAL, district TEXT, state TEXT, "
            "elevation_m REAL, description TEXT)"
        )
        conn.commit()
        conn.close()
        store = MessagingStore(db_path=str(empty))
        assert store.find_nearest_station(30.0, 75.0) == (None, None)

    def test_ignores_zero_zero_coordinates(self, store, db_path):
        # app.py can synthesise stations with lat/lon 0; those must not win.
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO stations (station_id, name, latitude, longitude) "
            "VALUES ('ZERO', 'Zero Station', 0, 0)"
        )
        conn.commit()
        conn.close()
        station_id, _ = store.find_nearest_station(0.05, 0.05)
        assert station_id != "ZERO"


class TestRegistration:
    def _register(self, store, **over):
        kwargs = dict(
            name="Balwinder Singh",
            phone="9876543210",
            village="Longowal",
            district="Sangrur",
            language="pa",
            latitude=STATION_NEAR[1] + 0.01,
            longitude=STATION_NEAR[2] + 0.01,
            consent=True,
        )
        kwargs.update(over)
        return store.register_farmer(**kwargs)

    def test_successful_registration_assigns_station(self, store):
        farmer = self._register(store)
        assert farmer.id is not None
        assert farmer.nearest_station_id == STATION_NEAR[0]
        assert farmer.distance_km < MAX_STATION_DISTANCE_KM
        assert farmer.phone == "+919876543210"
        assert farmer.language is Language.PA

    def test_rejects_when_nearest_station_beyond_25km(self, store):
        # Sit ~55 km north of the far station and far from the near one.
        with pytest.raises(NoNearbyStationError) as exc:
            self._register(
                store,
                latitude=STATION_FAR[1] + 0.5,
                longitude=STATION_FAR[2] + 0.5,
                phone="9876500001",
                village="Faraway",
            )
        message = str(exc.value)
        assert "km away" in message
        assert "25" in message
        # The error must explain why, not just refuse.
        assert "would not describe this farmer" in message

    def test_rejection_message_names_the_village(self, store):
        with pytest.raises(NoNearbyStationError, match="Faraway"):
            self._register(
                store,
                latitude=STATION_FAR[1] + 0.5,
                longitude=STATION_FAR[2] + 0.5,
                phone="9876500002",
                village="Faraway",
            )

    def test_boundary_just_inside_25km_is_accepted(self, store):
        # ~0.18 deg latitude ≈ 20 km.
        farmer = self._register(
            store,
            latitude=STATION_NEAR[1] + 0.18,
            longitude=STATION_NEAR[2],
            phone="9876500003",
        )
        assert 15 < farmer.distance_km < 25

    def test_rejected_farmer_is_not_persisted(self, store):
        with pytest.raises(NoNearbyStationError):
            self._register(
                store,
                latitude=STATION_FAR[1] + 0.5,
                longitude=STATION_FAR[2] + 0.5,
                phone="9876500004",
            )
        assert store.get_farmer_by_phone("9876500004") is None

    def test_consent_false_raises_and_persists_nothing(self, store):
        with pytest.raises(ValueError, match="without consent"):
            self._register(store, consent=False, phone="9876500005")
        assert store.get_farmer_by_phone("9876500005") is None

    def test_duplicate_phone_rejected(self, store):
        self._register(store)
        with pytest.raises(RegistrationError, match="already registered"):
            self._register(store, name="Someone Else")

    def test_duplicate_error_does_not_leak_the_number(self, store):
        self._register(store)
        with pytest.raises(RegistrationError) as exc:
            self._register(store)
        assert "9876543210" not in str(exc.value)

    def test_invalid_phone_rejected(self, store):
        with pytest.raises(PhoneError):
            self._register(store, phone="123")

    def test_requires_coordinates_or_explicit_station(self, store):
        with pytest.raises(RegistrationError, match="latitude/longitude"):
            store.register_farmer(
                name="No Coords",
                phone="9876500006",
                village="X",
                district="Y",
                consent=True,
            )

    def test_explicit_station_bypasses_coordinate_lookup(self, store):
        farmer = store.register_farmer(
            name="Direct",
            phone="9876500007",
            village="X",
            district="Y",
            consent=True,
            nearest_station_id=STATION_NEAR[0],
            distance_km=3.0,
        )
        assert farmer.nearest_station_id == STATION_NEAR[0]

    def test_explicit_station_still_subject_to_distance_limit(self, store):
        with pytest.raises(NoNearbyStationError):
            store.register_farmer(
                name="Too Far",
                phone="9876500008",
                village="X",
                district="Y",
                consent=True,
                nearest_station_id=STATION_NEAR[0],
                distance_km=60.0,
            )


class TestConsentFiltering:
    def test_farmers_for_station_returns_consenting_active(self, store, registered_farmer):
        farmers = store.farmers_for_station(STATION_NEAR[0])
        assert [f.id for f in farmers] == [registered_farmer.id]

    def test_opt_out_excludes_from_dispatch(self, store, registered_farmer):
        assert store.opt_out(registered_farmer.id) is True
        assert store.farmers_for_station(STATION_NEAR[0]) == []

    def test_opt_out_never_deletes_the_row(self, store, registered_farmer):
        store.opt_out(registered_farmer.id)
        # The consent trail must survive: we must still be able to prove
        # consent was given and when it was withdrawn.
        still_there = store.get_farmer(registered_farmer.id)
        assert still_there is not None
        assert still_there.active is False
        assert still_there.consent is True
        assert still_there.consent_ts is not None

    def test_opt_out_row_visible_in_all_farmers(self, store, registered_farmer):
        store.opt_out(registered_farmer.id)
        assert len(store.all_farmers(include_inactive=True)) == 1
        assert len(store.all_farmers(include_inactive=False)) == 0

    def test_opt_in_restores(self, store, registered_farmer):
        store.opt_out(registered_farmer.id)
        assert store.opt_in(registered_farmer.id) is True
        assert len(store.farmers_for_station(STATION_NEAR[0])) == 1

    def test_manually_revoked_consent_excluded(self, store, registered_farmer, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE farmers SET consent = 0 WHERE id = ?", (registered_farmer.id,))
        conn.commit()
        conn.close()
        assert store.farmers_for_station(STATION_NEAR[0]) == []

    def test_other_station_not_matched(self, store, registered_farmer):
        assert store.farmers_for_station(STATION_FAR[0]) == []

    def test_stations_with_farmers(self, store, registered_farmer):
        assert store.stations_with_farmers() == [STATION_NEAR[0]]
        store.opt_out(registered_farmer.id)
        assert store.stations_with_farmers() == []
