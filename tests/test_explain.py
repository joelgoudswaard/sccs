import time
import unittest
from unittest.mock import MagicMock

from engine.config_compile import compile_config
from engine.explain import build_explain_snapshot
from engine.policy import desired_outputs
from engine.reconcile import Reconciler
from engine.world import WorldStore
from modules.config import config


class TestExplain(unittest.TestCase):
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

    def test_kitchen_bench_not_ramping_when_kitchen_panel_forced(self):
        self.reconciler.invalidate_commanded_for_reed("kitchen_panel")
        self.reconciler.reconcile(ramp_source="reed")
        snap = self.reconciler.explain_snapshot()
        bench = snap["lights"]["kitchen_bench"]
        self.assertFalse(bench.get("ramping"))
        self.assertEqual(bench.get("desired_brightness"), 0)

    def test_forced_reed_open_uses_user_reed_force_source(self):
        snap = build_explain_snapshot(self.world.snapshot(), self.cfg)
        entry = snap["lights"]["kitchen_panel"]
        self.assertEqual(entry["source"], "user_reed_force")
        self.assertEqual(entry["source_label"], "forced reed · phase level")

    def test_explain_interlocked_vs_closed_labels(self):
        self.world.clear_all_reed_forces()
        self.world.update_reeds({
            name: name != "kitchen_bench" for name in self.cfg.reed_names
        })
        snap = build_explain_snapshot(self.world.snapshot(), self.cfg)
        self.assertEqual(snap["lights"]["kitchen_panel"]["source_label"], "reed closed")
        self.assertEqual(snap["lights"]["kitchen_bench"]["source_label"], "interlocked")

    def test_explain_hides_drift_during_ramp_grace(self):
        desired = desired_outputs(self.world.snapshot(), self.cfg)
        self.reconciler._last_desired = desired
        self.reconciler._commanded_lights["kitchen_panel"] = (100, "white")
        self.reconciler._commanded_at["kitchen_panel"] = time.time()

        snap = self.reconciler.explain_snapshot()
        self.assertFalse(snap["lights"]["kitchen_panel"]["drift"])
        self.assertTrue(snap["lights"]["kitchen_panel"]["ramping"])
        self.assertEqual(snap["drifts"], [])
        self.assertFalse(snap["has_drift"])

    def test_explain_shows_drift_after_ramp_grace(self):
        desired = desired_outputs(self.world.snapshot(), self.cfg)
        self.reconciler._last_desired = desired
        self.reconciler._commanded_lights["kitchen_panel"] = (100, "white")
        self.reconciler._commanded_at["kitchen_panel"] = time.time() - self.reconciler._drift_grace_s - 1

        snap = self.reconciler.explain_snapshot()
        self.assertTrue(snap["lights"]["kitchen_panel"]["drift"])
        self.assertNotIn("ramping", snap["lights"]["kitchen_panel"])
        self.assertEqual(len(snap["drifts"]), 1)

    def test_no_mode_drift_when_light_is_off(self):
        """RGB mode is not readable at 0% — both channels read 0 as white."""
        self.world.set_light_intent("awning", 0, "red")
        self.world.update_observed_lights({"awning": 0}, {"awning": "white"})
        desired = desired_outputs(self.world.snapshot(), self.cfg)
        snap = build_explain_snapshot(self.world.snapshot(), self.cfg, desired)
        entry = snap["lights"]["awning"]
        self.assertEqual(entry["desired_brightness"], 0)
        self.assertEqual(entry["desired_mode"], "red")
        self.assertFalse(entry["drift"])
        self.assertNotIn("awning", {d.get("light") for d in snap["drifts"]})

    def test_mode_drift_when_on_and_mode_mismatches(self):
        self.world.set_light_intent("awning", 100, "red")
        self.world.update_observed_lights({"awning": 100}, {"awning": "white"})
        desired = desired_outputs(self.world.snapshot(), self.cfg)
        snap = build_explain_snapshot(self.world.snapshot(), self.cfg, desired)
        entry = snap["lights"]["awning"]
        self.assertTrue(entry["drift"])
        self.assertIn("mode", entry.get("drift_detail", ""))


if __name__ == "__main__":
    unittest.main()