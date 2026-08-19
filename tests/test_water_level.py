"""Water tank level calibration: 150 Ω pull-up to 3V3, sender 240 Ω empty → 33 Ω full."""

import unittest

from modules.sensors import SensorManager


class _FakeConfig:
    def getfloat(self, section, option, fallback=None):
        data = {
            ("sensors", "water_resistance_empty"): 240.0,
            ("sensors", "water_resistance_full"): 33.0,
            ("sensors", "water_pullup_ohms"): 150.0,
            ("sensors", "water_adc_max"): 4095.0,
            ("tanks", "water_litres"): 160.0,
            ("sensors", "update_interval"): 5.0,
            ("sensors", "outside_temp_bus_gpio"): 3.0,
            ("sensors", "fridge_temp_bus_gpio"): 3.0,
            ("sensors", "freezer_temp_bus_gpio"): 3.0,
        }
        return data.get((section, option), fallback)

    def getint(self, section, option, fallback=None):
        data = {
            ("esp32 analog", "water_pin"): 1,
            ("esp32 analog", "water_esp"): 1,
        }
        return data.get((section, option), fallback)

    def get(self, section, option, fallback=None):
        return fallback


def _adc_for_rsense(r_sense: float, r_pullup: float = 150.0, vcc: float = 3.3, adc_max: float = 4095.0) -> float:
    """Ideal ADC counts for a given sender resistance on the divider."""
    v_sense = vcc * r_sense / (r_pullup + r_sense)
    return v_sense / vcc * adc_max


class WaterLevelCalibrationTests(unittest.TestCase):
    def setUp(self):
        # Bypass full SensorManager.__init__ hardware setup
        self.sm = object.__new__(SensorManager)
        self.sm.WATER_R_EMPTY = 240.0
        self.sm.WATER_R_FULL = 33.0
        self.sm.WATER_R_PULLUP = 150.0
        self.sm.WATER_ADC_MAX = 4095.0

    def test_empty_sender(self):
        adc = _adc_for_rsense(240.0)
        pct = self.sm._calculate_level_percent(adc, 3.3, 240.0, 33.0, 150.0, 4095.0)
        self.assertEqual(pct, 0)

    def test_full_sender(self):
        adc = _adc_for_rsense(33.0)
        pct = self.sm._calculate_level_percent(adc, 3.3, 240.0, 33.0, 150.0, 4095.0)
        self.assertEqual(pct, 100)

    def test_mid_sender(self):
        # Mid-resistance between 240 and 33 → ~50%
        r_mid = (240.0 + 33.0) / 2.0
        adc = _adc_for_rsense(r_mid)
        pct = self.sm._calculate_level_percent(adc, 3.3, 240.0, 33.0, 150.0, 4095.0)
        self.assertEqual(pct, 50)

    def test_open_circuit_near_rail(self):
        pct = self.sm._calculate_level_percent(4095.0, 3.3, 240.0, 33.0, 150.0, 4095.0)
        self.assertEqual(pct, 0)

    def test_expected_sense_voltages(self):
        # Documented calibration points at 3.3 V
        v_empty = 3.3 * 240.0 / (150.0 + 240.0)
        v_full = 3.3 * 33.0 / (150.0 + 33.0)
        self.assertAlmostEqual(v_empty, 2.031, places=2)
        self.assertAlmostEqual(v_full, 0.595, places=2)


if __name__ == "__main__":
    unittest.main()
