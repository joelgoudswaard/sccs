"""Validate compiled config before the runtime starts."""

from __future__ import annotations

import ipaddress
import subprocess
from typing import Dict, List, Optional, Set

from .config_compile import CompiledConfig, VALID_PHASES


class ConfigValidationError(Exception):
    """Raised when sccs.conf fails validation."""

    def __init__(self, errors: List[str], warnings: List[str] | None = None):
        self.errors = errors
        self.warnings = warnings or []
        msg = "Config validation failed:\n" + "\n".join(f"  • {e}" for e in errors)
        super().__init__(msg)


def _reed_pins(raw_cfg) -> dict:
    pins = {}
    if not raw_cfg.has_section("reeds"):
        return pins
    for name, line in raw_cfg.items("reeds"):
        parts = [p.strip() for p in str(line).split("|")]
        if len(parts) < 2:
            continue
        try:
            pin = int(parts[1])
        except ValueError:
            continue
        pins.setdefault(pin, []).append(name)
    return pins


def _local_ipv4_addrs() -> Set[ipaddress.IPv4Address]:
    """Global IPv4 addresses currently assigned to this host (best effort)."""
    addrs: Set[ipaddress.IPv4Address] = set()
    try:
        out = subprocess.check_output(
            ["ip", "-4", "-o", "addr", "show", "scope", "global"],
            text=True,
            timeout=3,
            stderr=subprocess.DEVNULL,
        )
        for line in out.splitlines():
            for tok in line.split():
                if "/" not in tok:
                    continue
                try:
                    addrs.add(ipaddress.IPv4Address(tok.split("/", 1)[0]))
                except ValueError:
                    continue
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        pass
    return addrs


def _local_ipv4_networks() -> List[ipaddress.IPv4Network]:
    """Global IPv4 networks on this host (for LAN membership checks)."""
    nets: List[ipaddress.IPv4Network] = []
    try:
        out = subprocess.check_output(
            ["ip", "-4", "-o", "addr", "show", "scope", "global"],
            text=True,
            timeout=3,
            stderr=subprocess.DEVNULL,
        )
        for line in out.splitlines():
            for tok in line.split():
                if "/" not in tok:
                    continue
                try:
                    nets.append(ipaddress.IPv4Network(tok, strict=False))
                except ValueError:
                    continue
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        pass
    return nets


def _configured_host_ips(raw_cfg) -> Set[ipaddress.IPv4Address]:
    """Extra known host IPs from config (e.g. Sonos interface_addr)."""
    addrs: Set[ipaddress.IPv4Address] = set()
    if raw_cfg is None:
        return addrs
    try:
        if raw_cfg.has_section("sonos"):
            val = (raw_cfg.get("sonos", "interface_addr", fallback="") or "").strip()
            if val:
                addrs.add(ipaddress.IPv4Address(val))
    except (ValueError, TypeError):
        pass
    return addrs


def _validate_screen_hosts(
    compiled: CompiledConfig,
    raw_cfg,
    errors: List[str],
    warnings: List[str],
    *,
    local_ipv4: Optional[Set[ipaddress.IPv4Address]] = None,
    local_nets: Optional[List[ipaddress.IPv4Network]] = None,
) -> None:
    """
    Screen host must be a real client IPv4 on the van LAN — never this Pi,
    never shared between two screens, never network/broadcast/special.
    """
    host_ips = set(local_ipv4) if local_ipv4 is not None else _local_ipv4_addrs()
    host_ips |= _configured_host_ips(raw_cfg)
    nets = list(local_nets) if local_nets is not None else _local_ipv4_networks()

    seen_hosts: Dict[str, str] = {}  # normalized ip → first screen name

    for screen, meta in compiled.screens.items():
        raw_host = (meta.get("host") or "").strip()
        if not raw_host:
            errors.append(f"Screen '{screen}' host is empty (need a fixed LAN IPv4)")
            continue
        try:
            ip = ipaddress.IPv4Address(raw_host)
        except ValueError:
            errors.append(
                f"Screen '{screen}' host must be an IPv4 address (got {raw_host!r})"
            )
            continue

        if ip.is_unspecified or ip.is_loopback or ip.is_multicast or ip.is_link_local:
            errors.append(
                f"Screen '{screen}' host {ip} is not a usable LAN address"
            )
            continue

        if ip in host_ips:
            errors.append(
                f"Screen '{screen}' host {ip} is this host Pi — "
                f"panels must use a different address on the van LAN"
            )
            continue

        # Reject network/broadcast of any local global subnet (e.g. 10.10.10.0 / .255)
        bad_net_bc = False
        for net in nets:
            if ip == net.network_address or ip == net.broadcast_address:
                errors.append(
                    f"Screen '{screen}' host {ip} is the network/broadcast "
                    f"address of {net}"
                )
                bad_net_bc = True
                break
        if bad_net_bc:
            continue

        # Prefer screens on a local LAN; warn (don't hard-fail) if none match
        # so offline edits on a laptop still work.
        if nets and not any(ip in net for net in nets):
            warnings.append(
                f"Screen '{screen}' host {ip} is not on any local interface "
                f"subnet ({', '.join(str(n) for n in nets)})"
            )

        key = str(ip)
        if key in seen_hosts:
            errors.append(
                f"Screen '{screen}' host {ip} duplicates screen '{seen_hosts[key]}'"
            )
        else:
            seen_hosts[key] = screen


def validate_compiled_config(
    raw_cfg,
    compiled: CompiledConfig,
    *,
    local_ipv4: Optional[Set[ipaddress.IPv4Address]] = None,
    local_nets: Optional[List[ipaddress.IPv4Network]] = None,
) -> List[str]:
    """
    Validate compiled config. Returns warnings; raises ConfigValidationError on errors.

    local_ipv4 / local_nets may be injected by tests; otherwise discovered via `ip`.
    """
    errors: List[str] = []
    warnings: List[str] = []
    lights: Set[str] = set(compiled.light_names)
    reeds: Set[str] = set(compiled.reed_names)

    # Reed controls reference real lights
    for reed, controlled in compiled.reed_to_lights.items():
        for light in controlled:
            if light not in lights:
                errors.append(
                    f"Reed '{reed}' controls unknown light '{light}'"
                )

    # Reed-linked lights: missing phase levels mean no automation action for that phase
    for reed, controlled in compiled.reed_to_lights.items():
        for light in controlled:
            levels = compiled.reed_phase_levels.get(light, {})
            for phase in ("day", "evening", "night"):
                if phase not in levels:
                    warnings.append(
                        f"Reed-linked light '{light}' has no [reed_phases.{light}] "
                        f"{phase} level (no automation action in that phase)"
                    )

    # Ambient lights need evening + night levels
    for light in compiled.ambient_lights:
        if light not in lights:
            errors.append(f"Ambient light '{light}' not defined in [lights]")
            continue
        levels = (
            compiled.reed_phase_levels.get(light)
            or compiled.ambient_phase_levels.get(light)
            or {}
        )
        for phase in ("evening", "night"):
            if phase not in levels:
                warnings.append(
                    f"Ambient light '{light}' has no {phase} level "
                    f"(will fall back to off)"
                )

    # Interlocks
    for controlled, required_list in compiled.interlocks.items():
        if controlled not in reeds:
            errors.append(f"Interlock controlled reed '{controlled}' is not configured")
        for req in required_list:
            if req not in reeds:
                errors.append(
                    f"Interlock for '{controlled}' references unknown reed '{req}'"
                )

    # Screens
    for screen, meta in compiled.screens.items():
        linked = meta.get("linked_reed")
        if linked and linked not in reeds:
            errors.append(
                f"Screen '{screen}' linked_reed '{linked}' is not in [reeds]"
            )
        for phase, level in (meta.get("phase_brightness") or {}).items():
            if not 0 <= int(level) <= 100:
                errors.append(
                    f"Screen '{screen}' {phase} brightness must be 0–100 (got {level})"
                )
        mac = (meta.get("mac") or "").strip().lower()
        if mac:
            parts = mac.split(":")
            if len(parts) != 6 or any(
                len(p) != 2 or any(c not in "0123456789abcdef" for c in p) for p in parts
            ):
                errors.append(
                    f"Screen '{screen}' mac must look like aa:bb:cc:dd:ee:ff (got {mac!r})"
                )

    _validate_screen_hosts(
        compiled,
        raw_cfg,
        errors,
        warnings,
        local_ipv4=local_ipv4,
        local_nets=local_nets,
    )

    # Scenes
    for scene_key, scene in compiled.scenes.items():
        for light in scene.get("lights", {}):
            if light not in lights:
                errors.append(
                    f"Scene '{scene_key}' references unknown light '{light}'"
                )

    # Duplicate reed GPIO pins
    for pin, names in _reed_pins(raw_cfg).items():
        if len(names) > 1:
            errors.append(
                f"Duplicate reed GPIO pin {pin}: {', '.join(names)}"
            )

    # Orphan reed_phases / ambient sections
    for light in compiled.reed_phase_levels:
        if light not in lights:
            warnings.append(f"[reed_phases.{light}] has no matching [lights] entry")
    for light in compiled.ambient_phase_levels:
        if light not in lights:
            warnings.append(f"[ambient.{light}] has no matching [lights] entry")

    if errors:
        raise ConfigValidationError(errors, warnings)

    return warnings
