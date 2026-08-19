/**
 * Hide home-page tiles whose hardware modules are offline.
 *
 * Module → tile mapping:
 *   gps           → #tile-gps
 *   esp32         → #tile-water, #tile-lighting-home
 *   shunt | mppt  → #tile-power (shown if either is online)
 *   sonos         → #tile-sonos (driven separately via setSonosOnline)
 *
 * Appearance “Show tiles with disconnected hardware” forces all of the above
 * visible, including Sonos when no speakers are found.
 */
(function () {
    'use strict';

    const GRID_SELECTOR = '#home .tile-grid';
    const REFLOW_CLASS = 'tile-grid--module-reflow';
    const HARDWARE_TILES = ['tile-gps', 'tile-water', 'tile-lighting-home', 'tile-power', 'tile-sonos'];

    /** @type {Record<string, boolean|null>} null = not yet known */
    const moduleOnline = {
        gps: null,
        esp32: null,
        shunt: null,
        mppt: null,
        sonos: null,
    };

    function getGrid() {
        return document.querySelector(GRID_SELECTOR);
    }

    function setTileVisible(tileId, visible) {
        const tile = document.getElementById(tileId);
        if (!tile) return false;

        const shouldHide = visible === false;
        if (tile.hidden === shouldHide) return false;

        tile.hidden = shouldHide;
        tile.setAttribute('aria-hidden', shouldHide ? 'true' : 'false');
        return true;
    }

    function anyHardwareTileHidden() {
        return HARDWARE_TILES.some((id) => {
            const el = document.getElementById(id);
            return Boolean(el?.hidden);
        });
    }

    function syncGridReflow() {
        const grid = getGrid();
        if (!grid) return;
        grid.classList.toggle(REFLOW_CLASS, anyHardwareTileHidden());
    }

    function refreshLayout() {
        syncGridReflow();
        window.SCCS?.homeTileFit?.refresh?.();
    }

    /**
     * Resolve visibility for a tile that depends on a single module.
     * unknown (null) keeps the tile visible to avoid flash-on-load.
     */
    function visibleIfOnline(status) {
        return status !== false;
    }

    // User preference (Appearance tile checkbox): show every hardware tile
    // regardless of module status. Toggled/persisted by show-disconnected-tiles.js.
    let showDisconnected = false;

    function setShowDisconnected(value) {
        showDisconnected = value === true;
        applyVisibility();
    }

    function applyVisibility() {
        if (showDisconnected) {
            let forcedChanged = false;
            HARDWARE_TILES.forEach((id) => {
                forcedChanged = setTileVisible(id, true) || forcedChanged;
            });
            if (forcedChanged) refreshLayout();
            else syncGridReflow();
            return;
        }

        let changed = false;

        changed = setTileVisible('tile-gps', visibleIfOnline(moduleOnline.gps)) || changed;
        changed = setTileVisible('tile-water', visibleIfOnline(moduleOnline.esp32)) || changed;
        changed = setTileVisible('tile-lighting-home', visibleIfOnline(moduleOnline.esp32)) || changed;

        // Power needs battery (shunt) and/or solar (mppt). Hide only once both
        // have reported offline so a single-device setup still shows the tile.
        const bothVictronKnown = moduleOnline.shunt !== null && moduleOnline.mppt !== null;
        const powerOnline = bothVictronKnown
            ? moduleOnline.shunt === true || moduleOnline.mppt === true
            : true;
        changed = setTileVisible('tile-power', powerOnline) || changed;

        // Sonos: hide when no speakers unless the disconnected-tiles tickbox is on.
        if (moduleOnline.sonos !== null) {
            changed = setTileVisible('tile-sonos', moduleOnline.sonos === true) || changed;
        }

        if (changed) refreshLayout();
        else syncGridReflow();
    }

    function applyModuleStatus(modules) {
        if (!Array.isArray(modules)) return;

        let touched = false;
        modules.forEach((module) => {
            const id = module.id ?? module.module_id;
            if (!id || !(id in moduleOnline) || id === 'sonos') return;

            const connected = module.connected ?? module.online ?? module.status === 'online';
            const next = connected === true ? true : connected === false ? false : null;
            if (next === null) return;
            if (moduleOnline[id] === next) return;
            moduleOnline[id] = next;
            touched = true;
        });

        if (touched) applyVisibility();
    }

    function setSonosOnline(online) {
        const next = online === true;
        if (moduleOnline.sonos === next) return;
        moduleOnline.sonos = next;
        applyVisibility();
    }

    window.SCCS = window.SCCS || {};
    window.SCCS.homeModuleVisibility = {
        applyModuleStatus,
        setSonosOnline,
        setShowDisconnected,
        refresh: applyVisibility,
    };
})();
