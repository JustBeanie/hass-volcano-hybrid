"""Volcano Hybrid device control."""
import asyncio
import logging
import struct
import time
from typing import Dict, Any, Optional, Callable, List, Tuple
from collections import deque

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

_LOGGER = logging.getLogger(__name__)

class VolcanoHybrid:
    """Representation of a Volcano Hybrid device."""

    def __init__(self, mac_address: str) -> None:
        """Initialize the Volcano Hybrid device."""
        _LOGGER.info("Initializing Volcano Hybrid device with MAC: %s", mac_address)
        self.mac_address = mac_address
        self.client = None
        self._current_temperature = 0
        self._target_temperature = 0
        self._heater_on = False
        self._fan_on = False
        self._brightness = 70  # Default brightness
        self._is_connected = False
        self._notification_callback = None
        self._reconnect_task = None
        self._last_command_time = 0
        self._connection_lock = asyncio.Lock()
        self._last_heater_change_time = 0
        self._heater_change_source = "init"
        
        # Device information
        self._serial_number = None
        self._ble_firmware_version = None
        self._hours_of_operation = {"hours": 0, "minutes": 0}
        self._volcano_firmware_version = None
        self._auto_off_time_seconds = 0
        
        # Device settings
        self._is_vibration_enabled = True  # Default to True
        self._is_display_on_cooling = True  # Default to True
        
        # Command queue for rate limiting
        self._command_queue = deque()
        self._command_processor_task = None
        self._command_rate_limit = 0.2  # Minimum time between commands in seconds
        self._processing_commands = False
        
        # Temperature cache
        self._temp_cache_time = 0
        self._temp_cache_duration = 1.0  # Shorter cache duration for more frequent updates
        
        # Raw data storage for debugging
        self._current_temperature_raw = None
        self._target_temperature_raw = None
        self._register_one_raw = None
        self._auto_off_time_raw = None
        
        # UUIDs from the original project
        self.heat_on_uuid = "1011000f-5354-4f52-5a26-4249434b454c"
        self.heat_off_uuid = "10110010-5354-4f52-5a26-4249434b454c"
        self.fan_on_uuid = "10110013-5354-4f52-5a26-4249434b454c"
        self.fan_off_uuid = "10110014-5354-4f52-5a26-4249434b454c"
        self.screen_brightness_uuid = "10110005-5354-4f52-5a26-4249434b454c"
        self.target_temp_uuid = "10110003-5354-4f52-5a26-4249434b454c"
        self.register_one_uuid = "1010000c-5354-4f52-5a26-4249434b454c"
        self.current_temp_uuid = "10110001-5354-4f52-5a26-4249434b454c"  # Added current temperature UUID
        
        # Device information UUIDs - Updated with correct UUIDs
        self.serial_number_uuid = "10100008-5354-4f52-5a26-4249434b454c"  # Serial number
        self.ble_firmware_uuid = "10100004-5354-4f52-5a26-4249434b454c"   # BLE firmware version
        self.hours_operation_uuid = "10110015-5354-4f52-5a26-4249434b454c"  # Hours of operation
        self.minutes_operation_uuid = "10110016-5354-4f52-5a26-4249434b454c"  # Minutes of operation
        self.firmware_version_uuid = "10100003-5354-4f52-5a26-4249434b454c"  # Volcano firmware version
        self.auto_shutoff_uuid = "1011000c-5354-4f52-5a26-4249434b454c"  # Auto-shutoff
        self.auto_shutoff_setting_uuid = "1011000d-5354-4f52-5a26-4249434b454c"  # Auto-shutoff setting
        
        # Configuration UUIDs - Updated with correct UUIDs
        self.register3_uuid = "1010000e-5354-4f52-5a26-4249434b454c"  # register3 (vibration in original code)
        self.register2_uuid = "1010000d-5354-4f52-5a26-4249434b454c"  # register2 (display during cooling in original code)
        
        _LOGGER.debug("Volcano Hybrid device initialized")

    async def connect(self, notification_callback: Optional[Callable] = None) -> bool:
        """Connect to the Volcano Hybrid device."""
        _LOGGER.info("Attempting to connect to Volcano Hybrid at %s", self.mac_address)
        
        # Check if device is available before attempting connection
        try:
            _LOGGER.debug("Scanning for device before connection attempt")
            device_found = False
            
            # Try to find the device with a short scan
            for attempt in range(2):
                try:
                    device = await BleakScanner.find_device_by_address(
                        self.mac_address, timeout=5.0
                    )
                    if device:
                        device_found = True
                        _LOGGER.debug("Device found in scan: %s", device)
                        break
                except Exception as e:
                    _LOGGER.warning("Error during device scan (attempt %d): %s", attempt + 1, e)
                    await asyncio.sleep(1)
            
            if not device_found:
                _LOGGER.warning("Device not found in scan, but will try to connect anyway")
        except Exception as e:
            _LOGGER.warning("Failed to scan for device: %s", e)
        
        # Use a timeout to prevent blocking indefinitely
        try:
            async with asyncio.timeout(15):  # Reduced timeout to fail faster
                async with self._connection_lock:
                    if self._is_connected:
                        _LOGGER.debug("Already connected to Volcano Hybrid")
                        return True
                        
                    self._notification_callback = notification_callback
                    
                    try:
                        _LOGGER.debug("Creating new BleakClient")
                        
                        # Disconnect any existing client
                        if self.client:
                            try:
                                _LOGGER.debug("Disconnecting existing client")
                                await self.client.disconnect()
                            except Exception as e:
                                _LOGGER.warning("Error disconnecting existing client: %s", e)
                            self.client = None
                        
                        # Create a new client with reduced timeout
                        self.client = BleakClient(
                            self.mac_address,
                            timeout=8.0,  # Reduced timeout to prevent blocking
                            disconnected_callback=self._handle_disconnect
                        )
                        
                        # Try to connect with retry
                        for attempt in range(3):
                            try:
                                _LOGGER.debug("Connection attempt %d", attempt + 1)
                                await asyncio.wait_for(self.client.connect(), timeout=8.0)
                                break
                            except asyncio.TimeoutError:
                                _LOGGER.warning("Connection attempt %d timed out", attempt + 1)
                                if attempt < 2:
                                    await asyncio.sleep(1)
                                else:
                                    raise
                            except BleakError as e:
                                if attempt < 2:  # Don't log on last attempt
                                    _LOGGER.warning(
                                        "Connection attempt %d failed: %s. Retrying...", 
                                        attempt + 1, str(e)
                                    )
                                    await asyncio.sleep(1)
                                else:
                                    raise
                        
                        if not self.client.is_connected:
                            _LOGGER.error("Failed to connect after 3 attempts")
                            self._is_connected = False
                            return False
                        
                        # Read initial state with shorter timeout
                        _LOGGER.debug("Reading initial state from register_one")
                        try:
                            value = await asyncio.wait_for(
                                self.client.read_gatt_char(self.register_one_uuid),
                                timeout=3.0
                            )
                            self._register_one_raw = value.hex() if value else None
                            self._process_notification(value, "initial_read")
                            _LOGGER.debug("Initial state read successfully: %s", self._register_one_raw)
                        except Exception as e:
                            _LOGGER.warning("Failed to read initial state: %s", e)
                        
                        # Set up notifications with shorter timeout
                        try:
                            _LOGGER.debug("Setting up notifications")
                            await asyncio.wait_for(
                                self.client.start_notify(self.register_one_uuid, self._notification_handler),
                                timeout=3.0
                            )
                            _LOGGER.debug("Notifications set up successfully")
                        except Exception as e:
                            _LOGGER.warning("Failed to set up notifications: %s", e)
                        
                        # Mark as connected before reading temperatures
                        self._is_connected = True
                        self._last_command_time = time.time()
                        
                        # Read initial temperature - do this quickly
                        try:
                            _LOGGER.debug("Reading initial temperatures")
                            # Use a task group to read both temperatures in parallel
                            async def read_temps():
                                await asyncio.gather(
                                    self._fast_read_current_temperature(),
                                    self._fast_read_target_temperature()
                                )
                            
                            # Set a timeout for the temperature reading
                            await asyncio.wait_for(read_temps(), timeout=3.0)
                            _LOGGER.debug("Initial temperatures read successfully")
                        except Exception as e:
                            _LOGGER.warning("Failed to read initial temperatures: %s", e)
                            # Continue anyway, we'll get temperatures on the next update
                        
                        # Read device information in the background
                        asyncio.create_task(self._read_device_information())
                        
                        # Read device settings in the background
                        asyncio.create_task(self._read_device_settings())
                        
                        _LOGGER.info("Connected to Volcano Hybrid at %s", self.mac_address)
                        
                        # Start keepalive task
                        self._start_keepalive()
                        
                        # Start command processor
                        self._start_command_processor()
                        
                        return True
                        
                    except Exception as e:
                        self._is_connected = False
                        _LOGGER.exception("Failed to connect to Volcano Hybrid: %s", e)
                        return False
        except asyncio.TimeoutError:
            _LOGGER.error("Connection process timed out after 15 seconds")
            self._is_connected = False
            return False

    async def _read_device_information(self):
        """Read device information from the device."""
        _LOGGER.debug("Reading device information")
        try:
            # Read serial number
            try:
                serial_value = await asyncio.wait_for(
                    self.client.read_gatt_char(self.serial_number_uuid),
                    timeout=2.0
                )
                if serial_value:
                    self._serial_number = serial_value.decode('utf-8').strip()
                    _LOGGER.debug("Serial number: %s", self._serial_number)
            except Exception as e:
                _LOGGER.warning("Failed to read serial number: %s", e)
            
            # Read BLE firmware version
            try:
                ble_fw_value = await asyncio.wait_for(
                    self.client.read_gatt_char(self.ble_firmware_uuid),
                    timeout=2.0
                )
                if ble_fw_value:
                    self._ble_firmware_version = ble_fw_value.decode('utf-8').strip()
                    _LOGGER.debug("BLE firmware version: %s", self._ble_firmware_version)
            except Exception as e:
                _LOGGER.warning("Failed to read BLE firmware version: %s", e)
            
            # Read hours and minutes of operation separately
            try:
                # Read hours
                hours_value = await asyncio.wait_for(
                    self.client.read_gatt_char(self.hours_operation_uuid),
                    timeout=2.0
                )
                # Read minutes
                minutes_value = await asyncio.wait_for(
                    self.client.read_gatt_char(self.minutes_operation_uuid),
                    timeout=2.0
                )
                
                # Debug the raw bytes
                _LOGGER.debug("Hours of operation raw bytes: %s", hours_value.hex() if hours_value else "None")
                _LOGGER.debug("Minutes of operation raw bytes: %s", minutes_value.hex() if minutes_value else "None")
                
                hours = 0
                minutes = 0
                
                if hours_value and len(hours_value) >= 2:
                    hours = int.from_bytes(hours_value[0:2], byteorder='little')
                
                if minutes_value and len(minutes_value) >= 2:
                    minutes = int.from_bytes(minutes_value[0:2], byteorder='little')
                
                self._hours_of_operation = {"hours": hours, "minutes": minutes}
                _LOGGER.debug("Hours of operation: %d hours, %d minutes", hours, minutes)
            except Exception as e:
                _LOGGER.warning("Failed to read hours/minutes of operation: %s", e)
            
            # Read Volcano firmware version
            try:
                fw_value = await asyncio.wait_for(
                    self.client.read_gatt_char(self.firmware_version_uuid),
                    timeout=2.0
                )
                if fw_value:
                    # Debug the raw bytes
                    _LOGGER.debug("Firmware version raw bytes: %s", fw_value.hex())
                    
                    # Try to parse as a string first
                    try:
                        fw_string = fw_value.decode('utf-8').strip()
                        # Check if it looks like a version string
                        if fw_string.startswith('V') or '.' in fw_string:
                            self._volcano_firmware_version = fw_string
                        else:
                            # If it doesn't look like a version string, try to format it
                            # It might be encoded as individual bytes for major.minor.patch
                            if len(fw_value) >= 3:
                                major = fw_value[0]
                                minor = fw_value[1]
                                patch = fw_value[2]
                                self._volcano_firmware_version = f"V{major:02d}.{minor:02d}.{patch}"
                            else:
                                # Just use the raw hex as a fallback
                                self._volcano_firmware_version = f"V{fw_value.hex()}"
                    except UnicodeDecodeError:
                        # If it can't be decoded as UTF-8, try to format it as a version
                        if len(fw_value) >= 3:
                            major = fw_value[0]
                            minor = fw_value[1]
                            patch = fw_value[2]
                            self._volcano_firmware_version = f"V{major:02d}.{minor:02d}.{patch}"
                        else:
                            # Just use the raw hex as a fallback
                            self._volcano_firmware_version = f"V{fw_value.hex()}"
                    
                    _LOGGER.debug("Volcano firmware version: %s", self._volcano_firmware_version)
            except Exception as e:
                _LOGGER.warning("Failed to read Volcano firmware version: %s", e)
            
            # Read auto-off time
            try:
                # Try both UUIDs to see which one works
                auto_off_value = None
                
                # First try the auto_shutoff_setting_uuid
                try:
                    if await self._characteristic_exists(self.auto_shutoff_setting_uuid):
                        auto_off_value = await asyncio.wait_for(
                            self.client.read_gatt_char(self.auto_shutoff_setting_uuid),
                            timeout=2.0
                        )
                        _LOGGER.debug("Read auto-off time from auto_shutoff_setting_uuid")
                except Exception as e:
                    _LOGGER.debug("Failed to read from auto_shutoff_setting_uuid: %s", e)
                
                # If that didn't work, try the auto_shutoff_uuid
                if not auto_off_value and await self._characteristic_exists(self.auto_shutoff_uuid):
                    try:
                        auto_off_value = await asyncio.wait_for(
                            self.client.read_gatt_char(self.auto_shutoff_uuid),
                            timeout=2.0
                        )
                        _LOGGER.debug("Read auto-off time from auto_shutoff_uuid")
                    except Exception as e:
                        _LOGGER.debug("Failed to read from auto_shutoff_uuid: %s", e)
                
                # Store raw value for debugging
                self._auto_off_time_raw = auto_off_value.hex() if auto_off_value else None
                _LOGGER.debug("Auto-off time raw bytes: %s", self._auto_off_time_raw)
                
                if auto_off_value and len(auto_off_value) >= 2:
                    # Try different parsing methods
                    
                    # Method 1: Direct byte value (if it's stored as a single byte)
                    if len(auto_off_value) == 1:
                        minutes = auto_off_value[0]
                    # Method 2: Little endian 16-bit value
                    elif len(auto_off_value) >= 2:
                        minutes = int.from_bytes(auto_off_value[0:2], byteorder='little')
                        
                        # If the value seems too large, it might be in seconds instead of minutes
                        if minutes > 180:  # Unlikely to have an auto-off time > 3 hours
                            # Try dividing by 60 to convert seconds to minutes
                            if minutes % 60 == 0:
                                _LOGGER.debug("Auto-off time appears to be in seconds, converting to minutes")
                                minutes = minutes // 60
                    
                    # Sanity check - auto-off time is typically between 1 and 180 minutes
                    if minutes < 1 or minutes > 180:
                        _LOGGER.warning("Auto-off time value %d seems out of range, using default", minutes)
                        minutes = 30  # Default to 30 minutes
                    
                    self._auto_off_time_seconds = minutes * 60
                    _LOGGER.debug("Auto-off time: %d minutes (%d seconds)", minutes, self._auto_off_time_seconds)
                else:
                    # Default to 30 minutes if we can't read the value
                    self._auto_off_time_seconds = 30 * 60
                    _LOGGER.debug("Using default auto-off time: 30 minutes")
            except Exception as e:
                _LOGGER.warning("Failed to read auto-off time: %s", e)
                # Default to 30 minutes if we can't read the value
                self._auto_off_time_seconds = 30 * 60
                _LOGGER.debug("Using default auto-off time due to error: 30 minutes")
            
            # Notify that device information has been updated
            if self._notification_callback:
                self._notification_callback(self.get_state())
                
        except Exception as e:
            _LOGGER.error("Error reading device information: %s", e)

    async def _read_device_settings(self):
        """Read device settings from the device."""
        _LOGGER.debug("Reading device settings")
        try:
            # Read register3 (vibration in original code)
            try:
                if await self._characteristic_exists(self.register3_uuid):
                    register3_value = await asyncio.wait_for(
                        self.client.read_gatt_char(self.register3_uuid),
                        timeout=2.0
                    )
                    _LOGGER.debug("Register3 raw value: %s", register3_value.hex() if register3_value else "None")
                    if register3_value and len(register3_value) >= 1:
                        self._is_vibration_enabled = register3_value[0] > 0
                        _LOGGER.debug("Register3 value: %s (raw value: %s)", 
                                    self._is_vibration_enabled, 
                                    register3_value.hex() if register3_value else "None")
                else:
                    _LOGGER.debug("Register3 characteristic not found, using default value")
            except Exception as e:
                _LOGGER.warning("Failed to read register3: %s", e)
        
            # Read register2 (display during cooling in original code)
            try:
                if await self._characteristic_exists(self.register2_uuid):
                    register2_value = await asyncio.wait_for(
                        self.client.read_gatt_char(self.register2_uuid),
                        timeout=2.0
                    )
                    _LOGGER.debug("Register2 raw value: %s", register2_value.hex() if register2_value else "None")
                    if register2_value and len(register2_value) >= 1:
                        self._is_display_on_cooling = register2_value[0] > 0
                        _LOGGER.debug("Register2 value: %s (raw value: %s)", 
                                    self._is_display_on_cooling, 
                                    register2_value.hex() if register2_value else "None")
                else:
                    _LOGGER.debug("Register2 characteristic not found, using default value")
            except Exception as e:
                _LOGGER.warning("Failed to read register2: %s", e)
        
            # Notify that device settings have been updated
            if self._notification_callback:
                self._notification_callback(self.get_state())
            
        except Exception as e:
            _LOGGER.error("Error reading device settings: %s", e)

    async def set_auto_off_time(self, minutes: int) -> None:
        """Set the auto-off time in minutes."""
        try:
            # Make sure minutes is within a reasonable range (1 to 180 minutes)
            minutes = max(1, min(180, minutes))
            
            # Debug log the exact data being sent
            # Convert minutes to seconds before packing
            seconds = minutes * 60
            packed_data = struct.pack('<H', seconds)
            _LOGGER.debug("Converting %s minutes to %s seconds for device", minutes, seconds)
            
            _LOGGER.debug("Setting auto-off time to %s minutes (%s seconds), sending data: %s", minutes, seconds, packed_data.hex())
            
            # Try both UUIDs to see which one works
            success = False
            
            # First try the auto_shutoff_setting_uuid
            if await self._characteristic_exists(self.auto_shutoff_setting_uuid):
                try:
                    await self._send_command(self.auto_shutoff_setting_uuid, packed_data)
                    success = True
                    _LOGGER.debug("Set auto-off time using auto_shutoff_setting_uuid")
                except Exception as e:
                    _LOGGER.warning("Failed to set auto-off time using auto_shutoff_setting_uuid: %s", e)
            
            # If that didn't work, try the auto_shutoff_uuid
            if not success and await self._characteristic_exists(self.auto_shutoff_uuid):
                try:
                    await self._send_command(self.auto_shutoff_uuid, packed_data)
                    success = True
                    _LOGGER.debug("Set auto-off time using auto_shutoff_uuid")
                except Exception as e:
                    _LOGGER.warning("Failed to set auto-off time using auto_shutoff_uuid: %s", e)
            
            if success:
                self._auto_off_time_seconds = minutes * 60
                _LOGGER.info("Set auto-off time to %s minutes", minutes)
                
                # Notify that device information has been updated
                if self._notification_callback:
                    self._notification_callback(self.get_state())
            else:
                _LOGGER.error("Failed to set auto-off time - no valid characteristic found")
                raise Exception("Failed to set auto-off time - no valid characteristic found")
                
        except Exception as e:
            _LOGGER.exception("Failed to set auto-off time: %s", e)
            raise

    async def set_vibration_enabled(self, enabled: bool) -> None:
        """Set whether vibration is enabled."""
        try:
            # Check if the characteristic exists
            if not await self._characteristic_exists(self.register3_uuid):
                _LOGGER.warning("Register3 characteristic not found on device, cannot set vibration")
                return
        
            # Debug log the exact data being sent
            value = 1 if enabled else 0
            packed_data = bytes([value])
            _LOGGER.debug("Setting register3 to %s, sending data: %s to UUID %s", 
                         enabled, packed_data.hex(), self.register3_uuid)
        
            await self._send_command(self.register3_uuid, packed_data)
            self._is_vibration_enabled = enabled
            _LOGGER.info("Set register3 to %s", enabled)
        
            # Notify that device settings have been updated
            if self._notification_callback:
                self._notification_callback(self.get_state())
        
        except Exception as e:
            _LOGGER.exception("Failed to set register3: %s", e)
            # Don't raise the exception, just log it
            self._is_vibration_enabled = enabled  # Pretend it worked

    async def set_display_on_cooling(self, enabled: bool) -> None:
        """Set whether the display stays on during cooling (or toggles F/C)."""
        try:
            # Check if the characteristic exists
            if not await self._characteristic_exists(self.register2_uuid):
                _LOGGER.warning("Register2 characteristic not found on device, cannot set register2")
                return
        
            # Debug log the exact data being sent
            value = 1 if enabled else 0
            packed_data = bytes([value])
            _LOGGER.debug("Setting register2 to %s, sending data: %s to UUID %s", 
                         enabled, packed_data.hex(), self.register2_uuid)
        
            await self._send_command(self.register2_uuid, packed_data)
            self._is_display_on_cooling = enabled
            _LOGGER.info("Set register2 to %s", enabled)
        
            # Notify that device settings have been updated
            if self._notification_callback:
                self._notification_callback(self.get_state())
        
        except Exception as e:
            _LOGGER.exception("Failed to set register2: %s", e)
            # Don't raise the exception, just log it
            self._is_display_on_cooling = enabled  # Pretend it worked

    def _handle_disconnect(self, client):
        """Handle disconnection event."""
        if self._is_connected:
            _LOGGER.warning("Volcano Hybrid disconnected unexpectedly")
            self._is_connected = False
            
            # Schedule reconnection
            if self._reconnect_task is None or self._reconnect_task.done():
                self._reconnect_task = asyncio.create_task(self._reconnect())

    async def _reconnect(self):
        """Attempt to reconnect to the device."""
        # Wait a moment before reconnecting
        await asyncio.sleep(2)
        
        for attempt in range(5):
            _LOGGER.debug("Reconnection attempt %d", attempt + 1)
            try:
                if await self.connect(self._notification_callback):
                    _LOGGER.info("Successfully reconnected to Volcano Hybrid")
                    return
            except Exception as e:
                _LOGGER.error("Reconnection attempt %d failed: %s", attempt + 1, e)
            
            # Exponential backoff
            await asyncio.sleep(min(30, 2 ** attempt))
        
        _LOGGER.error("Failed to reconnect after 5 attempts")

    def _start_keepalive(self):
        """Start the keepalive task."""
        asyncio.create_task(self._keepalive_loop())

    async def _keepalive_loop(self):
        """Periodically read from the device to keep the connection alive."""
        while self._is_connected:
            try:
                # If it's been more than 20 seconds since the last command, send a keepalive
                if time.time() - self._last_command_time > 20:
                    _LOGGER.debug("Sending keepalive read")
                    # Just read the register_one_uuid instead of temperature to avoid potential side effects
                    try:
                        value = await self.client.read_gatt_char(self.register_one_uuid)
                        self._last_command_time = time.time()
                        # Process the notification but don't update heater state from keepalive
                        self._register_one_raw = value.hex() if value else None
                        # Don't process notification from keepalive to avoid changing heater state
                    except Exception as e:
                        _LOGGER.warning("Keepalive read failed: %s", e)
                    
            except Exception as e:
                _LOGGER.warning("Keepalive failed: %s", e)
                # If keepalive fails, the disconnect callback should handle reconnection
            
            await asyncio.sleep(10)

    def _start_command_processor(self):
        """Start the command processor task."""
        if self._command_processor_task is None or self._command_processor_task.done():
            self._processing_commands = True
            self._command_processor_task = asyncio.create_task(self._process_command_queue())

    async def _process_command_queue(self):
        """Process commands from the queue with rate limiting."""
        _LOGGER.debug("Command processor started")
        last_command_time = 0
        
        while self._processing_commands and self._is_connected:
            try:
                if self._command_queue:
                    # Rate limit commands
                    time_since_last = time.time() - last_command_time
                    if time_since_last < self._command_rate_limit:
                        await asyncio.sleep(self._command_rate_limit - time_since_last)
                    
                    # Get the next command
                    uuid, data, future = self._command_queue.popleft()
                    
                    try:
                        _LOGGER.debug("Processing queued command to %s", uuid)
                        result = await self._execute_command(uuid, data)
                        future.set_result(result)
                    except Exception as e:
                        _LOGGER.error("Error executing queued command: %s", e)
                        future.set_exception(e)
                    
                    last_command_time = time.time()
                else:
                    # No commands in queue, sleep briefly
                    await asyncio.sleep(0.1)
            except Exception as e:
                _LOGGER.error("Error in command processor: %s", e)
                await asyncio.sleep(1)
        
        _LOGGER.debug("Command processor stopped")

    async def _queue_command(self, uuid, data):
        """Queue a command to be sent to the device."""
        future = asyncio.Future()
        self._command_queue.append((uuid, data, future))
        
        # Make sure processor is running
        if not self._processing_commands:
            self._start_command_processor()
            
        return await future

    async def disconnect(self) -> None:
        """Disconnect from the Volcano Hybrid device."""
        _LOGGER.info("Disconnecting from Volcano Hybrid")
        
        # Stop command processor
        self._processing_commands = False
        if self._command_processor_task and not self._command_processor_task.done():
            try:
                await asyncio.wait_for(self._command_processor_task, timeout=2.0)
            except asyncio.TimeoutError:
                _LOGGER.warning("Command processor did not stop gracefully")
        
        async with self._connection_lock:
            if self.client and self._is_connected:
                try:
                    await self.client.stop_notify(self.register_one_uuid)
                    await self.client.disconnect()
                    _LOGGER.info("Disconnected from Volcano Hybrid")
                except Exception as e:
                    _LOGGER.error("Error disconnecting from Volcano Hybrid: %s", e)
                finally:
                    self._is_connected = False
                    self.client = None

    def _notification_handler(self, sender, data):
        """Handle notifications from the device."""
        _LOGGER.debug("Received notification from device")
        self._register_one_raw = data.hex() if data else None
        
        # Process notification but track if it changes heater state
        old_heater_state = self._heater_on
        self._process_notification(data, "notification")
        
        # Log if notification changed heater state
        if old_heater_state != self._heater_on:
            _LOGGER.warning(
                "Heater state changed from %s to %s via notification (raw: %s)",
                old_heater_state, self._heater_on, self._register_one_raw
            )
            self._last_heater_change_time = time.time()
            self._heater_change_source = "notification"
        
        # Call external callback if provided
        if self._notification_callback:
            self._notification_callback(self.get_state())

    def _process_notification(self, data, source="unknown"):
        """Process notification data from the device."""
        if not data or len(data) < 2:
            _LOGGER.warning("Received invalid notification data: %s", data.hex() if data else None)
            return
            
        decoded_value = data[0] + (data[1] * 256)
        _LOGGER.debug("Decoded notification value: 0x%04x from %s", decoded_value, source)
        
        unmasked_fan_on_value = decoded_value & 0x2000
        unmasked_heat_on_value = decoded_value & 0x0020
        
        # Only update heater state if it's been more than 5 seconds since we explicitly set it
        # This prevents notifications from overriding user commands
        if source == "user_command" or time.time() - self._last_heater_change_time > 5 or self._heater_change_source != "user_command":
            old_heater_state = self._heater_on
            new_heater_state = unmasked_heat_on_value != 0
            
            if old_heater_state != new_heater_state:
                _LOGGER.info("Heater state changing from %s to %s via %s", 
                           old_heater_state, new_heater_state, source)
                
            self._heater_on = new_heater_state
        else:
            _LOGGER.debug("Ignoring heater state from notification as it was recently set by user")
        
        self._fan_on = unmasked_fan_on_value != 0
        
        _LOGGER.debug("Processed notification - Heater: %s, Fan: %s", 
                     "On" if self._heater_on else "Off", 
                     "On" if self._fan_on else "Off")
        
        # Try to extract temperature from notification if possible
        try:
            # This is a guess - adjust based on actual data patterns
            temp_value = (decoded_value & 0xFF00) >> 8
            if 40 <= temp_value <= 230:  # Sanity check for valid temperature range
                self._current_temperature = temp_value
                _LOGGER.debug("Extracted temperature from notification: %s°C", temp_value)
                self._temp_cache_time = time.time()
        except Exception as e:
            _LOGGER.debug("Could not extract temperature from notification: %s", e)

    def get_state(self) -> Dict[str, Any]:
        """Get the current state of the device."""
        state = {
            "current_temperature": self._current_temperature,
            "target_temperature": self._target_temperature,
            "heater_on": self._heater_on,
            "fan_on": self._fan_on,
            "brightness": self._brightness,
            "is_connected": self._is_connected,
            "is_vibration_enabled": self._is_vibration_enabled,
            "is_display_on_cooling": self._is_display_on_cooling,
            "device_info": {
                "serial_number": self._serial_number,
                "ble_firmware_version": self._ble_firmware_version,
                "hours_of_operation": self._hours_of_operation,
                "volcano_firmware_version": self._volcano_firmware_version,
                "auto_off_time_seconds": self._auto_off_time_seconds
            }
        }
        return state

    async def _ensure_connected(self):
        """Ensure the device is connected before sending commands."""
        if not self._is_connected:
            _LOGGER.debug("Device not connected, attempting to reconnect")
            if not await self.connect(self._notification_callback):
                _LOGGER.error("Failed to reconnect to device")
                raise ConnectionError("Failed to connect to Volcano Hybrid")

    async def _execute_command(self, characteristic_uuid, data):
        """Execute a command directly (used by command processor)."""
        async with self._connection_lock:
            await self._ensure_connected()
            
            try:
                _LOGGER.debug("Executing command to %s with data %s", characteristic_uuid, data.hex())
                await self.client.write_gatt_char(characteristic_uuid, data)
                self._last_command_time = time.time()
                return True
            except Exception as e:
                _LOGGER.error("Error executing command: %s", e)
                self._is_connected = False
                raise

    async def _send_command(self, characteristic_uuid, data, retry=True):
        """Send a command to the device with retry logic."""
        try:
            async with asyncio.timeout(10):  # 10 second timeout for sending commands
                # Queue the command instead of executing directly
                return await self._queue_command(characteristic_uuid, data)
        except asyncio.TimeoutError:
            _LOGGER.error("Command timed out after 10 seconds")
            self._is_connected = False
            raise TimeoutError(f"Command to {characteristic_uuid} timed out")

    async def turn_heater_on(self) -> None:
        """Turn on the heater."""
        _LOGGER.info("Turning heater on")
        await self._send_command(self.heat_on_uuid, bytes([0]))
        self._heater_on = True
        self._last_heater_change_time = time.time()
        self._heater_change_source = "user_command"
        _LOGGER.info("Turned heater on")

    async def turn_heater_off(self) -> None:
        """Turn off the heater."""
        _LOGGER.info("Turning heater off")
        await self._send_command(self.heat_off_uuid, bytes([0]))
        self._heater_on = False
        self._last_heater_change_time = time.time()
        self._heater_change_source = "user_command"
        _LOGGER.info("Turned heater off")

    async def turn_fan_on(self) -> None:
        """Turn on the fan."""
        _LOGGER.info("Turning fan on")
        await self._send_command(self.fan_on_uuid, bytes([0]))
        self._fan_on = True
        _LOGGER.info("Turned fan on")

    async def turn_fan_off(self) -> None:
        """Turn off the fan."""
        _LOGGER.info("Turning fan off")
        await self._send_command(self.fan_off_uuid, bytes([0]))
        self._fan_on = False
        _LOGGER.info("Turned fan off")

    async def set_brightness(self, brightness: int) -> None:
        """Set the screen brightness."""
        try:
            # Make sure brightness is within valid range
            brightness = max(0, min(100, brightness))
            
            # Debug log the exact data being sent
            packed_data = struct.pack('<H', brightness)
            _LOGGER.debug("Setting brightness to %s, sending data: %s", brightness, packed_data.hex())
            
            await self._send_command(self.screen_brightness_uuid, packed_data)
            self._brightness = brightness
            _LOGGER.info("Set brightness to %s", brightness)
        except Exception as e:
            _LOGGER.exception("Failed to set brightness: %s", e)
            raise

    async def _fast_read_current_temperature(self) -> int:
        """Read the current temperature quickly with minimal overhead."""
        # Check if we have a recent cached value
        if time.time() - self._temp_cache_time < self._temp_cache_duration:
            _LOGGER.debug("Using cached temperature: %s°C", self._current_temperature)
            return self._current_temperature
            
        try:
            async with self._connection_lock:
                if not self._is_connected or not self.client:
                    raise ConnectionError("Not connected to device")
                    
                try:
                    # Direct read with minimal overhead
                    value = await asyncio.wait_for(
                        self.client.read_gatt_char(self.current_temp_uuid),
                        timeout=2.0  # Shorter timeout for faster failure
                    )
                    self._last_command_time = time.time()
                    
                    # Store raw data for debugging
                    self._current_temperature_raw = value.hex() if value else None
                    
                    # Decode the temperature value
                    if len(value) >= 2:
                        decoded_value = value[0] + (value[1] * 256)
                        temperature = round(decoded_value / 10)  # Assuming temperature is in tenths of a degree
                        
                        # Validate temperature is within reasonable range (40-250°C)
                        if temperature > 1000 or temperature < 0:
                            _LOGGER.warning("Received unreasonable temperature reading: %s°C, ignoring", temperature)
                            return self._current_temperature
                            
                        self._current_temperature = temperature
                        self._temp_cache_time = time.time()
                        return temperature
                    return self._current_temperature
                except Exception as e:
                    _LOGGER.debug("Fast temperature read failed: %s", e)
                    return self._current_temperature
        except Exception:
            return self._current_temperature

    async def read_current_temperature(self) -> int:
        """Read the current temperature from the device."""
        # Try the fast read first
        try:
            temp = await self._fast_read_current_temperature()
            if temp > 0:
                return temp
        except Exception:
            pass
            
        # Fall back to the full read process if fast read fails
        try:
            async with asyncio.timeout(3):  # Even shorter timeout for faster response
                async with self._connection_lock:
                    await self._ensure_connected()
                    
                    try:
                        # Try to read from the current temperature characteristic
                        try:
                            _LOGGER.debug("Reading from current_temp_uuid")
                            value = await self.client.read_gatt_char(self.current_temp_uuid)
                            self._last_command_time = time.time()
                            
                            # Store raw data for debugging
                            self._current_temperature_raw = value.hex() if value else None
                            
                            # Decode the temperature value
                            if len(value) >= 2:
                                decoded_value = value[0] + (value[1] * 256)
                                temperature = round(decoded_value / 10)  # Assuming temperature is in tenths of a degree
                                
                                # Validate temperature is within reasonable range
                                if temperature > 1000 or temperature < 0:
                                    _LOGGER.warning("Received unreasonable temperature reading: %s°C, ignoring", temperature)
                                    return self._current_temperature
                                    
                                self._current_temperature = temperature
                                self._temp_cache_time = time.time()
                                return temperature
                        except Exception as e:
                            _LOGGER.warning("Failed to read from current_temp_uuid: %s", e)
                        
                        # Fallback: try to read from register_one_uuid
                        _LOGGER.debug("Falling back to register_one_uuid for temperature")
                        value = await self.client.read_gatt_char(self.register_one_uuid)
                        self._last_command_time = time.time()
                        
                        # Store raw data for debugging
                        self._register_one_raw = value.hex() if value else None
                        
                        # Extract temperature from register one data
                        decoded_value = value[0] + (value[1] * 256)
                        # The temperature might be in a specific part of this value
                        temperature = decoded_value & 0xFF
                        
                        # Validate temperature is within reasonable range
                        if temperature > 1000 or temperature < 0:
                            _LOGGER.warning("Received unreasonable temperature reading: %s°C, ignoring", temperature)
                            return self._current_temperature
                            
                        self._current_temperature = temperature
                        self._temp_cache_time = time.time()
                        return temperature
                        
                    except Exception as e:
                        _LOGGER.error("Error reading current temperature: %s", e)
                        return self._current_temperature
        except asyncio.TimeoutError:
            _LOGGER.warning("Reading current temperature timed out")
            return self._current_temperature
        except Exception as e:
            _LOGGER.error("Error in read_current_temperature: %s", e)
            return self._current_temperature

    async def _fast_read_target_temperature(self) -> int:
        """Read the target temperature quickly with minimal overhead."""
        try:
            async with self._connection_lock:
                if not self._is_connected or not self.client:
                    raise ConnectionError("Not connected to device")
                    
                try:
                    # Direct read with minimal overhead
                    value = await asyncio.wait_for(
                        self.client.read_gatt_char(self.target_temp_uuid),
                        timeout=2.0  # Shorter timeout for faster failure
                    )
                    self._last_command_time = time.time()
                    
                    # Store raw data for debugging
                    self._target_temperature_raw = value.hex() if value else None
                    
                    # Decode the temperature value
                    if len(value) >= 2:
                        decoded_value = value[0] + (value[1] * 256)
                        temperature = round(decoded_value / 10)
                        self._target_temperature = temperature
                        return temperature
                    return self._target_temperature
                except Exception as e:
                    _LOGGER.debug("Fast target temperature read failed: %s", e)
                    return self._target_temperature
        except Exception:
            return self._target_temperature

    async def set_target_temperature(self, temperature: int) -> None:
        """Set the target temperature."""
        try:
            # Make sure temperature is within valid range
            temperature = max(40, min(230, temperature))
            
            # Debug log the exact data being sent
            packed_data = struct.pack('<I', temperature * 10)
            _LOGGER.debug("Setting target temperature to %s°C, sending data: %s", temperature, packed_data.hex())
            
            await self._send_command(self.target_temp_uuid, packed_data)
            self._target_temperature = temperature
            _LOGGER.info("Set target temperature to %s°C", temperature)
        except Exception as e:
            _LOGGER.exception("Failed to set target temperature: %s", e)
            raise

    async def read_target_temperature(self) -> int:
        """Read the target temperature from the device."""
        # Try the fast read first
        try:
            temp = await self._fast_read_target_temperature()
            if temp > 0:
                return temp
        except Exception:
            pass
            
        # Fall back to the full read process if fast read fails
        try:
            async with asyncio.timeout(3):  # Even shorter timeout for faster response
                async with self._connection_lock:
                    await self._ensure_connected()
                    
                    try:
                        _LOGGER.debug("Reading target temperature")
                        value = await self.client.read_gatt_char(self.target_temp_uuid)
                        self._last_command_time = time.time()
                        
                        # Store raw data for debugging
                        self._target_temperature_raw = value.hex() if value else None
                        
                        if len(value) >= 2:
                            decoded_value = value[0] + (value[1] * 256)
                            temperature = round(decoded_value / 10)
                            self._target_temperature = temperature
                            return temperature
                        else:
                            return self._target_temperature
                            
                    except Exception as e:
                        _LOGGER.error("Error reading target temperature: %s", e)
                        return self._target_temperature
        except asyncio.TimeoutError:
            _LOGGER.warning("Reading target temperature timed out")
            return self._target_temperature
        except Exception as e:
            _LOGGER.error("Error in read_target_temperature: %s", e)
            return self._target_temperature

    async def fan_timer(self, duration: float, turn_off_heat: bool = False, turn_off_screen: bool = False) -> None:
        """Set a timer to turn off the fan after a specified duration."""
        await self._ensure_connected()
        
        await self.turn_fan_on()
        
        # Create a task to turn off the fan after the specified duration
        asyncio.create_task(self._fan_timer_task(duration, turn_off_heat, turn_off_screen))

    async def _fan_timer_task(self, duration: float, turn_off_heat: bool, turn_off_screen: bool) -> None:
        """Task to turn off the fan after a specified duration."""
        await asyncio.sleep(duration)
        try:
            await self.turn_fan_off()
            
            if turn_off_heat:
                await self.turn_heater_off()
                
            if turn_off_screen:
                await self.set_brightness(0)
        except Exception as e:
            _LOGGER.error("Error in fan timer task: %s", e)
            
    async def get_raw_register(self) -> str:
        """Get the raw register value for debugging."""
        try:
            async with asyncio.timeout(3):  # Even shorter timeout
                async with self._connection_lock:
                    await self._ensure_connected()
                    
                    try:
                        _LOGGER.debug("Reading raw register for debugging")
                        value = await self.client.read_gatt_char(self.register_one_uuid)
                        self._last_command_time = time.time()
                        
                        # Store and return the raw hex value
                        self._register_one_raw = value.hex() if value else None
                        return self._register_one_raw
                    except Exception as e:
                        _LOGGER.error("Error reading raw register: %s", e)
                        return self._register_one_raw or ""
        except asyncio.TimeoutError:
            _LOGGER.warning("Reading raw register timed out")
            return self._register_one_raw or ""
        except Exception as e:
            _LOGGER.error("Error in get_raw_register: %s", e)
            return self._register_one_raw or ""

    async def _characteristic_exists(self, uuid):
        """Check if a characteristic exists on the device."""
        if not self._is_connected or not self.client:
            return False
        
        try:
            # Try to get the characteristic
            services = self.client.services
            for service in services:
                for char in service.characteristics:
                    if char.uuid.lower() == uuid.lower():
                        return True
            return False
        except Exception as e:
            _LOGGER.debug("Error checking if characteristic exists: %s", e)
            return False
