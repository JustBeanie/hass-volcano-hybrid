"""Keep strings.json, translations/en.json, icons.json and the code in step."""

from __future__ import annotations

from collections.abc import Iterator
import inspect
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from custom_components.storzandbickel_ble import (
    binary_sensor,
    button,
    climate,
    light,
    number,
    sensor,
    switch,
    update,
)

PACKAGE = (
    Path(__file__).resolve().parent.parent / "custom_components" / "storzandbickel_ble"
)
STRINGS = json.loads((PACKAGE / "strings.json").read_text(encoding="utf-8"))
ENGLISH = json.loads((PACKAGE / "translations" / "en.json").read_text(encoding="utf-8"))
ICONS = json.loads((PACKAGE / "icons.json").read_text(encoding="utf-8"))

PLATFORMS: dict[str, ModuleType] = {
    "binary_sensor": binary_sensor,
    "button": button,
    "climate": climate,
    "light": light,
    "number": number,
    "sensor": sensor,
    "switch": switch,
    "update": update,
}


def _paths(tree: Any, prefix: str = "") -> Iterator[str]:
    """Yield the dotted path of every leaf string."""
    if isinstance(tree, dict):
        for key, value in tree.items():
            yield from _paths(value, f"{prefix}{key}.")
    else:
        yield prefix.rstrip(".")


def _translation_keys(module: ModuleType) -> set[str]:
    """Return every entity translation key a platform module declares."""
    keys: set[str] = set()
    for value in vars(module).values():
        # Description tuples (SENSORS, SWITCHES, ...) and single descriptions.
        items = value if isinstance(value, tuple) else (value,)
        for item in items:
            key = getattr(item, "translation_key", None)
            if isinstance(key, str) and hasattr(item, "key"):
                keys.add(key)
        # Entity classes with a fixed _attr_translation_key. Home Assistant's
        # entity metaclass turns the class attribute into a property and keeps
        # the value under "__attr_translation_key".
        if inspect.isclass(value) and value.__module__ == module.__name__:
            key = vars(value).get("__attr_translation_key")
            if isinstance(key, str):
                keys.add(key)
    return keys


def test_english_has_exactly_the_keys_strings_has() -> None:
    """en.json is strings.json with its references resolved, nothing more."""
    assert set(_paths(ENGLISH)) == set(_paths(STRINGS))


def test_english_has_no_unresolved_references() -> None:
    """A [%key:...%] reference only works in strings.json."""
    unresolved = [
        path for path in _paths(ENGLISH) if "[%key:" in str(_lookup(ENGLISH, path))
    ]
    assert unresolved == []


def _lookup(tree: Any, path: str) -> Any:
    for part in path.split("."):
        tree = tree[part]
    return tree


@pytest.mark.parametrize("platform", sorted(PLATFORMS))
def test_every_entity_has_a_name(platform: str) -> None:
    """Each translation key the code uses has a name in both files."""
    keys = _translation_keys(PLATFORMS[platform])
    # The climate entity is named after the device; every other platform has
    # keys, and finding none would mean this test is checking nothing.
    assert keys or platform == "climate"
    for tree in (STRINGS, ENGLISH):
        names = tree["entity"].get(platform, {})
        assert not keys - set(names), f"{platform}: {sorted(keys - set(names))}"


def test_every_icon_belongs_to_an_entity() -> None:
    """An icon for a key no entity has is dead configuration."""
    for platform, icons in ICONS["entity"].items():
        assert set(icons) <= set(STRINGS["entity"][platform]), platform
