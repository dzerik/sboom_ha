"""Регистрация custom services интеграции."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr

from .const import BT_CMD_CONNECT, BT_CMD_DISCONNECT, BT_CMD_REMOVE, DOMAIN
from .coordinator import SboomCoordinator

_LOGGER = logging.getLogger(__name__)

SERVICE_REFRESH_METADATA = "refresh_metadata"
SERVICE_REAUTH = "reauth"
SERVICE_BT_DEVICE = "bluetooth_device"

_BT_CMD_MAP = {
    "connect": BT_CMD_CONNECT,
    "disconnect": BT_CMD_DISCONNECT,
    "remove": BT_CMD_REMOVE,
}


def _coords_from_call(hass: HomeAssistant, call: ServiceCall) -> list[SboomCoordinator]:
    """Извлечь все SboomCoordinator'ы, на которые таргетится service-call.

    Поддерживает device_id-таргетинг — найдём entry'и принадлежащие нашим device'ам
    и вернём их coordinator'ы. Если ничего не таргетится — возвращаем все.
    """
    device_ids: list[str] = call.data.get("device_id", []) or []
    if isinstance(device_ids, str):
        device_ids = [device_ids]

    all_coords: dict[str, SboomCoordinator] = hass.data.get(DOMAIN, {})

    if not device_ids:
        return list(all_coords.values())

    device_reg = dr.async_get(hass)
    selected: list[SboomCoordinator] = []
    for did in device_ids:
        device = device_reg.async_get(did)
        if not device:
            continue
        for entry_id in device.config_entries:
            if entry_id in all_coords:
                selected.append(all_coords[entry_id])
    return selected


async def _handle_refresh_metadata(hass: HomeAssistant, call: ServiceCall) -> None:
    coords = _coords_from_call(hass, call)
    for coord in coords:
        try:
            await coord.async_request_refresh()
        except Exception:
            _LOGGER.exception("refresh_metadata failed for %s", coord.entry.entry_id)


async def _handle_reauth(hass: HomeAssistant, call: ServiceCall) -> None:
    coords = _coords_from_call(hass, call)
    for coord in coords:
        try:
            coord.entry.async_start_reauth(hass)
        except Exception:
            _LOGGER.exception("reauth failed for %s", coord.entry.entry_id)


async def _handle_bt_device(hass: HomeAssistant, call: ServiceCall) -> None:
    mac = call.data.get("mac_address")
    cmd = _BT_CMD_MAP.get(call.data.get("command", ""))
    if not mac or cmd is None:
        _LOGGER.error("bluetooth_device: требуются mac_address и валидный command")
        return
    for coord in _coords_from_call(hass, call):
        try:
            await coord.client.bt_device_command(mac, cmd)
        except Exception:
            _LOGGER.exception("bluetooth_device failed for %s", coord.entry.entry_id)


def async_register_services(hass: HomeAssistant) -> None:
    """Регистрация служб. Идемпотентно — несколько entries вызывают её безопасно."""
    if hass.services.has_service(DOMAIN, SERVICE_REFRESH_METADATA):
        return

    async def _refresh_handler(call: ServiceCall) -> None:
        await _handle_refresh_metadata(hass, call)

    async def _reauth_handler(call: ServiceCall) -> None:
        await _handle_reauth(hass, call)

    async def _bt_device_handler(call: ServiceCall) -> None:
        await _handle_bt_device(hass, call)

    hass.services.async_register(DOMAIN, SERVICE_REFRESH_METADATA, _refresh_handler)
    hass.services.async_register(DOMAIN, SERVICE_REAUTH, _reauth_handler)
    hass.services.async_register(DOMAIN, SERVICE_BT_DEVICE, _bt_device_handler)
