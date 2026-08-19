/**
 * SCCS Home page lighting summary — active lights and levels.
 */
(function () {
    'use strict';

    const FADE_MS = 1000;

    const listEl = document.getElementById('lighting-home-list');
    const emptyEl = document.getElementById('lighting-home-empty');
    const placeholderEl = document.getElementById('lighting-home-placeholder');
    if (!listEl) return;

    function reorderList() {
        state.lights.forEach((light) => {
            const el = document.getElementById(`lighting-home-${light.name}`);
            if (el?.parentElement === listEl) listEl.appendChild(el);
        });
    }

    const state = {
        lights: [],
        values: {},
        modes: {},
        reeds: {},
        pendingRemoval: new Map(),
    };

    function isRooftopClosed(name) {
        return name === 'rooftop_tent' && state.reeds.rooftop_tent !== false;
    }

    function getLevel(light) {
        if (isRooftopClosed(light.name)) return 0;
        const raw = state.values[light.name];
        if (light.type === 'relay') return raw ? 100 : 0;
        return Math.max(0, Math.min(100, raw || 0));
    }

    function isBugMode(light) {
        return light.has_mode && (state.modes[light.name] || 'white') === 'red';
    }

    function isActive(light) {
        if (isRooftopClosed(light.name)) return false;
        if (light.type === 'relay') return !!state.values[light.name];
        if (isBugMode(light)) return true;
        return getLevel(light) > 0;
    }

    function formatLevel(light) {
        if (light.type === 'relay') return 'On';
        return `${getLevel(light)}%`;
    }

    function iconClass(icon, fallback = 'fa-lightbulb') {
        const raw = (icon || fallback).trim();
        const name = raw.replace(/^fa-(solid|regular|brands)\s+/, '').trim();
        return name.startsWith('fa-') ? `fa-solid ${name}` : `fa-solid fa-${name}`;
    }

    function setOverlayVisible(el, visible) {
        if (!el) return;
        el.classList.toggle('is-visible', visible);
        el.setAttribute('aria-hidden', visible ? 'false' : 'true');
    }

    function clearPlaceholder() {
        setOverlayVisible(placeholderEl, false);
    }

    function updateEmpty() {
        setOverlayVisible(emptyEl, listEl.children.length === 0);
    }

    function fadeIn(el) {
        el.classList.remove('is-fading-out');
        el.classList.add('is-active', 'is-fading-in');
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                el.classList.remove('is-fading-in');
            });
        });
    }

    function insertInOrder(el, light) {
        const idx = state.lights.findIndex((item) => item.name === light.name);
        const nextLight = state.lights[idx + 1];
        const nextEl = nextLight
            ? document.getElementById(`lighting-home-${nextLight.name}`)
            : null;
        if (nextEl) {
            listEl.insertBefore(el, nextEl);
        } else {
            listEl.appendChild(el);
        }
    }

    function ensureItem(light) {
        let el = document.getElementById(`lighting-home-${light.name}`);
        if (el) return el;

        el = document.createElement('li');
        el.id = `lighting-home-${light.name}`;
        el.className = 'lighting-home-tile__item';
        el.dataset.lightName = light.name;
        el.innerHTML = `
            <span class="lighting-home-tile__icon" aria-hidden="true">
                <i class="${iconClass(light.icon)}"></i>
            </span>
            <span class="lighting-home-tile__name"></span>
            <span class="lighting-home-tile__level" aria-live="polite"></span>`;
        el.querySelector('.lighting-home-tile__name').textContent = light.label;
        insertInOrder(el, light);
        return el;
    }

    function fadeOut(name) {
        const el = document.getElementById(`lighting-home-${name}`);
        if (!el || el.classList.contains('is-fading-out')) return;

        clearTimeout(state.pendingRemoval.get(name));
        el.classList.add('is-fading-out');
        el.classList.remove('is-active');

        const timeoutId = window.setTimeout(() => {
            el.remove();
            state.pendingRemoval.delete(name);
            updateEmpty();
        }, FADE_MS);
        state.pendingRemoval.set(name, timeoutId);
    }

    function showLight(light) {
        if (!isActive(light)) {
            const el = document.getElementById(`lighting-home-${light.name}`);
            if (el?.classList.contains('is-active')) fadeOut(light.name);
            return;
        }

        clearTimeout(state.pendingRemoval.get(light.name));
        state.pendingRemoval.delete(light.name);

        const el = ensureItem(light);
        const wasActive = el.classList.contains('is-active') && !el.classList.contains('is-fading-out');
        if (wasActive) {
            el.classList.remove('is-fading-out', 'is-fading-in');
            el.classList.add('is-active');
        } else {
            fadeIn(el);
        }
        el.classList.toggle('is-bug-mode', isBugMode(light));

        const levelEl = el.querySelector('.lighting-home-tile__level');
        if (levelEl) levelEl.textContent = formatLevel(light);
        updateEmpty();
    }

    function refreshAll() {
        state.lights.forEach(showLight);
        updateEmpty();
    }

    function onLightsConfig(config) {
        if (!Array.isArray(config) || !config.length) return;

        clearPlaceholder();
        state.lights = config.map((light) => ({ ...light }));

        listEl.querySelectorAll('.lighting-home-tile__item').forEach((el) => {
            const name = el.dataset.lightName;
            if (!state.lights.some((light) => light.name === name)) {
                clearTimeout(state.pendingRemoval.get(name));
                state.pendingRemoval.delete(name);
                el.remove();
            }
        });

        reorderList();
        refreshAll();
    }

    function onLightUpdate(name, value, mode) {
        if (value !== undefined) state.values[name] = value;
        if (mode !== undefined) state.modes[name] = mode;

        const light = state.lights.find((item) => item.name === name);
        if (light) showLight(light);
    }

    function onStateUpdate(newState) {
        if (!newState || typeof newState !== 'object') return;

        state.lights.forEach((light) => {
            if (newState[light.name] !== undefined) {
                state.values[light.name] = newState[light.name];
            }
            const modeKey = `${light.name}_mode`;
            if (light.has_mode && newState[modeKey]) {
                state.modes[light.name] = newState[modeKey];
            }
        });

        refreshAll();
    }

    function onReedUpdate(payload) {
        state.reeds = payload?.states || {};
        refreshAll();
    }

    async function loadLights() {
        try {
            const res = await fetch('/api/lights', { cache: 'no-store' });
            if (!res.ok) return;
            const data = await res.json();
            onLightsConfig(data.lights);
            if (data.state) onStateUpdate(data.state);
        } catch {
            /* socket will deliver lights_config */
        }
    }

    loadLights();

    window.SCCS = window.SCCS || {};
    window.SCCS.lightingHome = {
        onLightsConfig,
        onStateUpdate,
        onLightUpdate,
        onReedUpdate,
        refresh: loadLights,
    };
})();