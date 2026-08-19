/**
 * SCCS Home page reed summary — open panels only, fade out when closed.
 */
(function () {
    'use strict';

    const FADE_MS = 1000;

    const listEl = document.getElementById('reed-home-list');
    const emptyEl = document.getElementById('reed-home-empty');
    const placeholderEl = document.getElementById('reed-home-placeholder');
    if (!listEl) return;

    function sortByLabel(items) {
        return items.slice().sort((a, b) =>
            (a.label || a.name || '').localeCompare(
                b.label || b.name || '',
                undefined,
                { sensitivity: 'base' },
            ),
        );
    }

    function reorderList() {
        state.reeds.forEach((reed) => {
            const el = document.getElementById(`reed-${reed.name}`);
            if (el?.parentElement === listEl) listEl.appendChild(el);
        });
    }

    const state = {
        reeds: [],
        raw: {},
        pendingRemoval: new Map(),
    };

    function isOpen(name) {
        if (!Object.prototype.hasOwnProperty.call(state.raw, name)) return false;
        return state.raw[name] === false;
    }

    function iconClass(icon, fallback = 'fa-door-closed') {
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

    function insertInOrder(el, reed) {
        const idx = state.reeds.findIndex((item) => item.name === reed.name);
        const nextReed = state.reeds[idx + 1];
        const nextEl = nextReed
            ? document.getElementById(`reed-${nextReed.name}`)
            : null;
        if (nextEl) {
            listEl.insertBefore(el, nextEl);
        } else {
            listEl.appendChild(el);
        }
    }

    function ensureItem(reed) {
        let el = document.getElementById(`reed-${reed.name}`);
        if (el) return el;

        el = document.createElement('li');
        el.id = `reed-${reed.name}`;
        el.className = 'reed-tile__item is-open';
        el.dataset.reedName = reed.name;
        el.innerHTML = `
            <span class="reed-tile__icon" aria-hidden="true">
                <i class="${iconClass(reed.icon)}"></i>
            </span>
            <span class="reed-tile__name"></span>
            <span class="reed-tile__state" aria-live="polite">Open</span>`;
        el.querySelector('.reed-tile__name').textContent = reed.label;
        insertInOrder(el, reed);
        return el;
    }

    function fadeOut(name) {
        const el = document.getElementById(`reed-${name}`);
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

    function showReed(reed) {
        if (!isOpen(reed.name)) {
            const el = document.getElementById(`reed-${reed.name}`);
            if (el?.classList.contains('is-active')) fadeOut(reed.name);
            return;
        }

        clearTimeout(state.pendingRemoval.get(reed.name));
        state.pendingRemoval.delete(reed.name);

        const el = ensureItem(reed);
        const wasActive = el.classList.contains('is-active') && !el.classList.contains('is-fading-out');
        if (wasActive) {
            el.classList.remove('is-fading-out', 'is-fading-in');
            el.classList.add('is-active', 'is-open');
        } else {
            fadeIn(el);
            el.classList.add('is-open');
        }
        updateEmpty();
    }

    function refreshAll() {
        state.reeds.forEach(showReed);
        updateEmpty();
    }

    function onReedsConfig(reeds) {
        if (!Array.isArray(reeds) || !reeds.length) return;

        clearPlaceholder();
        state.reeds = sortByLabel(reeds.map((reed) => ({ ...reed })));

        listEl.querySelectorAll('.reed-tile__item').forEach((el) => {
            const name = el.dataset.reedName;
            if (!state.reeds.some((reed) => reed.name === name)) {
                clearTimeout(state.pendingRemoval.get(name));
                state.pendingRemoval.delete(name);
                el.remove();
            }
        });

        reorderList();
        refreshAll();
    }

    function onReedUpdate(payload) {
        const states = payload?.states || {};
        Object.entries(states).forEach(([name, closed]) => {
            state.raw[name] = closed;
            const reed = state.reeds.find((item) => item.name === name);
            if (reed) showReed(reed);
        });
    }

    async function loadReeds() {
        try {
            const res = await fetch('/api/reeds', { cache: 'no-store' });
            if (!res.ok) return;
            const data = await res.json();
            onReedsConfig(data.reeds);
            if (data.states) {
                onReedUpdate({ states: data.states });
            }
        } catch {
            /* socket will deliver reeds_config */
        }
    }

    loadReeds();

    window.SCCS = window.SCCS || {};
    window.SCCS.reedsHome = { onReedsConfig, onReedUpdate, refresh: loadReeds };
})();