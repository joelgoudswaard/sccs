/**
 * SCCS Climate tile — outside/fridge temps, 24h scrolling forecast, hints.
 */
(function () {
    'use strict';

    const API_WEATHER_INTERVAL_MS = 300000;
    const SENSOR_POLL_INTERVAL_MS = 5000;

    const els = {
        outside: document.getElementById('temp-outside'),
        fridge: document.getElementById('temp-fridge'),
        freezer: document.getElementById('temp-freezer'),
        coldStrip: document.getElementById('climate-cold-strip'),
        fridgeItem: document.getElementById('climate-fridge-item'),
        freezerItem: document.getElementById('climate-freezer-item'),
        fridgeStatus: document.getElementById('fridge-status'),
        humidity: document.getElementById('temp-humidity'),
        weatherIcon: document.getElementById('climate-weather-icon'),
        summary: document.getElementById('weather-summary'),
        dailyRange: document.getElementById('weather-daily-range'),
        dailyRangeWrap: document.getElementById('weather-daily-range-wrap'),
        feels: document.getElementById('weather-feels'),
        lowTonight: document.getElementById('weather-low-tonight'),
        forecastArc: document.getElementById('weather-forecast-arc'),
        forecastPath: document.getElementById('weather-forecast-path'),
        forecastTooltip: document.getElementById('weather-forecast-tooltip'),
        forecastGrid: document.getElementById('weather-forecast-grid'),
        forecastNowLine: document.getElementById('weather-forecast-now-line'),
        forecastMarker: document.getElementById('weather-current-marker'),
        forecastAxis: document.getElementById('weather-forecast-axis'),
        forecastExtrema: document.getElementById('weather-forecast-extrema'),
        outlook: document.getElementById('weather-outlook'),
        hint: document.getElementById('weather-hint'),
    };

    if (!els.outside) return;

    const home = window.sccsLocation;
    let lat = home?.latitude ?? -37.191;
    let lng = home?.longitude ?? 145.711;
    let sensorOutside = null;
    let sensorFridge = null;
    let sensorFreezer = null;
    let fridgeConfigured = false;
    let freezerConfigured = false;
    let forecastOutside = null;
    let lastWeather = {};
    let layoutParams = null;
    let curveReady = false;
    let lastHourlyForecast = [];
    let lastForecastPoints = [];
    let lastForecastSegments = [];
    let lastCurrentTemp = null;
    let geometryAttempts = 0;
    const FORECAST_SVG_HEIGHT = 40;
    const FORECAST_AXIS_RESERVE = 14;
    const FORECAST_CURVE_TOP_PAD = 0;
    const FORECAST_CURVE_BOTTOM_PAD = 4;
    const FORECAST_NOW_LINE_BOTTOM_PAD = 8;
    const MARKER_RADIUS = 5;
    const CURVE_STROKE_PAD = 2;
    const FORECAST_CURVE_INSET = 0;
    const MIN_DISPLAY_TEMP_SPAN = 2;
    const NOW_POSITION_RATIO = 0.34;
    const VISIBLE_HOURS = 20;
    const SCROLL_TICK_MS = 60000;
    const FORECAST_HOVER_THRESHOLD_PX = 14;
    const FORECAST_GRID_LINES = 5;
    const HOUR_MS = 3600000;
    const HALF_HOUR_MS = HOUR_MS / 2;
    const TOOLTIP_NOW_THRESHOLD_MS = 15 * 60 * 1000;
    const AXIS_TICK_OFFSETS = [-6, 0, 6, 12];
    const EXTREMA_COLLISION_PAD = 28;
    const EXTREMA_SLOPE_OFFSET_Y = 8;
    const EXTREMA_MIN_SLOPE = 0.15;
    const TOOLTIP_EDGE_PAD = 6;
    const TOOLTIP_CURSOR_GAP = 8;
    const OUTLOOK_DAYS = 4;
    const OUTLOOK_START_INDEX = 1;


    function formatTemp(value, suffix = '°C') {
        if (value === null || value === undefined || Number.isNaN(Number(value))) {
            return `—${suffix}`;
        }
        return `${Math.round(Number(value))}${suffix}`;
    }

    function formatShortTemp(value) {
        if (value === null || value === undefined || Number.isNaN(Number(value))) {
            return '—°';
        }
        return `${Math.round(Number(value))}°`;
    }

    function formatDailyRange(min, max) {
        const hasMin = min !== null && min !== undefined && !Number.isNaN(Number(min));
        const hasMax = max !== null && max !== undefined && !Number.isNaN(Number(max));
        if (!hasMin && !hasMax) return null;
        if (hasMin && hasMax) {
            return `${formatShortTemp(min)} – ${formatShortTemp(max)}`;
        }
        return formatShortTemp(hasMin ? min : max);
    }

    function applyDailyRange(min, max) {
        if (!els.dailyRange || !els.dailyRangeWrap) return;
        const text = formatDailyRange(min, max);
        if (!text) {
            els.dailyRangeWrap.hidden = true;
            return;
        }
        els.dailyRange.textContent = text;
        els.dailyRangeWrap.hidden = false;
    }

    function storedFeelsLike() {
        return lastWeather.feels_like_c
            ?? lastWeather.apparent_temperature
            ?? lastWeather.feels_like;
    }

    function normalizeDailyForecast(data) {
        const raw = data?.daily_forecast;
        if (!Array.isArray(raw)) return [];
        return raw
            .map((day) => ({
                date: day?.date ?? day?.time ?? null,
                weather_code: day?.weather_code ?? day?.code ?? null,
                temp_min: day?.temp_min ?? day?.temperature_min ?? day?.min ?? null,
                temp_max: day?.temp_max ?? day?.temperature_max ?? day?.max ?? null,
            }))
            .filter((day) => (
                day.date
                && day.temp_min != null
                && day.temp_max != null
                && !Number.isNaN(Number(day.temp_min))
                && !Number.isNaN(Number(day.temp_max))
            ));
    }

    function outlookDayLabel(dateValue, index) {
        if (index === 0) return 'Tomorrow';
        const parsed = new Date(dateValue);
        if (Number.isNaN(parsed.getTime())) return '—';
        return parsed.toLocaleDateString(undefined, { weekday: 'short' });
    }

    function renderOutlook(daily) {
        if (!els.outlook) return;

        const days = daily.slice(OUTLOOK_START_INDEX, OUTLOOK_START_INDEX + OUTLOOK_DAYS);
        els.outlook.replaceChildren();

        if (!days.length) {
            els.outlook.hidden = true;
            return;
        }

        days.forEach((day, index) => {
            const tile = document.createElement('article');
            tile.className = 'climate-tile__outlook-day';

            const label = document.createElement('span');
            label.className = 'climate-tile__outlook-label';
            label.textContent = outlookDayLabel(day.date, index);

            const icon = document.createElement('i');
            icon.className = `fa-solid ${getWeatherIcon(day.weather_code, true)} climate-tile__outlook-icon`;
            icon.setAttribute('aria-hidden', 'true');

            const temps = document.createElement('span');
            temps.className = 'climate-tile__outlook-temps';

            const max = document.createElement('span');
            max.className = 'climate-tile__outlook-max';
            max.textContent = formatShortTemp(day.temp_max);

            const min = document.createElement('span');
            min.className = 'climate-tile__outlook-min';
            min.textContent = formatShortTemp(day.temp_min);

            temps.append(max, min);
            tile.append(label, icon, temps);
            tile.setAttribute(
                'aria-label',
                `${label.textContent}: high ${max.textContent}, low ${min.textContent}`
            );
            els.outlook.append(tile);
        });

        els.outlook.hidden = false;
    }

    function applyOutlook(data) {
        const daily = normalizeDailyForecast(data);
        if (daily.length) {
            lastWeather.daily_forecast = daily;
        }
        renderOutlook(normalizeDailyForecast(lastWeather));
    }

    function applyFeelsLike() {
        if (!els.feels) return;

        const feels = storedFeelsLike();
        const hasFeels = feels !== null && feels !== undefined && !Number.isNaN(Number(feels));
        els.feels.textContent = hasFeels ? `Feels ${formatTemp(feels)}` : 'Feels —°C';
    }

    function formatHumidity(value) {
        if (value === null || value === undefined || Number.isNaN(Number(value))) {
            return '—%';
        }
        return `${Math.round(Number(value))}%`;
    }

    function getWeatherIcon(code, isDay) {
        const icons = {
            0: isDay ? 'fa-sun' : 'fa-moon',
            1: isDay ? 'fa-cloud-sun' : 'fa-cloud-moon',
            2: 'fa-cloud',
            3: 'fa-cloud',
            45: 'fa-smog',
            48: 'fa-smog',
            51: 'fa-cloud-rain',
            53: 'fa-cloud-rain',
            55: 'fa-cloud-rain',
            61: 'fa-cloud-showers-heavy',
            63: 'fa-cloud-showers-heavy',
            65: 'fa-cloud-showers-heavy',
            71: 'fa-snowflake',
            73: 'fa-snowflake',
            75: 'fa-snowflake',
            80: 'fa-cloud-showers-heavy',
            81: 'fa-cloud-showers-heavy',
            82: 'fa-cloud-showers-heavy',
            95: 'fa-bolt',
            96: 'fa-bolt',
            99: 'fa-bolt',
        };
        return icons[Number(code)] || 'fa-cloud';
    }

    function applyWeatherIcon(code, isDay) {
        if (!els.weatherIcon || code === null || code === undefined) return;
        const icon = getWeatherIcon(code, Boolean(isDay));
        els.weatherIcon.className = `fa-solid ${icon} tile__icon`;
    }

    function currentOutsideTemp() {
        if (sensorOutside != null) return Number(sensorOutside);
        if (forecastOutside != null) return Number(forecastOutside);
        return null;
    }

    function applyForecastPath(pathD, layout) {
        if (!els.forecastPath || !layout) return;
        els.forecastPath.setAttribute('d', pathD);
        const svg = els.forecastArc?.querySelector('.climate-tile__forecast-svg');
        svg?.setAttribute('viewBox', `0 0 ${layout.viewBoxW} ${layout.h}`);
    }

    function clientPointToSvg(clientX, clientY, layout) {
        const svgEl = els.forecastArc?.querySelector('.climate-tile__forecast-svg');
        if (!svgEl || !layout) return null;
        const svgRect = svgEl.getBoundingClientRect();
        if (!svgRect.width || !svgRect.height) return null;
        return {
            x: (clientX - svgRect.left) * (layout.viewBoxW / svgRect.width),
            y: (clientY - svgRect.top) * (layout.h / svgRect.height),
        };
    }

    function clientXToSvgX(clientX, layout) {
        const point = clientPointToSvg(clientX, 0, layout);
        return point?.x ?? null;
    }

    function tempAtSvgX(svgX, points) {
        if (!points.length || svgX == null || !Number.isFinite(svgX)) return null;

        if (svgX <= points[0].x) return points[0].temp;
        const last = points[points.length - 1];
        if (svgX >= last.x) return last.temp;

        for (let i = 0; i < points.length - 1; i += 1) {
            const p0 = points[i];
            const p1 = points[i + 1];
            if (svgX < p0.x || svgX > p1.x) continue;
            const span = p1.x - p0.x;
            if (!span) return p0.temp;
            const ratio = (svgX - p0.x) / span;
            return p0.temp + ratio * (p1.temp - p0.temp);
        }

        return null;
    }

    function timeAtSvgX(svgX, layout, nowMs) {
        if (svgX == null || !Number.isFinite(svgX) || !layout) return null;
        const markerX = nowMarkerX(layout);
        const pph = pixelsPerHour(layout);
        if (!pph) return null;
        return nowMs + ((svgX - markerX) / pph) * HOUR_MS;
    }

    function formatTooltipTime(timeMs, nowMs) {
        if (timeMs == null || !Number.isFinite(timeMs)) return null;

        const rounded = Math.round(timeMs / HALF_HOUR_MS) * HALF_HOUR_MS;
        if (Math.abs(rounded - nowMs) < TOOLTIP_NOW_THRESHOLD_MS) {
            return 'Now';
        }

        const date = new Date(rounded);
        const hours24 = date.getHours();
        const minutes = date.getMinutes();
        const ampm = hours24 >= 12 ? 'PM' : 'AM';
        const hours = hours24 % 12 || 12;

        if (minutes === 0) {
            return `${hours} ${ampm}`;
        }

        return `${hours}:${String(minutes).padStart(2, '0')} ${ampm}`;
    }

    function formatForecastTooltip(timeMs, temp, nowMs) {
        const timeLabel = formatTooltipTime(timeMs, nowMs);
        const tempLabel = formatShortTemp(temp);
        return timeLabel ? `${timeLabel} · ${tempLabel}` : tempLabel;
    }

    function hideForecastTooltip() {
        if (!els.forecastTooltip) return;
        els.forecastTooltip.hidden = true;
        els.forecastTooltip.textContent = '';
        els.forecastTooltip.style.transform = '';
    }

    function clampForecastTooltip(anchorX, anchorY, arcWidth, arcHeight) {
        if (!els.forecastTooltip) return;

        els.forecastTooltip.style.left = '0';
        els.forecastTooltip.style.top = '0';
        els.forecastTooltip.style.transform = 'none';

        const tipWidth = els.forecastTooltip.offsetWidth;
        const tipHeight = els.forecastTooltip.offsetHeight;

        let left = anchorX - tipWidth / 2;
        let top = anchorY - tipHeight - TOOLTIP_CURSOR_GAP;

        if (top < TOOLTIP_EDGE_PAD) {
            top = anchorY + TOOLTIP_CURSOR_GAP;
        }

        if (top + tipHeight > arcHeight - TOOLTIP_EDGE_PAD) {
            top = Math.max(
                TOOLTIP_EDGE_PAD,
                anchorY - tipHeight - TOOLTIP_CURSOR_GAP
            );
        }

        left = Math.max(
            TOOLTIP_EDGE_PAD,
            Math.min(left, arcWidth - tipWidth - TOOLTIP_EDGE_PAD)
        );
        top = Math.max(
            TOOLTIP_EDGE_PAD,
            Math.min(top, arcHeight - tipHeight - TOOLTIP_EDGE_PAD)
        );

        els.forecastTooltip.style.left = `${left}px`;
        els.forecastTooltip.style.top = `${top}px`;
    }

    function showForecastTooltip(event, temp, svgX) {
        if (!els.forecastTooltip || !els.forecastArc) return;
        const arcRect = els.forecastArc.getBoundingClientRect();
        const anchorX = event.clientX - arcRect.left;
        const anchorY = event.clientY - arcRect.top;
        const nowMs = Date.now();
        const timeMs = timeAtSvgX(svgX, layoutParams, nowMs);

        els.forecastTooltip.textContent = formatForecastTooltip(timeMs, temp, nowMs);
        els.forecastTooltip.hidden = false;
        clampForecastTooltip(anchorX, anchorY, arcRect.width, arcRect.height);
    }

    function isNearForecastPath(clientX, clientY) {
        if (!layoutParams || lastForecastPoints.length < 2) return false;

        const point = clientPointToSvg(clientX, clientY, layoutParams);
        if (!point) return false;

        const lineY = markerY(lastForecastPoints, lastForecastSegments, point.x);
        if (lineY == null || !Number.isFinite(lineY)) return false;

        const svgEl = els.forecastArc?.querySelector('.climate-tile__forecast-svg');
        const svgRect = svgEl?.getBoundingClientRect();
        if (!svgRect?.height) return false;

        const thresholdSvg = FORECAST_HOVER_THRESHOLD_PX * (layoutParams.h / svgRect.height);
        return Math.abs(point.y - lineY) <= thresholdSvg;
    }

    function setForecastPathHover(active) {
        if (!els.forecastArc) return;
        els.forecastArc.classList.toggle('is-path-hover', Boolean(active));
    }

    function bindForecastPathHover() {
        if (!els.forecastArc || els.forecastArc.dataset.hoverBound) return;
        els.forecastArc.dataset.hoverBound = 'true';

        els.forecastArc.addEventListener('mousemove', (event) => {
            if (!isNearForecastPath(event.clientX, event.clientY)) {
                setForecastPathHover(false);
                hideForecastTooltip();
                return;
            }

            setForecastPathHover(true);

            const svgX = clientXToSvgX(event.clientX, layoutParams);
            const temp = tempAtSvgX(svgX, lastForecastPoints);
            if (temp == null) {
                setForecastPathHover(false);
                hideForecastTooltip();
                return;
            }

            showForecastTooltip(event, temp, svgX);
        });

        els.forecastArc.addEventListener('mouseleave', () => {
            setForecastPathHover(false);
            hideForecastTooltip();
        });
    }

    function svgPointToContainer(pt, layout) {
        if (!els.forecastArc || !layout) return { x: 0, y: 0 };
        const arcRect = els.forecastArc.getBoundingClientRect();
        const svgEl = els.forecastArc.querySelector('.climate-tile__forecast-svg');
        if (!svgEl || !arcRect.width) return { x: 0, y: 0 };
        const svgRect = svgEl.getBoundingClientRect();
        const scaleX = svgRect.width / layout.viewBoxW;
        const scaleY = svgRect.height / layout.h;
        return {
            x: (svgRect.left - arcRect.left) + pt.x * scaleX,
            y: (svgRect.top - arcRect.top) + pt.y * scaleY,
        };
    }

    function parseForecastTime(value) {
        if (!value) return null;
        const parsed = new Date(value);
        return Number.isNaN(parsed.getTime()) ? null : parsed.getTime();
    }

    function nowMarkerX(layout) {
        const usable = layout.viewBoxW - layout.inset * 2;
        return layout.inset + usable * NOW_POSITION_RATIO;
    }

    function pixelsPerHour(layout) {
        return (layout.viewBoxW - layout.inset * 2) / VISIBLE_HOURS;
    }

    function displayTempRange(globalMin, globalMax) {
        const span = Math.max(Number(globalMax) - Number(globalMin), MIN_DISPLAY_TEMP_SPAN);
        return {
            min: Number(globalMin),
            max: Number(globalMin) + span,
        };
    }

    function curveVerticalBand(layout) {
        return {
            top: layout.yTop,
            bottom: layout.yBottom - layout.yPad - CURVE_STROKE_PAD,
        };
    }

    function tempToY(temp, globalMin, globalMax, layout) {
        const { min, max } = displayTempRange(globalMin, globalMax);
        const span = max - min;
        const band = curveVerticalBand(layout);
        const usable = band.bottom - band.top;
        if (!Number.isFinite(Number(temp))) {
            return band.top + usable / 2;
        }
        const norm = (Number(temp) - min) / span;
        return band.top + (1 - norm) * usable;
    }

    function verticalFitTransform(globalMin, globalMax, layout, referencePoints) {
        const band = curveVerticalBand(layout);
        const bandHeight = band.bottom - band.top;
        const refYs = referencePoints.map((point) =>
            tempToY(point.temp, globalMin, globalMax, layout)
        );
        const minY = Math.min(...refYs);
        const maxY = Math.max(...refYs);
        const span = maxY - minY;

        return (rawY) => {
            if (span < 0.5) return band.top + bandHeight / 2;
            return band.top + ((rawY - minY) / span) * bandHeight;
        };
    }

    function displayTempToY(temp, globalMin, globalMax, layout, referencePoints) {
        const toDisplayY = verticalFitTransform(globalMin, globalMax, layout, referencePoints);
        return clampCurveY(
            toDisplayY(tempToY(temp, globalMin, globalMax, layout)),
            layout
        );
    }

    function fitPointsToVerticalBand(points, layout) {
        if (!points.length) return points;

        const band = curveVerticalBand(layout);
        const bandHeight = band.bottom - band.top;
        const ys = points.map((point) => point.y);
        const minY = Math.min(...ys);
        const maxY = Math.max(...ys);
        const span = maxY - minY;

        if (span < 0.5) {
            const centerY = band.top + bandHeight / 2;
            return points.map((point) => ({ ...point, y: centerY }));
        }

        return points.map((point) => ({
            ...point,
            y: band.top + ((point.y - minY) / span) * bandHeight,
        }));
    }

    function clampCurveY(y, layout) {
        const band = curveVerticalBand(layout);
        return Math.min(band.bottom, Math.max(band.top, y));
    }

    function normalizeHourlyForecast(data) {
        const raw = data?.hourly_forecast;
        if (!Array.isArray(raw)) return [];
        return raw
            .map((hour) => ({
                time: hour?.time ?? null,
                temperature_c: hour?.temperature_c ?? hour?.temperature ?? hour?.temp ?? null,
            }))
            .filter((hour) => hour.time && hour.temperature_c != null && !Number.isNaN(Number(hour.temperature_c)));
    }

    function buildScrollPoints(hourly, nowMs, layout) {
        const markerX = nowMarkerX(layout);
        const pph = pixelsPerHour(layout);
        const points = [];

        hourly.forEach((hour) => {
            const timeMs = parseForecastTime(hour.time);
            if (timeMs == null) return;
            const hoursFromNow = (timeMs - nowMs) / HOUR_MS;
            points.push({
                x: markerX + hoursFromNow * pph,
                temp: Number(hour.temperature_c),
                timeMs,
            });
        });

        points.sort((a, b) => a.x - b.x);

        const temps = points.map((point) => point.temp);
        const globalMin = temps.length ? Math.min(...temps) : 0;
        const globalMax = temps.length ? Math.max(...temps) : 0;

        const rawPoints = points.map((point) => ({
            x: point.x,
            y: tempToY(point.temp, globalMin, globalMax, layout),
            temp: point.temp,
            timeMs: point.timeMs,
        }));

        return {
            points: fitPointsToVerticalBand(rawPoints, layout).map((point) => ({
                ...point,
                y: clampCurveY(point.y, layout),
            })),
            globalMin,
            globalMax,
            markerX,
        };
    }

    function buildHourlyPath(points) {
        if (points.length < 2) {
            if (points.length === 1) {
                const p = points[0];
                return { pathD: `M ${p.x.toFixed(1)} ${p.y.toFixed(1)}`, segments: [] };
            }
            return { pathD: '', segments: [] };
        }

        let pathD = `M ${points[0].x.toFixed(1)} ${points[0].y.toFixed(1)}`;
        const segments = [];

        for (let i = 0; i < points.length - 1; i += 1) {
            const p0 = points[i];
            const p1 = points[i + 1];
            const cpx = (p0.x + p1.x) / 2;
            const cpy = (p0.y + p1.y) / 2;
            pathD += ` Q ${cpx.toFixed(1)} ${cpy.toFixed(1)} ${p1.x.toFixed(1)} ${p1.y.toFixed(1)}`;
            segments.push({
                x0: p0.x,
                y0: p0.y,
                x1: cpx,
                y1: cpy,
                x2: p1.x,
                y2: p1.y,
            });
        }

        return { pathD, segments };
    }

    function quadraticBezier(t, v0, v1, v2) {
        const mt = 1 - t;
        return mt * mt * v0 + 2 * mt * t * v1 + t * t * v2;
    }

    function yOnQuadraticSegmentAtX(seg, targetX) {
        const minX = Math.min(seg.x0, seg.x2) - 0.5;
        const maxX = Math.max(seg.x0, seg.x2) + 0.5;
        if (targetX < minX || targetX > maxX) return null;

        let lo = 0;
        let hi = 1;
        for (let i = 0; i < 24; i += 1) {
            const mid = (lo + hi) / 2;
            const x = quadraticBezier(mid, seg.x0, seg.x1, seg.x2);
            if (x < targetX) lo = mid;
            else hi = mid;
        }

        const t = (lo + hi) / 2;
        return quadraticBezier(t, seg.y0, seg.y1, seg.y2);
    }

    function yOnPathAtX(segments, targetX) {
        for (const seg of segments) {
            const y = yOnQuadraticSegmentAtX(seg, targetX);
            if (y != null && Number.isFinite(y)) return y;
        }
        return null;
    }

    function yAtXFromPoints(points, targetX) {
        if (!points.length) return null;

        for (let i = 0; i < points.length - 1; i += 1) {
            const p0 = points[i];
            const p1 = points[i + 1];
            if (targetX < p0.x || targetX > p1.x) continue;
            const dx = p1.x - p0.x;
            if (!dx) return p0.y;
            const t = (targetX - p0.x) / dx;
            return p0.y + t * (p1.y - p0.y);
        }

        if (targetX <= points[0].x) return points[0].y;
        return points[points.length - 1].y;
    }

    function markerY(points, segments, markerX) {
        const onPath = yOnPathAtX(segments, markerX);
        if (onPath != null && Number.isFinite(onPath)) return onPath;
        const interpolated = yAtXFromPoints(points, markerX);
        return interpolated != null && Number.isFinite(interpolated) ? interpolated : null;
    }

    function formatAxisTime(nowMs, hourOffset) {
        if (hourOffset === 0) return 'Now';
        const date = new Date(nowMs + hourOffset * HOUR_MS);
        const hours24 = date.getHours();
        const ampm = hours24 >= 12 ? 'PM' : 'AM';
        const hours = hours24 % 12 || 12;
        return `${hours} ${ampm}`;
    }

    function svgPointToExtrema(pt, layout) {
        if (!els.forecastExtrema || !layout) return { x: 0, y: 0 };
        const extremaRect = els.forecastExtrema.getBoundingClientRect();
        const svgEl = els.forecastArc?.querySelector('.climate-tile__forecast-svg');
        if (!svgEl || !extremaRect.width) return { x: 0, y: 0 };
        const svgRect = svgEl.getBoundingClientRect();
        const scaleX = svgRect.width / layout.viewBoxW;
        const scaleY = svgRect.height / layout.h;
        return {
            x: (svgRect.left - extremaRect.left) + pt.x * scaleX,
            y: (svgRect.top - extremaRect.top) + pt.y * scaleY,
        };
    }

    function resolveExtremaPoint(points, temp, layout) {
        const candidates = points.filter((point) => point.temp === temp);
        if (!candidates.length) return null;

        const viewMax = layout.viewBoxW;
        const inView = candidates.filter((point) => point.x >= 0 && point.x <= viewMax);
        if (!inView.length) return null;

        const markerX = nowMarkerX(layout);
        return inView.reduce((best, point) => (
            Math.abs(point.x - markerX) < Math.abs(best.x - markerX) ? point : best
        ));
    }

    function findExtremaPoints(points, layout) {
        if (!points.length) return { high: null, low: null };

        const maxTemp = Math.max(...points.map((point) => point.temp));
        const minTemp = Math.min(...points.map((point) => point.temp));
        const high = resolveExtremaPoint(points, maxTemp, layout);
        const low = maxTemp === minTemp
            ? null
            : resolveExtremaPoint(points, minTemp, layout);

        return { high, low };
    }

    function findPointIndex(points, target) {
        return points.findIndex((point) => (
            point.x === target.x && point.y === target.y && point.temp === target.temp
        ));
    }

    function adjacentSlopes(points, index) {
        const slopes = { before: 0, after: 0 };
        if (index > 0) {
            const dx = points[index].x - points[index - 1].x;
            if (dx) slopes.before = (points[index].y - points[index - 1].y) / dx;
        }
        if (index < points.length - 1) {
            const dx = points[index + 1].x - points[index].x;
            if (dx) slopes.after = (points[index + 1].y - points[index].y) / dx;
        }
        return slopes;
    }

    function extremaVerticalOffsetY(kind, slopes) {
        const steepness = Math.max(Math.abs(slopes.before), Math.abs(slopes.after));
        if (steepness < EXTREMA_MIN_SLOPE) return 0;

        const amount = Math.min(
            EXTREMA_SLOPE_OFFSET_Y,
            Math.max(4, steepness * 14)
        );

        if (kind === 'high') {
            if (slopes.after >= EXTREMA_MIN_SLOPE) return amount;
            if (slopes.before <= -EXTREMA_MIN_SLOPE) return -amount;
        } else if (slopes.after <= -EXTREMA_MIN_SLOPE) {
            return -amount;
        } else if (slopes.before >= EXTREMA_MIN_SLOPE) {
            return amount;
        }

        return 0;
    }

    function renderForecastExtrema(points, layout) {
        if (!els.forecastExtrema) return;
        els.forecastExtrema.replaceChildren();

        const { high, low } = findExtremaPoints(points, layout);
        if (!high && !low) return;

        const labels = [];

        const addLabel = (point, kind) => {
            if (!point) return;
            const pos = svgPointToExtrema({ x: point.x, y: point.y }, layout);
            const index = findPointIndex(points, point);
            if (index >= 0) {
                pos.y += extremaVerticalOffsetY(kind, adjacentSlopes(points, index));
            }
            labels.push({ point, kind, pos });
        };

        addLabel(high, 'high');
        addLabel(low, 'low');

        if (labels.length === 2) {
            const dx = Math.abs(labels[0].pos.x - labels[1].pos.x);
            const dy = Math.abs(labels[0].pos.y - labels[1].pos.y);
            if (dx < EXTREMA_COLLISION_PAD && dy < 18) {
                labels[0].pos.x -= EXTREMA_COLLISION_PAD / 2;
                labels[1].pos.x += EXTREMA_COLLISION_PAD / 2;
            }
        }

        labels.forEach(({ point, kind, pos }) => {
            const label = document.createElement('span');
            label.className = `climate-tile__forecast-extrema-label climate-tile__forecast-extrema-label--${kind}`;
            label.textContent = formatShortTemp(point.temp);
            label.style.left = `${pos.x}px`;
            label.style.top = `${pos.y}px`;
            els.forecastExtrema.append(label);
        });
    }

    function renderForecastGrid(layout) {
        if (!els.forecastGrid) return;
        els.forecastGrid.replaceChildren();
        if (!layout) return;

        const band = curveVerticalBand(layout);
        const x1 = layout.inset;
        const x2 = layout.viewBoxW - layout.inset;
        const svgNs = 'http://www.w3.org/2000/svg';
        const segments = Math.max(FORECAST_GRID_LINES - 1, 1);
        const bandHeight = band.bottom - band.top;

        for (let i = 0; i < FORECAST_GRID_LINES; i += 1) {
            const y = band.top + (i / segments) * bandHeight;
            const line = document.createElementNS(svgNs, 'line');
            line.setAttribute('x1', String(x1));
            line.setAttribute('x2', String(x2));
            line.setAttribute('y1', y.toFixed(2));
            line.setAttribute('y2', y.toFixed(2));
            els.forecastGrid.append(line);
        }
    }

    function renderForecastAxis(nowMs, layout) {
        if (!els.forecastAxis) return;
        els.forecastAxis.replaceChildren();

        const markerX = nowMarkerX(layout);
        const pph = pixelsPerHour(layout);
        const arcHeight = els.forecastArc?.clientHeight || 0;

        AXIS_TICK_OFFSETS.forEach((offset) => {
            const tick = document.createElement('span');
            tick.className = 'climate-tile__forecast-tick';
            if (offset === 0) tick.classList.add('climate-tile__forecast-tick--now');
            tick.textContent = formatAxisTime(nowMs, offset);

            const pos = svgPointToContainer(
                { x: markerX + offset * pph, y: layout.h },
                layout
            );
            tick.style.left = `${pos.x}px`;
            tick.style.top = `${arcHeight - 2}px`;
            els.forecastAxis.append(tick);
        });
    }

    function updateNowLine(layout) {
        if (!els.forecastNowLine || !layout) return;
        const x = nowMarkerX(layout);
        els.forecastNowLine.setAttribute('x1', x);
        els.forecastNowLine.setAttribute('x2', x);
        els.forecastNowLine.setAttribute('y1', layout.yTop);
        els.forecastNowLine.setAttribute('y2', layout.yBottom - FORECAST_NOW_LINE_BOTTOM_PAD);
    }

    function updateForecastMarker(points, segments, markerX, layout) {
        if (!els.forecastMarker || !els.forecastArc) return;

        const y = markerY(points, segments, markerX);
        if (y == null) {
            els.forecastArc.classList.remove('has-marker');
            return;
        }

        const band = curveVerticalBand(layout);
        const clampedY = Math.min(band.bottom, Math.max(band.top, y));

        els.forecastMarker.setAttribute('cx', markerX);
        els.forecastMarker.setAttribute('cy', clampedY);
        els.forecastArc.classList.add('has-marker');
    }

    function updateForecastGeometry() {
        if (!els.forecastArc) return;

        const w = Math.round(els.forecastArc.clientWidth || els.forecastArc.getBoundingClientRect().width);
        if (!w || w < 48) {
            if (geometryAttempts < 24) {
                geometryAttempts += 1;
                setTimeout(updateForecastGeometry, 120);
            }
            return;
        }

        geometryAttempts = 0;
        const arcHeight = Math.round(els.forecastArc.clientHeight || 0);
        const drawableHeight = Math.max(
            28,
            arcHeight - FORECAST_AXIS_RESERVE
        );
        const h = Math.max(FORECAST_SVG_HEIGHT, drawableHeight);
        const inset = FORECAST_CURVE_INSET;

        layoutParams = {
            viewBoxW: w,
            h,
            inset,
            yTop: FORECAST_CURVE_TOP_PAD,
            yBottom: h - FORECAST_CURVE_BOTTOM_PAD,
            yPad: 2,
        };

        curveReady = true;
        els.forecastArc.classList.add('is-ready');
        updateForecastCurve(lastHourlyForecast, lastCurrentTemp);
    }

    function updateForecastCurve(hourly, current) {
        if (!els.forecastArc) return;

        lastHourlyForecast = Array.isArray(hourly) ? hourly : [];
        lastCurrentTemp = current;

        if (!curveReady || !layoutParams) return;

        const nowMs = Date.now();
        const { points, globalMin, globalMax, markerX } = buildScrollPoints(
            lastHourlyForecast,
            nowMs,
            layoutParams
        );

        if (points.length < 2) {
            lastForecastPoints = [];
            lastForecastSegments = [];
            hideForecastTooltip();
            els.forecastArc.classList.add('is-empty');
            els.forecastArc.classList.remove('has-marker');
            els.forecastPath?.setAttribute('d', '');
            if (els.forecastGrid) els.forecastGrid.replaceChildren();
            if (els.forecastAxis) els.forecastAxis.replaceChildren();
            if (els.forecastExtrema) els.forecastExtrema.replaceChildren();
            return;
        }

        els.forecastArc.classList.remove('is-empty');
        lastForecastPoints = points;

        const { pathD, segments } = buildHourlyPath(points);
        lastForecastSegments = segments;
        applyForecastPath(pathD, layoutParams);
        renderForecastGrid(layoutParams);
        updateNowLine(layoutParams);
        renderForecastAxis(nowMs, layoutParams);
        renderForecastExtrema(points, layoutParams);
        updateForecastMarker(points, segments, markerX, layoutParams);
    }

    function fridgeState(temp) {
        if (temp === null || temp === undefined || Number.isNaN(Number(temp))) {
            return { label: '—', className: '' };
        }
        const t = Number(temp);
        if (t < 2) return { label: 'Cold', className: 'climate-tile__cold-status--cold' };
        if (t <= 6) return { label: 'OK', className: 'climate-tile__cold-status--ok' };
        if (t <= 10) return { label: 'Warm', className: 'climate-tile__cold-status--warm' };
        return { label: 'Check', className: 'climate-tile__cold-status--alert' };
    }

    function applyColdTemp(el, temp) {
        if (!el) return;
        el.textContent = (temp === null || temp === undefined || Number.isNaN(Number(temp)))
            ? '—°C'
            : formatTemp(temp);
    }

    function applyFridge(temp) {
        if (!els.fridge || !fridgeConfigured) return;

        applyColdTemp(els.fridge, temp);

        const missing = temp === null || temp === undefined || Number.isNaN(Number(temp));
        if (els.fridgeItem) els.fridgeItem.classList.toggle('is-empty', missing);

        if (els.fridgeStatus) {
            if (missing) {
                els.fridgeStatus.textContent = '—';
                els.fridgeStatus.className = 'climate-tile__cold-status';
                return;
            }
            const state = fridgeState(temp);
            els.fridgeStatus.textContent = state.label;
            els.fridgeStatus.className = `climate-tile__cold-status${state.className ? ` ${state.className}` : ''}`;
        }
    }

    function applyFreezer(temp) {
        if (!freezerConfigured) return;
        applyColdTemp(els.freezer, temp);
    }

    function applyColdStripConfig(data) {
        if (!data) return;

        if (Object.prototype.hasOwnProperty.call(data, 'fridge_configured')) {
            fridgeConfigured = !!data.fridge_configured;
            if (els.fridgeItem) els.fridgeItem.hidden = !fridgeConfigured;
        }
        if (Object.prototype.hasOwnProperty.call(data, 'freezer_configured')) {
            freezerConfigured = !!data.freezer_configured;
            if (els.freezerItem) els.freezerItem.hidden = !freezerConfigured;
        }

        if (els.coldStrip && (
            Object.prototype.hasOwnProperty.call(data, 'fridge_configured') ||
            Object.prototype.hasOwnProperty.call(data, 'freezer_configured')
        )) {
            els.coldStrip.hidden = !fridgeConfigured && !freezerConfigured;
        }
    }

    function isCurrentlyDay(data) {
        const raw = data?.is_day ?? data?.isDay
            ?? lastWeather?.is_day ?? lastWeather?.isDay;
        if (raw === true || raw === 1) return true;
        if (raw === false || raw === 0) return false;
        return null;
    }

    function applyHint(data) {
        if (!els.hint) return;

        const rain = data.rain_chance_percent ?? data.precipitation_probability ?? data.rain_chance;
        const cloud = data.cloud_cover_percent ?? data.cloud_cover;
        const wind = data.wind_kmh ?? data.wind_speed;
        const isDay = isCurrentlyDay(data);

        let text = 'Comfortable conditions';
        let className = 'climate-tile__hint';

        if (rain !== null && rain !== undefined && Number(rain) >= 55) {
            text = 'Rain likely today';
            className += ' climate-tile__hint--rain';
        } else if (rain !== null && rain !== undefined && Number(rain) >= 30) {
            text = 'Showers possible today';
            className += ' climate-tile__hint--rain';
        } else if (cloud !== null && cloud !== undefined && Number(cloud) >= 75) {
            if (isDay === true) {
                text = 'Overcast — limited solar';
                className += ' climate-tile__hint--solar';
            } else {
                text = 'Overcast skies';
            }
        } else if (
            isDay === true &&
            wind !== null && wind !== undefined && Number(wind) <= 14 &&
            (rain === null || rain === undefined || Number(rain) < 20)
        ) {
            text = 'Calm and dry';
        }

        els.hint.textContent = text;
        els.hint.className = className;
    }

    function applySensorTemps(data) {
        if (!data) return;

        applyColdStripConfig(data);

        if (data.outside_temp_c != null) {
            sensorOutside = data.outside_temp_c;
        } else if (data.temp_c != null) {
            sensorOutside = data.temp_c;
        }

        if (fridgeConfigured && data.fridge_temp_c != null) {
            sensorFridge = data.fridge_temp_c;
        }

        if (freezerConfigured && data.freezer_temp_c != null) {
            sensorFreezer = data.freezer_temp_c;
        }

        if (sensorOutside != null && els.outside) {
            els.outside.textContent = formatTemp(sensorOutside);
        }

        if (fridgeConfigured) applyFridge(sensorFridge);
        if (freezerConfigured) applyFreezer(sensorFreezer);

        updateForecastCurve(
            normalizeHourlyForecast(lastWeather),
            currentOutsideTemp()
        );
    }

    function updateWeatherData(data) {
        if (!data) return;
        lastWeather = { ...lastWeather, ...data };

        if (data.summary !== undefined && els.summary) {
            els.summary.textContent = data.summary || '—';
        }

        const tempMin = data.temp_min ?? data.temperature_min ?? data.daily_min;
        const tempMax = data.temp_max ?? data.temperature_max ?? data.daily_max;
        if (tempMin !== undefined || tempMax !== undefined) {
            applyDailyRange(
                tempMin !== undefined ? tempMin : lastWeather.temp_min,
                tempMax !== undefined ? tempMax : lastWeather.temp_max
            );
        }

        const low = data.low_tonight_c ?? data.low_tonight ?? data.overnight_low;
        if (low !== undefined && els.lowTonight) {
            els.lowTonight.textContent = formatShortTemp(low);
        }

        const code = data.weather_code ?? data.code;
        const isDay = data.is_day ?? data.isDay;
        if (code !== undefined) {
            applyWeatherIcon(code, isDay);
        }

        const humidity = data.humidity_percent ?? data.humidity ?? data.relative_humidity;
        if (humidity !== undefined && els.humidity) {
            els.humidity.textContent = formatHumidity(humidity);
        }

        applyFeelsLike();
        applyOutlook(data);

        const hourlyForecast = normalizeHourlyForecast(data);
        if (hourlyForecast.length) {
            lastWeather.hourly_forecast = hourlyForecast;
        }
        updateForecastCurve(
            normalizeHourlyForecast(lastWeather),
            currentOutsideTemp()
        );
        applyHint(data);
    }

    function setLocation(newLat, newLng) {
        if (typeof newLat !== 'number' || typeof newLng !== 'number') return;
        lat = newLat;
        lng = newLng;
        fetchApiWeather();
    }

    async function fetchApiWeather() {
        try {
            const res = await fetch('/api/weather', { cache: 'no-store' });
            if (!res.ok) return false;
            update(await res.json());
            return true;
        } catch {
            return false;
        }
    }

    async function fetchSensors() {
        try {
            const res = await fetch('/api/sensors', { cache: 'no-store' });
            if (!res.ok) return;
            onSensorUpdate(await res.json());
        } catch {
            /* socket will deliver sensor_update */
        }
    }

    function update(data) {
        if (!data) return;

        if (typeof data.latitude === 'number' && typeof data.longitude === 'number') {
            lat = data.latitude;
            lng = data.longitude;
        }

        applySensorTemps(data);

        const apiTemp = data.temperature_c ?? data.outside_temp ?? data.temp;
        if (apiTemp !== undefined && apiTemp !== null) {
            forecastOutside = apiTemp;
        }

        if (sensorOutside == null && forecastOutside != null && els.outside) {
            els.outside.textContent = formatTemp(forecastOutside);
        }

        updateWeatherData(data);
    }

    function onSensorUpdate(data) {
        if (!data) return;
        applySensorTemps(data);
    }

    fetchSensors();
    fetchApiWeather();
    setInterval(fetchSensors, SENSOR_POLL_INTERVAL_MS);
    setInterval(fetchApiWeather, API_WEATHER_INTERVAL_MS);

    if (els.forecastArc) {
        bindForecastPathHover();

        requestAnimationFrame(() => {
            updateForecastGeometry();
            requestAnimationFrame(updateForecastGeometry);
        });

        let resizeTimer;
        const queueForecastGeometry = () => {
            clearTimeout(resizeTimer);
            resizeTimer = setTimeout(updateForecastGeometry, 140);
        };

        window.addEventListener('resize', queueForecastGeometry);

        if (typeof ResizeObserver !== 'undefined') {
            const forecastObserver = new ResizeObserver(queueForecastGeometry);
            forecastObserver.observe(els.forecastArc);
            const forecastWrap = els.forecastArc.parentElement;
            if (forecastWrap) forecastObserver.observe(forecastWrap);
        }

        document.addEventListener('visibilitychange', () => {
            if (!document.hidden) updateForecastGeometry();
        });

        setInterval(() => {
            updateForecastCurve(lastHourlyForecast, lastCurrentTemp);
        }, SCROLL_TICK_MS);
    }

    window.SCCS = window.SCCS || {};
    window.SCCS.climate = {
        update,
        onSensorUpdate,
        setLocation,
        refreshSensors: fetchSensors,
        refreshApiWeather: fetchApiWeather,
        refreshWeather: fetchApiWeather,
    };

    window.climateTile = window.SCCS.climate;
    window.temperatureTile = window.SCCS.climate;
    window.weatherTile = window.SCCS.climate;
})();