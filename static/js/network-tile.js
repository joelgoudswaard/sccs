/**
 * SCCS Network tile — internet status, signal, latency, throughput.
 */
(function () {
    'use strict';

    const tile = document.getElementById('tile-network');
    if (!tile) return;

    const els = {
        statusIcon: tile.querySelector('.network-tile__status-icon'),
        statusText: tile.querySelector('.network-tile__status-text'),
        ifaceName: tile.querySelector('.network-tile__iface-name'),
        signal: tile.querySelector('.network-tile__row--signal .network-tile__row-value'),
        ping: tile.querySelector('.network-tile__ping'),
        speeds: tile.querySelectorAll('.network-tile__speed'),
        linkSpeedRow: tile.querySelector('.network-tile__link-speed-row'),
        linkSpeed: tile.querySelector('.network-tile__link-speed'),
    };

    const POLL_INTERVAL_MS = 8000;

    function setOnline(connected) {
        tile.classList.toggle('is-offline', !connected);
        if (els.statusIcon) {
            els.statusIcon.classList.toggle('is-online', connected);
            els.statusIcon.classList.toggle('fa-globe', connected);
            els.statusIcon.classList.toggle('fa-triangle-exclamation', !connected);
        }
        if (els.statusText) {
            els.statusText.textContent = connected ? 'Online' : 'Offline';
            els.statusText.classList.toggle('is-online', connected);
        }
    }

    function setPing(ms, status) {
        if (!els.ping) return;
        els.ping.classList.remove('is-good', 'is-slow', 'is-bad');
        if (ms == null) {
            els.ping.textContent = '—';
            return;
        }
        els.ping.textContent = `${ms}ms`;
        if (status === 'good') els.ping.classList.add('is-good');
        else if (status === 'slow') els.ping.classList.add('is-slow');
        else els.ping.classList.add('is-bad');
    }

    function update(data) {
        if (!data) return;
        const inet = data.internet || data;

        setOnline(!!inet.connected);

        if (els.ifaceName) {
            els.ifaceName.textContent = inet.friendly_name || inet.ssid || '—';
        }
        if (els.signal) {
            els.signal.textContent = inet.signal_quality || '—';
        }

        setPing(inet.ping_ms, inet.ping_status);

        const speedEls = els.speeds || [];
        if (speedEls[0]) {
            speedEls[0].textContent = inet.rx_kbps != null ? `↓${Math.round(inet.rx_kbps)}` : '↓—';
        }
        if (speedEls[1]) {
            speedEls[1].textContent = inet.tx_kbps != null ? `↑${Math.round(inet.tx_kbps)}` : '↑—';
        }
        const hasLinkSpeed = inet.link_speed_mbps != null && inet.link_speed_mbps > 0;
        if (els.linkSpeedRow) {
            els.linkSpeedRow.hidden = !hasLinkSpeed;
        }
        if (els.linkSpeed) {
            els.linkSpeed.textContent = hasLinkSpeed ? `${inet.link_speed_mbps} Mbps` : '—';
        }
    }

    async function fetchStatus() {
        try {
            const res = await fetch('/api/network', { cache: 'no-store' });
            if (!res.ok) return;
            update(await res.json());
        } catch {
            /* keep last values */
        }
    }

    fetchStatus();
    setInterval(fetchStatus, POLL_INTERVAL_MS);

    window.SCCS = window.SCCS || {};
    window.SCCS.network = { update, refresh: fetchStatus };
})();