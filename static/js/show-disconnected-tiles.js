/**
 * SCCS "Show tiles with disconnected hardware" preference.
 * Local-only (per-device) toggle — no server sync.
 */
(function () {
    'use strict';

    const STORAGE_KEY = 'sccs-show-disconnected-tiles';
    const SELECTOR = '[data-show-disconnected-toggle]';

    function getStored() {
        try {
            return localStorage.getItem(STORAGE_KEY) === 'true';
        } catch {
            return false;
        }
    }

    function persist(value) {
        try {
            localStorage.setItem(STORAGE_KEY, value ? 'true' : 'false');
        } catch {
            /* ignore quota / private browsing */
        }
    }

    function updateCheckboxes(value) {
        document.querySelectorAll(SELECTOR).forEach((checkbox) => {
            checkbox.checked = value;
        });
    }

    function apply(value, { persist: shouldPersist = true } = {}) {
        updateCheckboxes(value);
        if (shouldPersist) persist(value);
        window.SCCS?.homeModuleVisibility?.setShowDisconnected?.(value);
        window.SCCS?.lighting?.setShowDisconnected?.(value);
        window.SCCS?.scenes?.setShowDisconnected?.(value);
    }

    function bindCheckboxes() {
        document.querySelectorAll(SELECTOR).forEach((checkbox) => {
            checkbox.addEventListener('change', () => {
                apply(checkbox.checked);
            });
        });
    }

    function init() {
        const value = getStored();
        bindCheckboxes();
        apply(value, { persist: false });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
