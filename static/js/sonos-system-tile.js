/**
 * SCCS System tab — live Sonos speakers from Socket.IO discovery.
 */
(function () {
    'use strict';

    const POLL_INTERVAL_MS = 5000;
    const SPEAKER_ICONS = {
        kitchen: 'fa-utensils',
        lounge: 'fa-couch',
        awning: 'fa-umbrella',
        bedroom: 'fa-bed',
        bathroom: 'fa-shower',
    };

    const grid = document.getElementById('sonos-system-grid');
    const headlineEl = document.getElementById('sonos-system-headline');
    const detailEl = document.getElementById('sonos-system-detail');
    if (!grid || !headlineEl || !detailEl) return;

    const state = {
        speakers: [],
        activeSpeaker: null,
        preferredSpeaker: null,
        source: null,
        speakerStates: {},
        volumeDrag: null,
        socketConnected: false,
    };

    function isPreferredSpeaker(name) {
        const pref = String(state.preferredSpeaker || '').trim().toLowerCase();
        if (!pref || !name) return false;
        return String(name).toLowerCase().includes(pref);
    }

    function selectButtonLabel(name) {
        if (isPreferredSpeaker(name)) return 'Default';
        if (name === state.activeSpeaker) return 'Active';
        return 'Set default';
    }

    function getSocket() {
        return window.SCCS?.socket ?? null;
    }

    function speakerIcon(name) {
        const key = String(name || '').toLowerCase();
        for (const [fragment, icon] of Object.entries(SPEAKER_ICONS)) {
            if (key.includes(fragment)) return icon;
        }
        return 'fa-music';
    }

    function normalizeState(data) {
        if (!data) return null;
        return {
            speaker: data.speaker ?? data.room,
            title: data.title ?? data.track ?? 'Nothing playing',
            artist: data.artist ?? '',
            playing: Boolean(data.playing ?? data.is_playing),
            muted: Boolean(data.muted ?? data.mute),
            volume: Number(data.volume) || 0,
            elapsed_seconds: Number(data.elapsed_seconds ?? data.position ?? 0) || 0,
            duration_seconds: Number(data.duration_seconds ?? data.duration ?? 0) || 0,
            album_art: data.album_art ?? null,
            enabled: data.enabled !== false,
        };
    }

    function getSpeakerView(name) {
        const cached = state.speakerStates[name];
        if (!cached) {
            return {
                title: '—',
                artist: 'Waiting for state',
                playing: false,
                muted: false,
                volume: 0,
                elapsed_seconds: 0,
                duration_seconds: 0,
                album_art: null,
                reachable: true,
            };
        }
        return {
            title: cached.title || '—',
            artist: cached.artist || '—',
            playing: cached.playing,
            muted: cached.muted,
            volume: cached.volume,
            elapsed_seconds: cached.elapsed_seconds,
            duration_seconds: cached.duration_seconds,
            album_art: cached.album_art,
            reachable: true,
        };
    }

    function formatTime(seconds) {
        const total = Math.max(0, Math.round(Number(seconds) || 0));
        const mins = Math.floor(total / 60);
        const secs = total % 60;
        return `${mins}:${String(secs).padStart(2, '0')}`;
    }

    function availabilityMessage(data) {
        if (data?.message) return data.message;
        if (data?.source === 'unavailable') {
            return 'Sonos module not loaded — check logs and the soco package';
        }
        return 'Discovery running on the local network';
    }

    function renderSummary(extra) {
        if (extra?.source === 'unavailable' || state.source === 'unavailable') {
            headlineEl.textContent = 'Sonos unavailable';
            detailEl.textContent = availabilityMessage(extra || { source: state.source });
            detailEl.classList.add('is-warning');
            return;
        }

        if (!state.speakers.length) {
            headlineEl.textContent = 'No speakers found';
            detailEl.textContent = 'Discovery running on the local network';
            detailEl.classList.add('is-warning');
            return;
        }

        const active = state.activeSpeaker || state.speakers[0];
        const view = getSpeakerView(active);
        const isDefault = isPreferredSpeaker(active);
        headlineEl.textContent = isDefault ? `${active} · default` : `${active} selected`;

        if (!view.reachable) {
            detailEl.textContent = 'Speaker unreachable';
            detailEl.classList.add('is-warning');
            return;
        }

        const status = view.playing ? 'Playing' : 'Paused';
        const level = view.muted ? 'Muted' : `${view.volume}%`;
        const defaultHint = isDefault ? '' : ' · tap Set default to remember';
        detailEl.textContent = `${status} · ${level} · ${view.title}${defaultHint}`;
        detailEl.classList.toggle('is-warning', false);
    }

    function renderCard(name) {
        const view = getSpeakerView(name);
        const isActive = name === state.activeSpeaker;
        const isPreferred = isPreferredSpeaker(name);
        const progress = view.duration_seconds > 0
            ? Math.min(100, (view.elapsed_seconds / view.duration_seconds) * 100)
            : 0;
        const controlsDisabled = !view.reachable || !isActive;
        const icon = speakerIcon(name);

        const modifiers = [
            isActive ? 'is-active' : '',
            isPreferred ? 'is-default' : '',
            view.reachable ? 'is-live' : 'is-offline',
            view.playing ? 'is-playing' : 'is-paused',
            view.muted ? 'is-muted' : '',
        ].filter(Boolean).join(' ');

        const artHtml = view.album_art
            ? `<img class="sonos-system-tile__art" src="${view.album_art}" alt="" decoding="async">`
            : `<div class="sonos-system-tile__art sonos-system-tile__art--placeholder" aria-hidden="true">
                   <i class="fa-solid ${icon}"></i>
               </div>`;

        return `
            <article class="sonos-system-tile__card ${modifiers}"
                     data-speaker-name="${name}"
                     role="listitem"
                     aria-label="${name} speaker">
                <div class="sonos-system-tile__media">
                    ${artHtml}
                    <div class="sonos-system-tile__meta">
                        <h3 class="sonos-system-tile__room">${name}</h3>
                        <p class="sonos-system-tile__track">${view.title}</p>
                        <p class="sonos-system-tile__artist">${view.artist}</p>
                    </div>
                </div>

                ${view.reachable ? `
                    <div class="sonos-tile__progress" aria-hidden="true">
                        <div class="sonos-tile__progress-bar">
                            <div class="sonos-tile__progress-fill" style="--sonos-progress: ${progress}%"></div>
                        </div>
                        <div class="sonos-tile__times">
                            <span>${formatTime(view.elapsed_seconds)}</span>
                            <span>${formatTime(view.duration_seconds)}</span>
                        </div>
                    </div>
                ` : ''}

                <div class="sonos-tile__transport" role="group" aria-label="${name} playback">
                    <button type="button"
                            class="sonos-tile__transport-btn"
                            data-sonos-action="previous"
                            aria-label="Previous track"
                            ${controlsDisabled ? 'disabled' : ''}>
                        <i class="fa-solid fa-backward-step" aria-hidden="true"></i>
                    </button>
                    <button type="button"
                            class="sonos-tile__transport-btn sonos-tile__transport-btn--primary"
                            data-sonos-action="toggle"
                            aria-label="${view.playing ? 'Pause' : 'Play'}"
                            ${controlsDisabled ? 'disabled' : ''}>
                        <i class="fa-solid ${view.playing ? 'fa-pause' : 'fa-play'}" aria-hidden="true"></i>
                    </button>
                    <button type="button"
                            class="sonos-tile__transport-btn"
                            data-sonos-action="next"
                            aria-label="Next track"
                            ${controlsDisabled ? 'disabled' : ''}>
                        <i class="fa-solid fa-forward-step" aria-hidden="true"></i>
                    </button>
                </div>

                <div class="sonos-tile__volume-group">
                    <button type="button"
                            class="sonos-tile__mute-btn${view.muted ? ' is-muted' : ''}"
                            data-sonos-action="mute"
                            aria-label="${view.muted ? 'Unmute' : 'Mute'}"
                            aria-pressed="${view.muted ? 'true' : 'false'}"
                            ${controlsDisabled ? 'disabled' : ''}>
                        <i class="fa-solid ${view.muted ? 'fa-volume-xmark' : 'fa-volume-high'}" aria-hidden="true"></i>
                    </button>
                    <div class="sonos-tile__volume-wrap">
                        <div class="sonos-tile__volume-track">
                            <div class="sonos-tile__volume-fill-clip" aria-hidden="true">
                                <div class="sonos-tile__volume-fill" style="--sonos-volume: ${view.volume}%"></div>
                            </div>
                        </div>
                        <input type="range"
                               class="sonos-tile__volume"
                               data-sonos-volume="${name}"
                               min="0"
                               max="100"
                               value="${view.volume}"
                               aria-label="${name} volume"
                               ${controlsDisabled ? 'disabled' : ''}>
                    </div>
                    <span class="sonos-tile__volume-pct">${view.volume}%</span>
                </div>

                <div class="sonos-system-tile__footer">
                    <span class="sonos-system-tile__link${view.reachable ? ' is-up' : ' is-down'}">
                        <span class="sonos-system-tile__link-dot" aria-hidden="true"></span>
                        <span>${view.reachable ? (view.playing ? 'Playing' : 'Paused') : 'Offline'}</span>
                    </span>
                    <button type="button"
                            class="sonos-system-tile__select${isPreferred ? ' is-selected' : ''}"
                            data-sonos-action="select"
                            aria-pressed="${isPreferred ? 'true' : 'false'}"
                            title="Use this speaker and save as the default in sccs.conf">
                        ${selectButtonLabel(name)}
                    </button>
                </div>
            </article>`;
    }

    function setCardText(card, selector, text) {
        const el = card.querySelector(selector);
        if (el) el.textContent = text;
    }

    function updateCardInPlace(card, name, { skipVolume = false } = {}) {
        const view = getSpeakerView(name);
        const isActive = name === state.activeSpeaker;
        const isPreferred = isPreferredSpeaker(name);
        const progress = view.duration_seconds > 0
            ? Math.min(100, (view.elapsed_seconds / view.duration_seconds) * 100)
            : 0;
        const controlsDisabled = !view.reachable || !isActive;

        card.className = [
            'sonos-system-tile__card',
            isActive ? 'is-active' : '',
            isPreferred ? 'is-default' : '',
            view.reachable ? 'is-live' : 'is-offline',
            view.playing ? 'is-playing' : 'is-paused',
            view.muted ? 'is-muted' : '',
        ].filter(Boolean).join(' ');

        setCardText(card, '.sonos-system-tile__track', view.title);
        setCardText(card, '.sonos-system-tile__artist', view.artist);

        const artImg = card.querySelector('.sonos-system-tile__art[src]');
        if (view.album_art) {
            if (artImg) {
                if (artImg.getAttribute('src') !== view.album_art) {
                    artImg.setAttribute('src', view.album_art);
                }
            } else {
                const placeholder = card.querySelector('.sonos-system-tile__art--placeholder');
                if (placeholder) {
                    const img = document.createElement('img');
                    img.className = 'sonos-system-tile__art';
                    img.src = view.album_art;
                    img.alt = '';
                    img.decoding = 'async';
                    placeholder.replaceWith(img);
                }
            }
        }

        const progressFill = card.querySelector('.sonos-tile__progress-fill');
        if (progressFill) {
            progressFill.style.setProperty('--sonos-progress', `${progress}%`);
        }

        const times = card.querySelectorAll('.sonos-tile__times span');
        if (times.length >= 2) {
            times[0].textContent = formatTime(view.elapsed_seconds);
            times[1].textContent = formatTime(view.duration_seconds);
        }

        card.querySelectorAll('[data-sonos-action]').forEach((button) => {
            const action = button.dataset.sonosAction;
            if (action === 'select') return;
            button.disabled = controlsDisabled;
        });

        const playBtn = card.querySelector('[data-sonos-action="toggle"]');
        if (playBtn) {
            playBtn.setAttribute('aria-label', view.playing ? 'Pause' : 'Play');
            const icon = playBtn.querySelector('i');
            if (icon) {
                icon.className = `fa-solid ${view.playing ? 'fa-pause' : 'fa-play'}`;
            }
        }

        const muteBtn = card.querySelector('[data-sonos-action="mute"]');
        if (muteBtn) {
            muteBtn.classList.toggle('is-muted', view.muted);
            muteBtn.setAttribute('aria-label', view.muted ? 'Unmute' : 'Mute');
            muteBtn.setAttribute('aria-pressed', view.muted ? 'true' : 'false');
            const icon = muteBtn.querySelector('i');
            if (icon) {
                icon.className = `fa-solid ${view.muted ? 'fa-volume-xmark' : 'fa-volume-high'}`;
            }
        }

        if (!skipVolume) {
            const slider = card.querySelector('[data-sonos-volume]');
            if (slider && document.activeElement !== slider) {
                slider.value = String(view.volume);
                slider.disabled = controlsDisabled;
            }
            updateCardVolumeUi(card, view.volume);
        }

        const linkDot = card.querySelector('.sonos-system-tile__link');
        if (linkDot) {
            linkDot.classList.toggle('is-up', view.reachable);
            linkDot.classList.toggle('is-down', !view.reachable);
            const linkText = linkDot.querySelector('span:last-child');
            if (linkText) {
                linkText.textContent = view.reachable
                    ? (view.playing ? 'Playing' : 'Paused')
                    : 'Offline';
            }
        }

        const selectBtn = card.querySelector('[data-sonos-action="select"]');
        if (selectBtn) {
            selectBtn.classList.toggle('is-selected', isPreferred);
            selectBtn.setAttribute('aria-pressed', isPreferred ? 'true' : 'false');
            selectBtn.textContent = selectButtonLabel(name);
            selectBtn.title = 'Use this speaker and save as the default in sccs.conf';
        }
    }

    function ensureCard(name) {
        const existing = grid.querySelector(`[data-speaker-name="${CSS.escape(name)}"]`);
        if (existing) return existing;

        const wrap = document.createElement('div');
        wrap.innerHTML = renderCard(name);
        return wrap.firstElementChild;
    }

    function syncSpeakerGrid(extra) {
        renderSummary(extra);

        if ((extra?.source || state.source) === 'unavailable') {
            grid.innerHTML = `<p class="sonos-system-tile__empty">${availabilityMessage(extra)}</p>`;
            return;
        }

        const names = state.speakers.length ? state.speakers : [];
        if (!names.length) {
            grid.innerHTML = '';
            return;
        }

        const emptyMsg = grid.querySelector('.sonos-system-tile__empty');
        if (emptyMsg) emptyMsg.remove();

        const keep = new Set(names);
        grid.querySelectorAll('[data-speaker-name]').forEach((card) => {
            if (!keep.has(card.dataset.speakerName)) {
                card.remove();
            }
        });

        names.forEach((name) => {
            const skipVolume = state.volumeDrag === name;
            let card = grid.querySelector(`[data-speaker-name="${CSS.escape(name)}"]`);
            if (!card) {
                card = ensureCard(name);
                grid.appendChild(card);
            } else {
                updateCardInPlace(card, name, { skipVolume });
            }
        });

        names.forEach((name, index) => {
            const card = grid.querySelector(`[data-speaker-name="${CSS.escape(name)}"]`);
            if (!card) return;
            const current = grid.children[index];
            if (current !== card) {
                grid.insertBefore(card, current || null);
            }
        });
    }

    function render(extra) {
        syncSpeakerGrid(extra);
    }

    function onAvailabilityUpdate(data) {
        state.source = data?.source || 'unavailable';
        state.speakers = [];
        state.activeSpeaker = null;
        state.speakerStates = {};
        window.sonosTile?.handleStatus?.(data);
        render(data);
    }

    function syncHomeTile(data) {
        if (window.sonosTile?.onSocketUpdate && data) {
            window.sonosTile.onSocketUpdate(data);
        } else if (window.sonosTile?.update && data) {
            window.sonosTile.update(data);
        }
    }

    function onSpeakersUpdate(data) {
        if (!data) return;
        if (data.source === 'unavailable') {
            onAvailabilityUpdate(data);
            return;
        }
        state.source = data.source || 'live';
        state.speakers = Array.isArray(data.speakers) ? data.speakers.slice() : [];
        if (Object.prototype.hasOwnProperty.call(data, 'preferred')) {
            state.preferredSpeaker = data.preferred || null;
        }
        if (data.current) {
            state.activeSpeaker = data.current;
            window.sonosTile?.setActiveSpeaker?.(data.current);
        } else if (!state.activeSpeaker && state.speakers.length) {
            state.activeSpeaker = state.speakers[0];
            window.sonosTile?.setActiveSpeaker?.(state.activeSpeaker);
        }
        // Keep home tile online/offline in sync with discovery results.
        window.sonosTile?.handleStatus?.({
            speakers: state.speakers,
            speaker: state.activeSpeaker,
            source: state.source,
        });
        render();
    }

    function onSocketUpdate(data) {
        if (!data) return;
        if (data.source === 'unavailable') {
            onAvailabilityUpdate(data);
            return;
        }
        state.source = data.source || state.source || 'live';
        if (Array.isArray(data.speakers)) {
            state.speakers = data.speakers.slice();
        }
        const normalized = normalizeState(data);
        if (!normalized?.speaker) {
            // Speakers-only / empty updates still drive Home tile visibility.
            window.sonosTile?.handleStatus?.(data);
            render();
            return;
        }
        state.speakerStates[normalized.speaker] = normalized;
        if (normalized.speaker === state.activeSpeaker) {
            syncHomeTile(normalized);
        }
        const card = grid.querySelector(`[data-speaker-name="${CSS.escape(normalized.speaker)}"]`);
        if (card) {
            updateCardInPlace(card, normalized.speaker, {
                skipVolume: state.volumeDrag === normalized.speaker,
            });
            renderSummary();
        } else {
            render();
        }
    }

    function emitCommand(command, { speaker, value } = {}) {
        const socket = getSocket();
        if (!socket?.connected) return false;
        const payload = { command };
        if (speaker) payload.speaker = speaker;
        if (value !== undefined) payload.value = value;
        socket.emit('sonos_command', payload);
        return true;
    }

    async function postJson(url, body) {
        const res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) return null;
        return res.json();
    }

    async function fetchStatus() {
        try {
            const res = await fetch('/api/sonos', { cache: 'no-store' });
            if (!res.ok) return;
            onSocketUpdate(await res.json());
        } catch {
            /* keep last values */
        }
    }

    async function sendTransport(action) {
        const mapping = {
            toggle: 'playpause',
            play: 'play',
            pause: 'pause',
            next: 'next',
            previous: 'previous',
        };
        const command = mapping[action] || action;
        if (emitCommand(command, { speaker: state.activeSpeaker })) return;
        const data = await postJson('/api/sonos/transport', { action });
        if (data) onSocketUpdate(data);
    }

    async function sendVolume(level) {
        if (emitCommand('volume', { speaker: state.activeSpeaker, value: parseInt(level, 10) })) return;
        const data = await postJson('/api/sonos/volume', { level });
        if (data) onSocketUpdate(data);
    }

    async function sendMute(muted) {
        if (emitCommand('mute', { speaker: state.activeSpeaker, value: Boolean(muted) })) return;
        const data = await postJson('/api/sonos/mute', { muted });
        if (data) onSocketUpdate(data);
    }

    function setActiveSpeaker(name) {
        if (!state.speakers.includes(name)) return;
        state.activeSpeaker = name;
        // Optimistic: Selecting on the System tab also sets the saved default.
        state.preferredSpeaker = name;
        window.sonosTile?.setActiveSpeaker?.(name);
        const socket = getSocket();
        if (socket?.connected) {
            socket.emit('sonos_switch_speaker', { name, make_default: true });
        }
        const cached = state.speakerStates[name];
        if (cached) syncHomeTile(cached);
        grid.querySelectorAll('[data-speaker-name]').forEach((card) => {
            updateCardInPlace(card, card.dataset.speakerName, {
                skipVolume: state.volumeDrag === card.dataset.speakerName,
            });
        });
        renderSummary();
    }

    grid.addEventListener('click', (event) => {
        const button = event.target.closest('[data-sonos-action]');
        if (!button || button.disabled) return;

        const card = button.closest('[data-speaker-name]');
        if (!card) return;

        const name = card.dataset.speakerName;
        const action = button.dataset.sonosAction;

        if (action === 'select') {
            setActiveSpeaker(name);
            return;
        }

        if (name !== state.activeSpeaker) return;

        if (action === 'toggle' || action === 'previous' || action === 'next') {
            sendTransport(action);
            return;
        }

        if (action === 'mute') {
            const view = getSpeakerView(name);
            sendMute(!view.muted);
        }
    });

    grid.addEventListener('contextmenu', (event) => {
        if (event.target.closest('[data-sonos-volume], .sonos-tile__volume-wrap')) {
            event.preventDefault();
        }
    });

    grid.addEventListener('pointerdown', (event) => {
        const slider = event.target.closest('[data-sonos-volume]');
        if (!slider || slider.disabled) return;
        state.volumeDrag = slider.dataset.sonosVolume;
        if (event.pointerType === 'touch') {
            event.preventDefault();
            slider.setPointerCapture(event.pointerId);
        }
    });

    function updateCardVolumeUi(card, level) {
        if (!card) return;
        const vol = Math.max(0, Math.min(100, Math.round(Number(level) || 0)));
        const fill = card.querySelector('.sonos-tile__volume-fill');
        if (fill) fill.style.setProperty('--sonos-volume', `${vol}%`);
        const pct = card.querySelector('.sonos-tile__volume-pct');
        if (pct) pct.textContent = `${vol}%`;
    }

    grid.addEventListener('input', (event) => {
        const slider = event.target.closest('[data-sonos-volume]');
        if (!slider || slider.disabled) return;

        updateCardVolumeUi(slider.closest('[data-speaker-name]'), slider.value);
    });

    grid.addEventListener('pointerup', (event) => {
        const slider = event.target.closest('[data-sonos-volume]');
        if (slider?.hasPointerCapture(event.pointerId)) {
            slider.releasePointerCapture(event.pointerId);
        }
        state.volumeDrag = null;
    });

    grid.addEventListener('pointercancel', (event) => {
        const slider = event.target.closest('[data-sonos-volume]');
        if (slider?.hasPointerCapture(event.pointerId)) {
            slider.releasePointerCapture(event.pointerId);
        }
        state.volumeDrag = null;
    });

    grid.addEventListener('change', (event) => {
        const slider = event.target.closest('[data-sonos-volume]');
        if (!slider || slider.disabled) return;

        const name = slider.closest('[data-speaker-name]')?.dataset.speakerName;
        if (name !== state.activeSpeaker) return;

        state.volumeDrag = null;
        sendVolume(Number(slider.value));
    });

    function registerSocket(socket) {
        if (!socket) return;
        socket.on('connect', () => {
            state.socketConnected = true;
        });
        socket.on('disconnect', () => {
            state.socketConnected = false;
        });
        state.socketConnected = socket.connected;
    }

    async function pollStatus() {
        if (!window.SCCS?.isSystemTabActive) return;
        await fetchStatus();
    }

    render();
    setInterval(pollStatus, POLL_INTERVAL_MS);

    const socket = getSocket();
    if (socket) {
        registerSocket(socket);
    } else {
        document.addEventListener('sccs:socket-ready', (event) => {
            registerSocket(event.detail?.socket);
        }, { once: true });
    }

    window.sonosSystemTile = {
        setActiveSpeaker,
        onSocketUpdate,
        onSpeakersUpdate,
        onAvailabilityUpdate,
        fetchStatus,
        getState: () => ({
            activeSpeaker: state.activeSpeaker,
            speakers: state.speakers.slice(),
            speakerStates: { ...state.speakerStates },
        }),
        refresh: render,
    };
})();