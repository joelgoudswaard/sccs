"""Home network tile names the route that carries traffic, not a spare Wi-Fi link."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import network


def _wifi_and_usb() -> dict:
    return {
        "connected": True,
        "ssid": "Glenn Innes",
        "iface": "wlan0",
        "signal": 77,
        "uplink": {
            "active": "usb",
            "wifi": {"ssid": "Glenn Innes", "iface": "wlan0", "connected": True},
            "usb": {"iface": "eth1", "connected": True, "label": "USB Hotspot"},
        },
    }


class NetworkUplinkLabelTest(unittest.TestCase):
    def test_home_names_usb_when_that_route_is_active(self):
        with (
            patch.object(network, "get_wifi_status", return_value=_wifi_and_usb()),
            patch.object(network, "_get_cached_ping", return_value=(26, "good")),
            patch.object(network, "_throughput_kbps", return_value=(1.0, 2.0)) as throughput,
            patch.object(network, "_read_link_speed_mbps", return_value=None),
        ):
            payload = network.build_network_status()
        internet = payload["internet"]
        self.assertEqual(internet["friendly_name"], "USB Hotspot")
        self.assertEqual(internet["iface"], "eth1")
        self.assertIsNone(internet["signal_quality"])
        self.assertIsNone(internet["ssid"])
        throughput.assert_called_once_with("eth1")

    def test_home_names_the_wifi_ssid_when_wifi_is_the_route(self):
        status = _wifi_and_usb()
        status["uplink"]["active"] = "wifi"
        with (
            patch.object(network, "get_wifi_status", return_value=status),
            patch.object(network, "_get_cached_ping", return_value=(20, "good")),
            patch.object(network, "_throughput_kbps", return_value=(0.0, 0.0)),
            patch.object(network, "_read_link_speed_mbps", return_value=24),
        ):
            payload = network.build_network_status()
        internet = payload["internet"]
        self.assertEqual(internet["friendly_name"], "Glenn Innes")
        self.assertEqual(internet["iface"], "wlan0")
        self.assertEqual(internet["signal_quality"], "77%")


if __name__ == "__main__":
    unittest.main()
