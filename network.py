"""Network status for the home Network tile."""

from __future__ import annotations

import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from wifi import get_wifi_status

_ping_cache: dict[str, Any] = {"ts": 0.0, "ms": None, "status": "unknown"}
_PING_CACHE_TTL = 8.0

_throughput_cache: dict[str, Any] = {
    "ts": 0.0,
    "iface": None,
    "rx_bytes": 0,
    "tx_bytes": 0,
    "rx_kbps": 0.0,
    "tx_kbps": 0.0,
}


def _get_cached_ping() -> tuple[int | None, str]:
    now = time.time()
    if (
        _ping_cache["ts"]
        and now - _ping_cache["ts"] < _PING_CACHE_TTL
        and _ping_cache["ms"] is not None
    ):
        return _ping_cache["ms"], _ping_cache["status"]

    ms, status = None, "fail"
    try:
        start = time.time()
        subprocess.check_output(
            ["ping", "-c", "1", "-W", "1", "1.1.1.1"],
            stderr=subprocess.DEVNULL,
            timeout=2.2,
        )
        ms = int(round((time.time() - start) * 1000))
    except (OSError, subprocess.SubprocessError):
        pass

    if ms is None:
        try:
            start = time.time()
            result = subprocess.run(
                ["curl", "-I", "--connect-timeout", "2", "--max-time", "2", "http://1.1.1.1"],
                capture_output=True,
                text=True,
                timeout=2.5,
                check=False,
            )
            if result.returncode == 0:
                ms = int(round((time.time() - start) * 1000))
        except (OSError, subprocess.SubprocessError):
            pass

    if ms is not None:
        status = "good" if ms < 50 else ("slow" if ms < 150 else "fail")

    _ping_cache.update({"ts": now, "ms": ms, "status": status})
    return ms, status


def _read_iface_bytes(iface: str) -> tuple[int, int] | None:
    try:
        lines = Path("/proc/net/dev").read_text(encoding="ascii").splitlines()
    except OSError:
        return None

    for line in lines:
        line = line.strip()
        if not line.startswith(f"{iface}:"):
            continue
        parts = line.split()
        if len(parts) < 10:
            return None
        return int(parts[1]), int(parts[9])
    return None


def _read_link_speed_mbps(iface: str) -> int | None:
    """Best-effort negotiated link speed in Mbps (sysfs for wired, iw for wireless)."""
    if not iface:
        return None

    speed_path = Path(f"/sys/class/net/{iface}/speed")
    try:
        value = int(speed_path.read_text(encoding="ascii").strip())
        if 0 < value <= 100_000:
            return value
    except (OSError, ValueError):
        pass

    try:
        out = subprocess.check_output(
            ["iw", "dev", iface, "link"],
            stderr=subprocess.DEVNULL,
            timeout=2,
            text=True,
        )
        for line in out.splitlines():
            if "tx bitrate" not in line.lower():
                continue
            for part in line.split():
                token = part.rstrip(":")
                if token.replace(".", "", 1).isdigit():
                    return int(round(float(token)))
    except (OSError, subprocess.SubprocessError):
        pass

    return None


def _throughput_kbps(iface: str | None) -> tuple[float, float]:
    if not iface:
        return 0.0, 0.0

    sample = _read_iface_bytes(iface)
    if sample is None:
        return 0.0, 0.0

    rx_bytes, tx_bytes = sample
    now = time.time()
    prev = _throughput_cache

    if prev["iface"] == iface and prev["ts"]:
        elapsed = max(now - prev["ts"], 0.001)
        rx_kbps = max(0.0, (rx_bytes - prev["rx_bytes"]) * 8 / elapsed / 1000)
        tx_kbps = max(0.0, (tx_bytes - prev["tx_bytes"]) * 8 / elapsed / 1000)
    else:
        rx_kbps = prev.get("rx_kbps", 0.0)
        tx_kbps = prev.get("tx_kbps", 0.0)

    _throughput_cache.update({
        "ts": now,
        "iface": iface,
        "rx_bytes": rx_bytes,
        "tx_bytes": tx_bytes,
        "rx_kbps": rx_kbps,
        "tx_kbps": tx_kbps,
    })
    return round(rx_kbps, 1), round(tx_kbps, 1)


def _internet_connected(ping_ms: int | None, wifi_connected: bool) -> bool:
    if ping_ms is not None:
        return True
    return bool(wifi_connected)


def _friendly_connection_name(wifi: dict[str, Any]) -> str:
    if wifi.get("connected") and wifi.get("ssid"):
        return str(wifi["ssid"])
    iface = wifi.get("iface")
    if iface:
        return str(iface)
    return "No Internet"


def _signal_quality(wifi: dict[str, Any]) -> str | None:
    signal = wifi.get("signal")
    if signal is None:
        return None
    try:
        return f"{int(signal)}%"
    except (TypeError, ValueError):
        return str(signal)


def build_network_status(app_start_time: datetime | None = None) -> dict[str, Any]:
    """Assemble payload for the Network home tile."""
    wifi = get_wifi_status()
    ping_ms, ping_status = _get_cached_ping()
    connected = _internet_connected(ping_ms, bool(wifi.get("connected")))
    iface = wifi.get("iface")
    rx_kbps, tx_kbps = _throughput_kbps(iface)
    link_speed = _read_link_speed_mbps(iface) if iface else None

    payload: dict[str, Any] = {
        "internet": {
            "connected": connected,
            "friendly_name": _friendly_connection_name(wifi),
            "rx_kbps": rx_kbps,
            "tx_kbps": tx_kbps,
            "ping_ms": ping_ms,
            "ping_status": ping_status,
            "signal_quality": _signal_quality(wifi),
            "link_speed_mbps": link_speed,
            "iface": iface,
            "ssid": wifi.get("ssid"),
        },
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }

    if app_start_time:
        uptime_s = max(0, int((datetime.now() - app_start_time).total_seconds()))
        hours = uptime_s // 3600
        minutes = (uptime_s % 3600) // 60
        payload["app_uptime"] = f"{hours}h {minutes}m" if hours else f"{minutes}m"

    return payload