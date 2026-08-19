/**
 * SCCS offline overlay — shown when Socket.IO disconnects or is unavailable.
 */
(function () {
    'use strict';

    const SHOW_DELAY_MS = 800;

    let showTimeout = null;
    let overlayEl = null;
    let blockedEls = [];

    function getOverlay() {
        if (!overlayEl) {
            overlayEl = document.getElementById('offline-overlay');
        }
        return overlayEl;
    }

    function getBlockedElements() {
        if (!blockedEls.length) {
            blockedEls = [
                document.querySelector('.site-header'),
                document.querySelector('main'),
            ].filter(Boolean);
        }
        return blockedEls;
    }

    function setBlocked(interactive) {
        getBlockedElements().forEach((el) => {
            el.style.pointerEvents = interactive ? '' : 'none';
        });
    }

    function show() {
        if (showTimeout) clearTimeout(showTimeout);
        showTimeout = setTimeout(() => {
            showTimeout = null;
            const overlay = getOverlay();
            if (!overlay) return;

            overlay.classList.remove('is-hidden');
            overlay.classList.add('is-visible');
            overlay.removeAttribute('hidden');
            overlay.setAttribute('aria-hidden', 'false');
            setBlocked(false);
        }, SHOW_DELAY_MS);
    }

    function hide() {
        if (showTimeout) {
            clearTimeout(showTimeout);
            showTimeout = null;
        }

        const overlay = getOverlay();
        if (!overlay) return;

        overlay.classList.remove('is-visible');
        overlay.classList.add('is-hidden');
        overlay.setAttribute('hidden', '');
        overlay.setAttribute('aria-hidden', 'true');
        setBlocked(true);
    }

    function register(socket) {
        if (!socket) return;

        socket.on('disconnect', show);
        socket.on('connect_error', show);
    }

    function initNoSocketClient() {
        if (typeof io !== 'undefined') return;
        console.warn('[SCCS] Socket.IO unavailable — showing offline overlay');
        show();
    }

    window.SCCS = window.SCCS || {};
    window.SCCS.offline = { show, hide, register, initNoSocketClient };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initNoSocketClient);
    } else {
        initNoSocketClient();
    }
})();