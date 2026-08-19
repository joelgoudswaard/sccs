from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from .config_compile import CompiledConfig, Level
from .world import WorldState

LightLevel = Tuple[int, str]  # brightness, mode


@dataclass(frozen=True)
class ResolvedLight:
    brightness: int
    mode: str = "white"
    source: str = "fallback"

    def clamped(self) -> "ResolvedLight":
        b = max(0, min(100, int(self.brightness)))
        m = (self.mode or "white").lower()
        return ResolvedLight(b, m, self.source)


def _phase_key(world: WorldState) -> Optional[str]:
    p = str(world.phase_forced or world.phase or "").strip().lower()
    if not p:
        return None
    return p if p in ("day", "evening", "night") else "evening"


def reed_raw_closed(world: WorldState, reed: str) -> bool:
    """True when this reed is physically closed or operator-forced closed (ignoring interlock)."""
    if reed in world.reed_forces:
        return world.reed_forces[reed]
    return world.reeds.get(reed, True)


def effective_reed_closed(world: WorldState, reed: str, cfg: CompiledConfig) -> bool:
    """True when a reed is latched closed, forced closed, or interlocked closed."""
    if reed_raw_closed(world, reed):
        return True
    for required in cfg.interlocks.get(reed, []):
        if effective_reed_closed(world, required, cfg):
            return True
    return False


def reed_off_source(world: WorldState, reed: str, cfg: CompiledConfig) -> str:
    """Policy source when a reed-linked light is held off."""
    if reed_raw_closed(world, reed):
        return "reed_closed"
    if effective_reed_closed(world, reed, cfg):
        return "reed_interlocked"
    return "fallback"


def any_reed_open(world: WorldState, cfg: CompiledConfig) -> bool:
    return any(not effective_reed_closed(world, r, cfg) for r in cfg.reed_names)


def _get_phase_level(light: str, phase: str, cfg: CompiledConfig) -> Optional[Level]:
    key = phase.strip().lower()
    if light in cfg.reed_phase_levels and key in cfg.reed_phase_levels[light]:
        return cfg.reed_phase_levels[light][key]
    if light in cfg.ambient_phase_levels and key in cfg.ambient_phase_levels[light]:
        return cfg.ambient_phase_levels[light][key]
    return None


def _scene_level(light: str, setting: dict, cfg: CompiledConfig) -> Optional[LightLevel]:
    if setting.get("type") == "phase":
        lvl = _get_phase_level(light, setting["phase"], cfg)
        if lvl is None:
            return None
        return lvl[0], setting.get("forced_mode") or lvl[1]
    return setting["brightness"], setting.get("mode", "white")


def _scene_applies(light: str, world: WorldState, cfg: CompiledConfig) -> bool:
    if light in cfg.ambient_lights:
        return any_reed_open(world, cfg)
    reed = cfg.light_to_reed.get(light)
    if reed:
        return not effective_reed_closed(world, reed, cfg)
    return True


def _automation_default(light: str, world: WorldState, cfg: CompiledConfig) -> ResolvedLight:
    phase = _phase_key(world)
    if phase is None:
        return ResolvedLight(0, "white", "phase_pending")

    if light in cfg.ambient_lights:
        if not any_reed_open(world, cfg):
            if cfg.all_closed_action == "dim":
                lvl = _get_phase_level(light, "night", cfg)
                if lvl:
                    return ResolvedLight(lvl[0], lvl[1], "automation_all_closed_dim")
            return ResolvedLight(0, "white", "automation_all_closed")
        lvl = _get_phase_level(light, phase, cfg)
        if lvl:
            return ResolvedLight(lvl[0], lvl[1], "automation_ambient")
        return ResolvedLight(0, "white", "automation_ambient")

    reed = cfg.light_to_reed.get(light)
    if reed:
        lvl = _get_phase_level(light, phase, cfg)
        if lvl:
            return ResolvedLight(lvl[0], lvl[1], "automation_reed")
        # No [reed_phases] entry for this phase → do not command a level.
        return ResolvedLight(0, "white", "automation_omit")

    return ResolvedLight(0, "white", "fallback")


def _scene_resolve(light: str, world: WorldState, cfg: CompiledConfig) -> Optional[ResolvedLight]:
    if not world.active_scene:
        return None
    scene = cfg.scenes.get(world.active_scene, {})
    if not scene:
        return None

    if scene.get("all_off"):
        setting = scene.get("lights", {}).get(light)
        if setting:
            sl = _scene_level(light, setting, cfg)
            if sl:
                return ResolvedLight(sl[0], sl[1], "scene")
        return ResolvedLight(0, "white", "scene_all_off")

    phase_target = None
    if scene.get("evening_levels"):
        phase_target = "evening"
    elif scene.get("night_levels"):
        phase_target = "night"
    elif scene.get("day_levels"):
        phase_target = "day"

    if not _scene_applies(light, world, cfg):
        return None

    setting = scene.get("lights", {}).get(light)
    if setting:
        sl = _scene_level(light, setting, cfg)
        if sl:
            return ResolvedLight(sl[0], sl[1], "scene")

    if phase_target:
        lvl = _get_phase_level(light, phase_target, cfg)
        if lvl:
            return ResolvedLight(lvl[0], lvl[1], f"scene_{phase_target}")

    return None


def is_scene_source(source: str) -> bool:
    """True when a light level came from the active scene (incl. all_off, phase presets)."""
    return source.startswith("scene")


def _safety_clamp(light: str, resolved: ResolvedLight, world: WorldState, cfg: CompiledConfig) -> ResolvedLight:
    if light == "rooftop_tent" and resolved.brightness > 0:
        reed = cfg.light_to_reed.get("rooftop_tent", "rooftop_tent")
        if effective_reed_closed(world, reed, cfg):
            return ResolvedLight(0, "white", "safety_rooftop")
    return resolved


def resolve_light(light: str, world: WorldState, cfg: CompiledConfig) -> ResolvedLight:
    """Explicit precedence stack (highest wins first).

    Slider intents clear on phase change, reed transition, or operator reed force.

    1. User intent — UI levels while active
    2. Reed closed — reed-linked automation off (includes operator force)
    3. Active scene — while no phase/reed/manual override
    4. Automation — ambient / reed phase tables
    5. Fallback — off
    6. Safety clamp — rooftop tent cannot be on when reed closed (hard guard)
    """
    reed = cfg.light_to_reed.get(light)

    # 1. User intent (safety clamp still applies — e.g. rooftop tent)
    intent = world.light_intents.get(light)
    if intent is not None:
        resolved = ResolvedLight(intent.brightness, intent.mode or "white", "user_intent").clamped()
        return _safety_clamp(light, resolved, world, cfg)

    # 2. Reed closed or interlocked
    if reed and effective_reed_closed(world, reed, cfg):
        return ResolvedLight(0, "white", reed_off_source(world, reed, cfg))

    # 3. Scene
    scene_result = _scene_resolve(light, world, cfg)
    if scene_result is not None:
        return _safety_clamp(light, scene_result.clamped(), world, cfg)

    # 4. Automation
    auto = _automation_default(light, world, cfg)

    # 5–6. Fallback + safety
    return _safety_clamp(light, auto.clamped(), world, cfg)


def resolve_screen(screen: dict, world: WorldState, cfg: CompiledConfig) -> int:
    """Brightness percent (0–100) when the linked reed is open; 0 when closed.

    Manual UI wake/sleep is one-shot hardware only — no sticky intents.
    """
    linked_reed = screen.get("linked_reed", "")
    if linked_reed and effective_reed_closed(world, linked_reed, cfg):
        return 0
    phase = _phase_key(world)
    if phase is None:
        return 100  # no phase yet — allow awake rather than forced sleep
    levels = screen.get("phase_brightness") or {}
    return max(0, min(100, int(levels.get(phase, 100))))