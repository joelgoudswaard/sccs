"""Module connectivity, host stats, and SCCS core information."""

from __future__ import annotations

import importlib.metadata
import platform
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from modules.version import get_version

SCCS_VERSION = get_version()

_runtime = None
_gps_module = None
_victron_module = None


def set_runtime(runtime) -> None:
    """Called by app.py after SCCSRuntime is created."""
    global _runtime
    _runtime = runtime


def set_gps_module(gps_module) -> None:
    global _gps_module
    _gps_module = gps_module


def set_victron_module(victron_module) -> None:
    global _victron_module
    _victron_module = victron_module


def _esp32_connected() -> bool:
    if _runtime is None:
        return False
    try:
        return bool(_runtime.esp32.is_connected())
    except Exception:
        return False


def _gps_connected() -> bool:
    if _gps_module is None:
        return False
    if _gps_module.state.get("force_no_hardware"):
        return False
    serial = getattr(_gps_module, "serial", None)
    return bool(serial and getattr(serial, "is_open", False))


def _victron_device_connected(role: str) -> bool:
    if _victron_module is None:
        return False
    try:
        state = _victron_module.get_state()
    except Exception:
        return False
    device = state.get(role) or {}
    return bool(device.get("configured")) and device.get("connected") and not device.get("stale")


def _victron_shunt_connected() -> bool:
    if _victron_module is None or not getattr(_victron_module, "shunt_address", None):
        return False
    return _victron_device_connected("shunt")


def _victron_mppt_connected() -> bool:
    if _victron_module is None or not getattr(_victron_module, "mppt_address", None):
        return False
    return _victron_device_connected("mppt")


_MODULES: list[dict[str, Any]] = [
    {"id": "mppt", "name": "Solar", "connected": False},
    {"id": "shunt", "name": "Shunt", "connected": False},
    {"id": "esp32", "name": "Lighting", "connected": False},
    {"id": "gps", "name": "GPS", "connected": False},
]

_DEMO_CORE: dict[str, Any] = {
    "hostname": "singularity",
    # Generic fallback when host probe is incomplete (not a Pi claim).
    "model": "SCCS Core",
    "os": "Linux",
    "kernel": platform.release(),
    "cpu_model": platform.processor() or platform.machine() or "CPU",
    "cpu_cores": 4,
    "cpu_threads": 4,
    "load_avg": "0.18 0.14 0.11",
    "memory_total_mb": 8192,
    "memory_used_mb": 3441,
    "memory_percent": 42.0,
    "disk_total_gb": 58.2,
    "disk_used_gb": 19.4,
    "disk_percent": 33.3,
    "throttling_status": "Normal",
    "throttling_raw": "0x0",
    "throttling_ok": True,
    "primary_ip": "192.168.0.100",
    "primary_iface": "wlan0",
    "python_version": platform.python_version(),
    "flask_version": "3.1.0",
    "app_version": SCCS_VERSION,
    "source": "demo",
}

_DEMO_HOST: dict[str, Any] = {
    "core_temp_c": 45.2,
    "uptime_s": 172_800,
    "cpu_percent": 18.5,
    "memory_percent": 42.0,
    "source": "demo",
}

_prev_cpu: tuple[int, int] | None = None


def _read_core_temp_c() -> float | None:
    for path in Path("/sys/class/thermal").glob("thermal_zone*/temp"):
        try:
            raw = path.read_text(encoding="ascii").strip()
            return round(int(raw) / 1000, 1)
        except (OSError, ValueError):
            continue
    return None


def _read_uptime_s() -> float | None:
    try:
        return float(Path("/proc/uptime").read_text(encoding="ascii").split()[0])
    except (OSError, IndexError, ValueError):
        return None


def _format_uptime_human(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    total = max(0, int(seconds))
    days = total // 86400
    hours = (total % 86400) // 3600
    minutes = (total % 3600) // 60
    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _read_cpu_percent() -> float | None:
    global _prev_cpu

    try:
        line = Path("/proc/stat").read_text(encoding="ascii").splitlines()[0]
        parts = line.split()
        if len(parts) < 5 or parts[0] != "cpu":
            return None
        values = [int(part) for part in parts[1:8]]
    except (OSError, ValueError, IndexError):
        return None

    idle = values[3] + values[4]
    total = sum(values)
    if _prev_cpu is None:
        _prev_cpu = (idle, total)
        return None

    prev_idle, prev_total = _prev_cpu
    _prev_cpu = (idle, total)
    delta_total = total - prev_total
    delta_idle = idle - prev_idle
    if delta_total <= 0:
        return 0.0

    return round(100.0 * (1.0 - delta_idle / delta_total), 1)


def _read_memory() -> dict[str, float | int] | None:
    try:
        lines = Path("/proc/meminfo").read_text(encoding="ascii").splitlines()
    except OSError:
        return None

    info: dict[str, int] = {}
    for line in lines:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        parts = value.strip().split()
        if not parts:
            continue
        info[key] = int(parts[0])

    total = info.get("MemTotal")
    available = info.get("MemAvailable")
    if total is None or available is None or total <= 0:
        return None

    used = total - available
    return {
        "memory_total_mb": round(total / 1024),
        "memory_used_mb": round(used / 1024),
        "memory_percent": round(100.0 * used / total, 1),
    }


def _read_disk() -> dict[str, float] | None:
    try:
        usage = shutil.disk_usage("/")
    except OSError:
        return None

    total_gb = usage.total / (1024 ** 3)
    used_gb = usage.used / (1024 ** 3)
    if total_gb <= 0:
        return None

    return {
        "disk_total_gb": round(total_gb, 1),
        "disk_used_gb": round(used_gb, 1),
        "disk_percent": round(100.0 * usage.used / usage.total, 1),
    }


def _read_load_avg() -> str | None:
    try:
        parts = Path("/proc/loadavg").read_text(encoding="ascii").split()
        if len(parts) < 3:
            return None
        return " ".join(parts[:3])
    except (OSError, ValueError):
        return None


def _read_dmi_model() -> str | None:
    """x86 / mini-PC product name (e.g. GMKtec boxes)."""
    vendor = None
    product = None
    board = None
    for key, attr in (
        ("sys_vendor", "vendor"),
        ("product_name", "product"),
        ("board_name", "board"),
    ):
        try:
            raw = Path(f"/sys/devices/virtual/dmi/id/{key}").read_text(
                encoding="utf-8", errors="ignore"
            ).strip()
        except OSError:
            continue
        if not raw or raw.lower() in {
            "none",
            "default string",
            "to be filled by o.e.m.",
            "system product name",
        }:
            continue
        if attr == "vendor":
            vendor = raw
        elif attr == "product":
            product = raw
        else:
            board = raw

    if product and vendor and vendor.lower() not in product.lower():
        return f"{vendor} {product}"
    if product:
        return product
    if board and vendor:
        return f"{vendor} {board}"
    return board or vendor


def _read_model() -> str | None:
    dmi = _read_dmi_model()
    for path in ("/proc/device-tree/model", "/sys/firmware/devicetree/base/model"):
        try:
            raw = Path(path).read_bytes().decode("utf-8", errors="ignore").strip("\x00")
            if raw:
                if dmi and "raspberry pi" not in dmi.lower() and "raspberry pi" in raw.lower():
                    return dmi
                return raw
        except OSError:
            continue
    if dmi:
        return dmi
    machine = platform.machine()
    if machine:
        return f"Linux ({machine})"
    return None


def _read_cpu_topology() -> tuple[int | None, int | None]:
    try:
        text = Path("/proc/cpuinfo").read_text(encoding="ascii")
    except OSError:
        return None, None

    physical_ids = set()
    processor_ids = set()
    for line in text.splitlines():
        if line.startswith("processor"):
            processor_ids.add(line.split(":", 1)[1].strip())
        elif line.startswith("physical id"):
            physical_ids.add(line.split(":", 1)[1].strip())

    cores = len(physical_ids) or None
    threads = len(processor_ids) or None
    return cores, threads


def _read_cpu_model() -> str | None:
    try:
        text = Path("/proc/cpuinfo").read_text(encoding="ascii")
    except OSError:
        return None

    for line in text.splitlines():
        if line.lower().startswith("model name"):
            return line.split(":", 1)[1].strip()
        if "CPU part" in line:
            cpu_part = line.split(":", 1)[1].strip()
            if cpu_part == "0xd0b":
                return "Broadcom BCM2712 (4× Cortex-A76)"
            return f"ARM CPU (part 0x{cpu_part})"
    return None


def _read_primary_ip() -> tuple[str | None, str | None]:
    try:
        import socket

        hostname = socket.gethostname()
        ip = socket.gethostbyname(hostname)
        return ip, None
    except OSError:
        return None, None


def _read_throttling() -> tuple[str, str, bool]:
    try:
        result = subprocess.check_output(
            ["vcgencmd", "get_throttled"],
            stderr=subprocess.STDOUT,
            timeout=3,
            text=True,
        ).strip()
        raw = result.split("=", 1)[1].strip() if "=" in result else result
        ok = raw == "0x0"
        status = "Normal" if ok else "Throttled"
        return status, raw, ok
    except (OSError, subprocess.SubprocessError):
        return "Unavailable", "N/A", True


def _flask_version() -> str:
    try:
        return importlib.metadata.version("flask")
    except importlib.metadata.PackageNotFoundError:
        return "Unknown"


def get_host_stats() -> dict[str, Any]:
    core_temp_c = _read_core_temp_c()
    uptime_s = _read_uptime_s()
    cpu_percent = _read_cpu_percent()
    memory = _read_memory()
    memory_percent = memory["memory_percent"] if memory else None

    if all(value is not None for value in (core_temp_c, uptime_s, cpu_percent, memory_percent)):
        return {
            "core_temp_c": core_temp_c,
            "uptime_s": uptime_s,
            "uptime_human": _format_uptime_human(uptime_s),
            "cpu_percent": cpu_percent,
            "memory_percent": memory_percent,
            "source": "live",
        }

    host = dict(_DEMO_HOST)
    if core_temp_c is not None:
        host["core_temp_c"] = core_temp_c
    if uptime_s is not None:
        host["uptime_s"] = uptime_s
        host["uptime_human"] = _format_uptime_human(uptime_s)
    if cpu_percent is not None:
        host["cpu_percent"] = cpu_percent
    if memory_percent is not None:
        host["memory_percent"] = memory_percent
    host["uptime_human"] = host.get("uptime_human") or _format_uptime_human(host.get("uptime_s"))
    if any(value is not None for value in (core_temp_c, uptime_s, cpu_percent, memory_percent)):
        host["source"] = "partial"
    return host


def get_core_info() -> dict[str, Any]:
    host = get_host_stats()
    memory = _read_memory()
    disk = _read_disk()
    cores, threads = _read_cpu_topology()
    throttle_status, throttle_raw, throttle_ok = _read_throttling()
    primary_ip, _ = _read_primary_ip()
    uptime_s = host.get("uptime_s")
    model = _read_model()

    live_fields = [
        model,
        memory,
        disk,
        cores,
        _read_load_avg(),
        primary_ip,
    ]
    has_live = any(value is not None for value in live_fields)

    core = dict(_DEMO_CORE)
    core["hostname"] = platform.node() or core["hostname"]
    core["os"] = f"{platform.system()} {platform.release()}"
    core["kernel"] = platform.release()
    core["python_version"] = platform.python_version()
    core["flask_version"] = _flask_version()
    core["app_version"] = SCCS_VERSION
    core["uptime_human"] = host.get("uptime_human")
    core["uptime_s"] = uptime_s
    core["core_temp_c"] = host.get("core_temp_c")
    core["cpu_percent"] = host.get("cpu_percent")
    core["memory_percent"] = host.get("memory_percent") or core.get("memory_percent")
    core["uptime_human"] = host.get("uptime_human") or core.get("uptime_human")
    core["uptime_s"] = host.get("uptime_s") or core.get("uptime_s")
    core["throttling_status"] = throttle_status
    core["throttling_raw"] = throttle_raw
    core["throttling_ok"] = throttle_ok

    if model:
        core["model"] = model
    if cores:
        core["cpu_cores"] = cores
    if threads:
        core["cpu_threads"] = threads

    cpu_model = _read_cpu_model()
    if cpu_model:
        core["cpu_model"] = cpu_model

    load_avg = _read_load_avg()
    if load_avg:
        core["load_avg"] = load_avg

    if memory:
        core.update(memory)
    elif host.get("memory_percent") is not None:
        core["memory_percent"] = host["memory_percent"]

    if disk:
        core.update(disk)

    if primary_ip:
        core["primary_ip"] = primary_ip

    if uptime_s is not None:
        boot_time = datetime.now().timestamp() - float(uptime_s)
        core["boot_time"] = datetime.fromtimestamp(boot_time).strftime("%Y-%m-%d %H:%M")

    core["source"] = "live" if has_live else ("partial" if host.get("source") == "partial" else core["source"])
    return core


def get_system_status() -> dict[str, Any]:
    modules = [dict(module) for module in _MODULES]
    probes = {
        "esp32": _esp32_connected,
        "gps": _gps_connected,
        "shunt": _victron_shunt_connected,
        "mppt": _victron_mppt_connected,
    }
    for module in modules:
        probe = probes.get(module["id"])
        if probe:
            module["connected"] = probe()
    online = sum(1 for module in modules if module.get("connected"))
    host = get_host_stats()
    core = get_core_info()

    return {
        "modules": modules,
        "online_count": online,
        "total_count": len(modules),
        "host": host,
        "core": core,
        "source": core.get("source", "demo"),
    }