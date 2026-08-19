/**
 * SCCS toast notifications (frontend-only).
 */
(function () {
    'use strict';

    const TYPE_ICONS = {
        info: 'fa-circle-info',
        success: 'fa-circle-check',
        warning: 'fa-triangle-exclamation',
        error: 'fa-circle-xmark',
    };

    function getContainer() {
        let container = document.getElementById('toast-container');
        if (!container) {
            container = document.createElement('div');
            container.id = 'toast-container';
            document.body.appendChild(container);
        }
        return container;
    }

    function dismissToast(toast) {
        if (!toast || !toast.parentNode) return;

        toast.classList.remove('show');
        toast.addEventListener('transitionend', () => {
            if (toast.parentNode) toast.parentNode.removeChild(toast);
        }, { once: true });
    }

    function createToast(data) {
        const {
            id,
            message = '',
            type = 'info',
            duration = 5500,
            title = '',
            persistent = false,
        } = data || {};

        const safeType = Object.prototype.hasOwnProperty.call(TYPE_ICONS, type) ? type : 'info';
        const container = getContainer();

        const toast = document.createElement('div');
        toast.className = `toast toast-${safeType}`;
        toast.id = id || `toast-${Date.now()}`;

        const icon = document.createElement('i');
        icon.className = `fa-solid ${TYPE_ICONS[safeType]} toast__icon`;
        icon.setAttribute('aria-hidden', 'true');

        const content = document.createElement('div');
        content.className = 'toast__content';

        if (title) {
            const titleEl = document.createElement('p');
            titleEl.className = 'toast__title';
            titleEl.textContent = title;
            content.appendChild(titleEl);
        }

        const messageEl = document.createElement('p');
        messageEl.className = 'toast__message';
        messageEl.textContent = message;
        content.appendChild(messageEl);

        const closeBtn = document.createElement('button');
        closeBtn.type = 'button';
        closeBtn.className = 'toast__close';
        closeBtn.setAttribute('aria-label', 'Dismiss notification');
        closeBtn.innerHTML = '<i class="fa-solid fa-xmark" aria-hidden="true"></i>';

        toast.append(icon, content, closeBtn);
        container.appendChild(toast);

        requestAnimationFrame(() => toast.classList.add('show'));

        closeBtn.addEventListener('click', (event) => {
            event.stopPropagation();
            dismissToast(toast);
        });

        toast.addEventListener('click', (event) => {
            if (event.target.closest('button')) return;
            dismissToast(toast);
        });

        if (!persistent && duration > 0) {
            setTimeout(() => dismissToast(toast), duration);
        }

        return toast;
    }

    function getActiveCount() {
        return getContainer().querySelectorAll('.toast').length;
    }

    function handleServerToast(data) {
        if (!data) return;
        createToast({
            id: data.id,
            title: data.title || '',
            message: data.message || '',
            type: data.type || 'info',
            duration: data.duration ?? 5500,
            persistent: !!data.persistent,
        });
    }

    window.sccsToasts = {
        create: createToast,
        dismiss: dismissToast,
        getActiveCount,
        handleServer: handleServerToast,
    };
})();