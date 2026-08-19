import unittest

from actuators.screens import (
    DbusKdeBrightnessControl,
    DbusScreenSaverControl,
    SysfsControl,
    _compose_screen_remote,
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
        self.assertIn("output.HDMI-A-1.brightness.0", remote)
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
        self.assertIn("SetBrightness", remote)
        self.assertIn("SimulateUserActivity", remote)
        self.assertNotIn("SetActive", remote)


if __name__ == "__main__":
    unittest.main()