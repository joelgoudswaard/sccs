"""Daytime follows light even if a manual dark choice was saved earlier."""

import unittest

from modules.phases import PhaseManager


class _Socket:
    def __init__(self):
        self.events = []

    def emit(self, name, data):
        self.events.append((name, data))


class TestDarkModeFollowsPhase(unittest.TestCase):
    def _manager(self, phase: str, manual: str | None) -> PhaseManager:
        manager = PhaseManager.__new__(PhaseManager)
        manager.current_phase = phase
        manager.forced_phase = None
        manager.manual_dark_mode = manual
        manager.current_dark_mode = manual or "dark"
        manager.dark_mode_config = None
        manager.socketio = _Socket()
        manager.on_dark_mode_change = None
        return manager

    def test_saved_dark_clears_during_day(self):
        manager = self._manager("Day", "dark")
        manager._maybe_clear_manual_dark_mode()
        manager._auto_update_dark_mode()
        self.assertIsNone(manager.manual_dark_mode)
        self.assertEqual(manager.get_current_dark_mode(), "light")

    def test_saved_light_stays_during_day(self):
        manager = self._manager("Day", "light")
        manager._maybe_clear_manual_dark_mode()
        manager._auto_update_dark_mode()
        self.assertEqual(manager.manual_dark_mode, "light")
        self.assertEqual(manager.get_current_dark_mode(), "light")

    def test_evening_wants_dark(self):
        manager = self._manager("Evening", None)
        manager.current_dark_mode = "light"
        manager._auto_update_dark_mode()
        self.assertEqual(manager.get_current_dark_mode(), "dark")


if __name__ == "__main__":
    unittest.main()
