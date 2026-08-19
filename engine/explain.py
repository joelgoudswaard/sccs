"""Human-readable explanations for policy decisions and hardware state."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .config_compile import CompiledConfig
from .policy import DesiredOutputs, desired_outputs
from .precedence import effective_reed_closed
from .world import WorldState

# Internal source key → log/diag label
SOURCE_LABELS: Dict[str, str] = {
    "reed_closed": "reed closed",
    "reed_interlocked": "interlocked",
    "user_intent": "user set level",
    "user_reed_force": "forced reed · phase level",
    "automation_reed": "reed open · phase level",
    "automation_omit": "reed open · no phase level",
    "automation_ambient": "panel open · ambient on",
    "automation_all_closed": "all panels closed",
    "automation_all_closed_dim": "all panels closed · dim accent",
    "scene": "active scene",
    "scene_all_off": "scene · all off",
    "scene_evening": "scene · evening levels",
    "scene_night": "scene · night levels",
    "scene_day": "scene · day levels",
    "safety_rooftop": "safety · rooftop tent closed",
    "fallback": "default off",
    "phase_pending": "waiting for phase",
}

DRIFT_BRIGHTNESS_TOLERANCE = 8


def source_label(source: str) -> str:
    if source in SOURCE_LABELS:
        return SOURCE_LABELS[source]
    if source.startswith("scene_"):
        return f"scene · {source[6:].replace('_', ' ')}"
    return source.replace("_", " ")


def format_light_command(
    name: str,
    brightness: int,
    mode: Optional[str],
    source: str,
    trigger: str,
    ramp_ms: int,
) -> str:
    """Single INFO log line when a light is commanded."""
    why = source_label(source)
    trigger_part = f" · trigger:{trigger}" if trigger else ""
    if mode and mode != "white":
        return f"💡 {name} → {brightness}% {mode} · {why}{trigger_part} [{ramp_ms}ms]"
    return f"💡 {name} → {brightness}% · {why}{trigger_part} [{ramp_ms}ms]"


def format_relay_command(name: str, on: bool, source: str, trigger: str) -> str:
    state = "ON" if on else "OFF"
    why = source_label(source) if source in SOURCE_LABELS else source.replace("_", " ")
    trigger_part = f" · trigger:{trigger}" if trigger else ""
    return f"💡 {name} → {state} · {why}{trigger_part}"


def _reed_open_label(closed: bool) -> str:
    return "closed" if closed else "open"


def build_explain_snapshot(
    world: WorldState,
    cfg: CompiledConfig,
    desired: Optional[DesiredOutputs] = None,
    *,
    last_trigger: str = "unknown",
    drift_tolerance: int = DRIFT_BRIGHTNESS_TOLERANCE,
) -> dict:
    """Full decision snapshot for /api/explain and diagnostics."""
    desired = desired or desired_outputs(world, cfg)
    lights_out: Dict[str, Any] = {}
    drifts: List[dict] = []

    for light in cfg.light_names:
        target_b, target_m = desired.lights.get(light, (0, "white"))
        target_m = target_m or "white"
        source = desired.light_sources.get(light, "fallback")
        reed = cfg.light_to_reed.get(light)
        if (
            reed
            and reed in world.reed_forces
            and not world.reed_forces[reed]
            and source in ("automation_reed", "automation_ambient")
        ):
            source = "user_reed_force"
        observed_b = world.observed_lights.get(light)
        observed_m = world.observed_light_modes.get(light, "white")
        intent = world.light_intents.get(light)

        drift = False
        drift_detail = None
        if observed_b is not None:
            if abs(int(observed_b) - int(target_b)) > drift_tolerance:
                drift = True
                drift_detail = f"observed {observed_b}% vs desired {target_b}%"
            elif (
                light in cfg.rgb_lights
                and observed_m != target_m
                # Mode is not observable when either channel set is fully off
                # (both PWMs read 0 → reader always reports white).
                and int(target_b) > 0
                and int(observed_b) > 0
            ):
                drift = True
                drift_detail = f"observed mode {observed_m} vs desired {target_m}"

        label = cfg.light_labels.get(light, light)

        if drift:
            drifts.append({"light": light, "label": label, "detail": drift_detail})

        entry: Dict[str, Any] = {
            "label": label,
            "desired_brightness": target_b,
            "desired_mode": target_m,
            "observed_brightness": observed_b,
            "observed_mode": observed_m if light in cfg.rgb_lights else None,
            "source": source,
            "source_label": source_label(source),
            "drift": drift,
        }
        if drift_detail:
            entry["drift_detail"] = drift_detail
        if intent is not None:
            entry["intent"] = {
                "brightness": intent.brightness,
                "mode": intent.mode,
                "expires": intent.expires,
            }
        if reed:
            entry["linked_reed"] = reed
            entry["reed_effective_closed"] = effective_reed_closed(world, reed, cfg)
            entry["reed_hardware_closed"] = world.reeds.get(reed, True)
            if reed in world.reed_forces:
                entry["reed_forced_closed"] = world.reed_forces[reed]
        lights_out[light] = entry

    reeds_effective = {
        r: effective_reed_closed(world, r, cfg) for r in cfg.reed_names
    }
    reeds_hardware = {r: world.reeds.get(r, True) for r in cfg.reed_names}

    relays_out = {}
    for relay in cfg.relay_names:
        target = desired.relays.get(relay, False)
        observed = world.observed_relays.get(relay)
        rdrift = observed is not None and bool(observed) != bool(target)
        label = cfg.relay_labels.get(relay, relay)

        if rdrift:
            drifts.append({
                "relay": relay,
                "label": label,
                "detail": f"observed {'ON' if observed else 'OFF'} vs desired {'ON' if target else 'OFF'}",
            })
        relays_out[relay] = {
            "label": label,
            "desired": target,
            "observed": observed,
            "drift": rdrift,
            "intent": world.relay_intents.get(relay).on if relay in world.relay_intents else None,
        }

    return {
        "phase": world.phase,
        "phase_forced": world.phase_forced,
        "active_scene": world.active_scene,
        "last_reconcile_trigger": last_trigger,
        "reeds": {
            "hardware_closed": reeds_hardware,
            "effective_closed": reeds_effective,
            "forced": dict(world.reed_forces),
        },
        "lights": lights_out,
        "relays": relays_out,
        "drifts": drifts,
        "has_drift": len(drifts) > 0,
    }