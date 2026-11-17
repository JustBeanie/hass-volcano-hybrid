"""The blueprints in ``blueprints/``, run for real against the fake vaporizer.

Each one is set up through Home Assistant's own blueprint machinery, so a
blueprint Home Assistant would refuse to import fails here too.
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from datetime import timedelta
from pathlib import Path
import shutil

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util, yaml as yaml_util
import pytest
from pytest_homeassistant.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.storzandbickel_ble.const import (
    DOMAIN,
    EVENT_DEVICE,
    TRIGGER_TARGET_REACHED,
    TRIGGER_TYPES,
)
from custom_components.storzandbickel_ble.volcano import (
    CHAR_CURRENT_TEMP,
    CHAR_FAN_OFF,
    CHAR_FAN_ON,
    CHAR_HEAT_OFF,
    CHAR_HEAT_ON,
    CHAR_STATUS_REGISTER,
    CHAR_TARGET_TEMP,
    MASK_HEATER,
    MASK_TARGET_REACHED,
)

from .conftest import FORMATTED_MAC, FakeBleakClient

BLUEPRINTS = Path(__file__).parent.parent / "blueprints"
SCRIPTS = sorted((BLUEPRINTS / "script" / DOMAIN).glob("*.yaml"))
AUTOMATIONS = sorted((BLUEPRINTS / "automation" / DOMAIN).glob("*.yaml"))
CLIMATE = "climate.s_b_volcano_h"


@pytest.fixture(autouse=True)
def installed_blueprints(hass: HomeAssistant) -> Generator[None]:
    """Put the blueprints where an import would put them."""
    target = Path(hass.config.path("blueprints"))
    shutil.copytree(BLUEPRINTS, target, dirs_exist_ok=True)
    yield
    shutil.rmtree(target, ignore_errors=True)


async def _script(hass: HomeAssistant, name: str, **inputs: object) -> str:
    """Create a script from one of the blueprints and return its entity ID."""
    assert await async_setup_component(
        hass,
        "script",
        {
            "script": {
                name: {
                    "use_blueprint": {
                        "path": f"{DOMAIN}/{name}.yaml",
                        "input": {"volcano": CLIMATE, **inputs},
                    }
                }
            }
        },
    )
    await hass.async_block_till_done()
    return f"script.{name}"


async def _settle(hass: HomeAssistant) -> None:
    """Let a running script get as far as its next wait."""
    for _ in range(5):
        await hass.async_block_till_done()
        await asyncio.sleep(0)


async def _start(hass: HomeAssistant, entity_id: str) -> asyncio.Task[None]:
    """Run a script in the background and let it reach its first wait.

    Not through ``script.turn_on``: that hands the run to Home Assistant as a
    tracked task, and ``async_block_till_done`` would then wait for a script
    that is itself waiting for the vaporizer.
    """
    domain, name = entity_id.split(".")
    task: asyncio.Task[None] = hass.loop.create_task(
        hass.services.async_call(domain, name, blocking=True)
    )
    await _settle(hass)
    return task


async def _pass(hass: HomeAssistant, seconds: float) -> None:
    """Let ``seconds`` go by for the delays and timeouts in a script."""
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await _settle(hass)


def _written(client: FakeBleakClient) -> list[str]:
    """Return the characteristics written so far, in order."""
    return [uuid for uuid, _ in client.writes]


def test_there_are_blueprints_to_test() -> None:
    """Guard: a moved folder must not turn every test below into a no-op."""
    assert {path.stem for path in SCRIPTS} == {
        "bag_session",
        "fill_bag",
        "temperature_steps",
    }
    assert {path.stem for path in AUTOMATIONS} == {"when_ready"}


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda path: path.stem)
async def test_every_script_blueprint_imports(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, path: Path
) -> None:
    """Each becomes a script with nothing but the device chosen."""
    entity_id = await _script(hass, path.stem)

    assert hass.states.get(entity_id) is not None


@pytest.mark.parametrize("path", [*SCRIPTS, *AUTOMATIONS], ids=lambda path: path.stem)
def test_every_blueprint_points_at_this_integration(path: Path) -> None:
    """The device picker offers only our devices, and the link is its own."""
    blueprint = yaml_util.load_yaml(str(path))["blueprint"]
    first_input = next(iter(blueprint["input"].values()))
    (selector,) = first_input["selector"].values()

    assert selector["filter"][0]["integration"] == DOMAIN
    assert blueprint["source_url"].endswith(
        f"/blueprints/{path.parent.parent.name}/{DOMAIN}/{path.name}"
    )


async def test_fill_a_bag_heats_fills_and_turns_off(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fake_client: FakeBleakClient
) -> None:
    """Heat on, wait for the Volcano to say it is there, air on, air off, heat off."""
    entity_id = await _script(
        hass, "fill_bag", temperature=205, settle_seconds=0, fill_seconds=5
    )
    run = await _start(hass, entity_id)

    # Heating, target set, and waiting: no air yet.
    assert (CHAR_TARGET_TEMP, (2050).to_bytes(4, "little")) in fake_client.writes
    assert CHAR_HEAT_ON in _written(fake_client)
    assert CHAR_FAN_ON not in _written(fake_client)
    assert hass.states.get(entity_id).state == "on"

    # The Volcano reaches the target and says so.
    fake_client.reads[CHAR_CURRENT_TEMP] = (2050).to_bytes(4, "little")
    fake_client.reads[CHAR_STATUS_REGISTER] = (
        MASK_HEATER | MASK_TARGET_REACHED
    ).to_bytes(2, "little")
    await loaded_entry.runtime_data.async_refresh()
    await _settle(hass)
    assert CHAR_FAN_ON in _written(fake_client)
    assert CHAR_FAN_OFF not in _written(fake_client)

    await _pass(hass, 6)
    await run

    assert _written(fake_client)[-2:] == [CHAR_FAN_OFF, CHAR_HEAT_OFF]
    assert hass.states.get(entity_id).state == "off"


async def test_a_stale_reached_signal_does_not_start_the_air(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fake_client: FakeBleakClient
) -> None:
    """The device's signal can be left set from a lower target; cold is cold."""
    fake_client.reads[CHAR_STATUS_REGISTER] = MASK_TARGET_REACHED.to_bytes(2, "little")
    entity_id = await _script(hass, "fill_bag", temperature=205, settle_seconds=0)

    run = await _start(hass, entity_id)
    await loaded_entry.runtime_data.async_refresh()
    await _settle(hass)

    assert CHAR_HEAT_ON in _written(fake_client)
    assert CHAR_FAN_ON not in _written(fake_client)
    assert hass.states.get(entity_id).state == "on"

    await hass.services.async_call(
        "script", "turn_off", {"entity_id": entity_id}, blocking=True
    )
    await run


async def test_a_heat_up_that_times_out_turns_everything_off(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    fake_client: FakeBleakClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A Volcano that never gets there is not left heating, and no bag is filled."""
    entity_id = await _script(hass, "fill_bag", heat_up_minutes=1)
    run = await _start(hass, entity_id)
    assert CHAR_HEAT_ON in _written(fake_client)

    await _pass(hass, 61)
    await run

    # It ends in an error on purpose, so the trace shows what went wrong.
    assert "did not reach its temperature" in caplog.text

    assert CHAR_FAN_ON not in _written(fake_client)
    assert _written(fake_client)[-1] == CHAR_HEAT_OFF
    assert hass.states.get(entity_id).state == "off"


async def test_when_ready_runs_the_chosen_actions(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """The automation fires on the device's own "target reached" trigger."""
    assert TRIGGER_TARGET_REACHED in TRIGGER_TYPES
    (device,) = dr.async_get(hass).async_get_devices(
        identifiers={(DOMAIN, FORMATTED_MAC)}
    )
    calls = async_mock_service(hass, "test", "ready")
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "use_blueprint": {
                    "path": f"{DOMAIN}/when_ready.yaml",
                    "input": {
                        "device": device.id,
                        "actions": [
                            {
                                "action": "test.ready",
                                "data": {"at": "{{ trigger.event.data.temperature }}"},
                            }
                        ],
                    },
                }
            }
        },
    )
    await hass.async_block_till_done()

    hass.bus.async_fire(
        EVENT_DEVICE,
        {"device_id": device.id, "type": TRIGGER_TARGET_REACHED, "temperature": 190.0},
    )
    await hass.async_block_till_done()

    assert len(calls) == 1
    assert calls[0].data["at"] == 190.0
