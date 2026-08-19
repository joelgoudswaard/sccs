from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Dict, List, Optional

logger = logging.getLogger("sccs")


class ReedInput:
    """Poll GPIO reeds, debounce at input boundary, publish stable state to WorldStore."""

    def __init__(
        self,
        gpio_manager,
        reed_names: List[str],
        debounce_ms: int,
        on_update: Callable[[Dict[str, bool], List[str]], None],
        poll_interval_s: float = 0.2,
        stable_polls: int = 3,
    ):
        self._gpio = gpio_manager
        self._reed_names = reed_names
        self._debounce_ms = debounce_ms
        self._stable_polls = max(1, stable_polls)
        self._on_update = on_update
        self._poll_interval = poll_interval_s
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_change: Dict[str, float] = {}
        self._candidate: Dict[str, bool] = {}
        self._candidate_count: Dict[str, int] = {}
        self._stable: Dict[str, bool] = {
            n: gpio_manager.reed_states.get(n, True) for n in reed_names
        }

    def prime(self, interval_s: float = 0.05) -> Dict[str, bool]:
        """Blocking stable sample. Call before the first lighting pass."""
        needed = self._stable_polls
        counts: Dict[str, int] = {n: 0 for n in self._reed_names}
        last: Dict[str, Optional[bool]] = {n: None for n in self._reed_names}
        attempts = max(needed, needed * 4)
        for _ in range(attempts):
            settled = True
            for name in self._reed_names:
                button = self._gpio.reeds.get(name)
                if button is None:
                    counts[name] = needed
                    continue
                current = bool(button.is_pressed)
                self._gpio.reed_states[name] = current
                if last[name] == current:
                    counts[name] += 1
                else:
                    last[name] = current
                    counts[name] = 1
                if counts[name] < needed:
                    settled = False
            if settled:
                break
            time.sleep(max(0.01, interval_s))
        for name in self._reed_names:
            if last[name] is not None:
                self._stable[name] = last[name]
                self._gpio.reed_states[name] = last[name]
        return dict(self._stable)

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="ReedInput")
        self._thread.start()
        logger.debug(f"ReedInput started ({len(self._reed_names)} reeds)")

    def stop(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None

    def resync(self) -> List[str]:
        """Force hardware resample; return reeds whose stable state changed."""
        changed = []
        for name in self._reed_names:
            button = self._gpio.reeds.get(name)
            if button is None:
                continue
            current = bool(button.is_pressed)
            self._gpio.reed_states[name] = current
            if self._stable.get(name) != current:
                self._stable[name] = current
                changed.append(name)
        if changed:
            self._on_update(dict(self._stable), changed)
        return changed

    def _sample_reed(self, name: str, current: bool, now: float) -> Optional[bool]:
        """Return a new stable value when the reading has settled, else None."""
        stable = self._stable.get(name)
        if current == stable:
            self._candidate.pop(name, None)
            self._candidate_count.pop(name, None)
            return None

        if self._candidate.get(name) == current:
            self._candidate_count[name] = self._candidate_count.get(name, 0) + 1
        else:
            self._candidate[name] = current
            self._candidate_count[name] = 1

        if self._candidate_count[name] < self._stable_polls:
            return None

        last = self._last_change.get(name, 0)
        if (now - last) * 1000 < self._debounce_ms:
            return None

        self._candidate.pop(name, None)
        self._candidate_count.pop(name, None)
        self._last_change[name] = now
        return current

    def _loop(self):
        while self._running:
            try:
                pending: Dict[str, bool] = {}
                now = time.time()
                for name in self._reed_names:
                    button = self._gpio.reeds.get(name)
                    if button is None:
                        continue
                    current = bool(button.is_pressed)
                    self._gpio.reed_states[name] = current
                    accepted = self._sample_reed(name, current, now)
                    if accepted is not None:
                        pending[name] = accepted
                if pending:
                    transitioned = list(pending.keys())
                    for name, val in pending.items():
                        self._stable[name] = val
                        action = "CLOSED" if val else "OPEN"
                        logger.info(f"🚪 Reed {name} → {action}")
                    self._on_update(dict(self._stable), transitioned)
            except Exception as e:
                logger.debug(f"ReedInput loop error: {e}")
            time.sleep(self._poll_interval)