/**
 * JALSACHET — Groundwater & Aquifer Report
 * Subtle Hydro Frontend Controller with Light & Dark Mode Toggle
 */

document.addEventListener('DOMContentLoaded', () => {
    // Initialize Lucide Icons
    lucide.createIcons();

    // State Variables
    let allStations = [];
    let selectedStationId = null;
    let chartInstance = null;
    let leafletMap = null;
    let mapMarkers = [];
    let currentFilter = 'ALL';
    let searchQuery = '';

    // DOM Element Handles
    const themeToggleBtn = document.getElementById('themeToggleBtn');
    const refreshBtn = document.getElementById('refreshBtn');
    const headerStationSelect = document.getElementById('headerStationSelect');
    const stationSearch = document.getElementById('stationSearch');
    const filterPills = document.getElementById('filterPills');
    const stationsContainer = document.getElementById('stationsContainer');
    const toast = document.getElementById('toast');
    const toastMsg = document.getElementById('toastMsg');
    const dropletsContainer = document.getElementById('dropletsContainer');

    // Stats Handles
    const statStations = document.getElementById('statStations');
    const statReadings = document.getElementById('statReadings');
    const statTrendRate = document.getElementById('statTrendRate');
    const statRiskCount = document.getElementById('statRiskCount');

    // Detail Handles
    const detailStationName = document.getElementById('detailStationName');
    const detailStationMeta = document.getElementById('detailStationMeta');
    const detailRiskBadge = document.getElementById('detailRiskBadge');
    const detailTrendStatus = document.getElementById('detailTrendStatus');
    const detailTrendPill = document.getElementById('detailTrendPill');
    const detailRiskScore = document.getElementById('detailRiskScore');
    const detailRiskGauge = document.getElementById('detailRiskGauge');
    const detailSeasonalDev = document.getElementById('detailSeasonalDev');
    const detailSpan = document.getElementById('detailSpan');
    const detailReadingsCount = document.getElementById('detailReadingsCount');
    const insightBody = document.getElementById('insightBody');
    const mapPointCount = document.getElementById('mapPointCount');

    // --------------------------------------------------------------------------
    // 0. LIGHT / DARK MODE TOGGLE CONTROLLER
    // --------------------------------------------------------------------------
    const savedTheme = localStorage.getItem('jalsachet-theme') || 'dark';
    document.documentElement.setAttribute('data-theme', savedTheme);

    themeToggleBtn.addEventListener('click', () => {
        const currentTheme = document.documentElement.getAttribute('data-theme');
        const newTheme = currentTheme === 'dark' ? 'light' : 'dark';

        document.documentElement.setAttribute('data-theme', newTheme);
        localStorage.setItem('jalsachet-theme', newTheme);

        if (chartInstance && selectedStationId) {
            fetch(`/api/station/${selectedStationId}`)
                .then(res => res.json())
                .then(data => renderWaterChart(data.readings, data.trend_line, data.metrics.trend));
        }
    });

    // --------------------------------------------------------------------------
    // 1. GENERATE SUBTLE FLOATING WATER DROPLETS
    // --------------------------------------------------------------------------
    function initWaterDroplets() {
        if (!dropletsContainer) return;
        dropletsContainer.innerHTML = '';
        const dropletCount = 14;

        for (let i = 0; i < dropletCount; i++) {
            const drop = document.createElement('div');
            drop.className = 'droplet-particle';
            
            const size = Math.random() * 12 + 6;
            const leftPos = Math.random() * 96 + 2;
            const duration = Math.random() * 10 + 12;
            const delay = Math.random() * 8;

            drop.style.width = `${size}px`;
            drop.style.height = `${size * 1.3}px`;
            drop.style.left = `${leftPos}%`;
            drop.style.animationDuration = `${duration}s`;
            drop.style.animationDelay = `${delay}s`;

            dropletsContainer.appendChild(drop);
        }
    }

    // --------------------------------------------------------------------------
    // 2. SCROLL REVEAL ANIMATIONS
    // --------------------------------------------------------------------------
    function initScrollObserver() {
        const observerOptions = {
            root: null,
            rootMargin: '0px 0px -40px 0px',
            threshold: 0.12
        };

        const observer = new IntersectionObserver((entries, obs) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    entry.target.classList.add('active');
                    obs.unobserve(entry.target);
                }
            });
        }, observerOptions);

        document.querySelectorAll('.reveal').forEach(el => observer.observe(el));
    }

    // --------------------------------------------------------------------------
    // 3. INITIALIZE LEAFLET MAP
    // --------------------------------------------------------------------------
    function initLeafletMap() {
        const punjabCenter = [31.0, 75.5];
        leafletMap = L.map('hydroMap', {
            zoomControl: true,
            attributionControl: false
        }).setView(punjabCenter, 8);

        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 18,
            className: 'map-tiles'
        }).addTo(leafletMap);
    }

    function updateMapMarkers() {
        if (!leafletMap) return;

        mapMarkers.forEach(m => leafletMap.removeLayer(m));
        mapMarkers = [];
        const bounds = [];

        allStations.forEach(s => {
            if (!s.latitude || !s.longitude) return;

            let color = '#00B4D8';
            if (s.risk_level === 'High Risk' || s.risk_level === 'Critical Risk') {
                color = '#E151AF';
            } else if (s.risk_level === 'Moderate Risk') {
                color = '#FED43F';
            } else if (s.trend === 'Recharging') {
                color = '#0066E6';
            } else if (s.trend === 'Stable') {
                color = '#0f766e';
            }

            const marker = L.circleMarker([s.latitude, s.longitude], {
                radius: s.station_id === selectedStationId ? 10 : 6.5,
                fillColor: color,
                color: '#ffffff',
                weight: s.station_id === selectedStationId ? 3 : 1.5,
                opacity: 1,
                fillOpacity: 0.85
            }).addTo(leafletMap);

            marker.bindTooltip(`
                <div style="font-family: sans-serif; font-size: 12px; padding: 4px;">
                    <strong>💧 ${s.name}</strong><br>
                    Trend: ${s.trend} (${s.trend_rate_m_per_year > 0 ? '+' : ''}${s.trend_rate_m_per_year} m/yr)<br>
                    Risk Status: <strong>${s.risk_level}</strong>
                </div>
            `, { direction: 'top' });

            marker.on('click', () => {
                selectStation(s.station_id);
            });

            mapMarkers.push(marker);
            bounds.push([s.latitude, s.longitude]);
        });

        if (bounds.length > 0) {
            leafletMap.fitBounds(bounds, { padding: [30, 30] });
        }
        mapPointCount.textContent = `${allStations.length} Stations`;
    }

    // --------------------------------------------------------------------------
    // 4. FETCH OVERVIEW & STATIONS DATA
    // --------------------------------------------------------------------------
    async function loadDashboardData() {
        try {
            const overviewRes = await fetch('/api/overview');
            const overview = await overviewRes.json();

            statStations.textContent = overview.total_stations;
            statReadings.textContent = overview.total_readings.toLocaleString();
            statTrendRate.textContent = `${overview.avg_trend_m_per_year > 0 ? '+' : ''}${overview.avg_trend_m_per_year} m/yr`;

            const highRiskCount = (overview.risk_distribution['High Risk'] || 0) + (overview.risk_distribution['Critical Risk'] || 0);
            statRiskCount.textContent = highRiskCount > 0 ? `${highRiskCount} Alert Wells` : '0 Critical';

            const stationsRes = await fetch('/api/stations');
            allStations = await stationsRes.json();

            populateHeaderDropdown(allStations);
            renderStationsGrid();
            updateMapMarkers();

            if (allStations.length > 0 && !selectedStationId) {
                selectStation(allStations[0].station_id);
            }
        } catch (err) {
            console.error('Error loading dashboard data:', err);
            showToast('Failed to connect to backend server.', 'error');
        }
    }

    function populateHeaderDropdown(stations) {
        headerStationSelect.innerHTML = '';
        stations.forEach(s => {
            const opt = document.createElement('option');
            opt.value = s.station_id;

            let label = s.name;
            if (!s.name.includes(s.station_id)) {
                label = `${s.name} (${s.station_id})`;
            }

            opt.textContent = label;
            headerStationSelect.appendChild(opt);
        });
    }

    function renderStationsGrid() {
        stationsContainer.innerHTML = '';

        const filtered = allStations.filter(s => {
            const matchesSearch = s.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
                                  s.station_id.toLowerCase().includes(searchQuery.toLowerCase()) ||
                                  s.district.toLowerCase().includes(searchQuery.toLowerCase());

            if (!matchesSearch) return false;

            if (currentFilter === 'ALL') return true;
            if (currentFilter === 'High Risk') return s.risk_level === 'High Risk' || s.risk_level === 'Critical Risk';
            if (currentFilter === 'Depleting') return s.trend === 'Depleting';
            if (currentFilter === 'Recharging') return s.trend === 'Recharging';
            if (currentFilter === 'Stable') return s.trend === 'Stable';

            return true;
        });

        if (filtered.length === 0) {
            stationsContainer.innerHTML = `
                <div style="grid-column: 1/-1; padding: 36px; text-align: center; color: var(--text-muted);">
                    No groundwater stations found matching search criteria.
                </div>
            `;
            return;
        }

        filtered.forEach(s => {
            const card = document.createElement('div');
            const trendClass = s.trend.toLowerCase();
            const isSelected = s.station_id === selectedStationId;

            let riskBadgeClass = 'low';
            if (s.risk_level === 'Moderate Risk') riskBadgeClass = 'moderate';
            if (s.risk_level === 'High Risk' || s.risk_level === 'Critical Risk') riskBadgeClass = 'high';

            const formattedRate = s.trend_rate_m_per_year !== null && s.trend_rate_m_per_year !== undefined
                ? `${s.trend_rate_m_per_year > 0 ? '+' : ''}${s.trend_rate_m_per_year.toFixed(2)} m/yr`
                : '0.00 m/yr';

            card.className = `station-card ${trendClass} ${isSelected ? 'active' : ''}`;
            card.innerHTML = `
                <div class="card-top">
                    <span class="station-id-name" title="${s.name}">${s.name}</span>
                    <span class="badge-risk ${riskBadgeClass}">${s.risk_level}</span>
                </div>
                <div class="card-body">
                    <div class="card-left-info">
                        <span class="card-depth-val">${s.latest_water_level_m !== null ? s.latest_water_level_m + 'm' : 'N/A'}</span>
                        <span class="card-depth-date">Last: ${s.latest_reading_date || 'N/A'}</span>
                    </div>
                    <div class="card-right-info">
                        <div class="card-trend-tag ${trendClass}">
                            <i data-lucide="${s.trend === 'Depleting' ? 'trending-down' : (s.trend === 'Recharging' ? 'trending-up' : 'minus')}"></i>
                            <span>${formattedRate}</span>
                        </div>
                    </div>
                </div>
            `;

            card.addEventListener('click', () => selectStation(s.station_id));
            stationsContainer.appendChild(card);
        });

        lucide.createIcons();
    }

    // --------------------------------------------------------------------------
    // 5. SELECT STATION & RENDER DETAILS
    // --------------------------------------------------------------------------
    async function selectStation(stationId) {
        selectedStationId = stationId;
        headerStationSelect.value = stationId;

        renderStationsGrid();
        updateMapMarkers();

        try {
            const res = await fetch(`/api/station/${stationId}`);
            if (!res.ok) throw new Error('Station not found');

            const data = await res.json();
            renderStationDetails(data);

            document.getElementById('analyticsSection').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        } catch (err) {
            console.error('Error fetching station details:', err);
            showToast(`Failed to load station ${stationId}`, 'error');
        }
    }

    function renderStationDetails(data) {
        const { station, metrics, insight, readings, trend_line } = data;

        detailStationName.textContent = station.name;
        detailStationMeta.textContent = `ID: ${station.station_id} • District: ${station.district} • Coords: ${station.latitude}, ${station.longitude}`;

        detailRiskBadge.textContent = metrics.risk_level.toUpperCase();
        detailRiskBadge.className = 'risk-badge-large';
        if (metrics.risk_level === 'High Risk' || metrics.risk_level === 'Critical Risk') {
            detailRiskBadge.style.background = 'rgba(225, 81, 175, 0.2)';
            detailRiskBadge.style.color = '#E151AF';
            detailRiskBadge.style.borderColor = '#E151AF';
        } else if (metrics.risk_level === 'Moderate Risk') {
            detailRiskBadge.style.background = 'rgba(254, 212, 63, 0.2)';
            detailRiskBadge.style.color = '#FED43F';
            detailRiskBadge.style.borderColor = '#FED43F';
        } else {
            detailRiskBadge.style.background = 'rgba(0, 180, 216, 0.2)';
            detailRiskBadge.style.color = '#00B4D8';
            detailRiskBadge.style.borderColor = '#00B4D8';
        }

        detailTrendStatus.textContent = metrics.trend.toUpperCase();
        detailTrendPill.textContent = `${metrics.trend_rate_m_per_year > 0 ? '+' : ''}${metrics.trend_rate_m_per_year} m/yr`;
        detailTrendPill.className = `trend-indicator-pill ${metrics.trend.toLowerCase()}`;

        detailRiskScore.innerHTML = `${metrics.risk_index} <small>/ 100</small>`;
        detailRiskGauge.style.width = `${Math.min(metrics.risk_index, 100)}%`;

        detailSeasonalDev.textContent = `${metrics.seasonal_deviation > 0 ? '+' : ''}${metrics.seasonal_deviation} m`;
        detailSpan.textContent = `${metrics.data_span_days} Days`;
        detailReadingsCount.textContent = `${readings.length} readings analyzed`;

        let formattedInsight = insight
            .replace(/\n\n/g, '</p><p>')
            .replace(/WARNING/g, '<strong style="color:#E151AF;">WARNING</strong>')
            .replace(/RECHARGING/g, '<strong style="color:#00B4D8;">RECHARGING</strong>')
            .replace(/DEPLETING/g, '<strong style="color:#E151AF;">DEPLETING</strong>');

        if (metrics.trend === 'Depleting') {
            formattedInsight += `<div class="insight-alert-box"><i data-lucide="alert-triangle"></i> <strong>Advisory:</strong> Groundwater depletion observed. Recommend constructing artificial recharge structures & check dams.</div>`;
        } else if (metrics.trend === 'Recharging') {
            formattedInsight += `<div class="insight-recharge-box"><i data-lucide="check-circle"></i> <strong>Positive Recovery:</strong> Aquifer level shows consistent recharge. Maintain balanced extraction rates.</div>`;
        }

        insightBody.innerHTML = `<p>${formattedInsight}</p>`;
        lucide.createIcons();

        renderWaterChart(readings, trend_line, metrics.trend);
    }

    // --------------------------------------------------------------------------
    // 6. CHART.JS HYDROGRAPH — VISIBLE SHADING BELOW THE LINE
    // --------------------------------------------------------------------------
    function renderWaterChart(readings, trendLine, trendStatus) {
        const ctx = document.getElementById('waterChart').getContext('2d');
        const isLight = document.documentElement.getAttribute('data-theme') === 'light';

        if (chartInstance) {
            chartInstance.destroy();
        }

        const labels = readings.map(r => r.date);
        const actualData = readings.map(r => r.water_level_m);
        const trendData = trendLine.map(t => t.trend_m);

        const bgGradient = ctx.createLinearGradient(0, 0, 0, 360);
        if (isLight) {
            bgGradient.addColorStop(0, 'rgba(0, 102, 230, 0.40)');
            bgGradient.addColorStop(1, 'rgba(0, 102, 230, 0.08)');
        } else {
            bgGradient.addColorStop(0, 'rgba(0, 180, 216, 0.45)');
            bgGradient.addColorStop(1, 'rgba(0, 180, 216, 0.08)');
        }

        const textColor = isLight ? '#475569' : '#94a3b8';
        const gridColor = isLight ? 'rgba(0, 102, 230, 0.08)' : 'rgba(0, 180, 216, 0.08)';

        chartInstance = new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: 'Water Level Depth (m)',
                        data: actualData,
                        borderColor: '#0066E6',
                        borderWidth: 2.5,
                        fill: 'end',
                        backgroundColor: bgGradient,
                        tension: 0.3,
                        pointRadius: readings.length > 150 ? 0 : 3,
                        pointHoverRadius: 5
                    },
                    {
                        label: 'Linear Regression Trend',
                        data: trendData,
                        borderColor: '#00B4D8',
                        borderWidth: 1.8,
                        borderDash: [6, 4],
                        fill: false,
                        pointRadius: 0
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: {
                    mode: 'index',
                    intersect: false
                },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: isLight ? '#ffffff' : 'rgba(8, 14, 26, 0.95)',
                        titleColor: isLight ? '#0f172a' : '#ffffff',
                        bodyColor: isLight ? '#334155' : '#e2e8f0',
                        borderColor: 'rgba(0, 180, 216, 0.3)',
                        borderWidth: 1,
                        titleFont: { family: 'Outfit', size: 12 },
                        bodyFont: { family: 'Plus Jakarta Sans', size: 12 },
                        padding: 10,
                        cornerRadius: 6
                    }
                },
                scales: {
                    x: {
                        type: 'category',
                        grid: { color: gridColor },
                        ticks: { color: textColor, font: { size: 11 }, maxTicksLimit: 8 }
                    },
                    y: {
                        reverse: true,
                        grid: { color: gridColor },
                        ticks: { color: textColor, font: { size: 11 } },
                        title: { display: true, text: 'Depth (m below ground level)', color: isLight ? '#0284c7' : '#00B4D8' }
                    }
                }
            }
        });
    }

    // --------------------------------------------------------------------------
    // 7. TOP RIGHT REFRESH BUTTON EVENT
    // --------------------------------------------------------------------------
    refreshBtn.addEventListener('click', async () => {
        refreshBtn.classList.add('spinning');
        refreshBtn.disabled = true;

        try {
            const res = await fetch('/api/refresh', { method: 'POST' });
            const data = await res.json();

            showToast(data.message || 'Metrics refreshed!', 'success');
            await loadDashboardData();
        } catch (err) {
            console.error('Refresh error:', err);
            showToast('Failed to refresh data server.', 'error');
        } finally {
            setTimeout(() => {
                refreshBtn.classList.remove('spinning');
                refreshBtn.disabled = false;
            }, 500);
        }
    });

    // Event Listeners
    headerStationSelect.addEventListener('change', (e) => {
        if (e.target.value) selectStation(e.target.value);
    });

    stationSearch.addEventListener('input', (e) => {
        searchQuery = e.target.value;
        renderStationsGrid();
    });

    filterPills.addEventListener('click', (e) => {
        if (e.target.classList.contains('pill')) {
            filterPills.querySelectorAll('.pill').forEach(p => p.classList.remove('active'));
            e.target.classList.add('active');
            currentFilter = e.target.getAttribute('data-filter');
            renderStationsGrid();
        }
    });

    function showToast(msg, type = 'success') {
        toastMsg.textContent = msg;
        toast.className = `toast show ${type}`;
        setTimeout(() => {
            toast.className = 'toast';
        }, 3200);
    }

    // Initialize
    initWaterDroplets();
    initScrollObserver();
    initLeafletMap();
    loadDashboardData();
});
