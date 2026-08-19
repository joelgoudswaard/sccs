/**
 * Sunrise / sunset calculation (SunCalc algorithm).
 * https://github.com/mourner/suncalc — BSD licence
 */
(function () {
    'use strict';

    const PI = Math.PI;
    const sin = Math.sin;
    const cos = Math.cos;
    const asin = Math.asin;
    const atan = Math.atan2;
    const rad = PI / 180;
    const dayMs = 86400000;
    const J1970 = 2440588;
    const J2000 = 2451545;
    const J0 = 0.0009;
    const e = rad * 23.4397;

    function toJulian(date) {
        return date.valueOf() / dayMs - 0.5 + J1970;
    }

    function fromJulian(j) {
        return new Date((j + 0.5 - J1970) * dayMs);
    }

    function toDays(date) {
        return toJulian(date) - J2000;
    }

    function rightAscension(l, b) {
        return atan(sin(l) * cos(e) - Math.tan(b) * sin(e), cos(l));
    }

    function declination(l, b) {
        return asin(sin(b) * cos(e) + cos(b) * sin(e) * sin(l));
    }

    function solarMeanAnomaly(d) {
        return rad * (357.5291 + 0.98560028 * d);
    }

    function eclipticLongitude(m) {
        const c = rad * (1.9148 * sin(m) + 0.02 * sin(2 * m) + 0.0003 * sin(3 * m));
        const p = rad * 102.9372;
        return m + c + p + PI;
    }

    function julianCycle(d, lw) {
        return Math.round(d - J0 - lw / (2 * PI));
    }

    function approxTransit(ht, lw, n) {
        return J0 + (ht + lw) / (2 * PI) + n;
    }

    function solarTransitJ(ds, m, l) {
        return J2000 + ds + 0.0053 * sin(m) - 0.0069 * sin(2 * l);
    }

    function hourAngle(h, phi, d) {
        return Math.acos((sin(h) - sin(phi) * sin(d)) / (cos(phi) * cos(d)));
    }

    function getSetJ(h, lw, phi, dec, n, m, l) {
        const w = hourAngle(h, phi, dec);
        const a = approxTransit(w, lw, n);
        return solarTransitJ(a, m, l);
    }

    function getTimes(date, lat, lng) {
        const lw = rad * -lng;
        const phi = rad * lat;
        const d = toDays(date);
        const n = julianCycle(d, lw);
        const ds = approxTransit(0, lw, n);
        const m = solarMeanAnomaly(ds);
        const l = eclipticLongitude(m);
        const dec = declination(l, 0);
        const jnoon = solarTransitJ(ds, m, l);
        const h0 = -0.833 * rad;
        const jset = getSetJ(h0, lw, phi, dec, n, m, l);
        const jrise = jnoon - (jset - jnoon);

        return {
            sunrise: fromJulian(jrise),
            sunset: fromJulian(jset),
            solarNoon: fromJulian(jnoon),
        };
    }

    window.SunMath = { getTimes };
})();