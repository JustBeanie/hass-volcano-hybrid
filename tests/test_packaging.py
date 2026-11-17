"""Keep what HACS downloads and what the release workflow uploads in step."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parent.parent
HACS = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
RELEASE = (ROOT / ".github" / "workflows" / "release.yaml").read_text(encoding="utf-8")


def test_hacs_downloads_the_file_the_release_uploads() -> None:
    """A different name on either side leaves HACS with nothing to install."""
    assert HACS["zip_release"] is True
    assert RELEASE.count(HACS["filename"]) == 2  # built, then uploaded


def test_the_zip_is_the_integration_folder_itself() -> None:
    """HACS unpacks the zip straight into custom_components/<domain>."""
    assert "INTEGRATION: custom_components/storzandbickel_ble" in RELEASE
    assert 'cd "$INTEGRATION"' in RELEASE


def test_a_mismatched_tag_fails_the_release() -> None:
    """The tag HACS shows and the version Home Assistant shows must agree."""
    assert '"v$version" != "$TAG"' in RELEASE
