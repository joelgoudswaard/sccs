import unittest

import pynmea2

from modules.gps import GPSModule


def _gga_with_checksum(body: str) -> str:
    cs = 0
    for ch in body:
        cs ^= ord(ch)
    return f"${body}*{cs:02X}"


class GPSTests(unittest.TestCase):
    def _module(self) -> GPSModule:
        return object.__new__(GPSModule)

    def test_parse_lat_lon_southern_hemisphere(self):
        line = _gga_with_checksum(
            "GPGGA,123519,3741.460,S,14542.660,E,1,08,1.2,545.4,M,46.9,M,,"
        )
        msg = pynmea2.parse(line)
        mod = self._module()
        lat, lon = mod._parse_lat_lon(msg)
        self.assertAlmostEqual(lat, -37.691, places=3)
        self.assertAlmostEqual(lon, 145.711, places=3)

    def test_parse_lat_lon_northern_hemisphere(self):
        line = _gga_with_checksum(
            "GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,"
        )
        msg = pynmea2.parse(line)
        mod = self._module()
        lat, lon = mod._parse_lat_lon(msg)
        self.assertAlmostEqual(lat, 48.1173, places=3)
        self.assertAlmostEqual(lon, 11.5167, places=3)

    def test_parse_hdop_from_gga_uses_horizontal_dil(self):
        line = _gga_with_checksum(
            "GPGGA,123519,3741.460,S,14542.660,E,1,08,1.2,545.4,M,46.9,M,,"
        )
        msg = pynmea2.parse(line)
        mod = self._module()
        self.assertEqual(mod._parse_hdop(msg), 1.2)

    def test_parse_lat_lon_empty_gga_is_not_null_island(self):
        msg = pynmea2.parse("$GNGGA,,,,,,0,00,99.99,,,,,,*56")
        mod = self._module()
        self.assertEqual(mod._parse_lat_lon(msg), (None, None))
        self.assertFalse(mod._valid_position(0.0, 0.0))
        self.assertTrue(mod._valid_position(-37.191, 145.711))

    def test_parse_lat_lon_void_rmc_is_not_null_island(self):
        msg = pynmea2.parse("$GNRMC,,V,,,,,,,,,,N,V*37")
        mod = self._module()
        self.assertEqual(mod._parse_lat_lon(msg), (None, None))

    def test_get_state_replaces_null_island_with_fallback(self):
        class _Cfg:
            def getfloat(self, section, key, fallback=None):
                return {"fallback_latitude": -37.191, "fallback_longitude": 145.711}.get(key, fallback)

            def get(self, section, key, fallback=None):
                return {
                    "fallback_timezone": "Australia/Melbourne",
                    "fallback_name": "Alexandra",
                }.get(key, fallback)

        class _Serial:
            is_open = True

        mod = self._module()
        mod.config = _Cfg()
        mod.serial = _Serial()
        mod.last_known_lat = None
        mod.last_known_lon = None
        mod.state = {
            "latitude": 0.0,
            "longitude": 0.0,
            "force_no_hardware": False,
            "force_no_fix": False,
            "last_known_suburb": None,
            "sunrise": None,
            "sunset": None,
        }
        state = mod.get_state()
        self.assertAlmostEqual(state["latitude"], -37.191)
        self.assertAlmostEqual(state["longitude"], 145.711)
        self.assertTrue(state["using_fallback"])
        self.assertEqual(state["suburb"], "Alexandra")

    def test_parse_hdop_from_gsa(self):
        line = _gga_with_checksum(
            "GPGSA,A,3,01,02,03,04,05,06,07,08,09,10,11,12,1.5,0.9,1.2"
        )
        msg = pynmea2.parse(line)
        mod = self._module()
        self.assertEqual(mod._parse_hdop(msg), 0.9)


if __name__ == "__main__":
    unittest.main()