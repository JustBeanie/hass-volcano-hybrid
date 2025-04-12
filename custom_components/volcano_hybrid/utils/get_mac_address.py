#!/usr/bin/env python3
"""
Utility script to discover Volcano Hybrid devices and get their MAC addresses.
Run this script to find the MAC address needed for the Home Assistant integration.
"""

import asyncio
import sys
from bleak import BleakScanner

async def discover_volcano_devices():
    """Discover Volcano Hybrid devices and print their MAC addresses."""
    print("Scanning for Volcano Hybrid devices...")
    devices = await BleakScanner.discover()
    
    volcano_devices = []
    for device in devices:
        # Check if device.name is not None before looking for "VOLCANO" in it
        if device.name and "VOLCANO" in device.name.upper():
            volcano_devices.append((device.name, device.address))
    
    if volcano_devices:
        print("\nFound Volcano devices:")
        for i, (name, address) in enumerate(volcano_devices, 1):
            print(f"{i}. {name} - MAC Address: {address}")
        
        print("\nUse one of these MAC addresses in your Home Assistant integration setup.")
    else:
        print("\nNo Volcano devices found. Make sure your device is powered on and in range.")
        print("Tips:")
        print("- Ensure Bluetooth is enabled on this computer")
        print("- Make sure the Volcano Hybrid is turned on")
        print("- Try moving closer to the device")

async def run():
    """Run the discovery process."""
    try:
        await discover_volcano_devices()
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    print("Volcano Hybrid MAC Address Discovery Tool")
    print("========================================")
    asyncio.run(run())
