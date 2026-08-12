# Contributing

Thanks for considering a contribution.

## Reporting bugs

- Check whether the bug is already reported in the issue tracker.
- Use the bug report template.
- Include debug logs. Add this to `configuration.yaml`, restart, reproduce, and
  attach the relevant lines:

  ```yaml
  logger:
    logs:
      custom_components.volcano_hybrid: debug
  ```

- For anything the device reports incorrectly, enable the **Raw register**
  diagnostic sensor on the device page and include its attributes. Those are the
  raw bytes off the wire and they are usually enough to identify a decoding
  problem without owning the hardware.

## Development setup

Home Assistant needs Python 3.13 or newer, and the test harness needs a C
compiler for one of its dependencies. On Windows, use WSL.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements_test.txt
```

## Checks

All three run in CI on every push and pull request:

```bash
python -m pytest tests/
```

```bash
python -m ruff check .
```

```bash
python -m ruff format --check .
```

`config_flow.py` must stay at 100% coverage — that is a Bronze quality-scale
requirement and CI enforces it.

## Coding guidelines

- Match the surrounding style; `ruff format` decides layout, so do not argue
  with it.
- Keep the BLE layer in `volcano.py` free of Home Assistant imports. It takes a
  `BLEDevice` and returns a `VolcanoState`, which is what makes it testable
  without a running Home Assistant.
- **All GATT traffic goes through the single lock in `volcano.py`, acquired at
  exactly one level.** The public coroutines take it; the private `_read` and
  `_write` helpers assume it is already held. `asyncio.Lock` is not reentrant,
  and re-entering it is what deadlocked the pre-2.0 implementation.
- Background work goes through `ConfigEntry.async_create_background_task` so it
  is cancelled on unload. A bare `asyncio.create_task` can be garbage collected
  mid-flight and will happily keep driving the device after the integration is
  gone.
- Entity names, action descriptions and error messages live in `strings.json`;
  regenerate `translations/en.json` from it rather than editing both.
- Changing an entity's `unique_id` breaks people's automations. If it is
  genuinely necessary, bump the config entry version and migrate the registry in
  place in `async_migrate_entry`, the way the 1 → 2 migration does.

## Quality scale

The integration targets [Bronze](https://developers.home-assistant.io/docs/core/integration-quality-scale/).
`custom_components/volcano_hybrid/quality_scale.yaml` records the status of every
rule; keep it honest if you change something it covers.
