/**
 * SCCS Power tile — battery SoC, voltage, solar current (Victron shunt + MPPT).
 */
(function () {
    'use strict';

    const POLL_INTERVAL_MS = 5000;
    const LOW_THRESHOLD = 25;
    const CRITICAL_THRESHOLD = 10;
    const tile = document.getElementById('tile-power');
    const els = {
        soc: document.getElementById('power-soc'),
        socGauge: document.getElementById('power-soc-gauge'),
        socFill: document.getElementById('power-soc-fill'),
        voltage: document.getElementById('power-voltage'),
        ttg: document.getElementById('power-ttg'),
        solar: document.getElementById('power-solar'),
        current: document.getElementById('power-current'),
        yieldToday: document.getElementById('power-yield-today'),
    };

    if (!tile || !els.soc) return;

    function clampPercent(value) {
        const n = Number(value);
        if (Number.isNaN(n)) return null;
        return Math.max(0, Math.min(100, Math.round(n)));
    }

    function formatSoc(value) {
        const pct = clampPercent(value);
        if (pct === null) return '—%';
        return `${pct}%`;
    }

    function formatTemp(value) {
        if (value === null || value === undefined || Number.isNaN(Number(value))) {
            return '—°C';
        }
        return `${Math.round(Number(value))}°C`;
    }

    function formatVoltageWithTemp(voltage, tempC) {
        const v =
            voltage === null || voltage === undefined || Number.isNaN(Number(voltage))
                ? '— V'
                : `${Number(voltage).toFixed(1)} V`;
        return `${v} / ${formatTemp(tempC)}`;
    }

    function formatCurrent(value) {
        if (value === null || value === undefined || Number.isNaN(Number(value))) {
            return '— A';
        }
        return `${Number(value).toFixed(1)} A`;
    }

    function formatBatteryCurrent(value) {
        if (value === null || value === undefined || Number.isNaN(Number(value))) {
            return '— A';
        }
        const amps = Number(value);
        const sign = amps > 0 ? '+' : '';
        return `${sign}${amps.toFixed(1)} A`;
    }

    const TTG_UNKNOWN = -1;
    const TTG_INFINITE = 2147483647;

    function formatTtg(seconds) {
        if (seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) {
            return '—';
        }
        const total = Math.round(Number(seconds));
        if (total === TTG_UNKNOWN || total >= TTG_INFINITE) return '—';
        if (total <= 0) return '—';

        const days = Math.floor(total / 86400);
        const hours = Math.floor((total % 86400) / 3600);
        const mins = Math.floor((total % 3600) / 60);

        if (days > 0) {
            return hours > 0 ? `${days}d ${hours}h` : `${days}d`;
        }
        if (hours > 0) {
            return mins > 0 ? `${hours}h ${mins}m` : `${hours}h`;
        }
        if (mins > 0) return `${mins}m`;
        return '<1m';
    }

    function formatYieldToday(value) {
        if (value === null || value === undefined || Number.isNaN(Number(value))) {
            return '— kWh';
        }
        return `${Number(value).toFixed(1)} kWh`;
    }

    function applyCurrentState(value) {
        if (!els.current) return;
        els.current.classList.remove('power-tile__meta-value--charging', 'power-tile__meta-value--discharging');
        const amps = Number(value);
        if (Number.isNaN(amps)) return;
        if (amps > 0) {
            els.current.classList.add('power-tile__meta-value--charging');
        } else if (amps < 0) {
            els.current.classList.add('power-tile__meta-value--discharging');
        }
    }

    function applySocState(pct) {
        tile.classList.toggle('is-low', pct > 0 && pct <= LOW_THRESHOLD);
        tile.classList.toggle('is-critical', pct > 0 && pct <= CRITICAL_THRESHOLD);
    }

    function setSoc(pct) {
        const level = clampPercent(pct);
        if (level === null) {
            els.soc.textContent = '—%';
            return;
        }

        els.soc.textContent = formatSoc(level);

        if (els.socFill) {
            els.socFill.style.setProperty('--water-level', `${level}%`);
        }

        if (els.socGauge) {
            els.socGauge.setAttribute('aria-valuenow', String(level));
        }

        applySocState(level);
    }

    function normalizeVictronPayload(data) {
        if (!data) return data;
        const normalized = { ...data };
        if (normalized.battery_current == null) {
            normalized.battery_current = normalized.current_a ?? normalized.current;
        }
        if (normalized.solar_current == null) {
            normalized.solar_current = normalized.solar_current_a ?? normalized.pv_current;
        }
        if (normalized.yield_today == null && normalized.yield_today_kwh != null) {
            normalized.yield_today = normalized.yield_today_kwh;
        }
        if (normalized.time_to_go == null && normalized.time_to_go_mins != null) {
            normalized.time_to_go = Number(normalized.time_to_go_mins) * 60;
        }
        return normalized;
    }

    function update(data) {
        if (!data) return;
        data = normalizeVictronPayload(data);

        const unavailable =
            data.source === 'unavailable' ||
            (data.stale === true && (data.soc == null && data.voltage == null));
        tile.classList.toggle('is-unavailable', unavailable);
        tile.classList.toggle('is-stale', data.stale === true && !unavailable);

        const soc = data.soc ?? data.battery_soc ?? data.state_of_charge;
        if (soc !== undefined) setSoc(soc);

        const voltage = data.voltage ?? data.battery_voltage ?? data.dc_voltage;
        const batteryTemp =
            data.battery_temp_c ??
            data.battery_temp ??
            data.temperature ??
            data.temp_c ??
            null;
        if (els.voltage) {
            els.voltage.textContent = formatVoltageWithTemp(voltage, batteryTemp);
        }

        const ttg = data.time_to_go ?? data.ttg ?? data.time_to_go_seconds;
        if (ttg !== undefined && els.ttg) {
            els.ttg.textContent = formatTtg(ttg);
        }

        const solar =
            data.solar_current ??
            data.pv_current ??
            data.solar_amps ??
            data.mppt_current;
        if (solar !== undefined && els.solar) {
            els.solar.textContent = formatCurrent(solar);
        }

        const batteryCurrent =
            data.battery_current ?? data.current ?? data.dc_current;
        if (batteryCurrent !== undefined && els.current) {
            els.current.textContent = formatBatteryCurrent(batteryCurrent);
            applyCurrentState(batteryCurrent);
        }

        const yieldToday =
            data.yield_today ?? data.solar_yield_today ?? data.today_yield;
        if (yieldToday !== undefined && els.yieldToday) {
            els.yieldToday.textContent = formatYieldToday(yieldToday);
        }
    }

    async function fetchPower() {
        try {
            const res = await fetch('/api/power', { cache: 'no-store' });
            if (!res.ok) return;
            const data = await res.json();
            update(data);
        } catch {
            /* keep last values */
        }
    }

    let lastSocketUpdate = 0;

    function onVictronUpdate(data) {
        lastSocketUpdate = Date.now();
        update(data);
    }

    async function fetchPowerIfNeeded() {
        if (Date.now() - lastSocketUpdate < POLL_INTERVAL_MS) return;
        await fetchPower();
    }

    fetchPower();
    setInterval(fetchPowerIfNeeded, POLL_INTERVAL_MS);

    window.powerTile = { update, refresh: fetchPower, onVictronUpdate };
})();