"""Тесты действий интеграции Home Assistant."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import voluptuous as vol
from homeassistant.exceptions import (
    HomeAssistantError,
    ServiceValidationError,
    Unauthorized,
    UnknownUser,
)
from homeassistant.helpers import device_registry as dr
from sboom_ha import services as sboom_services
from sboom_ha.const import DOMAIN
from sboom_ha.services import (
    SERVICE_REAUTH,
    SERVICE_REFRESH_METADATA,
    async_register_services,
)
from websockets.exceptions import ConnectionClosedError

from tests._fakes import build_coordinator, make_entry, make_state, make_track
from tests._ha_stubs import HomeAssistant, ServiceCall


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id", ["alice", None])
async def test_play_music_records_one_launch_for_multiple_targets(monkeypatch, user_id):
    from sboom_ha import services
    from sboom_ha.play_history import async_get_play_history

    coord = build_coordinator(track=make_track(), state=make_state())
    monkeypatch.setattr(services, "_coords_from_call", lambda hass, call: [coord, coord])
    monkeypatch.setattr(services, "play_deeplink", AsyncMock(return_value=True))
    call = ServiceCall(data={"url": "staros://music?tid=55&pt=track"})
    if user_id:
        call.context = SimpleNamespace(user_id=user_id)
    await services._handle_play_music(coord.hass, call)
    history = await async_get_play_history(coord.hass)
    items = await history.async_list(user_id or "system")
    assert [(item["id"], item["count"]) for item in items] == [("55", 1)]
    if user_id:
        assert await history.async_list("system") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [False, TimeoutError("offline")])
async def test_failed_play_music_does_not_enter_history(monkeypatch, result):
    from homeassistant.exceptions import HomeAssistantError
    from sboom_ha import services
    from sboom_ha.play_history import async_get_play_history

    coord = build_coordinator(track=make_track(), state=make_state())
    if isinstance(result, Exception):
        play = AsyncMock(side_effect=result)
    else:
        play = AsyncMock(return_value=result)
    monkeypatch.setattr(services, "play_deeplink", play)
    call = ServiceCall(data={"url": "staros://music?tid=55&pt=track"})
    with pytest.raises(HomeAssistantError):
        await services._handle_play_music(coord.hass, call)
    assert await (await async_get_play_history(coord.hass)).async_list("system") == []


@pytest.mark.asyncio
async def test_play_music_preserves_accepted_launch_before_later_target_failure(monkeypatch):
    from homeassistant.exceptions import HomeAssistantError
    from sboom_ha import services
    from sboom_ha.play_history import async_get_play_history

    coord = build_coordinator(track=make_track(), state=make_state())
    monkeypatch.setattr(services, "_coords_from_call", lambda hass, call: [coord, coord])
    monkeypatch.setattr(services, "play_deeplink", AsyncMock(side_effect=[True, False]))
    call = ServiceCall(data={"url": "staros://music?tid=55&pt=track"})
    with pytest.raises(HomeAssistantError):
        await services._handle_play_music(coord.hass, call)
    items = await (await async_get_play_history(coord.hass)).async_list("system")
    assert [(item["id"], item["count"]) for item in items] == [("55", 1)]


@pytest.mark.asyncio
async def test_play_music_remains_successful_when_history_save_fails(monkeypatch):
    from sboom_ha import services
    from sboom_ha.play_history import async_get_play_history

    coord = build_coordinator(track=make_track(), state=make_state())
    history = await async_get_play_history(coord.hass)
    monkeypatch.setattr(history._store, "async_save", AsyncMock(side_effect=OSError("disk full")))
    monkeypatch.setattr(services, "play_deeplink", AsyncMock(return_value=True))
    call = ServiceCall(data={"url": "staros://music?tid=55&pt=track"})
    assert await services._handle_play_music(coord.hass, call) is None
    assert await history.async_list("system") == []


def _setup_hass_with_coord(coord) -> HomeAssistant:
    # build_coordinator уже прописал entry.runtime_data и config_entries.
    return coord.hass


# ─────────────────── registration ───────────────────


def test_register_services_idempotent():
    hass = HomeAssistant()
    async_register_services(hass)
    assert hass.services.has_service(DOMAIN, SERVICE_REFRESH_METADATA)
    assert hass.services.has_service(DOMAIN, SERVICE_REAUTH)
    # Повторный вызов не падает
    async_register_services(hass)
    assert hass.services.has_service(DOMAIN, SERVICE_REFRESH_METADATA)


# ─────────────────── refresh_metadata ───────────────────


@pytest.mark.asyncio
async def test_refresh_metadata_calls_request_refresh_on_all_when_no_target():
    coord = build_coordinator(track=make_track(), state=make_state())
    coord.async_request_refresh = AsyncMock()
    hass = _setup_hass_with_coord(coord)
    async_register_services(hass)

    handler = hass.services._registered[(DOMAIN, SERVICE_REFRESH_METADATA)]
    await handler(ServiceCall(domain=DOMAIN, service=SERVICE_REFRESH_METADATA, data={}))

    coord.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_refresh_metadata_propagates_errors_to_caller():
    """Новый контракт: ошибка команды видна пользователю, а не глотается молча."""
    coord = build_coordinator(track=make_track(), state=make_state())
    coord.async_request_refresh = AsyncMock(side_effect=RuntimeError("WS dead"))
    hass = _setup_hass_with_coord(coord)
    async_register_services(hass)

    handler = hass.services._registered[(DOMAIN, SERVICE_REFRESH_METADATA)]
    with pytest.raises(RuntimeError):
        await handler(ServiceCall(data={}))


@pytest.mark.asyncio
async def test_service_call_without_loaded_entries_raises_validation_error():
    """Без загруженных entries сервис падает с ServiceValidationError
    (IQS action-setup), а не молча делает ничего."""
    from tests._ha_stubs import ServiceValidationError

    hass = HomeAssistant()
    async_register_services(hass)

    handler = hass.services._registered[(DOMAIN, SERVICE_REFRESH_METADATA)]
    with pytest.raises(ServiceValidationError):
        await handler(ServiceCall(data={}))


# ─────────────────── reauth ───────────────────


@pytest.mark.asyncio
async def test_reauth_triggers_async_start_reauth():
    coord = build_coordinator(track=make_track(), state=make_state())
    coord.entry.async_start_reauth = MagicMock()
    hass = _setup_hass_with_coord(coord)
    async_register_services(hass)

    handler = hass.services._registered[(DOMAIN, SERVICE_REAUTH)]
    await handler(ServiceCall(data={}))

    coord.entry.async_start_reauth.assert_called_once_with(hass)


# ────────── сетевые ошибки колонки → HomeAssistantError (аудит #37) ──────────


@pytest.mark.asyncio
async def test_play_music_wraps_speaker_errors(monkeypatch):
    """TimeoutError от колонки не должен всплывать сырым traceback'ом в UI."""
    from homeassistant.exceptions import HomeAssistantError
    from sboom_ha import services

    coord = build_coordinator(track=make_track(), state=make_state())

    async def boom(client, deeplink):
        raise TimeoutError("no answer")

    monkeypatch.setattr(services, "play_deeplink", boom)
    call = ServiceCall(data={"url": "https://zvuk.com/track/55"})
    with pytest.raises(HomeAssistantError):
        await services._handle_play_music(coord.hass, call)


@pytest.mark.asyncio
async def test_bluetooth_device_wraps_speaker_errors():
    from homeassistant.exceptions import HomeAssistantError
    from sboom_ha import services

    coord = build_coordinator(track=make_track(), state=make_state())

    async def boom(mac, cmd):
        raise RuntimeError("not connected")

    coord.client.bt_device_command = boom
    call = ServiceCall(data={"mac_address": "AA:BB:CC:DD:EE:FF", "command": "connect"})
    with pytest.raises(HomeAssistantError):
        await services._handle_bt_device(coord.hass, call)


@pytest.fixture
def tv_target():
    entry = make_entry(entry_id="tv-entry")
    entry.options["enable_tv_controls"] = True
    tv = build_coordinator(entry=entry)
    registry = dr.async_get(tv.hass)
    registry.register(SimpleNamespace(id="tv-device", config_entries={entry.entry_id}))
    async_register_services(tv.hass)
    return tv, registry


@pytest.mark.parametrize(
    ("command", "link", "ack"),
    [
        ("show_inputs", "livetv://hdmi", {1: "OK"}),
        ("next_input", "action://sberdevices.intent.action.TV_INPUT", {1: "OK"}),
        ("channel_up", "action://sberdevices.intent.action.CHANNEL_NEXT", {1: "OK"}),
        ("channel_down", "action://sberdevices.intent.action.CHANNEL_PREVIEW", {1: "OK"}),
        ("settings", "sberdevices_app_v2://settings", {1: "OK"}),
        ("show_inputs", "livetv://hdmi", "OK"),
    ],
)
async def test_tv_action_sends_one_deeplink_to_the_selected_device(
    tv_target, monkeypatch, command, link, ack
):
    tv, registry = tv_target
    speaker = build_coordinator(hass=tv.hass, entry=make_entry(entry_id="speaker-entry"))
    speaker.entry.options["enable_tv_controls"] = True
    registry.register(
        SimpleNamespace(id="speaker-device", config_entries={speaker.entry.entry_id})
    )
    # Регистрация при повторном setup остаётся идемпотентной.
    key = (DOMAIN, "tv_action")
    handler = tv.hass.services._registered[key]
    async_register_services(tv.hass)
    assert tv.hass.services._registered[key] is handler
    send = AsyncMock(return_value=ack)
    monkeypatch.setattr(sboom_services, "send_server_action", send)
    data = tv.hass.services._schemas[key]({"device_id": "tv-device", "command": command})

    await handler(ServiceCall(data=data))

    send.assert_awaited_once_with(tv.client, "DEEPLINK", {"deeplink": link})


@pytest.mark.parametrize("result", [{1: "NOT OK"}, {}, {1: 0}, ["OK"]])
async def test_tv_action_rejects_unconfirmed_responses(tv_target, monkeypatch, result):
    tv, _ = tv_target
    handler = tv.hass.services._registered[(DOMAIN, "tv_action")]
    send = AsyncMock(return_value=result)
    monkeypatch.setattr(sboom_services, "send_server_action", send)

    with pytest.raises(HomeAssistantError):
        await handler(ServiceCall(data={"device_id": "tv-device", "command": "show_inputs"}))

    send.assert_awaited_once()


@pytest.mark.parametrize(
    "error",
    [TimeoutError(), OSError(), RuntimeError(), ConnectionClosedError(None, None)],
)
async def test_tv_action_wraps_transport_errors_without_retry(tv_target, monkeypatch, error):
    tv, _ = tv_target
    handler = tv.hass.services._registered[(DOMAIN, "tv_action")]
    send = AsyncMock(side_effect=error)
    monkeypatch.setattr(sboom_services, "send_server_action", send)

    with pytest.raises(HomeAssistantError) as exc_info:
        await handler(ServiceCall(data={"device_id": "tv-device", "command": "next_input"}))

    assert exc_info.value.__cause__ is error
    send.assert_awaited_once()


@pytest.mark.parametrize(
    "scenario",
    ["empty", "whitespace", "unknown", "unloaded", "ambiguous", "disabled", "no-token"],
)
async def test_tv_action_rejects_invalid_targets_without_sending(tv_target, monkeypatch, scenario):
    tv, registry = tv_target
    handler = tv.hass.services._registered[(DOMAIN, "tv_action")]
    send = AsyncMock()
    monkeypatch.setattr(sboom_services, "send_server_action", send)
    device_id = "tv-device"
    if scenario in {"empty", "whitespace", "unknown"}:
        device_id = {"empty": "", "whitespace": " ", "unknown": "unknown-device"}[scenario]
    elif scenario == "unloaded":
        tv.entry.runtime_data = None
    elif scenario == "ambiguous":
        other = build_coordinator(hass=tv.hass, entry=make_entry(entry_id="other-entry"))
        registry.register(
            SimpleNamespace(id=device_id, config_entries={tv.entry.entry_id, other.entry.entry_id})
        )
    elif scenario == "disabled":
        tv.entry.options.clear()
    elif scenario == "no-token":
        tv.client.pin_access_token = ""

    with pytest.raises(ServiceValidationError) as err:
        await handler(ServiceCall(data={"device_id": device_id, "command": "show_inputs"}))

    send.assert_not_awaited()
    if scenario in {"unknown", "unloaded"}:
        assert err.value.translation_key == "tv_device_required"


@pytest.mark.parametrize("access", ["allowed", "read-only", "other-entry", "unknown-user"])
async def test_tv_action_requires_control_of_selected_player(tv_target, monkeypatch, access):
    tv, _ = tv_target
    player_id = "media_player.renamed_tv"
    registry_entry = SimpleNamespace(
        domain="media_player", platform=DOMAIN, entity_id=player_id,
        config_entry_id="other" if access == "other-entry" else tv.entry.entry_id,
    )
    tv.hass.data["entity_registry"] = {"tv-device": [registry_entry]}
    check = MagicMock(return_value=access != "read-only")
    user = SimpleNamespace(permissions=SimpleNamespace(check_entity=check))
    tv.hass.auth = SimpleNamespace(async_get_user=AsyncMock(
        return_value=None if access == "unknown-user" else user
    ))
    send = AsyncMock(return_value={1: "OK"})
    monkeypatch.setattr(sboom_services, "send_server_action", send)
    handler = tv.hass.services._registered[(DOMAIN, "tv_action")]
    call = ServiceCall(data={"device_id": "tv-device", "command": "show_inputs"},
                       context=SimpleNamespace(user_id="viewer"))

    if access == "allowed":
        await handler(call)
        send.assert_awaited_once()
    else:
        with pytest.raises(UnknownUser if access == "unknown-user" else Unauthorized):
            await handler(call)
        send.assert_not_awaited()
    if access in {"allowed", "read-only"}:
        check.assert_called_once_with(player_id, "control")


@pytest.mark.parametrize(
    "data",
    [
        {"command": "show_inputs"},
        {"device_id": ["tv-device", "speaker-device"], "command": "show_inputs"},
        {"device_id": "tv-device"},
        {"device_id": "tv-device", "command": "unknown"},
    ],
)
def test_tv_action_schema_requires_one_device_and_a_known_command(tv_target, data):
    tv, _ = tv_target
    schema = tv.hass.services._schemas[(DOMAIN, "tv_action")]

    with pytest.raises(vol.Invalid):
        schema(data)
