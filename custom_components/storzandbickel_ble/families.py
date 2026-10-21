"""Which Storz & Bickel device an advertisement is, and the class that speaks it.

The Volcano Hybrid is the device this integration was built and tested on. The
Crafty, Venty and Veazy are decoded from Storz & Bickel's own web app and have
not been run against real hardware by this project; they are experimental.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from .ble_device import StorzBickelDevice
from .crafty import CRAFTY_SERVICE, CraftyDevice
from .qvap import QVAP_SERVICE, VeazyDevice, VentyDevice
from .volcano import VolcanoHybrid


class Family(StrEnum):
    """A protocol family. Stored in the config entry as ``model``."""

    VOLCANO_HYBRID = "volcano_hybrid"
    CRAFTY = "crafty"
    VENTY = "venty"
    VEAZY = "veazy"


FAMILY_NAMES: dict[Family, str] = {
    Family.VOLCANO_HYBRID: "Volcano Hybrid",
    Family.CRAFTY: "Crafty",
    Family.VENTY: "Venty",
    Family.VEAZY: "Veazy",
}

EXPERIMENTAL = frozenset({Family.CRAFTY, Family.VENTY, Family.VEAZY})
ALL_FAMILIES = frozenset(Family)
VOLCANO_ONLY = frozenset({Family.VOLCANO_HYBRID})
PORTABLE = EXPERIMENTAL
QVAP = frozenset({Family.VENTY, Family.VEAZY})


def detect_family(name: str | None, service_uuids: Iterable[str]) -> Family | None:
    """Return the family an advertisement belongs to, or None if not ours.

    By name first, because the Venty and Veazy share a service UUID and only
    their names tell them apart. The Crafty advertises a plain
    ``STORZ&BICKEL``/``Storz&Bickel`` name, so its service UUID backs that up.
    """
    upper = (name or "").upper()
    uuids = {uuid.lower() for uuid in service_uuids}
    if "VOLCANO" in upper:
        return Family.VOLCANO_HYBRID
    if upper.startswith("S&B VY"):
        return Family.VENTY
    if upper.startswith("S&B VZ"):
        return Family.VEAZY
    if upper.startswith("STORZ&BICKEL") or CRAFTY_SERVICE in uuids:
        return Family.CRAFTY
    return None


def create_device(family: Family, address: str) -> StorzBickelDevice:
    """Build the client that speaks ``family``'s protocol."""
    if family is Family.CRAFTY:
        return CraftyDevice(address)
    if family is Family.VENTY:
        return VentyDevice(address)
    if family is Family.VEAZY:
        return VeazyDevice(address)
    return VolcanoHybrid(address)


__all__ = ["QVAP_SERVICE"]
