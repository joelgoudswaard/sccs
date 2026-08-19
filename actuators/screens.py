from __future__ import annotations

import logging
import re
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional, Union

logger = logging.getLogger("sccs")

_SCREEN_KNOWN_HOSTS = Path.home() / ".sccs" / "screen_known_hosts"

OnCommandFailed = Optional[Callable[[str], None]]


@dataclass(frozen=True)
class SysfsControl:
    path: str


@dataclass(frozen=True)
class DbusScreenSaverControl:
    service: str
    object_path: str


@dataclass(frozen=True)
class DbusKdeBrightnessControl:
    service: str
    object_path: str
    interface: str = "org.kde.ScreenBrightness.Display"


ScreenControl = Union[SysfsControl, DbusScreenSaverControl, DbusKdeBrightnessControl]

DEFAULT_KDE_BLANK_PATH = "/sys/class/graphics/fb0/blank"


def _ssh_options(timeout: int = 5) -> str:
    _SCREEN_KNOWN_HOSTS.parent.mkdir(parents=True, exist_ok=True)
    identity = Path.home() / ".ssh" / "sccs_screen"
    id_opt = f"-i {shlex.quote(str(identity))} -o IdentitiesOnly=yes " if identity.is_file() else ""
    return (
        f"-o BatchMode=yes -o ConnectTimeout={timeout} "
        f"-o StrictHostKeyChecking=accept-new "
        f"-o UserKnownHostsFile={_SCREEN_KNOWN_HOSTS} "
        f"{id_opt}"
        f"-o PreferredAuthentications=publickey"
    )


def _ssh_cmd(username: str, host: str, remote: str, timeout: int) -> str:
    return (
        f"ssh {_ssh_options(timeout)} "
        f"{shlex.quote(username)}@{shlex.quote(host)} {shlex.quote(remote)}"
    )


def _parse_control(path: str) -> Optional[ScreenControl]:
    if not path:
        return None
    if path.startswith("dbus:"):
        spec = path[5:]
        if ":" in spec:
            service, object_path = spec.split(":", 1)
        else:
            service = spec
            object_path = "/" + service.replace(".", "/")
        if not service or not object_path.startswith("/"):
            return None
        if service == "org.kde.ScreenBrightness":
            return DbusKdeBrightnessControl(service=service, object_path=object_path)
        return DbusScreenSaverControl(service=service, object_path=object_path)
    # wlr:HDMI-A-1 — labwc / wlroots compositor output power
    if path.startswith("wlr:"):
        return SysfsControl(path=path)
    return SysfsControl(path=path)


def _is_blank_path(path: str) -> bool:
    return "blank" in path or "/graphics/fb" in path or path.startswith("wlr:")


def _is_kscreen_blank(path: str) -> bool:
    return path.startswith("kscreen:")


def _is_wlr_path(path: str) -> bool:
    return path.startswith("wlr:")


def brightness_is_adjustable(path: str) -> bool:
    """True when brightness_path supports continuous 0–100 levels (not just on/off)."""
    if not path:
        return False
    if path.startswith("wlr:") or path.startswith("kscreen:"):
        return False
    if path.startswith("dbus:"):
        # KDE ScreenBrightness is continuous; ScreenSaver is binary
        return "org.kde.ScreenBrightness" in path
    # sysfs blank / graphics fb = binary; other sysfs (backlight) = continuous
    if _is_blank_path(path):
        return False
    return path.startswith("/")


def _kscreen_output_name(path: str) -> str:
    return path.split(":", 1)[1]


def _wlr_output_name(path: str) -> str:
    return path.split(":", 1)[1]


def _wlr_env_prefix() -> str:
    return (
        "export XDG_RUNTIME_DIR=/run/user/$(id -u); "
        "export WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-wayland-0}; "
    )


def _wlr_set_cmd(output: str, brightness_pct: int) -> str:
    """Power output on/off via wlr-randr (labwc / Raspberry Pi OS Wayland)."""
    if not re.fullmatch(r"[\w.-]+", output):
        raise ValueError(f"invalid wlr output: {output!r}")
    env = _wlr_env_prefix()
    pct = max(0, min(100, int(brightness_pct)))
    action = "--off" if pct <= 0 else "--on"
    return f"{env}wlr-randr --output {shlex.quote(output)} {action}"


def _wlr_get_cmd(output: str) -> str:
    """Print Enabled yes/no for the named output."""
    if not re.fullmatch(r"[\w.-]+", output):
        raise ValueError(f"invalid wlr output: {output!r}")
    env = _wlr_env_prefix()
    # wlr-randr prints a block per output; grab Enabled for the matching name
    return (
        f"{env}wlr-randr 2>/dev/null | awk -v o={shlex.quote(output)} "
        f"'/^[^ ]/ {{cur=$1}} cur==o && /Enabled:/ {{print $2; exit}}'"
    )


def _kscreen_env_prefix() -> str:
    return (
        "export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus; "
        "export WAYLAND_DISPLAY=wayland-0; "
        "export XDG_RUNTIME_DIR=/run/user/$(id -u); "
    )


def _kscreen_set_cmd(
    output: str,
    brightness_pct: int,
    control: Optional[DbusKdeBrightnessControl] = None,
) -> str:
    if not re.fullmatch(r"[\w.-]+", output):
        raise ValueError(f"invalid kscreen output: {output!r}")
    env = _kscreen_env_prefix()
    pct = max(0, min(100, int(brightness_pct)))
    if pct <= 0:
        # Do not use ScreenSaver SetActive — on KDE Wayland it cannot be cleared
        # remotely and leaves the panel stuck black after wake.
        return f"{env}kscreen-doctor --dpms off output.{output}.brightness.0"
    parts = [f"{env}kscreen-doctor --dpms on"]
    if control:
        parts.append(_dbus_kde_set_brightness_cmd(control, pct))
    parts.append(f"{env}kscreen-doctor output.{output}.brightness.{pct}")
    parts.append(
        f"{env}busctl --user call org.freedesktop.ScreenSaver /org/freedesktop/ScreenSaver "
        f"org.freedesktop.ScreenSaver SimulateUserActivity"
    )
    return "; ".join(parts)


def _blank_awake_value(awake: bool) -> str:
    return "0" if awake else "1"


def _blank_is_awake(value: int) -> bool:
    """fb0/blank: 0 = awake; 1 or 4 = blanked (see config/sccs.conf [screens] notes)."""
    return value == 0


def _dbus_env_prefix() -> str:
    return "export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus;"


def _dbus_set_active_cmd(control: DbusScreenSaverControl, awake: bool) -> str:
    active = "false" if awake else "true"
    return (
        f"{_dbus_env_prefix()} "
        f"busctl --user call {shlex.quote(control.service)} "
        f"{shlex.quote(control.object_path)} "
        f"org.freedesktop.ScreenSaver SetActive b {active}"
    )


def _dbus_get_active_cmd(control: DbusScreenSaverControl) -> str:
    return (
        f"{_dbus_env_prefix()} "
        f"busctl --user call {shlex.quote(control.service)} "
        f"{shlex.quote(control.object_path)} "
        f"org.freedesktop.ScreenSaver GetActive"
    )


def _dbus_kde_set_brightness_cmd(control: DbusKdeBrightnessControl, brightness_pct: int) -> str:
    svc = shlex.quote(control.service)
    obj = shlex.quote(control.object_path)
    iface = shlex.quote(control.interface)
    pct = max(0, min(100, int(brightness_pct)))
    if pct <= 0:
        return (
            f"{_dbus_env_prefix()} "
            f"busctl --user call {svc} {obj} {iface} SetBrightness iu 0 0"
        )
    return (
        f"{_dbus_env_prefix()} "
        f"max=$(busctl --user get-property {svc} {obj} {iface} MaxBrightness "
        f"| awk '{{print $2}}'); "
        f"lvl=$(( max * {pct} / 100 )); "
        f"busctl --user call {svc} {obj} {iface} SetBrightness iu $lvl 0"
    )


def _dbus_kde_get_brightness_cmd(control: DbusKdeBrightnessControl) -> str:
    return (
        f"{_dbus_env_prefix()} "
        f"busctl --user get-property {shlex.quote(control.service)} "
        f"{shlex.quote(control.object_path)} "
        f"{shlex.quote(control.interface)} Brightness"
    )


def _parse_busctl_bool(output: str) -> Optional[bool]:
    match = re.search(r"\bb\s+(true|false)\b", output or "", re.IGNORECASE)
    if not match:
        return None
    return match.group(1).lower() == "true"


def _parse_busctl_int(output: str) -> Optional[int]:
    match = re.search(r"\bi\s+(-?\d+)\b", output or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _sysfs_brightness_value(control: SysfsControl, brightness_pct: int) -> str:
    pct = max(0, min(100, int(brightness_pct)))
    if _is_blank_path(control.path):
        return _blank_awake_value(pct > 0)
    if pct <= 0:
        return "0"
    return str(int(255 * pct / 100))


def _sysfs_write_cmd(path: str, value: str) -> str:
    path_q = shlex.quote(path)
    value_q = shlex.quote(value)
    return (
        f"if echo {value_q} > {path_q} 2>/dev/null; then exit 0; fi; "
        f"echo {value_q} | sudo -n tee {path_q} >/dev/null"
    )


def _effective_blank_path(conf: dict, control: ScreenControl) -> Optional[str]:
    blank_path = conf.get("blank_path")
    if blank_path in ("", "none", "-"):
        return None
    if blank_path:
        return blank_path
    if isinstance(control, DbusKdeBrightnessControl):
        return DEFAULT_KDE_BLANK_PATH
    if isinstance(control, SysfsControl) and not _is_blank_path(control.path):
        return DEFAULT_KDE_BLANK_PATH
    return None


def _compose_brightness_remote(control: ScreenControl, brightness_pct: int) -> str:
    pct = max(0, min(100, int(brightness_pct)))
    if isinstance(control, DbusKdeBrightnessControl):
        return _dbus_kde_set_brightness_cmd(control, pct)
    if isinstance(control, DbusScreenSaverControl):
        return _dbus_set_active_cmd(control, pct > 0)
    path = getattr(control, "path", "") or ""
    if _is_wlr_path(path):
        return _wlr_set_cmd(_wlr_output_name(path), pct)
    return _sysfs_write_cmd(control.path, _sysfs_brightness_value(control, pct))


def _compose_screen_remote(
    control: ScreenControl,
    brightness_pct: int,
    blank_path: Optional[str],
) -> str:
    pct = max(0, min(100, int(brightness_pct)))
    if blank_path and _is_kscreen_blank(blank_path):
        kde_control = control if isinstance(control, DbusKdeBrightnessControl) else None
        return _kscreen_set_cmd(_kscreen_output_name(blank_path), pct, kde_control)
    # Prefer wlr blank path when brightness is not already wlr
    if blank_path and _is_wlr_path(blank_path):
        control_path = getattr(control, "path", "") or ""
        if _is_wlr_path(control_path):
            return _wlr_set_cmd(_wlr_output_name(control_path), pct)
        return _wlr_set_cmd(_wlr_output_name(blank_path), pct)
    brightness_remote = _compose_brightness_remote(control, pct)
    if not blank_path or _is_blank_path(getattr(control, "path", "")):
        return brightness_remote
    if pct <= 0:
        return f"{brightness_remote}; {_sysfs_write_cmd(blank_path, '4')}"
    return f"{_sysfs_write_cmd(blank_path, '0')} && {brightness_remote}"


def _remote_set_cmd(
    username: str,
    host: str,
    control: ScreenControl,
    brightness_pct: int,
    timeout: int,
    blank_path: Optional[str] = None,
) -> str:
    remote = _compose_screen_remote(control, brightness_pct, blank_path)
    return _ssh_cmd(username, host, remote, timeout)


def _remote_read_cmd(username: str, host: str, control: ScreenControl, timeout: int) -> str:
    if isinstance(control, DbusKdeBrightnessControl):
        remote = _dbus_kde_get_brightness_cmd(control)
    elif isinstance(control, DbusScreenSaverControl):
        remote = _dbus_get_active_cmd(control)
    elif isinstance(control, SysfsControl) and _is_wlr_path(control.path):
        remote = _wlr_get_cmd(_wlr_output_name(control.path))
    else:
        path_q = shlex.quote(control.path)
        remote = f"cat {path_q} 2>/dev/null"
    return _ssh_cmd(username, host, remote, timeout)


def _brightness_pct_from_raw(
    control: ScreenControl,
    raw_value: int,
    max_value: Optional[int] = None,
) -> int:
    if isinstance(control, SysfsControl) and _is_blank_path(control.path):
        return 100 if _blank_is_awake(raw_value) else 0
    if isinstance(control, DbusScreenSaverControl):
        return 0 if raw_value else 100
    if raw_value <= 0:
        return 0
    if isinstance(control, DbusKdeBrightnessControl):
        if max_value and max_value > 0:
            return max(0, min(100, int(round(raw_value * 100 / max_value))))
        return 100
    return max(0, min(100, int(round(raw_value * 100 / 255))))


def _blanked_from_read(blank_output: str) -> Optional[bool]:
    if not blank_output:
        return None
    try:
        val = int((blank_output or "").strip())
    except ValueError:
        return None
    return not _blank_is_awake(val)


def _state_from_read(
    control: ScreenControl,
    raw_output: str,
    max_output: str = "",
    blank_output: str = "",
) -> tuple[Optional[bool], Optional[int], Optional[int]]:
    blanked = _blanked_from_read(blank_output)
    if blanked is True:
        return False, 0, 0

    if isinstance(control, DbusKdeBrightnessControl):
        brightness = _parse_busctl_int(raw_output)
        if brightness is None:
            return None, None, None
        max_brightness = _parse_busctl_int(max_output)
        pct = _brightness_pct_from_raw(control, brightness, max_brightness)
        return (pct > 0, brightness, pct)

    if isinstance(control, DbusScreenSaverControl):
        active = _parse_busctl_bool(raw_output)
        if active is None:
            return None, None, None
        pct = 0 if active else 100
        return (not active, 1 if active else 0, pct)

    # wlr-randr Enabled: yes/no
    if isinstance(control, SysfsControl) and _is_wlr_path(control.path):
        token = (raw_output or "").strip().split()[0].lower() if (raw_output or "").strip() else ""
        if token in ("yes", "true", "on", "enabled", "1"):
            return True, 1, 100
        if token in ("no", "false", "off", "disabled", "0"):
            return False, 0, 0
        return None, None, None

    try:
        val = int((raw_output or "").strip())
    except ValueError:
        return None, None, None
    is_blank = _is_blank_path(control.path)
    on = _blank_is_awake(val) if is_blank else (val > 0)
    pct = _brightness_pct_from_raw(control, val)
    return on, val, pct


class ScreenActuator:
    def __init__(self, screens: dict, compiled, on_command_failed: OnCommandFailed = None):
        self._screens = screens
        self._observed: Dict[str, int] = {n: 0 for n in screens}
        self._on_command_failed = on_command_failed
        if screens:
            threading.Thread(target=self._probe_all, daemon=True, name="screen-probe").start()

    def set_on_command_failed(self, callback: OnCommandFailed):
        self._on_command_failed = callback

    def set_screen(self, name: str, brightness_pct: int):
        brightness_pct = max(0, min(100, int(brightness_pct)))
        self._observed[name] = brightness_pct
        threading.Thread(
            target=self._apply,
            args=(name, brightness_pct),
            daemon=True,
        ).start()

    def _apply(self, name: str, brightness_pct: int):
        conf = self._screens.get(name)
        if not conf:
            return
        control = _parse_control(conf.get("brightness_path", ""))
        if not control:
            return
        cmd = _remote_set_cmd(
            conf["username"],
            conf["host"],
            control,
            brightness_pct,
            timeout=8,
            blank_path=_effective_blank_path(conf, control),
        )
        try:
            result = subprocess.run(cmd, shell=True, timeout=10, capture_output=True, text=True)
            if result.returncode == 0:
                self._observed[name] = brightness_pct
                if brightness_pct <= 0:
                    logger.info(f"🖥️ Slept screen: {conf.get('friendly', name)}")
                else:
                    logger.info(
                        f"🖥️ Set screen {conf.get('friendly', name)} to {brightness_pct}%"
                    )
            else:
                err = (result.stderr or result.stdout or "").strip()
                logger.warning(
                    "Screen %s SSH failed (exit %s): %s",
                    name,
                    result.returncode,
                    err[:200] or "no output",
                )
                if self._on_command_failed:
                    self._on_command_failed(name)
        except Exception as e:
            logger.warning(f"Screen {name} SSH: {e}")
            if self._on_command_failed:
                self._on_command_failed(name)

    def _probe_all(self):
        for name in self._screens:
            try:
                self.test_connectivity(name)
            except Exception as e:
                logger.debug("Screen %s startup probe: %s", name, e)

    def read_screens(self) -> Dict[str, int]:
        return dict(self._observed)

    def test_connectivity(self, name: str, timeout: float = 3.0) -> dict:
        """Ported for diag REST endpoints."""
        conf = self._screens.get(name)
        if not conf:
            return {"online": False, "error": "No config"}
        import socket
        from datetime import datetime

        result = {
            "online": False,
            "latency": None,
            "ssh_passwordless": False,
            "last_checked": datetime.now().isoformat(),
            "brightness": None,
            "brightness_pct": None,
            "on": None,
        }
        host = conf["host"]
        try:
            with socket.create_connection((host, 22), timeout=timeout):
                result["online"] = True
        except Exception:
            return result

        control = _parse_control(conf.get("brightness_path", ""))
        if not control:
            return result

        blank_path = _effective_blank_path(conf, control)
        cmd = _remote_read_cmd(conf["username"], host, control, int(timeout))
        max_output = ""
        blank_output = ""
        if blank_path:
            blank_cmd = _ssh_cmd(
                conf["username"],
                host,
                f"cat {shlex.quote(blank_path)} 2>/dev/null",
                int(timeout),
            )
            try:
                blank_proc = subprocess.run(
                    blank_cmd,
                    shell=True,
                    timeout=timeout + 2,
                    capture_output=True,
                    text=True,
                )
                if blank_proc.returncode == 0:
                    blank_output = blank_proc.stdout or ""
            except Exception:
                pass
        if isinstance(control, DbusKdeBrightnessControl):
            svc = shlex.quote(control.service)
            obj = shlex.quote(control.object_path)
            iface = shlex.quote(control.interface)
            max_cmd = _ssh_cmd(
                conf["username"],
                host,
                (
                    f"{_dbus_env_prefix()} "
                    f"busctl --user get-property {svc} {obj} {iface} MaxBrightness"
                ),
                int(timeout),
            )
            try:
                max_proc = subprocess.run(
                    max_cmd,
                    shell=True,
                    timeout=timeout + 2,
                    capture_output=True,
                    text=True,
                )
                if max_proc.returncode == 0:
                    max_output = max_proc.stdout or ""
            except Exception:
                pass
        try:
            proc = subprocess.run(cmd, shell=True, timeout=timeout + 2, capture_output=True, text=True)
            if proc.returncode == 0:
                on, brightness, brightness_pct = _state_from_read(
                    control,
                    proc.stdout or "",
                    max_output,
                    blank_output=blank_output,
                )
                if on is not None:
                    result["on"] = on
                    result["brightness"] = brightness
                    result["brightness_pct"] = brightness_pct
                    result["ssh_passwordless"] = True
                    if brightness_pct is not None:
                        self._observed[name] = brightness_pct
        except Exception as e:
            result["ssh_error"] = str(e)[:200]
        return result

    def manual_toggle(
        self,
        name: str,
        force_on: Optional[bool] = None,
        brightness_pct: Optional[int] = None,
    ):
        if brightness_pct is not None:
            self.set_screen(name, brightness_pct)
            return
        if force_on is None:
            force_on = self._observed.get(name, 0) <= 0
        if not force_on:
            self.set_screen(name, 0)
            return
        conf = self._screens.get(name) or {}
        levels = conf.get("phase_brightness") or {}
        fallback = max(levels.values(), default=100) if levels else 100
        self.set_screen(name, fallback)

    def shutdown_all(self, join_timeout: float = 15.0):
        """Power off every configured screen over SSH.

        Blocks until each attempt finishes (or *join_timeout* elapses) so the
        host can safely shut down afterward without killing in-flight SSH.
        """
        if not self._screens:
            return
        threads = []
        for name in self._screens:
            t = threading.Thread(
                target=self._shutdown_one,
                args=(name,),
                daemon=True,
                name=f"screen-shutdown-{name}",
            )
            t.start()
            threads.append(t)
        deadline = time.monotonic() + max(0.0, join_timeout)
        for t in threads:
            remaining = max(0.0, deadline - time.monotonic())
            t.join(timeout=remaining)
            if t.is_alive():
                logger.warning(
                    "Screen shutdown still running after %.0fs — continuing host shutdown",
                    join_timeout,
                )

    def _shutdown_one(self, name: str):
        conf = self._screens.get(name)
        if not conf:
            return
        label = conf.get("friendly", name)
        # nohup + background so SSH returns as soon as the command is queued;
        # a foreground `shutdown` often kills the session mid-handshake.
        remote = (
            "nohup sh -c '"
            "sudo -n shutdown -h now 2>/dev/null || "
            "sudo -n poweroff 2>/dev/null || "
            "shutdown -h now 2>/dev/null || poweroff"
            "' >/dev/null 2>&1 &"
        )
        cmd = _ssh_cmd(conf["username"], conf["host"], remote, 8)
        try:
            result = subprocess.run(cmd, shell=True, timeout=12, capture_output=True, text=True)
            if result.returncode == 0:
                logger.info(f"🖥️ Shutdown sent to screen: {label}")
            else:
                err = (result.stderr or result.stdout or "").strip()
                logger.warning(
                    "Screen %s shutdown SSH failed (exit %s): %s",
                    name,
                    result.returncode,
                    err[:200] or "no output",
                )
        except Exception as e:
            logger.warning(f"Screen {name} shutdown SSH: {e}")