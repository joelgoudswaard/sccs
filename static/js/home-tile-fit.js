/**
 * 1280×800 home tab — lock the three tile rows to the screen so a long
 * lighting list cannot push the page, with ~20px padding on each edge.
 */
(function () {
    'use strict';

    const TARGET_WIDTH = 1280;
    const TARGET_HEIGHT = 800;
    const VIEWPORT_TOLERANCE = 12;
    const PAGE_PADDING = 20;
    const MIN_LAYOUT_WIDTH = 1121; /* 70.0625rem — 6-column home grid */
    const CLASS_NAME = 'home-tile-fit-1280';

    let frameId = 0;
    let gridObserver = null;

    function isTargetViewport() {
        const w = window.innerWidth;
        const h = window.innerHeight;
        return (
            Math.abs(w - TARGET_WIDTH) <= VIEWPORT_TOLERANCE &&
            Math.abs(h - TARGET_HEIGHT) <= VIEWPORT_TOLERANCE
        );
    }

    function isHomeActive() {
        const home = document.getElementById('home');
        return Boolean(home && home.classList.contains('active') && !home.hidden);
    }

    function getGrid() {
        return document.querySelector('#home .tile-grid');
    }

    function clearFit() {
        document.documentElement.classList.remove(CLASS_NAME);
        const grid = getGrid();
        if (grid) grid.style.gridTemplateRows = '';
    }

    function applyFit() {
        const grid = getGrid();
        const header = document.querySelector('.site-header');
        const kiosk =
            isTargetViewport() &&
            isHomeActive() &&
            grid &&
            header &&
            window.innerWidth >= MIN_LAYOUT_WIDTH;

        if (!kiosk) {
            clearFit();
            return;
        }

        document.documentElement.classList.add(CLASS_NAME);

        if (grid.classList.contains('tile-grid--module-reflow')) {
            grid.style.gridTemplateRows = '';
            return;
        }

        const gap = parseFloat(getComputedStyle(grid).rowGap) || 16;
        const available =
            window.innerHeight - header.getBoundingClientRect().height - PAGE_PADDING * 2;
        const rowHeight = Math.floor((available - gap * 2) / 3);
        if (!Number.isFinite(available) || available <= 0 || rowHeight <= 0) return;

        grid.style.gridTemplateRows = `repeat(3, ${rowHeight}px)`;
    }

    function scheduleFit() {
        cancelAnimationFrame(frameId);
        frameId = requestAnimationFrame(() => {
            frameId = requestAnimationFrame(applyFit);
        });
    }

    function bindGridObserver() {
        const grid = getGrid();
        if (!grid || gridObserver) return;

        gridObserver = new ResizeObserver(scheduleFit);
        gridObserver.observe(grid);
    }

    function init() {
        bindGridObserver();
        scheduleFit();

        window.addEventListener('resize', scheduleFit);
        document.addEventListener('sccs:section-change', (event) => {
            if (event.detail?.sectionId === 'home') scheduleFit();
            else clearFit();
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    window.SCCS = window.SCCS || {};
    window.SCCS.homeTileFit = { refresh: scheduleFit, clear: clearFit };
})();