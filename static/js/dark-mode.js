/**
 * SCCS Dark / Light Mode
 * Local preference + server sync via global_dark_mode_update.
 */
(function () {
    'use strict';

    const STORAGE_KEY = 'sccs-color-mode';
    const DEFAULT_MODE = 'dark';

    function getSocket() {
        return window.SCCS?.socket ?? null;
    }

    function getStoredMode() {
        try {
            const stored = localStorage.getItem(STORAGE_KEY);
            return stored === 'light' ? 'light' : DEFAULT_MODE;
        } catch {
            return DEFAULT_MODE;
        }
    }

    function updateActiveButtons(mode) {
        document.querySelectorAll('.mode-btn[data-mode]').forEach((button) => {
            button.classList.toggle('active', button.dataset.mode === mode);
        });
    }

    function applyMode(mode) {
        const html = document.documentElement;
        html.classList.remove('dark', 'light');
        html.classList.add(mode);
        updateActiveButtons(mode);
    }

    function persistMode(mode) {
        try {
            localStorage.setItem(STORAGE_KEY, mode);
        } catch {
            /* ignore quota / private browsing */
        }
    }

    function setDarkMode(mode) {
        const nextMode = mode === 'light' ? 'light' : DEFAULT_MODE;
        applyMode(nextMode);
        persistMode(nextMode);

        const socket = getSocket();
        if (socket?.connected) {
            socket.emit('set_global_dark_mode', { mode: nextMode });
        }
    }

    function applyFromServer(data) {
        if (!data?.mode) return;
        applyMode(data.mode);
        persistMode(data.mode);
    }

    function bindButtons() {
        document.querySelectorAll('.mode-btn[data-mode]').forEach((button) => {
            button.addEventListener('click', () => {
                setDarkMode(button.dataset.mode);
            });
        });
    }

    function registerSocket(socket) {
        if (!socket) return;
        socket.on('global_dark_mode_update', applyFromServer);
    }

    async function loadFromServer() {
        try {
            const res = await fetch('/api/dark-mode', { cache: 'no-store' });
            if (!res.ok) return;
            const data = await res.json();
            if (data?.mode) {
                applyFromServer(data);
            }
        } catch {
            /* socket will deliver global_dark_mode_update */
        }
    }

    function init() {
        const mode = getStoredMode();
        applyMode(mode);
        bindButtons();
        updateActiveButtons(mode);
        loadFromServer();

        const socket = getSocket();
        if (socket) {
            registerSocket(socket);
        } else {
            document.addEventListener('sccs:socket-ready', (event) => {
                registerSocket(event.detail?.socket);
            }, { once: true });
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    window.setDarkMode = setDarkMode;
    window.colorMode = {
        applyMode,
        setDarkMode,
        getStoredMode,
        applyFromServer,
        registerSocket,
        refresh: loadFromServer,
    };
})();