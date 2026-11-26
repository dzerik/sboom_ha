"""Регистрация custom services интеграции.

Сервисы регистрируются в async_setup (IQS action-setup): существуют всегда,
даже когда entry не загружен — невалидные вызовы падают с
ServiceValidationError, а не с «service not found».
"""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import (
    HomeAssistantError,
    ServiceValidationError,
    Unauthorized,
    UnknownUser,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from websockets.exceptions import ConnectionClosed

from ._deeplink import play_deeplink, send_server_action
from ._ha_helpers import get_zvuk_client, iter_coordinators
from .const import BT_CMD_CONNECT, BT_CMD_DISCONNECT, BT_CMD_REMOVE, DOMAIN, OPT_ENABLE_TV_CONTROLS
from .coordinator import SboomCoordinator
from .play_history import async_record_play
from .zvuk_client import ZvukClient

_LOGGER = logging.getLogger(__name__)

SERVICE_REFRESH_METADATA = "refresh_metadata"
SERVICE_REAUTH = "reauth"
SERVICE_BT_DEVICE = "bluetooth_device"
SERVICE_PLAY_MUSIC = "play_music"
SERVICE_TV_ACTION = "tv_action"

_BT_CMD_MAP = {
    "connect": BT_CMD_CONNECT,
    "disconnect": BT_CMD_DISCONNECT,
    "remove": BT_CMD_REMOVE,
}
TV_ACTION_LINKS = {
    "show_inputs": "livetv://hdmi",
    "next_input": "action://sberdevices.intent.action.TV_INPUT",
    "channel_up": "action://sberdevices.intent.action.CHANNEL_NEXT",
    "channel_down": "action://sberdevices.intent.action.CHANNEL_PREVIEW",
    "settings": "sberdevices_app_v2://settings",
}

# device_id добавляет HA-таргетинг (`target:` в services.yaml) — сервис может
# прийти со строкой или списком. Схемы отклоняют мусор до входа в handler.
_TARGET_SCHEMA = {
    vol.Optional("device_id"): vol.Any(str, [str]),
}
SCHEMA_REFRESH_METADATA = vol.Schema(_TARGET_SCHEMA)
SCHEMA_REAUTH = vol.Schema(_TARGET_SCHEMA)
SCHEMA_BT_DEVICE = vol.Schema(
    {
        **_TARGET_SCHEMA,
        vol.Required("mac_address"): str,
        vol.Required("command"): vol.In(sorted(_BT_CMD_MAP)),
    }
)
# play_music: один из источников — url (zvuk.com/... или готовый staros://),
# id (+ kind), или query (поиск по названию в Звуке).
SCHEMA_PLAY_MUSIC = vol.Schema(
    {
        **_TARGET_SCHEMA,
        vol.Optional("url"): str,
        vol.Optional("query"): str,
        vol.Optional("id"): str,
        vol.Optional("kind"): vol.In(
            ["track", "artist", "release", "playlist", "podcast", "abook"]
        ),
    }
)
SCHEMA_TV_ACTION = vol.Schema(
    {
        vol.Required("device_id"): str,
        vol.Required("command"): vol.In(sorted(TV_ACTION_LINKS)),
    }
)


def _loaded_coordinators(hass: HomeAssistant) -> dict[str, SboomCoordinator]:
    """entry_id → coordinator для всех загруженных entries интеграции."""
    return {entry.entry_id: coord for entry, coord in iter_coordinators(hass)}


def _coords_from_call(
    hass: HomeAssistant, call: ServiceCall, *, no_match_key: str = "no_loaded_speakers"
) -> list[SboomCoordinator]:
    """Извлечь все SboomCoordinator'ы, на которые таргетится service-call.

    Поддерживает device_id-таргетинг. Если таргет не указан — все загруженные.
    Пустой результат — ошибка пользователя (entry не загружен или неверный
    device_id), о которой надо сказать явно, а не молча сделать ничего.
    """
    device_ids: list[str] | str = call.data.get("device_id", []) or []
    if isinstance(device_ids, str):
        device_ids = [device_ids]

    all_coords = _loaded_coordinators(hass)

    if not device_ids:
        selected = list(all_coords.values())
    else:
        device_reg = dr.async_get(hass)
        selected = []
        for did in device_ids:
            device = device_reg.async_get(did)
            if not device:
                continue
            for entry_id in device.config_entries:
                if entry_id in all_coords:
                    selected.append(all_coords[entry_id])

    if not selected:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key=no_match_key,
        )
    return selected


async def _handle_refresh_metadata(hass: HomeAssistant, call: ServiceCall) -> None:
    for coord in _coords_from_call(hass, call):
        await coord.async_request_refresh()


async def _handle_reauth(hass: HomeAssistant, call: ServiceCall) -> None:
    for coord in _coords_from_call(hass, call):
        coord.entry.async_start_reauth(hass)


async def _handle_bt_device(hass: HomeAssistant, call: ServiceCall) -> None:
    mac = call.data["mac_address"]
    cmd = _BT_CMD_MAP[call.data["command"]]
    for coord in _coords_from_call(hass, call):
        try:
            await coord.client.bt_device_command(mac, cmd)
        except (RuntimeError, TimeoutError, ConnectionError, OSError) as exc:
            # Транспортные сбои → HomeAssistantError: внятное сообщение в UI
            # вместо сырого traceback «Unknown error».
            raise HomeAssistantError(
                f"колонка {coord.client.host} не выполнила BT-команду: {exc}"
            ) from exc


async def _resolve_deeplink(hass: HomeAssistant, data: dict) -> str:
    """url / id+kind / query → staros://music-deeplink."""
    url = data.get("url")
    if url:
        if url.startswith("staros://"):
            return url  # готовый deeplink
        parsed = ZvukClient.parse_zvuk_url(url, None)
        if not parsed:
            raise ServiceValidationError(f"не удалось разобрать Звук-URL: {url}")
        return ZvukClient.build_deeplink(*parsed)

    if data.get("id"):
        parsed = ZvukClient.parse_zvuk_url(data["id"], data.get("kind"))
        if not parsed:
            raise ServiceValidationError(
                "для id укажите kind (track/artist/release/playlist/podcast)"
            )
        return ZvukClient.build_deeplink(*parsed)

    query = data.get("query")
    if query:
        deeplink = await get_zvuk_client(hass).search_first_deeplink(query)
        if deeplink:
            return deeplink
        raise ServiceValidationError(
            f"Звук: по запросу «{query}» ничего не найдено (или поиск недоступен)"
        )

    raise ServiceValidationError(
        "play_music: укажите один из источников — url, id или query"
    )


async def _handle_play_music(hass: HomeAssistant, call: ServiceCall) -> None:
    deeplink = await _resolve_deeplink(hass, call.data)
    recorded = False
    for coord in _coords_from_call(hass, call):
        try:
            accepted = await play_deeplink(coord.client, deeplink)
        except (RuntimeError, TimeoutError, ConnectionError, OSError) as exc:
            raise HomeAssistantError(
                f"колонка {coord.client.host} недоступна для play_music: {exc}"
            ) from exc
        if not accepted:
            raise ServiceValidationError(
                f"колонка отклонила deeplink: {deeplink}"
            )
        if not recorded:
            context = getattr(call, "context", None)
            await async_record_play(hass, getattr(context, "user_id", None), deeplink)
            recorded = True


async def _handle_tv_action(hass: HomeAssistant, call: ServiceCall) -> None:
    """Отправить действие одному сопряжённому телевизору."""
    if not call.data.get("device_id", "").strip():
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="tv_device_required")
    user_id = call.context.user_id
    user = await hass.auth.async_get_user(user_id) if user_id else None
    if user_id and user is None:
        raise UnknownUser(context=call.context)

    coords = _coords_from_call(hass, call, no_match_key="tv_device_required")
    if len(coords) != 1:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="tv_device_required")

    coord = coords[0]
    if user is not None:
        entries = er.async_entries_for_device(
            er.async_get(hass), call.data["device_id"], include_disabled_entities=True,
        )
        if not any(
            entity.config_entry_id == coord.entry.entry_id
            and entity.platform == DOMAIN
            and entity.domain == "media_player"
            and user.permissions.check_entity(entity.entity_id, POLICY_CONTROL)
            for entity in entries
        ):
            raise Unauthorized(context=call.context, permission=POLICY_CONTROL)
    if not coord.entry.options.get(OPT_ENABLE_TV_CONTROLS, False):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="tv_controls_disabled"
        )
    client = coord.client
    if not client.pin_access_token:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="tv_not_paired")

    deeplink = TV_ACTION_LINKS[call.data["command"]]
    try:
        result = await send_server_action(client, "DEEPLINK", {"deeplink": deeplink})
    except TimeoutError as exc:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="tv_action_timeout"
        ) from exc
    except (OSError, RuntimeError, ConnectionClosed) as exc:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="tv_connection_unavailable"
        ) from exc

    description = result.get(1) if isinstance(result, dict) else result
    if not isinstance(description, str) or description.strip() != "OK":
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="tv_action_unconfirmed")


def async_register_services(hass: HomeAssistant) -> None:
    """Регистрация служб (однократно, из async_setup)."""
    if hass.services.has_service(DOMAIN, SERVICE_REFRESH_METADATA):
        return

    async def _refresh_handler(call: ServiceCall) -> None:
        await _handle_refresh_metadata(hass, call)

    async def _reauth_handler(call: ServiceCall) -> None:
        await _handle_reauth(hass, call)

    async def _bt_device_handler(call: ServiceCall) -> None:
        await _handle_bt_device(hass, call)

    async def _play_music_handler(call: ServiceCall) -> None:
        await _handle_play_music(hass, call)

    async def _tv_action_handler(call: ServiceCall) -> None:
        await _handle_tv_action(hass, call)

    hass.services.async_register(
        DOMAIN, SERVICE_REFRESH_METADATA, _refresh_handler, schema=SCHEMA_REFRESH_METADATA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_REAUTH, _reauth_handler, schema=SCHEMA_REAUTH
    )
    hass.services.async_register(
        DOMAIN, SERVICE_BT_DEVICE, _bt_device_handler, schema=SCHEMA_BT_DEVICE
    )
    hass.services.async_register(
        DOMAIN, SERVICE_PLAY_MUSIC, _play_music_handler, schema=SCHEMA_PLAY_MUSIC
    )
    hass.services.async_register(
        DOMAIN, SERVICE_TV_ACTION, _tv_action_handler, schema=SCHEMA_TV_ACTION
    )
