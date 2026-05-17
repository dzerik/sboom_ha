"""Binary-сенсоры sboom_ha: булевы подсистемы из GET_STATE."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ._entity_base import SboomEntity
from .const import DOMAIN
from .coordinator import SboomCoordinator

# Read-only сенсоры, данные из coordinator — параллелизм безразличен.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SboomCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        SboomLedDisplayBinarySensor(coordinator, entry),
        SboomSleepBinarySensor(coordinator, entry),
        SboomStereoPairBinarySensor(coordinator, entry),
        SboomSubscriptionBinarySensor(coordinator, entry),
        SboomHomeSecurityBinarySensor(coordinator, entry),
        SboomMorningShowBinarySensor(coordinator, entry),
    ])


class SboomLedDisplayBinarySensor(SboomEntity, BinarySensorEntity):
    """LED-дисплей колонки включён."""

    _attr_translation_key = "led_display"
    _attr_icon = "mdi:television-ambient-light"

    def __init__(self, coordinator: SboomCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._device_unique_prefix}_led_display"

    @property
    def is_on(self) -> bool | None:
        dev = self.device_state
        return dev.led_on if dev else None


class SboomSleepBinarySensor(SboomEntity, BinarySensorEntity):
    """Колонка активна (не в режиме сна)."""

    _attr_translation_key = "active"
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, coordinator: SboomCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._device_unique_prefix}_active"

    @property
    def is_on(self) -> bool | None:
        dev = self.device_state
        if dev is None or dev.sleep_state is None:
            return None
        return dev.sleep_state == "working"


class SboomStereoPairBinarySensor(SboomEntity, BinarySensorEntity):
    """Колонка работает в стереопаре."""

    _attr_translation_key = "stereo_pair"
    _attr_icon = "mdi:speaker-multiple"

    def __init__(self, coordinator: SboomCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._device_unique_prefix}_stereo_pair"

    @property
    def is_on(self) -> bool | None:
        dev = self.device_state
        return dev.stereo_pair_active if dev else None


class SboomSubscriptionBinarySensor(SboomEntity, BinarySensorEntity):
    """Устройство привязано к подписке."""

    _attr_translation_key = "subscription_device"
    _attr_icon = "mdi:card-account-details"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SboomCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._device_unique_prefix}_subscription_device"

    @property
    def is_on(self) -> bool | None:
        dev = self.device_state
        return dev.is_subscription_device if dev else None


class SboomHomeSecurityBinarySensor(SboomEntity, BinarySensorEntity):
    """Режим домашней безопасности включён."""

    _attr_translation_key = "home_security"
    _attr_icon = "mdi:shield-home"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SboomCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._device_unique_prefix}_home_security"

    @property
    def is_on(self) -> bool | None:
        dev = self.device_state
        return dev.home_security if dev else None


class SboomMorningShowBinarySensor(SboomEntity, BinarySensorEntity):
    """Идёт «утреннее шоу»."""

    _attr_translation_key = "morning_show"
    _attr_icon = "mdi:weather-sunset-up"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SboomCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._device_unique_prefix}_morning_show"

    @property
    def is_on(self) -> bool | None:
        dev = self.device_state
        return dev.in_morning_show if dev else None
