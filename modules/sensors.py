# modules/sensors.py
import threading
import time
import logging
import glob
import os
import struct
import subprocess

logger = logging.getLogger("sccs")
logger.propagate = True

W1_DEVICES_DIR = "/sys/bus/w1/devices/"
W1_DT_BASE = "/sys/firmware/devicetree/base"


class SensorManager:
    def __init__(self, config, send_command_func, socketio):
        self.config = config
        self.send_command = send_command_func
        self.socketio = socketio
        self.running = False
        self.thread = None
        self.on_update = None

        # Load 1-Wire kernel modules (best effort). On modern Raspberry Pi OS these
        # are usually loaded automatically by dtoverlay=w1-gpio in config.txt.
        # Running as non-root user will cause "Operation not permitted", which is harmless.
        try:
            subprocess.run(['modprobe', 'w1-gpio'], capture_output=True, check=False)
            subprocess.run(['modprobe', 'w1-therm'], capture_output=True, check=False)
        except Exception:
            pass
        time.sleep(0.5)

        # ====================== CALIBRATION FROM CONFIG ======================
        # Divider: 3V3 -- R_pullup (150 Ω) -- sense -- R_sender -- GND
        # Sender: empty ≈ 240 Ω, full ≈ 33 Ω (standard automotive fuel sender).
        self.WATER_R_EMPTY = config.getfloat('sensors', 'water_resistance_empty', fallback=240.0)
        self.WATER_R_FULL = config.getfloat('sensors', 'water_resistance_full', fallback=33.0)
        self.WATER_R_PULLUP = config.getfloat('sensors', 'water_pullup_ohms', fallback=150.0)
        # ESP32-S3 ADC is 12-bit (0..4095) unless firmware remaps to 10-bit.
        self.WATER_ADC_MAX = config.getfloat('sensors', 'water_adc_max', fallback=4095.0)
        self.WATER_CAPACITY_LITRES = config.getfloat('tanks', 'water_litres', fallback=0)

        # Water level on ESP32-1 module GPIO (silk 1-1); ANALOG <gpio> protocol.
        self.WATER_PIN = config.getint('esp32 analog', 'water_pin')
        self.WATER_ESP = config.getint('esp32 analog', 'water_esp', fallback=1)

        # Dual 1-Wire buses (SCCS PCB): GPIO4 outside+fridge, GPIO3 freezer.
        # Must match dtoverlay=w1-gpio,gpiopin=N lines in /boot/firmware/config.txt.
        self.OUTSIDE_BUS_GPIO = config.getint('sensors', 'outside_temp_bus_gpio', fallback=4)
        self.FRIDGE_BUS_GPIO = config.getint('sensors', 'fridge_temp_bus_gpio', fallback=4)
        self.FREEZER_BUS_GPIO = config.getint('sensors', 'freezer_temp_bus_gpio', fallback=3)

        # 1-Wire DS18B20 sensor IDs (folder names under /sys/bus/w1/devices/, e.g. "28-3ce1d4435d5a")
        # Leave blank to auto-detect first available 28* device on that role's bus.
        self.OUTSIDE_TEMP_ID = (config.get('sensors', 'outside_temp_sensor', fallback='') or '').strip() or None
        self.FRIDGE_TEMP_ID = (config.get('sensors', 'fridge_temp_sensor', fallback='') or '').strip() or None
        self.FREEZER_TEMP_ID = (config.get('sensors', 'freezer_temp_sensor', fallback='') or '').strip() or None
        # ========================================================

        self._last_analog_warn = 0.0
        self._last_vcc_warn = 0.0
        self._last_missing_warn: dict[str, float] = {}
        self.last_reading: dict = {}

        # gpio_pin -> sysfs master path (e.g. /sys/devices/w1_bus_master1)
        self._w1_masters_by_gpio = self._discover_w1_masters_by_gpio()
        expected_gpios = sorted({self.OUTSIDE_BUS_GPIO, self.FRIDGE_BUS_GPIO, self.FREEZER_BUS_GPIO})
        found = sorted(self._w1_masters_by_gpio.keys())
        logger.info(
            "🔋 1-Wire buses: expected GPIO %s → masters on GPIO %s",
            expected_gpios,
            found if found else "none yet",
        )
        for gpio in expected_gpios:
            if gpio not in self._w1_masters_by_gpio:
                logger.warning(
                    "   🌡️ No w1 master for GPIO %s — add "
                    "dtoverlay=w1-gpio,gpiopin=%s to /boot/firmware/config.txt and reboot",
                    gpio,
                    gpio,
                )

        configured = []
        if self.OUTSIDE_TEMP_ID:
            configured.append(f"outside={self.OUTSIDE_TEMP_ID}@GPIO{self.OUTSIDE_BUS_GPIO}")
        else:
            configured.append(f"outside=auto@GPIO{self.OUTSIDE_BUS_GPIO}")
        if self.FRIDGE_TEMP_ID:
            configured.append(f"fridge={self.FRIDGE_TEMP_ID}@GPIO{self.FRIDGE_BUS_GPIO}")
        else:
            configured.append(f"fridge=none@GPIO{self.FRIDGE_BUS_GPIO}")
        if self.FREEZER_TEMP_ID:
            configured.append(f"freezer={self.FREEZER_TEMP_ID}@GPIO{self.FREEZER_BUS_GPIO}")
        else:
            configured.append(f"freezer=none@GPIO{self.FREEZER_BUS_GPIO}")
        logger.info("🔋 1-Wire temp sensors configured: %s", ", ".join(configured))

        logger.info("🔋 SensorManager initialized")

    @staticmethod
    def _discover_w1_masters_by_gpio() -> dict[int, str]:
        """Map each w1-gpio overlay pin to its sysfs master directory.

        Raspberry Pi device-tree nodes look like /proc/device-tree/onewire@N
        with a 3-cell `gpios` property: <phandle pin flags>. Platform devices
        expose of_node → those DT nodes, and w1 registers them as w1_bus_master*.
        """
        gpio_to_dt: dict[int, str] = {}
        if os.path.isdir(W1_DT_BASE):
            try:
                for name in os.listdir(W1_DT_BASE):
                    if not name.startswith("onewire"):
                        continue
                    gpios_path = os.path.join(W1_DT_BASE, name, "gpios")
                    if not os.path.isfile(gpios_path):
                        continue
                    try:
                        with open(gpios_path, "rb") as fh:
                            data = fh.read()
                        if len(data) >= 12:
                            pin = struct.unpack(">I", data[4:8])[0]
                            gpio_to_dt[pin] = os.path.realpath(os.path.join(W1_DT_BASE, name))
                    except OSError:
                        continue
            except OSError:
                pass

        masters: dict[int, str] = {}
        for master in sorted(glob.glob("/sys/devices/w1_bus_master*")):
            of_node = os.path.join(master, "of_node")
            if not os.path.exists(of_node):
                continue
            try:
                real = os.path.realpath(of_node)
            except OSError:
                continue
            for gpio, dt_path in gpio_to_dt.items():
                if real == dt_path or real.endswith(os.path.basename(dt_path)):
                    masters[gpio] = master
                    break

        # If device-tree mapping failed but exactly one master exists and one
        # DT pin is known, associate them.
        if not masters:
            masters_found = sorted(glob.glob("/sys/devices/w1_bus_master*"))
            if len(masters_found) == 1 and len(gpio_to_dt) == 1:
                masters[next(iter(gpio_to_dt))] = masters_found[0]
            elif len(masters_found) == 1 and not gpio_to_dt:
                # Single w1 master without DT pin map — default GPIO for SCCS.
                masters[3] = masters_found[0]

        return masters

    def _devices_on_bus(self, bus_gpio: int | None) -> list[str]:
        """Return DS18B20 sysfs folders on a given bus GPIO (or all buses if None)."""
        if bus_gpio is None:
            return sorted(d for d in glob.glob(W1_DEVICES_DIR + "28*") if os.path.isdir(d))

        master = self._w1_masters_by_gpio.get(bus_gpio)
        if master and os.path.isdir(master):
            return sorted(d for d in glob.glob(os.path.join(master, "28*")) if os.path.isdir(d))

        # Master not resolved yet — fall back to global list (single-bus / pre-reboot).
        return sorted(d for d in glob.glob(W1_DEVICES_DIR + "28*") if os.path.isdir(d))

    def _read_ds18b20(self, sensor_id=None, bus_gpio: int | None = None, role: str = "temp"):
        """Read DS18B20. If sensor_id given (e.g. '28-xxx'), read that device; else first 28* on bus.

        When a configured sensor_id is missing, the log will list devices on that bus
        to help you correct the ID in config/sccs.conf.
        """
        try:
            if sensor_id:
                device_folder = os.path.join(W1_DEVICES_DIR, sensor_id)
                # Prefer the device instance hanging off the role's bus master when known.
                master = self._w1_masters_by_gpio.get(bus_gpio) if bus_gpio is not None else None
                if master:
                    master_dev = os.path.join(master, sensor_id)
                    if os.path.isdir(master_dev):
                        device_folder = master_dev

                if not os.path.isdir(device_folder):
                    now = time.time()
                    last = self._last_missing_warn.get(role, 0.0)
                    if now - last > 60:
                        logger.warning(
                            "   🌡️ Configured 1-Wire sensor not present on bus: %s (%s GPIO%s)",
                            sensor_id,
                            role,
                            bus_gpio if bus_gpio is not None else "?",
                        )
                        available = [os.path.basename(d) for d in self._devices_on_bus(bus_gpio)]
                        all_available = [
                            os.path.basename(d)
                            for d in glob.glob(W1_DEVICES_DIR + "28*")
                            if os.path.isdir(d)
                        ]
                        if available:
                            logger.warning("   Available DS18B20 on this bus: %s", available)
                        elif all_available:
                            logger.warning("   No sensors on this bus; other buses have: %s", all_available)
                        else:
                            logger.warning("   No 28* DS18B20 sensors detected on any 1-wire bus")
                            logger.warning(
                                "   → Check wiring and dtoverlay=w1-gpio,gpiopin=N in /boot/firmware/config.txt"
                            )
                        logger.warning(
                            "   → Update [sensors] %s_temp_sensor in config/sccs.conf with the correct ID",
                            role if role != "temp" else "outside",
                        )
                        self._last_missing_warn[role] = now
                    return None
                device_folders = [device_folder]
            else:
                device_folders = self._devices_on_bus(bus_gpio)
                if not device_folders:
                    logger.warning(
                        "No 1-Wire DS18B20 sensor found%s",
                        f" on GPIO{bus_gpio}" if bus_gpio is not None else "",
                    )
                    return None

            device_file = device_folders[0] + '/w1_slave'
            sensor_name = device_folders[0].split('/')[-1]
            logger.debug("   🌡️ Reading sensor: %s", sensor_name)

            # Read twice for reliability
            for i in range(2):
                with open(device_file, 'r') as f:
                    lines = f.readlines()

                if len(lines) < 2:
                    time.sleep(0.2)
                    continue

                if "YES" not in lines[0]:
                    logger.warning("   🌡️ CRC check failed, retrying...")
                    time.sleep(0.25)
                    continue

                equals_pos = lines[1].find('t=')
                if equals_pos != -1:
                    temp_string = lines[1][equals_pos + 2:].strip()
                    temp_c = float(temp_string) / 1000.0

                    if temp_c == 85.0:
                        logger.info("   🌡️ Sensor returned power-on reset value (85°C) — invalid")
                        time.sleep(0.3)
                        continue
                    if abs(temp_c) < 0.1:
                        logger.warning("   🌡️ Sensor returned near-zero — possibly bad read")
                        time.sleep(0.3)
                        continue

                    logger.debug("   🌡️ Temperature = %.1f°C [%s]", temp_c, sensor_name)
                    return round(temp_c, 1)

            logger.error("   ⚠️ DS18B20 read failed after retries [%s]", sensor_name)
            return None

        except Exception as e:
            logger.error("DS18B20 read error: %s", e)
            return None

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

        logger.debug("✅ SensorManager started")

        time.sleep(0.5)
        self.update_sensors()

    def _read_analog(self, pin, esp: int = 1):
        for attempt in range(3):
            resp = self.send_command(f"ANALOG {pin}", expect="ANALOG", esp=esp)
            if resp and resp.startswith("ANALOG"):
                try:
                    value = float(resp.split()[2])
                    logger.debug("   ADC ESP%d GPIO%d = %.1f", esp, pin, value)
                    return value
                except Exception:
                    pass
            time.sleep(0.05)
        now = time.time()
        if now - self._last_analog_warn > 60:
            logger.warning("   ⚠️ Failed to read ANALOG ESP%d GPIO%d", esp, pin)
            self._last_analog_warn = now
        return None

    def _read_vcc(self, esp: int = 1):
        for attempt in range(3):
            resp = self.send_command("GETVCC", expect="VCC", esp=esp)
            if resp and resp.startswith("VCC"):
                try:
                    v = float(resp.split()[1]) / 1000.0
                    logger.debug("   VCC ESP%d = %.3fV", esp, v)
                    return v
                except Exception:
                    pass
            time.sleep(0.05)
        now = time.time()
        if now - self._last_vcc_warn > 60:
            logger.warning("   ⚠️ Failed to read VCC (ESP%d)", esp)
            self._last_vcc_warn = now
        return None

    def _calculate_level_percent(self, adc, vcc, r_empty, r_full, r_pullup=None, adc_max=None):
        """Fuel-sender style divider: 3V3 -- R_pullup -- sense -- R_sensor -- GND.

        SCCS: R_pullup = 150 Ω to 3V3 on each analog input.
        Open / disconnected (no sensor) → sense ≈ 3V3 → 0%.
        Empty ≈ r_empty (high R, default 240 Ω), full ≈ r_full (low R, default 33 Ω).

        ``adc`` is raw counts; ``adc_max`` is full-scale (4095 for ESP32-S3 12-bit,
        1023 for 10-bit). ``vcc`` is the pull-up rail voltage in volts (≈ 3.3).
        """
        if adc is None or vcc is None:
            return None
        r_pullup = float(r_pullup if r_pullup is not None else self.WATER_R_PULLUP)
        adc_fs = float(adc_max if adc_max is not None else self.WATER_ADC_MAX)
        if r_pullup <= 0 or r_empty <= r_full or adc_fs <= 0 or vcc <= 0:
            return None
        v_sense = (float(adc) / adc_fs) * float(vcc)
        # Near-rail: open circuit or sensor unplugged
        if abs(vcc - v_sense) < 0.02 or v_sense >= vcc:
            return 0
        sensor_r = r_pullup * v_sense / (vcc - v_sense)
        # At or above empty resistance (incl. open-ish high R) → empty
        if sensor_r >= r_empty:
            return 0
        # Below full resistance → treat as full
        if sensor_r <= r_full:
            return 100
        pct = (r_empty - sensor_r) / (r_empty - r_full) * 100
        return round(max(0, min(100, pct)))

    def update_sensors(self):
        logger.debug("🔄 Updating sensors (water + temperature)...")

        adc_water = self._read_analog(self.WATER_PIN, esp=self.WATER_ESP)
        vcc = self._read_vcc(esp=self.WATER_ESP)
        outside_temp = self._read_ds18b20(
            self.OUTSIDE_TEMP_ID,
            bus_gpio=self.OUTSIDE_BUS_GPIO,
            role="outside",
        )
        fridge_temp = (
            self._read_ds18b20(
                self.FRIDGE_TEMP_ID,
                bus_gpio=self.FRIDGE_BUS_GPIO,
                role="fridge",
            )
            if self.FRIDGE_TEMP_ID
            else None
        )
        freezer_temp = (
            self._read_ds18b20(
                self.FREEZER_TEMP_ID,
                bus_gpio=self.FREEZER_BUS_GPIO,
                role="freezer",
            )
            if self.FREEZER_TEMP_ID
            else None
        )

        water_pct = None
        if adc_water is not None and vcc is not None:
            water_pct = self._calculate_level_percent(
                adc_water,
                vcc,
                self.WATER_R_EMPTY,
                self.WATER_R_FULL,
                self.WATER_R_PULLUP,
                self.WATER_ADC_MAX,
            )
            logger.debug(
                "   💧 Water ESP%d GPIO%d adc=%.1f/%.0f vcc=%.3f → %s%% "
                "(pullup=%.0fΩ empty=%.0f full=%.0f)",
                self.WATER_ESP,
                self.WATER_PIN,
                adc_water,
                self.WATER_ADC_MAX,
                vcc,
                water_pct,
                self.WATER_R_PULLUP,
                self.WATER_R_EMPTY,
                self.WATER_R_FULL,
            )
        if water_pct is None:
            water_pct = self.last_reading.get("water_percent")
            if adc_water is None and water_pct is not None:
                logger.debug("   💧 Water ADC read failed — holding last level %.0f%%", water_pct)
            elif adc_water is not None and vcc is None and water_pct is not None:
                logger.debug("   💧 VCC read failed — holding last level %.0f%%", water_pct)

        water_capacity = self.WATER_CAPACITY_LITRES if self.WATER_CAPACITY_LITRES > 0 else None

        sensor_data = {
            "water_percent": water_pct,
            "water_capacity_litres": water_capacity,
            "temp_c": outside_temp if outside_temp is not None else None,
            "outside_temp_c": outside_temp,
            "fridge_temp_c": fridge_temp,
            "freezer_temp_c": freezer_temp,
            # UI hides fridge/freezer tiles when IDs are blank in [sensors] (optional).
            "fridge_configured": bool(self.FRIDGE_TEMP_ID),
            "freezer_configured": bool(self.FREEZER_TEMP_ID),
            "temp_valid": outside_temp is not None,
        }

        self.last_reading = sensor_data
        logger.debug("📤 Emitting sensor data: %s", sensor_data)
        self.socketio.emit('sensor_update', sensor_data)
        if self.on_update:
            try:
                self.on_update(sensor_data)
            except Exception as e:
                logger.debug("sensor on_update failed: %s", e)

    def _loop(self):
        while self.running:
            try:
                self.update_sensors()
            except Exception as e:
                logger.error("❌ Sensor loop error: %s", e)
            time.sleep(self.config.getfloat('sensors', 'update_interval', fallback=5.0))

    def stop(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)
