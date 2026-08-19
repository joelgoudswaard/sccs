# modules/gpio.py
from gpiozero import OutputDevice, Button, Device
from gpiozero.pins.lgpio import LGPIOFactory
import logging

logger = logging.getLogger("sccs")


class GPIODeviceManager:
    def __init__(self, config):
        self.config = config
        self.devices = {}
        self.reeds = {}
        self.relays = {}
        self.reed_states = {}
        self.reed_to_light_map = {}
        self.relay_initial_states = {}

        self._setup_pin_factory()

    def _setup_pin_factory(self):
        try:
            if Device.pin_factory is None:
                Device.pin_factory = LGPIOFactory()
                logger.debug("🏭 LGPIOFactory initialized")
        except Exception as e:
            logger.error(f"Failed to set LGPIOFactory: {e}")

    def init_devices(self) -> None:
        logger.debug("🔧 Initializing GPIO relays and reeds...")

        # ====================== RELAYS (from [gpio]) ======================
        if self.config.has_section('gpio'):
            gpio_section = self.config.get_section('gpio')
            for name, line in gpio_section.items():
                if name.endswith(('_pin', '_pull_up', '_bounce_time')):
                    continue

                parts = [p.strip() for p in str(line).split('|')]
                if len(parts) < 2:
                    continue

                friendly = parts[0]
                try:
                    pin = int(parts[1])
                except ValueError:
                    continue

                active_high = len(parts) > 2 and parts[2].lower() == 'true'
                initial = len(parts) > 3 and parts[3].lower() == 'true'
                icon = parts[4] if len(parts) > 4 and parts[4].startswith('fa-') else "fa-lightbulb"

                try:
                    dev = OutputDevice(pin, active_high=active_high, initial_value=initial)
                    self.devices[name] = dev
                    self.relays[name] = dev
                    self.relay_initial_states[name] = initial

                    logger.debug(f"📟 Relay: {name} → {friendly} (GPIO {pin}, initial={'ON' if initial else 'OFF'})")
                except Exception as e:
                    logger.error(f"Failed to create relay {name}: {e}")

        # ====================== REEDS (from [reeds]) ======================
        if self.config.has_section('reeds'):
            reed_section = self.config.get_section('reeds')
            for name, line in reed_section.items():
                parts = [p.strip() for p in str(line).split('|')]
                if len(parts) < 2:
                    continue

                friendly = parts[0]
                try:
                    pin = int(parts[1])
                except ValueError:
                    continue

                # pull_up: true = Pi internal pull-up; false = external pull-up, active-low to GND.
                use_internal_pull = len(parts) <= 2 or parts[2].lower() not in (
                    "false",
                    "0",
                    "no",
                    "external",
                    "none",
                )
                bounce = float(parts[3]) if len(parts) > 3 else 0.05

                controls = [name]
                if len(parts) > 6:
                    last_field = parts[6].strip()
                    if last_field.startswith("controls:"):
                        light_list = last_field[9:].strip()
                        if light_list:
                            controls = [x.strip() for x in light_list.split(',') if x.strip()]
                    elif last_field:
                        controls = [last_field]

                # Register logical reed even if the GPIO claim fails (defaults closed).
                self.reed_to_light_map[name] = controls
                self.reed_states[name] = True

                try:
                    if use_internal_pull:
                        button = Button(pin, pull_up=True, bounce_time=bounce)
                        pull_note = "internal pull-up"
                    else:
                        button = Button(
                            pin, pull_up=None, active_state=False, bounce_time=bounce
                        )
                        pull_note = "external pull-up"
                    self.devices[name] = button
                    self.reeds[name] = button
                    self.reed_states[name] = button.is_pressed
                    logger.debug(
                        f"🚪 Reed: {name} → {friendly} controls {controls} "
                        f"(GPIO {pin}, {pull_note})"
                    )
                    try:
                        import subprocess
                        pinfo = subprocess.check_output(
                            ['pinctrl', 'get', str(pin)],
                            text=True, stderr=subprocess.DEVNULL, timeout=0.8
                        ).strip().splitlines()[0]
                        logger.info(f"🚪 Reed {name} (GPIO{pin}) pinctrl: {pinfo} | is_pressed={button.is_pressed}")
                    except Exception:
                        logger.info(f"🚪 Reed {name} (GPIO{pin}) is_pressed={button.is_pressed}")
                except Exception as e:
                    logger.error(
                        f"Failed to create reed {name}: {e} "
                        "(defaulting closed; hardware edges disabled)"
                    )

        configured_reeds = len(self.reed_states)
        hardware_reeds = len(self.reeds)
        logger.info(f"🏭 GPIO initialized → {len(self.relays)} relay(s), "
                    f"{hardware_reeds}/{configured_reeds} reed(s) hardware")
        if hardware_reeds < configured_reeds:
            logger.warning(
                "⚠️ Some reeds failed hardware init. "
                "They default to closed; force/UI control still works."
            )

    def get_device(self, name: str):
        return self.devices.get(name)

    def get_relay(self, name: str):
        return self.relays.get(name)

    def cleanup(self):
        for dev in list(self.devices.values()):
            try:
                dev.close()
            except Exception:
                pass
        self.devices.clear()
        self.relays.clear()
        self.reeds.clear()
        self.reed_states.clear()
        self.relay_initial_states.clear()
        self.reed_to_light_map.clear()
        try:
            factory = Device.pin_factory
            if factory is not None:
                factory.close()
                Device.pin_factory = None
                logger.debug("🏭 GPIO pin factory closed")
        except Exception as e:
            logger.debug(f"GPIO pin factory close: {e}")