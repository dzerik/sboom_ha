"""Persistent, user-scoped history of accepted playback launches."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from importlib import import_module

import pytest

from tests._ha_stubs import HomeAssistant


def history_module(monkeypatch, initial=None):
    """Replace only HA's disk boundary, retaining production history behavior."""
    module = import_module("sboom_ha.play_history")

    class MemoryStore:
        data = deepcopy(initial)
        loads = 0
        fail_save = False
        fail_load = False
        load_started = None
        allow_load = None
        save_started = None
        allow_save = None

        def __init__(self, hass, version, key, **kwargs):
            assert version == 1
            assert key == "sboom_ha.play_history"

        async def async_load(self):
            type(self).loads += 1
            if self.load_started:
                self.load_started.set()
                await self.allow_load.wait()
            if self.fail_load:
                raise OSError("disk unavailable")
            return deepcopy(self.data)

        async def async_save(self, data):
            if self.save_started:
                self.save_started.set()
                await self.allow_save.wait()
            if self.fail_save:
                raise OSError("disk full")
            type(self).data = deepcopy(data)

    monkeypatch.setattr(module, "Store", MemoryStore)
    return module, MemoryStore


@pytest.mark.asyncio
async def test_history_isolates_users_deduplicates_and_returns_copies(monkeypatch):
    module, _ = history_module(monkeypatch)
    history = await module.async_get_play_history(HomeAssistant())
    await history.async_record("alice", {"kind": "track", "id": "7", "title": "First"})
    await history.async_record("alice", {"kind": "track", "id": "7", "title": "Updated"})
    await history.async_record("alice", {"kind": "release", "id": "7"})
    await history.async_record("bob", {"kind": "track", "id": "8"})
    items = await history.async_list("alice", "frequent")
    assert [(item["kind"], item["id"], item["count"]) for item in items] == [
        ("track", "7", 2),
        ("release", "7", 1),
    ]
    assert items[0]["title"] == "Updated"
    assert items[0]["first_played"] <= items[0]["last_played"]
    items[0]["title"] = "tampered"
    assert (await history.async_list("alice", "frequent"))[0]["title"] == "Updated"
    await history.async_clear("alice")
    assert await history.async_list("alice") == []
    assert [item["id"] for item in await history.async_list("bob")] == ["8"]


@pytest.mark.asyncio
async def test_history_limits_to_100_recent_items_and_survives_reload(monkeypatch):
    module, store = history_module(monkeypatch)
    clock = iter(range(1000, 1200))
    monkeypatch.setattr(module.time, "time", lambda: next(clock))
    history = await module.async_get_play_history(HomeAssistant())
    for number in range(105):
        await history.async_record("alice", {"kind": "track", "id": str(number)})
    reloaded = await module.async_get_play_history(HomeAssistant())
    items = await reloaded.async_list("alice")
    assert len(items) == 100
    assert [item["id"] for item in items[:2]] == ["104", "103"]
    assert items[-1]["id"] == "5"
    assert store.loads == 2


@pytest.mark.asyncio
async def test_history_drops_malformed_storage_rows(monkeypatch):
    good = {
        "kind": "track",
        "id": "7",
        "title": "Good",
        "subtitle": "",
        "cover_url": "",
        "count": 2,
        "first_played": 1.0,
        "last_played": 2.0,
    }
    module, _ = history_module(
        monkeypatch,
        {
            "users": {
                "alice": [
                    None,
                    {},
                    {**good, "id": "oops"},
                    {**good, "count": -2},
                    {**good, "last_played": float("nan")},
                    good,
                ],
                "bob": "broken",
            }
        },
    )
    history = await module.async_get_play_history(HomeAssistant())
    assert await history.async_list("alice") == [good]
    assert await history.async_list("bob") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [None, [], "bad", {"users": []}])
async def test_history_accepts_missing_or_malformed_storage_root(monkeypatch, data):
    module, _ = history_module(monkeypatch, data)
    history = await module.async_get_play_history(HomeAssistant())
    assert await history.async_list("alice") == []
    await history.async_record("alice", {"kind": "track", "id": "1"})
    assert (await history.async_list("alice"))[0]["count"] == 1


@pytest.mark.asyncio
async def test_concurrent_initial_load_and_record_share_one_singleton(monkeypatch):
    module, store = history_module(monkeypatch)
    store.load_started, store.allow_load = asyncio.Event(), asyncio.Event()
    hass = HomeAssistant()
    first = asyncio.create_task(module.async_get_play_history(hass))
    await store.load_started.wait()
    second = asyncio.create_task(
        module.async_record_play(hass, "alice", "staros://music?tid=9&pt=track")
    )
    await asyncio.sleep(0)
    store.allow_load.set()
    history, _ = await asyncio.gather(first, second)
    assert history is await module.async_get_play_history(hass)
    assert store.loads == 1
    assert (await history.async_list("alice"))[0]["count"] == 1


@pytest.mark.asyncio
async def test_concurrent_save_clear_and_record_persist_in_order(monkeypatch):
    module, store = history_module(monkeypatch)
    history = await module.async_get_play_history(HomeAssistant())
    store.save_started, store.allow_save = asyncio.Event(), asyncio.Event()
    first = asyncio.create_task(history.async_record("alice", {"kind": "track", "id": "1"}))
    await store.save_started.wait()
    clear = asyncio.create_task(history.async_clear("alice"))
    last = asyncio.create_task(history.async_record("alice", {"kind": "track", "id": "2"}))
    await asyncio.sleep(0)
    store.allow_save.set()
    await asyncio.gather(first, clear, last)
    reloaded = await module.async_get_play_history(HomeAssistant())
    assert [item["id"] for item in await reloaded.async_list("alice")] == ["2"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "param"),
    [
        ("track", "tid"),
        ("artist", "pid"),
        ("release", "pid"),
        ("playlist", "pid"),
        ("podcast", "tid"),
        ("abook", "tid"),
    ],
)
async def test_record_play_parses_supported_content_and_bounds_metadata(monkeypatch, kind, param):
    module, store = history_module(monkeypatch)
    hass = HomeAssistant()
    await module.async_record_play(
        hass,
        "alice",
        f"staros://music?{param}=42&pt={kind}",
        {
            "title": "T" * 2000,
            "subtitle": "Artist",
            "cover_url": "https://cdn-image.zvuk.com/42",
            "token": "secret",
            "kind": "malicious",
            "id": "wrong",
            "count": 999,
        },
    )
    item = (await (await module.async_get_play_history(hass)).async_list("alice"))[0]
    assert item["kind"] == kind
    assert item["id"] == "42"
    assert 0 < len(item["title"]) <= 512
    assert item["subtitle"] == "Artist"
    assert item["count"] == 1
    assert "secret" not in repr(store.data)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "deeplink",
    [
        "https://evil.test/?tid=42&pt=track",
        "staros://other?tid=42&pt=track",
        "staros://music?tid=bad&pt=track",
        "staros://music?tid=42&pt=unknown",
        "staros://music?tid=42&tid=43&pt=track",
        "staros://music?pid=42&pt=track",
    ],
)
async def test_record_play_ignores_unrecognized_or_ambiguous_deeplinks(monkeypatch, deeplink):
    module, _ = history_module(monkeypatch)
    hass = HomeAssistant()
    await module.async_record_play(hass, "alice", deeplink)
    assert await (await module.async_get_play_history(hass)).async_list("alice") == []


@pytest.mark.asyncio
async def test_record_without_metadata_preserves_known_title_and_uses_system_scope(monkeypatch):
    module, _ = history_module(monkeypatch)
    hass = HomeAssistant()
    await module.async_record_play(hass, None, "staros://music?tid=42&pt=track", {"title": "Known"})
    await module.async_record_play(hass, None, "staros://music?tid=42&pt=track")
    item = (await (await module.async_get_play_history(hass)).async_list("system"))[0]
    assert item["title"] == "Known"
    assert item["count"] == 2


@pytest.mark.asyncio
async def test_storage_errors_do_not_escape_record_play_and_load_can_retry(monkeypatch):
    module, store = history_module(monkeypatch)
    hass = HomeAssistant()
    store.fail_load = True
    await module.async_record_play(hass, "alice", "staros://music?tid=42&pt=track")
    store.fail_load = False
    store.fail_save = True
    await module.async_record_play(hass, "alice", "staros://music?tid=42&pt=track")
    history = await module.async_get_play_history(hass)
    assert await history.async_list("alice") == []
    store.fail_save = False
    await module.async_record_play(hass, "alice", "staros://music?tid=43&pt=track")
    reloaded = await module.async_get_play_history(HomeAssistant())
    assert [item["id"] for item in await reloaded.async_list("alice")] == ["43"]


@pytest.mark.asyncio
async def test_failed_clear_preserves_history_in_memory_and_storage(monkeypatch):
    module, store = history_module(monkeypatch)
    history = await module.async_get_play_history(HomeAssistant())
    await history.async_record("alice", {"kind": "track", "id": "1"})
    await history.async_record("bob", {"kind": "track", "id": "2"})
    original = await history.async_list("alice")
    store.fail_save = True
    with pytest.raises(OSError, match="disk full"):
        await history.async_clear("alice")
    assert await history.async_list("alice") == original
    store.fail_save = False
    reloaded = await module.async_get_play_history(HomeAssistant())
    assert await reloaded.async_list("alice") == original
    assert [item["id"] for item in await reloaded.async_list("bob")] == ["2"]
    await history.async_clear("alice")
    assert await history.async_list("alice") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("content_id", ["0", "100"])
async def test_failed_record_preserves_counts_and_capacity_in_memory_and_storage(
    monkeypatch, content_id
):
    module, store = history_module(monkeypatch)
    history = await module.async_get_play_history(HomeAssistant())
    for number in range(100):
        await history.async_record("alice", {"kind": "track", "id": str(number)})
    original = await history.async_list("alice")
    store.fail_save = True
    with pytest.raises(OSError, match="disk full"):
        await history.async_record("alice", {"kind": "track", "id": content_id})
    assert await history.async_list("alice") == original
    store.fail_save = False
    reloaded = await module.async_get_play_history(HomeAssistant())
    assert await reloaded.async_list("alice") == original


@pytest.mark.asyncio
async def test_cancelled_record_releases_lock_and_propagates_cancellation(monkeypatch):
    module, store = history_module(monkeypatch)
    hass = HomeAssistant()
    history = await module.async_get_play_history(hass)
    store.save_started, store.allow_save = asyncio.Event(), asyncio.Event()
    task = asyncio.create_task(
        module.async_record_play(hass, "alice", "staros://music?tid=42&pt=track")
    )
    await store.save_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    store.allow_save.set()
    await asyncio.wait_for(history.async_clear("alice"), timeout=1)
    assert await history.async_list("alice") == []


@pytest.mark.asyncio
async def test_loaded_rows_with_out_of_range_timestamps_are_discarded(monkeypatch):
    bad = {"kind": "track", "id": "7", "count": 1, "first_played": 10**400, "last_played": 10**400}
    module, _ = history_module(monkeypatch, {"users": {"alice": [bad]}})
    history = await module.async_get_play_history(HomeAssistant())
    assert await history.async_list("alice") == []


@pytest.mark.asyncio
async def test_cover_metadata_drops_credentials_and_url_tokens(monkeypatch):
    module, store = history_module(monkeypatch)
    hass = HomeAssistant()
    await module.async_record_play(
        hass,
        "alice",
        "staros://music?tid=42&pt=track",
        {
            "cover_url": "https://cdn-image.zvuk.com/42?token=secret#fragment",
        },
    )
    item = (await (await module.async_get_play_history(hass)).async_list("alice"))[0]
    assert item["cover_url"] == "https://cdn-image.zvuk.com/42"
    await module.async_record_play(
        hass,
        "alice",
        "staros://music?tid=43&pt=track",
        {
            "cover_url": "https://user:secret@cdn-image.zvuk.com/43",
        },
    )
    assert "secret" not in repr(store.data)
