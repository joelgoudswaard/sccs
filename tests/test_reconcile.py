import unittest
from unittest.mock import MagicMock

from engine.config_compile import compile_config
from engine.reconcile import Reconciler
from engine.world import WorldStore
from modules.config import config


class TestReconcile(unittest.TestCase):
    def setUp(self):
        self.cfg = compile_config(config)
        self.world = WorldStore(
            self.cfg.reed_names,
            self.cfg.light_names,
            self.cfg.relay_names,
        )
        self.world.set_light_to_reed_map(self.cfg.light_to_reed)
        self.world.set_phase("Day", invalidate=False)
        self.world.update_reeds({name: True for name in self.cfg.reed_names})
        self.world.set_reed_force("kitchen_panel", False)
        self.world.update_observed_lights({"kitchen_panel": 0}, {"kitchen_panel": "white"})

        self.esp32 = MagicMock()
        self.relays = MagicMock()
        self.relays.read_relays.return_value = {}
        self.reconciler = Reconciler(
            world=self.world,
            cfg=self.cfg,
            esp32_actuator=self.esp32,
            relay_actuator=self.relays,
        )

    def _kitchen_calls(self):
        return [
            call
            for call in self.esp32.set_light.call_args_list
            if call.args[0] == "kitchen_panel"
        ]

    def test_stale_commanded_cache_blocks_hardware_until_invalidated(self):
        self.world.update_observed_lights({"kitchen_panel": 100}, {"kitchen_panel": "white"})
        self.reconciler._commanded_lights["kitchen_panel"] = (100, "white")
        self.reconciler.reconcile(ramp_source="reed")
        self.assertEqual(self._kitchen_calls(), [])

        self.reconciler.invalidate_commanded_for_reed("kitchen_panel")
        self.reconciler.reconcile(ramp_source="reed")
        self.assertEqual(len(self._kitchen_calls()), 1)
        self.assertEqual(self._kitchen_calls()[0].args[1], 100)

    def test_observed_drift_triggers_recommand_even_when_commanded_matches(self):
        self.reconciler._commanded_lights["kitchen_panel"] = (100, "white")
        self.reconciler.reconcile(ramp_source="auto")
        self.assertEqual(len(self._kitchen_calls()), 1)
        self.assertEqual(self._kitchen_calls()[0].args[1], 100)

    def test_stale_observed_blocks_until_invalidate_clears_it(self):
        self.world.update_observed_lights({"kitchen_panel": 100}, {"kitchen_panel": "white"})
        self.reconciler._commanded_lights["kitchen_panel"] = (100, "white")
        self.reconciler.reconcile(ramp_source="auto")
        self.assertEqual(self._kitchen_calls(), [])

        self.reconciler.invalidate_commanded_for_reed("kitchen_panel")
        self.reconciler.reconcile(ramp_source="reed")
        self.assertEqual(len(self._kitchen_calls()), 1)
        self.assertEqual(self._kitchen_calls()[0].args[1], 100)

    def test_pending_reed_command_forces_send_despite_stale_observed(self):
        self.world.update_observed_lights({"kitchen_panel": 100}, {"kitchen_panel": "white"})
        self.reconciler._commanded_lights["kitchen_panel"] = (100, "white")
        self.reconciler._pending_reed_command.add("kitchen_panel")
        self.reconciler.reconcile(ramp_source="reed")
        self.assertEqual(len(self._kitchen_calls()), 1)
        self.assertNotIn("kitchen_panel", self.reconciler._pending_reed_command)

    def test_panel_transition_invalidates_interlock_dependent_bench(self):
        self.reconciler._commanded_lights["kitchen_bench"] = (100, "white")
        lights = self.reconciler.invalidate_commanded_for_reed("kitchen_panel")
        self.assertIn("kitchen_panel", lights)
        self.assertIn("kitchen_bench", lights)
        self.assertIn("kitchen_bench", self.reconciler._pending_reed_command)

    def test_bench_transition_does_not_invalidate_panel(self):
        lights = self.reconciler.invalidate_commanded_for_reed("kitchen_bench")
        self.assertIn("kitchen_bench", lights)
        self.assertNotIn("kitchen_panel", lights)

    def test_panel_open_while_bench_open_commands_both(self):
        self.world.set_reed_force("kitchen_panel", None)
        reeds = {name: True for name in self.cfg.reed_names}
        reeds["kitchen_panel"] = False
        reeds["kitchen_bench"] = False
        self.world.update_reeds(reeds)
        self.reconciler._commanded_lights["kitchen_panel"] = (0, "white")
        self.reconciler._commanded_lights["kitchen_bench"] = (0, "white")
        self.reconciler.invalidate_commanded_for_reed("kitchen_panel")
        self.reconciler.reconcile(ramp_source="reed")
        panel_calls = self._kitchen_calls()
        bench_calls = [
            c for c in self.esp32.set_light.call_args_list if c.args[0] == "kitchen_bench"
        ]
        self.assertEqual(len(panel_calls), 1)
        self.assertEqual(panel_calls[0].args[1], 100)
        self.assertEqual(len(bench_calls), 1)
        self.assertEqual(bench_calls[0].args[1], 100)

    def test_force_bench_open_panel_forced_closed_commands_bench_off(self):
        self.world.set_reed_force("kitchen_panel", True)
        self.world.set_reed_force("kitchen_bench", False)
        self.reconciler._commanded_lights["kitchen_bench"] = (100, "white")
        self.reconciler.invalidate_commanded_for_reed("kitchen_bench")
        self.reconciler.reconcile(ramp_source="reed")
        bench_calls = [
            c for c in self.esp32.set_light.call_args_list if c.args[0] == "kitchen_bench"
        ]
        self.assertEqual(len(bench_calls), 1)
        self.assertEqual(bench_calls[0].args[1], 0)

    def test_panel_close_while_bench_open_commands_both_off(self):
        self.world.set_reed_force("kitchen_panel", None)
        reeds = {name: True for name in self.cfg.reed_names}
        reeds["kitchen_bench"] = False
        self.world.update_reeds(reeds)
        self.reconciler._commanded_lights["kitchen_panel"] = (100, "white")
        self.reconciler._commanded_lights["kitchen_bench"] = (100, "white")
        self.reconciler.invalidate_commanded_for_reed("kitchen_panel")
        self.reconciler.reconcile(ramp_source="reed")
        panel_calls = self._kitchen_calls()
        bench_calls = [
            c for c in self.esp32.set_light.call_args_list if c.args[0] == "kitchen_bench"
        ]
        self.assertEqual(len(panel_calls), 1)
        self.assertEqual(panel_calls[0].args[1], 0)
        self.assertEqual(len(bench_calls), 1)
        self.assertEqual(bench_calls[0].args[1], 0)

    def test_reed_reconcile_emits_animation_meta(self):
        emitted = {}

        def capture(state):
            emitted.update(state)

        self.reconciler.on_state_emit = capture
        self.reconciler.reconcile(ramp_source="reed")
        self.assertTrue(emitted.get("_animate"))
        self.assertEqual(emitted.get("_trigger"), "reed")
        self.assertGreater(int(emitted.get("_ramp_ms", 0)), 0)

    def test_omitted_phase_level_does_not_command_hardware(self):
        """Missing reed_phases day key → no set_light even on reed reconcile."""
        self.cfg.reed_phase_levels["kitchen_panel"].pop("day", None)
        self.world.update_observed_lights({"kitchen_panel": 37}, {"kitchen_panel": "white"})
        self.reconciler._commanded_lights["kitchen_panel"] = (37, "white")
        self.reconciler.invalidate_commanded_for_reed("kitchen_panel")
        self.reconciler.reconcile(ramp_source="reed")
        self.assertEqual(self._kitchen_calls(), [])
        self.assertNotIn("kitchen_panel", self.reconciler._pending_reed_command)


if __name__ == "__main__":
    unittest.main()