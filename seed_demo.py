"""
Demo seeding - additive, and separate from the analysis pipeline on purpose.

Does NOT modify load_dataset.py, its curated station selection, or the CSV.
It only ADDS rows so the messaging layer has something local to demonstrate
against.

Why this exists:
    The curated 15-station set is chosen by reading count, and reading count
    is anti-correlated with record span in this dataset (see README, Known
    Issues #2). The practical consequence is geographic: Sangrur district has
    12 stations in the CSV that pass both the datum and slope filters, all
    DEPLETING at 0.6-1.6 m/yr, but none has enough readings to make the
    top-15. So no station lands within the 25 km advisory radius of any
    Sangrur town, and the guard in messaging/store.py correctly refuses to
    register Sangrur farmers.

    That guard is doing its job. The fix belongs in station selection, not in
    the guard. Until selection is span-weighted and geographically aware,
    this script seeds the nearest Sangrur station so the delivery layer can
    be demonstrated end to end.

Usage:
    python seed_demo.py             # seed station + demo farmers
    python seed_demo.py --clear     # remove seeded demo farmers
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd

from data_store import DataStore
from messaging.store import MessagingStore, NoNearbyStationError, RegistrationError
from models.reading import Reading
from models.station import Station

CSV_FILE = "groundwater_data.csv"

# 9.0 km from Sangrur town; DEPLETING at ~+1.08 m/yr; all-positive target
# values, so the datum is unambiguous; 32 readings.
DEMO_STATION_ID = "301710075454002"

# Three farmers in three languages, all within the 25 km radius, so the demo
# shows per-farmer language selection rather than one message repeated.
DEMO_FARMERS = [
    {
        "name": "Balwinder Singh",
        "phone": "9876500101",
        "village": "Bhawanigarh",
        "district": "Sangrur",
        "language": "pa",
        "latitude": 30.2700,
        "longitude": 75.7500,
    },
    {
        "name": "Ramesh Kumar",
        "phone": "9876500102",
        "village": "Dhuri",
        "district": "Sangrur",
        "language": "hi",
        "latitude": 30.3000,
        "longitude": 75.7800,
    },
    {
        "name": "Harpreet Kaur",
        "phone": "9876500103",
        "village": "Longowal",
        "district": "Sangrur",
        "language": "en",
        "latitude": 30.2600,
        "longitude": 75.7400,
    },
]


def seed_station() -> bool:
    """Ingest the demo station's readings from the CSV. Idempotent."""
    store = DataStore()

    if store.get_readings(DEMO_STATION_ID):
        print(f"Station {DEMO_STATION_ID} already has readings - skipping ingest.")
        return True

    print(f"Reading {CSV_FILE} for station {DEMO_STATION_ID}...")
    df = pd.read_csv(
        CSV_FILE,
        usecols=["station_id", "datetime", "target", "latitude", "longitude"],
        dtype={"station_id": str},
    )
    sub = df[df["station_id"] == DEMO_STATION_ID].copy()
    if sub.empty:
        print(f"ERROR: station {DEMO_STATION_ID} not found in {CSV_FILE}.")
        return False

    sub["datetime"] = pd.to_datetime(sub["datetime"])
    sub = sub.sort_values("datetime")

    store.save_station(
        Station(
            station_id=DEMO_STATION_ID,
            name=f"Station {DEMO_STATION_ID}",
            latitude=float(sub["latitude"].iloc[0]),
            longitude=float(sub["longitude"].iloc[0]),
            district="Sangrur",
            state="Punjab",
            description=(
                "Seeded by seed_demo.py for the advisory demo - nearest "
                "datum-unambiguous station to Sangrur town (~9 km). Not part "
                "of load_dataset.py's curated selection."
            ),
        )
    )
    store.save_readings([
        Reading(
            station_id=DEMO_STATION_ID,
            timestamp=row["datetime"].to_pydatetime(),
            water_level_m=float(row["target"]),
            quality_flag="GOOD",
            source="CSV",
        )
        for _, row in sub.iterrows()
    ])
    print(f"Seeded station {DEMO_STATION_ID} with {len(sub)} readings "
          f"({sub['latitude'].iloc[0]:.4f}, {sub['longitude'].iloc[0]:.4f}).")
    return True


def seed_farmers() -> None:
    store = MessagingStore()
    for spec in DEMO_FARMERS:
        existing = store.get_farmer_by_phone(spec["phone"])
        if existing:
            print(f"  {spec['name']:<18} already registered (id {existing.id})")
            continue
        try:
            farmer = store.register_farmer(consent=True, registered_by="seed_demo.py", **spec)
        except (NoNearbyStationError, RegistrationError, ValueError) as exc:
            print(f"  {spec['name']:<18} REFUSED: {exc}")
            continue
        print(
            f"  {farmer.name:<18} id={farmer.id} lang={farmer.language.value} "
            f"village={farmer.village:<12} station={farmer.nearest_station_id} "
            f"({farmer.distance_km:.1f} km)"
        )


def clear_farmers() -> None:
    store = MessagingStore()
    for spec in DEMO_FARMERS:
        farmer = store.get_farmer_by_phone(spec["phone"])
        if farmer:
            store.opt_out(farmer.id)
            print(f"  opted out {farmer.name} (id {farmer.id}) - row retained for audit")


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed demo station and farmers.")
    parser.add_argument("--clear", action="store_true", help="Opt out the demo farmers")
    args = parser.parse_args()

    if args.clear:
        print("Clearing demo farmers...")
        clear_farmers()
        return 0

    print("=" * 70)
    print("SEEDING DEMO DATA (additive - analysis pipeline untouched)")
    print("=" * 70)
    if not seed_station():
        return 1
    print("\nRegistering demo farmers:")
    seed_farmers()
    print("\nDone. Next:")
    print(f"  python -m messaging.dispatch --station {DEMO_STATION_ID} --dry-run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
