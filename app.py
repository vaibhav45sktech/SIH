"""
Streamlit dashboard application for Groundwater Recharge Insight Dashboard.
"""

import logging
from datetime import datetime, timedelta

import streamlit as st
import pandas as pd

from api_client import NWDPClient
from config import config
from data_store import DataStore
from insights import InsightInterpreter
from models.station import Station
from processing_engine import ProcessingEngine

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Page configuration
st.set_page_config(
    page_title="Groundwater Recharge Insight Dashboard",
    page_icon="💧",
    layout="wide",
    initial_sidebar_state="expanded"
)


def initialize_components():
    """Initialize application components."""
    if "data_store" not in st.session_state:
        st.session_state.data_store = DataStore()

    if "api_client" not in st.session_state:
        st.session_state.api_client = NWDPClient()

    if "processing_engine" not in st.session_state:
        st.session_state.processing_engine = ProcessingEngine()

    if "insight_interpreter" not in st.session_state:
        st.session_state.insight_interpreter = InsightInterpreter()


def main():
    """Main application entry point."""
    st.title("💧 Groundwater Recharge Insight Dashboard")
    st.markdown("**DWLR – NWDP Data Analysis**")

    # Initialize components
    initialize_components()

    # Sidebar
    with st.sidebar:
        st.header("Configuration")
        st.info(f"Data Mode: **{config.data_mode.upper()}**")

        if st.button("🔄 Refresh Data"):
            st.session_state.refresh_triggered = True

    data_store = st.session_state.data_store

    # Get stations
    stations = data_store.get_all_stations()

    # ------------------------------------------------------------------
    # NEW FIX: If stations table empty but readings exist, derive stations
    # ------------------------------------------------------------------
    if not stations:
        try:
            with data_store._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT DISTINCT station_id FROM readings LIMIT 10")
                rows = cursor.fetchall()
                station_ids = [row["station_id"] for row in rows]

            if station_ids:
                stations = [
                    Station(
                        station_id=sid,
                        name=f"Station {sid}",
                        latitude=0,
                        longitude=0
                    )
                    for sid in station_ids
                ]
                st.info("Stations automatically derived from dataset readings.")

        except Exception:
            pass
    # ------------------------------------------------------------------

    if not stations:
        st.warning("No stations found. Please fetch data from NWDP API or load mock data.")

        if st.button("Load Sample Stations"):
            with st.spinner("Loading sample stations and generating mock data..."):
                try:
                    api_client = st.session_state.api_client
                    data_store = st.session_state.data_store

                    mock_stations = api_client.fetch_stations()

                    if not mock_stations:
                        st.error("Failed to generate mock stations.")
                    else:
                        for station in mock_stations:
                            data_store.save_station(station)

                        st.success(f"Loaded {len(mock_stations)} stations.")

                        progress_bar = st.progress(0)
                        total_stations = len(mock_stations)

                        for idx, station in enumerate(mock_stations):

                            existing_readings = data_store.get_readings(station.station_id)
                            reading_count = len(existing_readings) if existing_readings else 0

                            if reading_count < 365:

                                end_date = datetime.now()
                                start_date = end_date - timedelta(days=365)

                                readings = api_client.fetch_readings(
                                    station.station_id,
                                    start_date,
                                    end_date
                                )

                                if readings:
                                    data_store.save_readings(readings)
                                    st.info(
                                        f"✅ Generated {len(readings)} readings for {station.name} "
                                        f"(had {reading_count} readings)"
                                    )
                                else:
                                    st.warning(f"⚠️ No readings generated for {station.name}")

                            else:
                                st.info(
                                    f"⏭️ Skipping {station.name} - {reading_count} readings already exist (>=365)"
                                )

                            progress_bar.progress((idx + 1) / total_stations)

                        st.success("✅ Sample data loading complete!")
                        st.rerun()

                except Exception as e:
                    st.error(f"Error loading sample data: {str(e)}")
                    logger.exception("Error in load sample stations")

    else:
        station_options = {f"{s.name} ({s.station_id})": s.station_id for s in stations}

        selected_station_name = st.selectbox(
            "Select Station",
            options=list(station_options.keys()),
            index=0
        )

        selected_station_id = station_options[selected_station_name]

        station = data_store.get_station(selected_station_id)

        if not station:
            station = Station(
                station_id=selected_station_id,
                name=f"Station {selected_station_id}",
                latitude=0,
                longitude=0
            )

        reference_date = data_store.get_max_reading_date(selected_station_id)

        if reference_date:
            logger.debug(
                f"Using reference_date={reference_date} for station {selected_station_id}"
            )

        display_station_summary(station, data_store)
        display_station_details(station, data_store, reference_date)


def display_station_summary(station: Station, data_store: DataStore):
    """Display summary card for selected station."""
    st.header(f"📊 {station.name}")

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("Station ID", station.station_id)

    with col2:
        if station.district:
            st.metric("District", station.district)

    with col3:
        if station.state:
            st.metric("State", station.state)

    with col4:
        metrics = data_store.get_latest_metrics(station.station_id)

        if metrics and metrics.risk_level:
            risk_color = {
                "Low Risk": "🟢",
                "Moderate Risk": "🟡",
                "High Risk": "🟠",
                "Critical Risk": "🔴"
            }.get(metrics.risk_level.value, "⚪")

            st.metric("Risk Level", f"{risk_color} {metrics.risk_level.value}")


def display_station_details(station: Station, data_store: DataStore, reference_date):
    """Display detailed analysis for selected station."""
    st.subheader("Detailed Analysis")

    if reference_date is None:
        st.warning(f"No readings found for station {station.station_id}")
        return

    end_date = reference_date
    start_date = end_date - timedelta(days=365)

    end_datetime = datetime.combine(end_date, datetime.max.time())
    start_datetime = datetime.combine(start_date, datetime.min.time())

    # Fix 3: Check for cached metrics first — only recalculate if none exist
    metrics = data_store.get_latest_metrics(station.station_id)

    if metrics is None:
        readings = data_store.get_readings(station.station_id)

        if not readings:
            st.warning(f"No readings found for station {station.station_id}")
            return

        processing_engine = st.session_state.processing_engine
        calculation_datetime = datetime.combine(reference_date, datetime.min.time())

        metrics = processing_engine.calculate_metrics(
            readings,
            calculation_date=calculation_datetime
        )

        # Save metrics so subsequent renders use the cache
        data_store.save_metrics(metrics)
    # else:
    #     st.caption("📦 Metrics loaded from cache.")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Trend Analysis")
        st.metric("Trend", metrics.trend_indicator.value)

        if metrics.trend_magnitude:
            st.metric("Change", f"{metrics.trend_magnitude:.2f} m")

    with col2:
        st.subheader("Risk Assessment")

        if metrics.risk_index is not None:
            st.metric("Risk Index", f"{metrics.risk_index:.1f}/100")

        if metrics.risk_level:
            st.metric("Risk Level", metrics.risk_level.value)

    insight_interpreter = st.session_state.insight_interpreter
    insight = insight_interpreter.generate_insight(metrics)

    st.subheader("📝 Interpretation")
    st.info(insight)

    st.subheader("Groundwater Level Chart")

    # Fetch all readings for chart (metrics window may be different)
    chart_readings = data_store.get_readings(station.station_id)

    if chart_readings:
        import plotly.graph_objects as go
        import numpy as np

        # Build dataframe for chart
        chart_df = pd.DataFrame([
            {"date": r.timestamp, "water_level_m": r.water_level_m}
            for r in chart_readings
        ]).sort_values("date")
        
        # Ensure y-axis has minimum 10m span to avoid exaggeration
        y_min = chart_df["water_level_m"].min()
        y_max = chart_df["water_level_m"].max()
        y_span = y_max - y_min
        if y_span < 10:
            padding = (10 - y_span) / 2
            y_range = [y_max + padding, y_min - padding]  # reversed
        else:
            y_range = [y_max + 1, y_min - 1]  # reversed, small padding

        # Determine chart color based on trend
        trend_colors = {
            "Recharging": "#2196F3",   # blue
            "Depleting":  "#F44336",   # red
            "Stable":     "#9E9E9E",   # grey
            "Insufficient Data": "#9E9E9E"
        }
        line_color = trend_colors.get(metrics.trend_indicator.value, "#9E9E9E")

        fig = go.Figure()

        # Actual readings line
        fig.add_trace(go.Scatter(
            x=chart_df["date"],
            y=chart_df["water_level_m"],
            mode="lines+markers",
            name="Water Level (m)",
            line=dict(color=line_color, width=2),
            marker=dict(size=4),
            hovertemplate="<b>%{x|%Y-%m-%d}</b><br>Water Level: %{y:.2f}m<extra></extra>"
        ))

        # Trend line overlay — always calculate directly from readings
        from scipy import stats as scipy_stats

        first_date = chart_df["date"].iloc[0]
        x_days = np.array([(d - first_date).days for d in chart_df["date"]])
        slope_result = scipy_stats.linregress(x_days, chart_df["water_level_m"].values)
        slope = slope_result.slope
        intercept = float(np.mean(chart_df["water_level_m"]) - slope * np.mean(x_days))
        trend_y = intercept + slope * x_days

        fig.add_trace(go.Scatter(
            x=chart_df["date"],
            y=trend_y,
            mode="lines",
            name="Trend Line",
            line=dict(color=line_color, width=2, dash="dash"),
            hovertemplate="<b>%{x|%Y-%m-%d}</b><br>Trend: %{y:.2f}m<extra></extra>"
        ))

        # Chart layout
        fig.update_layout(
            title=dict(
                text=f"{station.name} — Groundwater Level Over Time",
                font=dict(size=16)
            ),
            xaxis=dict(title="Date", showgrid=True, gridcolor="#333333"),
            yaxis=dict(
                title="Water Level (m below ground)",
                showgrid=True,
                gridcolor="#333333",
                range=y_range # Higher depth = lower on chart (more intuitive)
            ),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            hovermode="x unified",
            plot_bgcolor="#1e1e1e",
            paper_bgcolor="#1e1e1e",
            font=dict(color="#ffffff"),
            height=450
        )

        st.plotly_chart(fig, use_container_width=True)
    else:
        st.warning("No readings available for chart.")

if __name__ == "__main__":
    main()