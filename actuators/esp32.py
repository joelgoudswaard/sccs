from __future__ import annotations

import logging
from typing import Dict, Optional, Tuple

from modules.esp32 import Esp32Manager, brightness_to_pwm
from modules.esp_pins import parse_light_pin

logger = logging.getLogger("sccs")


class Esp32Actuator:
    """Thin wrapper around Esp32Manager for the reconciler."""

    def __init__(self, esp32: Esp32Manager, compiled):
        self._esp32 = esp32
        self._cfg = compiled

    def set_light(
        self,
        name: str,
        brightness: int,
        mode: Optional[str],
        ramp_ms: int,
        *,
        source: str = "",
        trigger: str = "",
    ):
        from engine.explain import format_light_command

        if not self._esp32.is_connected():
            return

        if name in self._cfg.rgb_lights:
            self._esp32.set_rgb_bug_light(name, brightness, mode or "white", ramp_ms)
        elif name in self._cfg.pwm_lights:
            esp_id, gpio = parse_light_pin(self._cfg.pwm_lights[name])
            pwm = brightness_to_pwm(brightness)
            self._esp32.send_command(f"RAMP {gpio} {pwm} {ramp_ms}", esp=esp_id)
        else:
            return

        logger.info(
            format_light_command(
                name, brightness, mode, source or "fallback", trigger, ramp_ms
            )
        )

    def read_lights(self) -> Tuple[Dict[str, int], Dict[str, str]]:
        self._esp32.read_all_states()
        lights: Dict[str, int] = {}
        modes: Dict[str, str] = {}
        for name in self._cfg.light_names:
            if name in self._esp32.state:
                lights[name] = self._esp32.state[name]
            elif name in self._cfg.pwm_lights:
                lights[name] = 0
            if name in self._cfg.rgb_lights:
                modes[name] = self._esp32.state.get(f"{name}_mode", "white")
        return lights, modes
