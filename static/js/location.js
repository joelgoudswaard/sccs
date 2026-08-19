/**
 * SCCS home location — suburb, coordinates, satellites (Adafruit Ultimate GPS).
 */
(function () {
    'use strict';

    const POLL_INTERVAL_MS = 5000;

    const home = {
        label: '—',
        latitude: null,
        longitude: null,
        altitude_m: null,
        timezone: null,
    };

    const els = {
        location: document.getElementById('gps-location'),
        coords: document.getElementById('gps-coords'),
        altitude: document.getElementById('gps-altitude'),
        satellites: document.getElementById('gps-satellites'),
    };

    window.sccsLocation = home;

    function formatCoords(lat, lng) {
        if (
            lat === null ||
            lat === undefined ||
            lng === null ||
            lng === undefined ||
            Number.isNaN(Number(lat)) ||
            Number.isNaN(Number(lng))
        ) {
            return '—';
        }
        const latNum = Number(lat);
        const lngNum = Number(lng);
        const latAbs = Math.abs(latNum).toFixed(3);
        const lngAbs = Math.abs(lngNum).toFixed(3);
        const latHem = latNum >= 0 ? 'N' : 'S';
        const lngHem = lngNum >= 0 ? 'E' : 'W';
        return `${latAbs}°${latHem}  ${lngAbs}°${lngHem}`;
    }

    function formatSatellites(count, fixQuality) {
        const sats = count ?? 0;
        const fix = fixQuality ?? '—';
        return `${sats} / ${fix}`;
    }

    function setCoords(lat, lng) {
        if (!els.coords) return;
        els.coords.textContent = formatCoords(lat, lng);
    }

    function formatAltitude(meters) {
        if (meters === null || meters === undefined || Number.isNaN(Number(meters))) {
            return '—';
        }
        return `${Math.round(Number(meters))} m`;
    }

    function setAltitude(meters) {
        if (!els.altitude) return;
        els.altitude.textContent = formatAltitude(meters);
    }

    function update(data) {
        if (!data) return;

        if (data.suburb && els.location) {
            els.location.textContent = data.suburb.trim() || '—';
            home.label = data.suburb.trim();
        } else if (data.label && els.location) {
            els.location.textContent = data.label;
            home.label = data.label;
        }

        const lat = data.latitude ?? data.lat;
        const lng = data.longitude ?? data.lng ?? data.lon;
        if (lat !== undefined && lng !== undefined) {
            if (typeof lat === 'number') home.latitude = lat;
            if (typeof lng === 'number') home.longitude = lng;
            setCoords(lat, lng);
        }

        const altitude = data.altitude_m ?? data.altitude ?? data.alt;
        if (altitude !== undefined) {
            if (typeof altitude === 'number') home.altitude_m = altitude;
            setAltitude(altitude);
        }

        if (data.satellites !== undefined && els.satellites) {
            els.satellites.textContent = formatSatellites(
                data.satellites,
                data.fix_quality ?? data.fixQuality
            );
        }

        if (data.timezone) home.timezone = data.timezone;
    }

    function propagateGpsSideEffects(data) {
        if (!data) return;

        const lat = data.latitude ?? data.lat;
        const lng = data.longitude ?? data.lng ?? data.lon;
        // Only push real fixes — null/0,0 would yank the sun arc to null island.
        if (
            typeof lat === 'number' &&
            typeof lng === 'number' &&
            Number.isFinite(lat) &&
            Number.isFinite(lng) &&
            Math.abs(lat) <= 90 &&
            Math.abs(lng) <= 180 &&
            !(lat === 0 && lng === 0)
        ) {
            window.datetimeTile?.setLocation?.(lat, lng);
            window.climateTile?.setLocation?.(lat, lng);
        }

        const sunPayload = {};
        if (data.sunrise) sunPayload.sunrise = data.sunrise;
        if (data.sunset) sunPayload.sunset = data.sunset;
        if (data.local_time) sunPayload.local_time = data.local_time;
        if (Object.keys(sunPayload).length) {
            window.datetimeTile?.update?.(sunPayload);
        }
    }

    async function fetchGps() {
        try {
            const res = await fetch('/api/gps', { cache: 'no-store' });
            if (!res.ok) return;
            const data = await res.json();
            update(data);
            propagateGpsSideEffects(data);
        } catch {
            /* keep placeholders until live GPS arrives */
        }
    }

    function onGpsUpdate(data) {
        update(data);
        propagateGpsSideEffects(data);
    }

    if (els.location) els.location.textContent = '—';
    setCoords(null, null);
    setAltitude(null);
    if (els.satellites) els.satellites.textContent = '— / —';
    fetchGps();
    setInterval(fetchGps, POLL_INTERVAL_MS);

    window.SCCS = window.SCCS || {};
    window.SCCS.location = { update, onGpsUpdate, refresh: fetchGps };

    window.locationTile = window.SCCS.location;
})();