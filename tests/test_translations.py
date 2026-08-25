"""Tests for integration translation files."""

import json
from pathlib import Path
from typing import Any

TRANSLATIONS = Path(__file__).parents[1] / "custom_components" / "nature_remo" / "translations"
REQUIRED_TRANSLATIONS = {"en.json", "ja.json"}


def _flatten_keys(data: dict[str, Any], prefix: str = "") -> set[str]:
    keys: set[str] = set()
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            keys.update(_flatten_keys(value, path))
        else:
            keys.add(path)
    return keys


def test_required_translation_files_exist() -> None:
    assert REQUIRED_TRANSLATIONS <= {path.name for path in TRANSLATIONS.glob("*.json")}


def test_translation_files_have_matching_keys() -> None:
    english = json.loads((TRANSLATIONS / "en.json").read_text())
    expected_keys = _flatten_keys(english)

    for path in TRANSLATIONS.glob("*.json"):
        data = json.loads(path.read_text())
        assert _flatten_keys(data) == expected_keys, path.name


# Home Assistant translates the standard climate fan modes but its swing modes
# cover only on/off/both/vertical/horizontal, so these Nature Remo values need
# translations of our own or they render raw and lowercase in the UI.
UNTRANSLATED_BY_HA_SWING_MODES = {"auto", "swing"}


def test_every_language_titles_the_nature_remo_swing_modes() -> None:
    for path in TRANSLATIONS.glob("*.json"):
        states = json.loads(path.read_text())["entity"]["climate"]["aircon"]["state_attributes"][
            "swing_mode"
        ]["state"]

        assert UNTRANSLATED_BY_HA_SWING_MODES <= set(states), path.name
        for value, label in states.items():
            assert label, f"{path.name}: {value} has an empty label"
            assert label != value, f"{path.name}: {value} is not translated"
            assert not label[0].islower(), f"{path.name}: {label!r} is not capitalised"
