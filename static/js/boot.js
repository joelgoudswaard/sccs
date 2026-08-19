/**
 * SCCS boot — reveal page after theme/CSS/fonts are ready (prevents FOUC).
 */
(function () {
    'use strict';

    const MAX_WAIT_MS = 1200;
    const FONT_WAIT_MS = 320;
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
            new Promise((resolve) => setTimeout(resolve, FONT_WAIT_MS)),
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

    if (document.readyState === 'complete') {
        init();
    } else {
        window.addEventListener('load', init, { once: true });
    }

    window.addEventListener('resize', fitLogoAlign);

    setTimeout(reveal, MAX_WAIT_MS);

    window.sccsBoot = { reveal, fitLogoAlign };
})();