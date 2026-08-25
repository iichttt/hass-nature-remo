"""Tests for Nature Remo switch, light, and TV entities."""

from typing import Any

import pytest
from homeassistant.components.media_player import MediaPlayerState
from homeassistant.components.media_player.const import MediaPlayerEntityFeature
from homeassistant.exceptions import HomeAssistantError

from custom_components.nature_remo.light import NatureRemoLight
from custom_components.nature_remo.media_player import NatureRemoTV
from custom_components.nature_remo.switch import NatureRemoSwitch


class FakeCoordinator:
    """Minimal coordinator test double."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self.data = data or {}


class FakeApi:
    """Minimal API test double."""

    def __init__(self, *responses: dict[str, Any]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def post(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((path, data))
        if self.responses:
            return self.responses.pop(0)
        return {}


def _device() -> dict[str, Any]:
    return {"id": "device-1", "name": "Living Room"}


def test_switch_sends_on_off_or_toggle_signals() -> None:
    appliance = {
        "id": "appliance-1",
        "nickname": "Fan",
        "type": "IR",
        "device": _device(),
        "signals": [
            {"id": "signal-on", "name": "On", "image": "ico_on"},
            {"id": "signal-off", "name": "Off", "image": "ico_off"},
        ],
    }
    api = FakeApi()
    switch = NatureRemoSwitch(FakeCoordinator(), api, appliance)  # type: ignore[arg-type]

    import asyncio

    asyncio.run(switch.async_turn_on())
    asyncio.run(switch.async_turn_off())

    assert api.calls == [
        ("/signals/signal-on/send", {}),
        ("/signals/signal-off/send", {}),
    ]
    assert switch.is_on is False


def test_switch_raises_when_requested_direction_has_no_signal() -> None:
    appliance = {
        "id": "appliance-1",
        "nickname": "Fan",
        "type": "IR",
        "device": _device(),
        "signals": [{"id": "signal-on", "name": "On", "image": "ico_on"}],
    }
    switch = NatureRemoSwitch(FakeCoordinator(), FakeApi(), appliance)  # type: ignore[arg-type]

    import asyncio

    with pytest.raises(HomeAssistantError):
        asyncio.run(switch.async_turn_off())


def test_light_uses_nature_remo_state_and_buttons() -> None:
    appliance = {
        "id": "appliance-1",
        "nickname": "Ceiling Light",
        "type": "LIGHT",
        "device": _device(),
        "light": {
            "buttons": [
                {"name": "on", "image": "ico_on", "label": "Light_on"},
                {"name": "off", "image": "ico_off", "label": "Light_off"},
            ],
            "state": {"power": "off", "brightness": "0", "last_button": "off"},
        },
    }
    api = FakeApi(
        {"power": "on", "brightness": "100", "last_button": "on"},
        {"power": "off", "brightness": "0", "last_button": "off"},
    )
    light = NatureRemoLight(FakeCoordinator(), api, appliance)  # type: ignore[arg-type]

    import asyncio

    assert light.is_on is False

    asyncio.run(light.async_turn_on())
    assert light.is_on is True

    asyncio.run(light.async_turn_off())
    assert light.is_on is False
    assert api.calls == [
        ("/appliances/appliance-1/light", {"button": "on"}),
        ("/appliances/appliance-1/light", {"button": "off"}),
    ]


def test_light_supports_toggle_only_remotes() -> None:
    appliance = {
        "id": "appliance-1",
        "nickname": "Ceiling Light",
        "type": "LIGHT",
        "device": _device(),
        "light": {
            "buttons": [{"name": "onoff", "image": "ico_io", "label": "Light_onoff"}],
            "state": {"power": "off", "brightness": "0", "last_button": "onoff"},
        },
    }
    api = FakeApi({"power": "on", "brightness": "100", "last_button": "onoff"})
    light = NatureRemoLight(FakeCoordinator(), api, appliance)  # type: ignore[arg-type]

    import asyncio

    asyncio.run(light.async_turn_on())

    assert api.calls == [("/appliances/appliance-1/light", {"button": "onoff"})]
    assert light.is_on is True


def test_light_maps_brightness_onto_low_and_full_buttons() -> None:
    appliance = {
        "id": "appliance-1",
        "nickname": "Ceiling Light",
        "type": "LIGHT",
        "device": _device(),
        "light": {
            "buttons": [
                {"name": "on", "image": "ico_on", "label": "Light_on"},
                {"name": "on-100", "image": "ico_on", "label": "Light_all"},
                {"name": "off", "image": "ico_off", "label": "Light_off"},
            ],
            "state": {"power": "off", "brightness": "0", "last_button": "off"},
        },
    }
    api = FakeApi(
        {"power": "on", "last_button": "on"},
        {"power": "on", "last_button": "on-100"},
        {"power": "off", "last_button": "off"},
    )
    light = NatureRemoLight(FakeCoordinator(), api, appliance, low_level=20)  # type: ignore[arg-type]

    import asyncio

    # 10% -> below the 20% threshold -> low preset.
    asyncio.run(light.async_turn_on(brightness=26))
    assert light.is_on is True
    assert light.brightness == 51

    # 80% -> above the threshold -> full brightness.
    asyncio.run(light.async_turn_on(brightness=204))
    assert light.brightness == 255

    # 0% is an off request, not a dim request.
    asyncio.run(light.async_turn_on(brightness=0))
    assert light.is_on is False
    assert light.brightness is None

    assert api.calls == [
        ("/appliances/appliance-1/light", {"button": "on"}),
        ("/appliances/appliance-1/light", {"button": "on-100"}),
        ("/appliances/appliance-1/light", {"button": "off"}),
    ]


def test_light_threshold_is_configurable() -> None:
    appliance = {
        "id": "appliance-1",
        "nickname": "Ceiling Light",
        "type": "LIGHT",
        "device": _device(),
        "light": {
            "buttons": [
                {"name": "on", "image": "ico_on", "label": "Light_on"},
                {"name": "on-100", "image": "ico_on", "label": "Light_all"},
                {"name": "off", "image": "ico_off", "label": "Light_off"},
            ],
            "state": {"power": "off", "brightness": "0", "last_button": "off"},
        },
    }
    api = FakeApi({"power": "on", "last_button": "on"})
    light = NatureRemoLight(FakeCoordinator(), api, appliance, low_level=60)  # type: ignore[arg-type]

    import asyncio

    # 50% would be "full" at the default threshold, but is "low" at 60%.
    asyncio.run(light.async_turn_on(brightness=128))

    assert api.calls == [("/appliances/appliance-1/light", {"button": "on"})]
    assert light.brightness == 153


def test_light_falls_back_to_full_button_when_low_button_missing() -> None:
    appliance = {
        "id": "appliance-1",
        "nickname": "Ceiling Light",
        "type": "LIGHT",
        "device": _device(),
        "light": {
            "buttons": [
                {"name": "on-100", "image": "ico_on", "label": "Light_all"},
                {"name": "off", "image": "ico_off", "label": "Light_off"},
            ],
            "state": {"power": "off", "brightness": "0", "last_button": "off"},
        },
    }
    api = FakeApi({"power": "on", "last_button": "on-100"})
    light = NatureRemoLight(FakeCoordinator(), api, appliance, low_level=20)  # type: ignore[arg-type]

    import asyncio

    asyncio.run(light.async_turn_on(brightness=13))

    assert api.calls == [("/appliances/appliance-1/light", {"button": "on-100"})]
    assert light.brightness == 255


def test_tv_exposes_features_and_sends_commands() -> None:
    appliance = {
        "id": "appliance-1",
        "nickname": "TV",
        "type": "TV",
        "device": _device(),
        "tv": {
            "buttons": [
                {"name": "power", "image": "ico_io", "label": "TV_power"},
                {"name": "input-t", "image": "ico_input", "label": "TV_input"},
                {"name": "input-bs", "image": "ico_input", "label": "TV_bs"},
                {"name": "vol-up", "image": "ico_plus", "label": "TV_vol_up"},
                {"name": "vol-down", "image": "ico_minus", "label": "TV_vol_down"},
                {"name": "mute", "image": "ico_mute", "label": "TV_mute"},
                {"name": "play", "image": "ico_play", "label": "TV_play"},
            ],
            "state": {"input": "t"},
        },
    }
    api = FakeApi({"input": "t"}, {"input": "bs"})
    tv = NatureRemoTV(FakeCoordinator(), api, appliance)  # type: ignore[arg-type]

    assert tv.source == "terrestrial"
    assert tv.source_list == ["terrestrial", "BS"]
    assert tv.supported_features & MediaPlayerEntityFeature.TURN_ON
    assert tv.supported_features & MediaPlayerEntityFeature.VOLUME_STEP
    assert tv.supported_features & MediaPlayerEntityFeature.PLAY

    import asyncio

    asyncio.run(tv.async_turn_on())
    asyncio.run(tv.async_select_source("BS"))

    assert tv.state == MediaPlayerState.IDLE
    assert tv.source == "BS"
    assert api.calls == [
        ("/appliances/appliance-1/tv", {"button": "power"}),
        ("/appliances/appliance-1/tv", {"button": "input-bs"}),
    ]


def test_light_treats_minimum_brightness_as_on_not_off() -> None:
    appliance = {
        "id": "appliance-1",
        "nickname": "Ceiling Light",
        "type": "LIGHT",
        "device": _device(),
        "light": {
            "buttons": [
                {"name": "on", "image": "ico_on", "label": "Light_on"},
                {"name": "on-100", "image": "ico_on", "label": "Light_all"},
                {"name": "off", "image": "ico_off", "label": "Light_off"},
            ],
            "state": {"power": "off", "brightness": "0", "last_button": "off"},
        },
    }
    api = FakeApi({"power": "on", "last_button": "on"})
    light = NatureRemoLight(FakeCoordinator(), api, appliance, low_level=20)  # type: ignore[arg-type]

    import asyncio

    # 1/255 rounds to 0% but is still an "on" request.
    asyncio.run(light.async_turn_on(brightness=1))

    assert api.calls == [("/appliances/appliance-1/light", {"button": "on"})]
    assert light.is_on is True


def _light_appliance(*button_names: str) -> dict[str, Any]:
    return {
        "id": "appliance-1",
        "nickname": "Ceiling Light",
        "type": "LIGHT",
        "device": _device(),
        "light": {
            "buttons": [{"name": name, "image": "ico", "label": name} for name in button_names],
            "state": {"power": "off", "brightness": "0", "last_button": "off"},
        },
    }


def test_light_uses_every_numbered_preset_the_remote_exposes() -> None:
    appliance = _light_appliance("on", "on-25", "on-50", "on-75", "on-100", "off")
    api = FakeApi()
    light = NatureRemoLight(FakeCoordinator(), api, appliance, low_level=20)  # type: ignore[arg-type]

    import asyncio

    assert light.extra_state_attributes["brightness_levels"] == [20, 25, 50, 75, 100]

    # Each request rounds *up* to the nearest reachable level, so the light is
    # never dimmer than asked for.
    # 26/255 = 10%, 60/255 = 24%, 128/255 = 50%, 166/255 = 65%, 230/255 = 90%
    for brightness in (26, 60, 128, 166, 230):
        asyncio.run(light.async_turn_on(brightness=brightness))

    assert [data["button"] for _, data in api.calls] == [
        "on",
        "on-25",
        "on-50",
        "on-75",
        "on-100",
    ]


def test_light_ladder_reproduces_two_bucket_behaviour() -> None:
    """A plain on/on-100 remote must behave exactly as it did before."""
    appliance = _light_appliance("on", "on-100", "off")
    api = FakeApi()
    light = NatureRemoLight(FakeCoordinator(), api, appliance, low_level=20)  # type: ignore[arg-type]

    import asyncio

    assert light.extra_state_attributes["brightness_levels"] == [20, 100]

    for brightness in (1, 26, 51):
        asyncio.run(light.async_turn_on(brightness=brightness))
    for brightness in (53, 128, 255):
        asyncio.run(light.async_turn_on(brightness=brightness))

    assert [data["button"] for _, data in api.calls] == ["on"] * 3 + ["on-100"] * 3


def test_light_with_only_a_plain_on_button_treats_it_as_full() -> None:
    appliance = _light_appliance("on", "off")
    api = FakeApi()
    light = NatureRemoLight(FakeCoordinator(), api, appliance, low_level=20)  # type: ignore[arg-type]

    import asyncio

    # "on" is the only output level, so it is full brightness, not a dim step.
    assert light.extra_state_attributes["brightness_levels"] == [100]

    asyncio.run(light.async_turn_on(brightness=255))

    assert api.calls == [("/appliances/appliance-1/light", {"button": "on"})]
    assert light.brightness == 255


def test_light_reports_level_reported_by_the_cloud() -> None:
    appliance = _light_appliance("on", "on-50", "on-100", "off")
    api = FakeApi({"power": "on", "brightness": "50", "last_button": "on-50"})
    light = NatureRemoLight(FakeCoordinator(), api, appliance, low_level=20)  # type: ignore[arg-type]

    import asyncio

    asyncio.run(light.async_turn_on(brightness=128))

    assert light.is_on is True
    assert light.brightness == 128  # 50% of 255
