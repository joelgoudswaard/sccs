"""Push SCCS state into HAP characteristics."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from modules.homekit.accessories import (
    BatteryAccessory,
    LightAccessory,
    ReedAccessory,
    RelayAccessory,
    TemperatureAccessory,
    WaterAccessory,
)
from modules.homekit.mapping import (
    KIND_BATTERY,
    KIND_LIGHT,
    KIND_REED,
    KIND_RELAY_LIGHT,
    KIND_RELAY_SWITCH,
    KIND_TEMPERATURE,
    KIND_WATER,
    AccessorySpec,
)


@dataclass
class AccessoryIndex:
    lights: dict[str, LightAccessory] = field(default_factory=dict)
    relays: dict[str, RelayAccessory] = field(default_factory=dict)
    reeds: dict[str, ReedAccessory] = field(default_factory=dict)
    temps: dict[str, TemperatureAccessory] = field(default_factory=dict)
    water: Optional[WaterAccessory] = None
    battery: Optional[BatteryAccessory] = None
    specs: list[AccessorySpec] = field(default_factory=list)

    def register(self, spec: AccessorySpec, accessory) -> None:
        self.specs.append(spec)
        if spec.kind == KIND_LIGHT:
            self.lights[spec.entity] = accessory
        elif spec.kind in (KIND_RELAY_LIGHT, KIND_RELAY_SWITCH):
            self.relays[spec.entity] = accessory
        elif spec.kind == KIND_REED:
            self.reeds[spec.entity] = accessory
        elif spec.kind == KIND_TEMPERATURE:
            self.temps[spec.entity] = accessory
        elif spec.kind == KIND_WATER:
            self.water = accessory
        elif spec.kind == KIND_BATTERY:
            self.battery = accessory

    def apply_ui_state(self, state: dict) -> None:
        if not state:
            return
        for name, acc in self.lights.items():
            if name not in state:
                continue
            acc.apply_state(state.get(name), state.get(f"{name}_mode"))
        for name, acc in self.relays.items():
            if name not in state:
                continue
            acc.apply_state(state.get(name))

    def apply_reeds(self, states: dict) -> None:
        if not states:
            return
        for name, acc in self.reeds.items():
            if name in states:
                acc.apply_state(states[name])

    def apply_sensors(self, data: dict) -> None:
        if not data:
            return
        for acc in self.temps.values():
            field = acc.spec.sensor_field
            if field and field in data:
                acc.apply_state(data.get(field))
        if self.water is not None:
            self.water.apply_state(data.get("water_percent"))

    def apply_power(self, data: dict) -> None:
        if not data or self.battery is None:
            return
        self.battery.apply_state(
            data.get("soc"),
            current_a=data.get("battery_current", data.get("current_a")),
            charge_state=data.get("charge_state"),
        )
