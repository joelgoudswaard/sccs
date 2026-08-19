"""Weather conditions — Open-Meteo forecast with wttr.in fallback when unavailable.

When the primary source returns fewer than five daily entries (needed for the
4-day outlook UI), daily highs/lows are supplemented from api.met.no.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import requests

_gps_module = None

_DEFAULT_LAT = -37.191
_DEFAULT_LNG = 145.711

_CACHE: dict[str, Any] = {"ts": 0.0, "payload": None}
_CACHE_TTL = 300.0
_OPEN_METEO_BACKOFF_UNTIL = 0.0
_OPEN_METEO_BACKOFF_S = 1800.0
_FORECAST_DAYS = 7
_OUTLOOK_DAYS_REQUIRED = 5
_METNO_USER_AGENT = "sccs/1.0 (https://github.com/joelgoudswaard/sccs)"

_SUMMARIES = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Foggy",
    48: "Rime fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    80: "Rain showers",
    81: "Rain showers",
    82: "Heavy showers",
    95: "Thunderstorm",
}

_UNAVAILABLE: dict[str, Any] = {
    "summary": None,
    "temperature_c": None,
    "feels_like_c": None,
    "wind_kmh": None,
    "wind_direction_deg": None,
    "rain_chance_percent": None,
    "humidity_percent": None,
    "cloud_cover_percent": None,
    "low_tonight_c": None,
    "temp_min": None,
    "temp_max": None,
    "hourly_forecast": [],
    "daily_forecast": [],
    "weather_code": None,
    "is_day": None,
    "source": "unavailable",
}


def set_gps_module(gps_module) -> None:
    global _gps_module
    _gps_module = gps_module


def _valid_coords(lat, lng) -> bool:
    try:
        lat_f = float(lat)
        lng_f = float(lng)
    except (TypeError, ValueError):
        return False
    if abs(lat_f) > 90 or abs(lng_f) > 180:
        return False
    # 0,0 is empty NMEA / null island — never a real SCCS position.
    if lat_f == 0.0 and lng_f == 0.0:
        return False
    return True


def _coords() -> tuple[float, float]:
    if _gps_module is not None:
        state = _gps_module.get_state()
        lat = state.get("latitude")
        lng = state.get("longitude")
        if _valid_coords(lat, lng):
            return float(lat), float(lng)
    return _DEFAULT_LAT, _DEFAULT_LNG


def _summary_for_code(code: int | None) -> str:
    if code is None:
        return "—"
    return _SUMMARIES.get(int(code), "Mixed conditions")


def _num(value: Any) -> float | int | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_ampm_time(value: str) -> tuple[int, int] | None:
    text = (value or "").strip().upper()
    for fmt in ("%I:%M %p", "%H:%M"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.hour, parsed.minute
        except ValueError:
            continue
    return None


def _is_day_from_astronomy(astronomy: list[dict[str, Any]] | None) -> bool | None:
    if not astronomy:
        return None
    entry = astronomy[0]
    sunrise = _parse_ampm_time(str(entry.get("sunrise", "")))
    sunset = _parse_ampm_time(str(entry.get("sunset", "")))
    if not sunrise or not sunset:
        return None
    now = datetime.now()
    now_mins = now.hour * 60 + now.minute
    rise_mins = sunrise[0] * 60 + sunrise[1]
    set_mins = sunset[0] * 60 + sunset[1]
    if rise_mins <= set_mins:
        return rise_mins <= now_mins < set_mins
    return now_mins >= rise_mins or now_mins < set_mins


def _wttr_hour_iso(date_value: str, time_value: str) -> str | None:
    if not date_value or time_value is None:
        return None
    try:
        hhmm = int(str(time_value))
    except ValueError:
        return None
    hours = hhmm // 100
    minutes = hhmm % 100
    if hours > 23 or minutes > 59:
        return None
    return f"{date_value}T{hours:02d}:{minutes:02d}:00"


_WWO_TO_WMO = {
    113: 0,
    116: 2,
    119: 3,
    122: 3,
    143: 45,
    176: 61,
    179: 71,
    182: 71,
    185: 56,
    200: 95,
    227: 71,
    230: 75,
    248: 45,
    260: 45,
    263: 51,
    266: 51,
    281: 56,
    284: 57,
    293: 61,
    296: 61,
    299: 63,
    302: 63,
    305: 65,
    308: 65,
    311: 66,
    314: 67,
    317: 71,
    320: 73,
    323: 71,
    326: 71,
    329: 73,
    332: 73,
    335: 75,
    338: 75,
    350: 77,
    353: 80,
    356: 81,
    359: 82,
    362: 85,
    365: 86,
    368: 85,
    371: 86,
    374: 77,
    377: 77,
    386: 95,
    389: 95,
    392: 96,
    395: 99,
}


def _local_date_from_iso(iso_utc: str) -> str | None:
    try:
        dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone().date().isoformat()


def _wmo_from_metno_symbol(symbol: str | None) -> int:
    if not symbol:
        return 3
    text = symbol.lower()
    if "thunder" in text:
        return 95
    if "heavyrain" in text or "heavysleet" in text:
        return 65
    if "lightrain" in text or "lightsleet" in text:
        return 61
    if "rain" in text or "sleet" in text or "showers" in text:
        return 63
    if "heavysnow" in text:
        return 75
    if "lightsnow" in text:
        return 71
    if "snow" in text:
        return 73
    if "fog" in text:
        return 45
    if "clearsky" in text:
        return 0
    if text.startswith("fair"):
        return 1
    if "partlycloudy" in text:
        return 2
    if "cloudy" in text:
        return 3
    return 3


def _daily_from_metno(data: dict[str, Any]) -> list[dict[str, Any]]:
    series = (data.get("properties") or {}).get("timeseries") or []
    buckets: dict[str, dict[str, Any]] = {}

    for entry in series:
        iso = entry.get("time")
        if not iso:
            continue
        date_key = _local_date_from_iso(iso)
        if not date_key:
            continue

        instant = (entry.get("data") or {}).get("instant", {}).get("details", {})
        temp = _num(instant.get("air_temperature"))
        if temp is None:
            continue

        bucket = buckets.setdefault(date_key, {"temps": [], "symbols": []})
        bucket["temps"].append(float(temp))

        block = None
        for key in ("next_12_hours", "next_6_hours", "next_1_hours"):
            block = (entry.get("data") or {}).get(key)
            if block:
                break
        if block:
            symbol = (block.get("summary") or {}).get("symbol_code")
            if symbol:
                bucket["symbols"].append(symbol)

    daily: list[dict[str, Any]] = []
    for date_key in sorted(buckets):
        bucket = buckets[date_key]
        temps = bucket["temps"]
        if not temps:
            continue
        symbols = bucket["symbols"]
        symbol = symbols[len(symbols) // 2] if symbols else None
        daily.append(
            {
                "date": date_key,
                "weather_code": _wmo_from_metno_symbol(symbol),
                "temp_min": min(temps),
                "temp_max": max(temps),
            }
        )
    return daily


def _fetch_metno_daily(lat: float, lng: float) -> list[dict[str, Any]] | None:
    url = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
    headers = {"User-Agent": _METNO_USER_AGENT}
    try:
        response = requests.get(
            url,
            params={"lat": lat, "lon": lng},
            headers=headers,
            timeout=12,
        )
        response.raise_for_status()
        daily = _daily_from_metno(response.json())
        return daily or None
    except (OSError, requests.RequestException, ValueError):
        return None


def _merge_daily_forecast(
    primary: list[dict[str, Any]],
    supplemental: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_date = {day["date"]: day for day in primary if day.get("date")}
    for day in supplemental:
        date_key = day.get("date")
        if date_key and date_key not in by_date:
            by_date[date_key] = day
    return [by_date[key] for key in sorted(by_date)]


def _ensure_outlook_days(payload: dict[str, Any], lat: float, lng: float) -> None:
    daily = payload.get("daily_forecast") or []
    if len(daily) >= _OUTLOOK_DAYS_REQUIRED:
        return
    extra = _fetch_metno_daily(lat, lng)
    if not extra:
        return
    payload["daily_forecast"] = _merge_daily_forecast(daily, extra)


def _wmo_from_wttr_code(code: Any) -> int | None:
    if code is None or code == "":
        return None
    try:
        wwo = int(code)
    except (TypeError, ValueError):
        return None
    return _WWO_TO_WMO.get(wwo, 3)


def _fetch_open_meteo(lat: float, lng: float) -> dict[str, Any] | None:
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lng}"
        "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,weathercode"
        "&hourly=temperature_2m"
        "&past_hours=6"
        f"&forecast_hours=24&forecast_days={_FORECAST_DAYS}"
        "&current=relative_humidity_2m,temperature_2m,weather_code,is_day,"
        "wind_speed_10m,wind_direction_10m,apparent_temperature,cloud_cover"
        "&timezone=auto"
    )
    global _OPEN_METEO_BACKOFF_UNTIL
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 429:
            _OPEN_METEO_BACKOFF_UNTIL = time.time() + _OPEN_METEO_BACKOFF_S
            return None
        response.raise_for_status()
        _OPEN_METEO_BACKOFF_UNTIL = 0.0
        return response.json()
    except (OSError, requests.RequestException):
        _OPEN_METEO_BACKOFF_UNTIL = time.time() + _OPEN_METEO_BACKOFF_S
        return None


def _fetch_wttr(lat: float, lng: float) -> dict[str, Any] | None:
    url = f"https://wttr.in/{lat},{lng}?format=j1"
    try:
        response = requests.get(
            url,
            timeout=12,
            headers={"User-Agent": "sccs/1.0"},
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else None
    except (OSError, requests.RequestException, ValueError):
        return None


def _status_from_open_meteo(data: dict[str, Any], lat: float, lng: float) -> dict[str, Any]:
    current = data.get("current") or {}
    daily = data.get("daily") or {}
    code = current.get("weather_code")
    payload: dict[str, Any] = {
        "summary": _summary_for_code(code),
        "temperature_c": current.get("temperature_2m"),
        "feels_like_c": current.get("apparent_temperature"),
        "wind_kmh": current.get("wind_speed_10m"),
        "wind_direction_deg": current.get("wind_direction_10m"),
        "humidity_percent": current.get("relative_humidity_2m"),
        "cloud_cover_percent": current.get("cloud_cover"),
        "weather_code": code,
        "is_day": bool(current.get("is_day") == 1),
        "latitude": lat,
        "longitude": lng,
        "source": "open-meteo",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }

    hourly = data.get("hourly") or {}
    hour_times = hourly.get("time") or []
    hour_temps = hourly.get("temperature_2m") or []
    hour_count = min(len(hour_times), len(hour_temps))
    if hour_count:
        payload["hourly_forecast"] = [
            {
                "time": hour_times[i],
                "temperature_c": hour_temps[i],
            }
            for i in range(hour_count)
            if hour_temps[i] is not None
        ]

    day_times = daily.get("time") or []
    mins = daily.get("temperature_2m_min") or []
    maxs = daily.get("temperature_2m_max") or []
    day_codes = daily.get("weathercode") or []
    rain = daily.get("precipitation_probability_max") or []
    day_count = min(len(day_times), len(mins), len(maxs), len(day_codes))
    if day_count:
        payload["daily_forecast"] = [
            {
                "date": day_times[i],
                "weather_code": day_codes[i],
                "temp_min": mins[i],
                "temp_max": maxs[i],
            }
            for i in range(day_count)
        ]
    if mins:
        payload["temp_min"] = mins[0]
        payload["low_tonight_c"] = mins[0]
    if maxs:
        payload["temp_max"] = maxs[0]
    if rain:
        payload["rain_chance_percent"] = rain[0]
    return payload


def _status_from_wttr(data: dict[str, Any], lat: float, lng: float) -> dict[str, Any]:
    current = (data.get("current_condition") or [{}])[0]
    days = data.get("weather") or []
    today = days[0] if days else {}
    code = _wmo_from_wttr_code(current.get("weatherCode"))
    summary = None
    desc = current.get("weatherDesc")
    if isinstance(desc, list) and desc:
        summary = desc[0].get("value")
    if not summary:
        summary = _summary_for_code(code)

    payload: dict[str, Any] = {
        "summary": summary,
        "temperature_c": _num(current.get("temp_C")),
        "feels_like_c": _num(current.get("FeelsLikeC")),
        "wind_kmh": _num(current.get("windspeedKmph")),
        "wind_direction_deg": _num(current.get("winddirDegree")),
        "humidity_percent": _num(current.get("humidity")),
        "cloud_cover_percent": _num(current.get("cloudcover")),
        "weather_code": code,
        "is_day": _is_day_from_astronomy(today.get("astronomy")),
        "latitude": lat,
        "longitude": lng,
        "source": "wttr.in",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }

    hourly_forecast: list[dict[str, Any]] = []
    daily_forecast: list[dict[str, Any]] = []
    rain_chances: list[float] = []

    for day in days:
        date_value = day.get("date")
        temp_min = _num(day.get("mintempC"))
        temp_max = _num(day.get("maxtempC"))
        day_hours = day.get("hourly") or []
        day_code = None
        if day_hours:
            day_code = _wmo_from_wttr_code(day_hours[len(day_hours) // 2].get("weatherCode"))
        if date_value and temp_min is not None and temp_max is not None:
            daily_forecast.append(
                {
                    "date": date_value,
                    "weather_code": day_code,
                    "temp_min": temp_min,
                    "temp_max": temp_max,
                }
            )

        for hour in day_hours:
            when = _wttr_hour_iso(date_value, hour.get("time"))
            temp = _num(hour.get("tempC"))
            chance = _num(hour.get("chanceofrain"))
            if chance is not None:
                rain_chances.append(chance)
            if when and temp is not None:
                hourly_forecast.append({"time": when, "temperature_c": temp})

    if hourly_forecast:
        payload["hourly_forecast"] = hourly_forecast
    if daily_forecast:
        payload["daily_forecast"] = daily_forecast
        payload["temp_min"] = daily_forecast[0]["temp_min"]
        payload["low_tonight_c"] = daily_forecast[0]["temp_min"]
        payload["temp_max"] = daily_forecast[0]["temp_max"]
    if rain_chances:
        payload["rain_chance_percent"] = max(rain_chances)
    return payload


def get_weather_status() -> dict[str, Any]:
    now = time.time()
    if _CACHE["payload"] and now - _CACHE["ts"] < _CACHE_TTL:
        return dict(_CACHE["payload"])

    lat, lng = _coords()
    data = None
    if now >= _OPEN_METEO_BACKOFF_UNTIL:
        data = _fetch_open_meteo(lat, lng)
    if data:
        payload = _status_from_open_meteo(data, lat, lng)
    else:
        wttr = _fetch_wttr(lat, lng)
        if wttr:
            payload = _status_from_wttr(wttr, lat, lng)
        else:
            cached = _CACHE["payload"]
            if cached:
                return dict(cached)
            return dict(_UNAVAILABLE)

    _ensure_outlook_days(payload, lat, lng)

    _CACHE.update({"ts": now, "payload": payload})
    return dict(payload)