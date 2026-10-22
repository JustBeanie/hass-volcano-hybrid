"""Compare the recorded Volcano Hybrid firmware with the one Storz & Bickel publish.

The official web app asks the vendor's server which firmware is current before
it offers an update. This asks the same question on a schedule, so the
integration itself never has to. It exits non-zero, with a title and body for
an issue written to $GITHUB_OUTPUT, when the published version has moved past
``LATEST_KNOWN_FIRMWARE`` or the endpoint stops answering the way it did.

Standard library only, and the constant is parsed rather than imported, so the
workflow needs neither Home Assistant nor an install step.
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import sys
from typing import Any, Final
import urllib.error
import urllib.parse
import urllib.request

ENDPOINT: Final = "https://app.storz-bickel.com/firmwareHybrid"
# "version=true" asks for the version numbers only; false would return the
# firmware binary, which this check has no business downloading.
REQUEST: Final = {"version": "true"}
TIMEOUT_SECONDS: Final = 30
FIRMWARE_MODULE: Final = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "storzandbickel_ble"
    / "firmware.py"
)


class CheckError(Exception):
    """Something worth opening an issue about."""

    def __init__(self, title: str, body: str) -> None:
        """Record the issue title and body."""
        super().__init__(title)
        self.title = title
        self.body = body


def recorded_version(source: str) -> tuple[int, int]:
    """Return LATEST_KNOWN_FIRMWARE from the firmware module's source."""
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "LATEST_KNOWN_FIRMWARE"
            and node.value is not None
        ):
            major, minor = ast.literal_eval(node.value)
            return int(major), int(minor)
    raise LookupError("LATEST_KNOWN_FIRMWARE not found")


def published_version(payload: Any) -> tuple[int, int]:
    """Return (major, minor) from the endpoint's answer.

    The answer has been a one-element list of
    ``{"valid": 1, "majorApplication": int, "minorApplication": int}``.
    Anything else means the endpoint changed and this check needs updating.
    """
    try:
        entry = payload[0]
        valid = entry["valid"] == 1
        version = int(entry["majorApplication"]), int(entry["minorApplication"])
    except (KeyError, IndexError, TypeError, ValueError) as err:
        raise _shape_changed(payload) from err
    if not valid:
        raise _shape_changed(payload)
    return version


def _shape_changed(payload: Any) -> CheckError:
    """Report an answer this check no longer understands."""
    return CheckError(
        "The Volcano Hybrid firmware endpoint changed shape",
        "The response no longer has the expected `valid`, `majorApplication`"
        " and `minorApplication` fields, so this check cannot tell whether"
        " new firmware shipped. Nothing is broken for users: the integration"
        f" never calls the endpoint.\n\n```\n{json.dumps(payload)[:2000]}\n```",
    )


def fetch() -> Any:
    """Ask the vendor endpoint for the current version."""
    data = urllib.parse.urlencode(REQUEST).encode()
    request = urllib.request.Request(ENDPOINT, data=data, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as err:
        raise CheckError(
            "The Volcano Hybrid firmware endpoint is not answering",
            f"`POST {ENDPOINT}` failed: `{err}`.",
        ) from err


def check(recorded: tuple[int, int], published: tuple[int, int]) -> None:
    """Raise if Storz & Bickel have published firmware newer than recorded."""
    if published > recorded:
        fmt = "V{:02d}.{:02d}".format
        raise CheckError(
            f"Volcano Hybrid firmware {fmt(*published)} is out",
            f"Storz & Bickel now publish {fmt(*published)}; this integration"
            f" records {fmt(*recorded)} as the newest known.\n\nFlash it, check"
            " the integration still works, then bump `LATEST_KNOWN_FIRMWARE` in"
            " `custom_components/storzandbickel_ble/firmware.py`.",
        )


def main() -> int:
    """Run the check, reporting through $GITHUB_OUTPUT on failure."""
    recorded = recorded_version(FIRMWARE_MODULE.read_text(encoding="utf-8"))
    try:
        check(recorded, published_version(fetch()))
    except CheckError as failure:
        if output := os.environ.get("GITHUB_OUTPUT"):
            with Path(output).open("a", encoding="utf-8") as handle:
                handle.write(f"title={failure.title}\n")
                handle.write(f"body<<EOF\n{failure.body}\nEOF\n")
        sys.stderr.write(f"{failure.title}\n{failure.body}\n")
        return 1
    sys.stdout.write(f"Up to date: V{recorded[0]:02d}.{recorded[1]:02d}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
