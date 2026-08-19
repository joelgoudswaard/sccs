/**
 * SCCS fullscreen toggle — expand/compress document (from SCCS fullscreen.js).
 */
(function () {
    'use strict';

    const btn = document.getElementById('fullscreen-btn');
    const icon = document.getElementById('fullscreen-icon');
    if (!btn || !icon) return;

    function syncIcon() {
        const expanded = Boolean(document.fullscreenElement);
        icon.classList.toggle('fa-expand', !expanded);
        icon.classList.toggle('fa-compress', expanded);
        btn.setAttribute('aria-label', expanded ? 'Exit fullscreen' : 'Enter fullscreen');
        btn.setAttribute('aria-pressed', String(expanded));
    }

    function toggleFullscreen() {
        if (!document.fullscreenElement) {
            document.documentElement.requestFullscreen().catch((err) => {
                console.warn('[SCCS] Fullscreen failed:', err);
            });
        } else {
            document.exitFullscreen().catch((err) => {
                console.warn('[SCCS] Exit fullscreen failed:', err);
            });
        }
    }

    btn.addEventListener('click', toggleFullscreen);
    document.addEventListener('fullscreenchange', syncIcon);
    syncIcon();
})();