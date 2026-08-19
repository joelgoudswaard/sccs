from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from actuators.esp32 import Esp32Actuator
from actuators.relays import RelayActuator
from actuators.screens import ScreenActuator
from engine.config_compile import compile_config
from engine.policy import desired_outputs
from engine.precedence import any_reed_open
from engine.reconcile import ANIMATED_RAMP_SOURCES, Reconciler
from engine.world import WorldStore
from inputs.reeds import ReedInput
from modules.esp32 import Esp32Manager
from modules.gpio import GPIODeviceManager

logger = logging.getLogger("sccs")


class SCCSRuntime:
    """Central runtime: world store, policy reconcile, inputs."""

    def __init__(self, config, socketio=None, dark_mode_config=None):
        self.config = config
        self.socketio = socketio
        self.compiled = compile_config(config)
        self.dark_mode_config = dark_mode_config

        self.esp32 = Esp32Manager(config)
        self.gpio = GPIODeviceManager(config)

        self.world = WorldStore(
            self.compiled.reed_names,
            self.compiled.light_names,
            self.compiled.relay_names,
        )
        self.world.set_light_to_reed_map(self.compiled.light_to_reed)

        self.esp32_actuator = Esp32Actuator(self.esp32, self.compiled)
        self.relay_actuator = RelayActuator(self.gpio, self.compiled.relay_names)
        self.screen_actuator = ScreenActuator(self.compiled.screens, self.compiled) if self.compiled.screens else None

        self.reconciler = Reconciler(
            world=self.world,
            cfg=self.compiled,
            esp32_actuator=self.esp32_actuator,
            relay_actuator=self.relay_actuator,
            screen_actuator=self.screen_actuator,
            on_state_emit=self._emit_state,
            on_screens_emit=self._emit_screens_observed,
            ramp_ms_for_source=self._ramp_ms_for_source,
            on_drift=self._on_hardware_drift,
        )
        self.reed_input: Optional[ReedInput] = None
        self.phase_manager = None
        self.gps = None
        self.sensor_manager = None
        self._shutdown = threading.Event()
        self._reconcile_lock = threading.Lock()
        self._gpio_ready = False
        self._state_listeners: list = []
        self._reed_listeners: list = []

    def _ramp_ms_for_source(self, source: str) -> int:
        return {
            "ui": self.compiled.ui_ramp_ms,
            "scene": self.compiled.scene_ramp_ms,
            "phase": self.compiled.phase_ramp_ms,
            "reed": self.compiled.reed_ramp_ms,
        }.get(source, self.compiled.reed_ramp_ms)

    def reconcile(self, ramp_source: str = "auto"):
        try:
            with self._reconcile_lock:
                self.reconciler.reconcile(ramp_source=ramp_source)
        except Exception as e:
            logger.warning(f"Reconcile ({ramp_source}): {e}")
        if ramp_source in ANIMATED_RAMP_SOURCES:
            self._schedule_post_ramp_read(self._ramp_ms_for_source(ramp_source))

    def _reconcile_async(self, ramp_source: str = "auto"):
        def _run():
            try:
                self.reconcile(ramp_source=ramp_source)
            except Exception as e:
                logger.warning(f"Async reconcile ({ramp_source}): {e}")

        threading.Thread(
            target=_run,
            daemon=True,
            name=f"Reconcile-{ramp_source}",
        ).start()

    def broadcast_ui_state(self):
        """Push lighting slider state to every connected browser."""
        self._emit_state(self.get_ui_state())

    def add_state_listener(self, callback) -> None:
        if callback and callback not in self._state_listeners:
            self._state_listeners.append(callback)

    def add_reed_listener(self, callback) -> None:
        if callback and callback not in self._reed_listeners:
            self._reed_listeners.append(callback)

    def _emit_state(self, state: dict):
        if self.socketio:
            try:
                self.socketio.emit("state_update", state, namespace="/")
            except Exception as e:
                logger.warning(f"state_update broadcast failed: {e}")
        for callback in self._state_listeners:
            try:
                callback(state)
            except Exception as e:
                logger.debug("state listener failed: %s", e)

    def _screen_policy_payload(self, snap=None) -> list[dict]:
        from actuators.screens import brightness_is_adjustable

        if not self.screen_actuator:
            return []
        snap = snap or self.world.snapshot()
        desired = desired_outputs(snap, self.compiled)
        screens = []
        for name, brightness in desired.screens.items():
            pct = max(0, min(100, int(brightness)))
            self.screen_actuator._observed[name] = pct
            conf = self.compiled.screens.get(name) or {}
            screens.append({
                "name": name,
                "on": pct > 0,
                "brightness_pct": pct if pct > 0 else None,
                "brightness_adjustable": brightness_is_adjustable(
                    conf.get("brightness_path") or ""
                ),
            })
        return screens

    def _emit_screens_update(self, screens: list[dict]):
        if not self.socketio or not screens:
            return
        try:
            self.socketio.emit("screens_update", {"screens": screens}, namespace="/")
        except Exception as e:
            logger.warning(f"screens_update broadcast failed: {e}")

    def _emit_screens_preview(self, snap=None):
        """Push policy-derived screen power/brightness immediately (reed/phase changes)."""
        self._emit_screens_update(self._screen_policy_payload(snap))

    def _emit_screens_observed(self, names: list):
        from actuators.screens import brightness_is_adjustable

        if not self.screen_actuator or not names:
            return
        screens = []
        for name in names:
            if name not in self.compiled.screens:
                continue
            pct = self.screen_actuator._observed.get(name, 0)
            conf = self.compiled.screens.get(name) or {}
            screens.append({
                "name": name,
                "on": pct > 0,
                "brightness_pct": pct if pct > 0 else None,
                "brightness_adjustable": brightness_is_adjustable(
                    conf.get("brightness_path") or ""
                ),
            })
        self._emit_screens_update(screens)

    def _schedule_post_ramp_read(self, ramp_ms: int):
        """Re-read hardware after a ramp so observed state catches up before drift checks."""
        delay_s = (
            ramp_ms / 1000.0
            + self.compiled.optimistic_lock_duration_s
            + 0.5
        )

        def _work():
            if self._shutdown.wait(delay_s):
                return
            try:
                if self.esp32.is_connected():
                    self.reconciler.read_hardware()
            except Exception as e:
                logger.debug(f"Post-ramp hardware read: {e}")

        threading.Thread(target=_work, daemon=True, name="PostRampRead").start()

    def _on_hardware_drift(self, drifts: list):
        from modules.toasts import toast_manager
        if not drifts:
            return
        if toast_manager:
            if len(drifts) == 1:
                d = drifts[0]
                key = d.get("light") or d.get("relay") or "output"
                toast_manager.warning(
                    d.get("detail", "hardware mismatch"),
                    title=f"Drift: {key}",
                    duration=8000,
                )
            else:
                toast_manager.warning(
                    f"{len(drifts)} outputs differ from desired state",
                    title="Hardware drift",
                    duration=8000,
                )
        self.broadcast_ui_state()

    def get_explain_json(self) -> dict:
        return self.reconciler.explain_snapshot()

    def effective_reed_states(self) -> dict:
        """Authoritative reed map for the main UI (forces override hardware)."""
        snap = self.world.snapshot()
        effective = dict(snap.reeds)
        for name, closed in snap.reed_forces.items():
            effective[name] = closed
        return effective

    def _emit_reeds(self):
        states = self.effective_reed_states()
        if self.socketio:
            self.socketio.emit("reed_update", {"states": states})
            self.socketio.emit("reed_diag_update", self.get_reed_diag_json())
        for callback in self._reed_listeners:
            try:
                callback(states)
            except Exception as e:
                logger.debug("reed listener failed: %s", e)

    def on_reeds_updated(self, reeds: dict, transitioned_reeds: list):
        snap_before = self.world.snapshot()
        open_before = any_reed_open(snap_before, self.compiled)
        self.world.update_reeds(reeds, transition_reeds=transitioned_reeds)
        transitioned = list(transitioned_reeds or [])
        snap_after = self.world.snapshot()
        open_after = any_reed_open(snap_after, self.compiled)
        include_ambient = open_before != open_after
        affected_lights: set[str] = set()
        for reed in transitioned:
            affected_lights |= self.reconciler.invalidate_commanded_for_reed(
                reed, include_ambient=False
            )
        if include_ambient:
            affected_lights |= self.reconciler.invalidate_ambient_lights()
        affected_reeds = set()
        for reed in transitioned:
            affected_reeds |= self.reconciler._reeds_for_invalidation(reed)
        self.world.clear_light_intents_for_reeds(affected_reeds)
        self._emit_state(self._preview_partial_ui_state("reed", affected_lights))
        self._emit_reeds()
        self._emit_screens_preview(snap_after)
        self._reconcile_async("reed")

    def on_phase_change(self, phase: str, forced: Optional[str], invalidate: bool):
        self.world.set_phase(phase, forced, invalidate=invalidate)
        self._emit_screens_preview()
        self.reconcile(ramp_source="phase")
        self.broadcast_ui_state()

    def set_light_intent(self, name: str, brightness: int, mode: Optional[str] = None):
        self.world.set_light_intent(name, brightness, mode, expires="until_reed_close")
        self.reconcile(ramp_source="ui")

    def set_relay_intent(self, name: str, on: bool):
        self.world.set_relay_intent(name, on)
        self.reconcile(ramp_source="ui")

    def set_scene(self, scene_key: str):
        scene = self.compiled.scenes.get(scene_key, {})
        if not scene:
            logger.warning(f"Unknown scene: {scene_key}")
            return

        # Ramp to scene levels; cleared on reed event, phase change, slider, or another scene.
        self.world.clear_all_light_intents()
        self.world.set_active_scene(scene_key)
        self.reconcile(ramp_source="scene")

        from modules.toasts import toast_manager
        if toast_manager and scene.get("name"):
            all_off = scene.get("all_off")
            all_off_exceptions = all_off and bool(scene.get("lights"))
            title = "All Off" if all_off and not all_off_exceptions else None
            toast_manager.success(
                f"{scene['name']} activated"
                if not all_off or all_off_exceptions
                else "All lights turned off",
                title=title or scene["name"],
                duration=4000 if all_off and not all_off_exceptions else 3500,
            )

    @staticmethod
    def _coerce_reed_closed(closed) -> Optional[bool]:
        if closed is None:
            return None
        if isinstance(closed, bool):
            return closed
        if isinstance(closed, str):
            value = closed.strip().lower()
            if value in ("true", "1", "yes", "on", "closed"):
                return True
            if value in ("false", "0", "no", "off", "open"):
                return False
        return bool(closed)

    def _reed_force_affected_lights(
        self, reed_names, *, include_ambient: bool
    ) -> set[str]:
        affected: set[str] = set()
        for reed_name in reed_names:
            affected |= self.reconciler.invalidate_commanded_for_reed(
                reed_name, include_ambient=False
            )
        if include_ambient:
            affected |= self.reconciler.invalidate_ambient_lights()
        return affected

    def force_reed(self, name: str, closed: Optional[bool]) -> dict:
        closed = self._coerce_reed_closed(closed)
        snap_before = self.world.snapshot()
        open_before = any_reed_open(snap_before, self.compiled)

        if name == "all" and closed is None:
            self.world.clear_all_reed_forces()
            reed_names = list(self.compiled.reed_names)
        elif closed is None:
            if name not in self.compiled.reed_names:
                logger.warning(f"force_reed ignored — unknown reed: {name}")
                return self.get_ui_state()
            self.world.set_reed_force(name, None)
            reed_names = [name]
        else:
            if name not in self.compiled.reed_names:
                logger.warning(f"force_reed ignored — unknown reed: {name}")
                return self.get_ui_state()
            self.world.set_reed_force(name, closed)
            reed_names = [name]

        snap_after = self.world.snapshot()
        include_ambient = open_before != any_reed_open(snap_after, self.compiled)
        affected_lights = self._reed_force_affected_lights(
            reed_names, include_ambient=include_ambient
        )
        affected_reeds = set()
        for reed_name in reed_names:
            affected_reeds |= self.reconciler._reeds_for_invalidation(reed_name)
        self.world.clear_light_intents_for_reeds(affected_reeds)

        preview = self._preview_partial_ui_state("reed", affected_lights)
        self._emit_state(preview)
        self._emit_reeds()
        self._emit_screens_preview(snap_after)
        self._reconcile_async("reed")
        return preview

    def force_phase(self, phase: Optional[str]):
        if not self.phase_manager:
            return
        if phase is None:
            self.phase_manager.clear_force()
        else:
            self.phase_manager.force_phase(phase)
        pm = self.phase_manager
        self.world.set_phase(pm.get_phase(), pm.forced_phase, invalidate=True)
        self.reconcile(ramp_source="phase")

    def get_ui_state(self) -> dict:
        state = self.reconciler.build_ui_state()
        state.update(self.reconciler._last_emit_meta)
        return state

    def _preview_ui_state(self, ramp_source: str) -> dict:
        """Desired UI levels from policy — no hardware I/O (for instant slider animation)."""
        return self._preview_partial_ui_state(ramp_source, None)

    def _preview_partial_ui_state(
        self, ramp_source: str, affected_lights: Optional[set]
    ) -> dict:
        """Policy preview; when affected_lights is set, leave other lights unchanged."""
        world = self.world.snapshot()
        desired = desired_outputs(world, self.compiled)
        if affected_lights is not None:
            self.reconciler._preserve_lights_except(desired, world, affected_lights)
        state = self.reconciler.build_ui_state(desired)
        if ramp_source in ANIMATED_RAMP_SOURCES:
            state.update({
                "_animate": True,
                "_ramp_ms": self._ramp_ms_for_source(ramp_source),
                "_trigger": ramp_source,
            })
        return state

    def get_reed_diag_json(self) -> dict:
        """Raw hardware + force overrides — diagnostics only."""
        snap = self.world.snapshot()
        return {"states": dict(snap.reeds), "forced": dict(snap.reed_forces)}

    def _ensure_reed_input(self) -> ReedInput:
        if self.reed_input is None:
            self.reed_input = ReedInput(
                gpio_manager=self.gpio,
                reed_names=self.compiled.reed_names,
                debounce_ms=self.compiled.reed_debounce_ms,
                stable_polls=self.compiled.reed_stable_polls,
                on_update=self.on_reeds_updated,
            )
        return self.reed_input

    def prime_reeds(self) -> dict:
        """GPIO + stable reed sample into the world. No lighting. Safe to call twice."""
        if not self._gpio_ready:
            self.gpio.init_devices()
            self._gpio_ready = True
        states = self._ensure_reed_input().prime()
        self.world.update_reeds(states)
        closed = sum(1 for v in states.values() if v)
        opened = len(states) - closed
        logger.info(f"🚪 Reeds primed — {opened} open / {closed} closed")
        return states

    def start_hardware(self):
        """GPIO/reeds first, then ESP serial. No reconcile yet — phase comes first."""
        self.prime_reeds()
        self.esp32.init_serial()
        self.reconciler.read_hardware()

    def bootstrap_phase(self):
        """Calculate real phase and write to world before any light automation runs."""
        pm = self.phase_manager
        if not pm:
            logger.warning("🌗 Phase manager not attached — automation deferred")
            return
        use_fallback = not pm._has_valid_gps()
        phase = pm.bootstrap_initial_phase(use_fallback=use_fallback)
        self.world.set_phase(phase, pm.forced_phase, invalidate=False)
        logger.info(f"🌗 Automation unlocked for phase: {phase}")

    def finish_startup(self):
        """Start reed polling and run the first reconcile (phase must already be set)."""
        self.prime_reeds()
        self._ensure_reed_input().start()
        self.reconcile(ramp_source="startup")

    def start_background_threads(self):
        """Background hardware read + drift reporting only — no periodic policy reconcile."""
        threading.Thread(target=self._sync_loop, daemon=True, name="HardwareSync").start()

    def _sync_loop(self):
        while not self._shutdown.is_set():
            time.sleep(self.compiled.sync_interval_s)
            try:
                # Re-probe silent UARTs so ESPs powered later come online without restart.
                self.esp32.refresh_connection()
                if self.esp32.is_connected():
                    self.reconciler.read_hardware()
                    self.reconciler.report_hardware_drift()
            except Exception as e:
                logger.debug(f"Hardware sync: {e}")

    def stop(self):
        self._shutdown.set()
        if self.reed_input:
            self.reed_input.stop()
            self.reed_input = None
        if self.phase_manager:
            self.phase_manager.stop()
        if self.sensor_manager:
            self.sensor_manager.stop()
        if self.gps:
            self.gps.cleanup()
        self.gpio.cleanup()
        self.esp32.cleanup()

    def get_frontend_config(self):
        return self.esp32.get_frontend_config()

    def get_reeds_frontend_config(self) -> list:
        """Ordered reed metadata for the system tile UI."""
        reeds = []
        if not self.config.has_section("reeds"):
            return reeds
        for name, line in self.config.get_section("reeds").items():
            parts = [p.strip() for p in str(line).split("|")]
            if len(parts) < 2:
                continue
            icon = parts[4] if len(parts) > 4 else "fa-door-closed"
            try:
                order = int(parts[5]) if len(parts) > 5 else 999
            except ValueError:
                order = 999
            reeds.append({"name": name, "label": parts[0], "icon": icon, "order": order})
        reeds.sort(key=lambda r: r.get("order", 999))
        return reeds