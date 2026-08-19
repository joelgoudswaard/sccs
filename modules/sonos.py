# modules/sonos.py
import logging
import time
import threading
import socket
from urllib.parse import quote

import soco
from soco.discovery import discover
from soco.exceptions import SoCoException

logger = logging.getLogger("sccs")


class SonosManager:
    def __init__(self, socketio, config):
        self.socketio = socketio
        self.config = config

        # ====================== CONFIG ======================
        self.preferred_name = (config.get('sonos', 'player_name', fallback='') or '').strip() or None
        self.auto_select_first = config.getboolean('sonos', 'auto_select_first', fallback=True)
        
        self.interface_addr = config.get('sonos', 'interface_addr', fallback=None)
        if self.interface_addr == "":
            self.interface_addr = None

        self._manual_override = False
        self._last_default_saved = False
        self.poll_interval = config.getint('sonos', 'poll_interval', fallback=3)
        self.discovery_interval = config.getint('sonos', 'discovery_interval', fallback=30)
        self.discovery_timeout = config.getint('sonos', 'discovery_timeout', fallback=8)
        self.default_volume = config.getint('sonos', 'default_volume', fallback=-1)

        # Internal state
        self.speakers = {}
        self.current_speaker = None
        self._running = False
        self._discovery_thread = None
        self._poll_thread = None
        self._last_state = {}
        self._last_speaker_count = 0
        self._initial_discovery_done = False

        logger.info(f"🎵 SonosManager initialized (preferred: '{self.preferred_name}', interface: {self.interface_addr or 'auto'})")

    def start(self):
        if self._running:
            return

        self._running = True

        # Initial discovery (always logged)
        self._discover_speakers(initial=True)
        self._initial_discovery_done = True

        self._discovery_thread = threading.Thread(target=self._discovery_loop, daemon=True)
        self._discovery_thread.start()

        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

        logger.debug("🎵 Sonos background threads started")

    def stop(self):
        self._running = False
        logger.debug("🎵 SonosManager stopped")

    def _discover_speakers(self, initial: bool = False):
        """Discover speakers using the correct network interface"""
        try:
            logger.debug(f"[Discovery] Running on interface {self.interface_addr or 'auto'}")

            discovered = discover(
                timeout=self.discovery_timeout, 
                include_invisible=False,
                interface_addr=self.interface_addr
            )
            devices = list(discovered) if discovered else []

            new_speakers = {}
            for device in devices:
                try:
                    name = device.player_name
                    if name:
                        new_speakers[name] = device
                except:
                    continue

            old_count = len(self.speakers)
            self.speakers = new_speakers
            new_count = len(self.speakers)

            if initial or new_count != old_count:
                if new_count == 0:
                    logger.info("🎵 0 Sonos speaker(s) detected")
                elif new_count == 1:
                    logger.info(f"🎵 1 Sonos speaker detected → {list(self.speakers.keys())[0]}")
                else:
                    logger.info(f"🎵 {new_count} Sonos speaker(s) detected → {', '.join(sorted(self.speakers.keys()))}")

            if self._manual_override and self.current_speaker in self.speakers:
                logger.debug(f"🎵 Keeping manual selection: {self.current_speaker}")
            elif initial or not self.current_speaker:
                self._select_best_speaker()
            else:
                logger.debug(f"🎵 Preserving current speaker: {self.current_speaker}")

            self._broadcast_speakers()
            self._last_speaker_count = new_count

        except Exception as e:
            logger.debug(f"Sonos discovery failed: {e}")
            if initial:
                logger.info("🎵 0 Sonos speaker(s) detected (discovery error)")

    def _select_best_speaker(self):
        if not self.speakers:
            return

        if self._manual_override and self.current_speaker in self.speakers:
            logger.debug(f"🎵 Respecting manual override: {self.current_speaker}")
            return

        if self.preferred_name:
            preferred_lower = self.preferred_name.lower()
            for name in self.speakers:
                if preferred_lower in name.lower():
                    self.current_speaker = name
                    self._manual_override = False
                    logger.info(f"🎵 Selected preferred speaker: {name}")
                    self._apply_default_volume()
                    self._broadcast_speakers()
                    return

        if self.auto_select_first and not self.current_speaker:
            self.current_speaker = next(iter(self.speakers.keys()))
            logger.info(f"🎵 Auto-selected initial speaker: {self.current_speaker}")
            self._apply_default_volume()
            self._broadcast_speakers()

    def _apply_default_volume(self):
        if self.default_volume >= 0 and self.current_speaker:
            try:
                device = self.speakers[self.current_speaker]
                device.volume = max(0, min(100, self.default_volume))
                logger.info(f"🎵 Applied default volume → {self.default_volume}% on {self.current_speaker}")
            except Exception as e:
                logger.warning(f"Failed to set default volume: {e}")

    def _discovery_loop(self):
        """Background loop — only logs when speaker count changes"""
        while self._running:
            time.sleep(self.discovery_interval)
            if self._running:
                self._discover_speakers(initial=False)

    def _poll_loop(self):
        """Poll ALL speakers (not just the active one) for diag page"""
        while self._running:
            try:
                self._poll_all_speakers()
            except Exception as e:
                logger.debug(f"Sonos poll error: {e}")
            time.sleep(self.poll_interval)

    def _poll_all_speakers(self):
        """Poll every speaker (for diag) but only emit full state for the ACTIVE one to everyone"""
        for name, device in list(self.speakers.items()):
            try:
                state = self._get_single_speaker_state(name, device)
                
                if state != self._last_state.get(name):
                    self._last_state[name] = state.copy()
                    
                    self.socketio.emit('sonos_update', state)
                    
                    if name == self.current_speaker:
                        current_state = state.copy()
                        current_state['is_current_active'] = True
                        self.socketio.emit('sonos_update', current_state)  # main UI listens to this too
                        
            except Exception as e:
                logger.debug(f"Failed to poll {name}: {e}")

    def _get_single_speaker_state(self, name: str, device) -> dict:
        """Get full state for one speaker"""
        state = {
            'speaker': name,
            'volume': device.volume,
            'mute': device.mute,
            'is_playing': False,
            'track': 'Nothing playing',
            'artist': '',
            'album': '',
            'album_art': None,
            'position': 0,
            'duration': 0,
        }

        try:
            # Transport state
            transport = device.get_current_transport_info()
            state['is_playing'] = transport.get('current_transport_state') == 'PLAYING'

            # Track info
            track = device.get_current_track_info()
            state.update({
                'track': track.get('title') or 'Nothing playing',
                'artist': track.get('artist') or '',
                'album': track.get('album') or '',
                'position': self._time_to_seconds(track.get('position')),
                'duration': self._time_to_seconds(track.get('duration')),
            })

            # Album art
            raw_art = track.get('album_art')
            if raw_art:
                state['album_art'] = self._make_album_art_proxy_url(raw_art)

        except Exception as e:
            logger.debug(f"Partial state for {name}: {e}")

        return state

    def _poll_current_state(self):
        if not self.current_speaker or self.current_speaker not in self.speakers:
            return
        device = self.speakers[self.current_speaker]

        try:
            state = {
                'speaker': self.current_speaker,
                'speakers': list(self.speakers.keys()),
                'volume': device.volume,
                'mute': device.mute,
                'is_playing': False,
                'track': 'Nothing playing',
                'artist': None,
                'album': None,
                'album_art': None,
                'position': 0,
                'duration': 0,
            }

            # Transport state (playing/paused)
            transport = device.get_current_transport_info()
            state['is_playing'] = transport.get('current_transport_state') == 'PLAYING'

            # Track info
            try:
                track = device.get_current_track_info()

                state.update({
                    'track': track.get('title') or 'Nothing playing',
                    'artist': track.get('artist') or '',
                    'album': track.get('album') or '',
                })

                # Convert position and duration to seconds
                state['position'] = self._time_to_seconds(track.get('position'))
                state['duration'] = self._time_to_seconds(track.get('duration'))

                # Album art
                raw_art = track.get('album_art')
                state['album_art'] = self._make_album_art_proxy_url(raw_art)

            except Exception as e:
                logger.debug(f"Failed to get track info: {e}")

            # Only emit if something meaningful changed
            if state != self._last_state:
                self._last_state = state.copy()
                self.socketio.emit('sonos_update', state)

        except Exception as e:
            logger.debug(f"SoCo error on {self.current_speaker}: {e}")

    def _time_to_seconds(self, time_str: str | None) -> int:
        """Convert SoCo time string (e.g. '0:03:45' or '2:15:30') to seconds"""
        if not time_str or time_str == '0:00':
            return 0
        try:
            parts = time_str.split(':')
            if len(parts) == 3:
                h, m, s = map(int, parts)
                return h * 3600 + m * 60 + s
            elif len(parts) == 2:
                m, s = map(int, parts)
                return m * 60 + s
            else:
                return int(parts[0])
        except:
            return 0

    def _make_album_art_proxy_url(self, original_url: str | None) -> str | None:
        if not original_url:
            return None
        if original_url.startswith(('https://', 'http://')) and not any(x in original_url for x in ['192.168.', '10.', '172.16.']):
            return original_url

        encoded = quote(original_url, safe=':/?=&')
        return f"/sonos-art?url={encoded}"

    def _broadcast_speakers(self):
        data = {
            'speakers': list(self.speakers.keys()),
            'current': self.current_speaker,
            'preferred': self.preferred_name,
        }
        self.socketio.emit('sonos_speakers', data)

    def _persist_preferred_name(self, name: str) -> bool:
        """Write [sonos] player_name so the choice survives restarts."""
        try:
            if hasattr(self.config, "set_option"):
                self.config.set_option("sonos", "player_name", name)
            else:
                # Raw ConfigParser (tests) — in-memory only
                if not self.config.has_section("sonos"):
                    self.config.add_section("sonos")
                self.config.set("sonos", "player_name", name)
            self.preferred_name = name
            logger.info(f"🎵 Default Sonos speaker saved: {name}")
            return True
        except Exception as e:
            logger.error(f"Failed to save default Sonos speaker: {e}")
            return False

    def switch_speaker(self, name: str, *, make_default: bool = True) -> bool:
        """Switch the active speaker; by default also persist as player_name."""
        if name not in self.speakers:
            logger.warning(f"Sonos speaker not found: {name}")
            return False

        self.current_speaker = name
        self._manual_override = True
        self._last_default_saved = False

        if make_default:
            self._last_default_saved = self._persist_preferred_name(name)
            if self._last_default_saved:
                # Preferred is the source of truth for future discovery.
                self._manual_override = False

        self._broadcast_speakers()
        return True

    def execute_command(self, data: dict) -> dict:
        target_speaker = data.get('speaker') or self.current_speaker
        if not target_speaker or target_speaker not in self.speakers:
            return {'error': f'No valid speaker: {target_speaker}'}

        device = self.speakers[target_speaker]

        cmd = data.get('command')
        value = data.get('value')

        if not cmd:
            return {'error': 'No command provided'}

        try:
            if cmd == 'playpause':
                transport = device.get_current_transport_info()
                if transport.get('current_transport_state') == 'PLAYING':
                    device.pause()
                else:
                    device.play()

            elif cmd == 'play':
                device.play()
            elif cmd == 'pause':
                device.pause()
            elif cmd == 'next':
                device.next()
            elif cmd == 'previous':
                device.previous()
            elif cmd == 'volume':
                if isinstance(value, (int, float)):
                    device.volume = max(0, min(100, int(value)))
            elif cmd == 'mute':
                if value is None:
                    device.mute = not device.mute
                else:
                    device.mute = bool(value)
            elif cmd == 'seek':
                if value is not None and 0 <= float(value) <= 1:
                    track = device.get_current_track_info()
                    duration = self._time_to_seconds(track.get('duration'))
                    if duration > 0:
                        # Convert percentage to seconds
                        position_seconds = int(duration * float(value))
                        # Convert to SoCo required HH:MM:SS format
                        hours = position_seconds // 3600
                        minutes = (position_seconds % 3600) // 60
                        seconds = position_seconds % 60
                        timestamp = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
                        
                        logger.debug(f"🎵 Seeking to {timestamp} ({position_seconds}s)")
                        device.seek(timestamp)
            else:
                return {'error': f'Unknown command: {cmd}'}

            # Refresh state after command
            time.sleep(0.4)
            self._poll_current_state()
            return {'success': True}

        except SoCoException as e:
            logger.error(f"Sonos command '{cmd}' failed on {target_speaker}: {e}")
            return {'error': str(e)}
        except Exception as e:
            logger.error(f"Unexpected error in Sonos command '{cmd}' on {target_speaker}: {e}")
            return {'error': str(e)}
            
    def request_state(self):
        """Called when frontend requests current state"""
        if self.current_speaker and self.current_speaker in self.speakers:
            self._poll_current_state()
        else:
            empty_state = {
                'speaker': self.current_speaker,
                'speakers': list(self.speakers.keys()),
                'track': 'Nothing playing',
                'artist': '',
                'is_playing': False,
                'volume': None,
                'mute': False,
            }
            self.socketio.emit('sonos_update', empty_state)

    def get_current_state(self) -> dict:
        if self.current_speaker and self.current_speaker in self.speakers:
            try:
                self._poll_current_state()
                return self._last_state
            except:
                pass
        return {
            'speaker': self.current_speaker,
            'speakers': list(self.speakers.keys()),
            'is_playing': False,
            'volume': None,
        }
