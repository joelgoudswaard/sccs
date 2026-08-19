/**
 * SCCS Socket.IO client — central hub for server → tile handlers.
 */
(function () {
    'use strict';

    window.SCCS = window.SCCS || {};

    function initSocket() {
        if (typeof io === 'undefined') {
            window.SCCS.offline?.show();
            return;
        }

        const socket = io({ transports: ['websocket', 'polling'] });
        window.SCCS.socket = socket;

        window.SCCS.offline?.hide();
        window.SCCS.offline?.register(socket);

        socket.on('connect', () => {
            console.info('[SCCS] socket connected');
            window.SCCS.offline?.hide();
            socket.emit('get_reeds');
            socket.emit('get_reeds_diag');
            socket.emit('get_victron_state');
            socket.emit('get_network_status');
            socket.emit('sonos_request_state');
            window.SCCS.water?.refresh?.();
            window.SCCS.climate?.refreshSensors?.();
            window.powerTile?.refresh?.();
            window.systemTile?.refresh?.();
            window.sonosTile?.refresh?.();
            window.SCCS.location?.refresh?.();
            window.SCCS.gpsStatus?.refresh?.();
            window.SCCS.screensSystem?.refresh?.();
            window.SCCS.reedsSystem?.refresh?.();
            window.SCCS.reedsHome?.refresh?.();
            window.SCCS.lightingHome?.refresh?.();
            window.SCCS.lighting?.syncFromServer?.();
            window.SCCS.phases?.refresh?.();
            window.SCCS.explain?.refresh?.();
            window.SCCS.wifi?.refresh?.({ quiet: true });
            window.sccsCoreTile?.refresh?.();
            window.colorMode?.refresh?.();
            document.dispatchEvent(new CustomEvent('sccs:socket-ready', { detail: { socket } }));
            window.colorMode?.registerSocket?.(socket);
            window.themeManager?.registerSocket?.(socket);
        });

        socket.on('disconnect', () => {
            console.warn('[SCCS] socket disconnected');
        });

        // Lighting
        socket.on('lights_config', (config) => {
            window.SCCS.lighting?.onLightsConfig(config);
            window.SCCS.lightingHome?.onLightsConfig?.(config);
        });

        socket.on('state_update', (state) => {
            window.SCCS.scenes?.onStateUpdate?.(state);
            window.SCCS.lightingHome?.onStateUpdate?.(state);
            const rampMs = state._ramp_ms ?? window.SCCS.lighting?.getSceneRampMs?.();
            const animate = !!state._animate;
            window.SCCS.lighting?.onStateUpdate(state, { rampMs, animate });
            window.SCCS.explain?.refresh?.();
        });

        socket.on('reed_update', (payload) => {
            window.SCCS.lighting?.onReedUpdate(payload);
            window.SCCS.lightingHome?.onReedUpdate?.(payload);
            window.SCCS.reedsHome?.onReedUpdate(payload);
            window.SCCS.lighting?.setReedActivating?.(true);
        });

        // Scenes (state_update handles slider ramp after set_scene)

        // Phases
        socket.on('phase_update', (data) => {
            window.SCCS.phases?.onPhaseUpdate(data);
        });

        socket.on('phase_diag_update', (data) => {
            window.SCCS.phases?.onPhaseDiagUpdate(data);
        });

        // Reeds (diag)
        socket.on('reeds_config', (config) => {
            const reeds = Array.isArray(config) ? config : config?.reeds;
            window.SCCS.reedsSystem?.onReedsConfig(reeds);
            window.SCCS.reedsHome?.onReedsConfig?.(reeds);
        });

        socket.on('reed_diag_update', (payload) => {
            window.SCCS.reedsSystem?.onReedDiagUpdate(payload);
        });

        // GPS
        socket.on('gps_update', (data) => {
            window.SCCS.gpsStatus?.onGpsUpdate(data);
            window.SCCS.location?.onGpsUpdate(data);
        });

        // Sensors (water + temps)
        socket.on('sensor_update', (data) => {
            window.SCCS.water?.onSensorUpdate(data);
            window.SCCS.climate?.onSensorUpdate(data);
        });

        // Toasts
        socket.on('toast', (data) => {
            window.sccsToasts?.handleServer?.(data);
        });

        // Touchscreens
        socket.on('screens_init', (data) => {
            window.SCCS.screensSystem?.onScreensInit?.(data);
        });

        socket.on('screens_update', (data) => {
            window.SCCS.screensSystem?.onScreensUpdate?.(data);
        });

        // Dark mode (phase-driven + manual override)
        socket.on('global_dark_mode_update', (data) => {
            window.colorMode?.applyFromServer?.(data);
        });

        // Victron / power tile
        socket.on('victron_update', (data) => {
            window.powerTile?.onVictronUpdate?.(data);
            window.victronSystemTile?.onVictronUpdate?.(data);
        });

        // Network
        socket.on('network_update', (data) => {
            window.SCCS.network?.update?.(data);
        });

        // Theme
        socket.on('global_theme_update', (data) => {
            window.themeManager?.applyFromServer?.(data);
        });

        // Sonos
        socket.on('sonos_update', (data) => {
            window.sonosTile?.onSocketUpdate?.(data);
            window.sonosSystemTile?.onSocketUpdate?.(data);
        });

        socket.on('sonos_speakers', (data) => {
            window.sonosSystemTile?.onSpeakersUpdate?.(data);
        });

    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initSocket);
    } else {
        initSocket();
    }
})();