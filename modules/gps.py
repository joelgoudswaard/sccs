# modules/gps.py
"""
GPS Module for SCCS (Singularity Camper Control System).
"""

import serial
import time
import threading
import math
import logging
from datetime import date
import zoneinfo
from typing import Tuple, Optional

import pynmea2
from geopy.geocoders import Nominatim
from astral import LocationInfo
from astral.sun import sun
from flask_socketio import SocketIO

logger = logging.getLogger("sccs")


def _send_gps_toast(message: str, title: str = "GPS", toast_type: str = "info", duration: int = 5000):
    """Safe toast sender"""
    try:
        from modules.toasts import toast_manager
        if toast_manager is None:
            logger.warning("ToastManager not available yet")
            return

        if toast_type == "success":
            toast_manager.success(message, title=title, duration=duration)
        elif toast_type == "warning":
            toast_manager.warning(message, title=title, duration=duration)
        else:
            toast_manager.info(message, title=title, duration=duration)

        logger.debug(f"GPS Toast sent → {title}: {message}")
    except Exception as e:
        logger.warning(f"Could not send GPS toast: {e}")


class GPSModule:
    """Manages GPS hardware interface, position tracking, time, location naming, and solar data."""

    def __init__(self, config, socketio: SocketIO):
        self.config = config
        self.socketio = socketio
        self.serial: Optional[serial.Serial] = None
        self.geolocator: Optional[Nominatim] = None
        self._serial_lock = threading.Lock()

        self.fallback_timezone = self.config.get(
            'gps', 'fallback_timezone', fallback='Australia/Melbourne'
        )

        self.state = {
            "latitude": None, 
            "longitude": None, 
            "local_time": None, 
            "date": None,
            "utc_time": None, 
            "satellites": 0,
            "fix_quality": 0,
            "hdop": None,
            "speed_kmh": None,
            "altitude_m": None,
            "suburb": None,
            "timezone": self.fallback_timezone,
            "last_known_suburb": None,
            "sunrise": None, 
            "sunset": None, 
            "raw_sentences": [],
            "using_fallback": False, 
            "force_no_fix": False,
            "force_no_hardware": False,
        }

        self.last_known_lat = self.last_known_lon = None
        self.last_suburb_update = self.last_broadcast = 0.0

        self._previous_fix_quality = 0
        self._last_fix_toast_time = 0.0
        self._toast_cooldown = config.getfloat('gps', 'toast_cooldown')

        self._gps_port_unhealthy_warned = False
        self._stop_event = threading.Event()
        self._reader_thread: threading.Thread | None = None
        self._sun_thread: threading.Thread | None = None

        logger.debug("📍 GPSModule initialized")

    # ==================================================================
    # Toast Helpers
    # ==================================================================
    def _send_fix_lost_toast(self, force=False):
        if not force and (time.time() - self._last_fix_toast_time < self._toast_cooldown):
            return
        _send_gps_toast("Using last known position", title="GPS Fix Lost", toast_type="warning")
        self._last_fix_toast_time = time.time()

    def _send_fix_acquired_toast(self, force=False):
        if not force and (time.time() - self._last_fix_toast_time < self._toast_cooldown):
            return
        _send_gps_toast("Updating location data", title="GPS Fix Acquired", toast_type="success")
        self._last_fix_toast_time = time.time()

    def set_no_fix_simulation(self, enabled: bool) -> None:
        """Force no-fix mode for the UI."""
        was_enabled = self.state.get("force_no_fix", False)
        if enabled == was_enabled:
            return

        self.state["force_no_fix"] = bool(enabled)
        if enabled:
            self.state["force_no_hardware"] = False
        self.socketio.emit('gps_update', self.get_state())

        if enabled:
            self._send_fix_lost_toast(force=True)
            self._previous_fix_quality = 0
        else:
            self._send_fix_acquired_toast(force=True)
            self._previous_fix_quality = 1

    def set_no_hardware_simulation(self, enabled: bool) -> None:
        """Simulate missing GPS serial hardware."""
        was_enabled = self.state.get("force_no_hardware", False)
        if enabled == was_enabled:
            return

        self.state["force_no_hardware"] = bool(enabled)
        if enabled:
            self.state["force_no_fix"] = False
        self.socketio.emit('gps_update', self.get_state())

    def _fallback_location_fields(self) -> dict:
        """Coords/name used for sun, weather, and UI when the receiver is absent."""
        lat, lon = self.get_fallback_coords()
        return {
            "latitude": lat,
            "longitude": lon,
            "suburb": self.get_fallback_name(),
            "timezone": self.get_fallback_timezone(),
            "using_fallback": True,
        }

    def get_state(self) -> dict:
        if self.state.get("force_no_hardware"):
            return {
                **self._fallback_location_fields(),
                "local_time": None,
                "date": None,
                "utc_time": None,
                "satellites": 0,
                "fix_quality": 0,
                "hdop": None,
                "speed_kmh": None,
                "altitude_m": None,
                "last_known_suburb": self.state.get("last_known_suburb"),
                "sunrise": self.state.get("sunrise"),
                "sunset": self.state.get("sunset"),
                "raw_sentences": [],
                "force_no_fix": False,
                "force_no_hardware": True,
                "hardware_missing": True,
            }

        state = self.state.copy()
        state["hardware_missing"] = not (
            self.serial and getattr(self.serial, "is_open", False)
        )
        if state["hardware_missing"]:
            # Surface configured fallback location so the Settings tile can show it.
            state.update(self._fallback_location_fields())
        elif not self._valid_position(state.get("latitude"), state.get("longitude")):
            # Receiver is up but has no fix (empty GGA/RMC → 0,0). Use last
            # known good coords if we have them, otherwise Alexandra fallback.
            if self._valid_position(self.last_known_lat, self.last_known_lon):
                state["latitude"] = self.last_known_lat
                state["longitude"] = self.last_known_lon
                state["using_fallback"] = True
            else:
                state.update(self._fallback_location_fields())
        if state.get("force_no_fix"):
            state.update({
                "fix_quality": 0,
                "satellites": 0,
                "hdop": None,
                "latitude": None,
                "longitude": None,
                "speed_kmh": None,
                "altitude_m": None,
            })
        return state

    @staticmethod
    def _valid_position(lat, lon) -> bool:
        """True for a real WGS84 pair. Empty NMEA becomes 0,0 (null island)."""
        if lat is None or lon is None:
            return False
        try:
            lat_f = float(lat)
            lon_f = float(lon)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(lat_f) or not math.isfinite(lon_f):
            return False
        if abs(lat_f) > 90 or abs(lon_f) > 180:
            return False
        # pynmea2 reports 0.0/0.0 when lat/lon fields are blank (no fix).
        if lat_f == 0.0 and lon_f == 0.0:
            return False
        return True

    def _parse_lat_lon(self, msg) -> Tuple[Optional[float], Optional[float]]:
        """Return signed decimal degrees from pynmea2 LatLonFix properties."""
        try:
            lat = getattr(msg, "latitude", None)
            lon = getattr(msg, "longitude", None)
            if not self._valid_position(lat, lon):
                return None, None
            return round(float(lat), 6), round(float(lon), 6)
        except Exception:
            return None, None

    def _parse_hdop(self, msg) -> Optional[float]:
        """GGA exposes horizontal_dil; GSA/GNS use hdop."""
        for attr in ("horizontal_dil", "hdop"):
            raw = getattr(msg, attr, None)
            if raw is None or raw == "":
                continue
            try:
                value = float(raw)
                if value >= 0:
                    return round(value, 1)
            except (TypeError, ValueError):
                continue
        return None

    def init_gps(self) -> bool:
        ports = [p.strip() for p in self.config.get('gps', 'serial_ports').split(',')]
        baud = self.config.getint('gps', 'baud_rate')
        timeout = self.config.getfloat('gps', 'timeout')

        for port in ports:
            if not __import__('os').path.exists(port):
                continue
            try:
                with self._serial_lock:
                    self.serial = serial.Serial(port, baud, timeout=timeout)
                logger.info(f"🛰️ GPS initialised on {port}")
                return True
            except Exception as e:
                logger.debug(f"Failed to open GPS on {port}: {e}")
        logger.error("No GPS hardware found on any configured port")
        return False

    def stop_reader(self) -> None:
        """Signal background GPS threads to exit and wait briefly."""
        self._stop_event.set()
        for thread in (self._reader_thread, self._sun_thread):
            if thread and thread.is_alive():
                thread.join(timeout=5)

    def cleanup(self) -> None:
        """Stop reader threads and release the GPS serial port."""
        self.stop_reader()
        if not self.serial:
            return
        try:
            with self._serial_lock:
                if getattr(self.serial, "is_open", False):
                    self.serial.close()
        except Exception as e:
            logger.debug(f"GPS serial close: {e}")
        finally:
            self.serial = None

    def init_geolocator(self) -> bool:
        if self.geolocator is not None:
            return True
        try:
            self.geolocator = Nominatim(user_agent="sccs-rv-control-system", timeout=12)
            logger.info("🌍 Nominatim geolocator initialised")
            return True
        except Exception as e:
            logger.warning(f"Geolocator initialisation failed: {e}")
            return False

    def start_reader(self) -> None:
        if self._reader_thread and self._reader_thread.is_alive():
            return
        self._stop_event.clear()
        self.init_geolocator()
        self._reader_thread = threading.Thread(
            target=self._reader_loop, daemon=True, name="GPS_Reader"
        )
        self._sun_thread = threading.Thread(
            target=self._sun_refresh_loop, daemon=True, name="SunRefresh"
        )
        self._reader_thread.start()
        self._sun_thread.start()

    def _reader_loop(self) -> None:
        while not self._stop_event.is_set():
            if not self.serial or not getattr(self.serial, 'is_open', False):
                if self._stop_event.wait(0.5):
                    break
                continue

            try:
                try:
                    with self._serial_lock:
                        line_bytes = self.serial.readline()
                except serial.SerialException as se:
                    msg = str(se)
                    if "readiness to read but returned no data" in msg or "multiple access" in msg:
                        if not self._gps_port_unhealthy_warned:
                            port = getattr(self.serial, 'port', 'unknown')
                            logger.warning(
                                "🛰️ GPS port %s opened but has no data. "
                                "Check wiring, power, serial console off, and [gps] serial_ports.",
                                port,
                            )
                            self._gps_port_unhealthy_warned = True
                        time.sleep(1.0)
                        continue
                    raise

                if not line_bytes:
                    time.sleep(0.05)
                    continue

                line = line_bytes.decode('ascii', errors='ignore').strip()
                if not line or not line.startswith('$'):
                    continue

                self.state["raw_sentences"] = (self.state["raw_sentences"] + [line])[-15:]

                msg = pynmea2.parse(line)
                position_updated = False
                telemetry_updated = False

                if self.state.get("force_no_fix"):
                    now = time.time()
                    if now - self.last_broadcast > self.config.getfloat('gps', 'broadcast_interval'):
                        self.socketio.emit('gps_update', self.get_state())
                        self.last_broadcast = now
                    time.sleep(0.03)
                    continue

                # Real GPS parsing...
                if isinstance(msg, pynmea2.GGA):
                    quality = getattr(msg, 'quality', None) or getattr(msg, 'gps_qual', None) or 0
                    new_quality = int(quality) if quality is not None else 0
                    if new_quality != self.state["fix_quality"]:
                        self.state["fix_quality"] = new_quality
                        telemetry_updated = True
                    num_sats = int(getattr(msg, 'num_sats', 0) or 0)
                    if num_sats != self.state["satellites"]:
                        self.state["satellites"] = num_sats
                        telemetry_updated = True

                    hdop = self._parse_hdop(msg)
                    if hdop is not None and hdop != self.state.get("hdop"):
                        self.state["hdop"] = hdop
                        telemetry_updated = True

                    lat, lon = self._parse_lat_lon(msg)
                    if new_quality >= 1 and lat is not None:
                        self.state["latitude"] = lat
                        self.state["longitude"] = lon
                        position_updated = True

                    altitude = getattr(msg, 'altitude', None)
                    if altitude is not None and altitude != '':
                        try:
                            altitude_m = round(float(altitude), 1)
                            if self.state.get("altitude_m") != altitude_m:
                                self.state["altitude_m"] = altitude_m
                                position_updated = True
                        except (TypeError, ValueError):
                            pass

                if isinstance(msg, pynmea2.GSA):
                    hdop = self._parse_hdop(msg)
                    if hdop is not None and hdop != self.state.get("hdop"):
                        self.state["hdop"] = hdop
                        telemetry_updated = True

                if isinstance(msg, pynmea2.RMC):
                    lat, lon = self._parse_lat_lon(msg)
                    rmc_ok = str(getattr(msg, "status", "") or "").upper() == "A"
                    if rmc_ok and lat is not None:
                        self.state["latitude"] = lat
                        self.state["longitude"] = lon
                        position_updated = True

                    # pynmea2.RMC.datetime raises TypeError when datestamp is
                    # missing (normal no-fix sentences like $GNRMC,,V,...).
                    utc_raw = None
                    try:
                        utc_raw = msg.datetime
                    except (TypeError, ValueError):
                        utc_raw = None
                    if utc_raw:
                        utc_dt = utc_raw.replace(tzinfo=zoneinfo.ZoneInfo("UTC"))
                        self.state["utc_time"] = utc_dt.isoformat()
                        try:
                            local_tz = zoneinfo.ZoneInfo(self.state["timezone"])
                            local_dt = utc_dt.astimezone(local_tz)
                            self.state["local_time"] = local_dt.strftime("%I:%M:%S %p")
                            self.state["date"] = local_dt.strftime("%A, %d %B %Y")
                        except Exception:
                            self.state["local_time"] = utc_dt.strftime("%H:%M:%S UTC")
                            self.state["date"] = utc_dt.strftime("%Y-%m-%d")

                    spd = getattr(msg, 'spd_over_grnd', None)
                    if spd not in (None, ""):
                        try:
                            self.state["speed_kmh"] = round(float(spd) * 1.852, 1)
                        except (TypeError, ValueError):
                            pass

                current_quality = self.state.get("fix_quality", 0)
                
                if current_quality != self._previous_fix_quality:
                    if current_quality >= 1 and self._previous_fix_quality == 0:
                        self._send_fix_acquired_toast()
                    elif current_quality == 0:
                        self._send_fix_lost_toast()
                    
                    self._previous_fix_quality = current_quality

                # Broadcast
                now = time.time()
                if (position_updated or telemetry_updated or current_quality != self._previous_fix_quality) and \
                   (now - self.last_broadcast > self.config.getfloat('gps', 'broadcast_interval')):
                    self.state["using_fallback"] = False
                    self.socketio.emit('gps_update', self.get_state())
                    self.last_broadcast = now

                    if current_quality >= 1:
                        if not self.state.get("sunrise"):
                            self._update_sun_times()
                        if now - self.last_suburb_update > self.config.getfloat('gps', 'suburb_update_interval'):
                            self._update_suburb()

            except pynmea2.ParseError:
                pass
            except Exception as e:
                logger.error(f"GPS reader error: {e}")
                time.sleep(0.2)

            time.sleep(0.03)

    # === Background tasks ===
    def _sun_refresh_loop(self) -> None:
        interval = self.config.getfloat('gps', 'sun_update_interval')
        while not self._stop_event.wait(interval):
            if self.state.get("latitude") and self.state.get("fix_quality", 0) >= 1:
                self._update_sun_times()

    def _update_sun_times(self) -> bool:
        lat = self.state.get("latitude")
        lon = self.state.get("longitude")
        if not lat or not lon:
            return False
        try:
            location = LocationInfo(latitude=lat, longitude=lon)
            s = sun(location.observer, date=date.today())
            local_tz = zoneinfo.ZoneInfo(self.state["timezone"])
            sunrise = s["sunrise"].astimezone(local_tz)
            sunset = s["sunset"].astimezone(local_tz)
            self.state["sunrise"] = sunrise.strftime("%I:%M %p")
            self.state["sunset"] = sunset.strftime("%-I:%M %p")
            self.socketio.emit('gps_update', self.get_state())
            return True
        except Exception as e:
            logger.error(f"Sun times calculation failed: {e}")
            return False

    def _haversine_km(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        R = 6371.0
        lat1_rad = math.radians(lat1)
        lon1_rad = math.radians(lon1)
        lat2_rad = math.radians(lat2)
        lon2_rad = math.radians(lon2)
        dlat = lat2_rad - lat1_rad
        dlon = lon2_rad - lon1_rad
        a = (math.sin(dlat / 2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2)**2)
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c

    def _update_suburb(self) -> None:
        """Update suburb name using online Nominatim or offline fallback towns."""
        lat = self.state.get("latitude")
        lon = self.state.get("longitude")
        if not lat or not lon or self.state.get("force_no_fix"):
            return

        # Skip if movement is insignificant
        if self.last_known_lat is not None and self.last_known_lon is not None:
            distance = self._haversine_km(self.last_known_lat, self.last_known_lon, lat, lon)
            if distance < self.config.getfloat('gps', 'movement_threshold_km'):
                self.last_suburb_update = time.time()
                return

        self.last_known_lat = lat
        self.last_known_lon = lon

        source = "unknown"
        new_suburb = None

        try:
            # === 1. Try Nominatim (online) ===
            if self.geolocator:
                try:
                    location = self.geolocator.reverse(
                        (lat, lon),
                        exactly_one=True,
                        timeout=12,
                        language='en',
                        addressdetails=True
                    )
                    if location and location.raw and location.raw.get('address'):
                        addr = location.raw['address']
                        name_keys = ['suburb', 'town', 'village', 'hamlet', 'locality', 'city', 'place']
                        new_suburb = next((addr[key] for key in name_keys if addr.get(key)), None)

                        if new_suburb:
                            source = "Nominatim"
                            self.state["using_fallback"] = False

                except Exception as e:
                    logger.debug(f"Nominatim lookup failed: {e}")

            # === 2. Offline fallback ===
            if new_suburb is None:
                major_towns = [
                    {"name": "Alexandra", "lat": -37.191, "lon": 145.711},
                    {"name": "Mansfield", "lat": -37.052, "lon": 146.083},
                    {"name": "Eildon", "lat": -37.233, "lon": 145.917},
                    {"name": "Yea", "lat": -37.213, "lon": 145.424},
                    {"name": "Marysville", "lat": -37.510, "lon": 145.733},
                    {"name": "Healesville", "lat": -37.654, "lon": 145.514},
                    {"name": "Lilydale", "lat": -37.758, "lon": 145.350},
                    {"name": "Melbourne", "lat": -37.8136, "lon": 144.9631},
                ]

                closest = None
                min_dist = float('inf')
                for town in major_towns:
                    dist = self._haversine_km(lat, lon, town["lat"], town["lon"])
                    if dist < min_dist:
                        min_dist = dist
                        closest = town

                if closest and min_dist < 120:
                    new_suburb = (
                        closest["name"] if min_dist < 10
                        else f"{closest['name']} ({min_dist:.0f} km away)"
                    )
                    source = "offline_fallback"
                else:
                    new_suburb = f"{lat:.4f}, {lon:.4f}"
                    source = "coordinates"

                self.state["using_fallback"] = True

        except Exception as e:
            logger.warning(f"Suburb update failed: {e}")
            new_suburb = f"{lat:.4f}, {lon:.4f}"
            source = "error"
            self.state["using_fallback"] = True

        logger.info(f"📍 Location: {new_suburb} [{source}]")
        self.state["suburb"] = new_suburb
        self.state["last_known_suburb"] = new_suburb
        self.socketio.emit('gps_update', self.get_state())
        self.last_suburb_update = time.time()
        
    # ====================== FALLBACK ACCESSORS (Single Source of Truth) ======================
    def get_fallback_coords(self) -> tuple[float, float]:
        """Return canonical fallback latitude/longitude from [gps] section"""
        lat = self.config.getfloat('gps', 'fallback_latitude', fallback=-37.191)
        lon = self.config.getfloat('gps', 'fallback_longitude', fallback=145.711)
        return lat, lon

    def get_fallback_timezone(self) -> str:
        """Return fallback timezone from [gps] section"""
        return self.config.get('gps', 'fallback_timezone', fallback='Australia/Melbourne')

    def get_fallback_name(self) -> str:
        """Return fallback location name"""
        return self.config.get('gps', 'fallback_name', fallback='Alexandra')

    def get_fallback_data(self) -> dict:
        """Convenience: return all fallback info"""
        lat, lon = self.get_fallback_coords()
        return {
            "latitude": lat,
            "longitude": lon,
            "name": self.get_fallback_name(),
            "timezone": self.get_fallback_timezone()
        }