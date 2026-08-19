from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal, Optional

IntentExpiry = Literal["until_reed_close", "until_phase_change", "until_scene_clear", "manual"]


@dataclass
class LightIntent:
    brightness: int
    mode: Optional[str] = None
    expires: IntentExpiry = "until_reed_close"
    set_at: float = 0.0

    def __post_init__(self):
        self.brightness = max(0, min(100, int(self.brightness)))
        if self.mode:
            self.mode = self.mode.lower()
        if not self.set_at:
            self.set_at = time.time()


@dataclass
class RelayIntent:
    on: bool
    expires: IntentExpiry = "manual"
    set_at: float = 0.0

    def __post_init__(self):
        if not self.set_at:
            self.set_at = time.time()
