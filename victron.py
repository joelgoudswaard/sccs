"""Battery & solar status — VictronManager BLE first, dbus fallback second."""

from __future__ import annotations

import re
import subprocess
from typing import Any

_victron_manager = None

# Fallback when no BLE manager and no dbus services.
_UNAVAILABLE: dict[str, Any] = {
    "soc": None,
    "voltage": None,
    "solar_current": None,
    "battery_current": None,
    "time_to_go": None,
    "yield_today": None,
    "stale": True,
    "source": "unavailable",
}

_VARIANT_RE = re.compile(
    r"variant\s+(?:double|int32|uint32|int64|uint64|byte)\s+([-\d.]+)",
    re.IGNORECASE,
)


def set_victron_manager(victron_manager) -> None:
    global _victron_manager
    _victron_manager = victron_manager


def _normalize_ble_state(state: dict[str, Any]) -> dict[str, Any]:
    ttg_mins = state.get("time_to_go_mins")
    ttg_seconds = None
    if ttg_mins is not None:
        try:
            ttg_seconds = int(float(ttg_mins) * 60)
        except (TypeError, ValueError):
            ttg_seconds = None

    return {
        "soc": state.get("soc"),
        "voltage": state.get("voltage"),
        "solar_current": state.get("solar_current_a"),
        "solar_current_a": state.get("solar_current_a"),
        "battery_current": state.get("current_a"),
        "current_a": state.get("current_a"),
        "time_to_go": ttg_seconds,
        "time_to_go_mins": ttg_mins,
        "yield_today": state.get("yield_today_kwh"),
        "yield_today_kwh": state.get("yield_today_kwh"),
        "solar_power_w": state.get("solar_power_w"),
        "consumed_ah": state.get("consumed_ah"),
        "charge_state": state.get("charge_state"),
        "temperature": state.get("temperature"),
        "battery_temp_c": state.get("temperature"),
        "stale": bool(state.get("stale")),
        "last_update": state.get("last_update"),
        "source": "victron-ble",
    }


def _list_victron_services() -> list[str]:
    try:
        out = subprocess.run(
            [
                "dbus-send",
                "--system",
                "--print-reply",
                "--dest=org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus.ListNames",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    if out.returncode != 0:
        return []

    return [
        m.group(1)
        for m in re.finditer(r'string "([^"]+)"', out.stdout)
        if m.group(1).startswith("com.victronenergy.")
    ]


def _find_service(prefix: str) -> str | None:
    for name in _list_victron_services():
        if name.startswith(prefix):
            return name
    return None


def _dbus_value(service: str, path: str) -> float | None:
    try:
        out = subprocess.run(
            [
                "dbus-send",
                "--system",
                "--print-reply",
                f"--dest={service}",
                path,
                "com.victronenergy.BusItem.GetValue",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    if out.returncode != 0:
        return None

    match = _VARIANT_RE.search(out.stdout)
    if not match:
        return None

    try:
        return float(match.group(1))
    except ValueError:
        return None


def _round1(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 1)


def _get_dbus_power_status() -> dict[str, Any] | None:
    battery = _find_service("com.victronenergy.battery.")
    mppt = _find_service("com.victronenergy.solarcharger.")
    if not battery and not mppt:
        return None

    soc = _dbus_value(battery, "/Soc") if battery else None
    voltage = _dbus_value(battery, "/Dc/0/Voltage") if battery else None
    battery_current = _dbus_value(battery, "/Dc/0/Current") if battery else None
    time_to_go = _dbus_value(battery, "/TimeToGo") if battery else None
    solar_current = _dbus_value(mppt, "/Pv/Current") if mppt else None
    yield_today = _dbus_value(mppt, "/Yield/Today") if mppt else None

    if voltage is None and mppt:
        voltage = _dbus_value(mppt, "/Dc/0/Voltage")

    result: dict[str, Any] = {
        "soc": round(soc) if soc is not None else None,
        "voltage": _round1(voltage),
        "solar_current": _round1(solar_current),
        "battery_current": _round1(battery_current),
        "time_to_go": int(time_to_go) if time_to_go is not None else None,
        "yield_today": _round1(yield_today),
        "stale": False,
        "source": "victron-dbus",
    }
    if battery:
        result["battery_service"] = battery
    if mppt:
        result["mppt_service"] = mppt
    return result


def get_power_status() -> dict[str, Any]:
    """Return power tile payload from BLE manager, dbus, or unavailable stub."""
    if _victron_manager is not None and getattr(_victron_manager, "device_keys", None):
        return _normalize_ble_state(_victron_manager.get_state())

    dbus_status = _get_dbus_power_status()
    if dbus_status is not None:
        return dbus_status

    return dict(_UNAVAILABLE)