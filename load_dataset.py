import pandas as pd

from data_store import DataStore
from models.reading import Reading
from models.station import Station

CSV_FILE = "groundwater_data.csv"

# Punjab bounding box used to select the station set below.
PUNJAB_LAT = (29.5, 32.6)
PUNJAB_LON = (73.8, 76.95)

# ---------------------------------------------------------------------------
# STATION SELECTION AND DOCUMENTED EXCLUSIONS
#
# The previous MVP set was 3 hand-picked stations. This set is the top 15 by
# reading count drawn from Punjab bbox stations with >= 30 readings, after two
# exclusions. Both exclusions are recorded here so the filtering is auditable
# and does not later read as arbitrary cherry-picking.
#
# Starting pool: 240 stations inside the Punjab bbox with >= 30 readings.
#
# EXCLUSION 1 - datum ambiguity: 65 of 240 stations dropped (59 whose target
#   values are entirely negative, 6 that change sign). The classifier in
#   processing/trend_engine.py assumes target is depth below ground level, so
#   that a positive slope means a falling water table. For these 65 stations
#   nothing in the dataset establishes whether target is depth-below-ground or
#   elevation-above-datum; under the second reading their slope sign, and
#   therefore their label, is inverted. Roughly 31 of them classify as
#   DEPLETING today and would flip to RECHARGING under the other convention.
#   Because this feeds an advisory system, a well that reports "recovering"
#   while its water table is actually falling is the one failure mode that
#   must not ship. These stations are excluded because they cannot be
#   interpreted, not because their values are unwelcome; restore them only
#   once the datum is established per station from an authoritative source.
#   The 175 all-positive stations are unambiguous and are retained.
#
# EXCLUSION 2 - implausible slope magnitude: 14 of the remaining 175 dropped
#   (13 with |3650-day slope| > 3 m/yr, plus 1 with too few points to fit).
#   Documented Punjab groundwater trends run about 0.2-1.5 m/yr; the removed
#   stations ranged to +194.9 and -53.0 m/yr. The abs(target) < 100 guard in
#   trend_engine.py does not catch these because it bounds the level, not the
#   rate of change; both guards are kept as they catch different faults.
#
# 161 stations survive. KNOWN LIMITATION: reading count is anti-correlated
# with record span in this dataset, so ranking by it favours short records.
# The 6 AAXI* entries below are daily telemetry covering only 253-495 days,
# and their annualised slopes are largely seasonal swing rather than
# multi-year trend. The 9 numeric-ID entries are quarterly manual records
# spanning ~3,650 days and are the trustworthy ones. See verify.py section 6.
# ---------------------------------------------------------------------------

MVP_STATIONS = {
    "AAXI067": {"lat": 31.7665, "lon": 76.3577},
    "AAXI108": {"lat": 31.1113, "lon": 75.3849},
    "AAXI140": {"lat": 31.4475, "lon": 76.6314},
    "AAXI005": {"lat": 30.1600, "lon": 76.3640},
    "AAXI046": {"lat": 30.8866, "lon": 75.5093},
    "AAXI147": {"lat": 31.5468, "lon": 76.3703},
    "315630075540001": {"lat": 31.9417, "lon": 75.9000},
    "300405074480501": {"lat": 30.0681, "lon": 74.8014},
    "313640075582001": {"lat": 31.6111, "lon": 75.9722},
    "300215074204501": {"lat": 30.0375, "lon": 74.3458},
    "303605074232001": {"lat": 30.6014, "lon": 74.3889},
    "302430076471501": {"lat": 30.4083, "lon": 76.7875},
    "300645075003001": {"lat": 30.1125, "lon": 75.0083},
    "314955075450501": {"lat": 31.8319, "lon": 75.7514},
    "304422074514401": {"lat": 30.7394, "lon": 74.8622},
}


def main():
    print("Loading CSV...")
    df = pd.read_csv(CSV_FILE, dtype={"station_id": str})
    df['datetime'] = pd.to_datetime(df['datetime'])

    # Filter to only MVP stations
    df_mvp = df[df['station_id'].isin(MVP_STATIONS.keys())]
    print(f"Rows matching MVP stations: {len(df_mvp)}")

    if df_mvp.empty:
        print("ERROR: No rows found for MVP station IDs. Check station IDs match the CSV.")
        return

    store = DataStore()

    # Save station records first
    for station_id, meta in MVP_STATIONS.items():
        station = Station(
            station_id=station_id,
            name=f"Station {station_id}",
            latitude=meta["lat"],
            longitude=meta["lon"],
            state="Punjab",
            description=(
                "Punjab bbox station, top-15 by reading count; passed "
                "datum-sign and slope-magnitude filters (see module docstring)"
            ),
        )
        store.save_station(station)
        print(f"Saved station: {station_id}")

    # Save readings per station
    total_readings = 0
    for station_id in MVP_STATIONS.keys():
        station_df = df_mvp[df_mvp['station_id'] == station_id].sort_values('datetime')

        if station_df.empty:
            print(f"WARNING: No data found for {station_id}")
            continue

        readings = [
            Reading(
                station_id=station_id,
                timestamp=row['datetime'].to_pydatetime(),
                water_level_m=float(row['target']),
                quality_flag="GOOD",
                source="CSV"
            )
            for _, row in station_df.iterrows()
        ]

        store.save_readings(readings)
        total_readings += len(readings)
        print(f"Saved {len(readings)} readings for {station_id}")

    print("\nDataset ingestion complete.")
    print(f"Loaded {len(MVP_STATIONS)} stations, {total_readings} readings from CSV.")


if __name__ == "__main__":
    main()
