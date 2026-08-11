# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2026-08-10

Rebuilt on Home Assistant's Bluetooth stack and brought up to the
[Bronze quality scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/).

Existing installations migrate automatically. **All entity IDs are preserved**,
so automations, scripts and dashboards keep working — only the internal device
identifier and entity unique IDs change, and both are rewritten in place.

### Fixed

- **The reported temperature was wrong.** The temperature characteristic is a
  little-endian *signed* 32-bit value in tenths of a degree; it was being read as
  an unsigned 16-bit value. Every genuine reading failed the plausibility check
  and was discarded, logging
  `Received unreasonable temperature reading: 6536°C` on every poll.
- **The displayed temperature came from the status register, not the sensor.**
  A heuristic pulled the high byte out of the status word and accepted it as a
  temperature if it landed between 40 and 230. Because the fan flag is `0x2000`,
  running the fan put `0x28` (= 40) in that byte, so the integration reported a
  steady 40 °C that had nothing to do with the device. Removed.
- **Two deadlocks in the BLE layer.** `connect()` held the connection lock and
  then called helpers that took the same non-reentrant lock, so the initial
  temperature read could never succeed. The command path had the same shape with
  no timeout, permanently wedging every subsequent command. All GATT traffic now
  goes through a single lock acquired at exactly one level.
- **Reconnecting re-applied the setup options.** Any Bluetooth blip re-ran
  "start the fan" and rewrote the initial temperature. These now apply once per
  startup or reload.
- **The screen brightness was never read from the device**, so it reported a
  hardcoded 70 % regardless of reality.
- **The raw register sensor was permanently `unknown`** — its work sat in
  `async_update`, which a `CoordinatorEntity` never calls.
- **Auto-off parsing** no longer guesses between seconds and minutes, and no
  longer silently falls back to 30 minutes.
- **Light brightness** no longer loses a step converting between 0–255 and 0–100.
- **Setup no longer reports success when the device is unreachable**; the entry
  retries instead of loading entities that can never work.
- **Fan timers are cancelled on unload**, so a pending timer can no longer
  operate the device after the integration has been removed.
- **Fixed the documentation and issue tracker URLs**, which pointed at a
  repository that does not exist.

### Added

- **Automatic discovery now works.** The Bluetooth matcher was `VOLCANO*`, but
  the device advertises as `S&B VOLCANO H`, so it never matched and the
  integration always had to be added by hand. It now matches on the Storz &
  Bickel manufacturer ID and the correct name.
- **Support for ESPHome Bluetooth proxies.** Connections are established through
  Home Assistant's Bluetooth manager, so the vaporizer no longer has to be in
  range of the Home Assistant host, and reconnects follow whichever adapter or
  proxy last heard it.
- `binary_sensor.<name>_connection` with the connectivity device class.
- An `hvac_action` on the climate entity, so dashboards can tell heating from idle.
- A full test suite, including 100 % coverage of the config flow.
- Removal instructions and documented actions in the README.

### Changed

- Actions are registered in `async_setup` with validated schemas, so they exist
  even when no device is loaded and a missing `duration` can no longer reach
  `asyncio.sleep`.
- Runtime state moved to `ConfigEntry.runtime_data`.
- Device and entity metadata moved into a shared base class instead of being
  copy-pasted across every platform.
- Sensors gained proper units, device classes and state classes; hours of
  operation is now a `total_increasing` duration and can be graphed.
- The debug entities (raw register, connection status, heater/fan status text)
  are disabled by default on new installs. Existing ones stay enabled.
- The current temperature reports `unknown` while the heater is idle instead of
  the device's −18 °C placeholder.

### Removed

- The unregistered `set_temperature` action declaration and the orphaned
  `next_sesh` translation, neither of which existed in the code.
- `brand.json` and the bundled logos, which do nothing inside a custom component;
  they now live in `brands/` for submission to the Home Assistant brands
  repository.
- `utils/get_mac_address.py`, made obsolete by working discovery.
- The bundled `automation/`, `script/` and `custom_templates/` folders moved to
  `examples/`.

## [0.1.0] - 2023-09-01

### Added

- Initial release
