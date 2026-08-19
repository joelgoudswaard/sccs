"""Shared accessory mapping for HomeKit and Matter."""

from modules.accessories.mapping import (
    KIND_BATTERY,
    KIND_LIGHT,
    KIND_REED,
    KIND_RELAY_LIGHT,
    KIND_RELAY_SWITCH,
    KIND_SCENE,
    KIND_TEMPERATURE,
    KIND_WATER,
    AccessorySpec,
    MapOptions,
    assign_aids,
    build_specs,
    charging_state,
    light_target_from_chars,
    relay_is_light,
    status_low_battery,
)

__all__ = [
    "KIND_BATTERY",
    "KIND_LIGHT",
    "KIND_REED",
    "KIND_RELAY_LIGHT",
    "KIND_RELAY_SWITCH",
    "KIND_SCENE",
    "KIND_TEMPERATURE",
    "KIND_WATER",
    "AccessorySpec",
    "MapOptions",
    "assign_aids",
    "build_specs",
    "charging_state",
    "light_target_from_chars",
    "relay_is_light",
    "status_low_battery",
]
