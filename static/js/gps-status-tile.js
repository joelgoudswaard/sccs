/**
 * SCCS GPS status tile — live telemetry via gps_update socket.
 */
(function () {
    'use strict';

    const FIX_LABELS = {
        0: 'No fix',
        1: 'GPS fix',
        2: 'DGPS fix',
        3: 'PPS fix',
        4: 'RTK fix',
        5: 'RTK float',
        6: 'Estimated',
        7: 'Manual',
        8: 'Simulation',
    };

    const FALLBACK_TIMEZONE = 'Australia/Melbourne';

    const NO_FIX_GPS = {
        fix_quality: 0,
        satellites: 0,
        satellites_in_view: 4,
        latitude: null,
        longitude: null,
        altitude_m: null,
        hdop: null,
        speed_kmh: null,
        course_deg: null,
        suburb: null,
        local_time: null,
        date: null,
        utc_time: null,
        timezone: FALLBACK_TIMEZONE,
        sunrise: null,
        sunset: null,
        raw_sentences: [],
    };

    const NO_HARDWARE_GPS = {
        fix_quality: 0,
        satellites: 0,
        satellites_in_view: 0,
        latitude: null,
        longitude: null,
        altitude_m: null,
        hdop: null,
        speed_kmh: null,
        course_deg: null,
        suburb: null,
        local_time: null,
        date: null,
        utc_time: null,
        timezone: null,
        sunrise: null,
        sunset: null,
        raw_sentences: [],
        hardware_missing: true,
    };

    const tile = document.getElementById('tile-gps-status');
    const els = {
        headline: document.getElementById('gps-headline'),
        detail: document.getElementById('gps-detail'),
        coords: document.getElementById('gps-status-coords'),
        metrics: document.getElementById('gps-metrics'),
        navigation: document.getElementById('gps-navigation'),
        clock: document.getElementById('gps-clock'),
        raw: document.getElementById('gps-raw'),
        clearBtn: document.getElementById('gps-clear-btn'),
        segments: document.querySelectorAll('.gps-status-tile__segment'),
    };

    if (!tile || !els.metrics) return;

    const state = {
        forceMode: null,
        liveData: null,
        backendConnected: false,
    };

    function getSocket() {
        return window.SCCS?.socket ?? null;
    }

    function formatCoordShort(value, axis) {
        if (value === null || value === undefined || Number.isNaN(Number(value))) {
            return null;
        }
        const num = Number(value);
        const abs = Math.abs(num).toFixed(2);
        const hemi = axis === 'lat' ? (num >= 0 ? 'N' : 'S') : num >= 0 ? 'E' : 'W';
        return `${abs}°${hemi}`;
    }

    function formatCoord(value, axis) {
        if (value === null || value === undefined || Number.isNaN(Number(value))) {
            return '—';
        }
        const num = Number(value);
        const abs = Math.abs(num).toFixed(6);
        const hemi = axis === 'lat' ? (num >= 0 ? 'N' : 'S') : num >= 0 ? 'E' : 'W';
        return `${abs}° ${hemi}`;
    }

    function formatNumber(value, digits, suffix = '') {
        if (value === null || value === undefined || Number.isNaN(Number(value))) {
            return '—';
        }
        return `${Number(value).toFixed(digits)}${suffix}`;
    }

    function formatFixLabel(data) {
        if (data.hardware_missing) return 'No hardware';
        const q = Number(data.fix_quality);
        if (Number.isNaN(q)) return '—';
        return FIX_LABELS[q] || `Fix ${q}`;
    }

    function getDisplayData() {
        if (state.forceMode === 'no_hardware') return { ...NO_HARDWARE_GPS };
        if (state.forceMode === 'no_fix') return { ...NO_FIX_GPS };
        if (state.liveData) return { ...state.liveData };
        return {
            fix_quality: 0,
            satellites: 0,
            latitude: null,
            longitude: null,
            suburb: null,
            hardware_missing: false,
        };
    }

    function getModeDetail() {
        if (state.forceMode === 'no_hardware') return 'Simulating missing hardware';
        if (state.forceMode === 'no_fix') return 'Simulating no fix';
        if (state.backendConnected) return 'Live receiver';
        return 'Waiting for GPS data';
    }

    function getHeadline(data) {
        if (data.hardware_missing) {
            return 'GPS unavailable';
        }
        if ((data.fix_quality ?? 0) < 1) return 'Searching for fix';
        return data.suburb || formatFixLabel(data);
    }

    function formatFallbackDetail(data) {
        // Coords are already shown on the coords line; title already says "GPS unavailable".
        const suburb = data.suburb || data.last_known_suburb;
        if (suburb) {
            return `Using fallback coordinates · ${suburb}`;
        }
        return 'Using fallback coordinates';
    }

    function renderSummary(data) {
        const fixLabel = formatFixLabel(data);
        const inUse = data.satellites ?? 0;
        const inView = data.satellites_in_view ?? inUse;
        const hasFix = !data.hardware_missing && (data.fix_quality ?? 0) >= 1;

        tile.classList.toggle('is-fix', hasFix);
        tile.classList.toggle('is-no-fix', !data.hardware_missing && (data.fix_quality ?? 0) < 1);
        tile.classList.toggle('is-no-hardware', !!data.hardware_missing);

        if (els.headline) {
            els.headline.textContent = getHeadline(data);
        }

        if (els.detail) {
            if (data.hardware_missing) {
                els.detail.textContent = formatFallbackDetail(data);
            } else if (hasFix) {
                els.detail.textContent = `${fixLabel} · ${inUse} in use · ${inView} in view`;
            } else {
                els.detail.textContent = `${inView} in view · ${getModeDetail()}`;
            }
            els.detail.classList.toggle('is-forced', state.forceMode !== null);
        }

        if (els.coords) {
            const lat = formatCoordShort(data.latitude, 'lat');
            const lng = formatCoordShort(data.longitude, 'lng');
            els.coords.textContent = lat && lng ? `${lat} · ${lng}` : '—';
        }
    }

    function renderMetrics(data) {
        const fixQuality = data.fix_quality ?? 0;
        const hasFix = !data.hardware_missing && fixQuality >= 1;
        const inUse = data.satellites ?? 0;
        const inView = data.satellites_in_view ?? inUse;
        const hdop = data.hdop;

        const metrics = [
            {
                label: 'In use',
                value: data.hardware_missing ? '—' : String(inUse),
                tone: inUse >= 6 ? 'is-good' : inUse > 0 ? 'is-warn' : 'is-bad',
            },
            {
                label: 'In view',
                value: data.hardware_missing ? '—' : String(inView),
                tone: inView >= 8 ? 'is-good' : inView > 0 ? 'is-warn' : 'is-bad',
            },
            {
                label: 'HDOP',
                value: formatNumber(hdop, 1),
                tone: hdop === null ? '' : hdop <= 1.5 ? 'is-good' : hdop <= 3 ? 'is-warn' : 'is-bad',
            },
            {
                label: 'Fix',
                value: data.hardware_missing ? '—' : (hasFix ? formatFixLabel(data) : 'None'),
                tone: hasFix ? 'is-good' : data.hardware_missing ? '' : 'is-bad',
            },
        ];

        els.metrics.innerHTML = metrics.map((metric) => `
            <div class="gps-status-tile__metric ${metric.tone}">
                <span class="gps-status-tile__metric-label">${metric.label}</span>
                <span class="gps-status-tile__metric-value">${metric.value}</span>
            </div>
        `).join('');
    }

    function renderFacts(container, rows) {
        if (!container) return;
        container.innerHTML = rows.map((row) => `
            <div class="gps-status-tile__fact">
                <dt class="gps-status-tile__fact-label">${row.label}</dt>
                <dd class="gps-status-tile__fact-value">${row.value}</dd>
            </div>
        `).join('');
    }

    function renderCards(data) {
        renderFacts(els.navigation, [
            { label: 'Latitude', value: formatCoord(data.latitude, 'lat') },
            { label: 'Longitude', value: formatCoord(data.longitude, 'lng') },
            { label: 'Course', value: formatNumber(data.course_deg, 1, '°') },
            { label: 'Speed', value: formatNumber(data.speed_kmh, 1, ' km/h') },
            { label: 'Altitude', value: formatNumber(data.altitude_m, 1, ' m') },
        ]);

        renderFacts(els.clock, [
            { label: 'Local', value: data.local_time || '—' },
            { label: 'UTC', value: data.utc_time || '—' },
            { label: 'Date', value: data.date || '—' },
            { label: 'Sunrise', value: data.sunrise || '—' },
            { label: 'Sunset', value: data.sunset || '—' },
        ]);
    }

    function renderRaw(data) {
        if (!els.raw) return;

        if (data.hardware_missing) {
            els.raw.textContent = 'No GPS receiver — using fallback coordinates from [gps] config';
            return;
        }

        const lines = data.raw_sentences || [];
        els.raw.textContent = lines.length ? lines.join('\n') : '—';
    }

    function updateSimulateControls() {
        els.segments.forEach((segment) => {
            const mode = segment.dataset.gpsForce;
            const selected = state.forceMode === mode;
            segment.classList.toggle('is-selected', selected);
            segment.setAttribute('aria-pressed', String(selected));
        });

        if (els.clearBtn) {
            els.clearBtn.hidden = state.forceMode === null;
        }
    }

    function render() {
        const data = getDisplayData();
        renderSummary(data);
        renderMetrics(data);
        renderCards(data);
        renderRaw(data);
        updateSimulateControls();
    }

    function setForceMode(mode) {
        state.forceMode = mode;
        render();

        const socket = getSocket();
        if (!socket?.connected) return;

        if (mode === 'no_fix') {
            socket.emit('set_gps_simulation', { no_fix: true, no_hardware: false });
        } else if (mode === 'no_hardware') {
            socket.emit('set_gps_simulation', { no_hardware: true, no_fix: false });
        } else if (mode === null) {
            socket.emit('set_gps_simulation', { no_fix: false, no_hardware: false });
        }
    }

    function onGpsUpdate(data) {
        if (!data || state.forceMode === 'no_hardware') return;
        state.liveData = data;
        state.backendConnected = true;
        if (state.forceMode !== 'no_fix') {
            render();
        }
    }

    function bindControls() {
        els.segments.forEach((segment) => {
            segment.addEventListener('click', () => {
                const mode = segment.dataset.gpsForce;
                if (!mode) return;
                setForceMode(mode);
            });
        });

        els.clearBtn?.addEventListener('click', () => setForceMode(null));
    }

    async function fetchGps() {
        if (!window.SCCS?.isSystemTabActive) return;
        try {
            const res = await fetch('/api/gps', { cache: 'no-store' });
            if (!res.ok) return;
            onGpsUpdate(await res.json());
        } catch {
            /* socket will deliver gps_update */
        }
    }

    const GPS_POLL_MS = 5000;

    async function pollGps() {
        if (!window.SCCS?.isSystemTabActive) return;
        await fetchGps();
    }

    bindControls();
    render();
    setInterval(pollGps, GPS_POLL_MS);

    window.SCCS = window.SCCS || {};
    window.SCCS.gpsStatus = {
        onGpsUpdate,
        refresh: fetchGps,
        setForceMode,
        clearForce: () => setForceMode(null),
        getState: () => ({ ...getDisplayData(), forceMode: state.forceMode }),
    };

    window.gpsStatusTile = window.SCCS.gpsStatus;
})();