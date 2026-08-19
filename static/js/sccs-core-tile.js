/**
 * SCCS System tab — consolidated SCCS core information.
 */
(function () {
    'use strict';

    const POLL_INTERVAL_MS = 5000;
    const TEMP_BAR_MAX_C = 85;

    const MODULE_META = {
        mppt: { label: 'Solar', icon: 'fa-solar-panel' },
        shunt: { label: 'Shunt', icon: 'fa-battery-half' },
        esp32: { label: 'Lighting', icon: 'fa-microchip' },
        gps: { label: 'GPS', icon: 'fa-satellite-dish' },
    };

    const headlineEl = document.getElementById('sccs-core-headline');
    const detailEl = document.getElementById('sccs-core-detail');
    const metricsEl = document.getElementById('sccs-core-metrics');
    const modulesEl = document.getElementById('sccs-core-modules');
    const thermalEl = document.getElementById('sccs-core-thermal');
    const resourcesEl = document.getElementById('sccs-core-resources');
    const platformEl = document.getElementById('sccs-core-platform');

    if (!headlineEl || !metricsEl || !modulesEl || !thermalEl || !resourcesEl || !platformEl) return;

    function formatPercent(value) {
        if (value == null || Number.isNaN(value)) return '—';
        return `${Number(value).toFixed(1)}%`;
    }

    function formatTemp(value) {
        if (value == null || Number.isNaN(value)) return '—';
        return `${Number(value).toFixed(1)}°C`;
    }

    function mergeView(data) {
        const core = data.core || {};
        const host = data.host || {};
        return {
            ...core,
            core_temp_c: core.core_temp_c ?? host.core_temp_c,
            cpu_percent: host.cpu_percent ?? core.cpu_percent,
            memory_percent: host.memory_percent ?? core.memory_percent,
            uptime_human: core.uptime_human ?? host.uptime_human,
            uptime_s: core.uptime_s ?? host.uptime_s,
            source: data.source ?? core.source ?? host.source,
        };
    }

    function tempMeterClass(value) {
        if (value == null || Number.isNaN(value)) return '';
        if (value >= 75) return 'is-hot';
        if (value >= 60) return 'is-warm';
        return '';
    }

    function meterRow(label, value, options = {}) {
        const {
            suffix = '%',
            max = 100,
            detail = '',
            modifier = '',
        } = options;

        const numeric = value == null || Number.isNaN(value) ? null : Number(value);
        const pct = numeric == null ? 0 : Math.max(0, Math.min(100, (numeric / max) * 100));
        const text = numeric == null ? '—' : `${numeric.toFixed(suffix === '°C' ? 1 : 1)}${suffix}`;

        return `
            <div class="sccs-core-tile__meter${modifier ? ` ${modifier}` : ''}">
                <div class="sccs-core-tile__meter-head">
                    <span class="sccs-core-tile__meter-label">${label}</span>
                    <span class="sccs-core-tile__meter-value">${text}</span>
                </div>
                <div class="sccs-core-tile__meter-track" aria-hidden="true">
                    <div class="sccs-core-tile__meter-fill" style="width:${pct}%"></div>
                </div>
                ${detail ? `<p class="sccs-core-tile__meter-detail">${detail}</p>` : ''}
            </div>`;
    }

    function statRow(label, value, options = {}) {
        const warning = options.warning ? ' is-warning' : '';
        const mono = options.mono ? ' is-mono' : '';
        return `
            <div class="sccs-core-tile__stat">
                <dt class="sccs-core-tile__stat-label">${label}</dt>
                <dd class="sccs-core-tile__stat-value${warning}${mono}">${value ?? '—'}</dd>
            </div>`;
    }

    /**
     * If every module the SCCS Core PCB hosts (solar, shunt, lighting, GPS) is
     * offline at once, the simplest explanation is the board itself is
     * unplugged/unpowered — surface that instead of 4 separate "Offline"
     * readings. Broadcast the verdict so other Settings tiles (GPS, Victron)
     * can fold it into their own offline messaging.
     */
    function updateCoreConnectivity(online, total) {
        const connected = total > 0 ? online > 0 : true;
        window.SCCS = window.SCCS || {};
        window.SCCS.core = { online, total, connected };
        document.dispatchEvent(new CustomEvent('sccs:core-connectivity', { detail: window.SCCS.core }));
        return connected;
    }

    function renderSummary(data, view) {
        const online = data.online_count ?? 0;
        const total = data.total_count ?? 0;
        const hostHealthy = view.throttling_ok !== false;
        const throttle = view.throttling_status || 'Unknown';
        const uptime = view.uptime_human || '—';
        const source = view.source ? `${view.source} data` : '—';
        const coreConnected = updateCoreConnectivity(online, total);

        // Only surface Pi status when throttling is active — otherwise it's redundant
        // (the UI is being served from this host).
        const throttleNote = hostHealthy ? null : `Pi: ${throttle}`;

        if (!coreConnected) {
            headlineEl.textContent = `SCCS Core not connected · Up ${uptime}`;
            detailEl.textContent = throttleNote || '';
            detailEl.classList.toggle('is-warning', true);
            return;
        }

        headlineEl.textContent = `SCCS Core connected · Up ${uptime}`;
        detailEl.textContent = [throttleNote, source].filter(Boolean).join(' · ');
        detailEl.classList.toggle('is-warning', !hostHealthy || (total > 0 && online < total));
    }

    function renderMetrics(view) {
        metricsEl.innerHTML = `
            <div class="sccs-core-tile__metric${tempMeterClass(view.core_temp_c) ? ` ${tempMeterClass(view.core_temp_c)}` : ''}">
                <span class="sccs-core-tile__metric-label">CPU temp</span>
                <span class="sccs-core-tile__metric-value">${formatTemp(view.core_temp_c)}</span>
            </div>
            <div class="sccs-core-tile__metric">
                <span class="sccs-core-tile__metric-label">CPU</span>
                <span class="sccs-core-tile__metric-value">${formatPercent(view.cpu_percent)}</span>
            </div>
            <div class="sccs-core-tile__metric">
                <span class="sccs-core-tile__metric-label">Memory</span>
                <span class="sccs-core-tile__metric-value">${formatPercent(view.memory_percent)}</span>
            </div>
            <div class="sccs-core-tile__metric">
                <span class="sccs-core-tile__metric-label">Disk</span>
                <span class="sccs-core-tile__metric-value">${formatPercent(view.disk_percent)}</span>
            </div>`;
    }

    function renderModules(modules) {
        if (!Array.isArray(modules) || modules.length === 0) {
            modulesEl.innerHTML = '<p class="sccs-core-tile__empty">No modules configured</p>';
            return;
        }

        modulesEl.innerHTML = `
            <div class="sccs-core-tile__module-grid" role="list">
                ${modules.map((module) => {
                    const id = module.id || module.module_id;
                    const meta = MODULE_META[id] || { label: module.name || id, icon: 'fa-circle' };
                    const online = module.connected === true;
                    const offline = module.connected === false;
                    const stateClass = online ? 'is-online' : offline ? 'is-offline' : 'is-unknown';
                    const stateText = online ? 'Online' : offline ? 'Offline' : '—';
                    return `
                        <div class="sccs-core-tile__module ${stateClass}" role="listitem">
                            <i class="fa-solid ${meta.icon} sccs-core-tile__module-icon" aria-hidden="true"></i>
                            <span class="sccs-core-tile__module-name">${module.name || meta.label}</span>
                            <span class="sccs-core-tile__module-state">${stateText}</span>
                        </div>`;
                }).join('')}
            </div>`;
    }

    function renderThermal(view) {
        const tempClass = tempMeterClass(view.core_temp_c);
        thermalEl.innerHTML = [
            meterRow('CPU temp', view.core_temp_c, {
                suffix: '°C',
                max: TEMP_BAR_MAX_C,
                modifier: tempClass,
                detail: view.throttling_status
                    ? `Pi throttle: ${view.throttling_status}${view.throttling_raw ? ` (${view.throttling_raw})` : ''}`
                    : '',
            }),
            meterRow('CPU usage', view.cpu_percent),
            statRow('Load average', view.load_avg, { mono: true }),
            statRow('Uptime', view.uptime_human),
        ].join('');
    }

    function renderResources(view) {
        const memoryDetail = view.memory_used_mb != null && view.memory_total_mb != null
            ? `${view.memory_used_mb} / ${view.memory_total_mb} MB`
            : '';
        const diskDetail = view.disk_used_gb != null && view.disk_total_gb != null
            ? `${view.disk_used_gb} / ${view.disk_total_gb} GB`
            : '';

        resourcesEl.innerHTML = [
            meterRow('Memory', view.memory_percent, { detail: memoryDetail }),
            meterRow('Disk', view.disk_percent, { detail: diskDetail }),
        ].join('');
    }

    function renderPlatform(view) {
        platformEl.innerHTML = `
            <dl class="sccs-core-tile__stats">
                ${statRow('Hostname', view.hostname)}
                ${statRow('Model', view.model)}
                ${statRow('CPU', view.cpu_model)}
                ${statRow('Cores', view.cpu_cores != null && view.cpu_threads != null
                    ? `${view.cpu_cores} physical · ${view.cpu_threads} threads`
                    : view.cpu_cores)}
                ${statRow('OS', view.os)}
                ${statRow('Kernel', view.kernel, { mono: true })}
                ${statRow('Network', view.primary_ip
                    ? `${view.primary_ip}${view.primary_iface ? ` · ${view.primary_iface}` : ''}`
                    : '—')}
                ${statRow('App Version', view.app_version ? `v${view.app_version}` : '—')}
                ${statRow('Python', view.python_version)}
                ${statRow('Flask', view.flask_version)}
                ${statRow('Booted', view.boot_time)}
                ${statRow('Pi throttle', view.throttling_status, { warning: view.throttling_ok === false })}
            </dl>`;
    }

    function update(data) {
        if (!data) return;
        const view = mergeView(data);
        renderSummary(data, view);
        renderMetrics(view);
        renderModules(data.modules);
        renderThermal(view);
        renderResources(view);
        renderPlatform(view);
    }

    async function fetchStatus() {
        if (!window.SCCS?.isSystemTabActive) return;
        try {
            const res = await fetch('/api/system', { cache: 'no-store' });
            if (!res.ok) return;
            update(await res.json());
        } catch {
            /* keep last values */
        }
    }

    setInterval(fetchStatus, POLL_INTERVAL_MS);

    window.sccsCoreTile = { update, refresh: fetchStatus };
})();