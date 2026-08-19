/**
 * SCCS Phases tile — schedule, timing editors, and force controls (socket-backed).
 */
(function () {
    'use strict';

    const TICK_INTERVAL_MS = 30000;
    const DAY_MINUTES = 24 * 60;

    const PHASE_ORDER = ['Day', 'Evening', 'Night'];

    const PHASE_META = {
        Day: { icon: 'fa-sun', tileClass: 'is-day', trackClass: 'phases-tile__track-segment--day' },
        Evening: { icon: 'fa-cloud-sun', tileClass: 'is-evening', trackClass: 'phases-tile__track-segment--evening' },
        Night: { icon: 'fa-moon', tileClass: 'is-night', trackClass: 'phases-tile__track-segment--night' },
    };

    const tile = document.getElementById('tile-phases');
    const els = {
        summaryIcon: document.getElementById('phases-summary-icon'),
        headline: document.getElementById('phases-headline'),
        detail: document.getElementById('phases-detail'),
        next: document.getElementById('phases-next'),
        trackBar: document.getElementById('phases-track-bar'),
        trackNow: document.getElementById('phases-track-now'),
        schedule: document.getElementById('phases-schedule'),
        clearBtn: document.getElementById('phases-clear-btn'),
        segments: document.querySelectorAll('.phases-tile__segment'),
        dayOffset: document.getElementById('phases-day-offset'),
        eveningOffset: document.getElementById('phases-evening-offset'),
        nightHour: document.getElementById('phases-night-hour'),
        timingSave: document.getElementById('phases-timing-save'),
        timingCancel: document.getElementById('phases-timing-cancel'),
        timingHint: document.getElementById('phases-timing-hint'),
        timingRoot: document.getElementById('phases-timing'),
    };

    if (!tile || !els.headline) return;

    const state = {
        times: {
            day_start: '—',
            evening_start: '—',
            night_start: '—',
        },
        settings: {
            day_offset_minutes: 45,
            evening_offset_minutes: 45,
            night_start_minutes: 1200, // 20:00
        },
        // Last saved/server values — Cancel restores these.
        baselineSettings: {
            day_offset_minutes: 45,
            evening_offset_minutes: 45,
            night_start_minutes: 1200,
        },
        forcedPhase: null,
        serverPhase: null,
        savingTiming: false,
    };

    // 19:30 (1170) … midnight (1440) in 30-minute steps.
    const NIGHT_MIN = 19 * 60 + 30;
    const NIGHT_MAX = 24 * 60;

    function getSocket() {
        return window.SCCS?.socket ?? null;
    }

    function notifyToast(type, title, message, duration = 5000) {
        window.sccsToasts?.create?.({ type, title, message, duration });
    }

    function clampNightMinutes(value, fallback = 1200) {
        let n = parseInt(value, 10);
        if (Number.isNaN(n)) n = fallback;
        n = Math.min(NIGHT_MAX, Math.max(NIGHT_MIN, n));
        return Math.round(n / 30) * 30;
    }

    function formatNightMinutesLabel(mins) {
        const m = clampNightMinutes(mins, 1200);
        if (m >= 24 * 60) return 'Midnight';
        const hours = Math.floor(m / 60);
        const minutes = m % 60;
        const meridiem = hours >= 12 ? 'PM' : 'AM';
        const hour12 = hours % 12 === 0 ? 12 : hours % 12;
        return `${hour12}:${String(minutes).padStart(2, '0')} ${meridiem}`;
    }

    function clampInt(value, min, max, fallback) {
        const n = parseInt(value, 10);
        if (Number.isNaN(n)) return fallback;
        return Math.min(max, Math.max(min, n));
    }

    function populateNightHourSelect() {
        if (!els.nightHour || els.nightHour.options.length) return;
        for (let mins = NIGHT_MIN; mins <= NIGHT_MAX; mins += 30) {
            const opt = document.createElement('option');
            opt.value = String(mins);
            opt.textContent = formatNightMinutesLabel(mins);
            els.nightHour.appendChild(opt);
        }
    }

    function snapshotSettings(settings) {
        return {
            day_offset_minutes: clampInt(settings.day_offset_minutes, 0, 180, 45),
            evening_offset_minutes: clampInt(settings.evening_offset_minutes, 0, 180, 45),
            night_start_minutes: clampNightMinutes(
                settings.night_start_minutes
                    ?? (settings.night_start_hour != null
                        ? Number(settings.night_start_hour) * 60
                        : state.settings.night_start_minutes),
                1200,
            ),
        };
    }

    function applySettingsToForm(settings, { asBaseline = false } = {}) {
        if (!settings) return;
        if (settings.day_offset_minutes != null) {
            state.settings.day_offset_minutes = clampInt(settings.day_offset_minutes, 0, 180, 45);
            if (els.dayOffset) els.dayOffset.value = String(state.settings.day_offset_minutes);
        }
        if (settings.evening_offset_minutes != null) {
            state.settings.evening_offset_minutes = clampInt(settings.evening_offset_minutes, 0, 180, 45);
            if (els.eveningOffset) els.eveningOffset.value = String(state.settings.evening_offset_minutes);
        }
        let nightMins = settings.night_start_minutes;
        if (nightMins == null && settings.night_start_hour != null) {
            nightMins = Number(settings.night_start_hour) * 60;
        }
        if (nightMins != null) {
            state.settings.night_start_minutes = clampNightMinutes(nightMins, 1200);
            if (els.nightHour) els.nightHour.value = String(state.settings.night_start_minutes);
        }
        if (asBaseline) {
            state.baselineSettings = snapshotSettings(state.settings);
        }
        updateTimingHint();
        syncTimingActions();
    }

    function cancelTimingEdits() {
        applySettingsToForm(state.baselineSettings, { asBaseline: false });
        // Re-sync inputs strictly from baseline (applySettings already set them).
        if (els.dayOffset) els.dayOffset.value = String(state.baselineSettings.day_offset_minutes);
        if (els.eveningOffset) els.eveningOffset.value = String(state.baselineSettings.evening_offset_minutes);
        if (els.nightHour) els.nightHour.value = String(state.baselineSettings.night_start_minutes);
        state.settings = snapshotSettings(state.baselineSettings);
        updateTimingHint();
        syncTimingActions();
    }

    function isTimingDirty() {
        const form = readFormSettings();
        const base = state.baselineSettings;
        return (
            form.day_offset_minutes !== base.day_offset_minutes
            || form.evening_offset_minutes !== base.evening_offset_minutes
            || form.night_start_minutes !== base.night_start_minutes
        );
    }

    function syncTimingActions() {
        const actions = els.timingSave?.closest('.phases-tile__timing-actions')
            || els.timingCancel?.closest('.phases-tile__timing-actions');
        if (!actions) return;
        // Keep actions visible while a save is in flight even if values match.
        const show = state.savingTiming || isTimingDirty();
        actions.hidden = !show;
    }

    function updateTimingHint() {
        if (!els.timingHint) return;
        const day = state.settings.day_offset_minutes;
        const eve = state.settings.evening_offset_minutes;
        const dayAt = stripLeadingZero(state.times.day_start) || '—';
        const eveAt = stripLeadingZero(state.times.evening_start) || '—';
        const nightAt = stripLeadingZero(state.times.night_start) || '—';
        els.timingHint.textContent =
            `Day ${dayAt} (+${day} min after sunrise) · Evening ${eveAt} (−${eve} min before sunset) · Night ${nightAt}`;
    }

    function readFormSettings() {
        return {
            day_offset_minutes: clampInt(els.dayOffset?.value, 0, 180, state.settings.day_offset_minutes),
            evening_offset_minutes: clampInt(els.eveningOffset?.value, 0, 180, state.settings.evening_offset_minutes),
            night_start_minutes: clampNightMinutes(
                els.nightHour?.value,
                state.settings.night_start_minutes,
            ),
        };
    }

    function stepOffset(which, delta) {
        const input = which === 'evening' ? els.eveningOffset : els.dayOffset;
        if (!input) return;
        const next = clampInt(Number(input.value || 0) + Number(delta), 0, 180, 0);
        input.value = String(next);
        state.settings = readFormSettings();
        updateTimingHint();
        syncTimingActions();
    }

    function onTimingFieldChange() {
        state.settings = readFormSettings();
        updateTimingHint();
        syncTimingActions();
    }

    async function saveTiming() {
        if (state.savingTiming || !isTimingDirty()) return;
        const payload = readFormSettings();
        state.savingTiming = true;
        syncTimingActions();
        if (els.timingSave) els.timingSave.disabled = true;
        if (els.timingCancel) els.timingCancel.disabled = true;

        try {
            const res = await fetch('/api/phases/timing', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const data = await res.json();
            if (!res.ok || !data.ok) {
                notifyToast('error', 'Phases', data.error || 'Could not save phase timing');
                return;
            }

            if (data.settings) applySettingsToForm(data.settings, { asBaseline: true });
            if (data.times) {
                onPhaseUpdate({
                    phase: data.phase,
                    ...data.times,
                });
            }
            if (data.forced && data.phase) {
                onPhaseDiagUpdate({ forced: true });
            } else {
                onPhaseDiagUpdate({ forced: false });
            }

            notifyToast('success', 'Phases', data.message || 'Phase timing saved');
        } catch (error) {
            notifyToast('error', 'Phases', error.message || 'Could not save phase timing');
        } finally {
            state.savingTiming = false;
            if (els.timingSave) els.timingSave.disabled = false;
            if (els.timingCancel) els.timingCancel.disabled = false;
            syncTimingActions();
        }
    }

    function stripLeadingZero(str) {
        return str ? str.replace(/^0(\d):/, '$1:') : str;
    }

    function parseTimeToMinutes(value) {
        if (!value || value === '—') return null;

        const match = String(value)
            .trim()
            .match(/^(\d{1,2}):(\d{2})\s*(AM|PM)?$/i);
        if (!match) return null;

        let hours = parseInt(match[1], 10);
        const minutes = parseInt(match[2], 10);
        const meridiem = match[3]?.toUpperCase();

        if (meridiem === 'PM' && hours !== 12) hours += 12;
        if (meridiem === 'AM' && hours === 12) hours = 0;

        return hours * 60 + minutes;
    }

    function formatDuration(totalMinutes) {
        const minutes = Math.max(0, Math.round(totalMinutes));
        const hours = Math.floor(minutes / 60);
        const mins = minutes % 60;
        if (hours > 0 && mins > 0) return `${hours}h ${mins}m`;
        if (hours > 0) return `${hours}h`;
        return `${mins}m`;
    }

    function getScheduleMinutes() {
        return {
            day: parseTimeToMinutes(state.times.day_start),
            evening: parseTimeToMinutes(state.times.evening_start),
            night: parseTimeToMinutes(state.times.night_start),
        };
    }

    function resolveAutomaticPhase(now = new Date()) {
        const { day, evening, night } = getScheduleMinutes();
        const nowMinutes = now.getHours() * 60 + now.getMinutes();

        if (day === null || evening === null || night === null) {
            return 'Day';
        }

        if (nowMinutes < day || nowMinutes >= night) {
            return 'Night';
        }
        if (nowMinutes >= evening) {
            return 'Evening';
        }
        return 'Day';
    }

    function activePhase() {
        if (state.forcedPhase) return state.forcedPhase;
        if (state.serverPhase) return state.serverPhase;
        return resolveAutomaticPhase();
    }

    function nextAutomaticTransition(now = new Date()) {
        const { day, evening, night } = getScheduleMinutes();
        const nowMinutes = now.getHours() * 60 + now.getMinutes();

        if (day === null || evening === null || night === null) {
            return null;
        }

        const transitions = [
            { at: day, phase: 'Day' },
            { at: evening, phase: 'Evening' },
            { at: night, phase: 'Night' },
            { at: day + DAY_MINUTES, phase: 'Day' },
        ].sort((a, b) => a.at - b.at);

        const upcoming = transitions.find((entry) => entry.at > nowMinutes);
        if (!upcoming) {
            return { phase: 'Day', minutesUntil: (day + DAY_MINUTES) - nowMinutes };
        }

        return {
            phase: upcoming.phase,
            minutesUntil: upcoming.at - nowMinutes,
        };
    }

    function buildTrackSegments() {
        const { day, evening, night } = getScheduleMinutes();
        if (day === null || evening === null || night === null) {
            return [];
        }

        return [
            { phase: 'Night', start: 0, end: day },
            { phase: 'Day', start: day, end: evening },
            { phase: 'Evening', start: evening, end: night },
            { phase: 'Night', start: night, end: DAY_MINUTES },
        ];
    }

    function setPhaseClasses(phase) {
        tile.classList.remove('is-day', 'is-evening', 'is-night');
        const meta = PHASE_META[phase];
        if (meta?.tileClass) tile.classList.add(meta.tileClass);
    }

    function renderSummary(phase, now = new Date()) {
        const meta = PHASE_META[phase] || PHASE_META.Day;

        if (els.summaryIcon) {
            els.summaryIcon.className = `fa-solid ${meta.icon} phases-tile__summary-icon`;
        }
        if (els.headline) {
            els.headline.textContent = `${phase} phase`;
        }
        if (els.detail) {
            els.detail.textContent = state.forcedPhase ? `Forced to ${state.forcedPhase}` : 'Following schedule';
            els.detail.classList.toggle('is-forced', state.forcedPhase !== null);
        }
        if (els.next) {
            if (state.forcedPhase) {
                els.next.textContent = 'Clear force to resume automatic phases';
            } else {
                const upcoming = nextAutomaticTransition(now);
                if (upcoming) {
                    els.next.textContent = `${upcoming.phase} in ${formatDuration(upcoming.minutesUntil)}`;
                } else {
                    els.next.textContent = '';
                }
            }
        }
    }

    function renderTrack(phase, now = new Date()) {
        if (!els.trackBar || !els.trackNow) return;

        const segments = buildTrackSegments();
        if (!segments.length) {
            els.trackBar.innerHTML = '';
            els.trackNow.style.left = '0%';
            return;
        }

        els.trackBar.innerHTML = segments.map((segment) => {
            const width = ((segment.end - segment.start) / DAY_MINUTES) * 100;
            const meta = PHASE_META[segment.phase];
            const active = segment.phase === phase;
            return `
                <span class="phases-tile__track-segment ${meta.trackClass}${active ? ' is-active' : ''}"
                      style="width: ${width}%"
                      title="${segment.phase}"></span>
            `;
        }).join('');

        const nowMinutes = now.getHours() * 60 + now.getMinutes();
        const markerLeft = (nowMinutes / DAY_MINUTES) * 100;
        els.trackNow.style.left = `${markerLeft}%`;
    }

    function renderSchedule(phase) {
        if (!els.schedule) return;

        const schedule = [
            { phase: 'Day', time: stripLeadingZero(state.times.day_start) },
            { phase: 'Evening', time: stripLeadingZero(state.times.evening_start) },
            { phase: 'Night', time: stripLeadingZero(state.times.night_start) },
        ];

        els.schedule.innerHTML = schedule.map((entry) => {
            const meta = PHASE_META[entry.phase];
            const active = entry.phase === phase;
            return `
                <article class="phases-tile__card${active ? ' is-active' : ''}"
                         data-phase="${entry.phase}"
                         role="listitem"
                         aria-label="${entry.phase} starts at ${entry.time}">
                    <div class="phases-tile__card-top">
                        <i class="fa-solid ${meta.icon} phases-tile__card-icon" aria-hidden="true"></i>
                        <span class="phases-tile__card-badge">${active ? 'Now' : ''}</span>
                    </div>
                    <p class="phases-tile__card-name">${entry.phase}</p>
                    <p class="phases-tile__card-time">${entry.time}</p>
                </article>
            `;
        }).join('');
    }

    function updateForceControls() {
        els.segments.forEach((segment) => {
            const phase = segment.dataset.phase;
            segment.classList.toggle('is-selected', state.forcedPhase === phase);
            segment.setAttribute('aria-pressed', String(state.forcedPhase === phase));
        });

        if (els.clearBtn) {
            els.clearBtn.hidden = state.forcedPhase === null;
        }
    }

    function render(phase = activePhase(), now = new Date()) {
        setPhaseClasses(phase);
        renderSummary(phase, now);
        renderTrack(phase, now);
        renderSchedule(phase);
        updateForceControls();
    }

    function emitForcePhase(phase) {
        const socket = getSocket();
        if (socket?.connected) {
            socket.emit('force_phase', { phase });
        }
    }

    function forcePhase(phase) {
        state.forcedPhase = phase;
        render(phase);
        emitForcePhase(phase);
    }

    function clearForce() {
        state.forcedPhase = null;
        render(activePhase());
        const socket = getSocket();
        if (socket?.connected) {
            socket.emit('force_phase', { phase: null });
        }
    }

    function tick() {
        if (state.forcedPhase) return;
        render(activePhase());
    }

    function onPhaseUpdate(data) {
        if (!data) return;
        if (data.day_start) state.times.day_start = data.day_start;
        if (data.evening_start) state.times.evening_start = data.evening_start;
        if (data.night_start) state.times.night_start = data.night_start;
        if (data.phase) state.serverPhase = data.phase;
        // Do not push config knobs into the form here — that would wipe in-progress
        // edits. Knobs are loaded via /api/phases and after Save.
        render(activePhase());
        updateTimingHint();
        window.SCCS?.datetime?.updatePhaseTimes?.(state.times);
        // Keep datetime sunrise/sunset on the same backend source as phase times.
        if (data.sunrise || data.sunset) {
            window.datetimeTile?.update?.({
                sunrise: data.sunrise,
                sunset: data.sunset,
            });
        }
    }

    function onPhaseDiagUpdate(data) {
        if (!data) return;
        if (!data.forced) {
            state.forcedPhase = null;
        } else if (state.serverPhase) {
            state.forcedPhase = state.serverPhase;
        }
        render(activePhase());
    }

    function bindControls() {
        els.segments.forEach((segment) => {
            segment.addEventListener('click', () => {
                const phase = segment.dataset.phase;
                if (!phase) return;
                forcePhase(phase);
            });
        });

        els.clearBtn?.addEventListener('click', clearForce);

        els.timingRoot?.addEventListener('click', (event) => {
            const stepBtn = event.target.closest('[data-timing-step]');
            if (!stepBtn) return;
            const which = stepBtn.dataset.timingStep;
            const delta = Number(stepBtn.dataset.delta || 0);
            if (which === 'day' || which === 'evening') {
                stepOffset(which, delta);
            }
        });

        els.dayOffset?.addEventListener('input', onTimingFieldChange);
        els.dayOffset?.addEventListener('change', onTimingFieldChange);
        els.eveningOffset?.addEventListener('input', onTimingFieldChange);
        els.eveningOffset?.addEventListener('change', onTimingFieldChange);
        els.nightHour?.addEventListener('change', onTimingFieldChange);
        els.nightHour?.addEventListener('input', onTimingFieldChange);

        els.timingSave?.addEventListener('click', () => {
            saveTiming();
        });

        els.timingCancel?.addEventListener('click', () => {
            cancelTimingEdits();
        });

        syncTimingActions();
    }

    async function loadPhases() {
        if (!window.SCCS?.isSystemTabActive) return;
        try {
            const res = await fetch('/api/phases', { cache: 'no-store' });
            if (!res.ok) return;
            const data = await res.json();
            if (data.settings) applySettingsToForm(data.settings, { asBaseline: true });
            onPhaseUpdate({
                phase: data.phase,
                ...data.times,
            });
            if (data.forced && data.phase) {
                onPhaseDiagUpdate({ forced: true });
            } else {
                onPhaseDiagUpdate({ forced: false });
            }
        } catch {
            /* socket will deliver phase_update */
        }
    }

    function tickIfActive() {
        if (!window.SCCS?.isSystemTabActive) return;
        tick();
    }

    function init() {
        populateNightHourSelect();
        applySettingsToForm(state.settings, { asBaseline: true });
        render(resolveAutomaticPhase());
        bindControls();
        setInterval(tickIfActive, TICK_INTERVAL_MS);
        loadPhases();
    }

    init();

    window.SCCS = window.SCCS || {};
    window.SCCS.phases = {
        onPhaseUpdate,
        onPhaseDiagUpdate,
        forcePhase,
        clearForce,
        refresh: loadPhases,
        getState: () => ({ ...state, phase: activePhase() }),
    };

    window.phasesTile = window.SCCS.phases;
})();