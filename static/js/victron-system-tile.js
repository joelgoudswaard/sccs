/**
 * SCCS System tab — full Victron BLE telemetry (SmartShunt + MPPT).
 */
(function () {
    'use strict';

    const POLL_INTERVAL_MS = 5000;

    const tile = document.getElementById('tile-victron-system');
    const els = {
        dot: document.getElementById('victron-status-dot'),
        headline: document.getElementById('victron-system-headline'),
        detail: document.getElementById('victron-system-detail'),
        updated: document.getElementById('victron-system-updated'),
        shuntCard: document.getElementById('victron-shunt-card'),
        shuntStatus: document.getElementById('victron-shunt-status'),
        shuntFacts: document.getElementById('victron-shunt-facts'),
        mpptCard: document.getElementById('victron-mppt-card'),
        mpptStatus: document.getElementById('victron-mppt-status'),
        mpptFacts: document.getElementById('victron-mppt-facts'),
    };

    if (!tile || !els.shuntFacts || !els.mpptFacts) return;

    let lastSocketUpdate = 0;

    function formatNumber(value, digits, suffix = '') {
        if (value === null || value === undefined || Number.isNaN(Number(value))) {
            return '—';
        }
        return `${Number(value).toFixed(digits)}${suffix}`;
    }

    function formatSignedAmps(value) {
        if (value === null || value === undefined || Number.isNaN(Number(value))) {
            return '—';
        }
        const amps = Number(value);
        const sign = amps > 0 ? '+' : '';
        return `${sign}${amps.toFixed(2)} A`;
    }

    function formatTtg(mins) {
        if (mins === null || mins === undefined || Number.isNaN(Number(mins))) {
            return '—';
        }
        const total = Math.round(Number(mins));
        if (total <= 0 || total >= 65000) return '—';
        const days = Math.floor(total / 1440);
        const hours = Math.floor((total % 1440) / 60);
        const minutes = total % 60;
        if (days > 0) return hours > 0 ? `${days}d ${hours}h` : `${days}d`;
        if (hours > 0) return minutes > 0 ? `${hours}h ${minutes}m` : `${hours}h`;
        return `${minutes}m`;
    }

    function formatRssi(value) {
        if (value === null || value === undefined || Number.isNaN(Number(value))) {
            return '—';
        }
        return `${Number(value)} dBm`;
    }

    function formatUpdated(iso) {
        if (!iso) return '';
        const date = new Date(iso);
        if (Number.isNaN(date.getTime())) return '';
        return `Updated ${date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`;
    }

    function deviceStatusLabel(device) {
        if (!device?.configured) return 'Not configured';
        if (!device.connected) return 'Waiting…';
        if (device.stale) return 'Stale';
        return 'Live';
    }

    function setDeviceStatus(el, card, device) {
        const label = deviceStatusLabel(device);
        if (el) {
            el.textContent = label;
            el.classList.toggle('is-live', device?.configured && device.connected && !device.stale);
            el.classList.toggle('is-stale', !!device?.stale && !!device?.connected);
            el.classList.toggle('is-off', !device?.configured || !device?.connected);
        }
        if (card) {
            card.classList.toggle('is-stale', !!device?.stale && !!device?.connected);
            card.classList.toggle('is-unconfigured', !device?.configured);
        }
    }

    function displayValue(value) {
        if (value === null || value === undefined || value === '') return '—';
        return String(value);
    }

    function renderFacts(container, rows) {
        if (!container) return;
        container.replaceChildren();
        rows.forEach((row) => {
            const label = row[0];
            const value = row[1];
            const className = row[2];

            const dt = document.createElement('dt');
            dt.className = 'victron-system-tile__fact-label';
            dt.textContent = label;

            const dd = document.createElement('dd');
            dd.className = 'victron-system-tile__fact-value';
            if (className) {
                className.split(/\s+/).forEach((cls) => {
                    if (cls) dd.classList.add(cls);
                });
            }
            dd.textContent = displayValue(value);

            container.append(dt, dd);
        });
    }

    function renderShunt(device) {
        setDeviceStatus(els.shuntStatus, els.shuntCard, device);

        if (!device?.configured) {
            renderFacts(els.shuntFacts, []);
            return;
        }

        renderFacts(els.shuntFacts, [
            ['Device name', device.name],
            ['Model', device.model_name],
            ['MAC address', device.address, 'is-nowrap'],
            ['RSSI', formatRssi(device.rssi), 'is-nowrap'],
            ['State of charge', device.soc != null ? `${formatNumber(device.soc, 1)}%` : '—', 'is-nowrap'],
            ['Voltage', device.voltage != null ? `${formatNumber(device.voltage, 2)} V` : '—', 'is-nowrap'],
            ['Current', formatSignedAmps(device.current), device.current < 0 ? 'is-negative is-nowrap' : device.current > 0 ? 'is-positive is-nowrap' : 'is-nowrap'],
            ['Remaining', formatTtg(device.remaining_mins), 'is-nowrap'],
            ['Consumed Ah', device.consumed_ah != null ? `${formatNumber(device.consumed_ah, 1)} Ah` : '—', 'is-nowrap'],
            ['Temperature', device.temperature != null ? `${formatNumber(device.temperature, 1)} °C` : '—', 'is-nowrap'],
            ['Alarm', device.alarm],
            ['Aux input', device.aux_mode],
            ['Last advert', formatUpdated(device.last_update), 'is-nowrap'],
        ]);
    }

    function renderMppt(device) {
        setDeviceStatus(els.mpptStatus, els.mpptCard, device);

        if (!device?.configured) {
            renderFacts(els.mpptFacts, []);
            return;
        }

        const yieldWh = device.yield_today_wh;
        const yieldLabel =
            yieldWh != null ? `${formatNumber(yieldWh, 0)} Wh (${formatNumber(yieldWh / 1000, 2)} kWh)` : '—';

        renderFacts(els.mpptFacts, [
            ['Device name', device.name],
            ['Model', device.model_name],
            ['MAC address', device.address, 'is-nowrap'],
            ['RSSI', formatRssi(device.rssi), 'is-nowrap'],
            ['Charge state', device.charge_state],
            ['Charger error', device.charger_error],
            ['Solar power', device.solar_power != null ? `${formatNumber(device.solar_power, 0)} W` : '—', 'is-nowrap'],
            ['Battery voltage', device.battery_voltage != null ? `${formatNumber(device.battery_voltage, 2)} V` : '—', 'is-nowrap'],
            ['Charge current', device.battery_charging_current != null ? `${formatNumber(device.battery_charging_current, 2)} A` : '—', 'is-nowrap'],
            ['Yield today', yieldLabel],
            ['Last advert', formatUpdated(device.last_update), 'is-nowrap'],
        ]);
    }

    function renderSummary(data) {
        const shunt = data?.shunt ?? {};
        const mppt = data?.mppt ?? {};
        const anyConfigured = shunt.configured || mppt.configured;
        const anyLive =
            (shunt.configured && shunt.connected && !shunt.stale) ||
            (mppt.configured && mppt.connected && !mppt.stale);

        tile.classList.toggle('is-unconfigured', !anyConfigured);
        tile.classList.toggle('is-live', anyLive);
        tile.classList.toggle('is-stale', !!data?.stale || (anyConfigured && !anyLive));

        if (!anyConfigured) {
            if (els.headline) els.headline.textContent = 'Victron not configured';
            if (els.detail) els.detail.textContent = 'Add device MAC addresses and keys under [victron] in sccs.conf';
        } else if (data?.stale && !anyLive) {
            const coreDisconnected = window.SCCS?.core?.connected === false;
            if (els.headline) {
                els.headline.textContent = coreDisconnected ? 'SCCS Core not connected' : 'Waiting for BLE data';
            }
            if (els.detail) {
                els.detail.textContent = 'Listening for Instant Readout advertisements';
            }
        } else {
            const parts = [];
            if (shunt.configured && shunt.soc != null) {
                parts.push(`${formatNumber(shunt.soc, 1)}% SoC`);
            }
            if (shunt.configured && shunt.current != null) {
                parts.push(formatSignedAmps(shunt.current));
            }
            if (mppt.configured && mppt.solar_power != null) {
                parts.push(`${formatNumber(mppt.solar_power, 0)} W solar`);
            }
            if (els.headline) {
                els.headline.textContent = parts.length ? parts.join(' · ') : 'Victron connected';
            }
            if (els.detail) {
                const names = [shunt.name, mppt.name].filter(Boolean);
                els.detail.textContent = names.length ? names.join(' · ') : 'Instant Readout via Bluetooth';
            }
        }

        if (els.updated) {
            els.updated.textContent = formatUpdated(data?.last_update);
        }
    }

    let lastData = null;

    function update(data) {
        if (!data) return;
        lastData = data;
        renderSummary(data);
        renderShunt(data.shunt);
        renderMppt(data.mppt);
    }

    async function fetchVictron() {
        try {
            const res = await fetch('/api/victron', { cache: 'no-store' });
            if (!res.ok) return;
            update(await res.json());
        } catch {
            /* keep last values */
        }
    }

    function onVictronUpdate(data) {
        lastSocketUpdate = Date.now();
        update(data);
    }

    async function fetchIfNeeded() {
        if (Date.now() - lastSocketUpdate < POLL_INTERVAL_MS) return;
        await fetchVictron();
    }

    fetchVictron();
    setInterval(fetchIfNeeded, POLL_INTERVAL_MS);
    document.addEventListener('sccs:core-connectivity', () => {
        if (lastData) renderSummary(lastData);
    });

    window.victronSystemTile = { update, refresh: fetchVictron, onVictronUpdate };
})();