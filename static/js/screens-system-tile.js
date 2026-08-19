/**
 * SCCS Touchscreens tile — live status via REST + manual wake/sleep via Socket.IO.
 */
(function () {
    'use strict';

    const POLL_INTERVAL_MS = 30000;

    const grid = document.getElementById('screens-system-grid');
    const headlineEl = document.getElementById('screens-summary-headline');
    const detailEl = document.getElementById('screens-summary-detail');
    if (!grid || !headlineEl || !detailEl) return;

    const state = {
        screens: [],
        testing: {},
        brightnessDrag: null,
        bootstrapped: false,
    };

    function getSocket() {
        return window.SCCS?.socket ?? null;
    }

    function getScreen(name) {
        return state.screens.find((screen) => screen.name === name);
    }

    function isTesting(name) {
        return Boolean(state.testing[name]);
    }

    function isDraggingBrightness(name) {
        return state.brightnessDrag === name;
    }

    function clampBrightness(value) {
        return Math.max(0, Math.min(100, Math.round(Number(value) || 0)));
    }

    function sliderValue(screen) {
        if (screen.brightness_pct != null) return clampBrightness(screen.brightness_pct);
        if (screen.on === false) return 0;
        if (screen.on === true) return 100;
        return 0;
    }

    function powerState(screen) {
        if (isTesting(screen.name)) {
            return { label: 'Checking…', modifier: 'is-pending' };
        }
        if (!screen.online) {
            return { label: 'Unreachable', modifier: 'is-offline' };
        }
        if (screen.on === true) {
            return { label: 'On', modifier: 'is-on' };
        }
        if (screen.on === false) {
            return { label: 'Off', modifier: 'is-off' };
        }
        return { label: '—', modifier: 'is-unknown' };
    }

    function brightnessState(screen) {
        if (isTesting(screen.name) || !screen.online) {
            return { label: '—', pct: null };
        }
        if (screen.on === false) {
            if (screen.brightness_pct != null) {
                return { label: '0%', pct: 0 };
            }
            if (screen.brightness != null) {
                return { label: `Blank ${screen.brightness}`, pct: 0 };
            }
            return { label: 'Off', pct: 0 };
        }
        if (screen.brightness_pct != null) {
            return { label: `${screen.brightness_pct}%`, pct: screen.brightness_pct };
        }
        if (screen.on === true) {
            return { label: 'On', pct: null };
        }
        return { label: '—', pct: null };
    }

    function linkLabel(screen) {
        if (isTesting(screen.name)) return 'Checking…';
        if (!screen.online) {
            return screen.ssh_error ? 'SSH error' : 'Unreachable';
        }
        if (screen.latency != null) return `${screen.latency} ms`;
        return 'Connected';
    }

    function isBrightnessAdjustable(screen) {
        // Explicit false from API; missing treated as adjustable only for legacy payloads
        return screen.brightness_adjustable !== false;
    }

    function renderSummary() {
        const total = state.screens.length;
        if (!total && !state.bootstrapped) return;
        if (!total) {
            headlineEl.textContent = 'No touchscreens configured';
            detailEl.textContent = 'Add entries under [screens] in sccs.conf';
            detailEl.classList.remove('is-warning');
            return;
        }

        const online = state.screens.filter((screen) => screen.online).length;
        const awake = state.screens.filter((screen) => screen.online && screen.on).length;
        const asleep = state.screens.filter((screen) => screen.online && screen.on === false).length;
        const withBrightness = state.screens.filter(
            (screen) =>
                screen.online
                && screen.on
                && isBrightnessAdjustable(screen)
                && screen.brightness_pct != null,
        );

        headlineEl.textContent = `${online} of ${total} reachable · ${awake} on · ${asleep} off`;

        const sshIssues = state.screens.filter(
            (screen) => screen.online && screen.ssh_passwordless === false
        ).length;
        const sshErrors = state.screens.filter((screen) => screen.ssh_error).length;

        detailEl.classList.remove('is-warning');

        if (sshErrors > 0) {
            detailEl.textContent = `${sshErrors} screen${sshErrors === 1 ? '' : 's'} need attention`;
            detailEl.classList.add('is-warning');
            return;
        }
        if (sshIssues > 0) {
            detailEl.textContent = `Remote control unavailable on ${sshIssues} screen${sshIssues === 1 ? '' : 's'}`;
            detailEl.classList.add('is-warning');
            return;
        }

        if (withBrightness.length === 1 && total === 1) {
            detailEl.textContent = `Brightness ${withBrightness[0].brightness_pct}%`;
            return;
        }

        detailEl.textContent = online === total ? 'All panels responding' : 'Some panels are offline';
        detailEl.classList.toggle('is-warning', online < total);
    }

    function updateSegmentState(card, screen) {
        if (!card) return;
        const awake = screen.on === true;
        const asleep = screen.on === false;
        const online = screen.online;
        const testing = isTesting(screen.name);
        const controlsDisabled = !online || testing;

        card.querySelectorAll('[data-screen-action="wake"], [data-screen-action="sleep"]').forEach((button) => {
            button.disabled = controlsDisabled;
        });

        const wakeBtn = card.querySelector('[data-screen-action="wake"]');
        const sleepBtn = card.querySelector('[data-screen-action="sleep"]');
        if (wakeBtn) {
            wakeBtn.classList.toggle('is-selected', awake);
            wakeBtn.setAttribute('aria-pressed', awake ? 'true' : 'false');
        }
        if (sleepBtn) {
            sleepBtn.classList.toggle('is-selected', asleep);
            sleepBtn.setAttribute('aria-pressed', asleep ? 'true' : 'false');
        }
    }

    function updateCardBrightnessUi(card, screen) {
        if (!card || !screen) return;

        const adjustable = isBrightnessAdjustable(screen);
        const pct = sliderValue(screen);
        const power = powerState(screen);

        card.style.setProperty('--screen-brightness-pct', String(adjustable ? pct : (screen.on ? 100 : 0)));
        card.classList.toggle('is-awake', screen.on === true);
        card.classList.toggle('is-asleep', screen.on === false);
        card.classList.toggle('is-brightness-fixed', !adjustable);

        const powerEl = card.querySelector('.screens-system-tile__power');
        if (powerEl) {
            powerEl.className = `screens-system-tile__power ${power.modifier}`;
            powerEl.textContent = power.label;
        }

        if (adjustable) {
            const fill = card.querySelector('.sonos-tile__volume-fill');
            if (fill) {
                fill.style.setProperty('--sonos-volume', `${pct}%`);
            }

            const sliderPctEl = card.querySelector('.sonos-tile__volume-pct');
            if (sliderPctEl) {
                sliderPctEl.textContent = `${pct}%`;
            }

            const slider = card.querySelector('[data-screen-brightness]');
            if (slider && document.activeElement !== slider) {
                slider.value = String(pct);
            }
        }

        updateSegmentState(card, screen);
    }

    function renderCard(screen) {
        const testing = isTesting(screen.name);
        const awake = screen.on === true;
        const asleep = screen.on === false;
        const online = screen.online;
        const adjustable = isBrightnessAdjustable(screen);
        const power = powerState(screen);
        const brightness = brightnessState(screen);
        const brightnessPct = !adjustable
            ? (awake ? '100' : '0')
            : (brightness.pct == null ? '' : String(brightness.pct));
        const sliderPct = sliderValue(screen);
        const controlsDisabled = !online || testing;

        const modifiers = [
            online ? 'is-online' : 'is-offline',
            awake ? 'is-awake' : '',
            asleep ? 'is-asleep' : '',
            testing ? 'is-testing' : '',
            adjustable ? '' : 'is-brightness-fixed',
        ].filter(Boolean).join(' ');

        const sliderBlock = adjustable ? `
                    <div class="screens-system-tile__slider-group">
                        <span class="sonos-tile__volume-label" id="screen-brightness-label-${screen.name}">
                            Brightness
                        </span>
                        <span class="sonos-tile__volume-pct" aria-live="polite">${sliderPct}%</span>
                        <div class="sonos-tile__volume-wrap">
                            <div class="sonos-tile__volume-track">
                                <div class="sonos-tile__volume-fill-clip" aria-hidden="true">
                                    <div class="sonos-tile__volume-fill"
                                         style="--sonos-volume: ${sliderPct}%"></div>
                                </div>
                            </div>
                            <input type="range"
                                   class="sonos-tile__volume"
                                   data-screen-brightness="${screen.name}"
                                   min="0"
                                   max="100"
                                   value="${sliderPct}"
                                   aria-labelledby="screen-brightness-label-${screen.name}"
                                   ${controlsDisabled ? 'disabled' : ''}>
                        </div>
                    </div>` : '';

        const previewBar = adjustable ? `
                            <div class="screens-system-tile__brightness-bar">
                                <span class="screens-system-tile__brightness-fill"></span>
                            </div>` : '';

        return `
            <article class="screens-system-tile__card ${modifiers}"
                     data-screen-name="${screen.name}"
                     role="listitem"
                     aria-label="${screen.label}"
                     ${brightnessPct !== '' ? `style="--screen-brightness-pct: ${brightnessPct}"` : ''}>
                <header class="screens-system-tile__header">
                    <h3 class="screens-system-tile__title">${screen.label}</h3>
                    <div class="screens-system-tile__header-actions">
                        <span class="screens-system-tile__power ${power.modifier}"
                              aria-live="polite"
                              aria-label="Power state">${power.label}</span>
                        <button type="button"
                                class="screens-system-tile__refresh"
                                data-screen-action="test"
                                aria-label="Refresh ${screen.label}"
                                ${testing ? 'disabled' : ''}>
                            <i class="fa-solid fa-arrows-rotate${testing ? ' fa-spin' : ''}" aria-hidden="true"></i>
                        </button>
                    </div>
                </header>

                <div class="screens-system-tile__preview" aria-hidden="true">
                    <div class="screens-system-tile__bezel">
                        <div class="screens-system-tile__face">
                            <i class="fa-solid ${screen.icon}" aria-hidden="true"></i>
                            ${previewBar}
                        </div>
                    </div>
                </div>

                <div class="screens-system-tile__controls">
                    ${sliderBlock}

                    <div class="screens-system-tile__segmented" role="group" aria-label="${screen.label} power">
                        <button type="button"
                                class="screens-system-tile__segment${awake ? ' is-selected' : ''}"
                                data-screen-action="wake"
                                aria-pressed="${awake ? 'true' : 'false'}"
                                ${controlsDisabled ? 'disabled' : ''}>
                            <i class="fa-solid fa-sun" aria-hidden="true"></i>
                            <span>Awake</span>
                        </button>
                        <button type="button"
                                class="screens-system-tile__segment${asleep ? ' is-selected' : ''}"
                                data-screen-action="sleep"
                                aria-pressed="${asleep ? 'true' : 'false'}"
                                ${controlsDisabled ? 'disabled' : ''}>
                            <i class="fa-solid fa-moon" aria-hidden="true"></i>
                            <span>Sleep</span>
                        </button>
                    </div>
                </div>

                <footer class="screens-system-tile__footer">
                    <span class="screens-system-tile__link${online ? ' is-up' : ' is-down'}${testing ? ' is-pending' : ''}"
                          aria-live="polite">
                        <span class="screens-system-tile__link-dot" aria-hidden="true"></span>
                        <span class="screens-system-tile__link-text">${linkLabel(screen)}</span>
                    </span>
                </footer>
            </article>`;
    }

    function render() {
        renderSummary();
        if (!state.screens.length) {
            grid.innerHTML = '';
            return;
        }
        grid.innerHTML = state.screens.map(renderCard).join('');
    }

    function mergeScreens(incoming, { observedOnly = false } = {}) {
        if (!Array.isArray(incoming)) return;
        const byName = new Map(state.screens.map((screen) => [screen.name, screen]));
        incoming.forEach((screen) => {
            const prev = byName.get(screen.name) || {};
            if (isDraggingBrightness(screen.name)) {
                const { brightness_pct: _brightness, on: _on, ...rest } = screen;
                byName.set(screen.name, { ...prev, ...rest });
                return;
            }
            if (observedOnly) {
                const next = { ...prev };
                if (Object.prototype.hasOwnProperty.call(screen, 'on')) {
                    next.on = screen.on;
                }
                if (Object.prototype.hasOwnProperty.call(screen, 'brightness_pct')) {
                    next.brightness_pct = screen.brightness_pct;
                }
                byName.set(screen.name, next);
                return;
            }
            byName.set(screen.name, { ...prev, ...screen });
        });
        state.screens = Array.from(byName.values());
        if (state.brightnessDrag) {
            renderSummary();
            return;
        }
        render();
    }

    function setScreenPower(name, awake) {
        const screen = getScreen(name);
        if (!screen) return;

        const socket = getSocket();
        if (!socket?.connected) {
            console.warn('[SCCS] screen_manual_toggle skipped — socket unavailable', name);
            return;
        }

        screen.on = awake;
        if (!awake) {
            screen.brightness_pct = 0;
        }
        render();
        socket.emit('screen_manual_toggle', { name, on: awake });
        setTimeout(() => testScreen(name), 1000);
    }

    function setScreenBrightness(name, pct) {
        const screen = getScreen(name);
        if (!screen || !isBrightnessAdjustable(screen)) return;

        const socket = getSocket();
        if (!socket?.connected) {
            console.warn('[SCCS] screen brightness skipped — socket unavailable', name);
            return;
        }

        const level = clampBrightness(pct);
        screen.brightness_pct = level;
        screen.on = level > 0;
        const card = grid.querySelector(`[data-screen-name="${name}"]`);
        updateCardBrightnessUi(card, screen);
        renderSummary();
        socket.emit('screen_manual_toggle', { name, brightness_pct: level });
        setTimeout(() => testScreen(name), 1000);
    }

    async function refreshAll() {
        if (!window.SCCS?.isSystemTabActive) return;
        try {
            const res = await fetch('/api/screens/status', { cache: 'no-store' });
            if (!res.ok) return;
            const data = await res.json();
            mergeScreens(data.screens || []);
        } catch (err) {
            console.warn('[SCCS] screen status fetch failed', err);
        }
    }

    async function testScreen(name) {
        const screen = getScreen(name);
        if (!screen) return;

        state.testing[name] = true;
        render();

        try {
            const res = await fetch('/api/screens/status', { cache: 'no-store' });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            const updated = (data.screens || []).find((item) => item.name === name);
            if (updated) {
                Object.assign(screen, updated);
            }
        } catch (err) {
            console.warn('[SCCS] screen test failed', name, err);
            screen.ssh_error = 'Status check failed';
        } finally {
            delete state.testing[name];
            render();
        }
    }

    grid.addEventListener('click', (event) => {
        const button = event.target.closest('[data-screen-action]');
        if (!button || button.disabled) return;

        const card = button.closest('[data-screen-name]');
        if (!card) return;

        const name = card.dataset.screenName;
        const action = button.dataset.screenAction;

        if (action === 'wake') {
            setScreenPower(name, true);
            return;
        }
        if (action === 'sleep') {
            setScreenPower(name, false);
            return;
        }
        if (action === 'test') {
            testScreen(name);
        }
    });

    grid.addEventListener('contextmenu', (event) => {
        if (event.target.closest('[data-screen-brightness], .sonos-tile__volume-wrap')) {
            event.preventDefault();
        }
    });

    grid.addEventListener('pointerdown', (event) => {
        const slider = event.target.closest('[data-screen-brightness]');
        if (!slider || slider.disabled) return;
        state.brightnessDrag = slider.dataset.screenBrightness;
        if (event.pointerType === 'touch') {
            event.preventDefault();
            slider.setPointerCapture(event.pointerId);
        }
    });

    grid.addEventListener('input', (event) => {
        const slider = event.target.closest('[data-screen-brightness]');
        if (!slider || slider.disabled) return;

        const name = slider.dataset.screenBrightness;
        const screen = getScreen(name);
        if (!screen) return;

        const level = clampBrightness(slider.value);
        screen.brightness_pct = level;
        screen.on = level > 0;
        updateCardBrightnessUi(slider.closest('[data-screen-name]'), screen);
        renderSummary();
    });

    grid.addEventListener('pointerup', (event) => {
        const slider = event.target.closest('[data-screen-brightness]');
        if (slider?.hasPointerCapture(event.pointerId)) {
            slider.releasePointerCapture(event.pointerId);
        }
        state.brightnessDrag = null;
    });

    grid.addEventListener('pointercancel', (event) => {
        const slider = event.target.closest('[data-screen-brightness]');
        if (slider?.hasPointerCapture(event.pointerId)) {
            slider.releasePointerCapture(event.pointerId);
        }
        state.brightnessDrag = null;
    });

    grid.addEventListener('change', (event) => {
        const slider = event.target.closest('[data-screen-brightness]');
        if (!slider || slider.disabled) return;

        state.brightnessDrag = null;
        setScreenBrightness(slider.dataset.screenBrightness, slider.value);
    });

    window.SCCS = window.SCCS || {};
    async function loadScreens() {
        if (!window.SCCS?.isSystemTabActive) return;
        try {
            const res = await fetch('/api/screens', { cache: 'no-store' });
            if (!res.ok) return;
            const data = await res.json();
            state.screens = (data.screens || []).map((screen) => ({ ...screen }));
            state.bootstrapped = true;
            render();
            if (state.screens.length) {
                await refreshAll();
            }
        } catch (err) {
            console.warn('[SCCS] screen config fetch failed', err);
        }
    }

    window.SCCS.screensSystem = {
        onScreensInit(payload) {
            state.screens = (payload?.screens || []).map((screen) => ({ ...screen }));
            state.bootstrapped = true;
            render();
            refreshAll();
        },
        onScreensUpdate(payload) {
            const incoming = payload?.screens || [];
            if (!incoming.length) return;

            if (!state.screens.length) {
                state.screens = incoming.map((screen) => ({
                    online: false,
                    icon: 'fa-display',
                    label: screen.name,
                    ...screen,
                }));
                state.bootstrapped = true;
                render();
                return;
            }

            mergeScreens(incoming, { observedOnly: true });
        },
        refresh: refreshAll,
        loadScreens,
        testScreen,
        setScreenPower,
        setScreenBrightness,
    };

    setInterval(refreshAll, POLL_INTERVAL_MS);
})();