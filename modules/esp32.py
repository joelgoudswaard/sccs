# modules/esp32.py — serial bridge to ESP32-S3 lighting MCUs (UART).
import serial
import threading
import time
import os
import logging

from modules.esp_pins import parse_light_pin

logger = logging.getLogger("sccs")


def brightness_to_pwm(brightness: int) -> int:
    """Convert 0-100 brightness percent to 0-255 PWM value for MCU."""
    return int(max(0, min(100, brightness)) * 2.55)


def pwm_to_brightness(pwm: int) -> int:
    """Convert 0-255 PWM to nearest 0-100 brightness percent (for state reads)."""
    return round(max(0, min(255, pwm)) / 2.55)


class Esp32Manager:
    def __init__(self, config):
        self.config = config
        # esp_id (1-based) → open Serial; serial_ports order maps to ESP 1, 2, …
        self.serials: dict[int, serial.Serial] = {}
        # esp_id → True only after the MCU answers a probe (open UART ≠ online)
        self.alive: dict[int, bool] = {}
        self.ser = None  # first *responding* port (compat for single-MCU callers)
        self.serial_lock = threading.Lock()
        self.state = {}

        self.OPTIMISTIC_LOCK: dict[str, float] = {}
        self.OPTIMISTIC_LOCK_DURATION = config.getfloat('esp32', 'optimistic_lock_duration', 2.5)

        # name → (esp_id, module_gpio)
        self.LIGHT_MAP = {}
        # name → {white|red|green: (esp_id, module_gpio)}
        self.RGB_BUG_LIGHTS = {}
        self.LIGHT_ICONS = {}

        self._frontend_controls = []   # Unified ordered list for frontend

        self._load_all_controls()

        self.COMMAND_DELAY = config.getfloat('esp32', 'command_delay', 0.08)
        self.RESPONSE_DELAY = config.getfloat('esp32', 'response_delay', 0.04)
        self.RGB_RED_SWITCH_RAMP = config.getint('esp32', 'rgb_red_switch_ramp_ms', 180)
        self.RGB_MODE_SWITCH_RAMP = config.getint('esp32', 'rgb_mode_switch_ramp_ms', 250)
        self._last_disconnect_warn = 0.0
        self._last_probe_attempt = 0.0
        # How often to re-probe silent/missing MCUs (seconds).
        self.PROBE_INTERVAL = config.getfloat('esp32', 'probe_interval', fallback=30.0)

    def _load_all_controls(self):
        """Load PWM, RGB, and Relay controls with custom ordering"""
        # Safely clear all collections
        self.LIGHT_MAP.clear()
        self.RGB_BUG_LIGHTS.clear()
        
        if not hasattr(self, 'RGB_LIGHTS'):
            self.RGB_LIGHTS = set()
        else:
            self.RGB_LIGHTS.clear()
        
        self.LIGHT_ICONS.clear()
        self._frontend_controls.clear()

        logger.debug("=== LOADING ALL CONTROLS WITH ORDER ===")

        # ====================== LIGHTS (PWM + RGB) ======================
        if self.config.has_section('lights'):
            for name, line in self.config.items('lights'):
                parts = [p.strip() for p in str(line).split('|')]
                if len(parts) < 4:
                    logger.warning(f"Invalid light: {name}")
                    continue

                friendly = parts[0]
                light_type = parts[1].lower()
                icon = parts[-2] if len(parts) > 2 and parts[-2].startswith('fa-') else "fa-lightbulb"
                try:
                    order = int(parts[-1])
                except:
                    order = 999

                self.LIGHT_ICONS[name] = icon

                if light_type == "pwm":
                    try:
                        self.LIGHT_MAP[name] = parse_light_pin(parts[2])
                        self._frontend_controls.append({
                            "name": name,
                            "label": friendly,
                            "type": "dimmer",
                            "icon": icon,
                            "has_mode": False,
                            "order": order
                        })
                        logger.debug(f"✓ PWM: {name} → {parts[2]} | order {order}")
                    except Exception:
                        logger.error(f"Bad PWM pin for {name}: {parts[2]!r}")

                elif light_type == "rgb_bug":
                    try:
                        if len(parts) < 5:
                            continue
                        self.RGB_BUG_LIGHTS[name] = {
                            "white": parse_light_pin(parts[2]),
                            "red":   parse_light_pin(parts[3]),
                            "green": parse_light_pin(parts[4]),
                        }
                        self.RGB_LIGHTS.add(name)
                        self._frontend_controls.append({
                            "name": name,
                            "label": friendly,
                            "type": "dimmer",
                            "icon": icon,
                            "has_mode": True,
                            "order": order
                        })
                        logger.debug(
                            f"✓ RGB: {name} → W{parts[2]} R{parts[3]} G{parts[4]} | order {order}"
                        )
                    except Exception as e:
                        logger.error(f"RGB parse error {name}: {e}")

        # ====================== RELAYS ======================
        if self.config.has_section('gpio'):
            for name, line in self.config.items('gpio'):
                parts = [p.strip() for p in str(line).split('|')]
                if len(parts) < 5 or line.strip().startswith('#'):
                    continue

                friendly = parts[0]
                icon = parts[4] if len(parts) > 4 and parts[4].startswith('fa-') else "fa-lightbulb"
                try:
                    order = int(parts[5])
                except:
                    order = 999

                self._frontend_controls.append({
                    "name": name,
                    "label": friendly,
                    "type": "relay",
                    "icon": icon,
                    "has_mode": False,
                    "order": order
                })
                logger.debug(f"✓ Relay: {name} | order {order}")

        self._frontend_controls.sort(key=lambda x: x['order'])

    # ====================== FRONTEND ======================
    def get_frontend_config(self):
        """Return single unified list in user-defined order"""
        return self._frontend_controls

    def init_serial(self) -> bool:
        """Open UART ports and probe for live ESP32 firmware.

        Opening /dev/ttyAMAx only proves the Pi UART exists. On a test bench
        without the modules fitted, those nodes still open successfully — so
        we require a GETVCC → VCC handshake before reporting online.
        """
        ports = [p.strip() for p in self.config.get('esp32', 'serial_ports').split(',') if p.strip()]
        baud_rate = self.config.getint('esp32', 'baud_rate')
        init_delay = self.config.getfloat('esp32', 'init_delay')
        timeout = self.config.getfloat('esp32', 'timeout')

        self.serials.clear()
        self.alive.clear()
        self.ser = None
        for idx, port in enumerate(ports, start=1):
            if not os.path.exists(port):
                logger.debug("ESP%d port %s not present yet", idx, port)
                continue
            try:
                ser = serial.Serial(port, baud_rate, timeout=timeout)
                time.sleep(init_delay)
                ser.reset_input_buffer()
                self.serials[idx] = ser
                logger.info("📟 ESP32-%d UART open on %s — probing MCU…", idx, port)
            except Exception as e:
                logger.error("❌ Failed to open ESP%d %s: %s", idx, port, e)

        if not self.serials:
            logger.warning("⚠️ No ESP32 lighting serial ports found")
            return False

        for idx in list(self.serials.keys()):
            if self._probe_esp(idx):
                continue
            # App firmware may still be coming out of reset after a flash.
            for _ in range(2):
                time.sleep(0.25)
                if self._probe_esp(idx):
                    break

        if not self.is_connected():
            logger.warning(
                "⚠️ ESP serial port(s) open but no MCU response — "
                "lighting offline until firmware answers (GETVCC)"
            )
            return False
        return True

    def _sync_primary_ser(self) -> None:
        """Keep self.ser pointing at the first responding (else first open) port."""
        for esp_id in sorted(self.serials.keys()):
            if self.alive.get(esp_id):
                ser = self.serials.get(esp_id)
                if ser and getattr(ser, "is_open", False):
                    self.ser = ser
                    return
        for ser in self.serials.values():
            if ser and getattr(ser, "is_open", False):
                self.ser = ser
                return
        self.ser = None

    def _probe_esp(self, esp_id: int) -> bool:
        """Return True if ESP firmware answers GETVCC on this port."""
        ser = self.serials.get(esp_id)
        if not ser or not getattr(ser, "is_open", False):
            self.alive[esp_id] = False
            self._sync_primary_ser()
            return False

        was_alive = self.alive.get(esp_id, False)
        # Probe must talk to this exact port even when not yet marked alive.
        resp = self.send_command("GETVCC", expect="VCC", esp=esp_id, require_alive=False)
        ok = bool(resp and resp.startswith("VCC"))
        self.alive[esp_id] = ok
        self._sync_primary_ser()

        port = getattr(ser, "port", "?")
        if ok and not was_alive:
            logger.info("📟 ESP32-%d online on %s", esp_id, port)
        elif not ok and was_alive:
            logger.warning("📟 ESP32-%d went silent on %s", esp_id, port)
        elif not ok:
            logger.warning(
                "📟 ESP32-%d not responding on %s (no GETVCC reply)",
                esp_id,
                port,
            )
        return ok

    def refresh_connection(self, force: bool = False) -> bool:
        """Re-open missing ports and re-probe silent MCUs (for hot-plug / delayed power)."""
        now = time.time()
        if not force and (now - self._last_probe_attempt) < self.PROBE_INTERVAL:
            return self.is_connected()
        self._last_probe_attempt = now

        ports = [p.strip() for p in self.config.get('esp32', 'serial_ports').split(',') if p.strip()]
        baud_rate = self.config.getint('esp32', 'baud_rate')
        timeout = self.config.getfloat('esp32', 'timeout')

        for idx, port in enumerate(ports, start=1):
            ser = self.serials.get(idx)
            open_ok = bool(ser and getattr(ser, "is_open", False))
            if open_ok and self.alive.get(idx):
                continue

            if not open_ok:
                if not os.path.exists(port):
                    self.alive[idx] = False
                    continue
                try:
                    if ser:
                        try:
                            ser.close()
                        except Exception:
                            pass
                    ser = serial.Serial(port, baud_rate, timeout=timeout)
                    time.sleep(min(0.2, self.config.getfloat('esp32', 'init_delay', fallback=0.5)))
                    ser.reset_input_buffer()
                    self.serials[idx] = ser
                    logger.debug("ESP%d reopened %s for probe", idx, port)
                except Exception as e:
                    logger.debug("ESP%d reopen %s failed: %s", idx, port, e)
                    self.alive[idx] = False
                    continue

            self._probe_esp(idx)

        self._sync_primary_ser()
        return self.is_connected()

    def _ser_for_esp(self, esp: int | None, *, require_alive: bool = True) -> serial.Serial | None:
        if esp is not None and esp in self.serials:
            ser = self.serials[esp]
            if ser and getattr(ser, "is_open", False):
                if require_alive and not self.alive.get(esp):
                    return None
                return ser
            return None
        # Prefer a responding MCU; fall back only when require_alive is False.
        for esp_id, ser in self.serials.items():
            if not (ser and getattr(ser, "is_open", False)):
                continue
            if require_alive and not self.alive.get(esp_id):
                continue
            return ser
        if not require_alive:
            if self.ser and getattr(self.ser, "is_open", False):
                return self.ser
            for ser in self.serials.values():
                if ser and getattr(ser, "is_open", False):
                    return ser
        return None

    def send_command(
        self,
        cmd: str,
        expect: str = None,
        esp: int | None = None,
        *,
        require_alive: bool = True,
    ) -> str | None:
        """Send cmd to an ESP (1-based). If esp is None, use the first live port.

        require_alive=False is used only for probes so a silent port can still
        be queried without looking "online".
        """
        ser = self._ser_for_esp(esp, require_alive=require_alive)
        if not ser:
            now = time.time()
            if now - self._last_disconnect_warn > 60:
                logger.warning(
                    "ESP lighting MCU not responding — commands skipped until firmware answers"
                )
                self._last_disconnect_warn = now
            return None

        with self.serial_lock:
            try:
                ser.reset_input_buffer()
                ser.write((cmd + '\n').encode('utf-8'))
                ser.flush()
                time.sleep(self.COMMAND_DELAY)

                if expect is None:
                    # RAMP/SET etc. do not produce responses.
                    return None

                for _ in range(5):
                    blob = ser.readline().decode('utf-8', errors='ignore').strip()
                    if not blob:
                        continue
                    if blob.startswith(expect):
                        if esp is not None:
                            self.alive[esp] = True
                        return blob
                    idx = blob.find(expect)
                    if idx != -1:
                        if esp is not None:
                            self.alive[esp] = True
                        return blob[idx:]
                    logger.debug("Discarded unexpected serial response while waiting for %s: %s", expect, blob)

            except serial.SerialException as se:
                msg = str(se)
                if "readiness to read but returned no data" in msg or "multiple access" in msg:
                    return None
                logger.error(f"Serial error sending '{cmd}' (ESP{esp}): {se}")
                if esp is not None:
                    self.alive[esp] = False
                try:
                    ser.reset_input_buffer()
                except Exception:
                    pass
            except Exception as e:
                msg = str(e)
                if any(x in msg for x in ("NoneType", "closed", "not open", "EBADF", "Bad file descriptor")):
                    if esp is not None:
                        self.alive[esp] = False
                    return None
                logger.error(f"Serial error sending '{cmd}' (ESP{esp}): {e}")
                try:
                    ser.reset_input_buffer()
                except Exception:
                    pass
        return None

    def should_ignore_for_optimistic(self, name: str) -> bool:
        if name in self.OPTIMISTIC_LOCK:
            if time.time() < self.OPTIMISTIC_LOCK[name]:
                return True
            else:
                self.OPTIMISTIC_LOCK.pop(name, None)
        return False

    def _ramp(self, target, pwm: int, ramp_ms: int) -> None:
        esp_id, gpio = parse_light_pin(target)
        self.send_command(f"RAMP {gpio} {pwm} {ramp_ms}", esp=esp_id)

    def _get_pwm(self, target) -> int | None:
        esp_id, gpio = parse_light_pin(target)
        resp = self.send_command(f"GET {gpio}", expect="VALUE", esp=esp_id)
        if resp and resp.startswith("VALUE"):
            try:
                return int(resp.split()[2])
            except (IndexError, ValueError):
                return None
        return None

    def read_all_states(self):
        if not self.is_connected():
            return

        for name, target in self.LIGHT_MAP.items():
            if self.should_ignore_for_optimistic(name):
                continue
            pwm = self._get_pwm(target)
            if pwm is not None:
                self.state[name] = pwm_to_brightness(pwm)

        for name, pins in self.RGB_BUG_LIGHTS.items():
            if self.should_ignore_for_optimistic(name):
                continue
            try:
                red_pwm = self._get_pwm(pins["red"]) or 0
                white_pwm = self._get_pwm(pins["white"]) or 0

                # Both channels off: brightness is 0 and mode cannot be inferred
                # (red==white==0 would otherwise always look like "white").
                if red_pwm == 0 and white_pwm == 0:
                    self.state[name] = 0
                    continue

                if red_pwm > white_pwm:
                    self.state[name] = pwm_to_brightness(red_pwm)
                    self.state[f"{name}_mode"] = "red"
                else:
                    self.state[name] = pwm_to_brightness(white_pwm)
                    self.state[f"{name}_mode"] = "white"
            except Exception:
                pass

    def set_rgb_bug_light(self, name: str, brightness: int, mode: str = 'white', ramp_ms: int | None = None) -> bool:
        config = self.RGB_BUG_LIGHTS.get(name)
        if not config:
            return False

        pwm = brightness_to_pwm(brightness)
        if ramp_ms is not None:
            mode_ramp = ramp_ms
        else:
            mode_ramp = self.RGB_MODE_SWITCH_RAMP

        # Use a consistent ramp time for crossfading the channels during mode switch.
        xfade_ramp = mode_ramp

        if mode == 'red':
            self._ramp(config['red'], pwm, xfade_ramp)
            self._ramp(config['green'], int(pwm * 0.05), xfade_ramp)
            self._ramp(config['white'], 0, xfade_ramp)
        else:
            self._ramp(config['red'], 0, xfade_ramp)
            self._ramp(config['green'], 0, xfade_ramp)
            self._ramp(config['white'], pwm, xfade_ramp)

        # Optimistic local state — mode must stick even when brightness is 0 so a
        # later dual-channel-zero hardware read doesn't invent "white".
        self.state[name] = max(0, min(100, int(brightness)))
        self.state[f"{name}_mode"] = mode if mode in ("red", "white") else "white"
        self.OPTIMISTIC_LOCK[name] = time.time() + self.OPTIMISTIC_LOCK_DURATION
        return True

    def cleanup(self):
        for esp_id, ser in list(self.serials.items()):
            if ser and getattr(ser, "is_open", False):
                try:
                    ser.close()
                except Exception as e:
                    logger.error("Error closing ESP%d serial: %s", esp_id, e)
        self.serials.clear()
        self.alive.clear()
        self.ser = None

    def is_connected(self) -> bool:
        """True only when at least one ESP firmware has answered a probe.

        An open Pi UART is not enough — the modules may be unpowered or not fitted.
        """
        for esp_id, ser in self.serials.items():
            if self.alive.get(esp_id) and ser and getattr(ser, "is_open", False):
                return True
        return False
