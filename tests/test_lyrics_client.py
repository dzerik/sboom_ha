"""Тесты Lrclib.net client + LRC parser.

Реальные кейсы:
- LRC с разной точностью timestamps (.cc vs .ccc)
- current_line на границах (до первой строки, между строками, после последней)
- network error vs 404 — разное поведение для retry-логики
- Пустые track/artist — не делаем запрос
- Fallback на запрос без album/duration если первый дал 404
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from sboom_ha.lyrics_client import (
    Lyrics,
    _parse_lrc,
    current_line,
    fetch_lyrics,
)


# ─────────────────────────── _parse_lrc ───────────────────────────

def test_parse_lrc_centisecond_format():
    """Стандартный LRC: [MM:SS.cc] (две цифры centisec)."""
    lrc = "[01:23.45]Текст\n[02:00.00]Вторая"
    timeline = _parse_lrc(lrc)
    assert timeline == [(83.45, "Текст"), (120.0, "Вторая")]


def test_parse_lrc_millisecond_format():
    """LRC от Lrclib иногда [MM:SS.ccc] (3 цифры миллисекунды)."""
    lrc = "[01:23.456]With ms"
    timeline = _parse_lrc(lrc)
    assert timeline[0] == (83.456, "With ms")


def test_parse_lrc_sorts_out_of_order_lines():
    """LRC может прийти неотсортированным — проверяем порядок."""
    lrc = "[02:00.00]Вторая\n[00:01.00]Первая\n[01:00.00]Между"
    timeline = _parse_lrc(lrc)
    assert [t for _, t in timeline] == ["Первая", "Между", "Вторая"]


def test_parse_lrc_strips_whitespace_around_text():
    timeline = _parse_lrc("[00:00.00]   текст с пробелами   ")
    assert timeline[0][1] == "текст с пробелами"


def test_parse_lrc_keeps_empty_lines():
    """Пустая строка lyrics (`[01:00.00]`) — это тоже валидный маркер паузы."""
    timeline = _parse_lrc("[00:00.00]Слова\n[00:30.00]\n[01:00.00]Снова")
    assert len(timeline) == 3
    assert timeline[1][1] == ""


def test_parse_lrc_skips_invalid_lines():
    """Не-LRC строки не должны падать или попасть в timeline."""
    lrc = "Random non-LRC text\n[00:05.00]Valid\nAnother junk"
    timeline = _parse_lrc(lrc)
    assert timeline == [(5.0, "Valid")]


# ─────────────────────────── current_line ───────────────────────────

def _sample_timeline():
    return [(0.0, "Первая"), (10.0, "Вторая"), (20.0, "Третья"), (30.0, "Финал")]


def test_current_line_returns_none_for_empty_timeline():
    assert current_line(None, 5.0) is None
    assert current_line([], 5.0) is None


def test_current_line_before_first_returns_none():
    """До первой строки — ничего ещё не звучало."""
    assert current_line(_sample_timeline(), -1.0) is None


def test_current_line_at_exact_timestamp_picks_that_line():
    """Если позиция точно на timestamp — это активная строка."""
    assert current_line(_sample_timeline(), 10.0) == "Вторая"
    assert current_line(_sample_timeline(), 20.0) == "Третья"


def test_current_line_between_picks_last_passed():
    """Между строками показываем ту что только что прошла."""
    assert current_line(_sample_timeline(), 15.0) == "Вторая"
    assert current_line(_sample_timeline(), 25.5) == "Третья"


def test_current_line_after_last_picks_last():
    """После финальной строки она остаётся активной."""
    assert current_line(_sample_timeline(), 99999.0) == "Финал"


def test_current_line_skips_empty_marker_lines():
    """Empty-marker (паузы) не должны "стирать" последнюю реплику.

    Это критично: если сейчас inструментал-проигрыш — экран не должен пустеть."""
    timeline = [(0.0, "Слова"), (10.0, ""), (20.0, "Снова")]
    # Между 10 и 20 секунд — был пустой marker, но активная остаётся "Слова"
    assert current_line(timeline, 15.0) == "Слова"


# ─────────────────────────── fetch_lyrics: contracts ───────────────────────────

@pytest.mark.asyncio
async def test_fetch_lyrics_returns_none_immediately_for_empty_track():
    """Без trackname — не дёргаем сеть вообще."""
    session = MagicMock(spec=aiohttp.ClientSession)
    result = await fetch_lyrics(session, "", "Artist")
    assert result is None
    session.get.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_lyrics_returns_none_immediately_for_empty_artist():
    session = MagicMock(spec=aiohttp.ClientSession)
    assert await fetch_lyrics(session, "Track", "") is None
    session.get.assert_not_called()


def _mock_session_response(status: int, json_data=None):
    """Helper: сессия которая возвращает указанный response."""
    resp = MagicMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_data)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock(spec=aiohttp.ClientSession)
    session.get = MagicMock(return_value=ctx)
    return session


@pytest.mark.asyncio
async def test_fetch_lyrics_success_parses_synced_timeline(lrclib_track_response):
    session = _mock_session_response(200, lrclib_track_response)
    result = await fetch_lyrics(session, "Sledgehammer", "Peter Gabriel")
    assert isinstance(result, Lyrics)
    assert result.plain == lrclib_track_response["plainLyrics"]
    assert result.timeline is not None
    assert len(result.timeline) == 4
    assert result.timeline[0] == (11.20, "Hey, hey, you there")


@pytest.mark.asyncio
async def test_fetch_lyrics_404_returns_empty_lyrics_marker():
    """404 — это "не найдено" (валидный результат). НЕ тот же что network error."""
    session = _mock_session_response(404, None)
    result = await fetch_lyrics(session, "Unknown", "Nobody")
    assert isinstance(result, Lyrics)
    assert result.plain is None
    assert result.synced is None
    assert result.timeline is None
    # Источник всё равно зафиксирован, чтобы coordinator знал что не делать retry
    assert result.source == "lrclib"


@pytest.mark.asyncio
async def test_fetch_lyrics_network_error_returns_none_after_retries():
    """Сетевые ошибки — None (caller повторит на следующем track-update)."""
    session = MagicMock(spec=aiohttp.ClientSession)
    session.get = MagicMock(side_effect=aiohttp.ClientError("connection refused"))
    result = await fetch_lyrics(session, "Track", "Artist", retries=1)
    assert result is None


@pytest.mark.asyncio
async def test_fetch_lyrics_falls_back_to_minimal_query_on_404():
    """Если с album/duration 404 — пробуем без них.

    Реальный кейс: в Lrclib запись часто без album metadata."""
    call_count = 0

    def mk_response(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        # Первая попытка (с album) — 404
        if call_count == 1:
            resp = MagicMock(); resp.status = 404
            resp.json = AsyncMock(return_value=None)
        else:
            # Вторая попытка (без album) — успех
            resp = MagicMock(); resp.status = 200
            resp.json = AsyncMock(return_value={
                "plainLyrics": "found without album",
                "syncedLyrics": None, "instrumental": False,
            })
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=resp)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    session = MagicMock(spec=aiohttp.ClientSession)
    session.get = MagicMock(side_effect=mk_response)

    result = await fetch_lyrics(session, "Track", "Artist", album="WrongAlbum",
                                duration_sec=200)
    assert call_count >= 2, "Должна быть минимум 2 попытки (с album и без)"
    assert isinstance(result, Lyrics)
    assert result.plain == "found without album"


@pytest.mark.asyncio
async def test_fetch_lyrics_instrumental_marked_correctly(lrclib_instrumental_response):
    session = _mock_session_response(200, lrclib_instrumental_response)
    result = await fetch_lyrics(session, "T", "C")
    assert result.instrumental is True
    assert result.plain is None
    assert result.timeline is None
