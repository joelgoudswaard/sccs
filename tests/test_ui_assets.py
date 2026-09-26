"""UI bundle, gzip, and panel-load markup."""

import gzip
import os
import unittest

from modules.ui_assets import (
    STATIC_DIR,
    UI_JS_FILES,
    css_source_paths,
    encode_gzip,
    fontawesome_css,
    javascript_bundle,
    stylesheet_bundle,
    theme_css,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as handle:
        return handle.read()


class TestUiBundle(unittest.TestCase):
    def test_stylesheet_inlines_imports_and_keeps_font_url(self):
        paths = css_source_paths()
        self.assertGreaterEqual(len(paths), 30)
        token, body, _modified = stylesheet_bundle()
        text = body.decode("utf-8")
        self.assertTrue(token)
        self.assertNotIn("@import", text)
        self.assertIn("../fonts/montserrat/montserrat-700-latin.woff2", text)
        self.assertIn("#tile-shutdown-system", text)
        self.assertIn("--blur-header: blur(16px);", text)

    def test_javascript_includes_every_ui_file_in_order(self):
        on_disk = {
            os.path.relpath(os.path.join(dirpath, name), STATIC_DIR)
            for dirpath, _dirs, names in os.walk(os.path.join(STATIC_DIR, "js"))
            for name in names
            if name.endswith(".js")
        }
        on_disk.add("nav.js")
        on_disk.add("socket.io.min.js")
        self.assertEqual(on_disk, set(UI_JS_FILES))

        _token, body, _modified = javascript_bundle()
        text = body.decode("utf-8")
        self.assertLess(text.index("sccsBoot"), text.index("bindSectionPoll"))
        self.assertIn("bindSectionPoll = function", text)
        self.assertLess(
            text.index("bindSectionPoll"),
            text.index("SCCS Socket.IO client"),
        )

    def test_fontawesome_webfonts_stay_under_static(self):
        _token, body, _modified = fontawesome_css("/static/fontawesome/webfonts/")
        text = body.decode("utf-8")
        self.assertIn("url(/static/fontawesome/webfonts/fa-solid-900.woff2)", text)
        self.assertNotIn("url(../webfonts/", text)

    def test_theme_names_are_files_only(self):
        self.assertIsNone(theme_css("../tokens.css"))
        self.assertIsNone(theme_css("Neuglass.css"))
        found = theme_css("neuglass.css")
        self.assertIsNotNone(found)
        text = found[1].decode("utf-8")
        self.assertIn(
            "linear-gradient(135deg, rgba(255, 255, 255, 0.09) 0%, transparent 50%)",
            text,
        )
        self.assertIn("--blur-tile: none", text)
        self.assertIn("--blur-surface: none", text)
        self.assertNotIn("backdrop-filter: none", text)
        minimal = theme_css("minimal.css")[1].decode("utf-8")
        self.assertIn("blur(32px)", minimal)

    def test_panel_chromium_composites_without_gpu_raster(self):
        install = _read("install.sh")
        launch = install.split("launch-chromium.sh", 1)[1].split("Wrote", 1)[0]
        self.assertIn("--disable-gpu-rasterization", launch)
        self.assertIn("--use-gl=egl", launch)
        self.assertIn("--ignore-gpu-blocklist", launch)
        self.assertIn("--force-device-scale-factor=1", launch)
        self.assertNotIn("--enable-gpu-rasterization", launch)
        self.assertNotIn("--enable-zero-copy", launch)
        self.assertNotIn("--disable-gpu-compositing", launch)
        self.assertNotRegex(launch, r"--disable-gpu(?!-)")
        self.assertIn('"restore_on_startup": 4', install)
        self.assertIn("RestoreOnStartupURLs", install)
        self.assertIn('"HomepageIsNewTabPage": false', install)
        self.assertIn("NewTabPageLocation", install)
        self.assertNotIn('+ " " + shlex.quote(ui)', launch)

    def test_gzip_roundtrip_and_skips(self):
        payload = b"x" * 2000
        compressed = encode_gzip(payload, "gzip, deflate")
        self.assertIsNotNone(compressed)
        self.assertEqual(gzip.decompress(compressed), payload)
        self.assertIsNone(encode_gzip(payload, "identity"))
        self.assertIsNone(encode_gzip(b"tiny", "gzip"))


class TestAssetRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import app

        cls.client = app.test_client()

    def test_home_is_one_gzipped_document(self):
        res = self.client.get("/", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get("Content-Encoding"), "gzip")
        html = gzip.decompress(res.data).decode()
        self.assertEqual(html.count("<script src="), 1)
        self.assertIn("/assets/app.css?", html)
        self.assertIn("/assets/app.js?", html)
        self.assertIn("defer", html)
        self.assertIn("/assets/themes/", html)

    def test_bundles_are_gzipped_and_revalidated(self):
        css = self.client.get("/assets/app.css", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(css.status_code, 200)
        self.assertEqual(css.headers.get("Content-Encoding"), "gzip")
        text = gzip.decompress(css.data).decode()
        self.assertNotIn("@import", text)
        self.assertIn("montserrat-700-latin.woff2", text)
        again = self.client.get(
            "/assets/app.css",
            headers={"Accept-Encoding": "gzip", "If-None-Match": css.headers["ETag"]},
        )
        self.assertEqual(again.status_code, 304)

        js = self.client.get("/assets/app.js", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(js.status_code, 200)
        self.assertIn(b"sccsBoot", gzip.decompress(js.data))

    def test_unknown_theme_is_not_found(self):
        res = self.client.get("/assets/themes/not-a-theme.css")
        self.assertEqual(res.status_code, 404)


class TestUiMarkup(unittest.TestCase):
    def test_page_requests_bundles_not_individual_scripts(self):
        base = _read("templates/base.html")
        index = _read("templates/index.html")
        self.assertIn("ui_stylesheet", base)
        self.assertIn("ui_javascript", base)
        self.assertIn("defer", base)
        self.assertNotIn("style.css", base)
        self.assertNotIn("js/boot.js", base)
        self.assertNotIn("<script src=", index)
        self.assertIn("gzip on;", _read("install.sh"))


if __name__ == "__main__":
    unittest.main()
