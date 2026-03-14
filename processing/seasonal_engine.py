"""
Seasonal deviation calculation engine using rolling 90-day windows.
"""

import logging
from datetime import datetime, timedelta
from typing import List, Optional

from models.metrics import SeasonalMetrics
from models.reading import Reading

logger = logging.getLogger(__name__)


class SeasonalEngine:
    """Engine for calculating seasonal deviation from historical baselines."""
    
    def calculate_seasonal_deviation(
        self,
        readings: List[Reading],
        window_days: int = 365,
        years: int = 3,
        reference_date: Optional[datetime] = None
    ) -> Optional[SeasonalMetrics]:
        """
        Calculate seasonal deviation using rolling 90-day windows.
        
        Args:
            readings: List of Reading objects (should be sorted by timestamp)
            window_days: Analysis window size in days (default 90)
            years: Number of historical years to compare (default 3)
            reference_date: Data-derived reference date (anchor for windows)
        
        Returns:
            SeasonalMetrics object or None if insufficient data
        """
        if not readings:
            logger.warning("No readings provided for seasonal deviation calculation")
            return None
        
        # Sort readings by timestamp
        sorted_readings = sorted(readings, key=lambda r: r.timestamp)
        
        # Use data-derived reference_date as anchor (Step 3.5 time contract)
        if reference_date is None:
            reference_date = sorted_readings[-1].timestamp
        end_date = reference_date
        start_date = end_date - timedelta(days=window_days)
        
        # Filter to current rolling window
        actual_window = [
            r for r in sorted_readings
            if start_date <= r.timestamp <= end_date and r.water_level_m is not None
        ]
        
        if len(actual_window) < 2:
            logger.warning(
                f"Insufficient data for seasonal deviation: {len(actual_window)} points "
                f"(need at least 2)"
            )
            return None
        
        # Calculate actual change (last - first)
        actual_change = actual_window[-1].water_level_m - actual_window[0].water_level_m
        
        # Collect historical 90-day changes from previous years
        historical_changes = []
        for year in range(1, years + 1):
            target_start = start_date - timedelta(days=year * 365)
            target_end = end_date - timedelta(days=year * 365)
            
            historical_window = [
                r for r in sorted_readings
                if target_start <= r.timestamp <= target_end and r.water_level_m is not None
            ]
            
            if len(historical_window) >= 2:
                historical_change = historical_window[-1].water_level_m - historical_window[0].water_level_m
                historical_changes.append(historical_change)
        
        if not historical_changes:
            logger.info("No valid historical windows found for seasonal baseline")
            return None
        
        # Calculate baseline as mean of historical changes
        historical_baseline = sum(historical_changes) / len(historical_changes)
        
        # Calculate deviation
        deviation = actual_change - historical_baseline
        
        # Get season label for current period
        season_label = self.get_season_label(end_date)
        
        return SeasonalMetrics(
            actual_change=actual_change,
            historical_baseline=historical_baseline,
            deviation=deviation,
            season_label=season_label,
            years_used=len(historical_changes)
        )
    
    @staticmethod
    def get_season_label(date: datetime) -> str:
        """
        Get season label for a given date.
        
        Args:
            date: Datetime object
        
        Returns:
            Season label string
        """
        month = date.month
        
        if month in [3, 4, 5]:
            return "Summer / Pre-Monsoon"
        elif month in [6, 7, 8, 9]:
            return "Monsoon"
        elif month in [10, 11]:
            return "Post-Monsoon"
        else:  # 12, 1, 2
            return "Winter"
