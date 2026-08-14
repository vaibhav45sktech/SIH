"""
Trend calculation engine using linear regression analysis.
"""

import logging
from datetime import datetime, timedelta
from typing import List, Optional

import numpy as np

from models.metrics import TrendIndicator, TrendMetrics, TrendStrength
from models.reading import Reading

logger = logging.getLogger(__name__)


# Trend classification thresholds (m/day)
NOISE_FLOOR = 0.0005  # m/day - slopes below this magnitude are considered noise (STABLE)

# Strength band thresholds (m/day)
LOW_STRENGTH_THRESHOLD = 0.0007
MEDIUM_STRENGTH_THRESHOLD = 0.0015

# Outlier guard (meters). Readings at or beyond this magnitude are sensor or
# transcription errors, not water levels: the raw CSV's target column spans
# -2014 m to +2431 m across 1,455 rows (0.35%) touching 61 stations. A single
# such value will dominate a least-squares fit, so they are dropped before
# regression.
OUTLIER_ABS_THRESHOLD = 100.0


class TrendEngine:
    """Engine for calculating groundwater level trends using linear regression."""
    
    def calculate_trend(
        self,
        readings: List[Reading],
        window_days: int = 3650
    ) -> Optional[TrendMetrics]:
        """
        Calculate trend using linear regression over last N days.

        Uses actual day deltas (timestamp differences) for x-values to handle
        irregular sampling correctly.

        Args:
            readings: List of Reading objects (should be sorted by timestamp)
            window_days: Analysis window in days (default 3650, i.e. ~10 years).
                The default was 90, but the median station is sampled only every
                ~92 days, so a 90-day window frequently captured fewer than the
                2 points regression requires and returned None.

        Returns:
            TrendMetrics object or None if insufficient data (< 2 points)
        """
        if not readings:
            logger.warning("No readings provided for trend calculation")
            return None

        # Outlier guard, applied before windowing so a bad value cannot also
        # drag the window anchor. See OUTLIER_ABS_THRESHOLD above.
        valid_readings = [
            r for r in readings
            if r.water_level_m is not None
            and abs(r.water_level_m) < OUTLIER_ABS_THRESHOLD
        ]
        if not valid_readings:
            logger.warning("No readings survived the outlier guard")
            return None

        # Filter to last N days from latest reading
        latest_date = max(r.timestamp for r in valid_readings)
        cutoff_date = latest_date - timedelta(days=window_days)
        filtered_readings = [
            r for r in valid_readings
            if r.timestamp >= cutoff_date
        ]

        # Need at least 2 points for linear regression
        if len(filtered_readings) < 2:
            logger.warning(
                f"Insufficient data for trend: {len(filtered_readings)} points "
                f"(need at least 2)"
            )
            return None
        
        # Sort by timestamp to ensure chronological order
        filtered_readings.sort(key=lambda r: r.timestamp)
        
        # Extract data for regression
        # Use actual day deltas from first timestamp (not index positions)
        first_timestamp = filtered_readings[0].timestamp
        x_values = np.array([
            (r.timestamp - first_timestamp).days
            for r in filtered_readings
        ])
        y_values = np.array([
            r.water_level_m
            for r in filtered_readings
        ])
        
        # Handle edge case: all readings identical (zero variance)
        if np.all(y_values == y_values[0]):
            slope = 0.0
            logger.info("All readings identical, slope = 0.0")
        else:
            # Perform linear regression: y = slope * x + intercept
            # Using numpy.polyfit with degree 1
            coefficients = np.polyfit(x_values, y_values, deg=1)
            slope = float(coefficients[0])  # m/day

        # Classify trend status
        # For depth below ground: negative slope = Recharging (water rising), positive slope = Depleting (water falling)
        # Use noise dead-zone to avoid classifying small slopes as trends
        if abs(slope) < NOISE_FLOOR:
            # Slope magnitude below noise floor = Stable
            status = TrendIndicator.STABLE
        elif slope < 0:
            # Negative slope: depth decreasing = water rising = Recharging
            status = TrendIndicator.RECHARGING
        else:
            # Positive slope: depth increasing = water falling = Depleting
            status = TrendIndicator.DEPLETING
        
        # Classify strength
        abs_slope = abs(slope)
        if abs_slope < LOW_STRENGTH_THRESHOLD:
            strength = TrendStrength.LOW
        elif abs_slope < MEDIUM_STRENGTH_THRESHOLD:
            strength = TrendStrength.MEDIUM
        else:
            strength = TrendStrength.STRONG
        
        # Calculate magnitude: total change over the window period
        # magnitude = slope * window_days (units: meters over window)
        magnitude = slope * window_days
        
        return TrendMetrics(
            status=status,
            slope=slope,
            strength=strength,
            magnitude=magnitude,
            window_days=window_days,
            data_points_used=len(filtered_readings)
        )

