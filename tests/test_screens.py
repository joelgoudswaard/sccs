import subprocess
import unittest
from unittest.mock import patch

from actuators.screens import (
    DbusKdeBrightnessControl,
    DbusScreenSaverControl,
    ScreenActuator,
    SysfsControl,
    _color_mode_ssh_command,
    _compose_screen_remote,
    color_mode_remote_script,
    _effective_blank_path,
    _parse_busctl_bool,
    _parse_busctl_int,
    _parse_control,
    _state_from_read,
)


class ScreenControlTests(unittest.TestCase):
    def test_parse_dbus_screensaver_default_path(self):
        control = _parse_control("dbus:org.freedesktop.ScreenSaver")
        self.assertIsInstance(control, DbusScreenSaverControl)
        self.assertEqual(control.service, "org.freedesktop.ScreenSaver")
        self.assertEqual(control.object_path, "/org/freedesktop/ScreenSaver")

    def test_parse_dbus_kde_brightness(self):
        control = _parse_control(
            "dbus:org.kde.ScreenBrightness:/org/kde/ScreenBrightness/display0"
        )
        self.assertIsInstance(control, DbusKdeBrightnessControl)
        self.assertEqual(control.service, "org.kde.ScreenBrightness")
        self.assertEqual(control.object_path, "/org/kde/ScreenBrightness/display0")

    def test_parse_sysfs_blank(self):
        control = _parse_control("/sys/class/graphics/fb0/blank")
        self.assertIsInstance(control, SysfsControl)
        self.assertEqual(control.path, "/sys/class/graphics/fb0/blank")

    def test_parse_wlr_path(self):
        control = _parse_control("wlr:HDMI-A-1")
        self.assertIsInstance(control, SysfsControl)
        self.assertEqual(control.path, "wlr:HDMI-A-1")

    def test_brightness_adjustable_flags(self):
        from actuators.screens import brightness_is_adjustable

        self.assertFalse(brightness_is_adjustable("wlr:HDMI-A-1"))
        self.assertFalse(brightness_is_adjustable("/sys/class/graphics/fb0/blank"))
        self.assertFalse(brightness_is_adjustable("kscreen:HDMI-A-1"))
        self.assertFalse(brightness_is_adjustable("dbus:org.freedesktop.ScreenSaver"))
        self.assertTrue(
            brightness_is_adjustable(
                "dbus:org.kde.ScreenBrightness:/org/kde/ScreenBrightness/display0"
            )
        )
        self.assertTrue(brightness_is_adjustable("/sys/class/backlight/foo/brightness"))

    def test_wlr_enabled_means_awake(self):
        on, brightness, pct = _state_from_read(
            SysfsControl("wlr:HDMI-A-1"),
            "yes\n",
        )
        self.assertTrue(on)
        self.assertEqual(pct, 100)

    def test_wlr_disabled_means_asleep(self):
        on, brightness, pct = _state_from_read(
            SysfsControl("wlr:HDMI-A-1"),
            "no\n",
        )
        self.assertFalse(on)
        self.assertEqual(pct, 0)

    def test_wlr_compose_off(self):
        remote = _compose_screen_remote(
            SysfsControl("wlr:HDMI-A-1"),
            0,
            "wlr:HDMI-A-1",
        )
        self.assertIn("wlr-randr", remote)
        self.assertIn("HDMI-A-1", remote)
        self.assertIn("--off", remote)

    def test_wlr_compose_on(self):
        remote = _compose_screen_remote(
            SysfsControl("wlr:HDMI-A-1"),
            100,
            "wlr:HDMI-A-1",
        )
        self.assertIn("--on", remote)

    def test_dbus_state_active_means_asleep(self):
        on, brightness, pct = _state_from_read(
            DbusScreenSaverControl("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver"),
            "b true\n",
        )
        self.assertFalse(on)
        self.assertEqual(brightness, 1)
        self.assertEqual(pct, 0)

    def test_dbus_state_inactive_means_awake(self):
        on, brightness, pct = _state_from_read(
            DbusScreenSaverControl("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver"),
            "b false\n",
        )
        self.assertTrue(on)
        self.assertEqual(brightness, 0)
        self.assertEqual(pct, 100)

    def test_kde_brightness_zero_is_asleep(self):
        on, brightness, pct = _state_from_read(
            DbusKdeBrightnessControl("org.kde.ScreenBrightness", "/org/kde/ScreenBrightness/display0"),
            "i 0\n",
        )
        self.assertFalse(on)
        self.assertEqual(brightness, 0)
        self.assertEqual(pct, 0)

    def test_kde_brightness_positive_is_awake(self):
        on, brightness, pct = _state_from_read(
            DbusKdeBrightnessControl("org.kde.ScreenBrightness", "/org/kde/ScreenBrightness/display0"),
            "i 10000\n",
            "i 10000\n",
        )
        self.assertTrue(on)
        self.assertEqual(brightness, 10000)
        self.assertEqual(pct, 100)

    def test_kde_brightness_partial(self):
        on, brightness, pct = _state_from_read(
            DbusKdeBrightnessControl("org.kde.ScreenBrightness", "/org/kde/ScreenBrightness/display0"),
            "i 3000\n",
            "i 10000\n",
        )
        self.assertTrue(on)
        self.assertEqual(brightness, 3000)
        self.assertEqual(pct, 30)

    def test_blank_sysfs_zero_is_awake(self):
        on, brightness, pct = _state_from_read(
            SysfsControl("/sys/class/graphics/fb0/blank"),
            "0\n",
        )
        self.assertTrue(on)
        self.assertEqual(brightness, 0)
        self.assertEqual(pct, 100)

    def test_parse_busctl_bool(self):
        self.assertTrue(_parse_busctl_bool("b true"))
        self.assertFalse(_parse_busctl_bool("b false"))

    def test_parse_busctl_int(self):
        self.assertEqual(_parse_busctl_int("i 10000"), 10000)
        self.assertIsNone(_parse_busctl_int("b true"))

    def test_kde_sleep_blanks_framebuffer(self):
        control = DbusKdeBrightnessControl(
            "org.kde.ScreenBrightness",
            "/org/kde/ScreenBrightness/display0",
        )
        remote = _compose_screen_remote(
            control,
            0,
            "/sys/class/graphics/fb0/blank",
        )
        self.assertIn("SetBrightness iu 0 0", remote)
        self.assertIn("/sys/class/graphics/fb0/blank", remote)
        self.assertIn("tee", remote)

    def test_kde_wake_unblanks_before_brightness(self):
        control = DbusKdeBrightnessControl(
            "org.kde.ScreenBrightness",
            "/org/kde/ScreenBrightness/display0",
        )
        remote = _compose_screen_remote(
            control,
            30,
            "/sys/class/graphics/fb0/blank",
        )
        self.assertLess(
            remote.index("/sys/class/graphics/fb0/blank"),
            remote.index("SetBrightness"),
        )
        self.assertIn("&&", remote)

    def test_blank_read_overrides_low_brightness(self):
        control = DbusKdeBrightnessControl(
            "org.kde.ScreenBrightness",
            "/org/kde/ScreenBrightness/display0",
        )
        on, brightness, pct = _state_from_read(
            control,
            "i 50\n",
            "i 10000\n",
            blank_output="1\n",
        )
        self.assertFalse(on)
        self.assertEqual(brightness, 0)
        self.assertEqual(pct, 0)

    def test_kde_defaults_blank_path(self):
        control = DbusKdeBrightnessControl(
            "org.kde.ScreenBrightness",
            "/org/kde/ScreenBrightness/display0",
        )
        self.assertEqual(
            _effective_blank_path({}, control),
            "/sys/class/graphics/fb0/blank",
        )
        self.assertIsNone(
            _effective_blank_path({"blank_path": "none"}, control),
        )

    def test_kde_sleep_uses_kscreen_dpms(self):
        control = DbusKdeBrightnessControl(
            "org.kde.ScreenBrightness",
            "/org/kde/ScreenBrightness/display0",
        )
        remote = _compose_screen_remote(control, 0, "kscreen:HDMI-A-1")
        self.assertIn("kscreen-doctor --dpms off", remote)
        self.assertNotIn("brightness.0", remote)
        self.assertNotIn("fb0/blank", remote)
        self.assertNotIn("SetActive", remote)

    def test_kde_wake_uses_kscreen_dpms(self):
        control = DbusKdeBrightnessControl(
            "org.kde.ScreenBrightness",
            "/org/kde/ScreenBrightness/display0",
        )
        remote = _compose_screen_remote(control, 30, "kscreen:HDMI-A-1")
        self.assertIn("kscreen-doctor --dpms on", remote)
        self.assertIn("output.HDMI-A-1.brightness.30", remote)
        self.assertNotIn("SetBrightness", remote)
        self.assertIn("SimulateUserActivity", remote)
        self.assertNotIn("SetActive", remote)

    def test_kscreen_path_is_full_on_or_dpms_off(self):
        control = SysfsControl("kscreen:HDMI-A-1")
        off = _compose_screen_remote(control, 0, "kscreen:HDMI-A-1")
        self.assertIn("kscreen-doctor --dpms off", off)
        self.assertNotIn("brightness", off)
        on = _compose_screen_remote(control, 5, None)
        self.assertIn("kscreen-doctor --dpms on", on)
        self.assertIn("output.HDMI-A-1.brightness.5", on)
        self.assertNotIn("SetBrightness", on)

    def test_kscreen_dpms_read_is_on_off(self):
        control = SysfsControl("kscreen:HDMI-A-1")
        on, _brightness, pct = _state_from_read(control, "on\n")
        self.assertTrue(on)
        self.assertEqual(pct, 100)
        on, _brightness, pct = _state_from_read(control, "off\n")
        self.assertFalse(on)
        self.assertEqual(pct, 0)

    def test_pick_display_control_prefers_kscreen_over_kde_brightness(self):
        from engine.screen_path import pick_display_control

        bright, blank, method, notes = pick_display_control([
            "KDE_OBJ=/org/kde/ScreenBrightness/display0",
            "KSCREEN_OUT=HDMI-A-1",
            "FB_BLANK=/sys/class/graphics/fb0/blank",
            "HOSTNAME=rock-5c",
            "DESKTOP=KDE",
        ])
        self.assertEqual(bright, "kscreen:HDMI-A-1")
        self.assertEqual(blank, "kscreen:HDMI-A-1")
        self.assertEqual(method, "kscreen-dpms")
        self.assertNotIn("ScreenBrightness", bright)
        self.assertIn("kde brightness ignored", notes)

    def test_pick_display_control_skips_dimmers_when_blank_exists(self):
        from engine.screen_path import phase_levels_for_brightness_path, pick_display_control

        bright, blank, method, _notes = pick_display_control([
            "KDE_OBJ=/org/kde/ScreenBrightness/display0",
            "BACKLIGHT=/sys/class/backlight/panel/brightness|max=255",
            "FB_BLANK=/sys/class/graphics/fb0/blank",
        ])
        self.assertEqual(bright, "/sys/class/graphics/fb0/blank")
        self.assertEqual(blank, bright)
        self.assertEqual(method, "sysfs-fb-blank")
        self.assertEqual(phase_levels_for_brightness_path(bright), (100, 100, 100))

    def test_screen_line_records_follow_phases_flag(self):
        from engine.config_compile import screen_line_with_follow_phases

        line = (
            "Kitchen Touchscreen | kitchen_panel | 10.10.10.10 | joel | "
            "kscreen:HDMI-A-1 | fa-utensils | 100 | 30 | 5 | "
            "kscreen:HDMI-A-1 | 52:58:15:8a:56:f6"
        )
        updated = screen_line_with_follow_phases(line, True)
        self.assertTrue(updated.endswith("| true"))
        self.assertIn("| 30 | 5 |", updated)
        cleared = screen_line_with_follow_phases(updated, False)
        self.assertTrue(cleared.endswith("| false"))

    def test_pick_display_control_keeps_backlight_dimmer_as_last_resort(self):
        from engine.screen_path import phase_levels_for_brightness_path, pick_display_control

        bright, blank, method, _notes = pick_display_control([
            "BACKLIGHT=/sys/class/backlight/panel/brightness|max=255",
        ])
        self.assertEqual(bright, "/sys/class/backlight/panel/brightness")
        self.assertEqual(blank, "none")
        self.assertEqual(method, "sysfs-backlight")
        self.assertEqual(phase_levels_for_brightness_path(bright), (100, 30, 5))


class ScreenColorModeTests(unittest.TestCase):
    def test_script_rejects_unknown_mode(self):
        with self.assertRaises(ValueError):
            color_mode_remote_script("dim")

    def test_script_selects_desktop_themes(self):
        dark = color_mode_remote_script("dark")
        light = color_mode_remote_script("light")
        self.assertIn("MODE=dark", dark)
        self.assertIn("MODE=light", light)
        self.assertNotIn("__SCCS_MODE__", dark)
        for script in (dark, light):
            self.assertIn("BreezeDark", script)
            self.assertIn("BreezeLight", script)
            self.assertIn("PiXonyx", script)
            self.assertIn("PiXtrix", script)
            self.assertIn("PiXnoir", script)
            self.assertIn("PiXflat", script)
            self.assertIn("prefer-dark", script)
            self.assertIn("prefer-light", script)
        subprocess.run(["bash", "-n"], input=dark, text=True, check=True)
        subprocess.run(["bash", "-n"], input=light, text=True, check=True)

    def test_apply_color_mode_sshs_each_panel(self):
        actuator = ScreenActuator.__new__(ScreenActuator)
        actuator._screens = {
            "kitchen": {
                "username": "joel",
                "host": "10.10.10.10",
                "friendly": "Kitchen Touchscreen",
            }
        }
        actuator._theme_lock = __import__("threading").Lock()
        actuator._theme_pending = None
        actuator._theme_running = False

        def run_now(*_args, **kwargs):
            class Fake:
                def start(self):
                    kwargs["target"]()

            return Fake()

        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="THEME_OK desktop=kde theme=BreezeDark mode=dark\n",
            stderr="",
        )
        with patch("actuators.screens.threading.Thread", side_effect=run_now), patch(
            "actuators.screens.subprocess.run", return_value=completed
        ) as run:
            actuator.apply_color_mode("dark")

        cmd = run.call_args.args[0]
        self.assertIn("joel@10.10.10.10", cmd)
        self.assertIn("MODE=dark", cmd)
        self.assertIn("plasma-apply-colorscheme", cmd)
        self.assertIn("SCCS_THEME", cmd)
        self.assertEqual(
            _color_mode_ssh_command("joel", "10.10.10.10", "light").count("MODE=light"),
            1,
        )


if __name__ == "__main__":
    unittest.main()