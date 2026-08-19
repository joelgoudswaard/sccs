"""GPS status for REST — live from modules/gps when runtime is up."""

from __future__ import annotations

from typing import Any

_gps_module = None

_UNAVAILABLE: dict[str, Any] = {
    "latitude": None,
    "longitude": None,
    "altitude_m": None,
    "label": "—",
    "suburb": None,
    "satellites": 0,
    "fix_quality": 0,
    "source": "unavailable",
}


def set_gps_module(gps_module) -> None:
    global _gps_module
    _gps_module = gps_module


def get_gps_status() -> dict[str, Any]:
    if _gps_module is None:
        return dict(_UNAVAILABLE)

    state = _gps_module.get_state()
    payload = {
        "latitude": state.get("latitude"),
        "longitude": state.get("longitude"),
        "altitude_m": state.get("altitude_m"),
        "label": state.get("suburb") or state.get("last_known_suburb") or "—",
        "suburb": state.get("suburb"),
        "satellites": state.get("satellites", 0),
        "fix_quality": state.get("fix_quality", 0),
        "speed_kmh": state.get("speed_kmh"),
        "course_deg": state.get("course_deg"),
        "hdop": state.get("hdop"),
        "local_time": state.get("local_time"),
        "utc_time": state.get("utc_time"),
        "date": state.get("date"),
        "timezone": state.get("timezone"),
        "sunrise": state.get("sunrise"),
        "sunset": state.get("sunset"),
        "raw_sentences": state.get("raw_sentences", []),
        "hardware_missing": state.get("hardware_missing", False),
        "source": "live",
    }
    return payload