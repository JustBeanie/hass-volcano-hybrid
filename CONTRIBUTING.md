# Contributing

Thanks for considering a contribution. The [roadmap](ROADMAP.md) lists the
planned work, and each item there is an issue you can pick up.

## Reporting bugs

- Check whether the bug is already reported in the issue tracker.
- Use the bug report template.
- Include debug logs. Add this to `configuration.yaml`, restart, reproduce, and
  attach the relevant lines:

  ```yaml
  logger:
    logs:
      custom_components.storzandbickel_ble: debug
  ```

- For anything the device reports incorrectly, enable the **Status register 1**
  diagnostic sensor on the device page and include its attributes. Those are the
  raw bytes off the wire and they are usually enough to identify a decoding
  problem without owning the hardware.

## Development setup

Home Assistant needs Python 3.14.2 or newer, and the test harness needs a C
compiler for one of its dependencies. On Windows, use WSL.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements_test.txt
```

## Checks

All four run in CI on every push and pull request:

```bash
python -m pytest tests/
```

```bash
python -m ruff check .
```

```bash
python -m ruff format --check .
```

```bash
python -m mypy
```

The dashboard card has its own tests, which need Node 22 or newer and nothing
else:

```bash
node --test "tests/frontend/*.test.mjs"
```

CI holds every pull request to these, and so should you before pushing:

- **100% line and branch coverage** of the whole integration
  (`--cov-branch --cov-fail-under=100`). A branch nobody tests is a behavior
  nobody has checked.
- **No warnings** from the integration's own code, and no coroutine left
  un-awaited, in the test run. Both are errors (`filterwarnings` in
  `pyproject.toml`), because a mock that is never awaited makes a test pass
  for the wrong reason.
- **mypy at Home Assistant's strict settings.** Use the typed accessors on the
  coordinator (`device_as(...)`, `portable`) for a family's own commands
  rather than reaching for `Any`.
- **Every user-facing string translated** and present in both `strings.json`
  and `translations/en.json`; `tests/test_translations.py` checks that.

The code is also kept clean under Home Assistant core's own ruff rules, apart
from the ones that only make sense inside core: dropping
`from __future__ import annotations` (core requires Python 3.14) and core's
test-layout rules.

## Minimum Home Assistant version

The integration supports Home Assistant **2026.11** and newer.

**It goes up only when something needs it.** That means a replacement for a
deprecated API, a fix or a feature this integration needs that older releases
don't have. It never goes up just because a new release is out. When it does
go up, it goes to the oldest release that has what's needed, not the newest.

**Raising it is a minor release** (4.1.0 is the model). HACS reads
`hacs.json` and doesn't offer the update to anyone on an older Home Assistant,
so their install keeps working on the release they have. A major release is
for changes that break entities, entity IDs or configuration.

**The main test job runs against the minimum.** `requirements_test.txt` pins
`pytest-homeassistant` to exactly the minimum (`==2026.11.0`), because that
package's version is the Home Assistant version tested. The second Pytest job
installs the newest release, betas included, so the code is checked at both
ends of the supported range.

When the minimum moves, change all of these in the same pull request:

- `hacs.json`: `homeassistant`
- `README.md`: *Requirements*
- `requirements_test.txt`: the `pytest-homeassistant` pin
- `min_version` in every blueprint under `blueprints/`
- the version named at the top of this section

The CHANGELOG entry opens the release's `### Changed` list with the minimum
and the reason, then notes the new test version under `### Development`:

```markdown
### Changed

- **Home Assistant 2026.11 or newer is now required.** Everything below uses
  APIs that older versions do not have.

### Development

- Tests run against Home Assistant 2026.11.
```

## Coding guidelines

- Match the surrounding style; `ruff format` decides layout, so do not argue
  with it.
- Keep the BLE layer — `ble_device.py` and the family modules `volcano.py`,
  `crafty.py` and `qvap.py` — free of Home Assistant imports. It takes a
  `BLEDevice` and returns the family's state dataclass, which is what makes
  it testable without a running Home Assistant.
- **All GATT traffic goes through the single lock in `ble_device.py`, acquired at
  exactly one level.** The public coroutines take it; the private `_read` and
  `_write` helpers assume it is already held. `asyncio.Lock` is not reentrant,
  and re-entering it is what deadlocked the pre-2.0 implementation.
- Background work goes through `ConfigEntry.async_create_task`, so the entry
  owns it and waits for it on unload. A bare `asyncio.create_task` can be
  garbage collected mid-flight and will happily keep driving the device after
  the integration is gone.
- Anything that has to be undone when the entry goes away is registered with
  `entry.async_on_unload` as soon as it exists. Home Assistant runs those when
  setup fails part-way as well as on unload, which is what stops a failed setup
  from leaving the Bluetooth link open.
- Entity names, action descriptions and error messages live in `strings.json`;
  regenerate `translations/en.json` from it rather than editing both.
- Changing an entity's `unique_id` breaks people's automations. If it is
  genuinely necessary, bump the config entry version and migrate the registry in
  place in `async_migrate_entry`.

## The dashboard card

- The card is plain JavaScript modules in
  `custom_components/storzandbickel_ble/frontend/`, with no build step and no
  dependencies. Keep it that way: what ships is what is in the repository.
- Decisions go in `card-logic.js`, which has no DOM in it and is what the
  tests cover. `storzandbickel-card.js` only turns its results into elements.
- Draw with Home Assistant's own building blocks: `ha-card`, theme color
  variables, and the control height, radius and spacing the built-in card
  features use. No color or size of its own, so it matches every theme.
- Find entities by `translation_key` and device, never by entity ID.

## Quality scale

The integration targets [Platinum](https://developers.home-assistant.io/docs/core/integration-quality-scale/).
`custom_components/storzandbickel_ble/quality_scale.yaml` records the status of every
rule; keep it honest if you change something it covers.
