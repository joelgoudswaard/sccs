import unittest
from unittest.mock import MagicMock

from inputs.reeds import ReedInput


class _FakeGpio:
    def __init__(self, initial: dict[str, bool]):
        self.reed_states = dict(initial)
        self.reeds = {name: MagicMock(is_pressed=val) for name, val in initial.items()}


class TestReedDebounce(unittest.TestCase):
    def setUp(self):
        self.updates: list[tuple[dict[str, bool], list[str]]] = []
        self.gpio = _FakeGpio({"rooftop_tent": False})  # open
        self.reed_input = ReedInput(
            self.gpio,
            ["rooftop_tent"],
            debounce_ms=250,
            stable_polls=3,
            on_update=lambda states, changed: self.updates.append((states, changed)),
        )

    def _sample(self, pressed: bool, t: float = 1.0):
        return self.reed_input._sample_reed("rooftop_tent", pressed, t)

    def test_brief_glitch_is_ignored(self):
        self.assertIsNone(self._sample(True, 1.0))
        self.assertIsNone(self._sample(True, 1.2))
        self.assertIsNone(self._sample(False, 1.4))
        self.assertEqual(self.reed_input._stable["rooftop_tent"], False)

    def test_stable_close_is_accepted_after_three_polls(self):
        self.assertIsNone(self._sample(True, 1.0))
        self.assertIsNone(self._sample(True, 1.2))
        accepted = self._sample(True, 1.4)
        self.assertTrue(accepted)

    def test_flip_flop_within_debounce_window_is_held_off(self):
        self._sample(True, 1.0)
        self._sample(True, 1.2)
        accepted = self._sample(True, 1.4)
        self.assertTrue(accepted)
        self.reed_input._stable["rooftop_tent"] = True

        # Three stable open reads, but still inside the 250ms post-transition cooldown.
        self.assertIsNone(self._sample(False, 1.5))
        self.assertIsNone(self._sample(False, 1.55))
        self.assertIsNone(self._sample(False, 1.63))

        accepted = self._sample(False, 1.7)
        self.assertFalse(accepted)

    def test_prime_takes_stable_open_before_loop_starts(self):
        states = self.reed_input.prime(interval_s=0.0)
        self.assertEqual(states["rooftop_tent"], False)
        self.assertEqual(self.reed_input._stable["rooftop_tent"], False)
        self.assertFalse(self.gpio.reed_states["rooftop_tent"])


if __name__ == "__main__":
    unittest.main()