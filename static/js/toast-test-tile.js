/**
 * SCCS Toast test tile — preview composer + server broadcast via socket.
 */
(function () {
    'use strict';

    const TYPE_ICONS = {
        info: 'fa-circle-info',
        success: 'fa-circle-check',
        warning: 'fa-triangle-exclamation',
        error: 'fa-circle-xmark',
    };

    const tile = document.getElementById('tile-toast-test');
    const detailEl = document.getElementById('toast-test-detail');
    const previewEl = document.getElementById('toast-test-preview');
    const previewIconEl = document.getElementById('toast-test-preview-icon');
    const previewTitleEl = document.getElementById('toast-test-preview-title');
    const previewMessageEl = document.getElementById('toast-test-preview-message');
    const titleInput = document.getElementById('toast-test-title');
    const messageInput = document.getElementById('toast-test-message');
    const stickyInput = document.getElementById('toast-test-sticky');
    const form = document.getElementById('toast-test-form');
    const typeButtons = tile ? tile.querySelectorAll('[data-toast-type]') : [];

    if (!tile || !detailEl || !previewEl || !titleInput || !messageInput || !form) return;

    const state = {
        type: 'info',
        sentCount: 0,
    };

    function updateStats() {
        const active = window.sccsToasts?.getActiveCount?.() ?? 0;
        detailEl.textContent = `${active} active · ${state.sentCount} sent`;
    }

    function getTitle() {
        return titleInput.value.trim() || 'Camper Control';
    }

    function getMessage() {
        return messageInput.value.trim() || 'Everything is working perfectly.';
    }

    function updatePreview() {
        const type = state.type;
        previewEl.className = `toast-test-tile__preview is-${type}`;
        previewIconEl.className = `fa-solid ${TYPE_ICONS[type]} toast-test-tile__preview-icon`;
        previewTitleEl.textContent = getTitle();
        previewMessageEl.textContent = getMessage();
    }

    function setType(type) {
        if (!Object.prototype.hasOwnProperty.call(TYPE_ICONS, type)) return;
        state.type = type;
        typeButtons.forEach((button) => {
            button.classList.toggle('is-selected', button.dataset.toastType === type);
            button.setAttribute('aria-pressed', button.dataset.toastType === type ? 'true' : 'false');
        });
        updatePreview();
    }

    function sendToast() {
        const payload = {
            title: getTitle(),
            message: getMessage(),
            type: state.type,
            duration: 5500,
            persistent: stickyInput.checked,
        };

        const socket = window.SCCS?.socket;
        if (socket?.connected) {
            socket.emit('toast_test', payload);
        } else if (window.sccsToasts?.create) {
            window.sccsToasts.create(payload);
        }

        state.sentCount += 1;
        updateStats();
        setTimeout(updateStats, 50);
    }

    typeButtons.forEach((button) => {
        button.addEventListener('click', () => setType(button.dataset.toastType));
    });

    titleInput.addEventListener('input', updatePreview);
    messageInput.addEventListener('input', updatePreview);

    form.addEventListener('submit', (event) => {
        event.preventDefault();
        sendToast();
    });

    setType('info');
    updateStats();
    setInterval(updateStats, 800);

    window.toastTestTile = {
        sendToast,
        setType,
        getState: () => ({
            type: state.type,
            sentCount: state.sentCount,
            title: getTitle(),
            message: getMessage(),
            sticky: stickyInput.checked,
        }),
        refresh: updateStats,
    };
})();