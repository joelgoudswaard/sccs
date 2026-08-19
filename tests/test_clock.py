import unittest
from unittest.mock import patch

from modules.clock import format_uptime, wait_for_clock_sync


class TestClock(unittest.TestCase):
    def test_format_uptime_seconds(self):
        self.assertEqual(format_uptime(0), "+0s")
        self.assertEqual(format_uptime(34.2), "+34s")

    def test_format_uptime_minutes(self):
        self.assertEqual(format_uptime(125), "+2m05s")

    def test_format_uptime_hours(self):
        self.assertEqual(format_uptime(3661), "+1h01m")

    @patch("modules.clock.is_clock_synchronized", side_effect=[False, False, True])
    @patch("modules.clock.time.sleep")
    def test_wait_for_clock_sync(self, _sleep, _sync):
        self.assertTrue(wait_for_clock_sync(10, poll_s=1))


if __name__ == "__main__":
    unittest.main()