"""Concatenate the UI stylesheets and scripts, and gzip text responses.

nginx serves /static/ as many small files. The panel pages instead request
one stylesheet and one script from /assets/, which Flask can compress even
when an older nginx site config is still in front.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import re
import threading
from datetime import datetime, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(_ROOT, "static")

# Same order as the old blocking <script> tags.
UI_JS_FILES = (
    "js/boot.js",
    "js/theme-manager.js",
    "js/dark-mode.js",
    "nav.js",
    "js/fullscreen.js",
    "socket.io.min.js",
    "js/offline-overlay.js",
    "js/location.js",
    "js/sun-math.js",
    "js/datetime-tile.js",
    "js/reed-home-tile.js",
    "js/lighting-home-tile.js",
    "js/home-tile-fit.js",
    "js/home-module-visibility.js",
    "js/show-disconnected-tiles.js",
    "js/climate-tile.js",
    "js/water-tile.js",
    "js/power-tile.js",
    "js/network-tile.js",
    "js/sonos-tile.js",
    "js/system-tile.js",
    "js/sccs-core-tile.js",
    "js/lighting-controller.js",
    "js/lighting-tab.js",
    "js/socket-client.js",
    "js/scenes-controller.js",
    "js/phases-tile.js",
    "js/gps-status-tile.js",
    "js/reeds-system-tile.js",
    "js/explain-tile.js",
    "js/screens-system-tile.js",
    "js/homekit-system-tile.js",
    "js/matter-system-tile.js",
    "js/sonos-system-tile.js",
    "js/wifi-system-tile.js",
    "js/victron-system-tile.js",
    "js/shutdown-system-tile.js",
    "js/toasts.js",
    "js/toast-test-tile.js",
    "js/system-tab.js",
)

GZIP_MIMETYPES = frozenset({
    "text/html",
    "text/css",
    "text/javascript",
    "application/javascript",
    "application/json",
    "application/manifest+json",
    "image/svg+xml",
    "text/plain",
})

GZIP_MIN_BYTES = 860

_IMPORT_RE = re.compile(r"""@import\s+url\(\s*['"]?([^'")\s]+)['"]?\s*\)\s*;""")
_THEME_NAME_RE = re.compile(r"^[a-z0-9-]+\.css$")


class _Cache:
    def __init__(self) -> None:
        self.key: object = None
        self.body = b""
        self.lock = threading.Lock()


_css_cache = _Cache()
_js_cache = _Cache()
_fa_cache = _Cache()


def _realpath(path: str) -> str:
    return os.path.realpath(path)


def _inside_static(path: str) -> bool:
    static = _realpath(STATIC_DIR)
    return os.path.commonpath([static, _realpath(path)]) == static


def fingerprint(paths: list[str]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        st = os.stat(path)
        digest.update(path.encode())
        digest.update(str(st.st_mtime_ns).encode())
        digest.update(str(st.st_size).encode())
    return digest.hexdigest()[:16]


def newest_mtime(paths: list[str]) -> datetime:
    mtime = max(os.stat(path).st_mtime for path in paths)
    return datetime.fromtimestamp(mtime, tz=timezone.utc)


def css_source_paths() -> list[str]:
    manifest = os.path.join(STATIC_DIR, "style.css")
    with open(manifest, encoding="utf-8") as handle:
        text = handle.read()
    paths: list[str] = []
    for match in _IMPORT_RE.finditer(text):
        path = os.path.normpath(os.path.join(STATIC_DIR, match.group(1)))
        if not _inside_static(path) or not os.path.isfile(path):
            raise FileNotFoundError(match.group(1))
        paths.append(path)
    if not paths:
        raise RuntimeError("style.css has no @import rules to bundle")
    return paths


def js_source_paths() -> list[str]:
    paths = [os.path.join(STATIC_DIR, rel) for rel in UI_JS_FILES]
    missing = [path for path in paths if not os.path.isfile(path)]
    if missing:
        raise FileNotFoundError(missing[0])
    return paths


def _remember(cache: _Cache, key: object, build) -> bytes:
    with cache.lock:
        if cache.key == key and cache.body:
            return cache.body
    body = build()
    with cache.lock:
        cache.key = key
        cache.body = body
    return body


def stylesheet_bundle() -> tuple[str, bytes, datetime]:
    paths = css_source_paths()
    token = fingerprint(paths)

    def build() -> bytes:
        parts: list[str] = []
        for path in paths:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            if text and not text.endswith("\n"):
                text += "\n"
            parts.append(text)
        return "".join(parts).encode("utf-8")

    return token, _remember(_css_cache, token, build), newest_mtime(paths)


def javascript_bundle() -> tuple[str, bytes, datetime]:
    paths = js_source_paths()
    token = fingerprint(paths)

    def build() -> bytes:
        parts: list[str] = []
        for path in paths:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            if text and not text.endswith("\n"):
                text += "\n"
            parts.append(text)
            parts.append(";\n")
        return "".join(parts).encode("utf-8")

    return token, _remember(_js_cache, token, build), newest_mtime(paths)


def fontawesome_css(font_prefix: str) -> tuple[str, bytes, datetime]:
    path = os.path.join(STATIC_DIR, "fontawesome", "css", "all.min.css")
    token = fingerprint([path])
    cache_key = (token, font_prefix)

    def build() -> bytes:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        # The stylesheet is served from /assets/, so the relative webfont
        # paths would otherwise resolve outside /static/fontawesome/.
        text = text.replace("url(../webfonts/", f"url({font_prefix}")
        text = text.replace('url("../webfonts/', f'url("{font_prefix}')
        return text.encode("utf-8")

    return token, _remember(_fa_cache, cache_key, build), newest_mtime([path])


def theme_css(name: str) -> tuple[str, bytes, datetime] | None:
    if not _THEME_NAME_RE.fullmatch(name or ""):
        return None
    path = os.path.join(STATIC_DIR, "css", "themes", name)
    if not _inside_static(path) or not os.path.isfile(path):
        return None
    token = fingerprint([path])
    with open(path, "rb") as handle:
        body = handle.read()
    return token, body, newest_mtime([path])


def template_asset_versions() -> dict[str, str]:
    theme = os.path.join(STATIC_DIR, "css", "themes", "neuglass.css")
    fa = os.path.join(STATIC_DIR, "fontawesome", "css", "all.min.css")
    return {
        "ui_css_version": fingerprint(css_source_paths()),
        "ui_js_version": fingerprint(js_source_paths()),
        "ui_fa_version": fingerprint([fa]),
        "ui_theme_version": fingerprint([theme]),
    }


def encode_gzip(payload: bytes, accept_encoding: str) -> bytes | None:
    if len(payload) < GZIP_MIN_BYTES:
        return None
    if "gzip" not in accept_encoding.lower():
        return None
    return gzip.compress(payload, compresslevel=5)


def apply_gzip(response, request):
    """Compress a Flask response when the client accepts gzip."""
    if request.method == "HEAD" or response.status_code != 200:
        return response
    if request.path.startswith("/socket.io"):
        return response
    if request.headers.get("Range"):
        return response
    if response.headers.get("Content-Encoding"):
        return response
    mimetype = (response.mimetype or "").split(";", 1)[0].strip().lower()
    if mimetype not in GZIP_MIMETYPES:
        return response
    try:
        payload = response.get_data()
    except (RuntimeError, OSError):
        return response
    compressed = encode_gzip(payload, request.headers.get("Accept-Encoding", ""))
    if compressed is None:
        return response
    response.set_data(compressed)
    response.headers["Content-Encoding"] = "gzip"
    response.headers["Content-Length"] = str(len(compressed))
    vary = response.headers.get("Vary")
    if not vary:
        response.headers["Vary"] = "Accept-Encoding"
    elif "accept-encoding" not in vary.lower():
        response.headers["Vary"] = f"{vary}, Accept-Encoding"
    return response
