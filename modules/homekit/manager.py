"""HomeKit bridge manager — start/stop, status, pairing reset."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

from modules.homekit.mapping import AccessorySpec, MapOptions, build_specs
from modules.homekit.sync import AccessoryIndex
from modules.version import APP_VERSION

logger = logging.getLogger("sccs")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class HomeKitManager:
    """Owns the HAP driver thread and accessory index."""

    def __init__(self, runtime, config, socketio=None):
        self.runtime = runtime
        self.config = config
        self.socketio = socketio
        self.driver = None
        self.thread: Optional[threading.Thread] = None
        self.index = AccessoryIndex()
        self.specs: list[AccessorySpec] = []
        self._lock = threading.Lock()
        self._error: Optional[str] = None
        self._started = False
        self._sensor_manager = None
        self._victron_manager = None

    # ----- config --------------------------------------------------------

    def is_enabled(self) -> bool:
        return bool(self.config.getboolean("homekit", "enabled", fallback=False))

    def _cfg(self, key: str, fallback: str = "") -> str:
        return (self.config.get("homekit", key, fallback=fallback) or fallback).strip()

    def _ensure_pin(self) -> str:
        from modules.homekit.driver import generate_pin, validate_pin

        pin = self._cfg("pin")
        if pin:
            return validate_pin(pin)
        pin = generate_pin()
        try:
            if self.config.has_section("homekit"):
                self.config.set_option("homekit", "pin", pin)
        except Exception as exc:
            logger.warning("Could not persist HomeKit pin: %s", exc)
        return pin

    def _map_options(self) -> MapOptions:
        fridge = bool((self.config.get("sensors", "fridge_temp_sensor", fallback="") or "").strip())
        freezer = bool((self.config.get("sensors", "freezer_temp_sensor", fallback="") or "").strip())
        outside = True
        victron = bool(
            (self.config.get("victron", "shunt_address", fallback="") or "").strip()
            or (self.config.get("victron", "mppt_address", fallback="") or "").strip()
        )
        return MapOptions(
            fridge_configured=fridge,
            freezer_configured=freezer,
            outside_configured=outside,
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

    # ----- lifecycle -----------------------------------------------------

    def start(self) -> None:
        if self._started:
            return
        if not self.is_enabled():
            logger.info("HomeKit disabled ([homekit] enabled = false)")
            return
        try:
            self._start_inner()
        except Exception:
            logger.exception("HomeKit failed to start")
            self._error = "failed to start"
            self.stop()

    def set_enabled(self, enabled: bool) -> dict:
        from modules.config import config as live

        live.ensure_section(
            "homekit",
            {
                "enabled": "false",
                "name": "SCCS",
                "pin": "",
                "port": "51826",
                "bind_address": "10.10.10.1",
                "persist_file": "config/homekit.state",
                "low_battery_percent": "20",
            },
        )
        live.set_option("homekit", "enabled", "true" if enabled else "false")
        self.config = live
        if enabled:
            self.start()
        else:
            self.stop()
        payload = self.status()
        payload["ok"] = True
        return payload

    def _start_inner(self) -> None:
        from pyhap.accessory import Bridge

        from modules.homekit.accessories import (
            BatteryAccessory,
            LightAccessory,
            ReedAccessory,
            RelayAccessory,
            SceneAccessory,
            TemperatureAccessory,
            WaterAccessory,
        )
        from modules.homekit.driver import (
            build_driver,
            persist_path,
            resolve_bind_address,
            start_driver_thread,
        )
        from modules.homekit.mapping import (
            KIND_BATTERY,
            KIND_LIGHT,
            KIND_REED,
            KIND_RELAY_LIGHT,
            KIND_RELAY_SWITCH,
            KIND_SCENE,
            KIND_TEMPERATURE,
            KIND_WATER,
        )

        address = resolve_bind_address(self._cfg("bind_address", "10.10.10.1"))
        if not address:
            self._error = "LAN disconnected"
            return

        pin = self._ensure_pin()
        port = int(self.config.getint("homekit", "port", fallback=51826) or 51826)
        persist = persist_path(self._cfg("persist_file", "config/homekit.state"), _REPO_ROOT)
        name = self._cfg("name", "SCCS") or "SCCS"
        low_batt = int(self.config.getint("homekit", "low_battery_percent", fallback=20) or 20)

        self.specs = build_specs(
            self.runtime.compiled,
            reed_labels=self._reed_labels(),
            relay_icons=self._relay_icons(),
            options=self._map_options(),
        )
        self.index = AccessoryIndex()

        driver = build_driver(
            address=address, port=port, persist_file=persist, pincode=pin
        )
        bridge = Bridge(driver, name)
        bridge.set_info_service(
            firmware_revision=APP_VERSION,
            manufacturer="SCCS",
            model="SCCS Bridge",
            serial_number="sccs-bridge",
        )

        builders = {
            KIND_LIGHT: lambda spec: LightAccessory(driver, spec, self.runtime),
            KIND_RELAY_LIGHT: lambda spec: RelayAccessory(driver, spec, self.runtime),
            KIND_RELAY_SWITCH: lambda spec: RelayAccessory(driver, spec, self.runtime),
            KIND_SCENE: lambda spec: SceneAccessory(driver, spec, self.runtime),
            KIND_REED: lambda spec: ReedAccessory(driver, spec),
            KIND_TEMPERATURE: lambda spec: TemperatureAccessory(driver, spec),
            KIND_WATER: lambda spec: WaterAccessory(driver, spec),
            KIND_BATTERY: lambda spec: BatteryAccessory(
                driver, spec, low_percent=low_batt
            ),
        }
        for spec in self.specs:
            factory = builders.get(spec.kind)
            if not factory:
                continue
            acc = factory(spec)
            bridge.add_accessory(acc)
            self.index.register(spec, acc)

        driver.add_accessory(bridge)

        self.runtime.add_state_listener(self.on_ui_state)
        self.runtime.add_reed_listener(self.on_reeds)

        self.driver = driver
        self.thread = start_driver_thread(driver)
        self._started = True
        self._error = None
        logger.info(
            "HomeKit bridge %r on %s:%s (%d accessories)",
            name,
            address,
            port,
            len(self.specs),
        )

        try:
            self.on_ui_state(self.runtime.get_ui_state())
            self.on_reeds(self.runtime.effective_reed_states())
        except Exception:
            logger.debug("HomeKit initial state push failed", exc_info=True)

    def stop(self) -> None:
        driver = self.driver
        self.driver = None
        self._started = False
        if driver is None:
            return
        try:
            driver.stop()
        except Exception:
            logger.debug("HomeKit driver stop", exc_info=True)
        thread = self.thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=4)
        self.thread = None

    def reset_pairing(self) -> dict:
        """Delete persist file and restart so a new pairing code is advertised."""
        from modules.homekit.driver import persist_path

        persist = persist_path(self._cfg("persist_file", "config/homekit.state"), _REPO_ROOT)
        was_enabled = self.is_enabled()
        self.stop()
        try:
            if os.path.exists(persist):
                os.remove(persist)
                logger.info("HomeKit pairing reset — removed %s", persist)
        except OSError as exc:
            logger.error("Failed to remove HomeKit persist file: %s", exc)
            return {"ok": False, "error": "Could not remove pairing file"}
        if was_enabled:
            # Give the previous HAP thread a moment to release the port.
            time.sleep(0.4)
            self.start()
        return self.status()

    # ----- incoming SCCS state ------------------------------------------

    def set_sensor_manager(self, manager) -> None:
        self._sensor_manager = manager

    def set_victron_manager(self, manager) -> None:
        self._victron_manager = manager

    def on_ui_state(self, state: dict) -> None:
        if not self._started:
            return
        try:
            self.index.apply_ui_state(state)
        except Exception:
            logger.debug("HomeKit ui state apply failed", exc_info=True)

    def on_reeds(self, states: dict) -> None:
        if not self._started:
            return
        try:
            self.index.apply_reeds(states)
        except Exception:
            logger.debug("HomeKit reed apply failed", exc_info=True)

    def on_sensors(self, data: dict) -> None:
        if not self._started:
            return
        try:
            self.index.apply_sensors(data)
        except Exception:
            logger.debug("HomeKit sensor apply failed", exc_info=True)

    def on_power(self, data: dict) -> None:
        if not self._started:
            return
        try:
            self.index.apply_power(data)
        except Exception:
            logger.debug("HomeKit power apply failed", exc_info=True)

    # ----- status / QR ---------------------------------------------------

    def status(self) -> dict:
        from modules.homekit.driver import qr_svg

        enabled = self.is_enabled()
        paired = False
        pin = self._cfg("pin")
        qr_uri = ""
        svg = ""
        address = self._cfg("bind_address", "10.10.10.1")
        port = int(self.config.getint("homekit", "port", fallback=51826) or 51826)
        name = self._cfg("name", "SCCS") or "SCCS"

        driver = self.driver
        if driver is not None:
            try:
                paired = bool(driver.state.paired)
                pin = driver.state.pincode.decode("ascii")
                if not paired and driver.accessory is not None:
                    qr_uri = driver.accessory.xhm_uri()
                    svg = qr_svg(qr_uri)
            except Exception:
                logger.debug("HomeKit status QR failed", exc_info=True)

        running = bool(self._started and self.thread and self.thread.is_alive())
        if enabled and not running and not self._error:
            headline_error = "starting"
        else:
            headline_error = self._error

        return {
            "ok": True,
            "enabled": enabled,
            "running": running,
            "paired": paired,
            "pin": pin,
            "qr_uri": qr_uri,
            "qr_svg": svg,
            "name": name,
            "address": address,
            "port": port,
            "accessory_count": len(self.specs),
            "error": headline_error,
        }
