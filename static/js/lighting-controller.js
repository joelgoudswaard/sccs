/**
 * SCCS Lighting tab — sliders and relays via Socket.IO + HTTP fallback.
 */
(function () {
    'use strict';

    const JUST_SET_DURATION = 2800;
    const UI_RAMP_MS = 1000;
    const SCENE_RAMP_MS = 4000;
    const REED_RAMP_MS = 2000;
    const PHASE_RAMP_MS = 4000;
    let backendConnected = false;
    let sceneActivating = false;
    let reedActivating = false;
    /** null = unknown (show ESP lights to avoid flash-on-load); false hides them */
    let esp32Online = null;
    /** User preference (Settings → Appearance): show ESP-driven lights even when the MCU is offline. */
    let forceShowDisconnected = false;
    const sceneAnimationCancels = {};

    const state = {
        lightsConfig: [],
        currentState: {},
        currentModes: {},
        currentReeds: {},
        lastRenderConfigHash: '',
        currentlyDragging: new Set(),
        userJustSet: new Set(),
        locallyAnimating: new Set(),
    };

    // Relays are GPIO-driven rather than ESP-driven, but they're still wired
    // through the SCCS Core PCB — if the core's unreachable, relay channels
    // are just as dead as the dimmer/RGB lights, so they hide too.
    function getVisibleLights() {
        if (forceShowDisconnected || esp32Online !== false) return state.lightsConfig;
        return [];
    }

    let lastTouchPointerUp = 0;
    let lastColumnCount = 1;

    function getSocket() {
        return window.SCCS?.socket ?? null;
    }

    function toggleAriaLabel(label, isOn) {
        return `${label} ${isOn ? 'on' : 'off'}`;
    }

    function renderRelayToggle(name, label, isOn) {
        return `
            <button type="button"
                    class="relay-toggle ${isOn ? 'on' : ''}"
                    data-name="${name}"
                    data-state="${isOn ? 'on' : 'off'}"
                    aria-pressed="${isOn ? 'true' : 'false'}"
                    aria-label="${toggleAriaLabel(label, isOn)}">
                <span class="relay-knob" aria-hidden="true"></span>
            </button>`;
    }

    function renderTogglePill(name, label, isOn, isBugMode = false) {
        return `
            <button type="button"
                    class="toggle-pill ${isOn ? 'on' : ''}${isBugMode ? ' bug-mode' : ''}"
                    data-name="${name}"
                    data-state="${isOn ? 'on' : 'off'}"
                    aria-pressed="${isOn ? 'true' : 'false'}"
                    aria-label="${toggleAriaLabel(label, isOn)}">
                <span class="toggle-knob" aria-hidden="true"></span>
            </button>`;
    }

    function syncSliderRange(wrapper, value) {
        const range = wrapper?.querySelector('.slider-range');
        if (!range || document.activeElement === range) return;
        const clamped = Math.max(0, Math.min(100, value || 0));
        range.value = String(clamped);
        range.setAttribute('aria-valuenow', String(clamped));
    }

    function emitLightChange(payload) {
        const socket = getSocket();
        if (socket?.connected) {
            socket.emit('light_change', payload);
            return;
        }
        fetch('/api/light', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
            keepalive: true,
        })
            .then((r) => (r.ok ? r.json() : null))
            .then((data) => {
                if (!data?.state) return;
                const meta = extractStateMeta(data.state);
                applyStateToUI(data.state, {
                    animate: meta.animate,
                    rampMs: meta.rampMs,
                });
            })
            .catch((err) => console.warn('[SCCS] light_change HTTP failed', err));
    }

    function emitRelayChange(name, on) {
        const socket = getSocket();
        if (socket?.connected) {
            socket.emit('relay_change', { name, on });
            return;
        }
        fetch('/api/relay', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, on }),
            keepalive: true,
        }).catch((err) => console.warn('[SCCS] relay_change HTTP failed', err));
    }

    function cancelSceneAnimations() {
        Object.values(sceneAnimationCancels).forEach((cancel) => {
            if (typeof cancel === 'function') cancel();
        });
        Object.keys(sceneAnimationCancels).forEach((k) => delete sceneAnimationCancels[k]);
    }

    function setSliderMotion(wrapper, enabled) {
        if (!wrapper) return;
        const fill = wrapper.querySelector('.slider-fill');
        const thumb = wrapper.querySelector('.slider-thumb');
        const transition = enabled ? '' : 'none';
        if (fill) fill.style.transition = transition;
        if (thumb) thumb.style.transition = transition;
    }

    function animateSlider(name, to, rampMs, onComplete, fromOverride) {
        const wrapper = document.querySelector(`.slider-wrapper[data-name="${name}"]`);
        if (!wrapper) {
            updateLightUI(name, to);
            onComplete?.();
            return () => {};
        }

        const fill = wrapper.querySelector('.slider-fill');
        const thumb = wrapper.querySelector('.slider-thumb');
        const transition = `width ${rampMs}ms ease, left ${rampMs}ms ease`;
        let timeoutId = null;
        const target = Math.max(0, Math.min(100, to || 0));

        function cancel() {
            if (timeoutId) clearTimeout(timeoutId);
            delete sceneAnimationCancels[name];
        }

        sceneAnimationCancels[name] = cancel;

        if (fromOverride !== undefined && fromOverride !== null) {
            setSliderMotion(wrapper, false);
            updateLightUI(name, fromOverride);
            void wrapper.offsetWidth;
        }

        if (fill) fill.style.transition = transition;
        if (thumb) thumb.style.transition = transition;
        void wrapper.offsetWidth;
        updateLightUI(name, target);

        timeoutId = window.setTimeout(() => {
            delete sceneAnimationCancels[name];
            onComplete?.();
        }, rampMs + 60);

        return cancel;
    }

    const STATE_META_KEYS = new Set(['last_scene', '_animate', '_ramp_ms', '_trigger']);

    function extractStateMeta(newState) {
        const trigger = newState._trigger;
        let rampMs = newState._ramp_ms;
        if (!rampMs && trigger === 'ui') rampMs = UI_RAMP_MS;
        if (!rampMs && trigger === 'reed') rampMs = REED_RAMP_MS;
        if (!rampMs && trigger === 'phase') rampMs = PHASE_RAMP_MS;
        return {
            animate: !!newState._animate,
            rampMs: rampMs || SCENE_RAMP_MS,
            trigger,
        };
    }

    function applyModesFromState(newState) {
        const modeChanged = new Set();
        state.lightsConfig.forEach((light) => {
            if (!light.has_mode) return;
            const modeKey = `${light.name}_mode`;
            if (!(modeKey in newState)) return;
            const nextMode = newState[modeKey] || 'white';
            const prevMode = state.currentModes[light.name]
                || state.currentState[modeKey]
                || 'white';
            if (nextMode !== prevMode) modeChanged.add(light.name);
            state.currentModes[light.name] = nextMode;
            state.currentState[modeKey] = nextMode;
        });
        return modeChanged;
    }

    function repaintLightFromState(name) {
        const light = state.lightsConfig.find((l) => l.name === name);
        if (!light) return;

        let val = state.currentState[name];
        if (val === undefined && light.type !== 'relay') {
            const wrapper = document.querySelector(`.slider-wrapper[data-name="${name}"]`);
            const parsed = parseInt(wrapper?.dataset.value, 10);
            if (Number.isFinite(parsed)) val = parsed;
        }
        if (val === undefined) return;
        updateLightUI(name, light.type === 'relay' ? !!val : (val || 0));
    }

    function repaintModeChanges(modeChanged, { skipNames = null } = {}) {
        const skip = skipNames || new Set();
        modeChanged.forEach((name) => {
            if (skip.has(name)) return;
            if (state.currentlyDragging.has(name) || state.locallyAnimating.has(name)) return;
            repaintLightFromState(name);
        });
    }

    function applyStateToUI(newState, { animate = false, rampMs = SCENE_RAMP_MS } = {}) {
        const meta = extractStateMeta(newState);
        let shouldAnimate = animate || meta.animate;
        if (document.hidden) shouldAnimate = false;
        const effectiveRampMs = meta.rampMs || rampMs;

        const protectedBrightness = new Set([
            ...state.currentlyDragging,
            ...state.locallyAnimating,
        ]);
        if (!shouldAnimate) {
            state.userJustSet.forEach((name) => protectedBrightness.add(name));
        }

        const modeChanged = applyModesFromState(newState);

        if (!shouldAnimate) {
            Object.keys(newState).forEach((k) => {
                if (k.endsWith('_mode') || STATE_META_KEYS.has(k)) return;
                if (!protectedBrightness.has(k)) state.currentState[k] = newState[k];
            });
            updateUIFromState();
            repaintModeChanges(modeChanged);
            return;
        }

        cancelSceneAnimations();

        const brightnessPainted = new Set();

        state.lightsConfig.forEach((light) => {
            if (protectedBrightness.has(light.name)) return;

            const target = newState[light.name];
            if (target === undefined) return;

            if (light.type === 'relay') {
                state.currentState[light.name] = !!target;
                updateLightUI(light.name, !!target);
                brightnessPainted.add(light.name);
                return;
            }

            const wrapper = document.querySelector(`.slider-wrapper[data-name="${light.name}"]`);
            const startVal = parseInt(wrapper?.dataset.value, 10);
            const from = Number.isFinite(startVal) ? startVal : (state.currentState[light.name] || 0);
            const end = Math.max(0, Math.min(100, target || 0));
            state.currentState[light.name] = end;

            if (from === end) {
                updateLightUI(light.name, end);
                brightnessPainted.add(light.name);
                return;
            }

            animateSlider(light.name, end, effectiveRampMs);
            brightnessPainted.add(light.name);
        });

        repaintModeChanges(modeChanged, { skipNames: brightnessPainted });
        updateRooftopTentControls();
    }

    async function syncFromServer() {
        try {
            const res = await fetch('/api/lights', { cache: 'no-store' });
            if (!res.ok) return;
            const data = await res.json();
            if (data.lights?.length) {
                const hadConfig = state.lightsConfig.length > 0;
                state.lightsConfig = data.lights;
                if (!hadConfig) renderLightingControls();
            }
            if (data.state) {
                applyStateToUI(data.state, { animate: false });
            }
        } catch {
            /* socket will retry */
        }
    }

    function getCurrentColumns() {
        const container = document.getElementById('lighting-controls');
        if (!container) return 1;

        const style = window.getComputedStyle(container);
        const gridTemplate = style.gridTemplateColumns || style.getPropertyValue('grid-template-columns');

        if (gridTemplate.includes('repeat(1') || gridTemplate.split(' ').length === 1) return 1;
        if (gridTemplate.includes('repeat(2') || gridTemplate.split(' ').length === 2) return 2;
        if (gridTemplate.includes('repeat(3') || gridTemplate.split(' ').length === 3) return 3;

        const children = container.children.length;
        if (children > 0) {
            const firstChild = container.children[0];
            const containerRect = container.getBoundingClientRect();
            const childRect = firstChild.getBoundingClientRect();

            if (containerRect.width > 0) {
                const approxCols = Math.round(containerRect.width / (childRect.width + 16));
                return Math.max(1, Math.min(3, approxCols));
            }
        }

        return 1;
    }

    function isRooftopTentPhysicallyClosed() {
        return state.currentReeds.rooftop_tent !== false;
    }

    function updateRooftopTentControls() {
        const tentCard = document.querySelector('.slider-wrapper[data-name="rooftop_tent"]')?.closest('.slider-card');
        if (!tentCard) return;

        const isClosed = isRooftopTentPhysicallyClosed();

        if (isClosed) {
            tentCard.classList.add('rooftop-disabled');
            updateLightUI('rooftop_tent', 0);
        } else {
            tentCard.classList.remove('rooftop-disabled');
        }
    }

    function updateLightUI(name, value) {
        const light = state.lightsConfig.find((l) => l.name === name);
        if (!light) return;

        const valueEl = document.getElementById(`val-${name}`);
        const pills = document.querySelectorAll(
            `.toggle-pill[data-name="${name}"], .relay-toggle[data-name="${name}"]`
        );

        if (light.type === 'relay') {
            const isOn = !!value;

            pills.forEach((toggle) => {
                toggle.classList.toggle('on', isOn);
                toggle.dataset.state = isOn ? 'on' : 'off';
                toggle.setAttribute('aria-pressed', String(isOn));
                toggle.setAttribute('aria-label', toggleAriaLabel(light.label, isOn));
            });

            if (valueEl) valueEl.textContent = isOn ? 'On' : 'Off';
            window.SCCS.lightingHome?.onLightUpdate?.(name, isOn ? 1 : 0);
            return;
        }

        const wrapper = document.querySelector(`.slider-wrapper[data-name="${name}"]`);
        const fill = wrapper ? wrapper.querySelector('.slider-fill') : null;
        const thumb = wrapper ? wrapper.querySelector('.slider-thumb') : null;
        const brightness = Math.max(0, Math.min(100, value || 0));
        const card = wrapper ? wrapper.closest('.slider-card') : null;

        if (wrapper) {
            wrapper.dataset.value = brightness;
            if (brightness > 0) wrapper.dataset.lastBrightness = brightness;

            if (fill) fill.style.width = `${brightness}%`;
            if (thumb) thumb.style.left = `${brightness}%`;
            if (valueEl) valueEl.textContent = `${brightness}%`;
            syncSliderRange(wrapper, brightness);
        }

        const isBugMode = light.has_mode && (state.currentModes[name] || 'white') === 'red';
        const pillOn = brightness > 0;

        pills.forEach((pill) => {
            pill.classList.toggle('on', pillOn);
            pill.classList.toggle('bug-mode', isBugMode);
            pill.dataset.state = pillOn ? 'on' : 'off';
            pill.setAttribute('aria-pressed', String(pillOn));
            pill.setAttribute('aria-label', toggleAriaLabel(light.label, pillOn));
        });

        const bugChip = document.querySelector(`.bug-mode-chip[data-name="${name}"]`);
        if (bugChip) {
            const mode = isBugMode ? 'red' : 'white';
            bugChip.classList.toggle('is-active', isBugMode);
            bugChip.dataset.mode = mode;
            bugChip.setAttribute('aria-pressed', String(isBugMode));
            bugChip.setAttribute('aria-label', isBugMode ? 'Bug mode on' : 'Bug mode off');
        }

        if (card) card.classList.toggle('bug-mode', isBugMode);

        if (wrapper) {
            wrapper.classList.toggle('bug-mode', isBugMode);
            if (fill) fill.classList.toggle('bug-mode', isBugMode);
            if (thumb) thumb.classList.toggle('bug-mode', isBugMode);
        }

        window.SCCS.lightingHome?.onLightUpdate?.(
            name,
            light.type === 'relay' ? (value ? 1 : 0) : (value || 0),
            state.currentModes[name]
        );
    }

    function updateUIFromState() {
        state.lightsConfig.forEach((light) => {
            if (state.currentlyDragging.has(light.name) || state.userJustSet.has(light.name)) {
                return;
            }

            const val = state.currentState[light.name];
            if (val === undefined) return;

            updateLightUI(light.name, light.type === 'relay' ? !!val : (val || 0));
        });

        updateRooftopTentControls();
    }

    function toggleControl(el) {
        const name = el.dataset.name;
        const light = state.lightsConfig.find((l) => l.name === name);
        if (!light) return;

        if (name === 'rooftop_tent' && isRooftopTentPhysicallyClosed()) return;

        state.userJustSet.add(name);
        setTimeout(() => state.userJustSet.delete(name), JUST_SET_DURATION);

        if (light.type === 'relay') {
            const isCurrentlyOn = el.dataset.state === 'on';
            const newState = !isCurrentlyOn;

            state.currentState[name] = newState ? 1 : 0;
            updateLightUI(name, newState ? 1 : 0);
            emitRelayChange(name, newState);
            return;
        }

        const wrapper = document.querySelector(`.slider-wrapper[data-name="${name}"]`);
        const currentBrightness = wrapper ? parseInt(wrapper.dataset.value, 10) || 0 : 0;
        const isOn = el.dataset.state === 'on';
        const newBrightness = isOn ? 0 : (parseInt(wrapper?.dataset.lastBrightness, 10) || 100);

        state.currentState[name] = newBrightness;
        state.locallyAnimating.add(name);
        animateSlider(name, newBrightness, UI_RAMP_MS, () => {
            state.locallyAnimating.delete(name);
        }, currentBrightness);
        const payload = { name, brightness: newBrightness };
        if (light.has_mode && state.currentModes[name]) {
            payload.mode = state.currentModes[name];
        }
        emitLightChange(payload);
    }

    function toggleBugMode(el) {
        const name = el.dataset.name;
        const light = state.lightsConfig.find((l) => l.name === name);
        if (!light?.has_mode) return;

        if (name === 'rooftop_tent' && isRooftopTentPhysicallyClosed()) return;

        state.userJustSet.add(name);
        setTimeout(() => state.userJustSet.delete(name), JUST_SET_DURATION);

        const currentMode = state.currentModes[name] || 'white';
        const newMode = currentMode === 'white' ? 'red' : 'white';
        state.currentModes[name] = newMode;
        state.currentState[`${name}_mode`] = newMode;

        const currentBrightness = state.currentState[name] || 0;
        updateLightUI(name, currentBrightness);
        emitLightChange({ name, brightness: currentBrightness, mode: newMode });
    }

    function initUnifiedToggleListeners() {
        const container = document.getElementById('lighting-controls');
        if (!container) return;

        container.removeEventListener('click', handleLightingClick);

        function handleLightingClick(e) {
            const bugChip = e.target.closest('.bug-mode-chip');
            if (bugChip) {
                if (bugChip.dataset.justClicked === 'true') return;
                bugChip.dataset.justClicked = 'true';
                setTimeout(() => delete bugChip.dataset.justClicked, 350);
                toggleBugMode(bugChip);
                return;
            }

            const toggle = e.target.closest('.relay-toggle, .toggle-pill');
            if (!toggle) return;

            if (toggle.dataset.justClicked === 'true') return;
            toggle.dataset.justClicked = 'true';
            setTimeout(() => delete toggle.dataset.justClicked, 350);

            toggleControl(toggle);
        }

        container.addEventListener('click', handleLightingClick);
    }

    function makeDraggable(wrapper) {
        if (wrapper.dataset.sccsSliderBound === '1') return;
        wrapper.dataset.sccsSliderBound = '1';

        const inner = wrapper.querySelector('.slider-inner');
        const fill = wrapper.querySelector('.slider-fill');
        const thumb = wrapper.querySelector('.slider-thumb');
        const name = wrapper.dataset.name;
        const valueEl = document.getElementById(`val-${name}`);

        let isDragging = false;
        let activePointerId = null;
        let startX = 0;
        let startY = 0;
        let valueAtPointerStart = 0;

        const range = wrapper.querySelector('.slider-range');

        function updatePosition(clientX) {
            const rect = inner.getBoundingClientRect();
            const percent = Math.max(
                0,
                Math.min(100, Math.round(((clientX - rect.left) / rect.width) * 100))
            );

            wrapper.dataset.value = percent;
            if (fill) fill.style.width = `${percent}%`;
            if (thumb) thumb.style.left = `${percent}%`;
            if (valueEl) valueEl.textContent = `${percent}%`;
            if (range) range.value = String(percent);
        }

        function startDrag() {
            if (isDragging) return;
            isDragging = true;
            wrapper.classList.add('dragging');
            state.currentlyDragging.add(name);
            state.userJustSet.delete(name);
            if (fill) fill.style.transition = 'none';
            if (thumb) thumb.style.transition = 'none';
        }

        function commitDrag(force, clickFinal) {
            if (!force && !isDragging) return;

            const wasDragging = isDragging;
            const final =
                clickFinal !== undefined
                    ? clickFinal
                    : parseInt(wrapper.dataset.value, 10) || 0;
            isDragging = false;
            activePointerId = null;
            wrapper.classList.remove('dragging');
            state.currentlyDragging.delete(name);

            state.userJustSet.add(name);
            setTimeout(() => state.userJustSet.delete(name), JUST_SET_DURATION);
            state.currentState[name] = final;

            const shouldAnimateLocal = !wasDragging && final !== valueAtPointerStart;
            if (shouldAnimateLocal) {
                state.locallyAnimating.add(name);
                animateSlider(name, final, UI_RAMP_MS, () => {
                    state.locallyAnimating.delete(name);
                }, valueAtPointerStart);
            } else {
                updateLightUI(name, final);
            }

            const light = state.lightsConfig.find((l) => l.name === name);
            const payload = { name, brightness: final };
            if (light?.has_mode && state.currentModes[name]) {
                payload.mode = state.currentModes[name];
            }
            emitLightChange(payload);
        }

        wrapper.addEventListener('pointerdown', (e) => {
            if (e.button !== 0) return;
            if (name === 'rooftop_tent' && isRooftopTentPhysicallyClosed()) return;
            if (e.pointerType === 'mouse' && Date.now() - lastTouchPointerUp < 600) return;

            activePointerId = e.pointerId;
            startX = e.clientX;
            startY = e.clientY;
            valueAtPointerStart = parseInt(wrapper.dataset.value, 10) || 0;
            isDragging = false;

            if (e.pointerType === 'mouse') {
                e.preventDefault();
                wrapper.setPointerCapture(e.pointerId);
            }
        });

        wrapper.addEventListener('pointermove', (e) => {
            if (e.pointerId !== activePointerId) return;
            if (name === 'rooftop_tent' && isRooftopTentPhysicallyClosed()) return;

            const deltaX = Math.abs(e.clientX - startX);
            const deltaY = Math.abs(e.clientY - startY);

            if (!isDragging) {
                if (e.pointerType === 'mouse' && (deltaX > 2 || deltaY > 2)) {
                    startDrag();
                } else if (deltaX > 10 && deltaX > deltaY * 1.5) {
                    e.preventDefault();
                    wrapper.setPointerCapture(e.pointerId);
                    startDrag();
                } else {
                    return;
                }
            }

            e.preventDefault();
            updatePosition(e.clientX);
        });

        wrapper.addEventListener('pointerup', (e) => {
            if (e.pointerId !== activePointerId) return;
            if (e.pointerType === 'touch') lastTouchPointerUp = Date.now();
            try {
                wrapper.releasePointerCapture(e.pointerId);
            } catch (_) {
                /* ok */
            }

            const rect = inner.getBoundingClientRect();
            const clickFinal = Math.max(
                0,
                Math.min(100, Math.round(((e.clientX - rect.left) / rect.width) * 100))
            );
            const final = isDragging ? parseInt(wrapper.dataset.value, 10) || 0 : clickFinal;
            const changed = isDragging || clickFinal !== valueAtPointerStart;
            if (changed) commitDrag(true, isDragging ? undefined : clickFinal);
            else {
                isDragging = false;
                activePointerId = null;
            }
        });

        wrapper.addEventListener('pointercancel', (e) => {
            if (e.pointerId !== activePointerId) return;
            if (isDragging) commitDrag(true);
            else {
                isDragging = false;
                activePointerId = null;
            }
        });
    }

    function bindSliderRange(wrapper) {
        const range = wrapper.querySelector('.slider-range');
        if (!range || range.dataset.sccsRangeBound === '1') return;
        range.dataset.sccsRangeBound = '1';

        const name = wrapper.dataset.name;
        const inner = wrapper.querySelector('.slider-inner');
        const fill = wrapper.querySelector('.slider-fill');
        const thumb = wrapper.querySelector('.slider-thumb');
        const valueEl = document.getElementById(`val-${name}`);

        function updateVisual(percent) {
            wrapper.dataset.value = percent;
            if (fill) fill.style.width = `${percent}%`;
            if (thumb) thumb.style.left = `${percent}%`;
            if (valueEl) valueEl.textContent = `${percent}%`;
        }

        function commitRangeValue() {
            if (name === 'rooftop_tent' && isRooftopTentPhysicallyClosed()) return;

            const final = parseInt(range.value, 10) || 0;
            state.userJustSet.add(name);
            setTimeout(() => state.userJustSet.delete(name), JUST_SET_DURATION);
            state.currentState[name] = final;
            updateLightUI(name, final);

            const light = state.lightsConfig.find((l) => l.name === name);
            const payload = { name, brightness: final };
            if (light?.has_mode && state.currentModes[name]) {
                payload.mode = state.currentModes[name];
            }
            emitLightChange(payload);
        }

        range.addEventListener('input', () => {
            if (name === 'rooftop_tent' && isRooftopTentPhysicallyClosed()) return;
            const percent = parseInt(range.value, 10) || 0;
            state.currentlyDragging.add(name);
            range.setAttribute('aria-valuenow', String(percent));
            updateVisual(percent);
        });

        range.addEventListener('change', () => {
            state.currentlyDragging.delete(name);
            commitRangeValue();
        });

        range.addEventListener('keydown', (event) => {
            if (event.key !== 'Enter') return;
            event.preventDefault();
            commitRangeValue();
        });

        range.addEventListener('blur', () => {
            state.currentlyDragging.delete(name);
        });

        if (inner) {
            inner.setAttribute('aria-hidden', 'true');
        }
    }

    function initSliders() {
        document
            .querySelectorAll('.slider-wrapper:not([data-sccs-slider-bound="1"])')
            .forEach((wrapper) => {
                makeDraggable(wrapper);
                bindSliderRange(wrapper);
            });
    }

    function renderLightingControls() {
        const container = document.getElementById('lighting-controls');
        if (!container) return false;

        if (!state.lightsConfig.length) {
            container.innerHTML = '<p class="lighting-connecting">Connecting to lighting backend…</p>';
            return false;
        }

        const visibleLights = getVisibleLights();
        const currentHash = JSON.stringify({
            lights: visibleLights.map((l) => l.name + l.type),
            esp32: esp32Online,
        });
        if (currentHash === state.lastRenderConfigHash && visibleLights.length > 0) {
            updateUIFromState();
            return true;
        }

        state.lastRenderConfigHash = currentHash;
        container.innerHTML = '';

        if (!visibleLights.length) {
            const offline = esp32Online === false;
            container.innerHTML = offline
                ? '<p class="hardware-offline-notice">Lighting hardware offline</p>'
                : '<p class="lighting-connecting">Connecting to lighting backend…</p>';
            return false;
        }

        const columns = getCurrentColumns();

        let i = 0;
        while (i < visibleLights.length) {
            const light = visibleLights[i];

            const canPair =
                light.type === 'relay' &&
                i + 1 < visibleLights.length &&
                visibleLights[i + 1].type === 'relay';

            let shouldPair = canPair;

            if (canPair) {
                if (columns === 1) {
                    shouldPair = false;
                } else if (columns === 2) {
                    const isLastPair = i + 2 === visibleLights.length;
                    shouldPair = !isLastPair;
                }
            }

            if (shouldPair) {
                const relay1 = light;
                const relay2 = visibleLights[i + 1];

                container.insertAdjacentHTML(
                    'beforeend',
                    `
                    <div class="slider-card paired-relay-card">
                        <div class="paired-relay-inner">
                            <div class="paired-relay-row">
                                <div class="slider-card-left">
                                    <div class="slider-card-title">
                                        <i class="fa-solid ${relay1.icon}"></i>
                                        <span class="slider-label">${relay1.label}</span>
                                    </div>
                                </div>
                                <div class="slider-card-right">
                                    <div class="value-display" id="val-${relay1.name}">${state.currentState[relay1.name] ? 'On' : 'Off'}</div>
                                    ${renderRelayToggle(relay1.name, relay1.label, !!state.currentState[relay1.name])}
                                </div>
                            </div>
                            <div class="paired-relay-divider"></div>
                            <div class="paired-relay-row">
                                <div class="slider-card-left">
                                    <div class="slider-card-title">
                                        <i class="fa-solid ${relay2.icon}"></i>
                                        <span class="slider-label">${relay2.label}</span>
                                    </div>
                                </div>
                                <div class="slider-card-right">
                                    <div class="value-display" id="val-${relay2.name}">${state.currentState[relay2.name] ? 'On' : 'Off'}</div>
                                    ${renderRelayToggle(relay2.name, relay2.label, !!state.currentState[relay2.name])}
                                </div>
                            </div>
                        </div>
                    </div>`
                );

                i += 2;
                continue;
            }

            const isRelay = light.type === 'relay';
            const currentVal = state.currentState[light.name] || 0;
            const isOn = isRelay ? !!currentVal : (currentVal > 0);
            const brightness = isRelay ? 0 : Math.max(0, Math.min(100, currentVal || 0));
            const currentMode = state.currentModes[light.name]
                || state.currentState[`${light.name}_mode`]
                || 'white';
            const isBugMode = light.has_mode && currentMode === 'red';

            const cardBugClass = isBugMode ? ' bug-mode' : '';

            let html = `
                <div class="slider-card${cardBugClass}">
                    <div class="slider-card-header">
                        <div class="slider-card-left">
                            <div class="slider-card-title">
                                <i class="fa-solid ${light.icon}"></i>
                                <span class="slider-label">${light.label}</span>
                            </div>
                        </div>
                        <div class="slider-card-right">
                            <div class="value-display" id="val-${light.name}">
                                ${isRelay ? (isOn ? 'On' : 'Off') : `${brightness}%`}
                            </div>`;

            if (isRelay) {
                html += renderRelayToggle(light.name, light.label, isOn);
            } else {
                if (light.has_mode) {
                    html += `
                            <button type="button"
                                class="bug-mode-chip ${isBugMode ? 'is-active' : ''}"
                                data-name="${light.name}"
                                data-mode="${currentMode}"
                                aria-label="${isBugMode ? 'Bug mode on' : 'Bug mode off'}"
                                aria-pressed="${isBugMode}">
                                <i class="fa-solid fa-mosquito" aria-hidden="true"></i>
                            </button>`;
                }
                html += renderTogglePill(light.name, light.label, isOn, isBugMode);
            }

            html += '</div></div>';

            if (!isRelay) {
                const sliderBugClass = isBugMode ? ' bug-mode' : '';
                html += `
                    <div class="slider-wrapper${sliderBugClass}" data-name="${light.name}" data-value="${brightness}" data-last-brightness="${brightness || 100}">
                        <div class="slider-inner">
                            <div class="slider-track"></div>
                            <div class="slider-fill${sliderBugClass}" style="width: ${brightness}%"></div>
                            <div class="slider-thumb${sliderBugClass}" style="left: ${brightness}%"></div>
                        </div>
                        <input type="range"
                               class="slider-range"
                               min="0"
                               max="100"
                               value="${brightness}"
                               aria-label="${light.label} brightness"
                               aria-valuemin="0"
                               aria-valuemax="100"
                               aria-valuenow="${brightness}">
                    </div>`;
            }

            html += '</div>';
            container.insertAdjacentHTML('beforeend', html);
            i += 1;
        }

        initUnifiedToggleListeners();
        initSliders();
        setTimeout(updateUIFromState, 100);
        return true;
    }

    function setEsp32Online(online) {
        const next = online === true ? true : online === false ? false : null;
        if (next === null || esp32Online === next) return;
        esp32Online = next;
        // Force a full re-render so ESP-driven cards appear/disappear.
        state.lastRenderConfigHash = '';
        if (state.lightsConfig.length) {
            renderLightingControls();
        }
    }

    function setShowDisconnected(value) {
        const next = value === true;
        if (forceShowDisconnected === next) return;
        forceShowDisconnected = next;
        state.lastRenderConfigHash = '';
        if (state.lightsConfig.length) {
            renderLightingControls();
        }
    }

    function initResize() {
        function handleResizeForLighting() {
            const newCols = getCurrentColumns();
            if (newCols !== lastColumnCount && getVisibleLights().length > 0) {
                lastColumnCount = newCols;
                renderLightingControls();
            }
        }

        window.addEventListener('resize', handleResizeForLighting);
        setTimeout(() => {
            lastColumnCount = getCurrentColumns();
        }, 300);
    }

    function initVisibilitySync() {
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden) syncFromServer();
        });
    }

    function init() {
        if (!document.getElementById('lighting-controls')) return;

        state.lightsConfig.forEach((light) => {
            const mode = state.currentState[`${light.name}_mode`];
            if (light.has_mode && mode) state.currentModes[light.name] = mode;
        });

        renderLightingControls();
        initResize();
        initVisibilitySync();
    }

    window.SCCS = window.SCCS || {};
    window.SCCS.lighting = {
        onLightsConfig(config) {
            backendConnected = true;
            const isFirstConfig = !state.lightsConfig.length;
            state.lightsConfig = config || [];
            if (isFirstConfig) {
                state.currentState = {};
                state.currentModes = {};
                state.lastRenderConfigHash = '';
            }
            state.lightsConfig.forEach((light) => {
                const mode = state.currentState[`${light.name}_mode`];
                if (light.has_mode && mode) state.currentModes[light.name] = mode;
            });
            renderLightingControls();
            const socket = getSocket();
            if (socket?.connected) socket.emit('get_reeds');
            if (Object.keys(state.currentState).length > 0) updateUIFromState();
        },
        onStateUpdate(newState, options = {}) {
            const meta = extractStateMeta(newState);
            const animate = options.animate ?? meta.animate ?? sceneActivating ?? reedActivating;
            if (sceneActivating) sceneActivating = false;
            if (reedActivating) reedActivating = false;
            applyStateToUI(newState, {
                animate,
                rampMs: options.rampMs ?? meta.rampMs ?? SCENE_RAMP_MS,
            });
        },
        setSceneActivating(value, rampMs) {
            sceneActivating = !!value;
            if (rampMs) window.SCCS._sceneRampMs = rampMs;
        },
        setReedActivating(value) {
            reedActivating = !!value;
        },
        getSceneRampMs() {
            return window.SCCS._sceneRampMs || SCENE_RAMP_MS;
        },
        onReedUpdate(payload) {
            state.currentReeds = payload.states || {};
            updateRooftopTentControls();
        },
        initResize,
        syncFromServer,
        setEsp32Online,
        setShowDisconnected,
        isBackendConnected() {
            return backendConnected;
        },
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();