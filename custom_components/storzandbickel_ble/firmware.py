"""The newest Volcano Hybrid firmware this release knows about.

The vaporizer cannot tell whether newer firmware exists; only Storz & Bickel's
web app knows, by asking their server. Rather than have every installation
call out to that server, the newest version known when this integration was
released is recorded here and compared with what the device reports.
``scripts/check_firmware.py`` asks the vendor endpoint on a schedule and opens
an issue when it no longer agrees with this constant.
"""

from __future__ import annotations

import re
from typing import Final

# (major, minor). Only bump it after flashing that firmware and confirming the
# integration still works against it: the point of the constant is that it
# names a version somebody has verified.
LATEST_KNOWN_FIRMWARE: Final[tuple[int, int]] = (1, 3)

# Where the firmware is installed from today. This integration only reports.
FIRMWARE_UPDATE_URL: Final = "https://app.storz-bickel.com/"

_NUMBERS = re.compile(r"\d+")


def parse_firmware_version(raw: str | None) -> tuple[int, int] | None:
    """Return (major, minor) from a reported version such as "V01.03.00.00"."""
    numbers = _NUMBERS.findall(raw or "")
    if len(numbers) < 2:
        return None
    return int(numbers[0]), int(numbers[1])


def format_firmware_version(version: tuple[int, int]) -> str:
    """Write a version the way Storz & Bickel do, e.g. "V01.03"."""
    return f"V{version[0]:02d}.{version[1]:02d}"


def latest_firmware_version(installed: tuple[int, int]) -> tuple[int, int]:
    """Return the newest firmware known for a device on ``installed``.

    A device flashed after this release can be ahead of the constant; offering
    it an "update" to older firmware would be wrong, so the installed version
    wins when it is newer.
    """
    return max(installed, LATEST_KNOWN_FIRMWARE)
