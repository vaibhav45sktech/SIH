import pandas as pd
import numpy as np

# Load the CSV
df = pd.read_csv('groundwater_data.csv')

# Convert datetime to actual datetime object
df['datetime'] = pd.to_datetime(df['datetime'])

# 1. Basic info
print("=" * 60)
print("DATASET OVERVIEW")
print("=" * 60)
print(f"Total rows: {len(df)}")
print(f"Date range: {df['datetime'].min()} to {df['datetime'].max()}")

# 2. Stations analysis
print("\n" + "=" * 60)
print("STATION ANALYSIS")
print("=" * 60)
stations = df['station_id'].unique()
print(f"Total unique stations: {len(stations)}")

# 3. Data completeness per station - TOP STATIONS
print("\n" + "=" * 60)
print("TOP 15 STATIONS BY DATA COMPLETENESS")
print("=" * 60)
top_15 = df.groupby('station_id').agg({
    'datetime': ['min', 'max', 'count'],
    'target': ['mean', 'std', 'min', 'max']
}).round(2)

top_15.columns = ['date_min', 'date_max', 'num_readings', 'target_mean', 'target_std', 'target_min', 'target_max']
top_15['years_span'] = (pd.to_datetime(top_15['date_max']) - pd.to_datetime(top_15['date_min'])).dt.days / 365.25
top_15 = top_15.sort_values('num_readings', ascending=False).head(15)

print(top_15)

# 4. Time series frequency check (FIXED)
print("\n" + "=" * 60)
print("TIME SERIES FREQUENCY CHECK")
print("=" * 60)
sample_station = top_15.index[0]
sample_data = df[df['station_id'] == sample_station].sort_values('datetime')
print(f"Station: {sample_station}")
print(f"Date range: {sample_data['datetime'].min()} to {sample_data['datetime'].max()}")
print(f"Total readings: {len(sample_data)}")
date_diffs = sample_data['datetime'].diff().dt.days.dropna()
print(f"Days between readings - Mean: {date_diffs.mean():.1f}, Std: {date_diffs.std():.1f}, Min: {date_diffs.min()}, Max: {date_diffs.max()}")

# 5. Stations with longest historical data (1994 onwards)
print("\n" + "=" * 60)
print("STATIONS WITH LONGEST HISTORICAL DATA (From 1994+)")
print("=" * 60)
early_stations = df[df['datetime'].dt.year <= 1995].groupby('station_id').agg({
    'datetime': ['min', 'count'],
    'target': ['mean', 'std']
}).round(2)

early_stations.columns = ['earliest_date', 'num_readings', 'target_mean', 'target_std']
early_stations = early_stations.sort_values('num_readings', ascending=False).head(15)
print(early_stations)

# 6. Data quality issues
print("\n" + "=" * 60)
print("DATA QUALITY CHECKS")
print("=" * 60)
print(f"Negative water levels: {(df['target'] < 0).sum()} records")
print(f"Very high water levels (>50m): {(df['target'] > 50).sum()} records")
print(f"Water level range: {df['target'].min():.2f}m to {df['target'].max():.2f}m")

# 7. Distribution of readings per station
print("\n" + "=" * 60)
print("READING DISTRIBUTION STATS")
print("=" * 60)
readings_per_station = df.groupby('station_id').size()
print(readings_per_station.describe())
print(f"\nStations with <20 readings: {(readings_per_station < 20).sum()}")
print(f"Stations with 20-50 readings: {((readings_per_station >= 20) & (readings_per_station < 50)).sum()}")
print(f"Stations with 50-100 readings: {((readings_per_station >= 50) & (readings_per_station < 100)).sum()}")
print(f"Stations with 100-200 readings: {((readings_per_station >= 100) & (readings_per_station < 200)).sum()}")
print(f"Stations with >200 readings: {(readings_per_station >= 200).sum()}")

# 8. RECOMMENDED STATIONS FOR MVP
print("\n" + "=" * 60)
print("RECOMMENDED STATIONS FOR MVP (>100 readings, from 1994+)")
print("=" * 60)
mvp_candidates = df.groupby('station_id').agg({
    'datetime': ['min', 'max', 'count'],
    'target': ['mean', 'std']
}).round(2)

mvp_candidates.columns = ['date_min', 'date_max', 'num_readings', 'target_mean', 'target_std']
mvp_candidates = mvp_candidates[
    (mvp_candidates['num_readings'] >= 100) & 
    (pd.to_datetime(mvp_candidates['date_min']).dt.year <= 1995)
].sort_values('num_readings', ascending=False).head(10)

print(mvp_candidates)
print(f"\nTotal candidates: {len(mvp_candidates)}")

# 9. TREND CLASSIFICATION FOR MVP CANDIDATES
from scipy import stats

print("\n" + "=" * 60)
print("TREND CLASSIFICATION FOR MVP CANDIDATES")
print("=" * 60)

mvp_station_ids = mvp_candidates.index.tolist()
mvp_data = df[df['station_id'].isin(mvp_station_ids)]

results = []
for station_id, group in mvp_data.groupby('station_id'):
    group = group.sort_values('datetime')
    x = (group['datetime'] - group['datetime'].min()).dt.days.values
    y = group['target'].values

    slope, intercept, r, p, _ = stats.linregress(x, y)

    if p > 0.05:
        trend = "STABLE"
    elif slope > 0.0005:
        trend = "DEPLETING"
    else:
        trend = "RECHARGING"

    results.append({
        'station_id': station_id,
        'slope_per_day': round(slope, 6),
        'r_squared': round(r**2, 3),
        'p_value': round(p, 4),
        'trend': trend,
        'num_readings': len(group),
        'years_span': round((group['datetime'].max() - group['datetime'].min()).days / 365.25, 1)
    })

results_df = pd.DataFrame(results).sort_values('trend')
print(results_df.to_string(index=False))

print("\nSUMMARY:")
for trend_type in ['DEPLETING', 'RECHARGING', 'STABLE']:
    matches = results_df[results_df['trend'] == trend_type]['station_id'].tolist()
    print(f"  {trend_type}: {matches if matches else 'None found'}")
    
# 10. FIND DEPLETING STATION FROM BROADER POOL
print("\n" + "=" * 60)
print("SEARCHING FOR DEPLETING STATIONS (>200 readings)")
print("=" * 60)

high_reading_stations = readings_per_station[readings_per_station >= 200].index.tolist()
broader_data = df[df['station_id'].isin(high_reading_stations)]

depleting_results = []
for station_id, group in broader_data.groupby('station_id'):
    group = group.sort_values('datetime')
    x = (group['datetime'] - group['datetime'].min()).dt.days.values
    y = group['target'].values

    slope, intercept, r, p, _ = stats.linregress(x, y)

    if p <= 0.05 and slope > 0.0005:
        depleting_results.append({
            'station_id': station_id,
            'slope_per_day': round(slope, 6),
            'r_squared': round(r**2, 3),
            'p_value': round(p, 4),
            'num_readings': len(group),
            'years_span': round((group['datetime'].max() - group['datetime'].min()).days / 365.25, 1),
            'date_min': group['datetime'].min().date(),
            'date_max': group['datetime'].max().date()
        })

depleting_df = pd.DataFrame(depleting_results).sort_values('r_squared', ascending=False)
print(f"Total depleting candidates found: {len(depleting_df)}")
print(depleting_df.head(10).to_string(index=False))

# # 12. REVISED MVP SELECTION (3-5 years, recent data)
# print("\n" + "=" * 60)
# print("REVISED MVP CANDIDATES (2020+, 3-5 years, 100+ readings)")
# print("=" * 60)

# recent_candidates = df[df['datetime'].dt.year >= 2020].groupby('station_id').agg({
#     'datetime': ['min', 'max', 'count'],
#     'target': ['mean', 'std']
# }).round(2)

# recent_candidates.columns = ['date_min', 'date_max', 'num_readings', 'target_mean', 'target_std']
# recent_candidates['years_span'] = (
#     pd.to_datetime(recent_candidates['date_max']) - pd.to_datetime(recent_candidates['date_min'])
# ).dt.days / 365.25

# recent_candidates = recent_candidates[
#     (recent_candidates['num_readings'] >= 100) &
#     (recent_candidates['years_span'] >= 3) &
#     (recent_candidates['years_span'] <= 5)
# ].sort_values('num_readings', ascending=False)

# print(f"Total candidates: {len(recent_candidates)}")

# # DEBUG: Check what the filter is actually producing
# print(f"\nTotal after num_readings >= 100 filter: {len(df[df['datetime'].dt.year >= 2020].groupby('station_id').filter(lambda x: len(x) >= 100).groupby('station_id').ngroups)}")
# print(f"\nYears span distribution of 100+ reading stations:")
# print(recent_candidates['years_span'].describe().round(2))
# print(f"\nTotal after all filters: {len(recent_candidates)}")
# print(recent_candidates.head(10).to_string())

# # Run trend classification on all of them
# recent_ids = recent_candidates.index.tolist()
# recent_data = df[df['station_id'].isin(recent_ids)]

# recent_results = []
# for station_id, group in recent_data.groupby('station_id'):
#     group = group.sort_values('datetime')
#     x = (group['datetime'] - group['datetime'].min()).dt.days.values
#     y = group['target'].values

#     slope, intercept, r, p, _ = stats.linregress(x, y)

#     if p > 0.05:
#         trend = "STABLE"
#     elif slope > 0.0005:
#         trend = "DEPLETING"
#     else:
#         trend = "RECHARGING"

#     recent_results.append({
#         'station_id': station_id,
#         'trend': trend,
#         'slope_per_day': round(slope, 6),
#         'r_squared': round(r**2, 3),
#         'p_value': round(p, 4),
#         'num_readings': len(group),
#         'years_span': round((group['datetime'].max() - group['datetime'].min()).days / 365.25, 1),
#         'date_min': group['datetime'].min().date(),
#         'date_max': group['datetime'].max().date()
#     })

# recent_results_df = pd.DataFrame(recent_results)

# # Pick best per trend type: highest R² within each category
# print("\nBEST CANDIDATE PER TREND TYPE (highest R²):")
# for trend_type in ['DEPLETING', 'RECHARGING', 'STABLE']:
#     subset = recent_results_df[recent_results_df['trend'] == trend_type].sort_values('r_squared', ascending=False)
#     print(f"\n--- {trend_type} (top 3) ---")
#     print(subset.head(3).to_string(index=False))

# 12. REVISED MVP SELECTION - using full dataset, best R² per trend type
print("\n" + "=" * 60)
print("REVISED MVP CANDIDATES (full dataset, 100+ readings)")
print("=" * 60)

all_candidates = df.groupby('station_id').agg({
    'datetime': ['min', 'max', 'count'],
    'target': ['mean', 'std']
}).round(2)

all_candidates.columns = ['date_min', 'date_max', 'num_readings', 'target_mean', 'target_std']
all_candidates['years_span'] = (
    pd.to_datetime(all_candidates['date_max']) - pd.to_datetime(all_candidates['date_min'])
).dt.days / 365.25

all_candidates = all_candidates[all_candidates['num_readings'] >= 100]
print(f"Total candidates with 100+ readings: {len(all_candidates)}")

# Run trend classification
all_ids = all_candidates.index.tolist()
all_trend_results = []

for station_id, group in df[df['station_id'].isin(all_ids)].groupby('station_id'):
    group = group.sort_values('datetime')
    x = (group['datetime'] - group['datetime'].min()).dt.days.values
    y = group['target'].values

    slope, intercept, r, p, _ = stats.linregress(x, y)

    if p > 0.05:
        trend = "STABLE"
    elif slope > 0.0005:
        trend = "DEPLETING"
    else:
        trend = "RECHARGING"

    all_trend_results.append({
        'station_id': station_id,
        'trend': trend,
        'slope_per_day': round(slope, 6),
        'r_squared': round(r**2, 3),
        'p_value': round(p, 4),
        'num_readings': int(len(group)),
        'years_span': round((group['datetime'].max() - group['datetime'].min()).days / 365.25, 1),
        'date_min': str(group['datetime'].min().date()),
        'date_max': str(group['datetime'].max().date())
    })

all_trend_df = pd.DataFrame(all_trend_results)

print("\nBEST CANDIDATE PER TREND TYPE (highest R²):")
for trend_type in ['DEPLETING', 'RECHARGING', 'STABLE']:
    subset = all_trend_df[all_trend_df['trend'] == trend_type].sort_values('r_squared', ascending=False)
    print(f"\n--- {trend_type} (top 3) ---")
    if len(subset) == 0:
        print("  None found")
    else:
        print(subset.head(3).to_string(index=False))
        
# At the end of section 12, add:
print("\nFILTERED MVP CANDIDATES (2+ years span, highest R²):")
filtered_trend_df = all_trend_df[all_trend_df['years_span'] >= 2]

for trend_type in ['DEPLETING', 'RECHARGING', 'STABLE']:
    subset = filtered_trend_df[filtered_trend_df['trend'] == trend_type].sort_values('r_squared', ascending=False)
    print(f"\n--- {trend_type} (top 3, 2+ years) ---")
    if len(subset) == 0:
        print("  None found")
    else:
        print(subset.head(3).to_string(index=False))