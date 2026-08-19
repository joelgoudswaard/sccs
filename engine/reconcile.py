from __future__ import annotations

import logging
import time
from typing import Callable, Dict, List, Optional, Tuple

from .config_compile import CompiledConfig
from .explain import DRIFT_BRIGHTNESS_TOLERANCE, build_explain_snapshot, source_label
from .policy import DesiredOutputs, desired_outputs
from .precedence import is_scene_source
from .world import WorldStore

logger = logging.getLogger("sccs")

CommandedLight = Tuple[int, str]
ANIMATED_RAMP_SOURCES = frozenset({"reed", "phase", "scene", "ui", "startup"})


class Reconciler:
    """Apply desired state diffs to hardware actuators."""

    def __init__(
        self,
        world: WorldStore,
        cfg: CompiledConfig,
        esp32_actuator,
        relay_actuator,
        screen_actuator=None,
        on_state_emit: Optional[Callable[[dict], None]] = None,
        on_screens_emit: Optional[Callable[[List[str]], None]] = None,
        ramp_ms_for_source: Optional[Callable[[str], int]] = None,
        on_drift: Optional[Callable[[List[dict]], None]] = None,
    ):
        self.world = world
        self.cfg = cfg
        self.esp32 = esp32_actuator
        self.relays = relay_actuator
        self.screens = screen_actuator
        self.on_state_emit = on_state_emit
        self.on_screens_emit = on_screens_emit
        self.on_drift = on_drift
        self._ramp_ms = ramp_ms_for_source or (lambda _s: cfg.reed_ramp_ms)
        self._last_desired: Optional[DesiredOutputs] = None
        self._last_ramp_source: str = "unknown"
        self._commanded_lights: Dict[str, CommandedLight] = {}
        self._commanded_relays: Dict[str, bool] = {}
        self._commanded_screens: Dict[str, int] = {}
        self._commanded_at: Dict[str, float] = {}
        self._drift_grace_s = max(
            5.0,
            cfg.reed_ramp_ms / 1000.0 + cfg.optimistic_lock_duration_s + 2.0,
        )
        self._active_drifts: Dict[str, str] = {}
        self._pending_reed_command: set[str] = set()
        self._pending_reed_screens: set[str] = set()
        self._last_emit_meta: Dict[str, object] = {}

    def _reeds_for_invalidation(self, reed_name: str) -> set[str]:
        """Reeds whose outputs may change when reed_name transitions (incl. interlock dependents)."""
        reeds = {reed_name}
        for dependent in self.cfg.interlock_dependents.get(reed_name, []):
            reeds.add(dependent)
        return reeds

    def _lights_for_reeds(self, reeds: set[str]) -> set[str]:
        lights: set[str] = set()
        for reed in reeds:
            for light, mapped_reed in self.cfg.light_to_reed.items():
                if mapped_reed == reed:
                    lights.add(light)
        return lights

    def invalidate_ambient_lights(self) -> set[str]:
        """Drop commanded cache for ambient lights (when any-reed-open state flips)."""
        lights = set(self.cfg.ambient_lights)
        for light in lights:
            self._commanded_lights.pop(light, None)
            self._commanded_at.pop(light, None)
            self._pending_reed_command.add(light)
        return lights

    def invalidate_commanded_for_reed(
        self, reed_name: str, *, include_ambient: bool = False
    ) -> set[str]:
        """Drop commanded cache for outputs tied to a reed so hardware is re-sent."""
        reeds = self._reeds_for_invalidation(reed_name)
        lights = self._lights_for_reeds(reeds)
        if include_ambient:
            lights.update(self.cfg.ambient_lights)

        for light in lights:
            self._commanded_lights.pop(light, None)
            self._commanded_at.pop(light, None)
            self._pending_reed_command.add(light)

        if self.screens:
            for screen_name, screen in self.cfg.screens.items():
                linked = screen.get("linked_reed")
                if linked in reeds or linked == reed_name:
                    self._commanded_screens.pop(screen_name, None)
                    self._pending_reed_screens.add(screen_name)

        return lights

    def _should_recommand_light(
        self,
        light: str,
        brightness: int,
        cmd_b: int,
        world,
    ) -> bool:
        if cmd_b != brightness:
            return True
        observed = world.observed_lights.get(light)
        if observed is None:
            return False
        return abs(int(observed) - int(brightness)) > DRIFT_BRIGHTNESS_TOLERANCE

    def _preserve_lights_except(self, desired: DesiredOutputs, world, affected: set) -> None:
        """Keep untouched lights at their prior commanded or observed levels."""
        for light in self.cfg.light_names:
            if light in affected:
                continue
            if self._last_desired and light in self._last_desired.lights:
                desired.lights[light] = self._last_desired.lights[light]
                desired.light_sources[light] = self._last_desired.light_sources.get(
                    light, "fallback"
                )
                if light in self.cfg.rgb_lights and light in self._last_desired.light_modes:
                    desired.light_modes[light] = self._last_desired.light_modes[light]
            elif light in self._commanded_lights:
                cmd_b, cmd_m = self._commanded_lights[light]
                desired.lights[light] = (cmd_b, cmd_m)
                if light in self.cfg.rgb_lights:
                    desired.light_modes[light] = cmd_m
                desired.light_sources[light] = (
                    self._last_desired.light_sources.get(light, "unchanged")
                    if self._last_desired
                    else "unchanged"
                )
            elif light in world.observed_lights:
                obs = world.observed_lights[light]
                mode = world.observed_light_modes.get(light, "white")
                desired.lights[light] = (obs, mode)
                desired.light_sources[light] = "unchanged"
                if light in self.cfg.rgb_lights:
                    desired.light_modes[light] = mode

    def reconcile(self, ramp_source: str = "auto"):
        world = self.world.snapshot()
        desired = desired_outputs(world, cfg=self.cfg)
        desired.ramp_source = ramp_source
        self._last_ramp_source = ramp_source
        ramp_ms = self._ramp_ms(ramp_source)
        now = time.time()
        scene_pass = ramp_source == "scene"
        ui_pass = ramp_source == "ui"
        reed_pass = ramp_source == "reed"
        reed_affected = set(self._pending_reed_command) if reed_pass else set()

        for light, (brightness, mode) in desired.lights.items():
            source = desired.light_sources.get(light, "fallback")
            if scene_pass and not is_scene_source(source):
                continue
            if ui_pass and light not in world.light_intents:
                continue
            if reed_pass and light not in reed_affected:
                continue
            # Omitted phase level: leave hardware alone (no ramp / re-command).
            if source == "automation_omit":
                if reed_pass:
                    self._pending_reed_command.discard(light)
                continue

            target_m = mode or "white"
            cmd_b, cmd_m = self._commanded_lights.get(light, (-1, ""))
            needs_mode = light in self.cfg.rgb_lights and cmd_m != target_m
            if (
                reed_pass
                or self._should_recommand_light(light, brightness, cmd_b, world)
                or needs_mode
            ):
                self.esp32.set_light(
                    light,
                    brightness,
                    target_m if light in self.cfg.rgb_lights else None,
                    ramp_ms,
                    source=source,
                    trigger=ramp_source,
                )
                self._commanded_lights[light] = (brightness, target_m)
                self._commanded_at[light] = now
                if ramp_source in ANIMATED_RAMP_SOURCES:
                    self.world.seed_observed_light(
                        light,
                        brightness,
                        target_m if light in self.cfg.rgb_lights else None,
                    )
                if reed_pass:
                    self._pending_reed_command.discard(light)

        for relay, on in desired.relays.items():
            if ui_pass and relay not in world.relay_intents:
                continue
            if self._commanded_relays.get(relay) != on:
                rsource = "user_intent" if relay in world.relay_intents else "hardware_default"
                self.relays.set_relay(relay, on, source=rsource, trigger=ramp_source)
                self._commanded_relays[relay] = on
                self._commanded_at[f"relay:{relay}"] = now

        screens_changed: list[str] = []
        if self.screens:
            observed_screens = self.screens.read_screens()
            for screen, brightness in desired.screens.items():
                if reed_pass and screen not in self._pending_reed_screens:
                    continue
                commanded = self._commanded_screens.get(screen, -1)
                observed = observed_screens.get(screen, -1)
                if (
                    commanded != brightness
                    or abs(observed - brightness) > DRIFT_BRIGHTNESS_TOLERANCE
                ):
                    self.screens.set_screen(screen, brightness)
                    self._commanded_screens[screen] = brightness
                    screens_changed.append(screen)
                if reed_pass:
                    self._pending_reed_screens.discard(screen)

        if screens_changed and self.on_screens_emit:
            self.on_screens_emit(screens_changed)

        if scene_pass:
            scene_lights = {
                light
                for light in self.cfg.light_names
                if is_scene_source(desired.light_sources.get(light, ""))
            }
            self._preserve_lights_except(desired, world, scene_lights)
        elif ui_pass:
            self._preserve_lights_except(desired, world, set(world.light_intents.keys()))
        elif reed_pass:
            self._preserve_lights_except(desired, world, reed_affected)

        self._last_desired = desired

        if self.on_state_emit:
            ui_state = self.build_ui_state(desired)
            if ramp_source in ANIMATED_RAMP_SOURCES:
                self._last_emit_meta = {
                    "_animate": True,
                    "_ramp_ms": ramp_ms,
                    "_trigger": ramp_source,
                }
            else:
                self._last_emit_meta = {}
            ui_state.update(self._last_emit_meta)
            self.on_state_emit(ui_state)

    def build_ui_state(self, desired: Optional[DesiredOutputs] = None) -> dict:
        desired = desired or self._last_desired
        if not desired:
            world = self.world.snapshot()
            desired = desired_outputs(world, self.cfg)
        state = {}
        for name, (b, _) in desired.lights.items():
            state[name] = b
        for name, mode in desired.light_modes.items():
            state[f"{name}_mode"] = mode
        for name, on in desired.relays.items():
            state[name] = on
        snap = self.world.snapshot()
        if snap.last_scene:
            state["last_scene"] = snap.last_scene
        return state

    def read_hardware(self):
        """Refresh observed state from hardware reads."""
        lights, modes = self.esp32.read_lights()
        relays = self.relays.read_relays()
        now = time.time()
        filtered_lights = dict(lights)
        filtered_modes = dict(modes)
        for light, brightness in list(filtered_lights.items()):
            commanded_at = self._commanded_at.get(light, 0)
            if now - commanded_at >= self._drift_grace_s:
                continue
            cmd = self._commanded_lights.get(light)
            if not cmd:
                continue
            cmd_b, cmd_m = cmd
            if brightness < cmd_b - DRIFT_BRIGHTNESS_TOLERANCE:
                filtered_lights.pop(light, None)
                filtered_modes.pop(light, None)
            elif light in self.cfg.rgb_lights and filtered_modes.get(light) != cmd_m:
                filtered_modes.pop(light, None)
        self.world.update_observed_lights(filtered_lights, filtered_modes)
        self.world.update_observed_relays(relays)

    def check_hardware_drift(self) -> List[dict]:
        """Compare desired vs observed hardware; return active drift items."""
        if not self._last_desired:
            return []

        world = self.world.snapshot()
        snap = build_explain_snapshot(
            world,
            self.cfg,
            self._last_desired,
            last_trigger=self._last_ramp_source,
        )
        now = time.time()
        drifts: List[dict] = []

        for item in snap.get("drifts", []):
            key = item.get("light") or item.get("relay")
            if not key:
                continue
            grace_key = f"relay:{key}" if "relay" in item else key
            commanded_at = self._commanded_at.get(grace_key, 0)
            if now - commanded_at < self._drift_grace_s:
                continue
            drifts.append(item)

        return drifts

    def report_hardware_drift(self) -> List[dict]:
        """Log and notify on drift changes. Returns current drift list."""
        drifts = self.check_hardware_drift()
        current = {
            (d.get("light") or d.get("relay")): d.get("detail", "")
            for d in drifts
        }

        cleared = [k for k in self._active_drifts if k not in current]
        for key in cleared:
            logger.info(f"✅ Drift cleared: {key}")
            del self._active_drifts[key]

        new_drifts: List[dict] = []
        for key, detail in current.items():
            if self._active_drifts.get(key) != detail:
                self._active_drifts[key] = detail
                label = source_label("fallback")
                if self._last_desired and key in self._last_desired.light_sources:
                    label = source_label(self._last_desired.light_sources[key])
                logger.warning(
                    f"⚠️ Hardware drift · {key}: {detail} (desired via {label})"
                )
                for item in drifts:
                    item_key = item.get("light") or item.get("relay")
                    if item_key == key:
                        new_drifts.append(item)
                        break

        if new_drifts and self.on_drift:
            try:
                self.on_drift(new_drifts)
            except Exception as e:
                logger.debug(f"Drift callback error: {e}")

        return drifts

    def _apply_drift_grace_to_explain(self, snap: dict) -> dict:
        """Hide transient observed/desired gaps while a ramp is still in flight."""
        now = time.time()
        grace = self._drift_grace_s

        filtered_drifts: List[dict] = []
        for item in snap.get("drifts", []):
            key = item.get("light") or item.get("relay")
            if not key:
                continue
            grace_key = f"relay:{key}" if "relay" in item else key
            if now - self._commanded_at.get(grace_key, 0) < grace:
                continue
            filtered_drifts.append(item)

        snap["drifts"] = filtered_drifts
        snap["has_drift"] = len(filtered_drifts) > 0

        for light, entry in snap.get("lights", {}).items():
            if not entry.get("drift"):
                entry.pop("ramping", None)
                continue
            commanded_at = self._commanded_at.get(light, 0)
            if now - commanded_at < grace:
                entry["drift"] = False
                entry["ramping"] = True
                entry.pop("drift_detail", None)
            else:
                entry.pop("ramping", None)

        for relay, entry in snap.get("relays", {}).items():
            if not entry.get("drift"):
                continue
            grace_key = f"relay:{relay}"
            if now - self._commanded_at.get(grace_key, 0) < grace:
                entry["drift"] = False
                entry["ramping"] = True

        return snap

    def explain_snapshot(self) -> dict:
        world = self.world.snapshot()
        desired = self._last_desired or desired_outputs(world, self.cfg)
        snap = build_explain_snapshot(
            world,
            self.cfg,
            desired,
            last_trigger=self._last_ramp_source,
        )
        return self._apply_drift_grace_to_explain(snap)