"""Screen host IP validation (invalid / host Pi / duplicates)."""

from __future__ import annotations

import configparser
import ipaddress
import unittest

from engine.config_compile import CompiledConfig
from engine.config_validate import ConfigValidationError, validate_compiled_config


def _screen(host: str, **kwargs) -> dict:
    meta = {
        "friendly": "Test",
        "linked_reed": "",
        "host": host,
        "username": "joel",
        "brightness_path": "/sys/class/graphics/fb0/blank",
        "icon": "fa-display",
        "phase_brightness": {"day": 100, "evening": 30, "night": 5},
        "blank_path": None,
        "mac": None,
    }
    meta.update(kwargs)
    return meta


def _cfg_with_sonos(interface_addr: str = "10.10.10.1") -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg.add_section("sonos")
    cfg.set("sonos", "interface_addr", interface_addr)
    return cfg


HOST_PI = ipaddress.IPv4Address("10.10.10.1")
LOCAL = {HOST_PI, ipaddress.IPv4Address("192.168.0.200")}
NETS = [
    ipaddress.IPv4Network("10.10.10.1/24", strict=False),
    ipaddress.IPv4Network("192.168.0.200/24", strict=False),
]


class ScreenHostValidationTests(unittest.TestCase):
    def _validate(self, screens: dict, raw_cfg=None):
        compiled = CompiledConfig()
        compiled.screens = screens
        return validate_compiled_config(
            raw_cfg or configparser.ConfigParser(),
            compiled,
            local_ipv4=LOCAL,
            local_nets=NETS,
        )

    def test_valid_lan_host_ok(self):
        warnings = self._validate({"kitchen": _screen("10.10.10.10")})
        self.assertEqual(warnings, [])

    def test_host_pi_lan_addr_rejected(self):
        with self.assertRaises(ConfigValidationError) as ctx:
            self._validate({"kitchen": _screen("10.10.10.1")})
        self.assertTrue(any("host Pi" in e for e in ctx.exception.errors))

    def test_host_pi_other_iface_rejected(self):
        with self.assertRaises(ConfigValidationError) as ctx:
            self._validate({"kitchen": _screen("192.168.0.200")})
        self.assertTrue(any("host Pi" in e for e in ctx.exception.errors))

    def test_sonos_interface_addr_counts_as_host(self):
        # Inject empty local set so only config sonos.interface_addr blocks.
        compiled = CompiledConfig()
        compiled.screens = {"kitchen": _screen("10.10.10.1")}
        with self.assertRaises(ConfigValidationError) as ctx:
            validate_compiled_config(
                _cfg_with_sonos("10.10.10.1"),
                compiled,
                local_ipv4=set(),
                local_nets=[],
            )
        self.assertTrue(any("host Pi" in e for e in ctx.exception.errors))

    def test_invalid_ipv4_rejected(self):
        with self.assertRaises(ConfigValidationError) as ctx:
            self._validate({"kitchen": _screen("not-an-ip")})
        self.assertTrue(any("IPv4" in e for e in ctx.exception.errors))

    def test_loopback_rejected(self):
        with self.assertRaises(ConfigValidationError) as ctx:
            self._validate({"kitchen": _screen("127.0.0.1")})
        self.assertTrue(any("usable LAN" in e for e in ctx.exception.errors))

    def test_network_and_broadcast_rejected(self):
        with self.assertRaises(ConfigValidationError) as ctx:
            self._validate({"kitchen": _screen("10.10.10.0")})
        self.assertTrue(any("network/broadcast" in e for e in ctx.exception.errors))

        with self.assertRaises(ConfigValidationError) as ctx:
            self._validate({"kitchen": _screen("10.10.10.255")})
        self.assertTrue(any("network/broadcast" in e for e in ctx.exception.errors))

    def test_duplicate_hosts_rejected(self):
        with self.assertRaises(ConfigValidationError) as ctx:
            self._validate(
                {
                    "kitchen": _screen("10.10.10.10"),
                    "storage": _screen("10.10.10.10"),
                }
            )
        self.assertTrue(any("duplicates" in e for e in ctx.exception.errors))

    def test_off_subnet_warns_only(self):
        warnings = self._validate({"kitchen": _screen("172.16.0.50")})
        self.assertTrue(any("not on any local interface" in w for w in warnings))

    def test_empty_host_rejected(self):
        with self.assertRaises(ConfigValidationError) as ctx:
            self._validate({"kitchen": _screen("")})
        self.assertTrue(any("empty" in e for e in ctx.exception.errors))


if __name__ == "__main__":
    unittest.main()
