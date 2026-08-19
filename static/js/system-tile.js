/**
 * SCCS System tile — module connectivity and host stats.
 */
(function () {
    'use strict';

    const POLL_INTERVAL_MS = 5000;

    const MODULE_IDS = ['mppt', 'shunt', 'esp32', 'gps'];

    const els = {
        modules: Object.fromEntries(
            MODULE_IDS.map((id) => [id, document.getElementById(`system-module-${id}`)])
        ),
        coreTemp: document.getElementById('system-core-temp'),
        uptime: document.getElementById('system-uptime'),
        cpu: document.getElementById('system-cpu'),
        memory: document.getElementById('system-memory'),
    };

    if (!MODULE_IDS.some((id) => els.modules[id]) && !els.coreTemp) return;

    function setModuleState(el, connected) {
        if (!el) return;

        const online = connected === true;
        const offline = connected === false;

        el.classList.remove('is-online', 'is-offline', 'is-unknown');
        if (online) {
            el.classList.add('is-online');
        } else if (offline) {
            el.classList.add('is-offline');
        } else {
            el.classList.add('is-unknown');
        }

        const stateEl = el.querySelector('.system-tile__state');
        if (stateEl) {
            stateEl.textContent = online ? 'Online' : offline ? 'Offline' : '—';
        }
    }

    function formatUptime(seconds) {
        if (seconds == null || Number.isNaN(seconds)) return '—';

        const total = Math.max(0, Math.floor(seconds));
        const days = Math.floor(total / 86400);
        const hours = Math.floor((total % 86400) / 3600);
        const minutes = Math.floor((total % 3600) / 60);

        if (days > 0) return `${days}d ${hours}h`;
        if (hours > 0) return `${hours}h ${minutes}m`;
        return `${minutes}m`;
    }

    function formatTemp(value) {
        if (value == null || Number.isNaN(value)) return '—°C';
        return `${Number(value).toFixed(1)}°C`;
    }

    function formatPercent(value) {
        if (value == null || Number.isNaN(value)) return '—%';
        return `${Number(value).toFixed(1)}%`;
    }

    function updateHost(host) {
        if (!host) return;

        if (els.coreTemp) {
            els.coreTemp.textContent = formatTemp(host.core_temp_c);
        }
        if (els.uptime) {
            els.uptime.textContent = host.uptime_human || formatUptime(host.uptime_s);
        }
        if (els.cpu) {
            els.cpu.textContent = formatPercent(host.cpu_percent);
        }
        if (els.memory) {
            els.memory.textContent = formatPercent(host.memory_percent);
        }
    }

    function update(data) {
        if (!data) return;

        const host = { ...(data.core || {}), ...(data.host || {}) };
        updateHost(host);

        const modules = data.modules ?? data.devices;
        if (Array.isArray(modules)) {
            modules.forEach((module) => {
                const id = module.id ?? module.module_id;
                if (!id) return;

                const connected = module.connected ?? module.online ?? module.status === 'online';

                // Hide ESP-driven lighting controls (and scenes, which depend on them) when the MCU is offline.
                if (id === 'esp32') {
                    window.SCCS?.lighting?.setEsp32Online?.(connected === true);
                    window.SCCS?.scenes?.setEsp32Online?.(connected === true);
                }

                const row = els.modules[id];
                if (!row) return;

                const nameEl = row.querySelector('.system-tile__name');
                if (nameEl && module.name) {
                    nameEl.textContent = module.name;
                }

                setModuleState(row, connected);
            });

            // Hide main-page feature tiles for offline hardware modules.
            window.SCCS?.homeModuleVisibility?.applyModuleStatus?.(modules);
        }
    }

    async function fetchStatus() {
        try {
            const res = await fetch('/api/system', { cache: 'no-store' });
            if (!res.ok) return;
            update(await res.json());
        } catch {
            /* keep last values */
        }
    }

    fetchStatus();
    setInterval(fetchStatus, POLL_INTERVAL_MS);

    window.systemTile = { update, refresh: fetchStatus };
})();