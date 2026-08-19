"""Boot uptime and wall-clock trust helpers for logging and phase automation."""

from __future__ import annotations

import logging
import subprocess
import time

logger = logging.getLogger("sccs")

_sync_cache_at: float = 0.0
_sync_cache_val: bool | None = None
_SYNC_CACHE_TTL_S = 2.0


def read_uptime_seconds() -> float:
    """Seconds since kernel boot (monotonic, not affected by NTP jumps)."""
    try:
        with open("/proc/uptime", encoding="ascii") as handle:
            return float(handle.read().split()[0])
    except OSError:
        return 0.0


def format_uptime(seconds: float) -> str:
    total = max(0, int(seconds))
    if total < 60:
        return f"+{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"+{minutes}m{secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"+{hours}h{minutes:02d}m"


def _read_clock_synchronized() -> bool | None:
    try:
        result = subprocess.run(
            ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip().lower() == "yes"


def is_clock_synchronized() -> bool | None:
    """Return True/False when timedatectl is available, else None."""
    global _sync_cache_at, _sync_cache_val
    now = time.monotonic()
    if now - _sync_cache_at < _SYNC_CACHE_TTL_S:
        return _sync_cache_val
    _sync_cache_val = _read_clock_synchronized()
    _sync_cache_at = now
    return _sync_cache_val


def clear_sync_cache() -> None:
    global _sync_cache_at, _sync_cache_val
    _sync_cache_at = 0.0
    _sync_cache_val = None


def wait_for_clock_sync(timeout_s: float, poll_s: float = 2.0) -> bool:
    deadline = time.monotonic() + max(0.0, timeout_s)
    while time.monotonic() < deadline:
        clear_sync_cache()
        if is_clock_synchronized():
            return True
        time.sleep(max(0.2, poll_s))
    clear_sync_cache()
    return bool(is_clock_synchronized())


def log_clock_status(app_logger: logging.Logger) -> None:
    uptime = format_uptime(read_uptime_seconds())
    synced = is_clock_synchronized()
    if synced is True:
        app_logger.info(f"🕐 System clock synchronized ({uptime} since boot)")
    elif synced is False:
        app_logger.warning(
            f"🕐 System clock not synchronized ({uptime} since boot) — wall time may be wrong"
        )
    else:
        app_logger.warning(
            f"🕐 Clock sync status unknown ({uptime} since boot) — wall time may be wrong"
        )


def ensure_clock_for_automation(app_logger: logging.Logger, config) -> bool:
    """Wait for NTP before phase/light automation when configured."""
    timeout_s = config.getint("logging", "clock_sync_timeout_s", fallback=120)
    if timeout_s <= 0:
        return is_clock_synchronized() is not False

    if is_clock_synchronized():
        return True

    app_logger.warning(
        f"🕐 Waiting up to {timeout_s}s for NTP before phase automation..."
    )
    if wait_for_clock_sync(timeout_s):
        uptime = format_uptime(read_uptime_seconds())
        app_logger.info(f"🕐 NTP synchronized ({uptime} since boot) — wall time now trustworthy")
        return True

    app_logger.warning(
        f"🕐 NTP not available after {timeout_s}s — continuing with possibly wrong wall time"
    )
    return False