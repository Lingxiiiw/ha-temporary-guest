"""Repository metadata consistency tests."""

import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
INTEGRATION = ROOT / "custom_components" / "temporary_guest"


def test_manifest_and_hacs_versions_match() -> None:
    """Manifest and bundled card have the same release version."""
    manifest = json.loads((INTEGRATION / "manifest.json").read_text(encoding="utf-8"))
    card = (INTEGRATION / "frontend" / "temporary-guest-card.js").read_text(
        encoding="utf-8"
    )
    assert manifest["domain"] == "temporary_guest"
    assert manifest["version"] in card
    assert manifest["config_flow"] is True
    assert manifest["requirements"] == []


def test_translations_are_valid_json() -> None:
    """All user-facing JSON files parse."""
    for path in [INTEGRATION / "strings.json", *sorted((INTEGRATION / "translations").glob("*.json"))]:
        assert json.loads(path.read_text(encoding="utf-8"))


def test_password_is_not_exposed_as_an_entity() -> None:
    """The integration has no sensor platform or services file containing password state."""
    assert not (INTEGRATION / "sensor.py").exists()
    assert not (INTEGRATION / "binary_sensor.py").exists()
    assert not (INTEGRATION / "services.yaml").exists()

