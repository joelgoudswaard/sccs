"""HAP AccessoryDriver lifecycle, persist, QR, LAN bind."""

from __future__ import annotations

import io
import logging
import os
import re
import threading
from typing import Optional

import psutil

logger = logging.getLogger("sccs")

_PIN_RE = re.compile(r"^\d{3}-\d{2}-\d{3}$")


def validate_pin(pin: str) -> str:
    pin = (pin or "").strip()
    if not _PIN_RE.match(pin):
        raise ValueError(f"HomeKit pin must be XXX-XX-XXX, got {pin!r}")
    return pin


def generate_pin() -> str:
    from pyhap.util import generate_pincode

    return generate_pincode().decode("ascii")


# Temporary bench override: listen on every NIC so pairing QR works without van LAN.
LISTEN_ALL_INTERFACES = False


def _first_live_ipv4() -> Optional[str]:
    for addrs in psutil.net_if_addrs().values():
        for item in addrs:
            ip = getattr(item, "address", "") or ""
            if ip.count(".") == 3 and not ip.startswith("127."):
                return ip
    return None


def address_is_local(addr: str) -> bool:
    if not addr:
        return False
    for addrs in psutil.net_if_addrs().values():
        for item in addrs:
            if item.address == addr:
                return True
    return False


def resolve_bind_address(configured: str) -> Optional[str]:
    """Return the configured LAN address, or None if that NIC is down."""
    if LISTEN_ALL_INTERFACES:
        logger.warning("HomeKit TEMP: listening on all interfaces")
        return "0.0.0.0"
    addr = (configured or "").strip()
    if not addr:
        logger.warning("HomeKit bind_address is empty — not starting")
        return None
    if addr in ("0.0.0.0", "::", "*"):
        logger.error("HomeKit will not bind %s — set bind_address to the van LAN IP", addr)
        return None
    if not address_is_local(addr):
        logger.warning("HomeKit LAN disconnected (%s not on this host)", addr)
        return None
    return addr


def persist_path(raw: str, repo_root: str) -> str:
    path = os.path.expanduser((raw or "config/homekit.state").strip())
    if not os.path.isabs(path):
        path = os.path.join(repo_root, path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    return path


def qr_svg(xhm_uri: str) -> str:
    from pyqrcode import QRCode

    buf = io.BytesIO()
    QRCode(xhm_uri).svg(buf, scale=4, quiet_zone=1)
    raw = buf.getvalue().decode("utf-8")
    # pyqrcode emits width/height but no viewBox; browsers then use the
    # intrinsic pixel size as min-content and clip the bottom-right.
    if "viewBox=" not in raw:
        import re

        match = re.search(r'\bwidth="(\d+(?:\.\d+)?)"[^>]*\bheight="(\d+(?:\.\d+)?)"', raw)
        if not match:
            match = re.search(r'\bheight="(\d+(?:\.\d+)?)"[^>]*\bwidth="(\d+(?:\.\d+)?)"', raw)
            if match:
                h, w = match.group(1), match.group(2)
            else:
                return raw
        else:
            w, h = match.group(1), match.group(2)
        raw = raw.replace("<svg ", f'<svg viewBox="0 0 {w} {h}" ', 1)
    return raw


def build_driver(*, address: str, port: int, persist_file: str, pincode: str):
    from pyhap.accessory_driver import AccessoryDriver

    listen = address
    advertised = address
    if address in ("0.0.0.0", "::"):
        advertised = _first_live_ipv4() or address
    return AccessoryDriver(
        address=advertised,
        listen_address=listen,
        advertised_address=advertised,
        port=int(port),
        persist_file=persist_file,
        pincode=validate_pin(pincode).encode("ascii"),
    )


def start_driver_thread(driver, name: str = "HomeKit") -> threading.Thread:
    thread = threading.Thread(target=driver.start, name=name, daemon=True)
    thread.start()
    return thread
