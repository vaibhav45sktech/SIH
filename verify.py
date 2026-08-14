"""
verify.py - READ-ONLY diagnostic script for the groundwater dataset.

Prints dataset shape/sampling diagnostics, DB inventory, and an independent
re-implementation of the trend classifier so its output can be compared
against processing/trend_engine.py.

This script never writes: the CSV is opened read-only by pandas and the
SQLite connection uses a file: URI with mode=ro so any accidental write
raises instead of mutating groundwater_data.db.
"""

import os
import sqlite3
from datetime import timedelta

import numpy as np
import pandas as pd

CSV_FILE = "groundwater_data.csv"
DB_FILE = "groundwater_data.db"

# Classification thresholds, mirrored from processing/trend_engine.py
NOISE_FLOOR = 0.0005  # m/day
OUTLIER_ABS = 100.0   # |target| >= this is treated as a sensor error
DAYS_PER_YEAR = 365.25

MVP_STATIONS = ["272315075030001", "300215074204501", "242112081493301"]

# Punjab bounding box
PB_LAT = (29.5, 32.6)
PB_LON = (73.8, 76.95)

SAMPLE_STATIONS = 300
SAMPLE_SEED = 42


def header(n, title):
    print()
    print("=" * 78)
    print(f"[{n}] {title}")
    print("=" * 78)


def load_csv():
    df = pd.read_csv(
        CSV_FILE,
        usecols=["station_id", "datetime", "target", "latitude", "longitude"],
        dtype={"station_id": str},
    )
    df["datetime"] = pd.to_datetime(df["datetime"])
    return df


# ---------------------------------------------------------------- section 1
def section_1(df):
    header(1, "CSV SHAPE / STATIONS / DATE RANGE / SAMPLE ROWS")
    print(f"File                : {CSV_FILE}")
    print(f"Shape (rows, cols)  : {df.shape[0]:,} rows x {df.shape[1]} cols (subset read)")
    print(f"Unique station_id   : {df['station_id'].nunique():,}")
    print(f"Date range          : {df['datetime'].min().date()}  ->  {df['datetime'].max().date()}")
    print(f"Span                : {(df['datetime'].max() - df['datetime'].min()).days:,} days")

    target = MVP_STATIONS[0]
    sub = df[df["station_id"] == target].sort_values("datetime")
    print(f"\n8 sample rows for station {target}  (total rows for it: {len(sub)}):")
    if sub.empty:
        print("  NO ROWS FOUND")
    else:
        print(sub.head(8).to_string(index=False))


# ---------------------------------------------------------------- section 2
def section_2(df):
    header(2, "SAMPLING CADENCE: MEDIAN GAP BETWEEN READINGS")
    ids = pd.Series(df["station_id"].unique())
    sampled = ids.sample(n=min(SAMPLE_STATIONS, len(ids)), random_state=SAMPLE_SEED)
    print(f"Sampled {len(sampled)} of {len(ids):,} stations (seed={SAMPLE_SEED})")

    pooled_gaps = []
    per_station_median = []
    sub = df[df["station_id"].isin(set(sampled))]
    for _, grp in sub.groupby("station_id", sort=False):
        dates = grp["datetime"].sort_values()
        if len(dates) < 2:
            continue
        gaps = dates.diff().dropna().dt.days.to_numpy()
        pooled_gaps.extend(gaps.tolist())
        per_station_median.append(float(np.median(gaps)))

    pooled_gaps = np.asarray(pooled_gaps, dtype=float)
    print(f"\nPooled consecutive-reading gaps across sample : n={len(pooled_gaps):,}")
    print(f"  MEDIAN GAP (days)          : {np.median(pooled_gaps):.1f}")
    print(f"  mean / min / max (days)    : {pooled_gaps.mean():.1f} / "
          f"{pooled_gaps.min():.0f} / {pooled_gaps.max():.0f}")
    for q in (25, 75, 90):
        print(f"  p{q} gap (days)             : {np.percentile(pooled_gaps, q):.1f}")

    psm = np.asarray(per_station_median, dtype=float)
    print(f"\nMedian of per-station median gaps            : {np.median(psm):.1f} days")

    counts_all = df.groupby("station_id").size()
    print(f"\nReadings per station (ALL {len(counts_all):,} stations):")
    print(f"  MEDIAN READINGS PER STATION: {counts_all.median():.1f}")
    print(f"  mean / min / max           : {counts_all.mean():.1f} / "
          f"{counts_all.min()} / {counts_all.max()}")


# ---------------------------------------------------------------- section 3
def section_3(df):
    header(3, "OUTLIERS IN target")
    n_out = int((df["target"].abs() > 100).sum())
    print(f"Rows with abs(target) > 100 : {n_out:,}  ({100.0 * n_out / len(df):.3f}% of {len(df):,})")
    print(f"target min                  : {df['target'].min():,.3f}")
    print(f"target max                  : {df['target'].max():,.3f}")
    print(f"target median               : {df['target'].median():,.3f}")
    n_ge = int((df["target"].abs() >= OUTLIER_ABS).sum())
    print(f"\nRows with abs(target) >= {OUTLIER_ABS:.0f} (guard threshold): {n_ge:,}")
    print(f"Stations touched by those rows                : "
          f"{df.loc[df['target'].abs() >= OUTLIER_ABS, 'station_id'].nunique():,}")


# ---------------------------------------------------------------- section 4
def section_4():
    header(4, "SQLITE INVENTORY (read-only)")
    if not os.path.exists(DB_FILE):
        print(f"{DB_FILE} does not exist yet.")
        return
    print(f"File: {DB_FILE}  ({os.path.getsize(DB_FILE):,} bytes)")
    uri = f"file:{DB_FILE}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        tables = [r[0] for r in cur.fetchall()]
        if not tables:
            print("No tables found.")
            return
        print(f"\n{'table':<20} {'rows':>10}")
        print("-" * 31)
        for t in tables:
            cur.execute(f'SELECT COUNT(*) FROM "{t}"')
            print(f"{t:<20} {cur.fetchone()[0]:>10,}")

        if "stations" in tables:
            cur.execute("SELECT station_id, name FROM stations ORDER BY station_id")
            rows = cur.fetchall()
            print(f"\nstations table ({len(rows)} rows):")
            for sid, name in rows:
                print(f"  {sid}  {name}")

        if "readings" in tables:
            cur.execute(
                "SELECT station_id, COUNT(*), MIN(timestamp), MAX(timestamp) "
                "FROM readings GROUP BY station_id ORDER BY station_id"
            )
            print(f"\nreadings per station:")
            print(f"  {'station_id':<18} {'n':>5}  {'first':<12} {'last':<12}")
            for sid, n, lo, hi in cur.fetchall():
                print(f"  {sid:<18} {n:>5}  {str(lo)[:10]:<12} {str(hi)[:10]:<12}")
    finally:
        conn.close()


# ---------------------------------------------------------------- section 5
def hand_trend(df, station_id, window_days):
    """
    Independent re-implementation of the trend classifier.

    1. filter to the station
    2. drop rows where abs(target) >= 100 (sensor-error guard)
    3. keep readings within `window_days` of that station's latest remaining date
    4. slope = np.polyfit(day_offsets, target, 1)[0]   -> m/day
    5. classify against the NOISE_FLOOR dead-zone

    Returns dict with slope/status/n, or {'n': <n>, 'status': None} when n < 2.
    """
    sub = df[df["station_id"] == station_id]
    n_raw = len(sub)

    clean = sub[sub["target"].abs() < OUTLIER_ABS]
    n_dropped = n_raw - len(clean)
    if clean.empty:
        return {"n": 0, "n_raw": n_raw, "n_dropped": n_dropped, "status": None}

    latest = clean["datetime"].max()
    cutoff = latest - timedelta(days=window_days)
    win = clean[clean["datetime"] >= cutoff].sort_values("datetime")
    n = len(win)

    if n < 2:
        return {"n": n, "n_raw": n_raw, "n_dropped": n_dropped,
                "latest": latest, "status": None}

    first = win["datetime"].iloc[0]
    x = (win["datetime"] - first).dt.days.to_numpy(dtype=float)
    y = win["target"].to_numpy(dtype=float)

    if np.all(y == y[0]):
        slope = 0.0
    else:
        slope = float(np.polyfit(x, y, 1)[0])

    if abs(slope) < NOISE_FLOOR:
        status = "STABLE"
    elif slope < 0:
        status = "RECHARGING"
    else:
        status = "DEPLETING"

    return {
        "n": n, "n_raw": n_raw, "n_dropped": n_dropped,
        "latest": latest, "first": first, "span_days": float(x[-1]),
        "slope": slope, "slope_yr": slope * DAYS_PER_YEAR, "status": status,
    }


def fmt_trend(r):
    if r["status"] is None:
        return f"NO RESULT              (n={r['n']})"
    return (f"{r['status']:<11} slope={r['slope']:+.6f} m/day  "
            f"({r['slope_yr']:+.4f} m/yr)  (n={r['n']})")


def section_5(df):
    header(5, "HAND-WRITTEN TREND FUNCTION @ window_days = 90 vs 3650")
    print("Rule: |slope| < 0.0005 m/day -> STABLE ; slope < 0 -> RECHARGING ; "
          "else DEPLETING")
    print(f"Outlier guard: rows with abs(target) >= {OUTLIER_ABS:.0f} dropped "
          "BEFORE windowing")
    print("Window anchored on each station's own latest reading date "
          "(not today's date).")

    for sid in MVP_STATIONS:
        print(f"\n--- station {sid} ---")
        for wd in (90, 3650):
            r = hand_trend(df, sid, wd)
            print(f"  window={wd:>5}d : {fmt_trend(r)}")
        r = hand_trend(df, sid, 3650)
        print(f"  rows in CSV={r['n_raw']}, dropped as outliers={r['n_dropped']}, "
              f"latest reading={str(r.get('latest'))[:10]}")

    print("\nSummary table:")
    print(f"  {'station':<18} {'w=90':<26} {'w=3650':<26}")
    for sid in MVP_STATIONS:
        a = hand_trend(df, sid, 90)
        b = hand_trend(df, sid, 3650)
        sa = "NO RESULT" if a["status"] is None else a["status"]
        sb = "NO RESULT" if b["status"] is None else b["status"]
        cell_a = "{} (n={})".format(sa, a["n"])
        cell_b = "{} (n={})".format(sb, b["n"])
        print(f"  {sid:<18} {cell_a:<26} {cell_b:<26}")


# ---------------------------------------------------------------- section 6
def section_6(df):
    header(6, "PUNJAB BOUNDING BOX STATIONS")
    print(f"Bounding box: lat {PB_LAT[0]}-{PB_LAT[1]}, lon {PB_LON[0]}-{PB_LON[1]}")

    meta = df.groupby("station_id").agg(
        n=("target", "size"),
        lat=("latitude", "first"),
        lon=("longitude", "first"),
    )
    pb = meta[
        meta["lat"].between(*PB_LAT) & meta["lon"].between(*PB_LON)
    ]
    print(f"Stations inside bbox          : {len(pb):,}")

    pb30 = pb[pb["n"] >= 30].sort_values("n", ascending=False)
    print(f"Stations inside bbox with 30+ readings : {len(pb30):,}")

    if pb30.empty:
        print("None to report.")
        return pb30

    print(f"\nTop 5 by reading count, with 3650-day trend:")
    print(f"  {'station_id':<18} {'n':>5} {'lat':>8} {'lon':>8}  "
          f"{'status':<11} {'m/yr':>10} {'pts':>5}")
    print("  " + "-" * 72)
    for sid, row in pb30.head(5).iterrows():
        r = hand_trend(df, sid, 3650)
        if r["status"] is None:
            print(f"  {sid:<18} {int(row['n']):>5} {row['lat']:>8.4f} "
                  f"{row['lon']:>8.4f}  NO RESULT (n={r['n']})")
        else:
            print(f"  {sid:<18} {int(row['n']):>5} {row['lat']:>8.4f} "
                  f"{row['lon']:>8.4f}  {r['status']:<11} "
                  f"{r['slope_yr']:>+10.4f} {r['n']:>5}")

    # Distribution across ALL qualifying Punjab stations
    tally = {"DEPLETING": 0, "RECHARGING": 0, "STABLE": 0, "NO RESULT": 0}
    for sid in pb30.index:
        r = hand_trend(df, sid, 3650)
        tally["NO RESULT" if r["status"] is None else r["status"]] += 1
    total = sum(tally.values())
    print(f"\n3650-day trend distribution across all {total} qualifying "
          f"Punjab stations:")
    for k, v in tally.items():
        pct = 100.0 * v / total if total else 0.0
        print(f"  {k:<11} {v:>4}  ({pct:5.1f}%)")
    return pb30


# ---------------------------------------------------------------- section 7
SLOPE_ABS_MAX_M_PER_YR = 3.0


def section_7(df):
    """Reproduce the station-selection filter chain used by load_dataset.py."""
    header(7, "STATION SELECTION FILTER CHAIN (audit trail)")

    meta = df.groupby("station_id").agg(
        n=("target", "size"), lat=("latitude", "first"), lon=("longitude", "first"),
    )
    pool = meta[
        meta["lat"].between(*PB_LAT) & meta["lon"].between(*PB_LON) & (meta["n"] >= 30)
    ].sort_values("n", ascending=False)
    print(f"Starting pool (Punjab bbox, >=30 readings) : {len(pool)}")

    # --- Step A: partition by target sign, on outlier-free rows
    clean = df[df["target"].abs() < OUTLIER_ABS]
    rng = clean[clean["station_id"].isin(pool.index)].groupby("station_id")["target"].agg(["min", "max"])

    def convention(sid):
        if sid not in rng.index:
            return "no-clean-data"
        lo, hi = rng.loc[sid, "min"], rng.loc[sid, "max"]
        if hi <= 0:
            return "all-negative"
        if lo >= 0:
            return "all-positive"
        return "mixed"

    pool = pool.assign(conv=[convention(s) for s in pool.index])
    print("\n[A] sign partition:")
    for k, v in pool["conv"].value_counts().items():
        print(f"      {k:<15} {v:>4}")

    # --- Step B: keep only all-positive (unambiguously depth-below-ground)
    kept = pool[pool["conv"] == "all-positive"]
    print(f"\n[B] datum-ambiguity exclusion:")
    print(f"      kept (all-positive)    : {len(kept)}")
    print(f"      EXCLUDED (neg + mixed) : {len(pool) - len(kept)}"
          f"   <- reason: target may be depth-below-ground or")
    print(f"                                     elevation-above-datum; sign of")
    print(f"                                     slope, hence label, is unresolvable")

    # --- Step C: slope-magnitude sanity filter
    recs = []
    for sid, row in kept.iterrows():
        r = hand_trend(df, sid, 3650)
        recs.append({
            "sid": sid, "n": int(row["n"]), "lat": row["lat"], "lon": row["lon"],
            "status": r["status"], "m_yr": r["slope_yr"] if r["status"] else np.nan,
            "span": r.get("span_days", np.nan),
        })
    k = pd.DataFrame(recs).set_index("sid")

    too_steep = k[k["m_yr"].abs() > SLOPE_ABS_MAX_M_PER_YR].sort_values("m_yr")
    no_fit = k[k["status"].isna()]
    print(f"\n[C] slope-magnitude filter (|slope| <= {SLOPE_ABS_MAX_M_PER_YR} m/yr;"
          f" documented Punjab range 0.2-1.5):")
    print(f"      EXCLUDED {len(too_steep)} for implausible rate:")
    for sid, r in too_steep.iterrows():
        print(f"        {sid:<16} n={int(r['n']):<4} {r['status']:<11} "
              f"{r['m_yr']:>+10.3f} m/yr")
    if len(no_fit):
        print(f"      EXCLUDED {len(no_fit)} for insufficient points: "
              f"{list(no_fit.index)}")

    surv = k[(k["m_yr"].abs() <= SLOPE_ABS_MAX_M_PER_YR) & k["status"].notna()]
    surv = surv.sort_values("n", ascending=False)
    print(f"\n      SURVIVORS: {len(surv)}")
    for kk, v in surv["status"].value_counts().items():
        print(f"        {kk:<12} {v:>4}  ({100.0 * v / len(surv):5.1f}%)")

    # --- record-span caveat: reading count is anti-correlated with span
    print(f"\n[!] record-span caveat (reading count favours SHORT records):")
    long_span = surv[surv["span"] >= 400]
    short_span = surv[surv["span"] < 400]
    print(f"      span >= 400d : {len(long_span):>4} stations, "
          f"median |slope| {long_span['m_yr'].abs().median():.3f} m/yr")
    print(f"      span <  400d : {len(short_span):>4} stations, "
          f"median |slope| {short_span['m_yr'].abs().median():.3f} m/yr"
          f"   <- sub-year records; annualised seasonal swing, not trend")

    # --- direction test on the trustworthy long-span subset
    print(f"\n[!] falsification test - slope SIGN on the {len(long_span)} "
          f"long-span survivors:")
    pos = int((long_span["m_yr"] > 0).sum())
    neg = int((long_span["m_yr"] < 0).sum())
    print(f"      positive slope (water falling) : {pos:>4} "
          f"({100.0 * pos / len(long_span):.1f}%)")
    print(f"      negative slope (water rising)  : {neg:>4} "
          f"({100.0 * neg / len(long_span):.1f}%)")
    print(f"      median slope: {long_span['m_yr'].median():+.3f} m/yr")
    print(f"      NOISE_FLOOR is {NOISE_FLOOR * DAYS_PER_YEAR:.3f} m/yr, which "
          f"EXCEEDS the median real trend,")
    print(f"      so genuine slow depletion is absorbed into the STABLE label.")

    # --- Step E: final selection
    top = surv.head(15)
    print(f"\n[E] FINAL SELECTION - top 15 survivors by reading count:")
    print(f"      {'station_id':<16} {'n':>5} {'span':>6} {'status':<11} {'m/yr':>9}")
    print("      " + "-" * 52)
    for sid, r in top.iterrows():
        print(f"      {sid:<16} {int(r['n']):>5} {int(r['span']):>6} "
              f"{r['status']:<11} {r['m_yr']:>+9.3f}")
    print(f"\n      selection split:")
    for kk, v in top["status"].value_counts().items():
        print(f"        {kk:<12} {v:>4}")
    print(f"      median slope {top['m_yr'].median():+.3f} m/yr, "
          f"sign: {int((top['m_yr'] > 0).sum())} pos / "
          f"{int((top['m_yr'] < 0).sum())} neg")
    return top


def main():
    pd.set_option("display.width", 200)
    print("READ-ONLY DIAGNOSTIC - verify.py")
    print("No writes are performed to the CSV or the database.")
    df = load_csv()
    section_1(df)
    section_2(df)
    section_3(df)
    section_4()
    section_5(df)
    section_6(df)
    section_7(df)
    print("\n" + "=" * 78)
    print("END OF DIAGNOSTIC")
    print("=" * 78)


if __name__ == "__main__":
    main()
