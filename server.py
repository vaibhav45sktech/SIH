"""
FastAPI REST server for Groundwater Recharge Insight Dashboard.
Provides high-performance backend API and serves the modern frontend.
"""

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta
import os
import numpy as np
from scipy import stats as scipy_stats

from config import config
from data_store import DataStore
from processing_engine import ProcessingEngine
from insights import InsightInterpreter
from models.station import Station

app = FastAPI(
    title="Groundwater Recharge Insight API",
    description="Backend API for DWLR - NWDP Data Analysis & Trends",
    version="2.0.0"
)

# Enable CORS for local testing
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Core Services
data_store = DataStore()
processing_engine = ProcessingEngine()
insight_interpreter = InsightInterpreter()


def get_trend_rate_m_per_year(metrics):
    """Helper to safely extract or calculate trend rate in meters/year."""
    if not metrics:
        return 0.0
    if metrics.trend_metrics and metrics.trend_metrics.slope is not None:
        return float(metrics.trend_metrics.slope * 365.0)
    if metrics.trend_magnitude is not None and metrics.trend_period_days:
        return float((metrics.trend_magnitude / metrics.trend_period_days) * 365.0)
    return 0.0


@app.get("/api/overview")
def get_overview():
    """Get high-level summary metrics across all monitoring stations."""
    stations = data_store.get_all_stations()
    
    if not stations:
        try:
            with data_store._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT DISTINCT station_id FROM readings")
                rows = cursor.fetchall()
                stations = [Station(station_id=r["station_id"], name=f"Station {r['station_id']}") for r in rows]
        except Exception:
            stations = []
            
    total_stations = len(stations)
    total_readings = 0
    risk_counts = {"Low Risk": 0, "Moderate Risk": 0, "High Risk": 0, "Critical Risk": 0, "Unknown": 0}
    trend_counts = {"Depleting": 0, "Recharging": 0, "Stable": 0, "Insufficient Data": 0}
    slopes = []
    
    for s in stations:
        readings = data_store.get_readings(s.station_id)
        if readings:
            total_readings += len(readings)
            
        metrics = data_store.get_latest_metrics(s.station_id)
        if not metrics and readings:
            ref_date = data_store.get_max_reading_date(s.station_id)
            if ref_date:
                calc_dt = datetime.combine(ref_date, datetime.min.time())
                metrics = processing_engine.calculate_metrics(readings, calculation_date=calc_dt)
                data_store.save_metrics(metrics)
                
        if metrics:
            if metrics.risk_level:
                lvl = metrics.risk_level.value
                risk_counts[lvl] = risk_counts.get(lvl, 0) + 1
            if metrics.trend_indicator:
                tr = metrics.trend_indicator.value
                trend_counts[tr] = trend_counts.get(tr, 0) + 1
            
            rate = get_trend_rate_m_per_year(metrics)
            slopes.append(rate)
                
    avg_slope = float(np.mean(slopes)) if slopes else 0.0
    
    return {
        "total_stations": total_stations,
        "total_readings": total_readings,
        "avg_trend_m_per_year": round(avg_slope, 3),
        "risk_distribution": risk_counts,
        "trend_distribution": trend_counts,
        "data_mode": config.data_mode,
        "db_path": config.db_path
    }


@app.get("/api/stations")
def list_stations():
    """Get list of all stations with latest metric highlights."""
    stations = data_store.get_all_stations()
    
    if not stations:
        with data_store._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT station_id FROM readings")
            rows = cursor.fetchall()
            stations = [Station(station_id=r["station_id"], name=f"Station {r['station_id']}") for r in rows]
            
    station_list = []
    for s in stations:
        readings = data_store.get_readings(s.station_id)
        metrics = data_store.get_latest_metrics(s.station_id)
        
        if not metrics and readings:
            ref_date = data_store.get_max_reading_date(s.station_id)
            if ref_date:
                calc_dt = datetime.combine(ref_date, datetime.min.time())
                metrics = processing_engine.calculate_metrics(readings, calculation_date=calc_dt)
                data_store.save_metrics(metrics)
                
        latest_reading = readings[-1] if readings else None
        rate = get_trend_rate_m_per_year(metrics)
        
        station_list.append({
            "station_id": s.station_id,
            "name": s.name,
            "latitude": s.latitude or 30.5,
            "longitude": s.longitude or 75.5,
            "district": s.district or "Punjab",
            "state": s.state or "Punjab",
            "reading_count": len(readings) if readings else 0,
            "latest_water_level_m": round(latest_reading.water_level_m, 2) if latest_reading else None,
            "latest_reading_date": latest_reading.timestamp.strftime("%Y-%m-%d") if latest_reading else None,
            "trend": metrics.trend_indicator.value if metrics and metrics.trend_indicator else "Unknown",
            "trend_rate_m_per_year": round(rate, 3),
            "risk_level": metrics.risk_level.value if metrics and metrics.risk_level else "Low Risk",
            "risk_index": round(metrics.risk_index, 1) if metrics and metrics.risk_index is not None else 0.0
        })
        
    return station_list


@app.get("/api/station/{station_id}")
def get_station_detail(station_id: str):
    """Get complete details, readings time-series, trend line, and insights for a station."""
    station = data_store.get_station(station_id)
    if not station:
        station = Station(station_id=station_id, name=f"Station {station_id}", latitude=30.5, longitude=75.5)
        
    readings = data_store.get_readings(station_id)
    if not readings:
        raise HTTPException(status_code=404, detail=f"No readings found for station {station_id}")
        
    metrics = data_store.get_latest_metrics(station_id)
    ref_date = data_store.get_max_reading_date(station_id)
    
    if not metrics and ref_date:
        calc_dt = datetime.combine(ref_date, datetime.min.time())
        metrics = processing_engine.calculate_metrics(readings, calculation_date=calc_dt)
        data_store.save_metrics(metrics)
        
    insight = insight_interpreter.generate_insight(metrics) if metrics else "No metrics available."
    rate = get_trend_rate_m_per_year(metrics)
    
    # Format readings time-series
    chart_readings = [
        {
            "date": r.timestamp.strftime("%Y-%m-%d"),
            "water_level_m": round(r.water_level_m, 2)
        }
        for r in readings
    ]
        
    # Calculate Linear Regression Trend line overlay
    dates_list = [r.timestamp for r in readings]
    first_date = dates_list[0]
    x_days = np.array([(d - first_date).days for d in dates_list])
    y_vals = np.array([r.water_level_m for r in readings])
    
    if len(x_days) > 1:
        slope_res = scipy_stats.linregress(x_days, y_vals)
        slope = slope_res.slope
        intercept = float(np.mean(y_vals) - slope * np.mean(x_days))
        trend_y = (intercept + slope * x_days).tolist()
    else:
        trend_y = y_vals.tolist()
        
    trend_line_points = [
        {"date": r.timestamp.strftime("%Y-%m-%d"), "trend_m": round(val, 2)}
        for r, val in zip(readings, trend_y)
    ]
    
    return {
        "station": {
            "station_id": station.station_id,
            "name": station.name,
            "latitude": station.latitude or 30.5,
            "longitude": station.longitude or 75.5,
            "district": station.district or "Punjab",
            "state": station.state or "Punjab",
            "description": station.description or "DWLR Monitoring Station"
        },
        "metrics": {
            "trend": metrics.trend_indicator.value if metrics and metrics.trend_indicator else "Unknown",
            "trend_magnitude": round(metrics.trend_magnitude, 2) if metrics and metrics.trend_magnitude is not None else 0.0,
            "trend_rate_m_per_year": round(rate, 3),
            "risk_index": round(metrics.risk_index, 1) if metrics and metrics.risk_index is not None else 0.0,
            "risk_level": metrics.risk_level.value if metrics and metrics.risk_level else "Low Risk",
            "seasonal_deviation": round(metrics.seasonal_deviation, 2) if metrics and metrics.seasonal_deviation is not None else 0.0,
            "confidence_score": 1.0,
            "data_span_days": metrics.trend_period_days if metrics and metrics.trend_period_days else len(readings)
        },
        "insight": insight,
        "readings": chart_readings,
        "trend_line": trend_line_points
    }


@app.post("/api/refresh")
def refresh_data():
    """Clear metric caches and refresh analysis."""
    try:
        stations = data_store.get_all_stations()
        recalculated = 0
        for s in stations:
            readings = data_store.get_readings(s.station_id)
            if readings:
                ref_date = data_store.get_max_reading_date(s.station_id)
                if ref_date:
                    calc_dt = datetime.combine(ref_date, datetime.min.time())
                    metrics = processing_engine.calculate_metrics(readings, calculation_date=calc_dt)
                    data_store.save_metrics(metrics)
                    recalculated += 1
        return {"status": "success", "message": f"Successfully refreshed metrics for {recalculated} stations."}
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


# Mount static directory for frontend
static_dir = os.path.join(os.path.dirname(__file__), "static")
if not os.path.exists(static_dir):
    os.makedirs(static_dir)

app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
def serve_index():
    """Serve main web dashboard."""
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Groundwater Insight API running."}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
