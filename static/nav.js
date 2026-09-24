window.SCCS = window.SCCS || {};
window.SCCS.activeSection = window.SCCS.activeSection || 'home';

/** Poll only while this tab is showing, and refresh as soon as it opens. */
window.SCCS.bindSectionPoll = function (sectionId, fn, intervalMs) {
    function runIfActive() {
        if (window.SCCS.activeSection === sectionId) fn();
    }

    runIfActive();
    if (intervalMs > 0) {
        window.setInterval(runIfActive, intervalMs);
    }
    document.addEventListener('sccs:section-activating', (event) => {
        if (event.detail && event.detail.sectionId === sectionId) fn();
    });
};

(function () {
    const nav = document.querySelector('.nav-pill');
    const stage = document.querySelector('.page-stage');
    if (!nav || !stage) return;

    const items = [...nav.querySelectorAll('.nav-pill__item')];
    const indicator = nav.querySelector('.nav-pill__indicator');
    const sections = [...stage.querySelectorAll('.page-section')];
    const fadeMs = parseFloat(getComputedStyle(document.documentElement)
        .getPropertyValue('--duration-page-fade')) * 1000 || 450;

    let switching = false;

    function moveIndicator(item) {
        if (!indicator || !item) return;
        const navRect = nav.getBoundingClientRect();
        const itemRect = item.getBoundingClientRect();
        const x = itemRect.left - navRect.left;
        indicator.style.width = `${itemRect.width}px`;
        indicator.style.transform = `translate3d(${x}px, 0, 0)`;
    }

    function prefersReducedMotion() {
        return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    }

    function afterPaint() {
        return new Promise((resolve) => {
            requestAnimationFrame(() => requestAnimationFrame(resolve));
        });
    }

    function waitForFade(ms) {
        return new Promise((resolve) => setTimeout(resolve, ms));
    }

    function setNavActive(sectionId) {
        window.SCCS.activeSection = sectionId;
        window.SCCS.isHomeTabActive = sectionId === 'home';
        window.SCCS.isSystemTabActive = sectionId === 'system';
        if (sectionId === 'system') {
            document.documentElement.classList.add('fa-brands-ready');
        }
        items.forEach((item) => {
            const isActive = item.dataset.section === sectionId;
            item.classList.toggle('active', isActive);
            item.setAttribute('aria-selected', String(isActive));
        });
        const activeItem = items.find((item) => item.classList.contains('active'));
        moveIndicator(activeItem);
        document.dispatchEvent(new CustomEvent('sccs:section-activating', {
            detail: { sectionId },
        }));
    }

    async function activate(sectionId) {
        if (switching) return;

        const current = sections.find((section) => section.classList.contains('active'));
        const next = document.getElementById(sectionId);
        if (!next || current === next) return;

        switching = true;
        const reduced = prefersReducedMotion();
        next.hidden = false;
        // Paint the incoming page at opacity 0 before .active. A same-turn
        // class change after display:none skips the fade in Chrome.
        if (!reduced) {
            void next.offsetWidth;
            await afterPaint();
        }

        if (current) current.classList.add('is-leaving');
        next.classList.add('active');
        if (current) current.classList.remove('active');
        if (!reduced) {
            if (current) current.classList.add('is-fading');
            next.classList.add('is-fading');
        }
        setNavActive(sectionId);

        await waitForFade(reduced ? 0 : fadeMs);

        if (current) {
            current.classList.remove('is-leaving', 'is-fading');
            current.hidden = true;
        }
        next.classList.remove('is-fading');

        document.dispatchEvent(new CustomEvent('sccs:section-change', {
            detail: { sectionId },
        }));

        switching = false;
    }

    items.forEach((item) => {
        item.addEventListener('click', () => activate(item.dataset.section));
    });

    window.addEventListener('resize', () => {
        const activeItem = items.find((item) => item.classList.contains('active'));
        moveIndicator(activeItem);
    });

    const initial = items.find((item) => item.classList.contains('active')) || items[0];
    setNavActive(initial.dataset.section);
})();