"""SBoom (LAN) — Home Assistant custom integration for SberBoom-class speakers."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import SboomCoordinator
from .services import async_register_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.MEDIA_PLAYER,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SWITCH,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.CAMERA,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Поднимаем coordinator + регистрируем платформу media_player."""
    coordinator = SboomCoordinator(hass, entry)
    await coordinator.async_start()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # Регистрация custom services (идемпотентно — несколько entries безопасны).
    async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Очистка при удалении."""
    coordinator: SboomCoordinator | None = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if coordinator is not None:
        await coordinator.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Миграция config entry между MAJOR.MINOR версиями.

    Сейчас актуальная версия: VERSION=1, MINOR_VERSION=1 — миграции не требуются.
    При появлении изменения формата (data → options перенос или добавление полей)
    добавлять блоки `if version == 1: data = {...}; entry.minor_version = 2`.
    """
    _LOGGER.debug(
        "migrating config entry from version %s.%s",
        entry.version,
        entry.minor_version,
    )
    # Будущие миграции — здесь.
    return True
