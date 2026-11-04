"""Actual speaker playback must enter history without any panel play command."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from sboom_ha import websocket_api as api
from sboom_ha._models import TrackInfo

from tests._fakes import build_coordinator, make_entry, make_state, make_track
from tests._ha_stubs import HomeAssistant
from tests.test_panel_features import connection
from tests.test_play_history import history_module


@pytest.fixture(autouse=True)
async def history_background_tasks(monkeypatch):
    tasks = []

    def create_task(self, coro, name=None):
        task = asyncio.create_task(coro, name=name)
        tasks.append(task)
        return task

    monkeypatch.setattr(HomeAssistant, "async_create_background_task", create_task)
    yield
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def observe(coord, *payload):
    await coord._handle_event(*payload)
    await coord.async_flush_listening()


def metadata(track_id, title="Song", playing=True):
    return json.dumps({"trackId": track_id, "title": title, "playing": playing,
                       "artists": [{"id": "1", "name": "Artist"}], "provider": "zvuk"}).encode(), {5: {10: True}}


@pytest.mark.asyncio
async def test_speaker_push_records_actual_song_without_panel_launch(monkeypatch):
    module, _ = history_module(monkeypatch)
    coord = build_coordinator(state=make_state())
    coord._maybe_fetch_lyrics = MagicMock()
    await observe(coord, *metadata("57767387", "Another One Bites The Dust"))
    history = await module.async_get_play_history(coord.hass)
    items = await history.async_list("speaker:" + coord.entry.entry_id)
    assert [(item["id"], item["title"], item["subtitle"]) for item in items] == [
        ("57767387", "Another One Bites The Dust", "Artist")
    ]
    assert await history.async_list("alice") == []


@pytest.mark.asyncio
async def test_listening_deduplicates_updates_resume_and_counts_return_to_song(monkeypatch):
    module, _ = history_module(monkeypatch)
    coord = build_coordinator(state=make_state())
    coord._maybe_fetch_lyrics = MagicMock()
    for track_id, playing in [("1", True), ("1", True), ("1", False), ("1", True), ("2", False), ("2", True), ("1", True)]:
        await observe(coord, *metadata(track_id, playing=playing))
    items = await (await module.async_get_play_history(coord.hass)).async_list("speaker:" + coord.entry.entry_id)
    assert [(i["id"], i["count"]) for i in items] == [("1", 2), ("2", 1)]


@pytest.mark.asyncio
async def test_poll_seeds_currently_playing_song_on_startup(monkeypatch):
    module, _ = history_module(monkeypatch)
    coord = build_coordinator(state=make_state())
    coord._maybe_fetch_lyrics = MagicMock()
    coord.client.get_state = AsyncMock(return_value=make_state())
    coord.client.get_metadata = AsyncMock(return_value=make_track(track_id="42", playing=True))
    coord.client.get_paired_bt_devices = AsyncMock(return_value=[])
    await coord._refresh_state_and_track()
    await coord._refresh_state_and_track()
    await coord.async_flush_listening()
    items = await (await module.async_get_play_history(coord.hass)).async_list("speaker:" + coord.entry.entry_id)
    assert [(i["id"], i["count"]) for i in items] == [("42", 1)]


@pytest.mark.asyncio
async def test_paused_metadata_is_not_a_listen_and_store_failure_is_retryable(monkeypatch):
    module, store = history_module(monkeypatch)
    coord = build_coordinator(state=make_state())
    coord._maybe_fetch_lyrics = MagicMock()
    await observe(coord, *metadata("42", playing=False))
    history = await module.async_get_play_history(coord.hass)
    scope = "speaker:" + coord.entry.entry_id
    assert await history.async_list(scope) == []
    store.fail_save = True
    await observe(coord, *metadata("42"))
    assert coord.track.track_id == "42"
    assert await history.async_list(scope) == []
    store.fail_save = False
    await observe(coord, *metadata("42"))
    assert len(await history.async_list(scope)) == 1


@pytest.mark.asyncio
async def test_history_api_keeps_listens_per_speaker_and_launches_private(monkeypatch):
    module, _ = history_module(monkeypatch)
    coord = build_coordinator(state=make_state())
    coord._maybe_fetch_lyrics = MagicMock()
    other = build_coordinator(entry=make_entry(entry_id="other"), hass=coord.hass, state=make_state())
    other._maybe_fetch_lyrics = MagicMock()
    await observe(coord, *metadata("1"))
    await observe(other, *metadata("2"))
    await module.async_record_play(coord.hass, "alice", "staros://music?tid=99&pt=track")
    conn = connection()
    await api.ws_history(coord.hass, conn, {"id": 1, "scope": "played", "entry_id": coord.entry.entry_id})
    assert [i["id"] for i in conn.send_result.call_args.args[1]["items"]] == ["1"]
    await api.ws_history_clear(coord.hass, conn, {"id": 2, "scope": "played", "entry_id": coord.entry.entry_id})
    history = await module.async_get_play_history(coord.hass)
    assert await history.async_list("speaker:" + coord.entry.entry_id) == []
    assert [i["id"] for i in await history.async_list("speaker:other")] == ["2"]
    assert [i["id"] for i in await history.async_list("alice")] == ["99"]


@pytest.mark.asyncio
async def test_non_catalog_bluetooth_and_radio_are_visible_without_false_replay(monkeypatch):
    module, _ = history_module(monkeypatch)
    coord = build_coordinator(state=make_state())
    coord._maybe_fetch_lyrics = MagicMock()
    for source in ("BLUETOOTH", "RADIO"):
        coord.client.get_state = AsyncMock(return_value=make_state())
        coord.client.get_metadata = AsyncMock(return_value=TrackInfo(title="Song", artists=["Artist"], media_source=source, playing=True))
        coord.client.get_paired_bt_devices = AsyncMock(return_value=[])
        await coord._refresh_state_and_track()
        await coord.async_flush_listening()
    items = await (await module.async_get_play_history(coord.hass)).async_list("speaker:" + coord.entry.entry_id)
    assert {i["kind"] for i in items} == {"bluetooth", "radio"}
    assert all(i["playable"] is False for i in items)


@pytest.mark.asyncio
async def test_listening_retains_public_zvuk_cover_query(monkeypatch):
    module, _ = history_module(monkeypatch)
    history = await module.async_get_play_history(build_coordinator().hass)
    await history.async_record("speaker:test", {"kind": "track", "id": "42", "cover_url": "https://cdn-image.zvuk.com/pic?type=release&id=7&size=600x600&token=secret"})
    cover = (await history.async_list("speaker:test"))[0]["cover_url"]
    assert "type=release" in cover and "id=7" in cover and "size=600x600" in cover
    assert "secret" not in cover and "token=" not in cover


@pytest.mark.asyncio
async def test_slow_history_save_does_not_block_speaker_receiver(monkeypatch):
    _, store = history_module(monkeypatch)
    store.save_started, store.allow_save = asyncio.Event(), asyncio.Event()
    coord = build_coordinator(state=make_state())
    coord._maybe_fetch_lyrics = MagicMock()
    callback = asyncio.create_task(coord._handle_event(*metadata("42")))
    await asyncio.wait_for(store.save_started.wait(), 1)
    try:
        assert callback.done(), "WS event handler must not await history storage"
    finally:
        store.allow_save.set()
        await callback
        await coord.async_flush_listening()


@pytest.mark.asyncio
@pytest.mark.parametrize("push", [True, False])
async def test_volume_only_update_does_not_record_unconfirmed_optimistic_play(monkeypatch, push):
    module, _ = history_module(monkeypatch)
    coord = build_coordinator(state=make_state(), track=make_track(track_id="42", playing=False))
    coord._maybe_fetch_lyrics = MagicMock()
    coord.apply_optimistic_track(playing=True)
    if push:
        await coord._handle_event(b'{"volume":{"percent":44}}', {5: {12: True}})
    else:
        coord.client.get_state = AsyncMock(return_value=make_state())
        coord.client.get_metadata = AsyncMock(side_effect=TimeoutError("No playback confirmation"))
        coord.client.get_paired_bt_devices = AsyncMock(return_value=[])
        await coord._refresh_state_and_track()
    await coord.async_flush_listening()
    history = await module.async_get_play_history(coord.hass)
    assert await history.async_list("speaker:" + coord.entry.entry_id) == []


@pytest.mark.asyncio
async def test_poll_cannot_queue_old_song_after_newer_push(monkeypatch):
    module, _ = history_module(monkeypatch)
    coord = build_coordinator(state=make_state())
    coord._maybe_fetch_lyrics = MagicMock()
    await observe(coord, *metadata("1", "First"))
    entered, release = asyncio.Event(), asyncio.Event()

    async def paired():
        entered.set()
        await release.wait()
        return []

    coord.client.get_state = AsyncMock(return_value=make_state())
    coord.client.get_metadata = AsyncMock(return_value=make_track(track_id="1", playing=True))
    coord.client.get_paired_bt_devices = paired
    poll = asyncio.create_task(coord._refresh_state_and_track())
    await entered.wait()
    try:
        await observe(coord, *metadata("2", "Second"))
    finally:
        release.set()
        await poll
    await coord.async_flush_listening()
    items = await (await module.async_get_play_history(coord.hass)).async_list("speaker:" + coord.entry.entry_id)
    assert [(i["id"], i["count"]) for i in items] == [("2", 1), ("1", 1)]
