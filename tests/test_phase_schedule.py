"""Displayed phase start times follow the current sunset, not startup."""

import unittest
import zoneinfo

from modules.phases import PhaseManager


class _Gps:
    def __init__(self, state):
        self.state = state

    def get_state(self):
        return dict(self.state)


class _Socket:
    def __init__(self):
        self.events = []

    def emit(self, name, data):
        self.events.append((name, data))


class TestPhaseScheduleRefresh(unittest.TestCase):
    def _manager(self, sunset: str, sunrise: str = "6:01 AM") -> PhaseManager:
        manager = PhaseManager.__new__(PhaseManager)
        manager.gps = _Gps({
            "fix_quality": 1,
            "sunrise": sunrise,
            "sunset": sunset,
            "timezone": "Australia/Melbourne",
        })
        manager.fallback_tz = zoneinfo.ZoneInfo("Australia/Melbourne")
        manager.day_offset_minutes = 45
        manager.evening_offset_minutes = 45
        manager.night_start_minutes = 1200
        manager._cached_phase_times = {}
        manager._schedule_inputs_seen = None
        manager._refreshing_schedule = False
        manager._using_fallback = False
        manager.current_phase = "Day"
        manager.forced_phase = None
        manager._last_broadcast_phase = "Day"
        manager.on_phase_change = None
        manager.socketio = _Socket()
        return manager

    def _phase_updates(self, manager: PhaseManager) -> list:
        return [data for name, data in manager.socketio.events if name == "phase_update"]

    def test_evening_label_follows_a_later_sunset(self):
        # 26 Sept: sunset 6:16, evening 45 minutes earlier at 5:31.
        # 9 Oct: sunset 7:27, evening at 6:42. The label has to move with it.
        manager = self._manager("6:16 PM")
        first = manager.get_phase_times()
        self.assertEqual(first["evening_start"], "05:31 PM")
        self.assertEqual(first["sunset"], "6:16 PM")
        self.assertEqual(self._phase_updates(manager), [])

        manager.gps.state["sunrise"] = "6:41 AM"
        manager.gps.state["sunset"] = "7:27 PM"
        updated = manager.get_phase_times()

        self.assertEqual(updated["evening_start"], "06:42 PM")
        self.assertEqual(updated["sunset"], "7:27 PM")
        self.assertEqual(updated["day_start"], "07:26 AM")
        pushes = self._phase_updates(manager)
        self.assertEqual(len(pushes), 1)
        self.assertEqual(pushes[0]["evening_start"], "06:42 PM")
        self.assertEqual(pushes[0]["sunset"], "7:27 PM")

    def test_same_sun_times_do_not_push_again(self):
        manager = self._manager("7:27 PM", sunrise="6:41 AM")
        manager.get_phase_times()
        manager.socketio.events.clear()

        again = manager.get_phase_times()
        manager._refresh_schedule_if_stale()

        self.assertEqual(again["evening_start"], "06:42 PM")
        self.assertEqual(self._phase_updates(manager), [])


if __name__ == "__main__":
    unittest.main()
