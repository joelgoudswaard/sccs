# Changelog

All notable changes to SCCS are documented in this file.

## [Unreleased]

### Added
- Update (menu 2 / `--update`) checks UniFi OS Server against Ubiquiti's release feed and runs the official installer when a newer version is published. An install that is already current is left alone. The controller restarts during the upgrade; adopted access points and the Podman volumes stay. The container DNS fix is applied again afterwards.
- Touchscreen setup logs the panel account into the desktop at boot on Raspberry Pi OS and Armbian. Raspberry Pi OS uses desktop autologin. Armbian uses LightDM, SDDM, GDM, or greetd, whichever is installed. The account password is unchanged, and the login applies on the next boot.
- Touchscreen setup turns off idle display power saving on Raspberry Pi OS and Armbian. The panel no longer blanks, dims, or sleeps after a period with no input. Brightness from the reed and the time of day is unchanged. Armbian KDE Plasma is included. Menu 8 applies desktop login, the Chromium homepage, and idle-power off to every touchscreen already in the config.

### Fixed
- Kitchen bench night is 10% red. It was off.
- Dragging a lighting slider thumb no longer opens the browser right-click menu. A press on the thumb is a drag.
- Neumorphism cards use the original halfway highlight again. Rewriting that fade, and painting it with GPU compositing forced off, turned it into a few flat shades on the kitchen panel.
- Closing the kitchen panel reed powers the touchscreen off. Brightness 0% only dimmed it. Wake turns the panel back on at the phase brightness.
- After a reboot the van LAN address could sit on the iPhone USB tether instead of the touchscreen port. Pi-hole then had nothing to lease from, so the panel came up with no address. The LAN profile is now pinned to that port’s name and MAC, and a phone tether is never treated as the van LAN.
- Victron setup offers the enrolled SmartShunt and MPPT Instant Readout keys as suggestions. Press Enter to use that device's key, or type 1 or 2. Those keys are the defaults in a new install.
- Touchscreen setup saves the panel account password in ~/.sccs/screen_credentials (mode 600). Later setup and package updates reuse it. The password is not written into sccs.conf. SSH itself still uses the key.
- Setup reads GPIO26, the SCCS Core presence pull-up (header pin 37, R5 to 3.3V). When that pin is high it skips the “is this Pi on the SCCS Core?” question and asks to proceed.
- Date & Time sunrise and sunset icons are half the height of the label and time together, and stay centered on those two lines.
- The home Lighting tile lists dimmable lights that are on, then a horizontal line, then relays that are on.
- The first touchscreen scan after Pi-hole DHCP is installed asks for every panel to be restarted, then waits for confirmation before detection. Panels that were already on have no address from the new server until they boot again.
- Install questions for the SCCS Core, Victron, phone USB tethering, touchscreens, and HomeKit or Google Home default to yes.
- Voice setup asks whether to install Apple HomeKit / Siri or Google Home / Gemini, then which one: HomeKit, Google, or both. HomeKit installs on its own. Node.js and the Matter bridge install when Google Home is chosen.
- HomeKit and Google Home pairing copy now says to use your iPhone or Android instead of the van Wi‑Fi.
- Date & Time sunrise and sunset labels now start in line with the time, and the sun and moon icons span that label and the time.
- Configuring temperature sensors or touchscreens left the running service on the old `sccs.conf` until something else restarted it. Those steps now restart `sccs.service` when it is already running, after the config for that step is saved.
- First-run ESP32 flashing waited on `/dev/ttyAMA2` and `/dev/ttyAMA3` before the UART overlays were active, so the chips were not detected until the Pi was restarted. The installer now loads those overlays on the running system, and when the kernel only creates the ports at startup it restarts and continues the install instead of asking for the BOOT button.
- A mismatched Samba password confirmation ended the share setup. The installer asks again until the two entries match, or both are left blank to skip.
- Victron setup saved a MAC and Instant Readout key without checking them. The installer now listens for each device, prints a live reading when the key decrypts, and offers to retype the credentials when the key does not match or the device is not heard.
- Touchscreen scan listed every LAN client. It now keeps hosts whose SSH banner is Debian, Ubuntu, Raspbian, or Armbian. A new panel's suggested internal name is `kitchen` and its friendly name is `Kitchen Touchscreen`.
- Panel SSH setup could reject a correct password: the setup script was passed on sudo's stdin, so ssh read that text instead of the password, and the password prompt itself was not taken from the terminal. The script is now a file, ssh is detached from the terminal, and the password is read from the keyboard.
- Temperature-sensor assignment ran before the 1-Wire bus existed, so a first install skipped it without asking. The bus is brought up first, the step asks to scan again when nothing is present, and it runs after the boot overlays are in place.
- Default lighting map: kitchen bench is bug-mode on 1-7 white, 1-8 red, 1-9 green. Accent is 1-10, rear drawer 1-11, storage panel 2-4, rooftop tent 2-5. Relays are Floodlights (GPIO22), Water (GPIO10), Lights (GPIO11), and Fridge/Oven (GPIO6).
- The date tile moon started on the right when Evening began. Evening starts before sunset, and the moon was still on the daytime arc until sunset, then jumped to the left. It now starts on the left at evening and walks toward midnight at the crest.
- Date & Time footer shows Sunrise and Sunset above those times.
- Startup turns the Lights relay on when any dimmer is already on, or when startup is about to turn one on. The relay GPIOs come up off, so those lights would otherwise have no power.
- While running, a change from every dimmer off to any light on also turns the Lights relay on. `lighting_relay_on_with_lights` in `[lighting]` controls this and the startup behavior. Default is on.
- Dragging a dimmer to a new level could leave the lights there while the slider jumped back. The UI treats the reply as an animation and was applying that animation on top of the level just set; a drag now stays put until a phase, reed, or scene change moves it.
- On a 1280×800 touchscreen the home lighting list grew the page. Lighting is two columns wide and lists its lights in two columns; System is one column, without the host-status column, so the three rows stay on the screen.
- In the two-column lighting layout the last two relays (Lights and Fridge/Oven) were left as separate tiles, so Fridge/Oven wrapped onto its own row. They stack in one tile, beside Floodlights and Water.
- The home Network tile named the Wi-Fi network whenever it was associated, even when the default route was the USB hotspot. It now names the path that is actually carrying traffic.
- Victron setup listens for Instant Readout and fills in the Bluetooth address for a single SmartShunt and a single MPPT. The 32-character key still has to be pasted from VictronConnect; it is not in the broadcast.
- A new install prefers Wi-Fi for internet. Phone USB tethering is still set up when asked, as the fallback route.
- On the two- and three-column System page, SCCS Core no longer draws platform names over their values: memory and platform each use the full tile width, and long values stay in their own column. Night’s start time lines up with the Day and Evening fields.

## [1.1.2.18082026] - 2026-08-18

### Added
- Installer 1-Wire setup can delete a temperature sensor role from `sccs.conf`; skip still leaves the existing value

## [1.1.1.18082026] - 2026-08-18

### Fixed
- Startup treated every reed as closed until the poller started, so the first lighting pass and a connecting UI could show “all panels closed”. Reeds are now sampled (three quick GPIO reads) before HTTP is advertised and before any lights or scenes run
- No-fix GPS sentences were stored as 0°N 0°E. Weather then fetched the equator (~24 °C). Empty GGA/RMC is ignored; with no valid fix the Alexandra fallback is used
- pynmea2 raised on void RMC datetime (`$GNRMC,,V,…`); those sentences no longer crash the GPS reader
- Settings tiles with `[hidden]` still painted in WebKit because author `display:flex` beat the UA rule

### Changed
- `[reeds]` comments map i1 pin pairs to functions; kitchen bench is GPIO23 and kitchen panel GPIO24
- Night bathroom scene renamed from `ensuite` to `bathroom`

## [1.1.17082026] - 2026-08-17

### Fixed
- ESP32 lighting stayed offline after a successful flash: the host protocol now uses UART0 (`Serial0` on GPIO 44/43), the same pins as the ROM bootloader, instead of a second `HardwareSerial(0)` on UART1
- Installer flashed over `/dev/ttyACM*` instead of the SCCS Core host UARTs; uploads now target `/dev/ttyAMA2` (ESP32-1) and `/dev/ttyAMA3` (ESP32-2) as the same user that compiled
- A failed `GETVCC` check after upload sent the installer back through the ESP32-1 flash loop. Handshake retries no longer re-flash; the next module is offered after a warning
- `USBMode=default` selected TinyUSB OTG (not the board default) and could hang `setup()` on modules with no USB. The FQBN stays `esp32:esp32:esp32s3`
- Pi UART has no RTS reset line, so esptool cannot start the app. The installer now asks for a RESET tap and checks `GETVCC` before moving on
- `sccs.service` is stopped for the upload and started again when the ESP step ends (including skip, abort, or Ctrl-C)
- Host `GETVCC` probe retries briefly after open so lighting can come online while firmware is still leaving reset

### Changed
- Installer enables the correct UART overlays per board: `uart2`/`uart3`/`uart4` on Pi 4, `uart1-pi5`/`uart2-pi5`/`uart3-pi5` on Pi 5 (GPS `/dev/ttyAMA1`, ESP1 `/dev/ttyAMA2`, ESP2 `/dev/ttyAMA3`)
- Firmware answers the host on a non-blocking line reader and prints `SCCS n READY` as soon as UART0 is up

## [1.0.0.13082026] - 2026-08-13

### Added
- Initial release of the Singularity Camper Control System (SCCS)
- Touchscreen UI for dimmable lighting, scenes, reeds, phases, water, climate, power, GPS, weather and networking
- Optional Apple HomeKit and Google Home control (off by default; installer menu 10 or Settings)
- Installer for imaging, Victron, LAN/Pi-hole, USB tethering and touchscreens
