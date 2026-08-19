import unittest

from engine.config_compile import compile_config
from modules.config import config


class TestConfigSmoke(unittest.TestCase):
    def test_compile_real_config(self):
        compiled = compile_config(config)
        self.assertGreater(len(compiled.light_names), 0)
        self.assertIn("floodlights", compiled.relay_names)
        self.assertIn("rooftop_tent", compiled.light_names)

    def test_frontend_control_count(self):
        compiled = compile_config(config)
        expected = {
            "rooftop_tent",
            "kitchen_bench",
            "storage_panel",
            "rear_drawer",
            "accent",
            "ensuite",
            "kitchen_panel",
            "awning",
            "floodlights",
        }
        all_names = set(compiled.light_names) | set(compiled.relay_names)
        self.assertTrue(expected.issubset(all_names))


if __name__ == "__main__":
    unittest.main()