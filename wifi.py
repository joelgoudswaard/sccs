"""Wi-Fi status, scan, connect, disconnect, and uplink preference via NetworkManager."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from modules.ui_state import ConfigManager

_WIFI_IFACE: str | None = None

# Route metrics (lower wins). Match install.sh USB-tether setup.
_METRIC_PREFERRED = 50
_METRIC_FALLBACK = 200

# Persisted preference (Wi‑Fi vs USB tether). Applied via route metrics when paths exist.
_uplink_pref_store = ConfigManager("preferred_uplink.json", {"prefer": "usb"})

# Van LAN is eth0 — never treat as phone USB tether / WAN.
_LAN_IFACES = frozenset({"lo", "eth0"})
_TETHER_NAME_RE = re.compile(
    r"^(eth[1-9]\d*|usb\d+|rndis\d+|enx[0-9a-f]+|enp\d+s\d+u\d+)$",
    re.IGNORECASE,
)

_UNAVAILABLE: dict[str, Any] = {
    "available": False,
    "iface": None,
    "state": "unavailable",
    "connected": False,
    "ssid": None,
    "signal": None,
    "security": None,
    "ip": None,
    "networks": [],
    "uplink": {
        "wifi": None,
        "usb": None,
        "active": None,
        "preferred": "usb",
        "wifi_online": False,
        "usb_online": False,
    },
    "error": "NetworkManager (nmcli) not available",
    "source": "unavailable",
}


def get_preferred_uplink() -> str:
    prefer = str(_uplink_pref_store.load().get("prefer") or "usb").strip().lower()
    return prefer if prefer in {"wifi", "usb"} else "usb"


def _save_preferred_uplink(prefer: str) -> None:
    _uplink_pref_store.save({"prefer": prefer})


def _nmcli_available() -> bool:
    return shutil.which("nmcli") is not None


def _run_nmcli(args: list[str], *, timeout: float = 30) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            ["nmcli", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", str(exc)


def _wifi_iface() -> str | None:
    global _WIFI_IFACE

    if _WIFI_IFACE:
        return _WIFI_IFACE

    code, stdout, _ = _run_nmcli(["-t", "-f", "DEVICE,TYPE", "device", "status"], timeout=5)
    if code != 0:
        return None

    for line in stdout.splitlines():
        parts = line.split(":")
        if len(parts) >= 2 and parts[1] == "wifi":
            _WIFI_IFACE = parts[0]
            return _WIFI_IFACE

    return None


def _band_from_channel(channel: str) -> str | None:
    try:
        value = int(channel)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return "2.4 GHz" if value < 15 else "5 GHz"


def _format_security(raw: str) -> str:
    value = (raw or "").strip()
    if not value or value == "--":
        return "Open"
    if "WPA3" in value:
        return "WPA3"
    if "WPA2" in value:
        return "WPA2"
    if "WPA1" in value or value == "WPA":
        return "WPA"
    if "WEP" in value:
        return "WEP"
    return value


def _device_state(iface: str) -> str:
    code, stdout, _ = _run_nmcli(["-g", "GENERAL.STATE", "device", "show", iface], timeout=5)
    if code != 0 or not stdout:
        return "unknown"

    match = re.search(r"(\d+)", stdout)
    if not match:
        return "unknown"

    numeric = int(match.group(1))
    if numeric == 100:
        return "connected"
    if numeric in {20, 30, 50, 60, 70, 80, 90}:
        return "connecting"
    if numeric <= 30:
        return "disconnected"
    return "unavailable"


def _device_ip(iface: str) -> str | None:
    code, stdout, _ = _run_nmcli(["-g", "IP4.ADDRESS", "device", "show", iface], timeout=5)
    if code != 0 or not stdout:
        return None

    for line in stdout.splitlines():
        ip = line.split("/", 1)[0].strip()
        if ip:
            return ip
    return None


def _connection_for_device(iface: str) -> str | None:
    code, stdout, _ = _run_nmcli(
        ["-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
        timeout=5,
    )
    if code != 0:
        return None

    for line in stdout.splitlines():
        # Connection names can contain colons; device is the last field.
        parts = line.rsplit(":", 1)
        if len(parts) != 2:
            continue
        name, device = parts[0].strip(), parts[1].strip()
        if device == iface and name:
            return name
    return None


def _connection_route_metric(connection: str) -> int | None:
    code, stdout, _ = _run_nmcli(
        ["-g", "ipv4.route-metric", "connection", "show", connection],
        timeout=5,
    )
    if code != 0:
        return None
    value = (stdout or "").strip()
    if not value or value == "-1":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _is_tether_iface(name: str) -> bool:
    if not name or name in _LAN_IFACES:
        return False
    if name.startswith("wlan") or name.startswith("p2p-"):
        return False
    if name.startswith(("veth", "br", "docker", "virbr", "vnet")):
        return False
    if _TETHER_NAME_RE.match(name):
        return True
    if name.startswith("eth") and name != "eth0":
        return True
    # USB-backed NICs often expose a device symlink under sysfs.
    device_path = Path(f"/sys/class/net/{name}/device")
    if device_path.exists():
        try:
            resolved = str(device_path.resolve())
        except OSError:
            resolved = ""
        if "/usb" in resolved.lower():
            return True
    return False


def _list_tether_ifaces() -> list[str]:
    try:
        names = sorted(p.name for p in Path("/sys/class/net").iterdir() if p.is_dir())
    except OSError:
        return []
    return [name for name in names if _is_tether_iface(name)]


def _default_routes() -> list[dict[str, Any]]:
    """Parse `ip -4 route show default` into iface/metric rows (lowest metric first)."""
    try:
        result = subprocess.run(
            ["ip", "-4", "route", "show", "default"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    routes: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if not parts or parts[0] != "default":
            continue
        iface = None
        metric = 0
        via = None
        i = 1
        while i < len(parts):
            token = parts[i]
            if token == "dev" and i + 1 < len(parts):
                iface = parts[i + 1]
                i += 2
                continue
            if token == "via" and i + 1 < len(parts):
                via = parts[i + 1]
                i += 2
                continue
            if token == "metric" and i + 1 < len(parts):
                try:
                    metric = int(parts[i + 1])
                except ValueError:
                    metric = 0
                i += 2
                continue
            i += 1
        if iface:
            routes.append({"iface": iface, "metric": metric, "via": via})

    routes.sort(key=lambda row: row["metric"])
    return routes


def _path_info(kind: str, iface: str | None, *, ssid: str | None = None) -> dict[str, Any] | None:
    if not iface:
        return None
    state = _device_state(iface)
    connected = state == "connected"
    ip = _device_ip(iface) if connected else None
    connection = _connection_for_device(iface) if connected else None
    metric = _connection_route_metric(connection) if connection else None
    info: dict[str, Any] = {
        "kind": kind,
        "iface": iface,
        "connected": False,
        "ip": ip,
        "connection": connection,
        "metric": metric,
        "state": state,
    }
    if kind == "wifi":
        info["ssid"] = ssid
        info["connected"] = bool(connected and ssid)
        info["label"] = ssid or "Wi‑Fi"
    else:
        info["connected"] = bool(connected and ip)
        info["label"] = "USB Hotspot"
    return info


def _find_usb_tether() -> dict[str, Any] | None:
    """Return the best connected USB-tether path, if any."""
    code, stdout, _ = _run_nmcli(
        ["-t", "-f", "DEVICE,TYPE,STATE", "device", "status"],
        timeout=5,
    )
    candidates: list[str] = []
    if code == 0:
        for line in stdout.splitlines():
            parts = line.split(":")
            if len(parts) < 3:
                continue
            device, dev_type, state = parts[0], parts[1], parts[2]
            if dev_type != "ethernet":
                continue
            if not state.startswith("connected"):
                continue
            if _is_tether_iface(device):
                candidates.append(device)

    for iface in _list_tether_ifaces():
        if iface not in candidates and _device_state(iface) == "connected":
            candidates.append(iface)

    best: dict[str, Any] | None = None
    for iface in candidates:
        info = _path_info("usb", iface)
        if not info or not info.get("connected"):
            continue
        if best is None:
            best = info
            continue
        best_metric = best.get("metric")
        info_metric = info.get("metric")
        if info_metric is not None and (best_metric is None or info_metric < best_metric):
            best = info
    return best


def _build_uplink(wifi_status: dict[str, Any]) -> dict[str, Any]:
    wifi_path = None
    if wifi_status.get("available") and wifi_status.get("iface"):
        wifi_path = _path_info(
            "wifi",
            str(wifi_status["iface"]),
            ssid=wifi_status.get("ssid"),
        )
        if wifi_path is not None:
            # Trust the already-computed Wi‑Fi association flags.
            wifi_path["connected"] = bool(wifi_status.get("connected"))
            wifi_path["ip"] = wifi_status.get("ip")
            wifi_path["ssid"] = wifi_status.get("ssid")
            wifi_path["label"] = wifi_status.get("ssid") or "Wi‑Fi"

    usb_path = _find_usb_tether()
    routes = _default_routes()
    active = None
    if routes:
        top = str(routes[0]["iface"])
        if usb_path and top == usb_path.get("iface"):
            active = "usb"
        elif wifi_path and top == wifi_path.get("iface"):
            active = "wifi"
        elif _is_tether_iface(top):
            active = "usb"
        elif top.startswith("wlan"):
            active = "wifi"

    wifi_connected = bool(wifi_path and wifi_path.get("connected"))
    usb_connected = bool(usb_path and usb_path.get("connected"))

    if active is None:
        if usb_connected and not wifi_connected:
            active = "usb"
        elif wifi_connected:
            active = "wifi"
        elif usb_connected:
            active = "usb"

    return {
        "wifi": wifi_path if wifi_connected else None,
        "usb": usb_path if usb_connected else None,
        "active": active,
        # Stored preference — UI always shows the toggle; runtime uses what's online.
        "preferred": get_preferred_uplink(),
        "wifi_online": wifi_connected,
        "usb_online": usb_connected,
        "default_routes": routes,
    }


def _set_connection_metric(connection: str, metric: int) -> tuple[bool, str | None]:
    code, stdout, stderr = _run_nmcli(
        ["connection", "modify", connection, "ipv4.route-metric", str(metric)],
        timeout=15,
    )
    if code != 0:
        message = stderr or stdout or "Failed to set route metric"
        return False, _normalize_nmcli_auth_error(message) or message
    return True, None


def _reapply_connection(iface: str, connection: str) -> None:
    code, _, _ = _run_nmcli(["device", "reapply", iface], timeout=20)
    if code == 0:
        return
    _run_nmcli(["connection", "up", connection], timeout=30)


def _parse_wifi_row(line: str) -> dict[str, Any] | None:
    parts = line.split(":")
    if len(parts) < 5:
        return None

    in_use = parts[0].strip() == "*"
    ssid = ":".join(parts[1:-3]).strip()
    signal_raw = parts[-3].strip()
    security_raw = parts[-2].strip()
    channel_raw = parts[-1].strip()

    if not ssid:
        return None

    try:
        signal = int(signal_raw)
    except ValueError:
        signal = 0

    security = _format_security(security_raw)
    return {
        "ssid": ssid,
        "signal": max(0, min(100, signal)),
        "security": security,
        "in_use": in_use,
        "band": _band_from_channel(channel_raw),
        "secured": security != "Open",
    }


def _parse_wifi_rows(stdout: str) -> list[dict[str, Any]]:
    networks: dict[str, dict[str, Any]] = {}

    for line in stdout.splitlines():
        entry = _parse_wifi_row(line)
        if not entry:
            continue

        ssid = entry["ssid"]
        existing = networks.get(ssid)
        if existing is None:
            networks[ssid] = entry
            continue

        if entry.get("in_use"):
            networks[ssid] = entry
            continue

        if existing.get("in_use"):
            existing["signal"] = max(existing["signal"], entry["signal"])
            continue

        if entry["signal"] > existing["signal"]:
            networks[ssid] = {**entry, "in_use": False}

    rows = list(networks.values())
    rows.sort(key=lambda row: (not row.get("in_use"), -row.get("signal", 0), row.get("ssid", "")))
    return rows


def _saved_ssids() -> set[str]:
    code, stdout, _ = _run_nmcli(["-t", "-f", "NAME,TYPE", "connection", "show"], timeout=5)
    if code != 0:
        return set()

    ssids: set[str] = set()
    for line in stdout.splitlines():
        parts = line.split(":", 1)
        if len(parts) != 2 or parts[1] != "802-11-wireless":
            continue

        profile_code, profile_out, _ = _run_nmcli(
            ["-g", "802-11-wireless.ssid", "connection", "show", parts[0]],
            timeout=5,
        )
        if profile_code == 0 and profile_out.strip():
            ssids.add(profile_out.strip())

    return ssids


def _normalize_nmcli_auth_error(message: str) -> str | None:
    lowered = message.lower()
    if "not authorized" not in lowered and "not authorised" not in lowered:
        return None
    if "control networking" in lowered or "control network" in lowered:
        return (
            "NetworkManager denied the request — run "
            "sudo ./install.sh (menu 6) and ensure the "
            "service user is in the netdev group"
        )
    return None


def _normalize_scan_warning(stderr: str | None) -> str | None:
    message = (stderr or "").strip()
    if not message:
        return None
    lowered = message.lower()
    auth_error = _normalize_nmcli_auth_error(message)
    if auth_error and ("scan" in lowered or "rescan" in lowered):
        return "Showing cached results — live rescan was not authorized"
    if "not authorized" in lowered or "not authorised" in lowered:
        return "Showing cached results — live rescan was not authorized"
    if "insufficient privileges" in lowered:
        return "Showing cached results — NetworkManager denied the scan request"
    return message


def _annotate_saved_profiles(networks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    saved = _saved_ssids()
    annotated: list[dict[str, Any]] = []

    for network in networks:
        row = dict(network)
        row["saved"] = row["ssid"] in saved
        if row["saved"] and row["security"] == "Open" and not row.get("in_use"):
            row["security"] = "Saved"
        annotated.append(row)

    return annotated


def _scan_wifi_networks(iface: str, *, rescan: bool) -> tuple[list[dict[str, Any]], str | None]:
    scan_error = None
    if rescan:
        code, _, stderr = _run_nmcli(["device", "wifi", "rescan", "ifname", iface], timeout=15)
        if code != 0:
            scan_error = _normalize_scan_warning(stderr)

    code, stdout, stderr = _run_nmcli(
        ["-g", "IN-USE,SSID,SIGNAL,SECURITY,CHAN", "device", "wifi", "list", "ifname", iface],
        timeout=20,
    )
    if code != 0:
        message = stderr or "Wi-Fi scan failed"
        return [], message

    networks = _annotate_saved_profiles(_parse_wifi_rows(stdout))
    if not networks and scan_error:
        return [], scan_error
    return networks, scan_error


def get_wifi_status() -> dict[str, Any]:
    if not _nmcli_available():
        return dict(_UNAVAILABLE)

    iface = _wifi_iface()
    if not iface:
        status = {
            "available": False,
            "iface": None,
            "state": "unavailable",
            "connected": False,
            "ssid": None,
            "signal": None,
            "security": None,
            "ip": None,
            "networks": [],
            "error": "No Wi-Fi interface found",
            "source": "live",
        }
        status["uplink"] = _build_uplink(status)
        return status

    state = _device_state(iface)
    networks, scan_error = _scan_wifi_networks(iface, rescan=False)
    active = next((network for network in networks if network.get("in_use")), None)

    status = {
        "available": True,
        "iface": iface,
        "state": state,
        "connected": state == "connected" and active is not None,
        "ssid": active["ssid"] if active else None,
        "signal": active["signal"] if active else None,
        "security": active["security"] if active else None,
        "ip": _device_ip(iface) if state == "connected" else None,
        "networks": networks,
        "scan_warning": scan_error,
        "source": "live",
    }
    status["uplink"] = _build_uplink(status)
    return status


def scan_wifi_networks() -> dict[str, Any]:
    status = get_wifi_status()
    if not status.get("available"):
        return {
            "ok": False,
            "networks": status.get("networks", []),
            "error": status.get("error", "Wi-Fi unavailable"),
            "source": status.get("source", "live"),
        }

    iface = status.get("iface")
    if not iface:
        return {
            "ok": False,
            "networks": [],
            "error": "No Wi-Fi interface found",
            "source": status.get("source", "live"),
        }

    networks, scan_error = _scan_wifi_networks(iface, rescan=True)
    return {
        "ok": bool(networks),
        "networks": networks,
        "warning": scan_error,
        "error": None if networks else (scan_error or "No networks found"),
        "source": "live",
    }


def connect_wifi(ssid: str, password: str | None = None) -> dict[str, Any]:
    ssid = ssid.strip()
    if not ssid:
        return {"ok": False, "error": "Network name is required", "status": get_wifi_status()}

    if not _nmcli_available():
        return {
            "ok": False,
            "error": _UNAVAILABLE["error"],
            "status": dict(_UNAVAILABLE),
        }

    iface = _wifi_iface()
    if not iface:
        return {"ok": False, "error": "No Wi-Fi interface found", "status": get_wifi_status()}

    args = ["device", "wifi", "connect", ssid, "ifname", iface]
    if password:
        args.extend(["password", password])

    code, stdout, stderr = _run_nmcli(args, timeout=45)
    if code != 0:
        message = stderr or stdout or "Connection failed"
        auth_error = _normalize_nmcli_auth_error(message)
        return {
            "ok": False,
            "error": auth_error or message,
            "status": get_wifi_status(),
        }

    status = get_wifi_status()
    return {
        "ok": True,
        "message": stdout or f"Connected to {ssid}",
        "status": status,
    }


def disconnect_wifi() -> dict[str, Any]:
    if not _nmcli_available():
        return {
            "ok": False,
            "error": _UNAVAILABLE["error"],
            "status": dict(_UNAVAILABLE),
        }

    iface = _wifi_iface()
    if not iface:
        return {"ok": False, "error": "No Wi-Fi interface found", "status": get_wifi_status()}

    status_before = get_wifi_status()
    if not status_before.get("connected"):
        return {
            "ok": False,
            "error": "Not connected to a Wi-Fi network",
            "status": status_before,
        }

    ssid = status_before.get("ssid")
    code, stdout, stderr = _run_nmcli(["device", "disconnect", iface], timeout=20)
    if code != 0:
        message = stderr or stdout or "Disconnect failed"
        auth_error = _normalize_nmcli_auth_error(message)
        return {
            "ok": False,
            "error": auth_error or message,
            "status": get_wifi_status(),
        }

    status = get_wifi_status()
    label = ssid or "network"
    return {
        "ok": True,
        "message": stdout or f"Disconnected from {label}",
        "status": status,
    }


def set_preferred_uplink(prefer: str) -> dict[str, Any]:
    """Set preferred WAN (Wi‑Fi or USB tether) and apply route metrics when possible.

    The preference is always saved. Metrics are applied to whichever of the two
    paths is currently up — if the preferred path is offline, traffic stays on
    the available one until the preferred path appears.
    """
    prefer = (prefer or "").strip().lower()
    if prefer not in {"wifi", "usb"}:
        return {
            "ok": False,
            "error": "prefer must be 'wifi' or 'usb'",
            "status": get_wifi_status(),
        }

    _save_preferred_uplink(prefer)
    prefer_name = "USB Hotspot" if prefer == "usb" else "Wi‑Fi"

    if not _nmcli_available():
        return {
            "ok": True,
            "message": (
                f"Preferred internet set to {prefer_name} "
                f"(NetworkManager unavailable)"
            ),
            "status": dict(_UNAVAILABLE),
        }

    status = get_wifi_status()
    # Re-probe paths even if choice was previously filtered.
    wifi_status = status
    wifi_path = None
    if wifi_status.get("available") and wifi_status.get("iface"):
        wifi_path = _path_info(
            "wifi",
            str(wifi_status["iface"]),
            ssid=wifi_status.get("ssid"),
        )
        if wifi_path is not None:
            wifi_path["connected"] = bool(wifi_status.get("connected"))
            wifi_path["ssid"] = wifi_status.get("ssid")
    usb_path = _find_usb_tether()

    wifi_ok = bool(wifi_path and wifi_path.get("connected") and wifi_path.get("connection"))
    usb_ok = bool(usb_path and usb_path.get("connected") and usb_path.get("connection"))

    if prefer == "usb":
        other_label = (wifi_path or {}).get("ssid") or "Wi‑Fi"
        pref_path = usb_path if usb_ok else None
        other_path = wifi_path if wifi_ok else None
    else:
        other_label = "USB Hotspot"
        pref_path = wifi_path if wifi_ok else None
        other_path = usb_path if usb_ok else None

    applied_any = False
    errors: list[str] = []

    if pref_path:
        ok, err = _set_connection_metric(str(pref_path["connection"]), _METRIC_PREFERRED)
        if not ok:
            errors.append(err or "preferred metric failed")
        else:
            _reapply_connection(str(pref_path["iface"]), str(pref_path["connection"]))
            applied_any = True

    if other_path:
        ok, err = _set_connection_metric(str(other_path["connection"]), _METRIC_FALLBACK)
        if not ok:
            errors.append(err or "fallback metric failed")
        else:
            _reapply_connection(str(other_path["iface"]), str(other_path["connection"]))
            applied_any = True

    status = get_wifi_status()
    active = (status.get("uplink") or {}).get("active")

    if not wifi_ok and not usb_ok:
        return {
            "ok": True,
            "message": f"Preferred internet set to {prefer_name} (no uplink online yet)",
            "status": status,
        }

    if not pref_path:
        return {
            "ok": True,
            "message": (
                f"Preferred internet set to {prefer_name}; currently using {other_label} "
                f"until {prefer_name} is available"
            ),
            "status": status,
        }

    if errors and not applied_any:
        return {
            "ok": False,
            "error": errors[0],
            "status": status,
        }

    if active != prefer:
        return {
            "ok": True,
            "message": f"Preferred internet set to {prefer_name} (route updating…)",
            "status": status,
        }

    return {
        "ok": True,
        "message": f"Internet is now coming from {prefer_name}",
        "status": status,
    }