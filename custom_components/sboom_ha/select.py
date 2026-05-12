"""Select-entity — режим повтора."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ._entity_base import SboomEntity
from .const import DOMAIN
from .coordinator import SboomCoordinator

REPEAT_OPTIONS = ["off", "playlist", "track"]
REPEAT_MAP_FROM_API = {
    "none":     "off",
    "playlist": "playlist",
    "all":      "playlist",
    "track":    "track",
    "one":      "track",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coord: SboomCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SboomRepeatSelect(coord, entry)])


class SboomRepeatSelect(SboomEntity, SelectEntity):
    _attr_translation_key = "repeat"
    _attr_icon = "mdi:repeat"
    _attr_options = REPEAT_OPTIONS
    # Repeat доступен через media_player.repeat — на dashboard не дублируем.
    _attr_entity_registry_visible_default = False

    def __init__(self, coordinator: SboomCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._device_unique_prefix}_repeat"

    @property
    def current_option(self) -> str | None:
        track = self.coordinator.track
        if not track or not track.repeat:
            return None
        return REPEAT_MAP_FROM_API.get(track.repeat.lower())

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.client.media_repeat(option)
        await self.coordinator.async_request_refresh()
