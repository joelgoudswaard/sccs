"""SCCS — Flask + SocketIO bridge for lighting/relay control."""

from __future__ import annotations

import atexit
import logging
import os
import subprocess
import sys
import threading
from datetime import datetime
from urllib.parse import urlparse

from flask import Flask, Response, jsonify, render_template, request
from flask_cors import CORS
from flask_socketio import SocketIO, emit

from bridge.runtime import SCCSRuntime
from gps import get_gps_status, set_gps_module
from modules.config import config
from modules.gps import GPSModule
from modules.logger import setup_logging
from modules.phases import PhaseManager
from modules.sensors import SensorManager
from modules.toasts import ToastManager
from modules.ui_state import ConfigManager

import modules.toasts
from network import build_network_status
from sonos import get_sonos_status, set_muted, set_sonos_manager, set_transport, set_volume
from system import get_system_status, set_gps_module as set_system_gps, set_runtime, set_victron_module
from victron import get_power_status, set_victron_manager as set_power_victron
from weather import get_weather_status, set_gps_module as set_weather_gps
from wifi import (
    connect_wifi,
    disconnect_wifi,
    get_wifi_status,
    scan_wifi_networks,
    set_preferred_uplink,
)
from modules.homekit import HomeKitManager
from modules.matter import MatterManager

logger = setup_logging(config)

if config.getboolean("logging", "suppress_werkzeug", fallback=True):
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
if config.getboolean("logging", "suppress_engineio", fallback=True):
    logging.getLogger("engineio").setLevel(logging.WARNING)
if config.getboolean("logging", "suppress_socketio", fallback=True):
    logging.getLogger("socketio").setLevel(logging.WARNING)


def log_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logger.critical("Unhandled exception", exc_info=(exc_type, exc_value, exc_traceback))


sys.excepthook = log_exception


def _patch_engineio_werkzeug_websocket() -> None:
    """Engine.IO hijacks the socket for WebSocket upgrades without calling WSGI start_response.

    Werkzeug then raises AssertionError: write() before start_response when the
    handler returns. Signal a dropped connection instead (same idea as gunicorn's
    StopIteration path in engineio's SimpleWebSocketWSGI).
    """
    from engineio.async_drivers import _websocket_wsgi as wswsgi

    if getattr(wswsgi.SimpleWebSocketWSGI, "_sccs_werkzeug_patch", False):
        return

    _orig_call = wswsgi.SimpleWebSocketWSGI.__call__

    def __call__(self, environ, start_response):
        ret = _orig_call(self, environ, start_response)
        if getattr(getattr(self, "ws", None), "mode", None) == "werkzeug":
            raise ConnectionError("WebSocket connection handled outside WSGI")
        return ret

    wswsgi.SimpleWebSocketWSGI.__call__ = __call__
    wswsgi.SimpleWebSocketWSGI._sccs_werkzeug_patch = True


_patch_engineio_werkzeug_websocket()

app = Flask(__name__)
app.config["SECRET_KEY"] = config.get("system", "secret_key", fallback="sccs-secret")
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

debug_mode = config.getboolean("system", "debug", fallback=False)
if debug_mode:
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

# Settings-tab diagnostic tools (force phase/reed, GPS simulate, toast test).
development_mode = config.getboolean("system", "development_mode", fallback=False)

toast_manager = ToastManager(config, socketio)
modules.toasts.toast_manager = toast_manager

dark_mode_config = ConfigManager("active_dark_mode.json", {"mode": "dark", "manual": False})
_default_theme = config.get("ui", "default_theme", fallback="neuglass")
if _default_theme == "base":
    _default_theme = "neuglass"
theme_config = ConfigManager("active_theme.json", {"theme": _default_theme})
current_global_theme = theme_config.load()["theme"]

runtime = SCCSRuntime(config, socketio=socketio, dark_mode_config=dark_mode_config)
set_runtime(runtime)

gps_module = None
phase_manager = None
sensor_manager = None
victron_module = None
sonos_module = None
homekit_module = None
matter_module = None
first_state_read_done = False
shutdown_event = threading.Event()
_cleanup_done = False


def _theme_sort_key(item: dict) -> tuple:
    name = item["name"].lower()
    return (0 if name.endswith("morphism") else 1, name)


def _extract_css_friendly_name(filepath: str, fallback: str) -> str:
    try:
        with open(filepath, encoding="utf-8") as handle:
            first = handle.readline().strip()
            if first.startswith("/*") and first.endswith("*/"):
                comment = first[2:-2].strip()
                if comment:
                    return comment
    except OSError:
        pass
    return fallback


def _network_status_payload() -> dict:
    start_time = getattr(app, "_start_time", None)
    return build_network_status(start_time)


def network_status_broadcaster() -> None:
    while not shutdown_event.is_set():
        try:
            socketio.emit("network_update", _network_status_payload())
        except Exception:
            pass
        if shutdown_event.wait(8.5):
            break

NAV_ITEMS = [
    {"id": "home", "label": "Home", "icon": "fa-house"},
    {"id": "lighting", "label": "Lighting", "icon": "fa-lightbulb"},
    {"id": "scenes", "label": "Scenes", "icon": "fa-wand-magic-sparkles"},
    {"id": "system", "label": "System", "icon": "fa-gear"},
]


@app.context_processor
def inject_nav():
    return {
        "nav_items": NAV_ITEMS,
        "development_mode": development_mode,
    }


def _require_development_mode(action: str = "action") -> bool:
    """Gate diagnostic socket/HTTP tools when development_mode is off."""
    if development_mode:
        return True
    logger.warning("Ignored %s — [system] development_mode is false", action)
    return False


@app.route("/")
def index():
    return render_template("index.html", active_page="home")


@app.route("/api/power")
def api_power():
    return jsonify(get_power_status())


@app.route("/api/victron")
def api_victron():
    if victron_module:
        return jsonify(victron_module.get_state())
    return jsonify({"stale": True, "shunt": {"configured": False}, "mppt": {"configured": False}})


@app.route("/api/gps")
def api_gps():
    return jsonify(get_gps_status())


@app.route("/api/system")
def api_system():
    return jsonify(get_system_status())


@app.route("/api/weather")
def api_weather():
    return jsonify(get_weather_status())


@app.route("/api/network")
def api_network():
    return jsonify(_network_status_payload())


@app.route("/api/themes")
def api_get_themes():
    themes, seen = [], set()
    themes_dir = os.path.join(app.static_folder, "css", "themes")
    if os.path.isdir(themes_dir):
        for filename in sorted(os.listdir(themes_dir)):
            if not filename.endswith(".css"):
                continue
            base = filename[:-4]
            if base in seen:
                continue
            seen.add(base)
            path = os.path.join(themes_dir, filename)
            fallback = base.replace("-", " ").replace("_", " ").title()
            themes.append({"file": base, "name": _extract_css_friendly_name(path, fallback)})
    themes.sort(key=_theme_sort_key)
    return jsonify({"themes": themes})


@app.route("/api/current-theme")
def api_get_current_theme():
    return jsonify({"theme": current_global_theme})


@app.route("/api/sonos")
def api_sonos():
    return jsonify(get_sonos_status())


@app.route("/api/sonos/transport", methods=["POST"])
def api_sonos_transport():
    payload = request.get_json(silent=True) or {}
    return jsonify(set_transport(str(payload.get("action", ""))))


@app.route("/api/sonos/volume", methods=["POST"])
def api_sonos_volume():
    payload = request.get_json(silent=True) or {}
    try:
        level = int(payload.get("level", 0))
    except (TypeError, ValueError):
        level = 0
    return jsonify(set_volume(level))


@app.route("/api/sonos/mute", methods=["POST"])
def api_sonos_mute():
    payload = request.get_json(silent=True) or {}
    return jsonify(set_muted(bool(payload.get("muted"))))


@app.route("/sonos-art")
def proxy_sonos_album_art():
    art_url = request.args.get("url")
    if not art_url:
        return "Missing url", 400
    try:
        import requests

        parsed = urlparse(art_url)
        if parsed.port != 1400:
            return "Invalid", 403
        resp = requests.get(art_url, timeout=10, stream=True)
        if resp.status_code != 200:
            return "Failed", resp.status_code
        return Response(
            resp.iter_content(8192),
            content_type=resp.headers.get("content-type", "image/jpeg"),
            headers={"Cache-Control": "public, max-age=7200"},
        )
    except Exception as e:
        logger.warning(f"Sonos art proxy: {e}")
        return "Error", 502


@app.route("/api/wifi")
def api_wifi():
    return jsonify(get_wifi_status())


@app.route("/api/wifi/scan", methods=["POST"])
def api_wifi_scan():
    return jsonify(scan_wifi_networks())


@app.route("/api/wifi/connect", methods=["POST"])
def api_wifi_connect():
    payload = request.get_json(silent=True) or {}
    password = payload.get("password")
    if password is not None:
        password = str(password)
    return jsonify(connect_wifi(str(payload.get("ssid", "")), password))


@app.route("/api/wifi/disconnect", methods=["POST"])
def api_wifi_disconnect():
    return jsonify(disconnect_wifi())


@app.route("/api/wifi/prefer", methods=["POST"])
def api_wifi_prefer():
    payload = request.get_json(silent=True) or {}
    prefer = payload.get("prefer") or payload.get("uplink") or ""
    return jsonify(set_preferred_uplink(str(prefer)))


def _apply_light_change(data, *, source: str = "socket"):
    if not data or "name" not in data:
        logger.warning(f"light_change ignored ({source}) — bad payload: {data!r}")
        return None
    name = data["name"]
    if name not in runtime.compiled.light_names:
        logger.warning(f"light_change ignored ({source}) — unknown light: {name}")
        return None
    try:
        target = max(0, min(100, int(data.get("brightness", 0))))
    except (TypeError, ValueError):
        logger.warning(f"light_change ignored ({source}) — invalid brightness: {data.get('brightness')!r}")
        return None
    mode = data.get("mode", "white") if name in runtime.compiled.rgb_lights else None
    runtime.set_light_intent(name, target, mode)
    return runtime.get_ui_state()


@app.route("/api/light", methods=["POST"])
def api_light_change():
    state = _apply_light_change(request.get_json(silent=True) or {}, source="http")
    if state is None:
        return jsonify({"ok": False}), 400
    return jsonify({"ok": True, "state": state})


@app.route("/api/relay", methods=["POST"])
def api_relay_change():
    data = request.get_json(silent=True) or {}
    name = data.get("name")
    if name not in runtime.compiled.relay_names:
        logger.warning(f"relay_change ignored (http) — unknown relay: {name}")
        return jsonify({"ok": False}), 400
    runtime.set_relay_intent(name, bool(data.get("on", False)))
    return jsonify({"ok": True})


@app.route("/api/explain")
def api_explain():
    return jsonify(runtime.get_explain_json())


@app.route("/api/scenes")
def api_get_scenes():
    scenes = dict(
        sorted(runtime.compiled.scenes.items(), key=lambda item: item[1].get("order", 999))
    )
    return jsonify({
        "scenes": [
            {
                "key": k,
                "name": d["name"],
                "icon": d["icon"],
                "description": d["description"],
                "all_off": d["all_off"],
            }
            for k, d in scenes.items()
        ]
    })


@app.route("/api/scene", methods=["POST"])
def api_set_scene():
    data = request.get_json(silent=True) or {}
    scene = data.get("scene")
    if not scene or scene not in runtime.compiled.scenes:
        return jsonify({"ok": False}), 400
    runtime.set_scene(scene)
    return jsonify({
        "ok": True,
        "state": runtime.get_ui_state(),
        "ramp_ms": runtime.compiled.scene_ramp_ms,
    })


@app.route("/api/lights")
def api_get_lights():
    return jsonify({
        "lights": runtime.get_frontend_config(),
        "state": runtime.get_ui_state(),
    })


@app.route("/api/reeds/force", methods=["POST"])
def api_force_reed():
    if not _require_development_mode("force_reed"):
        return jsonify({"ok": False, "error": "development_mode required"}), 403
    data = request.get_json(silent=True) or {}
    name = data.get("name")
    if name is None:
        return jsonify({"ok": False, "error": "name required"}), 400
    state = runtime.force_reed(name, data.get("closed"))
    return jsonify({
        "ok": True,
        "state": state,
        "ramp_ms": state.get("_ramp_ms") or runtime.compiled.reed_ramp_ms,
        "reeds": runtime.get_reed_diag_json(),
        "effective": runtime.effective_reed_states(),
    })


@app.route("/api/reeds")
def api_get_reeds():
    diag = runtime.get_reed_diag_json()
    return jsonify({
        "reeds": runtime.get_reeds_frontend_config(),
        "states": runtime.effective_reed_states(),
        "raw": diag.get("states", {}),
        "forced": diag.get("forced", {}),
    })


def _screen_frontend_item(name: str, conf: dict, conn: dict | None = None) -> dict:
    """Build one screen record for the system tile."""
    from actuators.screens import brightness_is_adjustable

    observed_pct = 0
    if runtime.screen_actuator:
        observed_pct = runtime.screen_actuator._observed.get(name, 0)

    adjustable = brightness_is_adjustable(conf.get("brightness_path") or "")

    item = {
        "name": name,
        "label": conf.get("friendly", name),
        "icon": conf.get("icon", "fa-display"),
        "online": False,
        "latency": None,
        "on": observed_pct > 0,
        "brightness": None,
        "brightness_pct": observed_pct if observed_pct > 0 else None,
        "brightness_adjustable": adjustable,
        "ssh_passwordless": False,
        "ssh_error": None,
    }
    if conn:
        if conn.get("on") is not None:
            item["on"] = conn["on"]
        item["online"] = conn.get("online", False)
        item["brightness"] = conn.get("brightness")
        if conn.get("brightness_pct") is not None:
            item["brightness_pct"] = conn["brightness_pct"]
        item["ssh_passwordless"] = conn.get("ssh_passwordless", False)
        item["ssh_error"] = conn.get("ssh_error")
    return item


def _screens_list(*, probe: bool = False) -> list:
    if not runtime.screen_actuator:
        return []
    screens = []
    for name, conf in runtime.compiled.screens.items():
        conn = None
        if probe:
            conn = runtime.screen_actuator.test_connectivity(name)
        screens.append(_screen_frontend_item(name, conf, conn))
    return screens


@app.route("/api/screens")
def api_get_screens():
    return jsonify({"screens": _screens_list()})


@app.route("/api/screens/status")
def api_get_screens_status():
    return jsonify({"screens": _screens_list(probe=True)})


def _cleanup_best_effort(timeout_s: float = 10) -> None:
    """Run cleanup without blocking shutdown if hardware teardown hangs."""
    try:
        worker = threading.Thread(target=cleanup, daemon=True, name="shutdown-cleanup")
        worker.start()
        worker.join(timeout=timeout_s)
        if worker.is_alive():
            logger.warning("Shutdown cleanup did not finish within %.0fs", timeout_s)
    except Exception as e:
        logger.error(f"Shutdown cleanup error: {e}")


def _issue_host_shutdown() -> bool:
    """Schedule host power-off. Never call this in tests without mocking."""
    try:
        subprocess.Popen(
            ["sudo", "-n", "shutdown", "-h", "now"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        logger.warning("Host shutdown command issued")
        return True
    except Exception as e:
        logger.error(f"Host shutdown failed: {e}")
        return False


@app.route("/api/system/shutdown", methods=["POST"])
def api_system_shutdown():
    """Shut down remote touchscreens, then this Pi."""
    logger.warning("System shutdown requested via API")

    def _shutdown_sequence():
        import time

        # Screens first: host power-off tears down the network and kills SSH.
        # shutdown_all() blocks until each remote attempt finishes (or times out).
        if runtime.screen_actuator:
            try:
                runtime.screen_actuator.shutdown_all()
            except Exception as e:
                logger.warning("Screen shutdown phase failed: %s", e)

        # Issue host shutdown before cleanup — GPIO factory.close() can hang
        # indefinitely and previously prevented shutdown from ever running.
        if not _issue_host_shutdown():
            return

        time.sleep(2)
        _cleanup_best_effort()

    threading.Thread(target=_shutdown_sequence, daemon=True, name="shutdown-host").start()
    return jsonify({"ok": True, "message": "Shutting down…"})


@app.route("/api/phases")
def api_get_phases():
    if not phase_manager:
        return jsonify({"phase": None, "forced": False, "times": {}, "settings": {}})
    times = {}
    settings = {}
    try:
        times = phase_manager.get_phase_times()
    except Exception:
        pass
    try:
        settings = phase_manager.get_timing_settings()
    except Exception:
        pass
    return jsonify({
        "phase": phase_manager.get_phase(),
        "forced": phase_manager.is_forced(),
        "times": times,
        "settings": settings,
    })


@app.route("/api/phases/timing", methods=["POST"])
def api_set_phase_timing():
    """Update [phases] day/evening offsets and night start hour."""
    if not phase_manager:
        return jsonify({"ok": False, "error": "Phase manager unavailable"}), 503

    data = request.get_json(silent=True) or {}
    kwargs = {}
    if "day_offset_minutes" in data:
        kwargs["day_offset_minutes"] = data.get("day_offset_minutes")
    if "evening_offset_minutes" in data:
        kwargs["evening_offset_minutes"] = data.get("evening_offset_minutes")
    if "night_start_minutes" in data:
        kwargs["night_start_minutes"] = data.get("night_start_minutes")
    elif "night_start_hour" in data:
        # Legacy hour-only clients.
        kwargs["night_start_hour"] = data.get("night_start_hour")

    if not kwargs:
        return jsonify({"ok": False, "error": "No timing fields provided"}), 400

    try:
        result = phase_manager.update_timing_settings(**kwargs)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        logger.error("Phase timing update failed: %s", exc)
        return jsonify({"ok": False, "error": "Failed to update phase timing"}), 500

    return jsonify({"ok": True, "message": "Phase timing saved", **result})


def _ensure_homekit_manager():
    global homekit_module
    if homekit_module is None:
        homekit_module = HomeKitManager(runtime, config, socketio=socketio)
    return homekit_module


def _ensure_matter_manager():
    global matter_module
    if matter_module is None:
        matter_module = MatterManager(runtime, config, socketio=socketio)
    return matter_module


@app.route("/api/matter")
def api_matter_status():
    if not matter_module:
        enabled = bool(config.getboolean("matter", "enabled", fallback=False))
        return jsonify({
            "ok": True,
            "enabled": enabled,
            "running": False,
            "paired": False,
            "error": None,
        })
    return jsonify(matter_module.status())


@app.route("/api/matter", methods=["POST"])
def api_matter_update():
    data = request.get_json(silent=True) or {}
    if "enabled" not in data:
        return jsonify({"ok": False, "error": "enabled required"}), 400
    mgr = _ensure_matter_manager()
    return jsonify(mgr.set_enabled(bool(data.get("enabled"))))


@app.route("/api/matter/reset", methods=["POST"])
def api_matter_reset():
    mgr = _ensure_matter_manager()
    result = mgr.reset_pairing()
    if result.get("ok") is False:
        return jsonify(result), 500
    result["ok"] = True
    return jsonify(result)


@app.route("/api/homekit")
def api_homekit_status():
    if not homekit_module:
        enabled = bool(config.getboolean("homekit", "enabled", fallback=False))
        return jsonify({
            "ok": True,
            "enabled": enabled,
            "running": False,
            "paired": False,
            "error": None,
        })
    return jsonify(homekit_module.status())


@app.route("/api/homekit", methods=["POST"])
def api_homekit_update():
    data = request.get_json(silent=True) or {}
    if "enabled" not in data:
        return jsonify({"ok": False, "error": "enabled required"}), 400
    mgr = _ensure_homekit_manager()
    return jsonify(mgr.set_enabled(bool(data.get("enabled"))))


@app.route("/api/homekit/reset", methods=["POST"])
def api_homekit_reset():
    mgr = _ensure_homekit_manager()
    result = mgr.reset_pairing()
    if result.get("ok") is False:
        return jsonify(result), 500
    result["ok"] = True
    return jsonify(result)


@app.route("/api/sensors")
def api_get_sensors():
    if not sensor_manager:
        return jsonify({"source": "unavailable"})
    reading = getattr(sensor_manager, "last_reading", None) or {}
    payload = dict(reading)
    payload["source"] = "live" if reading else "waiting"
    # Always surface config flags (even before the first sensor loop tick).
    payload["fridge_configured"] = bool(getattr(sensor_manager, "FRIDGE_TEMP_ID", None))
    payload["freezer_configured"] = bool(getattr(sensor_manager, "FREEZER_TEMP_ID", None))
    return jsonify(payload)


@app.route("/api/dark-mode")
def api_get_dark_mode():
    if not phase_manager:
        return jsonify({"mode": "dark", "manual": False})
    return jsonify({
        "mode": phase_manager.get_current_dark_mode(),
        "manual": phase_manager.manual_dark_mode is not None,
    })


@socketio.on("light_change")
def handle_light_change(data):
    state = _apply_light_change(data, source="socket")
    if state is not None:
        emit("state_update", state)


@socketio.on("relay_change")
def handle_relay_change(data):
    name = data.get("name")
    if name not in runtime.compiled.relay_names:
        logger.warning(f"relay_change ignored — unknown relay: {name}")
        return
    runtime.set_relay_intent(name, bool(data.get("on", False)))


@socketio.on("get_reeds")
def handle_get_reeds():
    emit("reed_update", {"states": runtime.effective_reed_states()})


@socketio.on("force_reed")
def handle_force_reed(data):
    if not _require_development_mode("force_reed"):
        return
    name = data.get("name")
    if name is None:
        return
    runtime.force_reed(name, data.get("closed"))


@socketio.on("set_scene")
def handle_set_scene(data):
    scene = data.get("scene")
    if not scene or scene not in runtime.compiled.scenes:
        return
    runtime.set_scene(scene)
    emit("state_update", runtime.get_ui_state(), broadcast=True)


@socketio.on("force_phase")
def handle_force_phase(data):
    if not _require_development_mode("force_phase"):
        return
    runtime.force_phase(data.get("phase"))


@socketio.on("get_reeds_diag")
def handle_get_reeds_diag():
    emit("reed_diag_update", runtime.get_reed_diag_json())


@socketio.on("set_gps_simulation")
def handle_gps_simulation(data):
    if not _require_development_mode("set_gps_simulation"):
        return
    if not gps_module:
        return
    payload = data or {}
    if "no_hardware" in payload and hasattr(gps_module, "set_no_hardware_simulation"):
        gps_module.set_no_hardware_simulation(bool(payload.get("no_hardware")))
    if "no_fix" in payload and hasattr(gps_module, "set_no_fix_simulation"):
        gps_module.set_no_fix_simulation(bool(payload.get("no_fix")))


@socketio.on("screen_manual_toggle")
def handle_screen_manual_toggle(data):
    """Direct hardware command only — no sticky screen intents (reed policy wins)."""
    if not runtime.screen_actuator:
        return
    name = data.get("name")
    if not name:
        return

    def _apply_screen_level(level: int):
        level = max(0, min(100, int(level)))
        # Align commanded cache so the next reed reconcile re-sends policy cleanly
        if hasattr(runtime.reconciler, "_commanded_screens"):
            runtime.reconciler._commanded_screens[name] = level
        runtime.screen_actuator.manual_toggle(name, brightness_pct=level)
        runtime._emit_screens_observed([name])

    brightness = data.get("brightness_pct")
    if brightness is not None:
        try:
            brightness = max(0, min(100, int(brightness)))
        except (TypeError, ValueError):
            return
        _apply_screen_level(brightness)
        return

    on = data.get("on")
    if on:
        from engine.precedence import resolve_screen

        screen = runtime.compiled.screens.get(name)
        # Use phase level when reed allows; if reed is closed (policy 0), still
        # send a one-shot wake using day level — next reed reconcile restores policy.
        level = 100
        if screen:
            level = resolve_screen(screen, runtime.world.snapshot(), runtime.compiled)
            if level <= 0:
                levels = screen.get("phase_brightness") or {}
                level = int(levels.get("day") or max(levels.values(), default=100) or 100)
        _apply_screen_level(level)
        return
    _apply_screen_level(0)


@socketio.on("set_global_dark_mode")
def handle_set_global_dark_mode(data):
    mode = data.get("mode")
    if mode not in ("dark", "light"):
        return
    if phase_manager:
        phase_manager.set_manual_dark_mode(mode)
    emit("global_dark_mode_update", {"mode": mode, "manual": True}, broadcast=True)


@socketio.on("set_global_theme")
def handle_set_global_theme(data):
    global current_global_theme
    theme = data.get("theme")
    if not theme:
        return
    current_global_theme = theme
    theme_config.save({"theme": theme})
    emit("global_theme_update", {"theme": theme}, broadcast=True)


@socketio.on("get_network_status")
def handle_get_network_status():
    emit("network_update", _network_status_payload())


@socketio.on("sonos_command")
def handle_sonos_command(data):
    if not sonos_module:
        return
    result = sonos_module.execute_command(data or {})
    if isinstance(result, dict) and result.get("error"):
        emit("toast", {"type": "error", "message": f"Sonos: {result['error']}"})


@socketio.on("sonos_switch_speaker")
def handle_sonos_switch_speaker(data):
    name = (data or {}).get("name")
    if not sonos_module or not name:
        return
    make_default = (data or {}).get("make_default", True)
    if sonos_module.switch_speaker(name, make_default=bool(make_default)):
        emit("sonos_update", sonos_module.get_current_state(), broadcast=True)
        emit("sonos_speakers", {
            "speakers": list(sonos_module.speakers.keys()),
            "current": sonos_module.current_speaker,
            "preferred": sonos_module.preferred_name,
        }, broadcast=True)
        if make_default:
            if getattr(sonos_module, "_last_default_saved", False):
                emit("toast", {
                    "type": "success",
                    "title": "Sonos",
                    "message": f"Default speaker set to {name}",
                })
            else:
                emit("toast", {
                    "type": "warning",
                    "title": "Sonos",
                    "message": f"Switched to {name}, but could not save default to config",
                })


@socketio.on("sonos_request_state")
def handle_sonos_request_state():
    if sonos_module:
        sonos_module.request_state()
    else:
        emit("sonos_update", {"source": "unavailable", "speakers": []})


@socketio.on("get_victron_state")
def handle_get_victron_state():
    if victron_module:
        emit("victron_update", victron_module.get_state())
    else:
        emit("victron_update", {"stale": True})


@socketio.on("toast_test")
def handle_toast_test(data):
    if not _require_development_mode("toast_test"):
        return
    if toast_manager:
        toast_manager.send_toast(
            title=data.get("title"),
            message=data.get("message", "Test"),
            toast_type=data.get("type", "info"),
            duration=data.get("duration", 4500),
            persistent=data.get("persistent", False),
        )


@socketio.on("connect")
def handle_connect():
    global first_state_read_done
    emit("lights_config", runtime.get_frontend_config())
    emit("reeds_config", runtime.get_reeds_frontend_config())

    # Hardware may still be initializing in the background (NTP wait, etc.).
    if not first_state_read_done:
        try:
            runtime.reconciler.read_hardware()
            first_state_read_done = True
        except Exception as e:
            logger.debug(f"Initial hardware read deferred until startup finishes: {e}")
    emit("state_update", runtime.get_ui_state())

    if phase_manager:
        phase_data = {"phase": phase_manager.get_phase()}
        try:
            phase_data.update(phase_manager.get_phase_times())
        except Exception:
            pass
        emit("phase_update", phase_data)
        emit("phase_diag_update", {"forced": phase_manager.is_forced()})
        emit("global_dark_mode_update", {
            "mode": phase_manager.get_current_dark_mode(),
            "manual": phase_manager.manual_dark_mode is not None,
        })

    if runtime.screen_actuator:
        emit("screens_init", {"screens": _screens_list()})

    if victron_module:
        emit("victron_update", victron_module.get_state())

    emit("global_theme_update", {"theme": current_global_theme})
    emit("network_update", _network_status_payload())

    if sonos_module:
        try:
            emit("sonos_update", sonos_module.get_current_state())
            emit("sonos_speakers", {
                "speakers": list(sonos_module.speakers.keys()),
                "current": sonos_module.current_speaker,
                "preferred": sonos_module.preferred_name,
            })
        except Exception as e:
            logger.debug(f"Sonos connect emit: {e}")
    else:
        emit("sonos_update", {"source": "unavailable", "speakers": []})

    emit("reed_update", {"states": runtime.effective_reed_states()})
    emit("reed_diag_update", runtime.get_reed_diag_json())

    if gps_module:
        emit("gps_update", gps_module.get_state())

    if sensor_manager:
        try:
            sensor_manager.update_sensors()
        except Exception as e:
            logger.debug(f"Initial sensor read failed: {e}")


def cleanup():
    global _cleanup_done
    if _cleanup_done:
        return
    _cleanup_done = True

    logger.info("Cleaning up runtime...")
    shutdown_event.set()

    if phase_manager:
        try:
            phase_manager.stop()
        except Exception as e:
            logger.debug(f"Phase manager stop: {e}")

    if sensor_manager:
        try:
            sensor_manager.stop()
        except Exception as e:
            logger.debug(f"Sensor manager stop: {e}")

    if gps_module:
        try:
            gps_module.cleanup()
        except Exception as e:
            logger.debug(f"GPS cleanup: {e}")

    if victron_module:
        try:
            victron_module.stop()
        except Exception as e:
            logger.debug(f"Victron stop: {e}")

    if sonos_module:
        try:
            sonos_module.stop()
        except Exception as e:
            logger.debug(f"Sonos stop: {e}")

    if homekit_module:
        try:
            homekit_module.stop()
        except Exception as e:
            logger.debug(f"HomeKit stop: {e}")

    if matter_module:
        try:
            matter_module.stop()
        except Exception as e:
            logger.debug(f"Matter stop: {e}")

    try:
        runtime.stop()
    except Exception as e:
        logger.error(f"Runtime cleanup error: {e}")


def _startup():
    global gps_module, phase_manager, sensor_manager, victron_module, sonos_module, homekit_module, matter_module

    logger.info("Starting SCCS lighting backend...")
    from modules.clock import ensure_clock_for_automation, log_clock_status

    log_clock_status(logger)
    app._start_time = datetime.now()

    runtime.start_hardware()

    gps_module = GPSModule(config, socketio)
    set_gps_module(gps_module)
    set_weather_gps(gps_module)
    set_system_gps(gps_module)
    phase_manager = PhaseManager(config, gps_module, socketio, dark_mode_config)
    phase_manager.on_phase_change = lambda p, f, inv: runtime.on_phase_change(p, f, inv)
    runtime.phase_manager = phase_manager
    runtime.gps = gps_module

    sensor_manager = SensorManager(config, runtime.esp32.send_command, socketio)
    runtime.sensor_manager = sensor_manager

    gps_module.init_gps()
    gps_module.init_geolocator()

    ensure_clock_for_automation(logger, config)
    runtime.bootstrap_phase()
    runtime.finish_startup()
    phase_manager.start()
    sensor_manager.start()

    runtime.start_background_threads()

    if getattr(gps_module, "serial", None):
        gps_module.start_reader()

    try:
        from modules.victron import VictronManager

        victron_module = VictronManager(socketio, config, phase_manager=phase_manager)
        victron_module.start()
        set_victron_module(victron_module)
        set_power_victron(victron_module)
        phase_manager.register_night_listener(victron_module.reset_daily_generation)
    except Exception as e:
        logger.error(f"Victron init failed: {e}")
        victron_module = None

    try:
        from modules.sonos import SonosManager

        sonos_module = SonosManager(socketio, config)
        sonos_module.start()
        set_sonos_manager(sonos_module)
    except Exception as e:
        logger.error(f"Sonos init failed: {e}")
        sonos_module = None

    try:
        homekit_module = HomeKitManager(runtime, config, socketio=socketio)
        homekit_module.start()
    except Exception as e:
        logger.error(f"HomeKit init failed: {e}")
        homekit_module = None

    try:
        matter_module = MatterManager(runtime, config, socketio=socketio)
        matter_module.start()
    except Exception as e:
        logger.error(f"Matter init failed: {e}")
        matter_module = None

    def _fanout_sensors(data):
        if homekit_module:
            homekit_module.on_sensors(data)
        if matter_module:
            matter_module.on_sensors(data)

    def _fanout_power(data):
        if homekit_module:
            homekit_module.on_power(data)
        if matter_module:
            matter_module.on_power(data)

    if sensor_manager:
        sensor_manager.on_update = _fanout_sensors
        reading = getattr(sensor_manager, "last_reading", None)
        if reading:
            _fanout_sensors(reading)
    if victron_module:
        victron_module.on_update = _fanout_power

    threading.Thread(target=network_status_broadcaster, daemon=True).start()

    logger.info("SCCS lighting backend ready")


if __name__ == "__main__":
    atexit.register(cleanup)
    try:
        host = config.get("system", "host", fallback="0.0.0.0")
        port = config.getint("system", "port", fallback=5000)

        # Bind HTTP immediately so nginx (port 80 → 5000) never returns 502 while
        # systemd already marks the unit active. NTP wait and hardware init can take
        # tens of seconds (up to clock_sync_timeout_s) and used to block the listen.
        # Sample reeds first (~150ms) so a connecting UI does not see the
        # all-closed world default, and lighting never applies before GPIO.
        try:
            runtime.prime_reeds()
        except Exception:
            logger.exception("Reed prime failed — lighting will wait for startup")

        def _run_startup():
            try:
                _startup()
            except Exception:
                logger.exception(
                    "Background startup failed — UI is up but backend may be incomplete"
                )

        threading.Thread(target=_run_startup, name="sccs-startup", daemon=True).start()
        socketio.run(
            app,
            host=host,
            port=port,
            debug=debug_mode,
            use_reloader=False,
            allow_unsafe_werkzeug=True,
        )
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutdown requested")
    finally:
        cleanup()