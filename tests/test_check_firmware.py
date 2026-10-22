"""Tests for the scheduled firmware check script."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import patch

import pytest

from custom_components.storzandbickel_ble.firmware import LATEST_KNOWN_FIRMWARE

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_firmware.py"


@pytest.fixture(scope="module")
def script() -> ModuleType:
    """Load the script the way the workflow runs it, without Home Assistant."""
    spec = importlib.util.spec_from_file_location("check_firmware", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_recorded_version_matches_the_integration(script: ModuleType) -> None:
    """The script reads the same constant the update entity uses."""
    source = script.FIRMWARE_MODULE.read_text(encoding="utf-8")
    assert script.recorded_version(source) == LATEST_KNOWN_FIRMWARE


def test_a_missing_constant_is_an_error(script: ModuleType) -> None:
    """A renamed constant fails loudly rather than comparing against nothing."""
    with pytest.raises(LookupError):
        script.recorded_version("OTHER: tuple[int, int] = (1, 3)\n")


def test_the_published_version_is_read(script: ModuleType) -> None:
    """The endpoint's known answer gives (major, minor)."""
    payload = [{"valid": 1, "majorApplication": 1, "minorApplication": 4}]
    assert script.published_version(payload) == (1, 4)


@pytest.mark.parametrize(
    "payload",
    [[], {}, [{"valid": 0, "majorApplication": 1, "minorApplication": 4}], "nope"],
)
def test_a_changed_shape_is_reported(script: ModuleType, payload: Any) -> None:
    """Anything else is reported as the endpoint changing, not as a version."""
    with pytest.raises(script.CheckError, match="changed shape"):
        script.published_version(payload)


def test_newer_firmware_is_reported(script: ModuleType) -> None:
    """Only a newer published version is worth an issue."""
    script.check((1, 3), (1, 3))
    script.check((1, 3), (1, 2))
    with pytest.raises(script.CheckError, match=r"V01.04 is out"):
        script.check((1, 3), (1, 4))


def test_main_writes_the_issue_for_the_workflow(
    script: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure lands in $GITHUB_OUTPUT as a title and a multi-line body."""
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    newer = [{"valid": 1, "majorApplication": 9, "minorApplication": 0}]

    with patch.object(script, "fetch", return_value=newer):
        assert script.main() == 1

    written = output.read_text(encoding="utf-8")
    assert "title=Volcano Hybrid firmware V09.00 is out" in written
    assert "body<<EOF" in written


def test_main_passes_when_up_to_date(script: ModuleType) -> None:
    """The same version as recorded is a green run."""
    major, minor = LATEST_KNOWN_FIRMWARE
    current = [{"valid": 1, "majorApplication": major, "minorApplication": minor}]

    with patch.object(script, "fetch", return_value=current):
        assert script.main() == 0


def test_an_unreachable_endpoint_is_reported(script: ModuleType) -> None:
    """A network failure becomes an issue, not a crash."""
    with (
        patch.object(script.urllib.request, "urlopen", side_effect=OSError("down")),
        pytest.raises(script.CheckError, match="not answering"),
    ):
        script.fetch()
