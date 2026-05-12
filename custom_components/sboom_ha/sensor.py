"""Сенсоры sboom_ha: текущая строка lyrics + полный текст в атрибутах."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from ._entity_base import SboomEntity
from .const import DOMAIN
from .coordinator import SboomCoordinator
from .helpers import track_position
from .lyrics_client import current_line


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SboomCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        SboomLyricsCurrentLineSensor(coordinator, entry),
        SboomLyricsFullSensor(coordinator, entry),
    ])


class SboomLyricsCurrentLineSensor(SboomEntity, SensorEntity):
    """State = текущая строка lyrics, активная по позиции трека."""

    _attr_translation_key = "lyrics_current_line"
    _attr_icon = "mdi:script-text"
    _SYNC_INTERVAL = timedelta(seconds=1)

    def __init__(self, coordinator: SboomCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._device_unique_prefix}_lyrics_current_line"
        self._last_line: str | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Тикаем 1 раз в секунду — но пишем в state ТОЛЬКО при смене строки.
        self.async_on_remove(
            async_track_time_interval(self.hass, self._tick, self._SYNC_INTERVAL)
        )

    def _tick(self, _now: datetime) -> None:
        line = self._compute_line()
        if line != self._last_line:
            self._last_line = line
            self.async_write_ha_state()

    def _compute_line(self) -> str | None:
        lyrics = self.coordinator.current_lyrics()
        if not lyrics or not lyrics.timeline:
            return None
        pos = track_position(self.coordinator)
        if pos is None:
            return None
        line = current_line(lyrics.timeline, pos)
        return line[:255] if line else None

    @property
    def available(self) -> bool:
        if not self.coordinator.connected:
            return False
        lyrics = self.coordinator.current_lyrics()
        return bool(lyrics and lyrics.timeline)

    @property
    def native_value(self) -> str | None:
        return self._compute_line()

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        lyrics = self.coordinator.current_lyrics()
        if not lyrics:
            return None
        return {
            "source": lyrics.source,
            "instrumental": lyrics.instrumental,
            "synced": lyrics.timeline is not None,
        }


class SboomLyricsFullSensor(SboomEntity, SensorEntity):
    """State = 'available' / 'instrumental' / 'not_found'. Полный текст — в атрибутах."""

    _attr_translation_key = "lyrics"
    _attr_icon = "mdi:text"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False  # включается вручную если нужен

    def __init__(self, coordinator: SboomCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._device_unique_prefix}_lyrics"

    @property
    def native_value(self) -> str | None:
        if not self.coordinator.track or not self.coordinator.track.track_id:
            return "no_track"
        lyrics = self.coordinator.current_lyrics()
        if lyrics is None:
            # запрос ещё не завершён ИЛИ ошибка сети
            return "loading"
        if lyrics.instrumental:
            return "instrumental"
        if lyrics.plain or lyrics.synced:
            return "available"
        return "not_found"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        lyrics = self.coordinator.current_lyrics()
        if not lyrics:
            return None
        return {
            "source": lyrics.source,
            "plain_lyrics": lyrics.plain,
            "synced_lyrics": lyrics.synced,
            "instrumental": lyrics.instrumental,
        }
