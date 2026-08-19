/**
 * SCCS System tab — Google Home enable, pairing QR, reset.
 */
(function () {
    'use strict';

    const headlineEl = document.getElementById('matter-headline');
    const detailEl = document.getElementById('matter-detail');
    const pairEl = document.getElementById('matter-pair');
    const qrEl = document.getElementById('matter-qr');
    const pinEl = document.getElementById('matter-pin');
    const enableBtn = document.getElementById('matter-enable-btn');
    const disableBtn = document.getElementById('matter-disable-btn');
    const resetBtn = document.getElementById('matter-reset-btn');
    const confirmBtn = document.getElementById('matter-reset-confirm');
    const cancelBtn = document.getElementById('matter-reset-cancel');

    if (
        !headlineEl || !detailEl || !pairEl || !qrEl || !pinEl ||
        !enableBtn || !disableBtn || !resetBtn || !confirmBtn || !cancelBtn
    ) {
        return;
    }

    const tile = document.getElementById('tile-matter-system');
    const POLL_MS = 8000;
    let confirming = false;
    let busy = false;

    function setConfirming(on) {
        confirming = on;
        resetBtn.hidden = on || resetBtn.hidden;
        confirmBtn.hidden = !on;
        cancelBtn.hidden = !on;
    }

    function render(data) {
        const enabled = Boolean(data && data.enabled);
        const running = Boolean(data && data.running);
        const paired = Boolean(data && data.paired);
        const error = (data && data.error) || '';
        const pin = (data && data.pin) || '';
        const svg = (data && data.qr_svg) || '';
        const name = (data && data.name) || 'SCCS';
        const count = Number(data && data.accessory_count) || 0;

        if (tile) tile.hidden = !enabled;

        detailEl.classList.toggle('is-warning', Boolean(error) && enabled);
        enableBtn.hidden = true;
        disableBtn.hidden = !enabled || confirming;

        if (!enabled) {
            pairEl.hidden = true;
            resetBtn.hidden = true;
            setConfirming(false);
            return;
        }

        if (error && error !== 'starting') {
            if (error === 'LAN disconnected') {
                headlineEl.textContent = 'Waiting for LAN connection';
                detailEl.textContent = '';
            } else {
                headlineEl.textContent = 'Google Home unavailable';
                detailEl.textContent = error;
            }
            pairEl.hidden = true;
            resetBtn.hidden = true;
            setConfirming(false);
            return;
        }

        if (!running) {
            headlineEl.textContent = 'Starting Google Home…';
            detailEl.textContent = `${name} · ${count || '—'} accessories`;
            pairEl.hidden = true;
            resetBtn.hidden = true;
            return;
        }

        if (paired) {
            headlineEl.textContent = 'Connected to Google Home';
            detailEl.textContent = `${name} · ${count} accessories on van Wi‑Fi`;
            pairEl.hidden = true;
            resetBtn.hidden = confirming;
            if (!confirming) {
                confirmBtn.hidden = true;
                cancelBtn.hidden = true;
            }
            return;
        }

        headlineEl.textContent = 'Waiting to Pair';
        detailEl.textContent = `${name} · ${count} accessories`;
        pairEl.hidden = false;
        pinEl.textContent = pin || '—';
        if (svg && qrEl.innerHTML !== svg) {
            qrEl.innerHTML = svg;
        }
        resetBtn.hidden = confirming;
        if (!confirming) {
            confirmBtn.hidden = true;
            cancelBtn.hidden = true;
        }
    }

    async function refresh() {
        try {
            const res = await fetch('/api/matter', { cache: 'no-store' });
            const data = await res.json();
            render(data);
        } catch (err) {
            headlineEl.textContent = 'Google Home unavailable';
            detailEl.textContent = 'Could not load status';
            detailEl.classList.add('is-warning');
        }
    }

    async function setEnabled(on) {
        if (busy) return;
        busy = true;
        enableBtn.disabled = true;
        disableBtn.disabled = true;
        try {
            const res = await fetch('/api/matter', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled: on }),
            });
            const data = await res.json();
            render(data);
        } catch (err) {
            detailEl.textContent = on ? 'Could not enable' : 'Could not disable';
            detailEl.classList.add('is-warning');
        } finally {
            busy = false;
            enableBtn.disabled = false;
            disableBtn.disabled = false;
        }
    }

    enableBtn.addEventListener('click', () => setEnabled(true));
    disableBtn.addEventListener('click', () => setEnabled(false));

    resetBtn.addEventListener('click', () => {
        if (busy) return;
        setConfirming(true);
        resetBtn.hidden = true;
        disableBtn.hidden = true;
    });

    cancelBtn.addEventListener('click', () => {
        if (busy) return;
        setConfirming(false);
        refresh();
    });

    confirmBtn.addEventListener('click', async () => {
        if (busy) return;
        busy = true;
        confirmBtn.disabled = true;
        try {
            const res = await fetch('/api/matter/reset', { method: 'POST' });
            const data = await res.json();
            setConfirming(false);
            render(data);
        } catch (err) {
            detailEl.textContent = 'Reset failed';
            detailEl.classList.add('is-warning');
        } finally {
            busy = false;
            confirmBtn.disabled = false;
        }
    });

    refresh();
    setInterval(refresh, POLL_MS);
}());
