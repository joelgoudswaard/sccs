"""HAP accessory subclasses. Setters call SCCSRuntime — never hardware."""

from __future__ import annotations

import logging
import threading
from typing import Optional

from pyhap.accessory import Accessory
from pyhap.const import (
    CATEGORY_LIGHTBULB,
    CATEGORY_SENSOR,
    CATEGORY_SWITCH,
)

from modules.homekit.mapping import light_target_from_chars
from modules.version import APP_VERSION

logger = logging.getLogger("sccs")

_MANUFACTURER = "SCCS"


def _info(acc: Accessory, spec, model: str) -> None:
    acc.set_info_service(
        firmware_revision=APP_VERSION,
        manufacturer=_MANUFACTURER,
        model=model,
        serial_number=spec.key,
    )


class LightAccessory(Accessory):
    category = CATEGORY_LIGHTBULB

    def __init__(self, driver, spec, runtime):
        super().__init__(driver, spec.name, aid=spec.aid)
        self.spec = spec
        self.runtime = runtime
        self._last_nonzero = 100
        extras = ["Brightness"] if spec.has_brightness else []
        serv = self.add_preload_service("Lightbulb", chars=extras)
        self.char_on = serv.configure_char("On", value=False)
        self.char_brightness = None
        if spec.has_brightness:
            self.char_brightness = serv.configure_char("Brightness", value=100)
        serv.setter_callback = self._set_light
        self.char_bug = None
        if spec.has_bug_mode:
            bug = self.add_preload_service("Switch", chars=["Name"])
            bug.configure_char("Name", value="Bug Mode")
            self.char_bug = bug.configure_char("On", value=False, setter_callback=self._set_bug)
        _info(self, spec, "RGB light" if spec.has_bug_mode else "PWM light")

    def _current_brightness(self) -> int:
        if self.char_brightness is None:
            return 100 if self.char_on.value else 0
        return int(self.char_brightness.value or 0)

    def _set_light(self, char_values):
        on = char_values.get("On")
        brightness = char_values.get("Brightness")
        if self.spec.has_brightness:
            target = light_target_from_chars(
                on=on,
                brightness=brightness,
                last_nonzero=self._last_nonzero,
            )
        else:
            target = 100 if on else 0
            if brightness is not None and on is None:
                target = 100 if int(brightness) > 0 else 0
        if target > 0:
            self._last_nonzero = target
        mode = None
        if self.spec.has_bug_mode and self.char_bug is not None:
            mode = "red" if self.char_bug.value else "white"
        try:
            self.runtime.set_light_intent(self.spec.entity, target, mode)
        except Exception:
            logger.exception("HomeKit light write failed: %s", self.spec.entity)

    def _set_bug(self, value):
        mode = "red" if value else "white"
        brightness = self._current_brightness()
        if brightness <= 0:
            brightness = self._last_nonzero
        try:
            self.runtime.set_light_intent(self.spec.entity, brightness, mode)
        except Exception:
            logger.exception("HomeKit bug-mode write failed: %s", self.spec.entity)

    def apply_state(self, brightness, mode: Optional[str] = None) -> None:
        try:
            level = max(0, min(100, int(brightness)))
        except (TypeError, ValueError):
            return
        self.char_on.set_value(level > 0)
        if level > 0:
            self._last_nonzero = level
            if self.char_brightness is not None:
                self.char_brightness.set_value(level)
        if self.char_bug is not None and mode is not None:
            self.char_bug.set_value(str(mode).lower() == "red")


class RelayAccessory(Accessory):
    def __init__(self, driver, spec, runtime):
        super().__init__(driver, spec.name, aid=spec.aid)
        self.spec = spec
        self.runtime = runtime
        if spec.kind == "relay_light":
            self.category = CATEGORY_LIGHTBULB
            serv = self.add_preload_service("Lightbulb")
            model = "Relay light"
        else:
            self.category = CATEGORY_SWITCH
            serv = self.add_preload_service("Switch")
            model = "Relay"
        self.char_on = serv.configure_char("On", value=False, setter_callback=self._set_on)
        _info(self, spec, model)

    def _set_on(self, value):
        try:
            self.runtime.set_relay_intent(self.spec.entity, bool(value))
        except Exception:
            logger.exception("HomeKit relay write failed: %s", self.spec.entity)

    def apply_state(self, on) -> None:
        self.char_on.set_value(bool(on))


class SceneAccessory(Accessory):
    category = CATEGORY_SWITCH

    def __init__(self, driver, spec, runtime):
        super().__init__(driver, spec.name, aid=spec.aid)
        self.spec = spec
        self.runtime = runtime
        serv = self.add_preload_service("Switch")
        self.char_on = serv.configure_char("On", value=False, setter_callback=self._set_on)
        _info(self, spec, "Scene")

    def _set_on(self, value):
        if not value:
            return
        try:
            self.runtime.set_scene(self.spec.entity)
        except Exception:
            logger.exception("HomeKit scene write failed: %s", self.spec.entity)
        timer = threading.Timer(0.35, lambda: self.char_on.set_value(False))
        timer.daemon = True
        timer.start()


class ReedAccessory(Accessory):
    category = CATEGORY_SENSOR

    def __init__(self, driver, spec, runtime=None):
        super().__init__(driver, spec.name, aid=spec.aid)
        self.spec = spec
        serv = self.add_preload_service("ContactSensor")
        # 0 = contact detected (closed), 1 = not detected (open)
        self.char_state = serv.configure_char("ContactSensorState", value=0)
        _info(self, spec, "Reed")

    def apply_state(self, closed) -> None:
        self.char_state.set_value(0 if closed else 1)


class TemperatureAccessory(Accessory):
    category = CATEGORY_SENSOR

    def __init__(self, driver, spec, runtime=None):
        super().__init__(driver, spec.name, aid=spec.aid)
        self.spec = spec
        serv = self.add_preload_service("TemperatureSensor")
        self.char_temp = serv.configure_char("CurrentTemperature", value=0)
        _info(self, spec, "Temperature")

    def apply_state(self, celsius) -> None:
        if celsius is None:
            return
        try:
            value = max(-270.0, min(100.0, float(celsius)))
        except (TypeError, ValueError):
            return
        self.char_temp.set_value(value)


class WaterAccessory(Accessory):
    category = CATEGORY_SENSOR

    def __init__(self, driver, spec, runtime=None):
        super().__init__(driver, spec.name, aid=spec.aid)
        self.spec = spec
        serv = self.add_preload_service("HumiditySensor")
        self.char_level = serv.configure_char("CurrentRelativeHumidity", value=0)
        _info(self, spec, "Water tank")

    def apply_state(self, percent) -> None:
        if percent is None:
            return
        try:
            value = max(0.0, min(100.0, float(percent)))
        except (TypeError, ValueError):
            return
        self.char_level.set_value(value)


class BatteryAccessory(Accessory):
    category = CATEGORY_SENSOR

    def __init__(self, driver, spec, runtime=None, low_percent: int = 20):
        super().__init__(driver, spec.name, aid=spec.aid)
        self.spec = spec
        self.low_percent = low_percent
        serv = self.add_preload_service("BatteryService")
        self.char_level = serv.configure_char("BatteryLevel", value=100)
        self.char_charging = serv.configure_char("ChargingState", value=0)
        self.char_low = serv.configure_char("StatusLowBattery", value=0)
        _info(self, spec, "House battery")

    def apply_state(self, soc, current_a=None, charge_state=None) -> None:
        from modules.homekit.mapping import charging_state, status_low_battery

        if soc is not None:
            try:
                self.char_level.set_value(max(0, min(100, int(round(float(soc))))))
            except (TypeError, ValueError):
                pass
        self.char_charging.set_value(charging_state(current_a, charge_state))
        self.char_low.set_value(status_low_battery(soc, self.low_percent))
