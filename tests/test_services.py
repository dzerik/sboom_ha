"""Тесты custom services: refresh_metadata, reauth."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sboom_ha.const import DOMAIN
from sboom_ha.services import (
    SERVICE_REAUTH,
    SERVICE_REFRESH_METADATA,
    async_register_services,
)

from tests._fakes import build_coordinator, make_state, make_track
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
