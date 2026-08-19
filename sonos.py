"""Sonos speaker control — delegates to modules/sonos when the manager is up."""

from __future__ import annotations

from typing import Any

_sonos_manager = None

_UNAVAILABLE: dict[str, Any] = {
    "source": "unavailable",
    "message": "Sonos module not loaded",
    "speakers": [],
}


def set_sonos_manager(sonos_manager) -> None:
    global _sonos_manager
    _sonos_manager = sonos_manager


def _normalize_state(state: dict[str, Any] | None) -> dict[str, Any]:
    if not state:
        return dict(_UNAVAILABLE)

    speaker = state.get("speaker") or state.get("room")
    track = state.get("track") or state.get("title") or "Nothing playing"
    playing = state.get("is_playing", state.get("playing", False))
    muted = state.get("mute", state.get("muted", False))
    position = int(state.get("position", state.get("elapsed_seconds", 0)) or 0)
    duration = int(state.get("duration", state.get("duration_seconds", 0)) or 0)
    remaining = max(0, duration - position)

    return {
        "room": speaker,
        "speaker": speaker,
        "title": track,
        "track": track,
        "artist": state.get("artist") or "",
        "album": state.get("album"),
        "album_art": state.get("album_art"),
        "playing": bool(playing),
        "is_playing": bool(playing),
        "muted": bool(muted),
        "mute": bool(muted),
        "volume": state.get("volume"),
        "elapsed_seconds": position,
        "position": position,
        "duration_seconds": duration,
        "duration": duration,
        "remaining_seconds": remaining,
        "speakers": state.get("speakers", []),
        "source": state.get("source", "live"),
    }


def get_sonos_status() -> dict[str, Any]:
    if _sonos_manager is None:
        return dict(_UNAVAILABLE)
    return _normalize_state(_sonos_manager.get_current_state())


def _execute_command(command: str, *, value=None, speaker: str | None = None) -> dict[str, Any]:
    if _sonos_manager is None:
        return get_sonos_status()

    payload: dict[str, Any] = {"command": command}
    if value is not None:
        payload["value"] = value
    if speaker:
        payload["speaker"] = speaker

    result = _sonos_manager.execute_command(payload)
    if isinstance(result, dict) and result.get("error"):
        status = get_sonos_status()
        status["error"] = result["error"]
        return status
    return get_sonos_status()


def set_transport(action: str) -> dict[str, Any]:
    mapping = {
        "toggle": "playpause",
        "play": "play",
        "pause": "pause",
        "next": "next",
        "previous": "previous",
    }
    command = mapping.get(str(action).lower())
    if not command:
        return get_sonos_status()
    return _execute_command(command)


def set_volume(level: int) -> dict[str, Any]:
    return _execute_command("volume", value=int(level))


def set_muted(muted: bool) -> dict[str, Any]:
    return _execute_command("mute", value=bool(muted))
