"""Базовый класс сущности sboom_ha — с DeviceInfo и привязкой к coordinator."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_DEVICE_FIRMWARE,
    CONF_DEVICE_ID,
    CONF_DEVICE_MODEL,
    CONF_DEVICE_NAME,
    CONF_HOST,
    DOMAIN,
)
from .coordinator import SboomCoordinator


class SboomEntity(CoordinatorEntity[SboomCoordinator]):
    """Common-ground для media_player / sensor / camera / button / switch / number / select."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SboomCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        host = entry.data[CONF_HOST]
        device_id = entry.data.get(CONF_DEVICE_ID) or host
        self._device_unique_prefix = f"{DOMAIN}_{device_id}"

        # Полный набор полей DeviceInfo. None-поля HA проигнорирует, поэтому
        # для manual flow (без zeroconf) часть полей просто не будет показана.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=entry.data.get(CONF_DEVICE_NAME) or f"SberBoom {host}",
            manufacturer="SberDevices",
            model=entry.data.get(CONF_DEVICE_MODEL) or "SberBoom",
            sw_version=entry.data.get(CONF_DEVICE_FIRMWARE),
            serial_number=entry.data.get(CONF_DEVICE_ID),
            configuration_url=f"http://{host}",
        )

    @property
    def available(self) -> bool:
        """Entity доступна когда WS-сессия с колонкой жива.

        Coordinator выставляет `connected=False` после N (DISCONNECT_THRESHOLD=3)
        подряд неудачных reconnect-попыток — это защищает от мерцаний UI при
        транзиентных сетевых проблемах.
        """
        return self.coordinator.connected
