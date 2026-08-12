"""Fixtures for the Volcano Hybrid tests."""

from __future__ import annotations

from collections.abc import Callable, Generator
from unittest.mock import AsyncMock, MagicMock, patch

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak.exc import BleakError
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.const import CONF_ADDRESS
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.volcano_hybrid.const import (
    CONF_FAN_ON_CONNECT,
    CONF_INITIAL_TEMP,
    CONF_MAC_ADDRESS,
    DOMAIN,
)
from custom_components.volcano_hybrid.volcano import (
    CHAR_AUTO_OFF_SETTING,
    CHAR_BLE_FIRMWARE,
    CHAR_BRIGHTNESS,
    CHAR_CURRENT_TEMP,
    CHAR_FAN_OFF,
    CHAR_FAN_ON,
    CHAR_FIRMWARE,
    CHAR_HEAT_OFF,
    CHAR_HEAT_ON,
    CHAR_HOURS_OF_OPERATION,
    CHAR_MINUTES_OF_OPERATION,
    CHAR_REGISTER2,
    CHAR_REGISTER3,
    CHAR_SERIAL_NUMBER,
    CHAR_STATUS_REGISTER,
    CHAR_TARGET_TEMP,
    MASK_FAN,
    MASK_HEATER,
)

ADDRESS = "AA:BB:CC:DD:EE:FF"
FORMATTED_MAC = "aa:bb:cc:dd:ee:ff"
DEVICE_NAME = "S&B VOLCANO H"
STORZ_BICKEL_MANUFACTURER_ID = 1736

# Wire values captured from a real Volcano Hybrid (firmware V01.03.00.00).
RAW_CURRENT_TEMP_IDLE = bytes.fromhex("4cffffff")  # -18.0 C sentinel
RAW_CURRENT_TEMP_HOT = bytes.fromhex("02080000")  # 205.0 C
RAW_TARGET_TEMP = bytes.fromhex("02080000")  # 205.0 C
RAW_STATUS_IDLE = bytes.fromhex("00000000")
RAW_STATUS_HEATING = bytes.fromhex("23000000")  # heater bit set
RAW_STATUS_FAN_AND_HEAT = bytes.fromhex("2328")  # fan + heater, high byte 0x28


def make_ble_device(address: str = ADDRESS, name: str = DEVICE_NAME) -> BLEDevice:
    """Build a BLEDevice for tests."""
    return BLEDevice(address, name, {})


def make_service_info(
    address: str = ADDRESS,
    name: str = DEVICE_NAME,
    connectable: bool = True,
) -> BluetoothServiceInfoBleak:
    """Build a BluetoothServiceInfoBleak for tests."""
    manufacturer_data = {STORZ_BICKEL_MANUFACTURER_ID: b"\x01"}
    return BluetoothServiceInfoBleak(
        name=name,
        address=address,
        rssi=-60,
        manufacturer_data=manufacturer_data,
        service_data={},
        service_uuids=[],
        source="local",
        device=make_ble_device(address, name),
        advertisement=AdvertisementData(
            local_name=name,
            manufacturer_data=manufacturer_data,
            service_data={},
            service_uuids=[],
            tx_power=-127,
            rssi=-60,
            platform_data=(),
        ),
        connectable=connectable,
        time=0.0,
        tx_power=-127,
    )


SERVICE_INFO = make_service_info()
NOT_VOLCANO_SERVICE_INFO = make_service_info(
    address="11:22:33:44:55:66", name="Some Other Thing"
)


DEFAULT_READS: dict[str, bytes] = {
    CHAR_CURRENT_TEMP: RAW_CURRENT_TEMP_IDLE,
    CHAR_TARGET_TEMP: RAW_TARGET_TEMP,
    CHAR_STATUS_REGISTER: RAW_STATUS_IDLE,
    CHAR_BRIGHTNESS: bytes.fromhex("4600"),  # 70 %
    CHAR_SERIAL_NUMBER: b"VH38NHG700",
    CHAR_BLE_FIRMWARE: b"V01.00.00.00",
    CHAR_FIRMWARE: b"V01.03.00.00",
    CHAR_HOURS_OF_OPERATION: (2721).to_bytes(2, "little"),
    CHAR_MINUTES_OF_OPERATION: (14).to_bytes(2, "little"),
    CHAR_AUTO_OFF_SETTING: (1200).to_bytes(2, "little"),  # 20 minutes
    CHAR_REGISTER3: b"\x01",
    CHAR_REGISTER2: b"\x01",
}


class FakeBleakClient:
    """A stand-in for a connected BleakClient."""

    def __init__(self, reads: dict[str, bytes] | None = None) -> None:
        """Initialise with a characteristic map."""
        self.reads = dict(DEFAULT_READS if reads is None else reads)
        self.writes: list[tuple[str, bytes]] = []
        self.is_connected = True
        self.notify_callbacks: dict[str, object] = {}

    async def read_gatt_char(self, uuid: str) -> bytes:
        """Return the canned value, or fail the way bleak does."""
        if uuid not in self.reads:
            raise BleakError(f"Characteristic {uuid} was not found")
        return self.reads[uuid]

    async def write_gatt_char(
        self, uuid: str, data: bytes, response: bool = True
    ) -> None:
        """Record a write and reflect it back, the way the device would."""
        self.writes.append((uuid, bytes(data)))

        if uuid in (CHAR_TARGET_TEMP, CHAR_BRIGHTNESS, CHAR_AUTO_OFF_SETTING):
            self.reads[uuid] = bytes(data)
            return

        status = int.from_bytes(self.reads[CHAR_STATUS_REGISTER][:2], "little")
        if uuid == CHAR_HEAT_ON:
            status |= MASK_HEATER
        elif uuid == CHAR_HEAT_OFF:
            status &= ~MASK_HEATER
        elif uuid == CHAR_FAN_ON:
            status |= MASK_FAN
        elif uuid == CHAR_FAN_OFF:
            status &= ~MASK_FAN
        else:
            self.reads[uuid] = bytes(data)
            return
        self.reads[CHAR_STATUS_REGISTER] = status.to_bytes(2, "little")

    async def start_notify(self, uuid: str, callback: object) -> None:
        """Record a notification subscription."""
        self.notify_callbacks[uuid] = callback

    async def stop_notify(self, uuid: str) -> None:
        """Drop a notification subscription."""
        self.notify_callbacks.pop(uuid, None)

    async def disconnect(self) -> None:
        """Mark the client as disconnected."""
        self.is_connected = False


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading of the custom integration in every test."""
    return


@pytest.fixture
def fake_client() -> FakeBleakClient:
    """Return the fake BLE client used by the connection mock."""
    return FakeBleakClient()


@pytest.fixture
def mock_establish_connection(
    fake_client: FakeBleakClient,
) -> Generator[AsyncMock]:
    """Patch bleak_retry_connector so no real BLE traffic happens."""
    with patch(
        "custom_components.volcano_hybrid.volcano.establish_connection",
        AsyncMock(return_value=fake_client),
    ) as mock:
        yield mock


@pytest.fixture
def bluetooth_callbacks() -> list[Callable[..., None]]:
    """Collect the callbacks the integration registers with the manager."""
    return []


@pytest.fixture
def mock_bluetooth(
    enable_bluetooth: None,
    mock_establish_connection: AsyncMock,
    bluetooth_callbacks: list[Callable[..., None]],
) -> Generator[MagicMock]:
    """Make the device visible to every module that looks it up."""
    ble_device = make_ble_device()

    def _register_callback(
        _hass: object, callback: Callable[..., None], _matcher: object, _mode: object
    ) -> Callable[[], None]:
        """Record the callback so tests can invoke it the way the manager does."""
        bluetooth_callbacks.append(callback)
        return lambda: None

    with (
        patch(
            "custom_components.volcano_hybrid.async_ble_device_from_address",
            return_value=ble_device,
        ),
        patch(
            "custom_components.volcano_hybrid.config_flow.async_ble_device_from_address",
            return_value=ble_device,
        ),
        patch(
            "custom_components.volcano_hybrid.coordinator."
            "async_ble_device_from_address",
            return_value=ble_device,
        ),
        patch(
            "custom_components.volcano_hybrid.async_register_callback",
            _register_callback,
        ),
        patch(
            "custom_components.volcano_hybrid.config_flow.async_discovered_service_info",
            return_value=[SERVICE_INFO],
        ) as discovered,
    ):
        yield discovered


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return a current-version config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=DEVICE_NAME,
        version=3,
        unique_id=FORMATTED_MAC,
        data={CONF_ADDRESS: ADDRESS},
        options={CONF_FAN_ON_CONNECT: False},
    )


@pytest.fixture
def v2_config_entry() -> MockConfigEntry:
    """Return a version 2 entry, with the behaviour settings still in data."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=DEVICE_NAME,
        version=2,
        unique_id=FORMATTED_MAC,
        data={
            CONF_ADDRESS: ADDRESS,
            CONF_FAN_ON_CONNECT: True,
            CONF_INITIAL_TEMP: 180,
        },
    )


@pytest.fixture
def legacy_config_entry() -> MockConfigEntry:
    """Return a version 1 config entry, as created before the rewrite."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=DEVICE_NAME,
        version=1,
        unique_id=ADDRESS,
        data={CONF_MAC_ADDRESS: ADDRESS, "name": DEVICE_NAME},
    )
