"""Installer Victron probe: a bad key is a mismatch, a good parse is a connection."""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "install.sh"


def _load_probe():
    text = INSTALL.read_text(encoding="utf-8")
    start = text.index("import asyncio\n")
    end = text.index("\nasyncio.run(main())\n", start)
    ns: dict = {}
    exec(text[start:end], ns)
    return ns


def _packet(check_byte: int) -> bytes:
    # prefix 0x10, model 0, BatteryMonitor mode 0x02, then the key-check byte.
    return bytes.fromhex("1000") + (0).to_bytes(2, "little") + bytes([0x02, 0x01, 0x00, check_byte]) + bytes(16)


class VictronProbeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import victron_ble  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("victron_ble is not installed")
        cls.probe = _load_probe()

    def test_wrong_key_byte_is_a_mismatch(self):
        state = {"done": False, "status": "NOT_HEARD", "detail": "", "heard": False}
        device = SimpleNamespace(address="AA:BB:CC:DD:EE:FF", name="SmartShunt")
        adv = SimpleNamespace(manufacturer_data={0x02E1: _packet(0xFF)}, rssi=-60)
        self.probe["handle"](state, "aa:bb:cc:dd:ee:ff", "aa" + "11" * 15, device, adv)
        self.assertEqual(state["status"], "MISMATCH")
        self.assertTrue(state["done"])

    def test_other_address_is_ignored(self):
        state = {"done": False, "status": "NOT_HEARD", "detail": "", "heard": False}
        device = SimpleNamespace(address="11:22:33:44:55:66", name="other")
        adv = SimpleNamespace(manufacturer_data={0x02E1: _packet(0xFF)}, rssi=-40)
        self.probe["handle"](state, "aa:bb:cc:dd:ee:ff", "aa" + "11" * 15, device, adv)
        self.assertEqual(state["status"], "NOT_HEARD")
        self.assertFalse(state["heard"])

    def test_visible_address_without_readout_can_be_reported(self):
        state = {"done": False, "status": "NOT_HEARD", "detail": "", "heard": False}
        device = SimpleNamespace(address="aa:bb:cc:dd:ee:ff", name="nearby")
        adv = SimpleNamespace(manufacturer_data={}, rssi=-50)
        self.probe["handle"](state, "aa:bb:cc:dd:ee:ff", "aa" + "11" * 15, device, adv)
        self.assertTrue(state["heard"])
        self.probe["finish"](state)
        self.assertEqual(state["status"], "HEARD")
        self.assertIn("Instant Readout", state["detail"])

    def test_decrypted_reading_is_a_connection(self):
        class Parsed:
            def get_model_name(self):
                return "SmartShunt 500A"

            def get_voltage(self):
                return 12.84

            def get_soc(self):
                return 87

        class FakeDevice:
            def __init__(self, key):
                self.key = key

            def parse(self, data):
                return Parsed()

        import victron_ble.devices as devices

        state = {"done": False, "status": "NOT_HEARD", "detail": "", "heard": False}
        device = SimpleNamespace(address="aa:bb:cc:dd:ee:ff", name="HQ")
        adv = SimpleNamespace(manufacturer_data={0x02E1: b"\x10" + bytes(20)}, rssi=-58)
        original = devices.detect_device_type
        devices.detect_device_type = lambda data: FakeDevice
        try:
            self.probe["handle"](state, "aa:bb:cc:dd:ee:ff", "aa" + "11" * 15, device, adv)
        finally:
            devices.detect_device_type = original
        self.assertEqual(state["status"], "OK")
        self.assertTrue(state["done"])
        self.assertIn("SmartShunt 500A", state["detail"])
        self.assertIn("12.84 V", state["detail"])
        self.assertIn("87%", state["detail"])
        self.assertIn("RSSI -58", state["detail"])

    def test_classifies_shunt_and_mppt_without_a_key(self):
        shunt = bytes([0x10, 0x00]) + (0xA389).to_bytes(2, "little") + bytes([0x02, 0, 0])
        solar = bytes([0x10, 0x00]) + (0xA102).to_bytes(2, "little") + bytes([0x01, 0, 0])
        shunt_info = self.probe["classify_victron_advert"](shunt)
        solar_info = self.probe["classify_victron_advert"](solar)
        self.assertEqual(shunt_info["role"], "shunt")
        self.assertIn("SmartShunt", shunt_info["model"])
        self.assertEqual(solar_info["role"], "mppt")
        self.assertIn("SmartSolar", solar_info["model"])

    def test_non_victron_advert_is_ignored(self):
        classify = self.probe["classify_victron_advert"]
        self.assertIsNone(classify(b""))
        self.assertIsNone(classify(b"\x00\x01\x02\x03\x04"))


if __name__ == "__main__":
    unittest.main()
