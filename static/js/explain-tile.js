/**
 * SCCS Policy explain tile — why each light/relay is at its current level.
 */
(function () {
    'use strict';

    const tile = document.getElementById('tile-explain');
    const headlineEl = document.getElementById('explain-headline');
    const detailEl = document.getElementById('explain-detail');
    const lightsEl = document.getElementById('explain-lights');
    const driftsEl = document.getElementById('explain-drifts');
    if (!tile || !headlineEl || !detailEl || !lightsEl || !driftsEl) return;

    const POLL_INTERVAL_MS = 10000;
    let lastPayload = null;

    function formatPercent(value) {
        if (value == null) return '—';
        return `${Math.round(Number(value))}%`;
    }

    function formatObserved(entry) {
        if (entry.ramping) return 'ramping';
        const observed = entry.observed_brightness;
        if (observed == null) {
            const desired = entry.desired_brightness;
            if (desired == null) return '—';
            return formatPercent(desired);
        }
        return formatPercent(observed);
    }

    function displayName(name, entry) {
        return entry?.label || name;
    }

    function renderLightRow(name, entry) {
        const label = displayName(name, entry);
        const desired = formatPercent(entry.desired_brightness);
        const observed = formatObserved(entry);
        const mode = entry.desired_mode && entry.desired_mode !== 'white'
            ? ` · ${entry.desired_mode}`
            : '';
        const rowClass = entry.drift
            ? ' explain-tile__row--drift'
            : (entry.ramping ? ' explain-tile__row--ramping' : '');

        return `
            <div class="explain-tile__row${rowClass}">
                <div class="explain-tile__row-main">
                    <span class="explain-tile__name">${label}</span>
                    <span class="explain-tile__levels">${desired}${mode} → ${observed}</span>
                </div>
                <span class="explain-tile__source">${entry.source_label || entry.source || '—'}</span>
            </div>`;
    }

    function renderRelayRow(name, entry) {
        const label = displayName(name, entry);
        const desired = entry.desired ? 'ON' : 'OFF';
        const observed = entry.observed == null ? '—' : (entry.observed ? 'ON' : 'OFF');
        const rowClass = entry.drift
            ? ' explain-tile__row--drift'
            : (entry.ramping ? ' explain-tile__row--ramping' : '');

        return `
            <div class="explain-tile__row${rowClass}">
                <div class="explain-tile__row-main">
                    <span class="explain-tile__name">${label}</span>
                    <span class="explain-tile__levels">${desired} → ${observed}</span>
                </div>
                <span class="explain-tile__source">relay</span>
            </div>`;
    }

    function renderDrifts(drifts) {
        if (!drifts?.length) {
            driftsEl.hidden = true;
            driftsEl.innerHTML = '';
            return;
        }

        driftsEl.hidden = false;
        driftsEl.innerHTML = `
            <p class="explain-tile__drifts-title">Hardware drift</p>
            <ul class="explain-tile__drifts-list">
                ${drifts.map((item) => {
                    const label = item.label || item.light || item.relay || 'output';
                    return `<li><strong>${label}</strong> — ${item.detail || 'mismatch'}</li>`;
                }).join('')}
            </ul>`;
    }

    function render(data) {
        if (!data) return;
        lastPayload = data;

        const phase = data.phase || '—';
        const scene = data.active_scene ? ` · scene ${data.active_scene}` : '';
        const forced = data.phase_forced ? ' · phase forced' : '';
        headlineEl.textContent = `${phase}${scene}${forced}`;

        const driftCount = Array.isArray(data.drifts) ? data.drifts.length : 0;
        const rampCount = Object.values(data.lights || {}).filter((e) => e.ramping).length
            + Object.values(data.relays || {}).filter((e) => e.ramping).length;
        const trigger = data.last_reconcile_trigger || 'unknown';

        if (driftCount) {
            detailEl.textContent = `${driftCount} drift${driftCount === 1 ? '' : 's'} · trigger ${trigger}`;
            detailEl.classList.add('is-warning');
        } else if (rampCount) {
            detailEl.textContent = `Ramping ${rampCount} output${rampCount === 1 ? '' : 's'} · trigger ${trigger}`;
            detailEl.classList.remove('is-warning');
        } else {
            detailEl.textContent = `Aligned · trigger ${trigger}`;
            detailEl.classList.remove('is-warning');
        }

        const lightRows = Object.entries(data.lights || {})
            .sort(([, a], [, b]) => displayName('', a).localeCompare(displayName('', b)))
            .map(([name, entry]) => renderLightRow(name, entry))
            .join('');

        const relayRows = Object.entries(data.relays || {})
            .sort(([, a], [, b]) => displayName('', a).localeCompare(displayName('', b)))
            .map(([name, entry]) => renderRelayRow(name, entry))
            .join('');

        lightsEl.innerHTML = lightRows + relayRows || '<p class="explain-tile__empty">No outputs configured</p>';
        renderDrifts(data.drifts || []);
    }

    async function refresh() {
        const tile = document.getElementById('tile-explain');
        if (!tile || tile.closest('.page-section')?.hidden) return;
        try {
            const res = await fetch('/api/explain', { cache: 'no-store' });
            if (!res.ok) return;
            render(await res.json());
        } catch {
            /* keep last values */
        }
    }

    setInterval(refresh, POLL_INTERVAL_MS);

    window.SCCS = window.SCCS || {};
    window.SCCS.explain = { refresh, render, getLast: () => lastPayload };
})();