"""
Processing engine for trend analysis, seasonal comparison, and risk index calculation.
"""

import logging
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from config import config
from models.metrics import Metrics, RiskLevel, TrendIndicator
from models.reading import Reading
from processing.trend_engine import (
    LOW_STRENGTH_THRESHOLD,
    MEDIUM_STRENGTH_THRESHOLD,
    TrendEngine,
)
from processing.seasonal_engine import SeasonalEngine

logger = logging.getLogger(__name__)

# Mean days per year, for converting m/day slopes to m/yr rates.
DAYS_PER_YEAR = 365.25


class ProcessingEngine:
    """Core analytics engine for groundwater data processing."""
    
    def __init__(self):
        """Initialize processing engine."""
        self.trend_window_days = config.trend_window_days
        self.seasonal_window_days = config.seasonal_window_days
        self.seasonal_comparison_years = config.seasonal_comparison_years
        self.risk_trend_weight = config.risk_trend_weight
        self.risk_seasonal_weight = config.risk_seasonal_weight
        self.trend_engine = TrendEngine()
        self.seasonal_engine = SeasonalEngine()
    
    def calculate_metrics(self, readings: List[Reading], calculation_date: Optional[datetime] = None) -> Metrics:
        """
        Calculate all metrics for a set of readings.
        
        Args:
            readings: List of Reading objects
            calculation_date: Date for calculation (defaults to latest reading date)
        
        Returns:
            Metrics object with calculated values
        """
        if not readings:
            return self._create_empty_metrics(readings, calculation_date)
        
        calculation_date = calculation_date or max(r.timestamp for r in readings)
        station_id = readings[0].station_id
        
        # Convert to DataFrame for easier processing
        df = self._readings_to_dataframe(readings)
        
        # Calculate trend using TrendEngine
        trend_metrics = self.trend_engine.calculate_trend(readings, self.trend_window_days)
        
        if trend_metrics:
            trend_indicator = trend_metrics.status
            trend_magnitude = trend_metrics.magnitude
            # Annualised rate, used for risk banding. Must be window-independent:
            # magnitude is slope * window_days, so it scales with the window and
            # cannot be compared against fixed thresholds.
            trend_rate_m_per_yr = trend_metrics.slope * DAYS_PER_YEAR
        else:
            trend_indicator = TrendIndicator.INSUFFICIENT_DATA
            trend_magnitude = None
            trend_rate_m_per_yr = None
        
        # Calculate seasonal deviation using SeasonalEngine
        seasonal_metrics = self.seasonal_engine.calculate_seasonal_deviation(
            readings,
            self.seasonal_window_days,
            self.seasonal_comparison_years,
            reference_date=calculation_date
        )
        
        if seasonal_metrics:
            seasonal_deviation = seasonal_metrics.deviation
            seasonal_baseline = seasonal_metrics.historical_baseline
        else:
            seasonal_deviation = None
            seasonal_baseline = None
        
        # Calculate risk index
        risk_index, risk_level = self._calculate_risk_index(
            trend_indicator, trend_rate_m_per_yr, seasonal_deviation
        )
        
        return Metrics(
            station_id=station_id,
            calculation_date=calculation_date,
            trend_indicator=trend_indicator,
            trend_magnitude=trend_magnitude,
            trend_period_days=self.trend_window_days,
            trend_metrics=trend_metrics,
            seasonal_deviation=seasonal_deviation,  # Backward compatibility
            seasonal_baseline=seasonal_baseline,    # Backward compatibility
            seasonal_metrics=seasonal_metrics,      # Detailed seasonal analysis
            risk_index=risk_index,
            risk_level=risk_level,
            data_points_used=len(readings)
        )
    
    def _readings_to_dataframe(self, readings: List[Reading]) -> pd.DataFrame:
        """Convert readings list to pandas DataFrame."""
        return pd.DataFrame([
            {
                "timestamp": r.timestamp,
                "water_level_m": r.water_level_m
            }
            for r in readings
        ]).sort_values("timestamp")
    
    def _calculate_trend(self, df: pd.DataFrame) -> Tuple[TrendIndicator, Optional[float]]:
        """
        Calculate short-term trend using moving average comparison.
        
        Returns:
            Tuple of (TrendIndicator, magnitude in meters)
        """
        if len(df) < 2:
            return TrendIndicator.INSUFFICIENT_DATA, None
        
        # TODO: Implement trend calculation logic
        # - Compare recent average to earlier average
        # - Classify as Recharging, Stable, or Depleting
        # - Calculate magnitude of change
        
        return TrendIndicator.INSUFFICIENT_DATA, None
    
    def _calculate_seasonal_deviation(
        self,
        df: pd.DataFrame,
        reference_date: datetime
    ) -> Tuple[Optional[float], Optional[float]]:
        """
        Calculate deviation from seasonal baseline.
        
        Returns:
            Tuple of (deviation in meters, baseline value)
        """
        if len(df) < 30:  # Need at least some historical data
            return None, None
        
        # TODO: Implement seasonal deviation calculation
        # - Compare current period to same period in previous years
        # - Calculate deviation from baseline
        
        return None, None
    
    def _calculate_risk_index(
        self,
        trend_indicator: TrendIndicator,
        trend_rate_m_per_yr: Optional[float],
        seasonal_deviation: Optional[float]
    ) -> Tuple[Optional[float], Optional[RiskLevel]]:
        """
        Calculate composite risk index (0-100).

        Args:
            trend_indicator: Trend classification
            trend_rate_m_per_yr: Annualised rate of change (m/yr). Deliberately a
                rate, not a magnitude-over-window: magnitude is slope*window_days
                and so rescales with the window, which would silently reband every
                station whenever trend_window_days changed.
            seasonal_deviation: Deviation from seasonal baseline

        Returns:
            Tuple of (risk_index, RiskLevel)
        """
        if trend_indicator == TrendIndicator.INSUFFICIENT_DATA:
            return None, None

        # --- Trend Component (0-100) ---
        if trend_indicator == TrendIndicator.RECHARGING:
            trend_score = 0.0
        elif trend_indicator == TrendIndicator.STABLE:
            trend_score = 10.0
        else:  # DEPLETING — score by annualised rate
            if trend_rate_m_per_yr is None:
                trend_score = 50.0
            else:
                abs_rate = abs(trend_rate_m_per_yr)
                # Bands preserve the original intent: these are the strength
                # thresholds from trend_engine expressed in m/yr, i.e.
                #   LOW    < 0.0007 m/day * 365.25 = 0.256 m/yr
                #   MEDIUM 0.256 - 0.548 m/yr
                #   STRONG > 0.548 m/yr
                if abs_rate < LOW_STRENGTH_THRESHOLD * DAYS_PER_YEAR:
                    trend_score = 50.0
                elif abs_rate < MEDIUM_STRENGTH_THRESHOLD * DAYS_PER_YEAR:
                    trend_score = 75.0
                else:
                    trend_score = 100.0

        # --- Seasonal Component (0-100) ---
        if seasonal_deviation is None:
            seasonal_score = 50.0  # Neutral — no data
        elif seasonal_deviation > 0.05:
            seasonal_score = 0.0   # Above normal — favorable
        elif abs(seasonal_deviation) <= 0.05:
            seasonal_score = 20.0  # Normal
        elif seasonal_deviation >= -1.0:
            seasonal_score = 50.0  # Below normal, moderate
        else:
            seasonal_score = 80.0  # Below normal by >1m, concerning

        # --- Composite Score ---
        risk_index = (trend_score * self.risk_trend_weight) + (seasonal_score * self.risk_seasonal_weight)
        risk_index = round(risk_index, 1)

        # --- Classify Risk Level ---
        if risk_index < config.risk_low_threshold:
            risk_level = RiskLevel.LOW
        elif risk_index < config.risk_moderate_threshold:
            risk_level = RiskLevel.MODERATE
        elif risk_index < 80.0:
            risk_level = RiskLevel.HIGH
        else:   
            risk_level = RiskLevel.CRITICAL

        return risk_index, risk_level

    def _create_empty_metrics(
        self,
        readings: List[Reading],
        calculation_date: Optional[datetime]
    ) -> Metrics:
        """Create empty metrics when insufficient data."""
        station_id = readings[0].station_id if readings else "unknown"
        # calculation_date must be provided explicitly - no system time fallback
        if calculation_date is None:
            raise ValueError("calculation_date must be provided when creating empty metrics")
        return Metrics(
            station_id=station_id,
            calculation_date=calculation_date,
            trend_indicator=TrendIndicator.INSUFFICIENT_DATA,
            data_points_used=0,
            calculation_notes="Insufficient data for calculation"
        )

