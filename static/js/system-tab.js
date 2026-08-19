/**
 * SCCS System tab — lazy load/refresh when active, and pack tiles into
 * columns so heights stay as even as possible across the page.
 *
 * Packing is deliberately conservative: hover transforms, style/class
 * flicker, and minor resizes must not rebuild the layout (that caused
 * Chrome hover bounce and scroll jumping).
 */
(function () {
    'use strict';

    const SECTION_ID = 'system';
    const PACK_DEBOUNCE_MS = 120;
    /** Ignore ResizeObserver noise right after we move tiles. */
    const PACK_SETTLE_MS = 400;
    /** Only re-pack when a measured height changes by at least this many px. */
    const HEIGHT_CHANGE_PX = 24;

    window.SCCS = window.SCCS || {};
    window.SCCS.isSystemTabActive = false;

    let packTimer = 0;
    let packing = false;
    let lastPackAt = 0;
    let lastColCount = 0;
    /** @type {string} */
    let lastAssignmentKey = '';
    /** @type {Map<string, number>} tile id → last measured height */
    let lastHeights = new Map();
    let resizeObserver = null;

    function isSystemSectionActive() {
        const section = document.getElementById(SECTION_ID);
        return Boolean(
            section &&
            !section.hidden &&
            section.classList.contains('active')
        );
    }

    function getGrid() {
        return document.querySelector('#system .tile-grid');
    }

    /** Match previous CSS breakpoints: 1 / 2 / 3 columns. */
    function columnCount() {
        const w = window.innerWidth;
        if (w <= 45 * 16) return 1; // 45rem
        if (w < 64 * 16) return 2; // 64rem
        return 3;
    }

    function isTileVisible(tile) {
        if (!tile || tile.hidden) return false;
        if (tile.hasAttribute('hidden')) return false;
        const style = window.getComputedStyle(tile);
        if (style.display === 'none' || style.visibility === 'hidden') return false;
        return true;
    }

    function tileKey(tile) {
        return tile.id || tile.dataset.systemPackIndex || '';
    }

    /**
     * Collect system tiles in stable source order (first-seen DOM index).
     * Survives re-packing into column wrappers.
     */
    function gatherTiles(grid) {
        const tiles = Array.from(grid.querySelectorAll('.tile'));
        tiles.forEach((tile, index) => {
            if (tile.dataset.systemPackIndex == null) {
                tile.dataset.systemPackIndex = String(index);
            }
        });
        tiles.sort(
            (a, b) =>
                Number(a.dataset.systemPackIndex) - Number(b.dataset.systemPackIndex),
        );
        return tiles;
    }

    /**
     * Ensure column shell exists for colsN. Avoid tearing down columns when
     * the count is unchanged — only move tiles.
     */
    function ensurePackShell(grid, colsN) {
        let colsRoot = grid.querySelector(':scope > .system-pack-cols');
        if (!colsRoot) {
            colsRoot = document.createElement('div');
            colsRoot.className = 'system-pack-cols';
            const orphanTiles = Array.from(grid.children).filter((el) =>
                el.classList?.contains('tile'),
            );
            grid.appendChild(colsRoot);
            orphanTiles.forEach((t) => colsRoot.appendChild(t));
        }

        let colEls = Array.from(colsRoot.querySelectorAll(':scope > .system-pack-col'));
        if (colEls.length !== colsN) {
            // Column count changed — rebuild columns, keep tiles on colsRoot.
            const allTiles = gatherTiles(grid);
            allTiles.forEach((t) => colsRoot.appendChild(t));
            colEls.forEach((col) => col.remove());
            colEls = [];
            for (let i = 0; i < colsN; i++) {
                const col = document.createElement('div');
                col.className = 'system-pack-col';
                colsRoot.appendChild(col);
                colEls.push(col);
            }
        }

        return { colsRoot, colEls, tiles: gatherTiles(grid) };
    }

    /**
     * Measure height without hover lift affecting layout (transform is ignored
     * for layout, but use offsetHeight which is transform-independent).
     */
    function measureHeight(tile) {
        return tile.offsetHeight || 0;
    }

    function heightsMeaningfullyChanged(measured) {
        if (lastHeights.size === 0) return true;
        for (const { tile, height } of measured) {
            const key = tileKey(tile);
            const prev = lastHeights.get(key);
            if (prev == null || Math.abs(prev - height) >= HEIGHT_CHANGE_PX) {
                return true;
            }
        }
        // Tile appeared/disappeared
        if (measured.length !== lastHeights.size) return true;
        return false;
    }

    /**
     * Pack visible tiles into N columns (LPT onto shortest column).
     * Skips DOM work when the assignment would not change.
     */
    function packSystemTiles({ force = false } = {}) {
        const grid = getGrid();
        if (!grid || !isSystemSectionActive()) return;
        if (packing) return;

        packing = true;
        try {
            const colsN = columnCount();
            const { colEls, tiles } = ensurePackShell(grid, colsN);
            const visible = tiles.filter(isTileVisible);
            const hidden = tiles.filter((t) => !isTileVisible(t));

            hidden.forEach((t) => {
                // Keep hidden tiles out of height math; park in col 0 if needed
                if (colEls[0] && t.parentElement !== colEls[0]) {
                    colEls[0].appendChild(t);
                }
            });

            if (colsN === 1) {
                const key = `1|${visible.map(tileKey).join(',')}`;
                if (!force && key === lastAssignmentKey) {
                    return;
                }
                visible.forEach((t) => {
                    if (t.parentElement !== colEls[0]) colEls[0].appendChild(t);
                });
                lastAssignmentKey = key;
                lastColCount = 1;
                lastHeights = new Map(visible.map((t) => [tileKey(t), measureHeight(t)]));
                lastPackAt = Date.now();
                return;
            }

            // Measure at current column width without restaging every tile into
            // col 0 (that caused flicker). Heights are stable enough across cols.
            const measured = visible.map((tile) => ({
                tile,
                height: measureHeight(tile),
            }));

            if (
                !force &&
                colsN === lastColCount &&
                !heightsMeaningfullyChanged(measured)
            ) {
                return;
            }

            // Longest-processing-time first → more even column heights
            const ordered = measured.slice().sort(
                (a, b) =>
                    b.height - a.height ||
                    Number(a.tile.dataset.systemPackIndex) -
                        Number(b.tile.dataset.systemPackIndex),
            );

            const colHeights = Array(colsN).fill(0);
            const byCol = Array.from({ length: colsN }, () => []);
            ordered.forEach(({ tile, height }) => {
                let best = 0;
                for (let i = 1; i < colsN; i++) {
                    if (colHeights[i] < colHeights[best]) best = i;
                }
                byCol[best].push(tile);
                colHeights[best] += height + 16; // approximate gap
            });

            const key = byCol
                .map((list) => list.map(tileKey).join(','))
                .join('|');

            if (!force && key === lastAssignmentKey && colsN === lastColCount) {
                lastHeights = new Map(measured.map(({ tile, height }) => [tileKey(tile), height]));
                return;
            }

            byCol.forEach((list, i) => {
                list.forEach((tile) => {
                    if (tile.parentElement !== colEls[i]) {
                        colEls[i].appendChild(tile);
                    }
                });
            });

            lastAssignmentKey = key;
            lastColCount = colsN;
            lastHeights = new Map(measured.map(({ tile, height }) => [tileKey(tile), height]));
            lastPackAt = Date.now();
        } finally {
            packing = false;
        }
    }

    function schedulePack(opts = {}) {
        if (!isSystemSectionActive()) return;
        const force = opts.force === true;
        if (!force && packing) return;
        if (!force && Date.now() - lastPackAt < PACK_SETTLE_MS) return;

        window.clearTimeout(packTimer);
        packTimer = window.setTimeout(() => {
            packTimer = 0;
            packSystemTiles({ force });
        }, force ? 0 : PACK_DEBOUNCE_MS);
    }

    function refreshSystemTiles() {
        window.sccsCoreTile?.refresh?.();
        window.SCCS.phases?.refresh?.();
        window.SCCS.gpsStatus?.refresh?.();
        window.SCCS.explain?.refresh?.();
        window.SCCS.reedsSystem?.refresh?.();
        window.SCCS.screensSystem?.loadScreens?.();
        window.SCCS.shutdownSystem?.loadTargets?.();
        window.SCCS.wifi?.refresh?.({ quiet: true });
        window.sonosSystemTile?.fetchStatus?.();
        // Content loads async — force pack after data settles.
        schedulePack({ force: true });
        window.setTimeout(() => schedulePack({ force: true }), 300);
        window.setTimeout(() => schedulePack({ force: true }), 900);
    }

    function onSectionChange(sectionId) {
        const active = sectionId === SECTION_ID;
        window.SCCS.isSystemTabActive = active;
        if (active) {
            lastAssignmentKey = '';
            lastHeights = new Map();
            refreshSystemTiles();
        }
    }

    function initPacking() {
        const grid = getGrid();
        if (!grid) return;

        if (typeof ResizeObserver !== 'undefined') {
            // Observe the grid only — not each tile. Per-tile observation
            // fired on every internal chart/slider reflow and hover noise.
            resizeObserver = new ResizeObserver(() => {
                if (packing) return;
                if (Date.now() - lastPackAt < PACK_SETTLE_MS) return;
                schedulePack();
            });
            resizeObserver.observe(grid);
        }

        window.addEventListener('resize', () => {
            schedulePack({ force: columnCount() !== lastColCount });
        });

        // Only react to tiles being shown/hidden — not class/style thrash
        // from live updates or hover.
        const mo = new MutationObserver((records) => {
            if (packing) return;
            let relevant = false;
            for (const rec of records) {
                if (rec.type === 'attributes' && rec.attributeName === 'hidden') {
                    relevant = true;
                    break;
                }
                if (rec.type === 'childList') {
                    // Ignore our own column moves (tiles into .system-pack-col)
                    const nodes = [...rec.addedNodes, ...rec.removedNodes];
                    if (
                        nodes.some(
                            (n) =>
                                n.nodeType === 1 &&
                                (n.classList?.contains('tile') ||
                                    n.classList?.contains('system-pack-col')),
                        )
                    ) {
                        // Tile add/remove or column rebuild — only force if a
                        // .tile entered/left the document under #system outside
                        // a normal re-parent between columns.
                        const tileChange = nodes.some(
                            (n) => n.nodeType === 1 && n.classList?.contains('tile'),
                        );
                        if (tileChange && Date.now() - lastPackAt > PACK_SETTLE_MS) {
                            relevant = true;
                            break;
                        }
                    }
                }
            }
            if (relevant) schedulePack({ force: true });
        });
        mo.observe(grid, {
            childList: true,
            subtree: true,
            attributes: true,
            attributeFilter: ['hidden'],
        });
    }

    document.addEventListener('sccs:section-change', (event) => {
        onSectionChange(event.detail?.sectionId);
    });

    function boot() {
        initPacking();
        if (isSystemSectionActive()) {
            onSectionChange(SECTION_ID);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }

    window.SCCS.packSystemTiles = () => schedulePack({ force: true });
})();
