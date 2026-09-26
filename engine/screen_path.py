"""Touchscreen power target.

A linked reed turns the panel on or off. Dimming by time of day is only for a
path that can actually set a brightness level. HDMI panels that expose KDE
ScreenBrightness still stay faintly lit at 0%, so that path is not power control.
"""

from __future__ import annotations


def brightness_is_adjustable(path: str) -> bool:
    """True when brightness_path supports continuous 0–100 levels (not just on/off)."""
    if not path:
        return False
    if path.startswith("wlr:") or path.startswith("kscreen:"):
        return False
    if path.startswith("dbus:"):
        # KDE ScreenBrightness is continuous; ScreenSaver is binary
        return "org.kde.ScreenBrightness" in path
    # sysfs blank / graphics fb = binary; other sysfs (backlight) = continuous
    if "blank" in path or "/graphics/fb" in path:
        return False
    return path.startswith("/")


def phase_levels_for_brightness_path(path: str) -> tuple[int, int, int]:
    """Day, evening, night percent while the linked reed is open.

    On/off paths stay fully lit. A dimmer keeps 100 / 30 / 5.
    """
    if brightness_is_adjustable(path):
        return (100, 30, 5)
    return (100, 100, 100)


def pick_display_control(lines: list[str]) -> tuple[str, str, str, str]:
    """Choose brightness_path, blank_path, method, and a short note.

    Prefer a reed on/off target (kscreen DPMS, wlr-randr, framebuffer blank,
    screensaver). A backlight or KDE ScreenBrightness entry only dims, so it
    is used when nothing else can switch the panel.
    """
    kde: list[str] = []
    kscreen: list[str] = []
    wlr: list[str] = []
    backlight: list[str] = []
    fb: list[str] = []
    screensaver = ""
    desktop = ""
    host = ""

    for raw in lines:
        line = raw.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key == "KDE_OBJ":
            kde.append(value)
        elif key == "KSCREEN_OUT":
            if value not in kscreen:
                kscreen.append(value)
        elif key == "WLR_OUT":
            if value not in wlr:
                wlr.append(value)
        elif key == "BACKLIGHT":
            backlight.append(value.split("|", 1)[0])
        elif key == "FB_BLANK":
            fb.append(value)
        elif key == "SCREENSAVER" and value:
            screensaver = value
        elif key == "DESKTOP":
            desktop = value
        elif key == "HOSTNAME":
            host = value
        elif key == "PROBE_FAIL":
            return ("", "", "probe-failed", "probe failed")

    detail: list[str] = []
    if host:
        detail.append("host=" + host)
    if desktop:
        detail.append("desktop=" + desktop)

    def finish(bright: str, blank: str, method: str) -> tuple[str, str, str, str]:
        return (bright, blank, method, "; ".join(detail))

    if kscreen:
        path = "kscreen:" + kscreen[0]
        detail.append("kscreen outs: " + ", ".join(kscreen))
        if kde:
            detail.append("kde brightness ignored (dims, does not switch power)")
        return finish(path, path, "kscreen-dpms")
    if wlr:
        path = "wlr:" + wlr[0]
        detail.append("wlr outs: " + ", ".join(wlr))
        return finish(path, path, "wlr-randr")
    if fb:
        if kde:
            detail.append("kde brightness ignored (dims, does not switch power)")
        if backlight:
            detail.append("backlight ignored (dims): " + ", ".join(backlight))
        return finish(fb[0], fb[0], "sysfs-fb-blank")
    if screensaver:
        path = "dbus:" + screensaver
        return finish(path, "none", "screensaver")
    if backlight:
        detail.append("backlights: " + ", ".join(backlight))
        blank = "none"
        return finish(backlight[0], blank, "sysfs-backlight")
    if kde:
        path = "dbus:org.kde.ScreenBrightness:" + kde[0]
        detail.append("kde objs: " + ", ".join(kde))
        return finish(path, "none", "kde-brightness")

    path = "/sys/class/graphics/fb0/blank"
    detail.append("no display controls found")
    return finish(path, path, "fallback-fb")
