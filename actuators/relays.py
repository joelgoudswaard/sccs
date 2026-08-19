from __future__ import annotations

import logging
from typing import Dict

logger = logging.getLogger("sccs")


class RelayActuator:
    def __init__(self, gpio_manager, relay_names):
        self._gpio = gpio_manager
        self._names = relay_names
        self._observed: Dict[str, bool] = {n: False for n in relay_names}

    def set_relay(self, name: str, on: bool, *, source: str = "", trigger: str = ""):
        from engine.explain import format_relay_command

        device = self._gpio.get_relay(name)
        if not device:
            return
        try:
            if on:
                device.on()
            else:
                device.off()
            self._observed[name] = on
            logger.info(
                format_relay_command(
                    name, on, source or "hardware_default", trigger
                )
            )
        except Exception as e:
            logger.error(f"Relay {name} failed: {e}")

    def read_relays(self) -> Dict[str, bool]:
        observed: Dict[str, bool] = {}
        for name in self._names:
            device = self._gpio.get_relay(name)
            if device is not None:
                try:
                    observed[name] = bool(device.value)
                    self._observed[name] = observed[name]
                    continue
                except Exception as e:
                    logger.debug(f"Relay GPIO read {name}: {e}")
            observed[name] = self._observed.get(name, False)
        return observed