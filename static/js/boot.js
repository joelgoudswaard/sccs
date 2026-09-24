/**
 * SCCS boot — reveal once CSS is in and fonts are ready (or the cap hits).
 * Does not wait for the window load event, which also waits on favicons.
 */
(function () {
    'use strict';

    const MAX_WAIT_MS = 1200;
    let revealed = false;

    function reveal() {
        if (revealed) return;
        revealed = true;
        document.documentElement.classList.add('theme-ready');
    }

    function whenPaintReady() {
        const fontWait =
            document.fonts && typeof document.fonts.ready !== 'undefined'
                ? document.fonts.ready
                : Promise.resolve();

        return Promise.race([
            fontWait,
            new Promise((resolve) => setTimeout(resolve, MAX_WAIT_MS)),
        ]);
    }

    function fitLogoAlign() {
        const logo = document.querySelector('.site-logo');
        const lockup = document.querySelector('.site-title-lockup');
        if (!logo || !lockup) return;

        logo.style.marginTop = '0px';
        logo.style.height = '';
        logo.style.width = '';
        const lockupHeight = lockup.getBoundingClientRect().height;

        if (!lockupHeight) return;

        logo.style.height = `${lockupHeight}px`;
        logo.style.width = `${lockupHeight}px`;
    }

    function init() {
        whenPaintReady().then(() => {
            requestAnimationFrame(() => {
                fitLogoAlign();
                requestAnimationFrame(reveal);
            });
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init, { once: true });
    } else {
        init();
    }

    window.addEventListener('resize', fitLogoAlign);

    setTimeout(reveal, MAX_WAIT_MS);

    window.sccsBoot = { reveal, fitLogoAlign };
})();