"""Panel Bluetooth/history adapters, user scope and confirmed playback."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sboom_ha import websocket_api as api

from tests._fakes import build_coordinator


def connection(user_id="alice"):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id), send_result=MagicMock(), send_error=MagicMock()
    )


@pytest.mark.asyncio
async def test_bluetooth_handler_targets_selected_coordinator():
    coord = build_coordinator()
    snapshot = {"paired": [{"mac": "AA:BB:CC:DD:EE:FF", "name": "Phone", "connected": True}], "scanned": []}
    coord.async_bluetooth = AsyncMock(return_value=snapshot)
    conn = connection()
    await api.ws_bluetooth(coord.hass, conn, {"id": 1, "action": "status", "entry_id": coord.entry.entry_id})
    coord.async_bluetooth.assert_awaited_once_with("status", None)
    conn.send_result.assert_called_once_with(1, snapshot)


@pytest.mark.asyncio
async def test_bluetooth_handler_rejects_unknown_entry():
    coord = build_coordinator()
    conn = connection()
    await api.ws_bluetooth(coord.hass, conn, {"id": 2, "action": "scan", "entry_id": "missing"})
    assert conn.send_error.call_args.args[1] == "not_loaded"


@pytest.mark.asyncio
@pytest.mark.parametrize("error,code", [(ValueError("Bad MAC"), "invalid_args"), (TimeoutError("Scan timed out"), "command_failed")])
async def test_bluetooth_handler_reports_errors(error, code):
    coord = build_coordinator()
    coord.async_bluetooth = AsyncMock(side_effect=error)
    conn = connection()
    await api.ws_bluetooth(coord.hass, conn, {"id": 3, "action": "connect", "mac_address": "bad"})
    assert conn.send_error.call_args.args[1] == code
    conn.send_result.assert_not_called()


@pytest.mark.asyncio
async def test_history_handlers_use_authenticated_user_not_payload(monkeypatch):
    coord = build_coordinator()
    history = SimpleNamespace(async_list=AsyncMock(return_value=[{"id": "42"}]), async_clear=AsyncMock())
    monkeypatch.setattr(api, "async_get_play_history", AsyncMock(return_value=history), raising=False)
    conn = connection()
    await api.ws_history(coord.hass, conn, {"id": 4, "order": "frequent", "user_id": "bob"})
    history.async_list.assert_awaited_once_with("alice", "frequent")
    conn.send_result.assert_called_with(4, {"items": [{"id": "42"}]})
    await api.ws_history_clear(coord.hass, conn, {"id": 5, "user_id": "bob"})
    history.async_clear.assert_awaited_once_with("alice")


@pytest.mark.asyncio
async def test_history_requires_authenticated_user():
    coord = build_coordinator()
    conn = connection(None)
    await api.ws_history(coord.hass, conn, {"id": 6})
    assert conn.send_error.call_args.args[1] == "unauthorized"


@pytest.mark.asyncio
@pytest.mark.parametrize("accepted", [True, False])
async def test_play_records_history_only_after_speaker_accepts(monkeypatch, accepted):
    coord = build_coordinator()
    play = AsyncMock(return_value=accepted)
    record = AsyncMock()
    monkeypatch.setattr(api, "play_deeplink", play)
    monkeypatch.setattr(api, "async_record_play", record, raising=False)
    conn = connection()
    metadata = {"title": "A song", "subtitle": "Artist"}
    await api.ws_play(coord.hass, conn, {"id": 7, "content_id": "42", "kind": "track", "history_item": metadata})
    if accepted:
        record.assert_awaited_once_with(coord.hass, "alice", "staros://music?tid=42&pt=track", metadata)
        assert conn.send_result.call_args.args[1]["success"] is True
    else:
        record.assert_not_awaited()
        assert conn.send_error.call_args.args[1] == "command_rejected"


@pytest.mark.asyncio
async def test_play_success_survives_history_storage_failure(monkeypatch):
    coord = build_coordinator()
    monkeypatch.setattr(api, "play_deeplink", AsyncMock(return_value=True))
    monkeypatch.setattr(api, "async_record_play", AsyncMock(side_effect=OSError("disk full")), raising=False)
    conn = connection()
    await api.ws_play(coord.hass, conn, {"id": 8, "content_id": "42", "kind": "track"})
    assert conn.send_result.call_args.args[1]["success"] is True
    conn.send_error.assert_not_called()
