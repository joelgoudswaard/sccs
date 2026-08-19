from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from .config_compile import CompiledConfig
from .precedence import ResolvedLight, resolve_light, resolve_screen
from .world import WorldState

LightOutput = Tuple[int, Optional[str]]


@dataclass
class DesiredOutputs:
    lights: Dict[str, LightOutput] = field(default_factory=dict)
    light_modes: Dict[str, str] = field(default_factory=dict)
    light_sources: Dict[str, str] = field(default_factory=dict)
    relays: Dict[str, bool] = field(default_factory=dict)
    screens: Dict[str, int] = field(default_factory=dict)
    ramp_source: str = "auto"


def desired_outputs(world: WorldState, cfg: CompiledConfig) -> DesiredOutputs:
    out = DesiredOutputs()

    for light in cfg.light_names:
        resolved: ResolvedLight = resolve_light(light, world, cfg)
        if resolved.source == "automation_omit":
            # Hold current hardware/UI level; reconciler will not re-command.
            brightness = world.observed_lights.get(light)
            if brightness is None:
                brightness = 0
            mode = world.observed_light_modes.get(light, "white")
            out.lights[light] = (int(brightness), mode)
            out.light_sources[light] = "automation_omit"
            if light in cfg.rgb_lights:
                out.light_modes[light] = mode
            continue

        out.lights[light] = (resolved.brightness, resolved.mode)
        out.light_sources[light] = resolved.source
        if light in cfg.rgb_lights:
            out.light_modes[light] = resolved.mode

    for relay in cfg.relay_names:
        if relay in world.relay_intents:
            out.relays[relay] = world.relay_intents[relay].on
        else:
            out.relays[relay] = world.observed_relays.get(relay, False)

    for name, screen in cfg.screens.items():
        out.screens[name] = resolve_screen(screen, world, cfg)

    return out