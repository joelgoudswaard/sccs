# Changelog

All notable changes to SCCS are documented in this file.

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
