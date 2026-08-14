"""
Configuration settings and mode toggles for the Groundwater Recharge Insight Dashboard.
"""

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class Config:
    """Application configuration settings."""
    
    # Data source mode: 'db' | 'api' | 'mock'
    #   db   - read real readings from the local SQLite store (default)
    #   api  - fetch live from the NWDP API
    #   mock - synthesise readings via api_client's generator
    # Defaults to 'db' so the dashboard always shows real CGWB data. Mock data
    # is synthetic and must never be served unless explicitly requested with
    # DATA_MODE=mock, since it is indistinguishable from real readings on screen.
    data_mode: str = os.getenv("DATA_MODE", "db")
    
    # NWDP API settings
    nwdp_api_base_url: str = os.getenv("NWDP_API_BASE_URL", "https://api.nwdp.gov.in")
    nwdp_api_key: Optional[str] = os.getenv("NWDP_API_KEY", None)
    nwdp_api_timeout: int = int(os.getenv("NWDP_API_TIMEOUT", "30"))
    
    # Database settings
    db_path: str = os.getenv("DB_PATH", "groundwater_data.db")
    
    # Processing settings
    # 3650 days (~10 years) to match TrendEngine.calculate_trend's default. This
    # value is passed explicitly by ProcessingEngine, so it SHADOWS that default:
    # leaving it at 365 made the dashboard report a 1-year trend while verify.py
    # reported a 10-year one. The median station is sampled every ~92 days, so a
    # 1-year window rests on only ~4 points.
    trend_window_days: int = int(os.getenv("TREND_WINDOW_DAYS", "3650"))

    # Seasonal deviation needs its OWN window and must stay at ~1 year. It compares
    # the current window against the same window offset by whole years, so a window
    # longer than the offset makes those windows overlap and the comparison
    # meaningless. Do not reuse trend_window_days here.
    seasonal_window_days: int = int(os.getenv("SEASONAL_WINDOW_DAYS", "365"))
    seasonal_comparison_years: int = int(os.getenv("SEASONAL_COMPARISON_YEARS", "2"))
    
    # Risk index weights
    risk_trend_weight: float = float(os.getenv("RISK_TREND_WEIGHT", "0.6"))
    risk_seasonal_weight: float = float(os.getenv("RISK_SEASONAL_WEIGHT", "0.4"))
    
    # Risk thresholds
    risk_low_threshold: float = float(os.getenv("RISK_LOW_THRESHOLD", "30.0"))
    risk_moderate_threshold: float = float(os.getenv("RISK_MODERATE_THRESHOLD", "60.0"))
    
    # Mock data settings
    mock_data_path: Optional[str] = os.getenv("MOCK_DATA_PATH", None)
    
    def is_mock_mode(self) -> bool:
        """Check if running in mock (synthetic) data mode. Opt-in only."""
        return self.data_mode.lower() == "mock"

    def is_api_mode(self) -> bool:
        """Check if running in API mode."""
        return self.data_mode.lower() == "api"

    def is_db_mode(self) -> bool:
        """Check if reading real stored readings from the local SQLite store."""
        return self.data_mode.lower() == "db"


# Global configuration instance
config = Config()

