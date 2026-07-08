"""Тесты персиста lyrics-кеша через HA Store."""
from __future__ import annotations

import pytest

from sboom_ha.lyrics_client import Lyrics
from tests._fakes import build_coordinator


def _lyrics(plain: str = "text") -> Lyrics:
    return Lyrics(plain=plain, synced=None, instrumental=False,
                  source="lrclib", timeline=None)


def test_lyrics_cache_data_serialises_and_skips_none():
    """Снимок кеша содержит только реальные Lyrics, None-записи отбрасываются."""
    coord = build_coordinator()
    coord.lyrics.by_track = {"1": _lyrics("a"), "2": None, "3": _lyrics("b")}
    data = coord.lyrics.cache_snapshot()
    assert set(data.keys()) == {"1", "3"}
    assert data["1"]["plain"] == "a"


@pytest.mark.asyncio
async def test_load_lyrics_cache_populates_from_store():
    coord = build_coordinator()
    coord.lyrics._store._data = {
        "42": {"plain": "hello", "synced": None,
               "instrumental": False, "source": "lrclib"},
    }
    await coord.lyrics.async_load()
    assert coord.lyrics.by_track["42"].plain == "hello"


@pytest.mark.asyncio
async def test_load_lyrics_cache_empty_store_starts_clean():
    """Store пуст (async_load → None) — стартуем с пустым кешем, без падения."""
    coord = build_coordinator()
    await coord.lyrics.async_load()
    assert coord.lyrics.by_track == {}


@pytest.mark.asyncio
async def test_load_lyrics_cache_skips_garbage_entries():
    coord = build_coordinator()
    coord.lyrics._store._data = {
        "ok": {"plain": "x", "instrumental": False, "source": "lrclib"},
        "bad": "not a dict",
    }
    await coord.lyrics.async_load()
    assert "ok" in coord.lyrics.by_track
    assert "bad" not in coord.lyrics.by_track
