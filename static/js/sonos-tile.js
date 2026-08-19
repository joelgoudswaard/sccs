/**
 * SCCS Sonos tile — transport, mute, volume display, now playing (socket + HTTP fallback).
 */
(function () {
    'use strict';

    const POLL_INTERVAL_MS = 3000;
    const TICK_INTERVAL_MS = 1000;

    const els = {
        art: document.getElementById('sonos-art'),
        track: document.getElementById('sonos-track'),
        artist: document.getElementById('sonos-artist'),
        progress: document.getElementById('sonos-progress'),
        progressFill: document.getElementById('sonos-progress-fill'),
        elapsed: document.getElementById('sonos-elapsed'),
        remaining: document.getElementById('sonos-remaining'),
        prev: document.getElementById('sonos-prev'),
        play: document.getElementById('sonos-play'),
        playIcon: document.getElementById('sonos-play-icon'),
        next: document.getElementById('sonos-next'),
        mute: document.getElementById('sonos-mute'),
        muteIcon: document.getElementById('sonos-mute-icon'),
        volumePct: document.getElementById('sonos-volume-pct'),
    };

    if (!els.track) return;

    const tile = document.getElementById('tile-sonos');
    const labelEl = document.getElementById('sonos-tile-label');
    const controlEls = [
        els.prev,
        els.play,
        els.next,
        els.mute,
    ].filter(Boolean);
    const marqueeEls = [els.track, els.artist].filter(Boolean);
    let controlsDisabled = false;
    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

    let localPlaying = false;
    let activeSpeaker = null;
    let socketConnected = false;

    function getSocket() {
        return window.SCCS?.socket ?? null;
    }

    function normalizeState(data) {
        if (!data) return null;

        const title = data.title ?? data.track ?? 'Nothing playing';
        const playing = data.playing ?? data.is_playing ?? false;
        const muted = data.muted ?? data.mute ?? false;
        const elapsed = data.elapsed_seconds ?? data.elapsed ?? data.position ?? 0;
        const duration = data.duration_seconds ?? data.duration ?? 0;

        return {
            room: data.room ?? data.speaker,
            speaker: data.speaker ?? data.room,
            title,
            artist: data.artist ?? '',
            album: data.album,
            album_art: data.album_art ?? data.album_art_url ?? data.albumArt,
            playing: Boolean(playing),
            muted: Boolean(muted),
            volume: data.volume,
            elapsed_seconds: Number(elapsed) || 0,
            duration_seconds: Number(duration) || 0,
            enabled: data.enabled !== false,
        };
    }

    function marqueeTextEl(el) {
        return el?.querySelector('.sonos-tile__marquee-text') ?? el;
    }

    function setMarqueeText(el, text) {
        if (!el) return;
        marqueeTextEl(el).textContent = text || '—';
        refreshMarquee(el);
    }

    function refreshMarquee(el) {
        if (!el) return;

        el.classList.remove('is-scrolling');
        el.style.removeProperty('--sonos-marquee-distance');
        el.style.removeProperty('--sonos-marquee-duration');

        if (prefersReducedMotion.matches) return;

        const inner = marqueeTextEl(el);
        requestAnimationFrame(() => {
            inner.style.maxWidth = 'none';
            const overflow = inner.scrollWidth - el.clientWidth;
            inner.style.removeProperty('max-width');

            if (overflow <= 2) return;

            el.classList.add('is-scrolling');
            el.style.setProperty('--sonos-marquee-distance', `${overflow}px`);
            el.style.setProperty('--sonos-marquee-duration', `${Math.max(6, overflow / 24)}s`);
        });
    }

    function refreshMarquees() {
        marqueeEls.forEach(refreshMarquee);
    }

    function formatTime(seconds) {
        const total = Math.max(0, Math.round(Number(seconds) || 0));
        const mins = Math.floor(total / 60);
        const secs = total % 60;
        return `${mins}:${String(secs).padStart(2, '0')}`;
    }

    function formatRemaining(seconds) {
        return `-${formatTime(seconds)}`;
    }

    function setPlayState(playing) {
        localPlaying = Boolean(playing);
        if (!els.play || !els.playIcon) return;

        const isPlaying = localPlaying;
        els.play.setAttribute('aria-label', isPlaying ? 'Pause' : 'Play');
        els.playIcon.className = `fa-solid ${isPlaying ? 'fa-pause' : 'fa-play'}`;
    }

    function setMuteState(muted) {
        if (!els.mute || !els.muteIcon) return;
        const isMuted = Boolean(muted);
        els.mute.classList.toggle('is-muted', isMuted);
        els.mute.setAttribute('aria-pressed', String(isMuted));
        els.mute.setAttribute('aria-label', isMuted ? 'Unmute' : 'Mute');
        els.muteIcon.className = `fa-solid ${isMuted ? 'fa-volume-xmark' : 'fa-volume-high'}`;
    }

    function setAlbumArt(url) {
        if (!els.art) return;
        if (url) {
            els.art.src = url;
            els.art.hidden = false;
        } else {
            els.art.hidden = true;
        }
    }

    function setVolume(level) {
        const vol = Math.max(0, Math.min(100, Math.round(Number(level) || 0)));
        if (els.volumePct) {
            els.volumePct.textContent = `${vol}%`;
        }
    }

    function setControlsDisabled(disabled) {
        controlsDisabled = Boolean(disabled);
        controlEls.forEach((el) => {
            el.disabled = controlsDisabled;
        });
    }

    function updateLabel(room) {
        if (!labelEl) return;
        labelEl.textContent = room ? `Sonos - ${room}` : 'Sonos';
    }

    function unavailableMessage(data) {
        if (data?.message) return data.message;
        if (data?.source === 'unavailable') return 'Sonos module not loaded';
        return 'No speakers found';
    }

    function setUnavailable(data) {
        tile?.classList.add('is-unavailable');
        updateLabel(null);
        setMarqueeText(els.track, 'Unavailable');
        setMarqueeText(els.artist, unavailableMessage(data));
        setAlbumArt(null);
        setPlayState(false);
        setMuteState(false);
        setVolume(0);
        setProgress(0, 0);
        setControlsDisabled(true);
        window.SCCS?.homeModuleVisibility?.setSonosOnline?.(false);
    }

    function setAvailable() {
        tile?.classList.remove('is-unavailable');
        setControlsDisabled(false);
        window.SCCS?.homeModuleVisibility?.setSonosOnline?.(true);
    }

    function setProgress(elapsed, duration) {
        const dur = Math.max(0, Number(duration) || 0);
        const el = Math.max(0, Math.min(Number(elapsed) || 0, dur));
        const pct = dur > 0 ? (el / dur) * 100 : 0;
        const rem = Math.max(0, dur - el);

        if (els.progressFill) {
            els.progressFill.style.setProperty('--sonos-progress', `${pct}%`);
        }
        if (els.progress) {
            els.progress.setAttribute('aria-valuenow', String(Math.round(pct)));
        }
        if (els.elapsed) els.elapsed.textContent = formatTime(el);
        if (els.remaining) els.remaining.textContent = formatRemaining(rem);
    }

    function isSonosOnline(data) {
        if (!data) return false;
        // Empty speakers list means discovery found nothing (hide Home tile).
        if (Array.isArray(data.speakers) && data.speakers.length === 0) return false;
        // Unavailable module (init failed) — no speakers either.
        if (data.source === 'unavailable') return false;
        // Need at least a current speaker or non-empty discovery set.
        if (data.speaker || data.room) return true;
        if (Array.isArray(data.speakers) && data.speakers.length > 0) return true;
        // Partial updates without a speakers field while already playing keep online.
        return data.track != null || data.title != null || data.volume != null;
    }

    function handleStatus(data) {
        if (!data) return;
        if (!isSonosOnline(data)) {
            setUnavailable(data);
            return;
        }
        setAvailable();
        update(data);
        if (data.room || data.speaker) {
            updateLabel(data.room || data.speaker);
        }
    }

    function update(data) {
        const state = normalizeState(data);
        if (!state) return;

        if (state.title !== undefined) setMarqueeText(els.track, state.title);
        if (state.artist !== undefined) setMarqueeText(els.artist, state.artist);

        if (state.album_art) {
            setAlbumArt(state.album_art);
        } else {
            setAlbumArt(null);
        }

        if (state.room || state.speaker) {
            updateLabel(state.room || state.speaker);
        }

        if (state.playing !== undefined) setPlayState(state.playing);
        if (state.muted !== undefined) setMuteState(state.muted);
        if (state.volume !== undefined && state.volume !== null) setVolume(state.volume);

        setProgress(state.elapsed_seconds ?? 0, state.duration_seconds ?? 0);
    }

    function onSocketUpdate(data) {
        if (!data) return;
        if (!isSonosOnline(data)) {
            handleStatus(data);
            return;
        }
        if (activeSpeaker && data.speaker && data.speaker !== activeSpeaker) return;
        setAvailable();
        update(data);
    }

    function setActiveSpeaker(name) {
        activeSpeaker = name || null;
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
            handleStatus(await res.json());
        } catch {
            /* keep last values */
        }
    }

    function emitCommand(command, value) {
        const socket = getSocket();
        if (!socket?.connected) return false;
        const payload = { command };
        if (value !== undefined) payload.value = value;
        socket.emit('sonos_command', payload);
        return true;
    }

    async function sendTransport(action) {
        if (controlsDisabled) return;
        const mapping = {
            toggle: 'playpause',
            play: 'play',
            pause: 'pause',
            next: 'next',
            previous: 'previous',
        };
        const command = mapping[action] || action;
        if (emitCommand(command)) return;
        const data = await postJson('/api/sonos/transport', { action });
        if (data) update(data);
    }

    async function sendMute(muted) {
        if (controlsDisabled) return;
        if (emitCommand('mute', Boolean(muted))) return;
        const data = await postJson('/api/sonos/mute', { muted });
        if (data) update(data);
    }

    function bindControls() {
        els.prev?.addEventListener('click', () => sendTransport('previous'));
        els.next?.addEventListener('click', () => sendTransport('next'));
        els.play?.addEventListener('click', () => sendTransport('toggle'));

        els.mute?.addEventListener('click', () => {
            const nextMuted = !els.mute.classList.contains('is-muted');
            sendMute(nextMuted);
        });
    }

    function tickLocalProgress() {
        if (!localPlaying) return;
        const elapsedText = els.elapsed?.textContent || '0:00';
        const remainingText = (els.remaining?.textContent || '-0:00').replace('-', '');
        const elapsed = elapsedText.split(':').reduce((m, s) => m * 60 + Number(s), 0);
        const remaining = remainingText.split(':').reduce((m, s) => m * 60 + Number(s), 0);
        if (remaining <= 0) return;
        setProgress(elapsed + 1, elapsed + remaining);
    }

    function registerSocket(socket) {
        if (!socket) return;
        socket.on('connect', () => {
            socketConnected = true;
        });
        socket.on('disconnect', () => {
            socketConnected = false;
        });
        socketConnected = socket.connected;
    }

    bindControls();
    fetchStatus().then(refreshMarquees);
    setInterval(fetchStatus, POLL_INTERVAL_MS);
    setInterval(tickLocalProgress, TICK_INTERVAL_MS);

    const socket = getSocket();
    if (socket) {
        registerSocket(socket);
    } else {
        document.addEventListener('sccs:socket-ready', (event) => {
            registerSocket(event.detail?.socket);
        }, { once: true });
    }

    window.addEventListener('resize', refreshMarquees);
    prefersReducedMotion.addEventListener('change', refreshMarquees);
    els.art?.addEventListener('load', refreshMarquees);

    if (tile && typeof ResizeObserver !== 'undefined') {
        const resizeObserver = new ResizeObserver(refreshMarquees);
        resizeObserver.observe(tile);
    }

    window.sonosTile = {
        update,
        handleStatus,
        onSocketUpdate,
        setActiveSpeaker,
        refresh: fetchStatus,
        refreshMarquees,
    };
})();