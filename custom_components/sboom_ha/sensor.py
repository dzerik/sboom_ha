"""Сенсоры sboom_ha: lyrics + декларативные read-only сенсоры подсистем GET_STATE."""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from ._entity_base import SboomEntity
from ._models import DeviceState
from ._schedule import next_alarm, next_timer
from .coordinator import SboomCoordinator
from .helpers import lyrics_position
from .lyrics_client import current_line

# Read-only сенсоры, данные из coordinator — параллелизм безразличен.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class SboomSensorSpec:
    """Декларативное описание тривиального read-only сенсора.

    Все значения читаются из coordinator через value_fn/attrs_fn — сенсор
    описывается одной записью в SENSOR_SPECS вместо отдельного класса
    (тот же паттерн, что BUTTONS в button.py и SWITCHES в switch.py).
    """

    key: str  # суффикс unique_id
    translation_key: str
    icon: str | None = None
    value_fn: Callable[[SboomCoordinator], Any]
    attrs_fn: Callable[[SboomCoordinator], dict[str, Any] | None] | None = None
    native_unit: str | None = None
    state_class: Any = None  # SensorStateClass; Any — чтобы не тянуть импорт в тестовые stub'ы
    device_class: Any = None  # SensorDeviceClass; аналогично
    entity_category: EntityCategory | None = None
    enabled_default: bool = True


def _dev(c: SboomCoordinator) -> DeviceState | None:
    """Подсистемы устройства из последнего GET_STATE, либо None."""
    return c.state.device if c.state else None


SENSOR_SPECS: tuple[SboomSensorSpec, ...] = (
    # Яркость LED-дисплея колонки (0-100%).
    SboomSensorSpec(
        key="led_brightness",
        translation_key="led_brightness",
        icon="mdi:brightness-6",
        native_unit=PERCENTAGE,
        state_class="measurement",  # строка == SensorStateClass.MEASUREMENT
        value_fn=lambda c: dev.led_brightness if (dev := _dev(c)) else None,
    ),
    # Количество установленных будильников. Список — в атрибутах.
    SboomSensorSpec(
        key="alarms",
        translation_key="alarms",
        icon="mdi:alarm",
        value_fn=lambda c: dev.alarms_count if (dev := _dev(c)) else None,
        attrs_fn=lambda c: {"alarms": dev.alarms} if (dev := _dev(c)) else None,
    ),
    # Количество активных таймеров. Список — в атрибутах.
    SboomSensorSpec(
        key="timers",
        translation_key="timers",
        icon="mdi:timer-outline",
        value_fn=lambda c: dev.timers_count if (dev := _dev(c)) else None,
        attrs_fn=lambda c: {"timers": dev.timers} if (dev := _dev(c)) else None,
    ),
    # Время ближайшего срабатывания будильника (timestamp).
    SboomSensorSpec(
        key="next_alarm",
        translation_key="next_alarm",
        icon="mdi:alarm-check",
        device_class="timestamp",
        value_fn=lambda c: (
            next_alarm(dev.alarms, datetime.now(UTC)) if (dev := _dev(c)) else None
        ),
    ),
    # Время окончания ближайшего таймера (timestamp).
    SboomSensorSpec(
        key="next_timer",
        translation_key="next_timer",
        icon="mdi:timer-check-outline",
        device_class="timestamp",
        value_fn=lambda c: (
            next_timer(dev.timers, datetime.now(UTC)) if (dev := _dev(c)) else None
        ),
    ),
    # Активное приложение колонки (music/bluetooth/news/…).
    SboomSensorSpec(
        key="active_app",
        translation_key="active_app",
        icon="mdi:application",
        value_fn=lambda c: dev.active_app if (dev := _dev(c)) else None,
    ),
    # Персона голосового ассистента (afina/joy/sber).
    SboomSensorSpec(
        key="assistant_character",
        translation_key="assistant_character",
        icon="mdi:account-voice",
        value_fn=lambda c: dev.assistant_character if (dev := _dev(c)) else None,
    ),
    # Режим multiroom (NONE/…).
    SboomSensorSpec(
        key="multiroom_mode",
        translation_key="multiroom_mode",
        icon="mdi:speaker-multiple",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: dev.multiroom_mode if (dev := _dev(c)) else None,
    ),
    # Тип сетевого подключения колонки (WIFI/…).
    SboomSensorSpec(
        key="network_type",
        translation_key="network_type",
        icon="mdi:wifi",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: dev.network_type if (dev := _dev(c)) else None,
    ),
    # Спаренные Bluetooth-устройства. State = количество, список — в атрибутах.
    SboomSensorSpec(
        key="paired_bt",
        translation_key="paired_bt",
        icon="mdi:bluetooth-connect",
        value_fn=lambda c: len(c.paired_bt),
        attrs_fn=lambda c: {
            "devices": [
                {"mac": d.mac, "name": d.name, "connected": d.connected}
                for d in c.paired_bt
            ]
        },
    ),
    # IP-адрес колонки в локальной сети (network.ip). Diagnostic.
    SboomSensorSpec(
        key="ip_address",
        translation_key="ip_address",
        icon="mdi:ip-network",
        entity_category=EntityCategory.DIAGNOSTIC,
        enabled_default=False,
        value_fn=lambda c: dev.network_ip if (dev := _dev(c)) else None,
    ),
    # Часовой пояс колонки (time.timezone_id). Diagnostic.
    SboomSensorSpec(
        key="timezone",
        translation_key="timezone",
        icon="mdi:map-clock",
        entity_category=EntityCategory.DIAGNOSTIC,
        enabled_default=False,
        value_fn=lambda c: dev.timezone_id if (dev := _dev(c)) else None,
    ),
    # Возрастной режим профиля (user_settings.age_mode: adult/child). Diagnostic.
    SboomSensorSpec(
        key="age_mode",
        translation_key="age_mode",
        icon="mdi:account-child",
        entity_category=EntityCategory.DIAGNOSTIC,
        enabled_default=False,
        value_fn=lambda c: dev.age_mode if (dev := _dev(c)) else None,
    ),
    # Рассинхрон часов колонки и HA (сек): device_unixtime − now. Помогает
    # диагностировать сдвиг караоке/позиции. Значение включает возраст poll'а
    # (до volume_poll_interval), поэтому это грубый индикатор минутного skew,
    # а не точный тайминг. Diagnostic, выключен по умолчанию.
    SboomSensorSpec(
        key="clock_skew",
        translation_key="clock_skew",
        icon="mdi:clock-alert-outline",
        native_unit="s",
        state_class="measurement",
        entity_category=EntityCategory.DIAGNOSTIC,
        enabled_default=False,
        value_fn=lambda c: (
            round(dev.device_unixtime - time.time())
            if (dev := _dev(c)) and dev.device_unixtime is not None
            else None
        ),
    ),
    # Сырые Wi-Fi-координаты колонки. В отличие от device_tracker (state =
    # имя зоны, «Дома»/«Не дома»), здесь state = "lat, lon", а lat/lon/
    # accuracy/source — в атрибутах для карточек и шаблонов. Diagnostic,
    # выключен по умолчанию (координаты — чувствительные данные).
    SboomSensorSpec(
        key="coordinates",
        translation_key="coordinates",
        icon="mdi:map-marker",
        entity_category=EntityCategory.DIAGNOSTIC,
        enabled_default=False,
        value_fn=lambda c: (
            f"{dev.latitude}, {dev.longitude}"
            if (dev := _dev(c)) and dev.latitude is not None and dev.longitude is not None
            else None
        ),
        attrs_fn=lambda c: (
            {
                "latitude": dev.latitude,
                "longitude": dev.longitude,
                "gps_accuracy": dev.location_accuracy,
                "source": dev.location_source,
            }
            if (dev := _dev(c)) and dev.latitude is not None
            else None
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SboomCoordinator = entry.runtime_data
    async_add_entities([
        SboomLyricsCurrentLineSensor(coordinator, entry),
        SboomLyricsFullSensor(coordinator, entry),
        *(SboomDeviceSensor(coordinator, entry, spec) for spec in SENSOR_SPECS),
    ])


class SboomDeviceSensor(SboomEntity, SensorEntity):
    """Generic-сенсор, полностью описанный через SboomSensorSpec."""

    def __init__(
        self,
        coordinator: SboomCoordinator,
        entry: ConfigEntry,
        spec: SboomSensorSpec,
    ) -> None:
        super().__init__(coordinator, entry)
        self._spec = spec
        self._attr_unique_id = f"{self._device_unique_prefix}_{spec.key}"
        self._attr_translation_key = spec.translation_key
        self._attr_icon = spec.icon
        self._attr_native_unit_of_measurement = spec.native_unit
        self._attr_state_class = spec.state_class
        self._attr_device_class = spec.device_class
        self._attr_entity_category = spec.entity_category
        self._attr_entity_registry_enabled_default = spec.enabled_default

    @property
    def native_value(self) -> Any:
        return self._spec.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self._spec.attrs_fn is None:
            return None
        return self._spec.attrs_fn(self.coordinator)


class SboomLyricsCurrentLineSensor(SboomEntity, SensorEntity):
    """State = текущая строка lyrics, активная по позиции трека.

    Для треков без synced-текста сенсор остаётся available со state=None
    (unknown): «нет лирики» — валидное состояние данных, а не недоступность
    устройства. Иначе автоматизации ловили бы ложные unavailable на каждом
    треке без текста.
    """

    _attr_translation_key = "lyrics_current_line"
    _attr_icon = "mdi:script-text"

    def __init__(self, coordinator: SboomCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._device_unique_prefix}_lyrics_current_line"
        self._last_line: str | None = None
        self._unsub_tick = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._schedule_tick(0.5)
        self.async_on_remove(self._cancel_tick)

    @callback
    def _cancel_tick(self) -> None:
        if self._unsub_tick is not None:
            self._unsub_tick()
            self._unsub_tick = None

    @callback
    def _schedule_tick(self, delay: float) -> None:
        self._cancel_tick()
        self._unsub_tick = async_call_later(self.hass, delay, self._tick)

    @callback
    def _tick(self, _now: datetime) -> None:
        line = self._compute_line()
        if line != self._last_line:
            self._last_line = line
            self.async_write_ha_state()
        # Самопланирование на границу следующей строки (а не жёсткий 1s-тик):
        # строка меняется без запаздывания до секунды.
        self._schedule_tick(self._next_tick_delay())

    def _next_tick_delay(self) -> float:
        lyrics = self.coordinator.current_lyrics()
        track = self.coordinator.track
        pos = lyrics_position(self.coordinator)
        if (
            lyrics and lyrics.timeline
            and track and track.playing
            and pos is not None
        ):
            next_ts = next((ts for ts, _ in lyrics.timeline if ts > pos), None)
            if next_ts is not None:
                return min(2.0, max(0.25, next_ts - pos))
        return 1.0

    def _compute_line(self) -> str | None:
        lyrics = self.coordinator.current_lyrics()
        if not lyrics or not lyrics.timeline:
            return None
        pos = lyrics_position(self.coordinator)
        if pos is None:
            return None
        line = current_line(lyrics.timeline, pos)
        return line[:255] if line else None

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
    # ENUM: набор состояний определяется нашим же кодом (native_value ниже) —
    # HA получает переводимые состояния и валидацию значений.
    _attr_device_class = "enum"  # строка == SensorDeviceClass.ENUM
    _attr_options: list[str] = ["no_track", "loading", "available", "instrumental", "not_found"]  # noqa: RUF012 — контракт HA: list

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
