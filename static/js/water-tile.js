/**
 * SCCS Water tile — fresh tank level percent + circular gauge.
 */
(function () {
    'use strict';

    /** Above this: normal accent colour. At this level: start orange. */
    const WARN_START_PCT = 50;
    /** At/below this: full red. Between WARN_START and here: orange → red. */
    const WARN_RED_PCT = 20;
    // f59e0b (amber) → ef4444 (status offline red)
    const COLOR_ORANGE = [245, 158, 11];
    const COLOR_RED = [239, 68, 68];
    const RING_RADIUS = 52;
    const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS;

    const tile = document.getElementById('tile-water');
    const els = {
        percent: document.getElementById('water-percent'),
        litres: document.getElementById('water-litres'),
        gauge: document.getElementById('water-gauge'),
        fill: document.getElementById('water-gauge-fill'),
    };

    if (!tile || !els.percent) return;

    let tankCapacityLitres = null;

    function clampPercent(value) {
        const n = Number(value);
        if (Number.isNaN(n)) return null;
        return Math.max(0, Math.min(100, Math.round(n)));
    }

    function formatPercent(value) {
        const pct = clampPercent(value);
        if (pct === null) return '—%';
        return `${pct}%`;
    }

    function formatLitres(pct) {
        if (tankCapacityLitres == null || tankCapacityLitres <= 0 || pct === null) {
            return null;
        }
        return `${Math.round((pct / 100) * tankCapacityLitres)} L`;
    }

    function applyCapacity(capacity) {
        const litres = Number(capacity);
        if (!Number.isFinite(litres) || litres <= 0) return;
        tankCapacityLitres = litres;
    }

    function applyLitres(pct) {
        if (!els.litres) return;

        const text = formatLitres(pct);
        if (!text) {
            els.litres.hidden = true;
            return;
        }

        els.litres.textContent = text;
        els.litres.hidden = false;
    }

    function lerpChannel(a, b, t) {
        return Math.round(a + (b - a) * t);
    }

    function warnColorAt(pct) {
        // t = 0 at WARN_START (orange), t = 1 at WARN_RED (red)
        const span = WARN_START_PCT - WARN_RED_PCT;
        const t = span > 0
            ? Math.min(1, Math.max(0, (WARN_START_PCT - pct) / span))
            : 1;
        const r = lerpChannel(COLOR_ORANGE[0], COLOR_RED[0], t);
        const g = lerpChannel(COLOR_ORANGE[1], COLOR_RED[1], t);
        const b = lerpChannel(COLOR_ORANGE[2], COLOR_RED[2], t);
        return {
            color: `rgb(${r}, ${g}, ${b})`,
            glow: `rgba(${r}, ${g}, ${b}, 0.35)`,
        };
    }

    function applyLevelState(pct) {
        // Clear legacy discrete classes
        tile.classList.remove('is-low', 'is-critical');

        if (pct <= 0 || pct > WARN_START_PCT) {
            tile.classList.remove('is-level-warn');
            tile.style.removeProperty('--water-level-color');
            tile.style.removeProperty('--water-level-glow');
            return;
        }

        const { color, glow } = warnColorAt(pct);
        tile.classList.add('is-level-warn');
        tile.style.setProperty('--water-level-color', color);
        tile.style.setProperty('--water-level-glow', glow);
    }

    function setRingLevel(pct) {
        const offset = RING_CIRCUMFERENCE * (1 - pct / 100);

        if (els.fill) {
            els.fill.style.strokeDasharray = String(RING_CIRCUMFERENCE);
            els.fill.style.strokeDashoffset = String(offset);
            els.fill.style.setProperty('--ring-offset', String(offset));
        }

        if (els.gauge) {
            els.gauge.setAttribute('aria-valuenow', String(pct));
            const litresLabel = formatLitres(pct);
            els.gauge.setAttribute(
                'aria-label',
                litresLabel
                    ? `Fresh water tank level, ${pct} percent, about ${litresLabel}`
                    : 'Fresh water tank level'
            );
        }
    }

    function update(data) {
        if (data?.water_capacity_litres != null) {
            applyCapacity(data.water_capacity_litres);
        }

        const raw =
            data?.water_percent ??
            data?.water_level ??
            data?.tank_percent ??
            data?.percent;
        const pct = clampPercent(raw);
        if (pct === null) return;

        els.percent.textContent = formatPercent(pct);
        applyLitres(pct);
        setRingLevel(pct);
        applyLevelState(pct);
    }

    function onSensorUpdate(data) {
        if (!data) return;
        update(data);
    }

    async function fetchSensors() {
        try {
            const res = await fetch('/api/sensors', { cache: 'no-store' });
            if (!res.ok) return;
            onSensorUpdate(await res.json());
        } catch {
            /* socket will deliver sensor_update */
        }
    }

    const POLL_INTERVAL_MS = 5000;

    fetchSensors();
    setInterval(fetchSensors, POLL_INTERVAL_MS);

    window.SCCS = window.SCCS || {};
    window.SCCS.water = { update, onSensorUpdate, refresh: fetchSensors };

    window.waterTile = window.SCCS.water;
})();