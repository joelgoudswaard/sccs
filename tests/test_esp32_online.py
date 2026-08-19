"""ESP online status must require MCU firmware response, not just an open UART."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from modules.esp32 import Esp32Manager


class _FakeConfig:
    def __init__(self, values=None):
        self._values = {
            ("esp32", "optimistic_lock_duration"): 2.5,
            ("esp32", "command_delay"): 0.0,
            ("esp32", "response_delay"): 0.0,
            ("esp32", "rgb_red_switch_ramp_ms"): 180,
            ("esp32", "rgb_mode_switch_ramp_ms"): 250,
            ("esp32", "probe_interval"): 30.0,
            ("esp32", "serial_ports"): "/dev/ttyAMA2,/dev/ttyAMA3",
            ("esp32", "baud_rate"): 115200,
            ("esp32", "init_delay"): 0.0,
            ("esp32", "timeout"): 0.1,
        }
        if values:
            self._values.update(values)

    def get(self, section, key, fallback=None):
        return self._values.get((section, key), fallback)

    def getfloat(self, section, key, fallback=None):
        v = self._values.get((section, key), fallback)
        return float(v) if v is not None else None

    def getint(self, section, key, fallback=None):
        v = self._values.get((section, key), fallback)
        return int(v) if v is not None else None

    def has_section(self, section):
        return False

    def items(self, section):
        return []


class TestEsp32Online(unittest.TestCase):
    def _manager(self):
        return Esp32Manager(_FakeConfig())

    def test_open_uart_without_reply_is_offline(self):
        mgr = self._manager()
        silent = MagicMock()
        silent.is_open = True
        silent.port = "/dev/ttyAMA2"
        silent.readline.return_value = b""

        with (
            patch("modules.esp32.os.path.exists", return_value=True),
            patch("modules.esp32.serial.Serial", return_value=silent),
            patch("modules.esp32.time.sleep", return_value=None),
        ):
            ok = mgr.init_serial()

        self.assertFalse(ok)
        self.assertFalse(mgr.is_connected())
        self.assertFalse(mgr.alive.get(1, False))

    def test_getvcc_reply_marks_online(self):
        mgr = self._manager()
        live = MagicMock()
        live.is_open = True
        live.port = "/dev/ttyAMA2"
        live.readline.return_value = b"VCC 3300\n"

        with (
            patch("modules.esp32.os.path.exists", return_value=True),
            patch("modules.esp32.serial.Serial", return_value=live),
            patch("modules.esp32.time.sleep", return_value=None),
        ):
            # Only one port in config for a cleaner assert
            mgr.config = _FakeConfig({
                ("esp32", "serial_ports"): "/dev/ttyAMA2",
            })
            # Re-apply float/int defaults after config swap
            mgr.config = _FakeConfig({
                ("esp32", "optimistic_lock_duration"): 2.5,
                ("esp32", "command_delay"): 0.0,
                ("esp32", "response_delay"): 0.0,
                ("esp32", "rgb_red_switch_ramp_ms"): 180,
                ("esp32", "rgb_mode_switch_ramp_ms"): 250,
                ("esp32", "probe_interval"): 30.0,
                ("esp32", "serial_ports"): "/dev/ttyAMA2",
                ("esp32", "baud_rate"): 115200,
                ("esp32", "init_delay"): 0.0,
                ("esp32", "timeout"): 0.1,
            })
            ok = mgr.init_serial()

        self.assertTrue(ok)
        self.assertTrue(mgr.is_connected())
        self.assertTrue(mgr.alive.get(1))
        live.write.assert_called()
        written = b"".join(c.args[0] for c in live.write.call_args_list)
        self.assertIn(b"GETVCC", written)

    def test_is_connected_false_when_alive_cleared(self):
        mgr = self._manager()
        ser = MagicMock()
        ser.is_open = True
        mgr.serials[1] = ser
        mgr.alive[1] = True
        self.assertTrue(mgr.is_connected())

        mgr.alive[1] = False
        self.assertFalse(mgr.is_connected())


if __name__ == "__main__":
    unittest.main()
