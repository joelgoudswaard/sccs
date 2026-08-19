"""ESP silkscreen pin IDs: \"{esp}-{gpio}\" e.g. 1-4 = ESP32-1 module GPIO 4."""

from __future__ import annotations

from typing import Tuple, Union

PinTarget = Tuple[int, int]  # (esp_id 1-based, module_gpio)


def parse_light_pin(token: Union[str, int, PinTarget]) -> PinTarget:
    """Parse a light pin token into (esp_id, module_gpio).

    Accepts silkscreen form ``1-4``, bare integers (ESP32-1),
    or an already-parsed ``(esp_id, gpio)`` tuple.
    """
    if isinstance(token, tuple) and len(token) == 2:
        return int(token[0]), int(token[1])
    if isinstance(token, int):
        return 1, int(token)
    raw = str(token).strip()
    if not raw:
        raise ValueError("empty pin token")
    if "-" in raw:
        esp_s, gpio_s = raw.split("-", 1)
        esp_id = int(esp_s.strip())
        gpio = int(gpio_s.strip())
        if esp_id < 1:
            raise ValueError(f"invalid ESP id in pin {raw!r}")
        return esp_id, gpio
    return 1, int(raw)


def format_light_pin(target: PinTarget) -> str:
    esp_id, gpio = target
    return f"{esp_id}-{gpio}"
