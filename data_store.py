"""
SQLite database storage and retrieval helpers for groundwater data.
"""

import logging
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from typing import List, Optional

from config import config
from models.metrics import Metrics, TrendIndicator, RiskLevel
from models.reading import Reading
from models.station import Station

logger = logging.getLogger(__name__)


class DataStore:
    """Manages SQLite database operations for groundwater data."""
    
    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize data store.
        
        Args:
            db_path: Path to SQLite database file (defaults to config)
        """
        self.db_path = db_path or config.db_path
        self._initialize_schema()
    
    @contextmanager
    def _get_connection(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    
    def _initialize_schema(self):
        """Create database tables if they don't exist."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Stations table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS stations (
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
            """)
            
            # Readings table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS readings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    station_id TEXT NOT NULL,
                    timestamp TIMESTAMP NOT NULL,
                    water_level_m REAL NOT NULL,
                    quality_flag TEXT,
                    source TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (station_id) REFERENCES stations(station_id),
                    UNIQUE(station_id, timestamp)
                )
            """)
            
            # Metrics table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    station_id TEXT NOT NULL,
                    calculation_date TIMESTAMP NOT NULL,
                    trend_indicator TEXT NOT NULL,
                    trend_magnitude REAL,
                    trend_period_days INTEGER,
                    seasonal_deviation REAL,
                    seasonal_baseline REAL,
                    risk_index REAL,
                    risk_level TEXT,
                    data_points_used INTEGER,
                    calculation_notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (station_id) REFERENCES stations(station_id),
                    UNIQUE(station_id, calculation_date)
                )
            """)
            
            # Create indexes
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_readings_station_timestamp ON readings(station_id, timestamp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_metrics_station_date ON metrics(station_id, calculation_date)")
    
    def save_station(self, station: Station) -> None:
        """Save or update a station record."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO stations 
                (station_id, name, latitude, longitude, district, state, elevation_m, description, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                station.station_id,
                station.name,
                station.latitude,
                station.longitude,
                station.district,
                station.state,
                station.elevation_m,
                station.description,
                datetime.now()
            ))
    
    def save_readings(self, readings: List[Reading]) -> None:
        """Save multiple readings (insert or ignore duplicates)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for reading in readings:
                cursor.execute("""
                    INSERT OR IGNORE INTO readings 
                    (station_id, timestamp, water_level_m, quality_flag, source)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    reading.station_id,
                    reading.timestamp,
                    reading.water_level_m,
                    reading.quality_flag,
                    reading.source
                ))
    
    def save_metrics(self, metrics: Metrics) -> None:
        """Save calculated metrics."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO metrics 
                (station_id, calculation_date, trend_indicator, trend_magnitude, 
                 trend_period_days, seasonal_deviation, seasonal_baseline, 
                 risk_index, risk_level, data_points_used, calculation_notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                metrics.station_id,
                metrics.calculation_date,
                metrics.trend_indicator.value,
                metrics.trend_magnitude,
                metrics.trend_period_days,
                metrics.seasonal_deviation,
                metrics.seasonal_baseline,
                metrics.risk_index,
                metrics.risk_level.value if metrics.risk_level else None,
                metrics.data_points_used,
                metrics.calculation_notes
            ))
    
    def get_station(self, station_id: str) -> Optional[Station]:
        """Retrieve a station by ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM stations WHERE station_id = ?", (station_id,))
            row = cursor.fetchone()
            if row:
                return Station(
                    station_id=row["station_id"],
                    name=row["name"],
                    latitude=row["latitude"],
                    longitude=row["longitude"],
                    district=row["district"],
                    state=row["state"],
                    elevation_m=row["elevation_m"],
                    description=row["description"]
                )
            return None
    
    def get_all_stations(self) -> List[Station]:
        """Retrieve all stations."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM stations ORDER BY name")
            return [
                Station(
                    station_id=row["station_id"],
                    name=row["name"],
                    latitude=row["latitude"],
                    longitude=row["longitude"],
                    district=row["district"],
                    state=row["state"],
                    elevation_m=row["elevation_m"],
                    description=row["description"]
                )
                for row in cursor.fetchall()
            ]
    
    def get_readings(
        self,
        station_id: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> List[Reading]:
        """Retrieve readings for a station within a date range."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM readings WHERE station_id = ?"
            params = [station_id]
            
            if start_date:
                query += " AND timestamp >= ?"
                params.append(start_date)
            
            if end_date:
                query += " AND timestamp <= ?"
                params.append(end_date)
            
            query += " ORDER BY timestamp"
            cursor.execute(query, params)
            
            return [
                Reading(
                    station_id=row["station_id"],
                    timestamp=datetime.fromisoformat(row["timestamp"]) if isinstance(row["timestamp"], str) else row["timestamp"],
                    water_level_m=row["water_level_m"],
                    quality_flag=row["quality_flag"],
                    source=row["source"]
                )
                for row in cursor.fetchall()
            ]
    
    def get_max_reading_date(self, station_id: str) -> Optional[date]:
        """
        Get the latest reading date for a station.
        
        This becomes the system's effective "now" - the reference date
        for all UI queries and date window calculations.
        
        Args:
            station_id: Station identifier
            
        Returns:
            datetime.date of the latest reading, or None if no readings exist
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT MAX(timestamp) FROM readings WHERE station_id = ?",
                (station_id,)
            )
            row = cursor.fetchone()
            if row and row[0]:
                max_timestamp = row[0]
                # Handle both string and datetime types
                if isinstance(max_timestamp, str):
                    dt = datetime.fromisoformat(max_timestamp)
                else:
                    dt = max_timestamp
                # Return as date
                if isinstance(dt, datetime):
                    return dt.date()
                elif isinstance(dt, date):
                    return dt
            return None
    
    def get_latest_metrics(self, station_id: str) -> Optional[Metrics]:
        """Retrieve the most recent metrics for a station."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM metrics 
                WHERE station_id = ? 
                ORDER BY calculation_date DESC 
                LIMIT 1
            """, (station_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return Metrics(
                station_id=row["station_id"],
                calculation_date=datetime.fromisoformat(row["calculation_date"]) if isinstance(row["calculation_date"], str) else row["calculation_date"],
                trend_indicator=TrendIndicator(row["trend_indicator"]),
                trend_magnitude=row["trend_magnitude"],
                trend_period_days=row["trend_period_days"],
                seasonal_deviation=row["seasonal_deviation"],
                seasonal_baseline=row["seasonal_baseline"],
                risk_index=row["risk_index"],
                risk_level=RiskLevel(row["risk_level"]) if row["risk_level"] else None,
                data_points_used=row["data_points_used"],
                calculation_notes=row["calculation_notes"]
            )

