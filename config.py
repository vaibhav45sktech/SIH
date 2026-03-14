"""
Configuration settings and mode toggles for the Groundwater Recharge Insight Dashboard.
"""

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class Config:
    """Application configuration settings."""
    
    # Data source mode: 'api' or 'mock'
    data_mode: str = os.getenv("DATA_MODE", "mock")
    
    # NWDP API settings
    nwdp_api_base_url: str = os.getenv("NWDP_API_BASE_URL", "https://api.nwdp.gov.in")
    nwdp_api_key: Optional[str] = os.getenv("NWDP_API_KEY", None)
    nwdp_api_timeout: int = int(os.getenv("NWDP_API_TIMEOUT", "30"))
    
    # Database settings
    db_path: str = os.getenv("DB_PATH", "groundwater_data.db")
    
    # Processing settings
    trend_window_days: int = int(os.getenv("TREND_WINDOW_DAYS", "365"))
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
        """Check if running in mock data mode."""
        return self.data_mode.lower() == "mock"
    
    def is_api_mode(self) -> bool:
        """Check if running in API mode."""
        return self.data_mode.lower() == "api"


# Global configuration instance
config = Config()

