"""Sonos album art is proxied as one query parameter."""

import configparser
import unittest
from urllib.parse import parse_qs, urlparse

from modules.sonos import SonosManager


def _config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg.add_section("sonos")
    return cfg


class TestAlbumArtProxyUrl(unittest.TestCase):
    def setUp(self):
        self.manager = SonosManager(None, _config())

    def test_local_art_keeps_the_track_query(self):
        raw = (
            "http://10.10.10.187:1400/getaa?s=1"
            "&u=x-sonos-spotify%3aspotify%253atrack%253a5L178jU442BbvIsj8l7bI8"
        )
        proxied = self.manager._make_album_art_proxy_url(raw)
        self.assertTrue(proxied.startswith("/sonos-art?url="))
        recovered = parse_qs(urlparse(proxied).query)["url"]
        self.assertEqual(recovered, [raw])

    def test_public_art_is_not_proxied(self):
        raw = "https://i.scdn.co/image/ab67616d0000b273"
        self.assertEqual(self.manager._make_album_art_proxy_url(raw), raw)

    def test_empty_art_is_none(self):
        self.assertIsNone(self.manager._make_album_art_proxy_url(None))
        self.assertIsNone(self.manager._make_album_art_proxy_url(""))


if __name__ == "__main__":
    unittest.main()
