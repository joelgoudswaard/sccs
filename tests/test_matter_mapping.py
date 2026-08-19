import unittest
from unittest.mock import MagicMock

from engine.config_compile import compile_config
from modules.accessories.mapping import (
    KIND_LIGHT,
    KIND_RELAY_LIGHT,
    KIND_SCENE,
    KIND_WATER,
    MapOptions,
    build_specs,
)
from modules.config import config
from modules.matter.manager import MatterManager


class TestMatterMapping(unittest.TestCase):
    def test_specs_include_google_types(self):
        compiled = compile_config(config)
        specs = build_specs(
            compiled,
            relay_icons={"floodlights": "fa-lightbulb"},
            options=MapOptions(victron_configured=False, fridge_configured=False),
        )
        by_key = {s.key: s for s in specs}
        self.assertEqual(by_key["light:kitchen_panel"].kind, KIND_LIGHT)
        self.assertTrue(by_key["light:kitchen_panel"].has_bug_mode)
        self.assertEqual(by_key["relay:floodlights"].kind, KIND_RELAY_LIGHT)
        self.assertEqual(by_key["scene:bedtime"].kind, KIND_SCENE)
        self.assertEqual(by_key["water:tank"].kind, KIND_WATER)

    def test_command_light_calls_runtime(self):
        runtime = MagicMock()
        runtime.get_ui_state.return_value = {"kitchen_bench": 40}
        mgr = MatterManager(runtime, config)
        mgr._handle_child(
            {
                "type": "command",
                "kind": "light",
                "entity": "kitchen_bench",
                "on": True,
                "brightness": 65,
            }
        )
        runtime.set_light_intent.assert_called_once_with("kitchen_bench", 65, None)

    def test_command_scene_and_relay(self):
        runtime = MagicMock()
        mgr = MatterManager(runtime, config)
        mgr._handle_child({"type": "command", "kind": "scene", "entity": "bedtime", "on": True})
        runtime.set_scene.assert_called_once_with("bedtime")
        mgr._handle_child({"type": "command", "kind": "relay_light", "entity": "floodlights", "on": True})
        runtime.set_relay_intent.assert_called_once_with("floodlights", True)

    def test_disabled_does_not_start_child(self):
        runtime = MagicMock()
        mgr = MatterManager(runtime, config)
        self.assertFalse(mgr.is_enabled())
        mgr.start()
        self.assertIsNone(mgr.proc)
        self.assertFalse(mgr.status()["running"])


if __name__ == "__main__":
    unittest.main()
