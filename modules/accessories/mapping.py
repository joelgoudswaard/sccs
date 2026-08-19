"""Pure mapping from SCCS compiled config to voice-assistant accessory specs.

No HomeKit or Matter imports — unit-testable without those stacks.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Iterable, Optional

KIND_LIGHT = "light"
KIND_RELAY_LIGHT = "relay_light"
KIND_RELAY_SWITCH = "relay_switch"
KIND_SCENE = "scene"
KIND_REED = "reed"
KIND_TEMPERATURE = "temperature"
KIND_WATER = "water"
KIND_BATTERY = "battery"

# AID 1 is the bridge. HAP-python skips 7 (unsupported by some iOS versions).
_FORBIDDEN_AIDS = {1, 7}

_LIGHT_HINT = re.compile(r"light|flood|lamp|\bled\b", re.IGNORECASE)
_LIGHT_ICONS = {"fa-lightbulb", "fa-sun", "fa-lantern", "fa-flashlight"}


@dataclass(frozen=True)
class AccessorySpec:
    key: str
    kind: str
    name: str
    entity: str
    aid: int
    has_brightness: bool = False
    has_bug_mode: bool = False
    sensor_field: str = ""


@dataclass(frozen=True)
class MapOptions:
    include_scenes: bool = True
    include_sensors: bool = True
    include_battery: bool = True
    fridge_configured: bool = False
    freezer_configured: bool = False
    outside_configured: bool = True
    victron_configured: bool = False


def relay_is_light(name: str, label: str = "", icon: str = "") -> bool:
    """True when a GPIO relay should appear as a HomeKit lightbulb."""
    if (icon or "").strip() in _LIGHT_ICONS:
        return True
    return bool(_LIGHT_HINT.search(f"{name} {label}"))


def light_target_from_chars(
    *,
    on: Optional[bool],
    brightness: Optional[int],
    last_nonzero: int,
) -> int:
    """Resolve a HomeKit On/Brightness write to an SCCS 0–100 level."""
    last = max(1, min(100, int(last_nonzero or 100)))
    if on is False:
        return 0
    if brightness is not None:
        try:
            level = int(brightness)
        except (TypeError, ValueError):
            level = last
        return max(0, min(100, level))
    if on is True:
        return last
    return last


def charging_state(current_a, charge_state) -> int:
    """HomeKit ChargingState: 0 not charging, 1 charging, 2 not chargeable."""
    try:
        if current_a is not None and float(current_a) > 0.05:
            return 1
    except (TypeError, ValueError):
        pass
    text = str(charge_state or "").lower()
    if any(token in text for token in ("bulk", "absorp", "float", "storage", "equaliz")):
        return 1
    return 0


def status_low_battery(soc, threshold: int = 20) -> int:
    """HomeKit StatusLowBattery: 0 normal, 1 low."""
    try:
        if soc is None:
            return 0
        return 1 if float(soc) <= float(threshold) else 0
    except (TypeError, ValueError):
        return 0


def _hash_aid(key: str) -> int:
    digest = hashlib.sha1(key.encode("utf-8")).digest()
    aid = int.from_bytes(digest[:4], "big") % 100000 + 2
    if aid in _FORBIDDEN_AIDS:
        aid = 8
    return aid


def assign_aids(keys: Iterable[str]) -> dict[str, int]:
    """Stable name → AID map. Collisions increment; 1 and 7 are never used."""
    used = set(_FORBIDDEN_AIDS)
    out: dict[str, int] = {}
    for key in keys:
        aid = _hash_aid(key)
        while aid in used:
            aid += 1
            if aid > 200000:
                aid = 2
        used.add(aid)
        out[key] = aid
    return out


def build_specs(
    compiled,
    *,
    reed_labels: Optional[dict[str, str]] = None,
    relay_icons: Optional[dict[str, str]] = None,
    options: Optional[MapOptions] = None,
) -> list[AccessorySpec]:
    """Build accessory specs from compiled SCCS config."""
    options = options or MapOptions()
    reed_labels = reed_labels or {}
    relay_icons = relay_icons or {}

    pending: list[tuple] = []

    for name in compiled.light_names:
        pending.append(
            (
                f"light:{name}",
                KIND_LIGHT,
                compiled.light_labels.get(name, name),
                name,
                True,
                name in compiled.rgb_lights,
                "",
            )
        )

    for name in compiled.relay_names:
        label = compiled.relay_labels.get(name, name)
        icon = relay_icons.get(name, "")
        kind = KIND_RELAY_LIGHT if relay_is_light(name, label, icon) else KIND_RELAY_SWITCH
        pending.append((f"relay:{name}", kind, label, name, False, False, ""))

    if options.include_scenes:
        scenes = sorted(
            compiled.scenes.items(),
            key=lambda item: (item[1].get("order", 999), item[0]),
        )
        for key, scene in scenes:
            pending.append(
                (
                    f"scene:{key}",
                    KIND_SCENE,
                    scene.get("name") or key,
                    key,
                    False,
                    False,
                    "",
                )
            )

    if options.include_sensors:
        for name in compiled.reed_names:
            pending.append(
                (
                    f"reed:{name}",
                    KIND_REED,
                    reed_labels.get(name) or name.replace("_", " ").title(),
                    name,
                    False,
                    False,
                    "",
                )
            )
        temps = []
        if options.outside_configured:
            temps.append(("outside", "Outside", "outside_temp_c"))
        if options.fridge_configured:
            temps.append(("fridge", "Fridge", "fridge_temp_c"))
        if options.freezer_configured:
            temps.append(("freezer", "Freezer", "freezer_temp_c"))
        for entity, label, field in temps:
            pending.append(
                (f"temp:{entity}", KIND_TEMPERATURE, label, entity, False, False, field)
            )
        pending.append(
            ("water:tank", KIND_WATER, "Water Tank", "water", False, False, "water_percent")
        )

    if options.include_battery and options.victron_configured:
        pending.append(
            ("battery:house", KIND_BATTERY, "House Battery", "battery", False, False, "soc")
        )

    aids = assign_aids(item[0] for item in pending)
    specs = []
    for key, kind, name, entity, has_bri, has_bug, field in pending:
        specs.append(
            AccessorySpec(
                key=key,
                kind=kind,
                name=name,
                entity=entity,
                aid=aids[key],
                has_brightness=has_bri,
                has_bug_mode=has_bug,
                sensor_field=field,
            )
        )
    return specs
