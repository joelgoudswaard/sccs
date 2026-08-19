import unittest

from engine.config_compile import CompiledConfig, compile_config
from engine.intent import LightIntent, RelayIntent
from engine.policy import desired_outputs
from engine.precedence import effective_reed_closed, resolve_light, resolve_screen
from engine.world import WorldState, WorldStore
from modules.config import config as sccs_config


def minimal_cfg() -> CompiledConfig:
    cfg = CompiledConfig()
    cfg.light_names = [
        "rooftop_tent", "accent", "ensuite", "kitchen_bench", "kitchen_panel",
        "awning", "rear_drawer",
    ]
    cfg.pwm_lights = {
        "rooftop_tent": 6,
        "accent": 7,
        "ensuite": 2,
        "kitchen_bench": 10,
        "kitchen_panel": 6,
        "awning": 9,
        "rear_drawer": 8,
        "storage_panel": 9,
    }
    cfg.rgb_lights = {
        "kitchen_panel": {"white": 13, "red": 12, "green": 11},
        "awning": {"white": 5, "red": 4, "green": 3},
    }
    cfg.relay_names = ["floodlights"]
    cfg.reed_names = [
        "rooftop_tent", "kitchen_bench", "kitchen_panel", "rear_drawer",
    ]
    cfg.reed_to_lights = {
        "rooftop_tent": ["rooftop_tent"],
        "kitchen_bench": ["kitchen_bench"],
        "kitchen_panel": ["kitchen_panel"],
        "rear_drawer": ["rear_drawer"],
    }
    cfg.light_to_reed = {
        "rooftop_tent": "rooftop_tent",
        "kitchen_bench": "kitchen_bench",
        "kitchen_panel": "kitchen_panel",
        "rear_drawer": "rear_drawer",
    }
    cfg.interlocks = {"kitchen_bench": ["kitchen_panel"]}
    cfg.ambient_lights = ["accent", "awning"]
    cfg.all_closed_action = "off"
    cfg.reed_phase_levels = {
        "rooftop_tent": {
            "day": (0, "white"),
            "evening": (20, "white"),
            "night": (5, "white"),
        },
        "kitchen_bench": {
            "day": (100, "white"),
            "evening": (30, "white"),
            "night": (5, "white"),
        },
        "kitchen_panel": {
            "day": (100, "white"),
            "evening": (40, "white"),
            "night": (5, "white"),
        },
        "rear_drawer": {
            "day": (0, "white"),
            "evening": (50, "white"),
            "night": (10, "white"),
        },
    }
    cfg.ambient_phase_levels = {
        "accent": {"day": (0, "white"), "evening": (20, "white"), "night": (5, "white")},
        "awning": {"day": (0, "white"), "evening": (20, "white"), "night": (10, "red")},
    }
    cfg.screens = {
        "kitchen": {
            "friendly": "Kitchen",
            "linked_reed": "kitchen_panel",
            "host": "10.10.10.10",
            "username": "joel",
            "brightness_path": "/sys/class/graphics/fb0/blank",
            "icon": "fa-utensils",
            "phase_brightness": {"day": 100, "evening": 30, "night": 5},
        }
    }
    cfg.scenes = {
        "bedtime": {
            "name": "Bedtime",
            "all_off": False,
            "lights": {
                "kitchen_panel": {"type": "fixed", "brightness": 5, "mode": "white"},
                "rooftop_tent": {"type": "fixed", "brightness": 5, "mode": "white"},
                "accent": {"type": "phase", "phase": "night"},
            },
        },
        "all_off": {"name": "All Off", "all_off": True, "lights": {}},
        "goodnight": {
            "name": "Goodnight",
            "all_off": True,
            "lights": {"accent": {"type": "fixed", "brightness": 2, "mode": "white"}},
        },
        "evening_mood": {
            "name": "Evening",
            "evening_levels": True,
            "lights": {"accent": {"type": "phase", "phase": "night"}},
        },
        "night_mood": {
            "name": "Night",
            "night_levels": True,
            "lights": {},
        },
        "day_mood": {
            "name": "Day",
            "day_levels": True,
            "lights": {},
        },
    }
    return cfg


def dim_cfg() -> CompiledConfig:
    cfg = minimal_cfg()
    cfg.all_closed_action = "dim"
    return cfg


def real_cfg() -> CompiledConfig:
    return compile_config(sccs_config)


def _default_reeds(open_names=(), closed_names=()) -> dict:
    """Default: tent/bench/panel/rear_drawer — unspecified default closed."""
    names = ["rooftop_tent", "kitchen_bench", "kitchen_panel", "rear_drawer"]
    reeds = {n: True for n in names}
    for n in open_names:
        reeds[n] = False
    for n in closed_names:
        reeds[n] = True
    return reeds


class PolicyTests(unittest.TestCase):
    # ── Reed-linked automation ──────────────────────────────────────────

    def test_tent_open_user_off_stays_off(self):
        world = WorldState(
            reeds=_default_reeds(open_names=["rooftop_tent"]),
            phase="Evening",
            light_intents={"rooftop_tent": LightIntent(0, expires="until_reed_close")},
        )
        out = desired_outputs(world, minimal_cfg())
        self.assertEqual(out.lights["rooftop_tent"][0], 0)
        self.assertEqual(out.light_sources["rooftop_tent"], "user_intent")

    def test_no_phase_automation_deferred(self):
        world = WorldState(reeds=_default_reeds(open_names=["rooftop_tent"]), phase="")
        out = desired_outputs(world, minimal_cfg())
        self.assertEqual(out.lights["rooftop_tent"][0], 0)
        self.assertEqual(out.light_sources["rooftop_tent"], "phase_pending")

    def test_tent_close_then_open_gets_phase_level(self):
        world = WorldState(reeds=_default_reeds(open_names=["rooftop_tent"]), phase="Evening")
        out = desired_outputs(world, minimal_cfg())
        self.assertEqual(out.lights["rooftop_tent"][0], 20)
        self.assertEqual(out.light_sources["rooftop_tent"], "automation_reed")

    def test_tent_closed_forces_off(self):
        world = WorldState(reeds=_default_reeds(closed_names=["rooftop_tent"]), phase="Evening")
        out = desired_outputs(world, minimal_cfg())
        self.assertEqual(out.lights["rooftop_tent"][0], 0)
        self.assertEqual(out.light_sources["rooftop_tent"], "reed_closed")

    def test_force_reed_closed_overrides_open_hardware(self):
        world = WorldState(
            reeds=_default_reeds(open_names=["rooftop_tent"]),
            reed_forces={"rooftop_tent": True},
            phase="Evening",
        )
        out = desired_outputs(world, minimal_cfg())
        self.assertEqual(out.lights["rooftop_tent"][0], 0)
        self.assertEqual(out.light_sources["rooftop_tent"], "reed_closed")

    def test_phase_evening_vs_night_changes_reed_level(self):
        cfg = minimal_cfg()
        reeds = _default_reeds(open_names=["rooftop_tent"])
        evening = desired_outputs(WorldState(reeds=reeds, phase="Evening"), cfg)
        night = desired_outputs(WorldState(reeds=reeds, phase="Night"), cfg)
        self.assertEqual(evening.lights["rooftop_tent"][0], 20)
        self.assertEqual(night.lights["rooftop_tent"][0], 5)

    def test_forced_phase_overrides_scheduled_phase(self):
        world = WorldState(
            reeds=_default_reeds(open_names=["rooftop_tent"]),
            phase="Day",
            phase_forced="Night",
        )
        out = desired_outputs(world, minimal_cfg())
        self.assertEqual(out.lights["rooftop_tent"][0], 5)
        self.assertEqual(out.light_sources["rooftop_tent"], "automation_reed")

    def test_day_phase_rooftop_stays_off(self):
        world = WorldState(reeds=_default_reeds(open_names=["rooftop_tent"]), phase="Day")
        out = desired_outputs(world, minimal_cfg())
        self.assertEqual(out.lights["rooftop_tent"][0], 0)

    def test_day_phase_rear_drawer_stays_off(self):
        world = WorldState(reeds=_default_reeds(open_names=["rear_drawer"]), phase="Day")
        out = desired_outputs(world, minimal_cfg())
        self.assertEqual(out.lights["rear_drawer"][0], 0)

    def test_omitted_phase_level_is_no_action(self):
        """Missing [reed_phases] key → automation_omit; hold observed, do not force 0."""
        cfg = minimal_cfg()
        del cfg.reed_phase_levels["rear_drawer"]["day"]
        world = WorldState(
            reeds=_default_reeds(open_names=["rear_drawer"]),
            phase="Day",
            observed_lights={"rear_drawer": 42},
            observed_light_modes={"rear_drawer": "white"},
        )
        resolved = resolve_light("rear_drawer", world, cfg)
        self.assertEqual(resolved.source, "automation_omit")
        out = desired_outputs(world, cfg)
        self.assertEqual(out.lights["rear_drawer"][0], 42)
        self.assertEqual(out.light_sources["rear_drawer"], "automation_omit")

    def test_omitted_phase_level_without_observed_stays_zero_in_ui(self):
        cfg = minimal_cfg()
        del cfg.reed_phase_levels["rear_drawer"]["day"]
        world = WorldState(reeds=_default_reeds(open_names=["rear_drawer"]), phase="Day")
        out = desired_outputs(world, cfg)
        self.assertEqual(out.light_sources["rear_drawer"], "automation_omit")
        self.assertEqual(out.lights["rear_drawer"][0], 0)

    def test_explicit_zero_still_forces_off(self):
        """day = 0 is not the same as omitting day — still commands off."""
        world = WorldState(reeds=_default_reeds(open_names=["rear_drawer"]), phase="Day")
        out = desired_outputs(world, minimal_cfg())
        self.assertEqual(out.lights["rear_drawer"][0], 0)
        self.assertEqual(out.light_sources["rear_drawer"], "automation_reed")

    def test_invalid_phase_falls_back_to_evening(self):
        world = WorldState(reeds=_default_reeds(open_names=["rooftop_tent"]), phase="Bonkers")
        out = desired_outputs(world, minimal_cfg())
        self.assertEqual(out.lights["rooftop_tent"][0], 20)

    # ── Ambient lighting ──────────────────────────────────────────────────

    def test_all_reeds_closed_ambient_off(self):
        world = WorldState(reeds=_default_reeds(), phase="Evening")
        out = desired_outputs(world, minimal_cfg())
        self.assertEqual(out.lights["accent"][0], 0)
        self.assertEqual(out.light_sources["accent"], "automation_all_closed")

    def test_any_reed_open_ambient_on(self):
        world = WorldState(reeds=_default_reeds(open_names=["rooftop_tent"]), phase="Night")
        out = desired_outputs(world, minimal_cfg())
        self.assertEqual(out.lights["accent"][0], 5)

    def test_all_closed_action_dim_uses_night_level(self):
        world = WorldState(reeds=_default_reeds(), phase="Evening")
        out = desired_outputs(world, dim_cfg())
        self.assertEqual(out.lights["accent"][0], 5)
        self.assertEqual(out.light_sources["accent"], "automation_all_closed_dim")

    def test_user_intent_on_ambient_light(self):
        world = WorldState(
            reeds=_default_reeds(open_names=["rooftop_tent"]),
            phase="Evening",
            light_intents={"accent": LightIntent(40, expires="until_reed_close")},
        )
        out = desired_outputs(world, minimal_cfg())
        self.assertEqual(out.lights["accent"][0], 40)
        self.assertEqual(out.light_sources["accent"], "user_intent")

    def test_rgb_mode_on_awning_at_night(self):
        world = WorldState(reeds=_default_reeds(open_names=["rooftop_tent"]), phase="Night")
        out = desired_outputs(world, minimal_cfg())
        self.assertEqual(out.lights["awning"][0], 10)
        self.assertEqual(out.light_modes["awning"], "red")

    # ── Standalone lights ─────────────────────────────────────────────────

    def test_standalone_light_defaults_off(self):
        world = WorldState(reeds=_default_reeds(open_names=["rooftop_tent"]), phase="Evening")
        out = desired_outputs(world, minimal_cfg())
        self.assertEqual(out.lights["ensuite"][0], 0)

    # ── Scenes ────────────────────────────────────────────────────────────

    def test_user_intent_beats_scene(self):
        world = WorldState(
            reeds=_default_reeds(open_names=["kitchen_panel"]),
            phase="Evening",
            active_scene="bedtime",
            light_intents={"kitchen_panel": LightIntent(80, expires="until_reed_close")},
        )
        out = desired_outputs(world, minimal_cfg())
        self.assertEqual(out.lights["kitchen_panel"][0], 80)
        self.assertEqual(out.light_sources["kitchen_panel"], "user_intent")

    def test_scene_applies_when_no_intent(self):
        world = WorldState(
            reeds=_default_reeds(open_names=["kitchen_panel"]),
            phase="Evening",
            active_scene="bedtime",
        )
        out = desired_outputs(world, minimal_cfg())
        self.assertEqual(out.lights["kitchen_panel"][0], 5)
        self.assertEqual(out.light_sources["kitchen_panel"], "scene")

    def test_stale_intents_block_transient_scene_snapshot(self):
        """set_scene clears intents before reconciling so scene levels are not skipped."""
        cfg = minimal_cfg()
        zero_intents = {
            name: LightIntent(0, expires="until_phase_change") for name in cfg.light_names
        }
        world = WorldState(
            reeds=_default_reeds(open_names=["kitchen_panel"]),
            phase="Evening",
            active_scene="bedtime",
            light_intents=zero_intents,
        )
        out = desired_outputs(world, cfg)
        self.assertEqual(out.lights["kitchen_panel"][0], 0)
        self.assertEqual(out.light_sources["kitchen_panel"], "user_intent")

    def test_transient_scene_applies_when_intents_cleared(self):
        cfg = minimal_cfg()
        world = WorldStore(cfg.reed_names, cfg.light_names, [])
        world.set_light_to_reed_map(cfg.light_to_reed)
        world.set_phase("Evening", invalidate=False)
        world.update_reeds(_default_reeds(open_names=["kitchen_panel"]))
        for light in cfg.light_names:
            world.set_light_intent(light, 0, expires="until_phase_change")

        world.clear_all_light_intents()
        world.set_active_scene("bedtime")
        out = desired_outputs(world.snapshot(), cfg)

        self.assertEqual(out.lights["kitchen_panel"][0], 5)
        self.assertEqual(out.light_sources["kitchen_panel"], "scene")

    def test_scene_phase_reference(self):
        world = WorldState(
            reeds=_default_reeds(open_names=["rooftop_tent"]),
            phase="Evening",
            active_scene="bedtime",
        )
        out = desired_outputs(world, minimal_cfg())
        self.assertEqual(out.lights["accent"][0], 5)
        self.assertEqual(out.light_sources["accent"], "scene")

    def test_scene_skips_reed_closed_light(self):
        world = WorldState(
            reeds=_default_reeds(open_names=["kitchen_panel"], closed_names=["rooftop_tent"]),
            phase="Evening",
            active_scene="bedtime",
        )
        out = desired_outputs(world, minimal_cfg())
        self.assertEqual(out.lights["rooftop_tent"][0], 0)
        self.assertEqual(out.light_sources["rooftop_tent"], "reed_closed")
        self.assertEqual(out.lights["kitchen_panel"][0], 5)
        self.assertEqual(out.light_sources["kitchen_panel"], "scene")

    def test_scene_ambient_skipped_when_all_reeds_closed(self):
        world = WorldState(reeds=_default_reeds(), phase="Evening", active_scene="bedtime")
        out = desired_outputs(world, minimal_cfg())
        self.assertEqual(out.lights["accent"][0], 0)
        self.assertNotEqual(out.light_sources["accent"], "scene")

    def test_all_off_scene(self):
        world = WorldState(
            reeds=_default_reeds(
                open_names=["rooftop_tent", "kitchen_bench", "kitchen_panel", "rear_drawer"]
            ),
            phase="Evening",
            active_scene="all_off",
        )
        out = desired_outputs(world, minimal_cfg())
        for light in minimal_cfg().light_names:
            self.assertEqual(out.lights[light][0], 0)
            self.assertEqual(out.light_sources[light], "scene_all_off")

    def test_goodnight_scene_all_off_except_accent(self):
        world = WorldState(
            reeds=_default_reeds(),
            phase="Night",
            active_scene="goodnight",
        )
        out = desired_outputs(world, minimal_cfg())
        self.assertEqual(out.lights["accent"][0], 2)
        self.assertEqual(out.light_sources["accent"], "scene")
        for light in minimal_cfg().light_names:
            if light == "accent":
                continue
            self.assertEqual(out.lights[light][0], 0)

    def test_evening_levels_scene(self):
        world = WorldState(
            reeds=_default_reeds(open_names=["kitchen_bench", "kitchen_panel"]),
            phase="Night",
            active_scene="evening_mood",
        )
        out = desired_outputs(world, minimal_cfg())
        self.assertEqual(out.lights["kitchen_panel"][0], 40)
        self.assertEqual(out.light_sources["kitchen_panel"], "scene_evening")
        self.assertEqual(out.lights["accent"][0], 5)
        self.assertEqual(out.light_sources["accent"], "scene")

    def test_night_levels_scene(self):
        world = WorldState(
            reeds=_default_reeds(open_names=["kitchen_panel"]),
            phase="Evening",
            active_scene="night_mood",
        )
        out = desired_outputs(world, minimal_cfg())
        self.assertEqual(out.lights["kitchen_panel"][0], 5)
        self.assertEqual(out.light_sources["kitchen_panel"], "scene_night")

    def test_day_levels_scene(self):
        world = WorldState(
            reeds=_default_reeds(open_names=["kitchen_bench", "kitchen_panel"]),
            phase="Night",
            active_scene="day_mood",
        )
        out = desired_outputs(world, minimal_cfg())
        self.assertEqual(out.lights["kitchen_panel"][0], 100)
        self.assertEqual(out.light_sources["kitchen_panel"], "scene_day")

    def test_reed_closed_beats_scene(self):
        world = WorldState(
            reeds=_default_reeds(closed_names=["rooftop_tent"]),
            phase="Evening",
            active_scene="evening_mood",
        )
        out = desired_outputs(world, minimal_cfg())
        self.assertEqual(out.lights["rooftop_tent"][0], 0)
        self.assertEqual(out.light_sources["rooftop_tent"], "reed_closed")

    # ── Interlocks & forced reeds ─────────────────────────────────────────

    def test_kitchen_bench_interlock_panel_closed(self):
        cfg = minimal_cfg()
        world = WorldState(
            reeds=_default_reeds(open_names=["kitchen_bench"], closed_names=["kitchen_panel"]),
            phase="Evening",
        )
        self.assertTrue(effective_reed_closed(world, "kitchen_bench", cfg))
        out = desired_outputs(world, cfg)
        self.assertEqual(out.lights["kitchen_bench"][0], 0)
        self.assertEqual(out.light_sources["kitchen_bench"], "reed_interlocked")

    def test_kitchen_bench_closed_shows_reed_closed_not_interlocked(self):
        cfg = minimal_cfg()
        world = WorldState(
            reeds=_default_reeds(closed_names=["kitchen_bench", "kitchen_panel"]),
            phase="Evening",
        )
        out = desired_outputs(world, cfg)
        self.assertEqual(out.light_sources["kitchen_bench"], "reed_closed")

    def test_kitchen_bench_open_when_panel_open(self):
        cfg = minimal_cfg()
        world = WorldState(
            reeds=_default_reeds(open_names=["kitchen_bench", "kitchen_panel"]),
            phase="Evening",
        )
        self.assertFalse(effective_reed_closed(world, "kitchen_bench", cfg))
        out = desired_outputs(world, cfg)
        self.assertEqual(out.lights["kitchen_bench"][0], 30)

    def test_force_panel_open_unblocks_bench_interlock(self):
        cfg = minimal_cfg()
        world = WorldState(
            reeds=_default_reeds(open_names=["kitchen_bench"], closed_names=["kitchen_panel"]),
            reed_forces={"kitchen_panel": False},
            phase="Evening",
        )
        self.assertFalse(effective_reed_closed(world, "kitchen_bench", cfg))
        out = desired_outputs(world, cfg)
        self.assertEqual(out.lights["kitchen_bench"][0], 30)

    def test_force_panel_closed_blocks_bench(self):
        cfg = minimal_cfg()
        world = WorldState(
            reeds=_default_reeds(open_names=["kitchen_bench", "kitchen_panel"]),
            reed_forces={"kitchen_panel": True},
            phase="Evening",
        )
        self.assertTrue(effective_reed_closed(world, "kitchen_bench", cfg))
        out = desired_outputs(world, cfg)
        self.assertEqual(out.lights["kitchen_bench"][0], 0)

    def test_force_bench_open_panel_closed_still_interlocked(self):
        """Forced bench open cannot bypass a closed/forced-closed panel."""
        cfg = real_cfg()
        world = WorldState(
            reeds=_default_reeds(closed_names=["kitchen_panel", "kitchen_bench"]),
            reed_forces={"kitchen_bench": False, "kitchen_panel": True},
            phase="Day",
        )
        self.assertTrue(effective_reed_closed(world, "kitchen_bench", cfg))
        out = desired_outputs(world, cfg)
        self.assertEqual(out.lights["kitchen_bench"][0], 0)
        self.assertEqual(out.lights["kitchen_panel"][0], 0)
        self.assertEqual(out.light_sources["kitchen_bench"], "reed_interlocked")
        self.assertEqual(out.light_sources["kitchen_panel"], "reed_closed")

    def test_force_panel_closed_turns_off_forced_open_bench(self):
        cfg = real_cfg()
        world = WorldState(
            reeds=_default_reeds(open_names=["kitchen_bench", "kitchen_panel"]),
            reed_forces={"kitchen_panel": True, "kitchen_bench": False},
            phase="Day",
        )
        out = desired_outputs(world, cfg)
        self.assertEqual(out.lights["kitchen_panel"][0], 0)
        self.assertEqual(out.lights["kitchen_bench"][0], 0)

    def test_force_panel_open_with_forced_open_bench_both_on(self):
        cfg = real_cfg()
        world = WorldState(
            reeds=_default_reeds(closed_names=["kitchen_panel", "kitchen_bench"]),
            reed_forces={"kitchen_panel": False, "kitchen_bench": False},
            phase="Day",
        )
        out = desired_outputs(world, cfg)
        self.assertEqual(out.lights["kitchen_panel"][0], 100)
        self.assertEqual(out.lights["kitchen_bench"][0], 100)

    def test_kitchen_interlock_panel_closed_bench_opens_no_action(self):
        """Bench reed open while panel closed — bench light stays off."""
        cfg = real_cfg()
        world = WorldState(
            reeds=_default_reeds(open_names=["kitchen_bench"], closed_names=["kitchen_panel"]),
            phase="Day",
        )
        out = desired_outputs(world, cfg)
        self.assertEqual(out.lights["kitchen_bench"][0], 0)
        self.assertEqual(out.lights["kitchen_panel"][0], 0)

    def test_kitchen_interlock_bench_open_panel_opens_both_on(self):
        """Bench already open; panel opens — both lights to phase levels."""
        cfg = real_cfg()
        world = WorldState(
            reeds=_default_reeds(open_names=["kitchen_bench", "kitchen_panel"]),
            phase="Day",
        )
        out = desired_outputs(world, cfg)
        self.assertEqual(out.lights["kitchen_panel"][0], 100)
        self.assertEqual(out.lights["kitchen_bench"][0], 100)

    def test_kitchen_interlock_bench_closed_panel_opens_panel_only(self):
        """Panel opens while bench closed — panel light only."""
        cfg = real_cfg()
        world = WorldState(
            reeds=_default_reeds(open_names=["kitchen_panel"], closed_names=["kitchen_bench"]),
            phase="Day",
        )
        out = desired_outputs(world, cfg)
        self.assertEqual(out.lights["kitchen_panel"][0], 100)
        self.assertEqual(out.lights["kitchen_bench"][0], 0)

    def test_kitchen_interlock_panel_open_bench_opens_bench_on(self):
        """Panel already open; bench opens — bench light to phase level."""
        cfg = real_cfg()
        world = WorldState(
            reeds=_default_reeds(open_names=["kitchen_panel", "kitchen_bench"]),
            phase="Day",
        )
        out = desired_outputs(world, cfg)
        self.assertEqual(out.lights["kitchen_bench"][0], 100)

    def test_kitchen_interlock_panel_closes_both_off(self):
        """Panel closes — both lights off even if bench reed still open."""
        cfg = real_cfg()
        world = WorldState(
            reeds=_default_reeds(open_names=["kitchen_bench"], closed_names=["kitchen_panel"]),
            phase="Day",
        )
        out = desired_outputs(world, cfg)
        self.assertEqual(out.lights["kitchen_panel"][0], 0)
        self.assertEqual(out.lights["kitchen_bench"][0], 0)

    # ── Screens ───────────────────────────────────────────────────────────

    def test_screen_day_brightness_when_panel_open(self):
        world = WorldState(reeds=_default_reeds(open_names=["kitchen_panel"]), phase="day")
        screen = minimal_cfg().screens["kitchen"]
        self.assertEqual(resolve_screen(screen, world, minimal_cfg()), 100)

    def test_screen_evening_brightness_when_panel_open(self):
        world = WorldState(reeds=_default_reeds(open_names=["kitchen_panel"]), phase="evening")
        screen = minimal_cfg().screens["kitchen"]
        self.assertEqual(resolve_screen(screen, world, minimal_cfg()), 30)

    def test_screen_night_brightness_when_panel_open(self):
        world = WorldState(reeds=_default_reeds(open_names=["kitchen_panel"]), phase="night")
        screen = minimal_cfg().screens["kitchen"]
        self.assertEqual(resolve_screen(screen, world, minimal_cfg()), 5)

    def test_screen_off_when_panel_closed(self):
        world = WorldState(reeds=_default_reeds(closed_names=["kitchen_panel"]), phase="day")
        screen = minimal_cfg().screens["kitchen"]
        self.assertEqual(resolve_screen(screen, world, minimal_cfg()), 0)

    def test_screen_follows_forced_panel_open(self):
        world = WorldState(
            reeds=_default_reeds(closed_names=["kitchen_panel"]),
            reed_forces={"kitchen_panel": False},
            phase="evening",
        )
        out = desired_outputs(world, minimal_cfg())
        self.assertEqual(out.screens["kitchen"], 30)

    # ── Intent lifecycle ──────────────────────────────────────────────────

    def test_reed_transition_clears_linked_light_intents(self):
        world = WorldStore(["rooftop_tent"], ["rooftop_tent", "accent"], [])
        world.set_light_to_reed_map({"rooftop_tent": "rooftop_tent"})
        world.set_light_intent("rooftop_tent", 50, expires="until_reed_close")
        world.set_light_intent("accent", 25, expires="until_phase_change")
        world.update_reeds({"rooftop_tent": True}, transition_reeds=["rooftop_tent"])
        snap = world.snapshot()
        self.assertNotIn("rooftop_tent", snap.light_intents)
        self.assertIn("accent", snap.light_intents)

    def test_phase_change_clears_all_light_intents(self):
        world = WorldStore(["rooftop_tent"], ["rooftop_tent", "accent"], [])
        world.set_light_intent("rooftop_tent", 50, expires="until_reed_close")
        world.set_light_intent("accent", 25, expires="until_phase_change")
        world.set_phase("Night", invalidate=True)
        snap = world.snapshot()
        self.assertEqual(snap.light_intents, {})

    def test_phase_change_clears_intent_restores_automation(self):
        cfg = minimal_cfg()
        world = WorldStore(cfg.reed_names, cfg.light_names, [])
        world.set_light_to_reed_map(cfg.light_to_reed)
        world.set_phase("Day", invalidate=False)
        world.update_reeds(_default_reeds(open_names=["kitchen_panel"]))
        world.set_light_intent("kitchen_panel", 0, expires="until_reed_close")
        world.set_phase("Evening", invalidate=True)
        out = desired_outputs(world.snapshot(), cfg)
        self.assertNotIn("kitchen_panel", world.snapshot().light_intents)
        self.assertEqual(out.lights["kitchen_panel"][0], 40)
        self.assertEqual(out.light_sources["kitchen_panel"], "automation_reed")

    def test_reed_open_clears_intent_restores_automation(self):
        cfg = minimal_cfg()
        world = WorldStore(cfg.reed_names, cfg.light_names, [])
        world.set_light_to_reed_map(cfg.light_to_reed)
        world.set_phase("Evening", invalidate=False)
        world.update_reeds(_default_reeds(closed_names=["rooftop_tent"]))
        world.set_light_intent("rooftop_tent", 0, expires="until_reed_close")
        world.update_reeds(
            _default_reeds(open_names=["rooftop_tent"]),
            transition_reeds=["rooftop_tent"],
        )
        out = desired_outputs(world.snapshot(), cfg)
        self.assertNotIn("rooftop_tent", world.snapshot().light_intents)
        self.assertEqual(out.lights["rooftop_tent"][0], 20)
        self.assertEqual(out.light_sources["rooftop_tent"], "automation_reed")

    def test_reed_force_clears_intents_restores_automation(self):
        """Operator reed force clears all slider intents (same as phase force)."""
        cfg = minimal_cfg()
        world = WorldStore(cfg.reed_names, cfg.light_names, [])
        world.set_light_to_reed_map(cfg.light_to_reed)
        world.set_phase("Evening", invalidate=False)
        world.update_reeds(_default_reeds(closed_names=["kitchen_panel"]))
        world.set_light_intent("kitchen_panel", 0, expires="until_reed_close")
        world.set_light_intent("awning", 54, "red", expires="until_reed_close")
        world.set_reed_force("kitchen_panel", False)
        world.clear_all_light_intents()
        snap = world.snapshot()
        self.assertEqual(snap.light_intents, {})
        out = desired_outputs(snap, cfg)
        self.assertEqual(out.lights["kitchen_panel"][0], 40)
        self.assertEqual(out.light_sources["kitchen_panel"], "automation_reed")
        self.assertGreater(out.lights["awning"][0], 0)
        self.assertEqual(out.light_sources["awning"], "automation_ambient")

    def test_until_scene_clear_wiped_on_scene_activate(self):
        world = WorldStore([], ["accent"], [])
        world.set_light_intent("accent", 33, expires="until_scene_clear")
        world.set_active_scene("bedtime")
        snap = world.snapshot()
        self.assertNotIn("accent", snap.light_intents)

    def test_active_scene_persists_over_phase_automation(self):
        world = WorldState(
            reeds=_default_reeds(open_names=["kitchen_panel"]),
            phase="Evening",
            active_scene="night_mood",
        )
        out = desired_outputs(world, minimal_cfg())
        self.assertEqual(out.lights["kitchen_panel"][0], 5)
        self.assertEqual(out.light_sources["kitchen_panel"], "scene_night")

    def test_last_scene_cleared_on_reed_transition(self):
        world = WorldStore(["kitchen_panel"], ["kitchen_panel"], [])
        world.set_active_scene("night_mood")
        world.update_reeds({"kitchen_panel": False}, transition_reeds=["kitchen_panel"])
        snap = world.snapshot()
        self.assertIsNone(snap.last_scene)
        self.assertIsNone(snap.active_scene)

    def test_reed_force_clears_active_scene(self):
        world = WorldStore(["kitchen_panel"], ["kitchen_panel"], [])
        world.set_active_scene("night_mood")
        world.set_reed_force("kitchen_panel", False)
        snap = world.snapshot()
        self.assertIsNone(snap.active_scene)
        self.assertIsNone(snap.last_scene)

    def test_reed_transition_clears_scene_open_uses_phase_not_global_ramp(self):
        from engine.reconcile import Reconciler

        class TrackingEsp32:
            def __init__(self):
                self.commanded = []

            def set_light(self, name, *args, **kwargs):
                self.commanded.append(name)

            def read_lights(self):
                return {}, {}

        class FakeRelays:
            def read_relays(self):
                return {}

            def set_relay(self, *args, **kwargs):
                pass

        cfg = minimal_cfg()
        world = WorldStore(cfg.reed_names, cfg.light_names, [])
        world.set_light_to_reed_map(cfg.light_to_reed)
        world.set_phase("Evening", invalidate=False)
        world.update_reeds(_default_reeds(closed_names=["kitchen_panel", "rear_drawer"]))
        world.set_active_scene("night_mood")

        esp32 = TrackingEsp32()
        rec = Reconciler(
            world=world,
            cfg=cfg,
            esp32_actuator=esp32,
            relay_actuator=FakeRelays(),
        )
        rec._commanded_lights["rear_drawer"] = (10, "white")
        rec._commanded_lights["kitchen_panel"] = (5, "white")

        world.update_reeds(
            _default_reeds(open_names=["kitchen_panel"]),
            transition_reeds=["kitchen_panel"],
        )
        snap = world.snapshot()
        self.assertIsNone(snap.active_scene)

        rec.invalidate_commanded_for_reed("kitchen_panel")
        rec.reconcile(ramp_source="reed")

        self.assertEqual(set(esp32.commanded), {"kitchen_panel"})
        self.assertEqual(rec._last_desired.lights["kitchen_panel"][0], 40)
        self.assertEqual(rec._last_desired.light_sources["kitchen_panel"], "automation_reed")
        self.assertEqual(rec._last_desired.lights["rear_drawer"][0], 10)
        self.assertNotIn("rear_drawer", esp32.commanded)

    def test_last_scene_cleared_on_phase_invalidate(self):
        world = WorldStore([], ["accent"], [])
        world.set_active_scene("night_mood")
        world.set_phase("Night", invalidate=True)
        snap = world.snapshot()
        self.assertIsNone(snap.last_scene)
        self.assertIsNone(snap.active_scene)

    def test_last_scene_cleared_on_light_intent(self):
        world = WorldStore([], ["accent"], [])
        world.set_active_scene("night_mood")
        world.set_light_intent("accent", 25)
        snap = world.snapshot()
        self.assertIsNone(snap.last_scene)
        self.assertIsNone(snap.active_scene)

    def test_reed_reopen_after_intent_cleared_restores_automation(self):
        cfg = minimal_cfg()
        world = WorldStore(
            ["rooftop_tent", "kitchen_bench", "kitchen_panel", "rear_drawer"],
            cfg.light_names,
            [],
        )
        world.set_light_to_reed_map(cfg.light_to_reed)
        world.set_phase("Evening")
        world.set_light_intent("rooftop_tent", 0, expires="until_reed_close")
        world.update_reeds(
            _default_reeds(closed_names=["rooftop_tent"]),
            transition_reeds=["rooftop_tent"],
        )
        self.assertNotIn("rooftop_tent", world.snapshot().light_intents)
        world.update_reeds(
            _default_reeds(open_names=["rooftop_tent"]),
            transition_reeds=["rooftop_tent"],
        )
        out = desired_outputs(world.snapshot(), cfg)
        self.assertEqual(out.lights["rooftop_tent"][0], 20)
        self.assertEqual(out.light_sources["rooftop_tent"], "automation_reed")

    def test_intent_brightness_clamped(self):
        world = WorldState(
            reeds=_default_reeds(open_names=["rooftop_tent"]),
            phase="Evening",
            light_intents={"rooftop_tent": LightIntent(150, expires="until_reed_close")},
        )
        out = desired_outputs(world, minimal_cfg())
        self.assertEqual(out.lights["rooftop_tent"][0], 100)

    def test_rooftop_safety_clamp(self):
        world = WorldState(
            reeds=_default_reeds(closed_names=["rooftop_tent"]),
            phase="Evening",
            light_intents={"rooftop_tent": LightIntent(50, expires="manual")},
        )
        resolved = resolve_light("rooftop_tent", world, minimal_cfg())
        self.assertEqual(resolved.brightness, 0)
        self.assertEqual(resolved.source, "safety_rooftop")

    def test_user_intent_overrides_reed_closed(self):
        world = WorldState(
            reeds=_default_reeds(closed_names=["kitchen_bench", "kitchen_panel"]),
            phase="Evening",
            light_intents={"kitchen_bench": LightIntent(39, expires="until_reed_close")},
        )
        resolved = resolve_light("kitchen_bench", world, minimal_cfg())
        self.assertEqual(resolved.brightness, 39)
        self.assertEqual(resolved.source, "user_intent")

    # ── Relays ────────────────────────────────────────────────────────────

    def test_relay_intent_overrides_observed(self):
        world = WorldState(
            observed_relays={"floodlights": False},
            relay_intents={"floodlights": RelayIntent(True)},
        )
        out = desired_outputs(world, minimal_cfg())
        self.assertTrue(out.relays["floodlights"])

    def test_relay_defaults_to_observed(self):
        world = WorldState(observed_relays={"floodlights": True})
        out = desired_outputs(world, minimal_cfg())
        self.assertTrue(out.relays["floodlights"])

    # ── Real sccs.conf smoke tests ────────────────────────────────────────

    def test_real_config_bedtime_scene(self):
        cfg = real_cfg()
        world = WorldState(
            reeds={name: False for name in cfg.reed_names},
            phase="Evening",
            active_scene="bedtime",
        )
        out = desired_outputs(world, cfg)
        # Matches [scenes.bedtime] in config/sccs.conf
        self.assertEqual(out.lights["kitchen_panel"][0], 1)
        self.assertEqual(out.light_modes["kitchen_panel"], "white")
        self.assertEqual(out.lights["kitchen_bench"][0], 1)
        self.assertEqual(out.lights["storage_panel"][0], 1)
        self.assertEqual(out.lights["accent"][0], 1)  # accent = night
        self.assertEqual(out.lights["rooftop_tent"][0], 1)
        self.assertEqual(out.lights["ensuite"][0], 10)

    def test_real_config_bathroom_scene(self):
        cfg = real_cfg()
        world = WorldState(
            reeds={name: False for name in cfg.reed_names},
            phase="Evening",
            active_scene="bathroom",
        )
        out = desired_outputs(world, cfg)
        self.assertEqual(out.lights["ensuite"][0], 10)
        self.assertEqual(out.lights["rooftop_tent"][0], 2)
        self.assertEqual(out.lights["accent"][0], 1)  # accent = night

    def test_real_config_kitchen_screen_follows_panel(self):
        cfg = real_cfg()
        open_panel = WorldState(
            reeds={name: True for name in cfg.reed_names},
            phase="day",
        )
        open_panel.reeds["kitchen_panel"] = False
        closed_panel = WorldState(
            reeds={name: True for name in cfg.reed_names},
            phase="day",
        )
        out_open = desired_outputs(open_panel, cfg)
        out_closed = desired_outputs(closed_panel, cfg)
        self.assertGreater(out_open.screens["kitchen"], 0)
        self.assertEqual(out_closed.screens["kitchen"], 0)

    def test_bedtime_scene_leaves_undefined_lights_on_automation_source(self):
        world = WorldState(
            reeds=_default_reeds(open_names=["kitchen_panel", "rear_drawer"]),
            phase="Evening",
            active_scene="bedtime",
        )
        out = desired_outputs(world, minimal_cfg())
        self.assertEqual(out.light_sources["kitchen_panel"], "scene")
        self.assertEqual(out.light_sources["rear_drawer"], "automation_reed")


class SceneReconcileTests(unittest.TestCase):
    def test_reed_force_after_scene_only_commands_affected_light(self):
        from engine.reconcile import Reconciler

        class TrackingEsp32:
            def __init__(self):
                self.commanded = []

            def read_lights(self):
                return {}, {}

            def set_light(self, name, *args, **kwargs):
                self.commanded.append(name)

        class FakeRelays:
            def read_relays(self):
                return {}

            def set_relay(self, *args, **kwargs):
                pass

        cfg = minimal_cfg()
        world = WorldStore(cfg.reed_names, cfg.light_names, [])
        world.set_light_to_reed_map(cfg.light_to_reed)
        world.set_phase("Evening", invalidate=False)
        world.update_reeds(_default_reeds(open_names=["kitchen_panel", "rear_drawer"]))
        world.set_active_scene("night_mood")
        world.set_reed_force("kitchen_panel", None)  # clears scene

        esp32 = TrackingEsp32()
        rec = Reconciler(
            world=world,
            cfg=cfg,
            esp32_actuator=esp32,
            relay_actuator=FakeRelays(),
        )
        rec._commanded_lights["kitchen_panel"] = (5, "white")
        rec._commanded_lights["rear_drawer"] = (10, "white")
        rec._commanded_lights["accent"] = (5, "white")
        rec.invalidate_commanded_for_reed("kitchen_panel")
        rec.reconcile(ramp_source="reed")

        self.assertEqual(set(esp32.commanded), {"kitchen_panel"})
        self.assertEqual(rec._last_desired.lights["rear_drawer"][0], 10)
        self.assertNotIn("rear_drawer", esp32.commanded)

    def test_scene_reconcile_only_commands_scene_lights(self):
        from engine.reconcile import Reconciler

        class TrackingEsp32:
            def __init__(self):
                self.commanded = []

            def read_lights(self):
                return {"rear_drawer": 50, "kitchen_panel": 40}, {}

            def set_light(self, name, *args, **kwargs):
                self.commanded.append(name)

        class FakeRelays:
            def read_relays(self):
                return {}

            def set_relay(self, *args, **kwargs):
                pass

        cfg = minimal_cfg()
        world = WorldStore(cfg.reed_names, cfg.light_names, [])
        world.set_light_to_reed_map(cfg.light_to_reed)
        world.set_phase("Evening", invalidate=False)
        world.update_reeds(_default_reeds(open_names=list(cfg.reed_names)))
        world.update_observed_lights({"rear_drawer": 50, "kitchen_panel": 40, "accent": 0})

        esp32 = TrackingEsp32()
        rec = Reconciler(
            world=world,
            cfg=cfg,
            esp32_actuator=esp32,
            relay_actuator=FakeRelays(),
        )
        rec.reconcile(ramp_source="reed")
        esp32.commanded.clear()

        world.clear_all_light_intents()
        world.set_active_scene("bedtime")
        rec.reconcile(ramp_source="scene")

        self.assertIn("kitchen_panel", esp32.commanded)
        self.assertNotIn("rear_drawer", esp32.commanded)
        self.assertEqual(rec._last_desired.lights["rear_drawer"][0], 50)

    def test_all_off_scene_commands_every_light(self):
        from engine.reconcile import Reconciler

        class TrackingEsp32:
            def __init__(self):
                self.commanded = []

            def read_lights(self):
                return {}, {}

            def set_light(self, name, *args, **kwargs):
                self.commanded.append(name)

        class FakeRelays:
            def read_relays(self):
                return {}

            def set_relay(self, *args, **kwargs):
                pass

        cfg = minimal_cfg()
        world = WorldStore(cfg.reed_names, cfg.light_names, [])
        world.set_light_to_reed_map(cfg.light_to_reed)
        world.set_phase("Evening", invalidate=False)
        world.update_reeds(_default_reeds(open_names=list(cfg.reed_names)))

        esp32 = TrackingEsp32()
        rec = Reconciler(
            world=world,
            cfg=cfg,
            esp32_actuator=esp32,
            relay_actuator=FakeRelays(),
        )
        world.set_active_scene("all_off")
        rec.reconcile(ramp_source="scene")

        self.assertEqual(set(esp32.commanded), set(cfg.light_names))

    def test_ui_reconcile_only_commands_intent_light(self):
        from engine.reconcile import Reconciler

        class TrackingEsp32:
            def __init__(self):
                self.commanded = []

            def read_lights(self):
                return {"kitchen_panel": 5, "accent": 5}, {"kitchen_panel": "white"}

            def set_light(self, name, *args, **kwargs):
                self.commanded.append(name)

        class FakeRelays:
            def read_relays(self):
                return {}

            def set_relay(self, *args, **kwargs):
                pass

        cfg = minimal_cfg()
        world = WorldStore(cfg.reed_names, cfg.light_names, [])
        world.set_light_to_reed_map(cfg.light_to_reed)
        world.set_phase("Evening", invalidate=False)
        world.update_reeds(_default_reeds(open_names=list(cfg.reed_names)))
        world.update_observed_lights(
            {"kitchen_panel": 5, "accent": 5},
            {"kitchen_panel": "white"},
        )

        esp32 = TrackingEsp32()
        rec = Reconciler(
            world=world,
            cfg=cfg,
            esp32_actuator=esp32,
            relay_actuator=FakeRelays(),
        )

        world.set_active_scene("bedtime")
        rec.reconcile(ramp_source="scene")
        esp32.commanded.clear()

        world.set_light_intent("accent", 60)
        rec.reconcile(ramp_source="ui")

        self.assertEqual(esp32.commanded, ["accent"])
        self.assertEqual(rec._last_desired.lights["kitchen_panel"][0], 5)


class RelayIntentLifecycleTests(unittest.TestCase):
    """Relays use expires=manual — survive scene activation."""

    def test_relay_intent_survives_scene_clear(self):
        cfg = minimal_cfg()
        world = WorldStore(cfg.reed_names, cfg.light_names, cfg.relay_names)
        world.set_light_to_reed_map(cfg.light_to_reed)
        world.set_phase("Evening", invalidate=False)
        world.update_reeds(_default_reeds(open_names=list(cfg.reed_names)))
        world.set_relay_intent("floodlights", True)
        world.clear_all_light_intents()
        world.set_active_scene("bedtime")

        out = desired_outputs(world.snapshot(), cfg)
        self.assertTrue(out.relays["floodlights"])


if __name__ == "__main__":
    unittest.main()