"""HomeKit mapping — re-exports the shared accessory map."""

from modules.accessories.mapping import *  # noqa: F403
from modules.accessories.mapping import (  # noqa: F401
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
