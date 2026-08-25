"""Support for Nature Remo lights."""

import logging
from typing import Any, Dict, Iterable

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from . import (
    CONF_LIGHT_LOW_LEVEL,
    DEFAULT_LIGHT_LOW_LEVEL,
    DOMAIN,
    NatureRemoAPI,
    NatureRemoBase,
)

_LOGGER = logging.getLogger(__name__)

BUTTON_OFF_PRIORITY = ("off", "onoff", "power")

# Nature Remo light remotes expose discrete buttons rather than a dimming level.
# "on" is the dim/low preset on most Japanese ceiling lights; "on-<n>" buttons
# name their own percentage. Toggle-only remotes get "onoff"/"power".
BUTTON_LOW_PRIORITY = ("on", "on-favorite")
BUTTON_TOGGLE_PRIORITY = ("onoff", "power")

ATTR_BRIGHTNESS_PERCENT = "brightness_percent"
ATTR_LAST_BUTTON = "last_button"
ATTR_BUTTONS = "buttons"
ATTR_BRIGHTNESS_LEVELS = "brightness_levels"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Nature Remo light appliances."""
    _LOGGER.debug("Setting up light platform.")
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    api = hass.data[DOMAIN][entry.entry_id]["api"]
    config = hass.data[DOMAIN][entry.entry_id]["config"]
    appliances = coordinator.data["appliances"]
    low_level = int(config.get(CONF_LIGHT_LOW_LEVEL, DEFAULT_LIGHT_LOW_LEVEL))

    async_add_entities(
        [
            NatureRemoLight(coordinator, api, appliance, low_level=low_level)
            for appliance in appliances.values()
            if appliance["type"] == "LIGHT" and appliance.get("light") is not None
        ]
    )


class NatureRemoLight(NatureRemoBase, LightEntity):
    """Representation of a Nature Remo light appliance."""

    _attr_assumed_state = True
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_color_mode = ColorMode.BRIGHTNESS

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        api: NatureRemoAPI,
        appliance: Dict[str, Any],
        low_level: int = DEFAULT_LIGHT_LOW_LEVEL,
    ) -> None:
        super().__init__(coordinator, appliance)
        self._api = api
        self._low_level = _clamp_percent(low_level, minimum=1)
        self._buttons = _button_names(appliance)
        self._level_buttons = self._build_level_buttons()
        self._state: dict[str, Any] = {}
        self._is_on = False
        self._brightness = _percent_to_brightness(100)
        self._update((appliance.get("light") or {}).get("state"))

    @property
    def is_on(self) -> bool:
        """Return true if the light is on."""
        return self._is_on

    @property
    def brightness(self) -> int | None:
        """Return the current brightness bucket as a 0-255 value."""
        return self._brightness if self._is_on else None

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return Nature Remo-specific light state."""
        attributes: dict[str, Any] = {
            ATTR_BUTTONS: self._buttons,
            ATTR_BRIGHTNESS_LEVELS: sorted(self._level_buttons),
        }
        if brightness := self._state.get("brightness"):
            attributes[ATTR_BRIGHTNESS_PERCENT] = brightness
        if last_button := self._state.get("last_button"):
            attributes[ATTR_LAST_BUTTON] = last_button
        return attributes

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on, mapping any requested brightness onto a button."""
        requested = kwargs.get(ATTR_BRIGHTNESS)
        if requested is None:
            # No level asked for: repeat the last bucket we know about.
            percent = _brightness_to_percent(self._brightness)
        else:
            percent = _brightness_to_percent(requested)

        if percent == 0:
            await self.async_turn_off()
            return

        if not self._level_buttons:
            raise HomeAssistantError(f"No matching Nature Remo light button found for {self.name}")

        level = self._snap(percent)
        await self._send_button(self._level_buttons[level], level=level)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        await self._post_button(BUTTON_OFF_PRIORITY, level=0)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        appliance = self.coordinator.data["appliances"][self._appliance_id]
        self._buttons = _button_names(appliance)
        self._level_buttons = self._build_level_buttons()
        self._update((appliance.get("light") or {}).get("state"))
        self.async_write_ha_state()

    def _build_level_buttons(self) -> Dict[int, str]:
        """Map brightness percentage to the button that selects it.

        Remotes name their presets: "on-100" is full output, "on-50" is half,
        and a bare "on" is the dim preset whose real percentage the remote
        never reports, so the configured low level stands in for it.
        """
        levels: Dict[int, str] = {
            _clamp_percent(int(name[3:]), minimum=1): name
            for name in self._buttons
            if name.startswith("on-") and name[3:].isdigit()
        }

        if (low := self._resolve_button(BUTTON_LOW_PRIORITY)) is not None:
            if levels:
                levels.setdefault(self._low_level, low)
            else:
                # A bare "on" with nothing above it is this remote's only
                # output level, so treat it as full rather than as a dim step.
                levels[100] = low
        elif not levels and (toggle := self._resolve_button(BUTTON_TOGGLE_PRIORITY)) is not None:
            levels[100] = toggle

        return levels

    def _snap(self, percent: int) -> int:
        """Round a percentage up to the nearest level the remote can produce.

        Rounding up rather than to the nearest keeps the light from coming on
        dimmer than asked for.
        """
        if not self._level_buttons:
            return _clamp_percent(percent, minimum=1)
        return min(
            (level for level in sorted(self._level_buttons) if level >= percent),
            default=max(self._level_buttons),
        )

    def _resolve_button(self, candidates: Iterable[str]) -> str | None:
        """Return the first candidate button this remote actually has."""
        return next((candidate for candidate in candidates if candidate in self._buttons), None)

    async def _post_button(self, candidates: Iterable[str], level: int | None = None) -> None:
        """Post the first available light button from the candidate list."""
        button = self._resolve_button(candidates)
        if button is None:
            raise HomeAssistantError(f"No matching Nature Remo light button found for {self.name}")

        await self._send_button(button, level)

    async def _send_button(self, button: str, level: int | None = None) -> None:
        """Post a specific light button and record the level it represents."""
        _LOGGER.debug("Sending light button %s for %s", button, self._appliance_id)
        response = await self._api.post(
            f"/appliances/{self._appliance_id}/light", {"button": button}
        )
        self._update(response)
        if level is not None:
            # The API echoes the remote's own brightness field, which is not
            # always meaningful for the bucket we just selected, so trust the
            # command we sent.
            self._apply_level(level)
        self._async_write_state_if_added()

    def _update(self, state: Dict[str, Any] | None) -> None:
        """Update local state from Nature Remo light state."""
        if not state:
            return

        self._state = state
        if state.get("power") == "on":
            self._is_on = True
        elif state.get("power") == "off":
            self._is_on = False

        if (percent := self._percent_from_state(state)) is not None:
            self._apply_level(percent)

    def _apply_level(self, percent: int) -> None:
        """Snap a percentage onto a level and store it as HA brightness."""
        if percent <= 0:
            self._is_on = False
            return
        self._is_on = True
        self._brightness = _percent_to_brightness(self._snap(percent))

    def _percent_from_state(self, state: Dict[str, Any]) -> int | None:
        """Derive a brightness percentage from a Nature Remo light state."""
        raw = state.get("brightness")
        if raw is not None:
            text = str(raw).strip()
            if text.isdigit():
                return _clamp_percent(int(text))

        # Remotes with stepped presets report things like "on-100" instead of a
        # number, so fall back to whichever button was pressed last.
        last_button = str(state.get("last_button") or "")
        if last_button.startswith("on-") and last_button[3:].isdigit():
            return _clamp_percent(int(last_button[3:]))
        if last_button == "off":
            return 0
        if last_button in ("on", "on-favorite"):
            return self._low_level
        return None

    def _async_write_state_if_added(self) -> None:
        """Write state only after Home Assistant has added the entity."""
        if self.hass is not None:
            self.async_write_ha_state()


def _button_names(appliance: Dict[str, Any]) -> list[str]:
    """Return all supported Nature Remo button names for a light appliance."""
    return [button["name"] for button in (appliance.get("light") or {}).get("buttons") or []]


def _clamp_percent(value: int, minimum: int = 0) -> int:
    """Clamp a percentage into range."""
    return max(minimum, min(100, int(value)))


def _percent_to_brightness(percent: int) -> int:
    """Convert a 0-100 percentage to a 0-255 HA brightness value."""
    return round(_clamp_percent(percent) * 255 / 100)


def _brightness_to_percent(brightness: int | None) -> int:
    """Convert a 0-255 HA brightness value to a 0-100 percentage."""
    if brightness is None:
        return 100
    value = max(0, min(255, int(brightness)))
    if value == 0:
        return 0
    # Any non-zero brightness is an "on" request, so never let rounding turn
    # 1 or 2 into an off command.
    return max(1, round(value * 100 / 255))
