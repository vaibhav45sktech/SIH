import pandas as pd
from data_store import DataStore
from models.reading import Reading

CSV_FILE = "groundwater_data.csv"

STATION_ID = "KAGGLE_STATION_1"

def main():

    df = pd.read_csv(CSV_FILE)

    store = DataStore()

    readings = []

    for _, row in df.iterrows():

        reading = Reading(
            station_id=STATION_ID,
            timestamp=row["datetime"],
            water_level_m=float(row["target"])
        )

        readings.append(reading)

    store.save_readings(readings)

    print("Dataset ingestion complete.")

if __name__ == "__main__":
    main()