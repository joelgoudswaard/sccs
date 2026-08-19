# modules/victron.py
"""
Victron SmartShunt + MPPT SmartSolar manager (BLE Instant Readout).

Uses the `victron_ble` library for passive advertisement scanning.
All values shown in the UI are taken directly from the devices — no local
calculations, no Arduino fallbacks.

The tile stays compact (target ~150 px) and only displays fields the user
explicitly asked for:
  - SoC gauge + %
  - Battery voltage
  - Time-to-go (∞ when charging sentinel)
  - Consumed Ah (from shunt)
  - Solar current A ("current generated" from MPPT)
  - Yield today kWh ("total generated for the day" from MPPT)

Emits: 'victron_update'.
"""

import logging
import time
import threading
import asyncio
from datetime import datetime, timezone

logger = logging.getLogger("sccs")


class VictronManager:
    def __init__(self, socketio, config, phase_manager=None):
        self.socketio = socketio
        self.config = config
        self.phase_manager = phase_manager

        # ====================== CONFIG ======================
        self.shunt_address = (config.get('victron', 'shunt_address', fallback='') or '').strip().lower()
        self.shunt_key     = (config.get('victron', 'shunt_key',     fallback='') or '').strip()

        self.mppt_address  = (config.get('victron', 'mppt_address',  fallback='') or '').strip().lower()
        self.mppt_key      = (config.get('victron', 'mppt_key',      fallback='') or '').strip()

        self.scan_interval = config.getfloat('victron', 'scan_interval', fallback=2.0)
        self.stale_timeout = config.getfloat('victron', 'stale_timeout', fallback=600.0)

        # Internal
        self._running = False
        self._ble_thread = None
        self._stop_event = threading.Event()
        self._last_emit_ts = 0.0
        self._last_data_ts = 0.0
        self.on_update = None
        self._device_last_ts: dict[str, float] = {}
        self._known_devices = {}

        self.state = {
            "stale": True,
            "soc": None,
            "voltage": None,
            "current_a": None,
            "consumed_ah": None,
            "time_to_go_mins": None,
            "solar_power_w": None,
            "solar_current_a": None,
            "yield_today_kwh": None,
            "charge_state": None,
            "temperature": None,
            "last_update": None,
            "shunt": self._empty_shunt_state(),
            "mppt": self._empty_mppt_state(),
        }

        self.device_keys = {}
        if self.shunt_address and self.shunt_key:
            self.device_keys[self.shunt_address] = self.shunt_key
        if self.mppt_address and self.mppt_key:
            self.device_keys[self.mppt_address] = self.mppt_key

        if not self.device_keys:
            logger.warning("🔋 Victron: no shunt or mppt keys configured — tile will stay stale until devices are added")
        else:
            logger.info(
                "🔋 VictronManager initialized (shunt=%s, mppt=%s, scan=%.1fs, stale=%.0fs)",
                "yes" if self.shunt_address else "no",
                "yes" if self.mppt_address else "no",
                self.scan_interval,
                self.stale_timeout,
            )

    # ====================== PUBLIC API ======================

    def start(self):
        if self._running:
            return
        if not self.device_keys:
            return

        self._running = True
        self._stop_event.clear()

        self._ble_thread = threading.Thread(target=self._ble_thread_target, daemon=True, name="victron-ble")
        self._ble_thread.start()

        logger.info("🔋 Victron BLE scanner thread started")

    def stop(self):
        if not self._running:
            return

        logger.info("🔋 VictronManager stopping...")
        self._running = False
        self._stop_event.set()

        if self._ble_thread and self._ble_thread.is_alive():
            self._ble_thread.join(timeout=3.0)

        logger.info("🔋 VictronManager stopped")

    def get_state(self):
        """Return a copy of current state for socket / API consumers."""
        s = self.state.copy()
        s["stale"] = self._is_stale()
        s["shunt"] = self._device_state_view("shunt", self.shunt_address)
        s["mppt"] = self._device_state_view("mppt", self.mppt_address)
        s.pop("_last_stale", None)
        if s.get("last_update") is None and self.state.get("last_update"):
            s["last_update"] = self.state["last_update"]
        return s

    def reset_daily_generation(self):
        """Called by PhaseManager when entering Night phase.
        With real Victron MPPT we just rely on its native yield_today — no-op here.
        """
        logger.debug("🔋 Victron daily reset requested (ignored — using device yield_today)")

    # ====================== INTERNAL ======================

    def _is_stale(self):
        if not self._last_data_ts:
            return True
        return (time.time() - self._last_data_ts) > self.stale_timeout

    def _device_is_stale(self, address: str) -> bool:
        if not address:
            return True
        last = self._device_last_ts.get(address)
        if not last:
            return True
        return (time.time() - last) > self.stale_timeout

    @staticmethod
    def _enum_label(value) -> str | None:
        if value is None:
            return None
        if hasattr(value, "name"):
            return value.name.replace("_", " ").title()
        return str(value)

    def _empty_shunt_state(self) -> dict:
        return {
            "configured": bool(self.shunt_address),
            "address": self.shunt_address or None,
            "connected": False,
            "stale": True,
            "name": None,
            "rssi": None,
            "model_name": None,
            "soc": None,
            "voltage": None,
            "current": None,
            "remaining_mins": None,
            "consumed_ah": None,
            "temperature": None,
            "alarm": None,
            "aux_mode": None,
            "starter_voltage": None,
            "midpoint_voltage": None,
            "last_update": None,
        }

    def _empty_mppt_state(self) -> dict:
        return {
            "configured": bool(self.mppt_address),
            "address": self.mppt_address or None,
            "connected": False,
            "stale": True,
            "name": None,
            "rssi": None,
            "model_name": None,
            "charge_state": None,
            "charger_error": None,
            "battery_voltage": None,
            "battery_charging_current": None,
            "yield_today_wh": None,
            "solar_power": None,
            "external_device_load": None,
            "last_update": None,
        }

    def _device_state_view(self, role: str, address: str) -> dict:
        raw = self.state.get(role, {})
        view = dict(raw)
        view["configured"] = bool(address)
        view["address"] = address or None
        connected = bool(address and address in self._device_last_ts)
        view["connected"] = connected
        view["stale"] = self._device_is_stale(address) if address else True
        return view

    def _touch_device_meta(self, role: str, ble_device, advertisement, now: float):
        addr = (ble_device.address or "").lower()
        self._device_last_ts[addr] = now
        self._last_data_ts = now
        device = self.state[role]
        name = getattr(ble_device, "name", None)
        if name and name != device.get("name"):
            device["name"] = name
        rssi = getattr(advertisement, "rssi", None)
        if rssi is not None and rssi != device.get("rssi"):
            device["rssi"] = int(rssi)
        device["connected"] = True
        device["last_update"] = datetime.now(timezone.utc).isoformat()

    def _apply_shunt_reading(self, ble_device, advertisement, parsed, now: float) -> bool:
        self._touch_device_meta("shunt", ble_device, advertisement, now)
        device = self.state["shunt"]
        changed = False

        if hasattr(parsed, "get_model_name"):
            model = parsed.get_model_name()
            if model and model != device.get("model_name"):
                device["model_name"] = model
                changed = True

        for key, getter, fmt in (
            ("soc", "get_soc", lambda v: round(float(v), 1)),
            ("voltage", "get_voltage", lambda v: round(float(v), 2)),
            ("current", "get_current", lambda v: round(float(v), 2)),
            ("consumed_ah", "get_consumed_ah", lambda v: round(float(v), 1)),
            ("temperature", "get_temperature", lambda v: round(float(v), 1)),
            ("starter_voltage", "get_starter_voltage", lambda v: round(float(v), 2)),
            ("midpoint_voltage", "get_midpoint_voltage", lambda v: round(float(v), 2)),
        ):
            if not hasattr(parsed, getter):
                continue
            val = getattr(parsed, getter)()
            if val is None:
                continue
            formatted = fmt(val)
            if formatted != device.get(key):
                device[key] = formatted
                changed = True
            if key == "soc" and formatted != self.state.get("soc"):
                self.state["soc"] = formatted
                changed = True
            if key == "voltage" and formatted != self.state.get("voltage"):
                self.state["voltage"] = formatted
                changed = True
            if key == "current" and formatted != self.state.get("current_a"):
                self.state["current_a"] = formatted
                changed = True
            if key == "consumed_ah" and formatted != self.state.get("consumed_ah"):
                self.state["consumed_ah"] = formatted
                changed = True
            if key == "temperature" and formatted != self.state.get("temperature"):
                self.state["temperature"] = formatted
                changed = True

        if hasattr(parsed, "get_remaining_mins"):
            ttg = parsed.get_remaining_mins()
            mins = int(ttg) if ttg is not None and ttg < 65000 else None
            if mins != device.get("remaining_mins"):
                device["remaining_mins"] = mins
                changed = True
            if mins != self.state.get("time_to_go_mins"):
                self.state["time_to_go_mins"] = mins
                changed = True

        if hasattr(parsed, "get_alarm"):
            alarm = self._enum_label(parsed.get_alarm())
            if alarm != device.get("alarm"):
                device["alarm"] = alarm
                changed = True

        if hasattr(parsed, "get_aux_mode"):
            aux_mode = self._enum_label(parsed.get_aux_mode())
            if aux_mode != device.get("aux_mode"):
                device["aux_mode"] = aux_mode
                changed = True

        return changed

    def _apply_mppt_reading(self, ble_device, advertisement, parsed, now: float) -> bool:
        self._touch_device_meta("mppt", ble_device, advertisement, now)
        device = self.state["mppt"]
        changed = False

        if hasattr(parsed, "get_model_name"):
            model = parsed.get_model_name()
            if model and model != device.get("model_name"):
                device["model_name"] = model
                changed = True

        if hasattr(parsed, "get_battery_voltage"):
            bv = parsed.get_battery_voltage()
            if bv is not None:
                formatted = round(float(bv), 2)
                if formatted != device.get("battery_voltage"):
                    device["battery_voltage"] = formatted
                    changed = True
                if self.state.get("voltage") is None:
                    self.state["voltage"] = formatted
                    changed = True

        if hasattr(parsed, "get_battery_charging_current"):
            sc = parsed.get_battery_charging_current()
            if sc is not None:
                formatted = round(float(sc), 2)
                if formatted != device.get("battery_charging_current"):
                    device["battery_charging_current"] = formatted
                    changed = True
                if formatted != self.state.get("solar_current_a"):
                    self.state["solar_current_a"] = formatted
                    changed = True

        if hasattr(parsed, "get_yield_today"):
            y = parsed.get_yield_today()
            if y is not None:
                wh = round(float(y), 0)
                kwh = round(float(y) / 1000.0, 2)
                if wh != device.get("yield_today_wh"):
                    device["yield_today_wh"] = wh
                    changed = True
                if kwh != self.state.get("yield_today_kwh"):
                    self.state["yield_today_kwh"] = kwh
                    changed = True

        if hasattr(parsed, "get_solar_power"):
            sp = parsed.get_solar_power()
            if sp is not None:
                formatted = round(float(sp), 0)
                if formatted != device.get("solar_power"):
                    device["solar_power"] = formatted
                    changed = True
                if formatted != self.state.get("solar_power_w"):
                    self.state["solar_power_w"] = formatted
                    changed = True

        if hasattr(parsed, "get_external_device_load"):
            load = parsed.get_external_device_load()
            if load is not None:
                formatted = round(float(load), 2)
                if formatted != device.get("external_device_load"):
                    device["external_device_load"] = formatted
                    changed = True

        if hasattr(parsed, "get_charge_state"):
            try:
                cs = self._map_charge_state(parsed.get_charge_state())
            except Exception:
                cs = None
            if cs is not None and cs != device.get("charge_state"):
                device["charge_state"] = cs
                changed = True
            if cs is not None and cs != self.state.get("charge_state"):
                self.state["charge_state"] = cs
                changed = True

        if hasattr(parsed, "get_charger_error"):
            try:
                err = self._enum_label(parsed.get_charger_error())
            except Exception:
                err = None
            if err is not None and err != device.get("charger_error"):
                device["charger_error"] = err
                changed = True

        return changed

    def _ble_thread_target(self):
        """Dedicated thread with its own asyncio loop (required for BLE + Flask)."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._ble_loop(loop))
        except Exception as e:
            logger.error("🔋 Victron BLE thread crashed: %s", e, exc_info=True)
        finally:
            try:
                loop.close()
            except:
                pass

    @staticmethod
    def _ble_error_message(exc: BaseException) -> str:
        """Flatten bleak's multi-arg errors into a single readable string."""
        if not getattr(exc, "args", None):
            return str(exc) or exc.__class__.__name__
        parts = []
        for arg in exc.args:
            if arg is None:
                continue
            # BleakBluetoothNotAvailableError often packs (msg, reason_enum)
            text = getattr(arg, "name", None) or str(arg)
            text = text.strip()
            if text and text not in parts:
                parts.append(text)
        return " — ".join(parts) if parts else exc.__class__.__name__

    async def _wait_or_stop(self, seconds: float) -> None:
        """Sleep in short slices so stop() remains responsive."""
        remaining = max(0.0, float(seconds))
        while remaining > 0 and self._running and not self._stop_event.is_set():
            step = min(0.5, remaining)
            await asyncio.sleep(step)
            remaining -= step

    async def _ble_loop(self, loop):
        """Main scanning loop using victron_ble.BaseScanner.

        Missing / powered-off Bluetooth is expected on some hosts (adapter
        soft-blocked, service down, etc.). Degrade quietly and retry with
        backoff so bringing Bluetooth up later recovers without restart.
        """
        try:
            from victron_ble.scanner import BaseScanner
        except ImportError as e:
            logger.error("🔋 victron_ble (or bleak) not importable: %s — BLE scanning unavailable", e)
            return

        try:
            from bleak.exc import BleakBluetoothNotAvailableError, BleakError
        except ImportError:  # pragma: no cover - bleak ships with victron_ble
            BleakBluetoothNotAvailableError = type("BleakBluetoothNotAvailableError", (Exception,), {})
            BleakError = Exception

        manager = self

        class _SCCSBleScanner(BaseScanner):
            def callback(self, ble_device, raw_data, advertisement):
                try:
                    manager._handle_advertisement(ble_device, raw_data, advertisement)
                except Exception as ex:
                    logger.debug(
                        "🔋 Victron parse error for %s: %s",
                        getattr(ble_device, "address", "?"),
                        ex,
                    )

        backoff = 5.0
        max_backoff = 60.0
        logged_unavailable = False

        while self._running and not self._stop_event.is_set():
            scanner = _SCCSBleScanner()
            try:
                await scanner.start()
                if logged_unavailable:
                    logger.info(
                        "🔋 Victron BLE scanner active — Bluetooth available again, listening for %s device(s)",
                        len(self.device_keys),
                    )
                else:
                    logger.info(
                        "🔋 Victron BLE scanner active — listening for %s device(s)",
                        len(self.device_keys),
                    )
                logged_unavailable = False
                backoff = 5.0

                while self._running and not self._stop_event.is_set():
                    await asyncio.sleep(0.5)
                    # Opportunistic staleness + emit check (cheap)
                    self._emit_if_needed()

                try:
                    await scanner.stop()
                except Exception:
                    pass
                logger.debug("🔋 Victron scanner stopped cleanly")
                break

            except BleakBluetoothNotAvailableError as e:
                msg = self._ble_error_message(e)
                if not logged_unavailable:
                    logger.warning(
                        "🔋 Bluetooth unavailable (%s) — Victron BLE scanning deferred; "
                        "will retry when an adapter is available",
                        msg,
                    )
                    logged_unavailable = True
                else:
                    logger.debug("🔋 Bluetooth still unavailable (%s); retry in %.0fs", msg, backoff)
                try:
                    await scanner.stop()
                except Exception:
                    pass
                await self._wait_or_stop(backoff)
                backoff = min(max_backoff, backoff * 2)

            except BleakError as e:
                msg = self._ble_error_message(e)
                # Adapter gone mid-scan, bluez restart, permission flake, etc.
                if not logged_unavailable:
                    logger.warning(
                        "🔋 Victron BLE scanner error (%s) — will retry",
                        msg,
                    )
                    logged_unavailable = True
                else:
                    logger.debug("🔋 Victron BLE still failing (%s); retry in %.0fs", msg, backoff)
                try:
                    await scanner.stop()
                except Exception:
                    pass
                await self._wait_or_stop(backoff)
                backoff = min(max_backoff, backoff * 2)

            except Exception as e:
                logger.error("🔋 Victron scanner error: %s", e, exc_info=True)
                try:
                    await scanner.stop()
                except Exception:
                    pass
                # Unexpected failures still get a quiet retry rather than killing the thread
                await self._wait_or_stop(backoff)
                backoff = min(max_backoff, backoff * 2)

    def _handle_advertisement(self, ble_device, raw_data, advertisement):
        """Parse one Victron advertisement and fold it into self.state."""
        addr = (ble_device.address or "").lower()
        if addr not in self.device_keys:
            return

        try:
            from victron_ble.devices import detect_device_type
            device_type = detect_device_type(raw_data)
            if device_type is None:
                return

            if addr not in self._known_devices:
                self._known_devices[addr] = device_type(self.device_keys[addr])
            parsed = self._known_devices[addr].parse(raw_data)

        except Exception as e:
            logger.debug("🔋 Victron advertisement handling failed for %s: %s", addr, e)
            return

        now = time.time()
        changed = False

        if addr == self.shunt_address:
            changed = self._apply_shunt_reading(ble_device, advertisement, parsed, now)
        elif addr == self.mppt_address:
            changed = self._apply_mppt_reading(ble_device, advertisement, parsed, now)

        self.state["last_update"] = datetime.now(timezone.utc).isoformat()

        if changed or (now - self._last_emit_ts) > (self.scan_interval * 3):
            self._emit_if_needed(force=True)

    def _map_charge_state(self, raw):
        """Map Victron charge state numbers / enums to friendly strings."""
        mapping = {
            0: "Off",
            1: "Bulk",
            2: "Absorption",
            3: "Float",
            4: "Storage",
            5: "Equalize",
            6: "Inverting",
            7: "Power supply",
            8: "Starting",
            9: "Repeated absorption",
            10: "Auto equalize",
            11: "BatterySafe",
            252: "External control",
        }
        if isinstance(raw, int):
            return mapping.get(raw, f"State {raw}")
        if hasattr(raw, "name"):
            return raw.name.replace("_", " ").title()
        if hasattr(raw, "value"):
            return mapping.get(raw.value, str(raw))
        return str(raw)

    def _emit_if_needed(self, force=False):
        if not self.socketio and not self.on_update:
            return

        now = time.time()
        stale = self._is_stale()

        # Always emit when staleness changes so the UI can react
        staleness_changed = stale != self.state.get("_last_stale", None)
        self.state["_last_stale"] = stale

        interval = 0.8 if force else self.scan_interval
        if (now - self._last_emit_ts) < interval and not staleness_changed and not force:
            return

        payload = self.get_state()
        try:
            if self.socketio:
                self.socketio.emit("victron_update", payload)
            self._last_emit_ts = now
            if self.on_update:
                self.on_update(payload)
        except Exception as e:
            logger.debug("🔋 Failed to emit victron_update: %s", e)

