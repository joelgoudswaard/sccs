/**
 * SCCS Shutdown tile — power off the host Pi and configured touchscreens.
 */
(function () {
    'use strict';

    const tile = document.getElementById('tile-shutdown-system');
    const headlineEl = document.getElementById('shutdown-summary-headline');
    const detailEl = document.getElementById('shutdown-summary-detail');
    const targetsEl = document.getElementById('shutdown-targets');
    const startBtn = document.getElementById('shutdown-start-btn');

    const dialogEl = document.getElementById('shutdown-confirm-dialog');
    const dialogTargetsEl = document.getElementById('shutdown-confirm-targets');
    const dialogTitleEl = document.getElementById('shutdown-confirm-title');
    const dialogMessageEl = document.getElementById('shutdown-confirm-message');
    const cancelBtn = document.getElementById('shutdown-confirm-cancel');
    const confirmBtn = document.getElementById('shutdown-confirm-confirm');

    if (
        !tile || !headlineEl || !detailEl || !targetsEl || !startBtn ||
        !dialogEl || !dialogTargetsEl || !dialogTitleEl || !dialogMessageEl || !cancelBtn || !confirmBtn
    ) {
        return;
    }

    const CORE_LABEL = 'SCCS Core';
    const CORE_DETAIL = 'Control system core';

    const state = {
        screens: [],
        shuttingDown: false,
    };

    let lastFocusedEl = null;

    function getTargetItems() {
        return [
            {
                label: CORE_LABEL,
                detail: CORE_DETAIL,
                icon: 'fa-server',
            },
            ...state.screens.map((screen) => ({
                label: screen.label || screen.name,
                detail: screen.online === false ? 'May be unreachable' : 'Touchscreen',
                icon: screen.icon || 'fa-display',
                offline: screen.online === false,
            })),
        ];
    }

    function renderTargetList(container, items) {
        if (!items.length) {
            container.innerHTML = '';
            return;
        }

        container.innerHTML = items.map((item) => `
            <li class="${container === dialogTargetsEl ? 'confirm-dialog__target' : 'shutdown-system-tile__target'}${item.offline ? ' is-offline' : ''}">
                <i class="fa-solid ${item.icon} ${container === dialogTargetsEl ? 'confirm-dialog__target-icon' : 'shutdown-system-tile__target-icon'}" aria-hidden="true"></i>
                <div class="${container === dialogTargetsEl ? 'confirm-dialog__target-copy' : 'shutdown-system-tile__target-copy'}">
                    <span class="${container === dialogTargetsEl ? 'confirm-dialog__target-label' : 'shutdown-system-tile__target-label'}">${item.label}</span>
                    <span class="${container === dialogTargetsEl ? 'confirm-dialog__target-detail' : 'shutdown-system-tile__target-detail'}">${item.detail}</span>
                </div>
            </li>`).join('');
    }

    function renderTargets() {
        renderTargetList(targetsEl, getTargetItems());
    }

    function renderSummary() {
        const screenCount = state.screens.length;
        headlineEl.textContent = screenCount
            ? `Power off ${screenCount + 1} devices`
            : 'Shutdown SCCS';

        if (state.shuttingDown) {
            detailEl.textContent = 'Shutdown in progress…';
            detailEl.classList.remove('is-warning');
            return;
        }

        const offlineCount = state.screens.filter((screen) => screen.online === false).length;

        if (offlineCount > 0) {
            detailEl.textContent = offlineCount === 1
                ? '1 touchscreen may be unreachable — host will still shut down.'
                : `${offlineCount} touchscreens may be unreachable — host will still shut down.`;
            detailEl.classList.add('is-warning');
            return;
        }

        detailEl.textContent = screenCount
            ? 'Shuts down SCCS Core and all configured touchscreens.'
            : 'Shuts down the control system core.';
        detailEl.classList.remove('is-warning');
    }

    function updateDialogCopy() {
        const screenCount = state.screens.length;
        dialogTitleEl.textContent = screenCount
            ? `Shut down ${screenCount + 1} devices?`
            : 'Shut down SCCS Core?';
        dialogMessageEl.textContent = screenCount
            ? 'This will power off SCCS Core and all configured touchscreens. This cannot be undone.'
            : 'This will power off the control system core. This cannot be undone.';
        renderTargetList(dialogTargetsEl, getTargetItems());
    }

    function openDialog() {
        if (state.shuttingDown) return;

        lastFocusedEl = document.activeElement;
        updateDialogCopy();

        dialogEl.classList.remove('is-hidden');
        dialogEl.classList.add('is-visible');
        dialogEl.removeAttribute('hidden');
        dialogEl.setAttribute('aria-hidden', 'false');
        document.body.classList.add('has-confirm-dialog');

        confirmBtn.focus();
    }

    function closeDialog({ restoreFocus = true } = {}) {
        dialogEl.classList.remove('is-visible');
        dialogEl.classList.add('is-hidden');
        dialogEl.setAttribute('hidden', '');
        dialogEl.setAttribute('aria-hidden', 'true');
        document.body.classList.remove('has-confirm-dialog');

        if (restoreFocus && lastFocusedEl && typeof lastFocusedEl.focus === 'function') {
            lastFocusedEl.focus();
        }
    }

    function setDialogBusy(busy) {
        cancelBtn.disabled = busy;
        confirmBtn.disabled = busy;
        startBtn.disabled = busy;
        tile.classList.toggle('is-shutting-down', busy);
    }

    async function loadTargets() {
        if (!window.SCCS?.isSystemTabActive) return;

        try {
            const screensRes = await fetch('/api/screens/status', { cache: 'no-store' });

            if (screensRes.ok) {
                const data = await screensRes.json();
                state.screens = (data.screens || []).map((screen) => ({ ...screen }));
            }
        } catch (err) {
            console.warn('[SCCS] shutdown tile load failed', err);
        }

        renderSummary();
        renderTargets();
    }

    async function shutdownAll() {
        if (state.shuttingDown) return;

        state.shuttingDown = true;
        setDialogBusy(true);
        closeDialog({ restoreFocus: false });
        renderSummary();

        window.sccsToasts?.create?.({
            type: 'warning',
            title: 'Shutting down',
            message: 'Powering off all devices…',
            duration: 8000,
        });

        try {
            const res = await fetch('/api/system/shutdown', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
            });
            if (!res.ok) {
                throw new Error(`HTTP ${res.status}`);
            }
        } catch (err) {
            console.warn('[SCCS] shutdown request failed', err);
            state.shuttingDown = false;
            setDialogBusy(false);
            renderSummary();
            window.sccsToasts?.create?.({
                type: 'error',
                title: 'Shutdown failed',
                message: 'Could not start shutdown. Check server logs.',
                duration: 6000,
            });
        }
    }

    startBtn.addEventListener('click', openDialog);
    cancelBtn.addEventListener('click', () => closeDialog());
    confirmBtn.addEventListener('click', shutdownAll);

    dialogEl.addEventListener('click', (event) => {
        if (event.target === dialogEl) {
            closeDialog();
        }
    });

    document.addEventListener('keydown', (event) => {
        if (!dialogEl.classList.contains('is-visible')) return;
        if (event.key === 'Escape') {
            event.preventDefault();
            closeDialog();
        }
    });

    window.SCCS = window.SCCS || {};
    window.SCCS.shutdownSystem = {
        loadTargets,
    };
})();