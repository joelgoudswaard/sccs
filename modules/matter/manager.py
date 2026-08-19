"""Matter child-process manager — start/stop, status, pairing reset."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from typing import Optional

import psutil

from modules.accessories.mapping import AccessorySpec, MapOptions, build_specs
from modules.homekit.driver import address_is_local, persist_path, qr_svg

logger = logging.getLogger("sccs")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BRIDGE_DIR = os.path.join(_REPO_ROOT, "matter-bridge")
_BRIDGE_JS = os.path.join(_BRIDGE_DIR, "index.mjs")


def iface_for_address(addr: str) -> Optional[str]:
    if not addr:
        return None
    for name, addrs in psutil.net_if_addrs().items():
        for item in addrs:
            if item.address == addr:
                return name
    return None


def iface_has_ipv6(iface: str) -> bool:
    addrs = psutil.net_if_addrs().get(iface) or []
    for item in addrs:
        if getattr(item, "family", None) == getattr(psutil, "AF_INET6", None) or (
            isinstance(item.address, str) and ":" in item.address and "%" in item.address
        ):
            if str(item.address).startswith("fe80:") or ":" in str(item.address):
                return True
    # family may be socket.AF_INET6 (10) rather than psutil constant
    import socket

    for item in addrs:
        fam = getattr(item, "family", None)
        if fam == socket.AF_INET6:
            return True
    return False


class MatterManager:
    def __init__(self, runtime, config, socketio=None):
        self.runtime = runtime
        self.config = config
        self.socketio = socketio
        self.proc: Optional[subprocess.Popen] = None
        self.specs: list[AccessorySpec] = []
        self._lock = threading.Lock()
        self._error: Optional[str] = None
        self._started = False
        self._status: dict = {}
        self._reader: Optional[threading.Thread] = None

    def is_enabled(self) -> bool:
        return bool(self.config.getboolean("matter", "enabled", fallback=False))

    def _cfg(self, key: str, fallback: str = "") -> str:
        return (self.config.get("matter", key, fallback=fallback) or fallback).strip()

    def _map_options(self) -> MapOptions:
        fridge = bool((self.config.get("sensors", "fridge_temp_sensor", fallback="") or "").strip())
        freezer = bool((self.config.get("sensors", "freezer_temp_sensor", fallback="") or "").strip())
        victron = bool(
            (self.config.get("victron", "shunt_address", fallback="") or "").strip()
            or (self.config.get("victron", "mppt_address", fallback="") or "").strip()
        )
        return MapOptions(
            fridge_configured=fridge,
            freezer_configured=freezer,
            outside_configured=True,
            victron_configured=victron,
        )

    def _reed_labels(self) -> dict[str, str]:
        return {
            item["name"]: item["label"]
            for item in (self.runtime.get_reeds_frontend_config() or [])
            if item.get("name")
        }

    def _relay_icons(self) -> dict[str, str]:
        icons = {}
        for item in self.runtime.get_frontend_config() or []:
            if item.get("type") == "relay" and item.get("name"):
                icons[item["name"]] = item.get("icon") or ""
        return icons

    def start(self) -> None:
        if self._started and self.proc and self.proc.poll() is None:
            return
        if not self.is_enabled():
            logger.info("Matter disabled ([matter] enabled = false)")
            return
        try:
            self._start_inner()
        except Exception:
            logger.exception("Matter failed to start")
            self._error = "failed to start"
            self.stop()

    def set_enabled(self, enabled: bool) -> dict:
        from modules.config import config as live

        live.ensure_section(
            "matter",
            {
                "enabled": "false",
                "name": "SCCS",
                "port": "5540",
                "bind_address": "10.10.10.1",
                "persist_file": "config/matter.state",
            },
        )
        live.set_option("matter", "enabled", "true" if enabled else "false")
        self.config = live
        if enabled:
            self.start()
        else:
            self.stop()
        payload = self.status()
        payload["ok"] = True
        return payload

    def _start_inner(self) -> None:
        if not shutil.which("node"):
            self._error = "nodejs is not installed"
            logger.error("Matter: node not on PATH")
            return
        if not os.path.isfile(_BRIDGE_JS):
            self._error = "matter-bridge/index.mjs missing"
            return
        if not os.path.isdir(os.path.join(_BRIDGE_DIR, "node_modules", "@matter")):
            self._error = "matter-bridge npm packages missing (run npm install)"
            logger.error("Matter: %s", self._error)
            return

        address = self._cfg("bind_address", "10.10.10.1")
        if not address_is_local(address):
            self._error = "LAN disconnected"
            logger.warning("Matter LAN disconnected (%s not on this host)", address)
            return
        iface = iface_for_address(address)
        if not iface or not iface_has_ipv6(iface):
            self._error = "LAN has no IPv6 address — re-run installer menu 6"
            logger.warning("Matter: no IPv6 on %s (%s)", iface or "?", address)
            return

        persist = persist_path(self._cfg("persist_file", "config/matter.state"), _REPO_ROOT)
        os.makedirs(persist, exist_ok=True)

        self.specs = build_specs(
            self.runtime.compiled,
            reed_labels=self._reed_labels(),
            relay_icons=self._relay_icons(),
            options=self._map_options(),
        )
        cfg = {
            "name": self._cfg("name", "SCCS") or "SCCS",
            "unique_id": "sccs",
            "port": int(self.config.getint("matter", "port", fallback=5540) or 5540),
            "storage_path": persist,
            "mdns_interface": iface,
            "specs": [
                {
                    "key": s.key,
                    "kind": s.kind,
                    "name": s.name,
                    "entity": s.entity,
                    "has_brightness": s.has_brightness,
                    "has_bug_mode": s.has_bug_mode,
                    "sensor_field": s.sensor_field,
                }
                for s in self.specs
                if s.kind != "battery"
            ],
        }
        cfg_path = os.path.join(persist, "bridge.json")
        with open(cfg_path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)

        env = os.environ.copy()
        env["MATTER_STORAGE_PATH"] = persist
        self.proc = subprocess.Popen(
            ["node", _BRIDGE_JS, cfg_path],
            cwd=_BRIDGE_DIR,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        self._started = True
        self._error = None
        self._reader = threading.Thread(target=self._read_loop, name="MatterOut", daemon=True)
        self._reader.start()
        threading.Thread(target=self._read_stderr, name="MatterErr", daemon=True).start()

        self.runtime.add_state_listener(self.on_ui_state)
        self.runtime.add_reed_listener(self.on_reeds)

        logger.info(
            "Matter bridge %r on %s (%s, %d accessories)",
            cfg["name"],
            address,
            iface,
            len(cfg["specs"]),
        )
        try:
            self.on_ui_state(self.runtime.get_ui_state())
            self.on_reeds(self.runtime.effective_reed_states())
        except Exception:
            logger.debug("Matter initial state push failed", exc_info=True)

    def _send(self, payload: dict) -> None:
        proc = self.proc
        if not proc or not proc.stdin:
            return
        try:
            proc.stdin.write(json.dumps(payload) + "\n")
            proc.stdin.flush()
        except Exception:
            logger.debug("Matter stdin write failed", exc_info=True)

    def _read_loop(self) -> None:
        proc = self.proc
        if not proc or not proc.stdout:
            return
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                logger.debug("Matter stdout (non-json): %s", line[:200])
                continue
            self._handle_child(msg)

    def _read_stderr(self) -> None:
        proc = self.proc
        if not proc or not proc.stderr:
            return
        for line in proc.stderr:
            text = line.rstrip()
            if text:
                logger.info("Matter: %s", text)

    def _handle_child(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind in ("ready", "status"):
            self._status = msg
            logger.info(
                "Matter %s commissioned=%s qr=%s",
                kind,
                msg.get("commissioned"),
                "yes" if msg.get("qr") else "no",
            )
            return
        if kind == "error":
            logger.warning("Matter child: %s", msg.get("message"))
            return
        if kind != "command":
            return
        entity = msg.get("entity")
        spec_kind = msg.get("kind")
        try:
            if spec_kind == "scene":
                self.runtime.set_scene(entity)
            elif spec_kind in ("relay_light", "relay_switch"):
                self.runtime.set_relay_intent(entity, bool(msg.get("on")))
            elif spec_kind == "light":
                brightness = msg.get("brightness")
                mode = msg.get("mode")
                if brightness is None and mode is None and "on" in msg:
                    brightness = 100 if msg.get("on") else 0
                if brightness is None:
                    state = self.runtime.get_ui_state()
                    brightness = int(state.get(entity) or 0)
                if mode is None:
                    state = self.runtime.get_ui_state()
                    mode = state.get(f"{entity}_mode")
                self.runtime.set_light_intent(entity, int(brightness), mode)
        except Exception:
            logger.exception("Matter command failed: %s", msg)

    def stop(self) -> None:
        self._started = False
        proc = self.proc
        self.proc = None
        if not proc:
            return
        try:
            if proc.stdin:
                proc.stdin.write(json.dumps({"type": "shutdown"}) + "\n")
                proc.stdin.flush()
        except Exception:
            pass
        try:
            proc.wait(timeout=4)
        except Exception:
            proc.kill()

    def reset_pairing(self) -> dict:
        persist = persist_path(self._cfg("persist_file", "config/matter.state"), _REPO_ROOT)
        was = self.is_enabled()
        self.stop()
        try:
            if os.path.isdir(persist):
                shutil.rmtree(persist)
                logger.info("Matter pairing reset — removed %s", persist)
        except OSError as exc:
            logger.error("Failed to remove Matter persist: %s", exc)
            return {"ok": False, "error": "Could not remove pairing file"}
        if was:
            time.sleep(0.4)
            self.start()
        return self.status()

    def on_ui_state(self, state: dict) -> None:
        if not self._started or not state:
            return
        for spec in self.specs:
            if spec.kind == "light" and spec.entity in state:
                self._send(
                    {
                        "type": "set",
                        "key": spec.key,
                        "brightness": state.get(spec.entity),
                        "mode": state.get(f"{spec.entity}_mode"),
                    }
                )
            elif spec.kind in ("relay_light", "relay_switch") and spec.entity in state:
                self._send(
                    {
                        "type": "set",
                        "key": spec.key,
                        "on": bool(state.get(spec.entity)),
                    }
                )

    def on_reeds(self, states: dict) -> None:
        if not self._started or not states:
            return
        for spec in self.specs:
            if spec.kind == "reed" and spec.entity in states:
                self._send({"type": "set", "key": spec.key, "closed": bool(states[spec.entity])})

    def on_sensors(self, data: dict) -> None:
        if not self._started or not data:
            return
        for spec in self.specs:
            if spec.kind == "temperature" and spec.sensor_field in data:
                self._send({"type": "set", "key": spec.key, "celsius": data.get(spec.sensor_field)})
            elif spec.kind == "water":
                self._send({"type": "set", "key": spec.key, "percent": data.get("water_percent")})

    def on_power(self, data: dict) -> None:
        return

    def status(self) -> dict:
        enabled = self.is_enabled()
        running = bool(self._started and self.proc and self.proc.poll() is None)
        child = self._status or {}
        qr_uri = child.get("qr") or ""
        svg = ""
        if qr_uri:
            try:
                svg = qr_svg(qr_uri)
            except Exception:
                svg = ""
        return {
            "ok": True,
            "enabled": enabled,
            "running": running,
            "paired": bool(child.get("commissioned")),
            "pin": child.get("manual") or "",
            "qr_uri": qr_uri,
            "qr_svg": svg,
            "name": self._cfg("name", "SCCS") or "SCCS",
            "address": self._cfg("bind_address", "10.10.10.1"),
            "port": int(self.config.getint("matter", "port", fallback=5540) or 5540),
            "accessory_count": len(self.specs),
            "error": self._error,
        }
