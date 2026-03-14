import pandas as pd
from data_store import DataStore
from models.reading import Reading
from models.station import Station

CSV_FILE = "groundwater_data.csv"

# The 3 MVP stations selected from analysis
MVP_STATIONS = {
    "272315075030001": {"name": "Station 272315075030001", "trend": "DEPLETING"},
    "300215074204501": {"name": "Station 300215074204501", "trend": "RECHARGING"},
    "242112081493301": {"name": "Station 242112081493301", "trend": "STABLE"},
}

def main():
    print("Loading CSV...")
    df = pd.read_csv(CSV_FILE)
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
            name=meta["name"],
            description=f"MVP station - {meta['trend']} trend"
        )
        store.save_station(station)
        print(f"Saved station: {station_id} ({meta['trend']})")

    # Save readings per station
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
        print(f"Saved {len(readings)} readings for {station_id}")

    print("\nDataset ingestion complete.")
    print(f"Loaded {len(MVP_STATIONS)} stations from CSV.")

if __name__ == "__main__":
    main()