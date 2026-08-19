import tempfile
import unittest
from unittest.mock import MagicMock

from engine.config_compile import compile_config
from modules.config import config
from modules.homekit.mapping import (
    KIND_BATTERY,
    KIND_LIGHT,
    KIND_REED,
    KIND_RELAY_LIGHT,
    KIND_RELAY_SWITCH,
    KIND_SCENE,
    KIND_TEMPERATURE,
    KIND_WATER,
    MapOptions,
    assign_aids,
    build_specs,
    charging_state,
    light_target_from_chars,
    relay_is_light,
    status_low_battery,
)
from modules.homekit.sync import AccessoryIndex


class TestHomeKitMapping(unittest.TestCase):
    def setUp(self):
        self.cfg = compile_config(config)

    def test_dist_lights_and_relays(self):
        specs = build_specs(
            self.cfg,
            reed_labels={"kitchen_panel": "Kitchen Panel"},
            relay_icons={"floodlights": "fa-lightbulb"},
            options=MapOptions(
                include_scenes=False,
                include_sensors=False,
                include_battery=False,
            ),
        )
        by_key = {s.key: s for s in specs}
        self.assertIn("light:kitchen_bench", by_key)
        self.assertEqual(by_key["light:kitchen_bench"].kind, KIND_LIGHT)
        self.assertTrue(by_key["light:kitchen_bench"].has_brightness)
        self.assertFalse(by_key["light:kitchen_bench"].has_bug_mode)

        self.assertTrue(by_key["light:kitchen_panel"].has_bug_mode)
        self.assertTrue(by_key["light:awning"].has_bug_mode)

        self.assertEqual(by_key["relay:floodlights"].kind, KIND_RELAY_LIGHT)
        self.assertEqual(by_key["relay:floodlights"].name, "Floodlights")

    def test_scenes_and_sensors(self):
        specs = build_specs(
            self.cfg,
            reed_labels={"rooftop_tent": "Rooftop Tent"},
            options=MapOptions(
                fridge_configured=True,
                freezer_configured=False,
                victron_configured=True,
            ),
        )
        kinds = {s.key: s.kind for s in specs}
        self.assertEqual(kinds["scene:bedtime"], KIND_SCENE)
        self.assertEqual(kinds["reed:rooftop_tent"], KIND_REED)
        self.assertEqual(kinds["temp:fridge"], KIND_TEMPERATURE)
        self.assertNotIn("temp:freezer", kinds)
        self.assertEqual(kinds["water:tank"], KIND_WATER)
        self.assertEqual(kinds["battery:house"], KIND_BATTERY)

    def test_aids_stable_and_skip_forbidden(self):
        keys = ["light:a", "light:b", "scene:bedtime"]
        first = assign_aids(keys)
        second = assign_aids(keys)
        self.assertEqual(first, second)
        self.assertNotIn(1, first.values())
        self.assertNotIn(7, first.values())

    def test_relay_inference(self):
        self.assertTrue(relay_is_light("floodlights", "Floodlights", "fa-lightbulb"))
        self.assertTrue(relay_is_light("awning_lights", "Awning Lights", "fa-umbrella"))
        self.assertFalse(relay_is_light("water_circuit", "Water Circuit", "fa-faucet"))

    def test_light_target_from_chars(self):
        self.assertEqual(
            light_target_from_chars(on=False, brightness=40, last_nonzero=65),
            0,
        )
        self.assertEqual(
            light_target_from_chars(on=True, brightness=None, last_nonzero=65),
            65,
        )
        self.assertEqual(
            light_target_from_chars(on=True, brightness=20, last_nonzero=65),
            20,
        )
        self.assertEqual(
            light_target_from_chars(on=None, brightness=0, last_nonzero=65),
            0,
        )

    def test_refuse_wildcard_bind(self):
        from modules.homekit.driver import resolve_bind_address

        self.assertIsNone(resolve_bind_address("0.0.0.0"))
        self.assertIsNone(resolve_bind_address(""))
        self.assertIsNone(resolve_bind_address("203.0.113.9"))

    def test_battery_helpers(self):
        self.assertEqual(charging_state(1.2, None), 1)
        self.assertEqual(charging_state(-3.0, None), 0)
        self.assertEqual(charging_state(0, "Absorption"), 1)
        self.assertEqual(status_low_battery(15, 20), 1)
        self.assertEqual(status_low_battery(50, 20), 0)
        self.assertEqual(status_low_battery(None, 20), 0)


class TestHomeKitSetters(unittest.TestCase):
    def _driver(self):
        from pyhap.accessory_driver import AccessoryDriver

        persist = tempfile.NamedTemporaryFile(suffix=".state", delete=False)
        persist.close()
        driver = AccessoryDriver(port=0, persist_file=persist.name)
        self.addCleanup(driver.loop.close)
        return driver

    def test_light_setter_calls_runtime(self):
        from modules.homekit.accessories import LightAccessory
        from modules.homekit.mapping import AccessorySpec

        runtime = MagicMock()
        spec = AccessorySpec(
            key="light:kitchen_bench",
            kind=KIND_LIGHT,
            name="Kitchen Bench",
            entity="kitchen_bench",
            aid=12,
            has_brightness=True,
        )
        try:
            driver = self._driver()
        except ImportError:
            self.skipTest("HAP-python not installed")

        acc = LightAccessory(driver, spec, runtime)
        acc._set_light({"On": True, "Brightness": 65})
        runtime.set_light_intent.assert_called_once_with("kitchen_bench", 65, None)

        runtime.reset_mock()
        acc._set_light({"On": False})
        runtime.set_light_intent.assert_called_once_with("kitchen_bench", 0, None)

    def test_bug_mode_preserves_brightness(self):
        from modules.homekit.accessories import LightAccessory
        from modules.homekit.mapping import AccessorySpec
        runtime = MagicMock()
        spec = AccessorySpec(
            key="light:awning",
            kind=KIND_LIGHT,
            name="Awning Lights",
            entity="awning",
            aid=13,
            has_brightness=True,
            has_bug_mode=True,
        )
        acc = LightAccessory(self._driver(), spec, runtime)
        acc.char_brightness.set_value(40)
        acc._set_bug(True)
        runtime.set_light_intent.assert_called_once_with("awning", 40, "red")

    def test_scene_calls_set_scene(self):
        from modules.homekit.accessories import SceneAccessory
        from modules.homekit.mapping import AccessorySpec

        runtime = MagicMock()
        spec = AccessorySpec(
            key="scene:bedtime",
            kind=KIND_SCENE,
            name="Bedtime",
            entity="bedtime",
            aid=14,
        )
        acc = SceneAccessory(self._driver(), spec, runtime)
        acc._set_on(True)
        runtime.set_scene.assert_called_once_with("bedtime")

    def test_relay_calls_set_relay_intent(self):
        from modules.homekit.accessories import RelayAccessory
        from modules.homekit.mapping import AccessorySpec

        runtime = MagicMock()
        spec = AccessorySpec(
            key="relay:floodlights",
            kind=KIND_RELAY_LIGHT,
            name="Floodlights",
            entity="floodlights",
            aid=15,
        )
        acc = RelayAccessory(self._driver(), spec, runtime)
        acc._set_on(True)
        runtime.set_relay_intent.assert_called_once_with("floodlights", True)

    def test_apply_ui_state_does_not_write_runtime(self):
        from modules.homekit.accessories import LightAccessory
        from modules.homekit.mapping import AccessorySpec

        runtime = MagicMock()
        spec = AccessorySpec(
            key="light:ensuite",
            kind=KIND_LIGHT,
            name="Ensuite",
            entity="ensuite",
            aid=16,
            has_brightness=True,
        )
        acc = LightAccessory(self._driver(), spec, runtime)
        index = AccessoryIndex()
        index.register(spec, acc)
        index.apply_ui_state({"ensuite": 30, "ensuite_mode": "white"})
        runtime.set_light_intent.assert_not_called()
        self.assertTrue(acc.char_on.value)
        self.assertEqual(acc.char_brightness.value, 30)


if __name__ == "__main__":
    unittest.main()
