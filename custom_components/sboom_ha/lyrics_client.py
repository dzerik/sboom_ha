"""Получение текстов песен из Lrclib.net (open API, без auth)."""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

import aiohttp

_LOGGER = logging.getLogger(__name__)

LRCLIB_BASE = "https://lrclib.net/api"
USER_AGENT = "sboom_ha/HomeAssistant"

# Таймстамп LRC: [MM:SS.cc] или [MM:SS.ccc]. Строка может начинаться с
# НЕСКОЛЬКИХ таймстампов подряд ([00:10.00][01:30.00]Припев) — одна строка
# текста на несколько моментов времени.
_LRC_TS = re.compile(r"\[(\d{1,2}):(\d{2})\.(\d{2,3})\]")
# Word-теги enhanced LRC внутри текста: <MM:SS.cc> — вычищаем.
_LRC_WORD_TAG = re.compile(r"<\d{1,2}:\d{2}\.\d{2,3}>")


@dataclass(slots=True)
class Lyrics:
    plain: str | None
    synced: str | None
    instrumental: bool
    source: str  # "lrclib"
    # Распарсенные timestamps (sec → text); None если sync недоступен
    timeline: list[tuple[float, str]] | None


def _parse_lrc(synced: str) -> list[tuple[float, str]]:
    """LRC → отсортированный по времени список (sec, text).

    Поддерживает multi-timestamp строки ([00:10.00][01:30.00]Припев — текст
    попадает в обе точки) и вычищает word-теги enhanced LRC (<00:12.34>).
    """
    out: list[tuple[float, str]] = []
    for line in synced.splitlines():
        stamps: list[re.Match[str]] = []
        end = 0
        for m in _LRC_TS.finditer(line):
            if m.start() != end:
                break  # таймстампы только подряд в начале строки
            stamps.append(m)
            end = m.end()
        if not stamps:
            continue
        text = _LRC_WORD_TAG.sub("", line[end:]).strip()
        for m in stamps:
            mm, ss, cc = m.groups()
            cs = int(cc) / (1000 if len(cc) == 3 else 100)
            out.append((int(mm) * 60 + int(ss) + cs, text))
    out.sort(key=lambda x: x[0])
    return out


def lyrics_to_dict(lyrics: Lyrics) -> dict:
    """Сериализация Lyrics для HA Store. timeline опускаем — derived из synced."""
    return {
        "plain": lyrics.plain,
        "synced": lyrics.synced,
        "instrumental": lyrics.instrumental,
        "source": lyrics.source,
    }


def lyrics_from_dict(data: dict) -> Lyrics:
    """Десериализация Lyrics из HA Store. timeline пересобирается из synced."""
    synced = data.get("synced")
    return Lyrics(
        plain=data.get("plain"),
        synced=synced,
        instrumental=bool(data.get("instrumental", False)),
        source=data.get("source") or "lrclib",
        timeline=_parse_lrc(synced) if synced else None,
    )


async def _request_get(
    session: aiohttp.ClientSession,
    track: str,
    artist: str,
    album: str | None,
    duration_sec: int | None,
    timeout: float,
) -> dict | None | str:
    """Один запрос. Возвращает dict (data), None (network err), или 'not_found'."""
    params: dict[str, str] = {"track_name": track, "artist_name": artist}
    if album:
        params["album_name"] = album
    if duration_sec:
        params["duration"] = str(int(duration_sec))
    try:
        async with session.get(
            f"{LRCLIB_BASE}/get",
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as r:
            if r.status == 404:
                return "not_found"
            if r.status != 200:
                _LOGGER.debug("lrclib HTTP %s for %r — %r", r.status, track, artist)
                return None
            return await r.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        _LOGGER.debug("lrclib network err: %s", exc.__class__.__name__)
        return None


async def fetch_lyrics(
    session: aiohttp.ClientSession,
    track: str,
    artist: str,
    album: str | None = None,
    duration_sec: int | None = None,
    timeout: float = 6.0,
    retries: int = 2,
) -> Lyrics | None:
    """Запрос lyrics в Lrclib с retry. Сначала с album+duration, fallback без них."""
    if not track or not artist:
        return None

    # Пробы: (a) полные параметры, (b) только track+artist (некоторые песни в lrclib
    # без album metadata).
    attempts: list[tuple[str | None, int | None]] = [(album, duration_sec)]
    if album or duration_sec:
        attempts.append((None, None))

    network_err = False
    for attempt_album, attempt_dur in attempts:
        for retry in range(retries + 1):
            res = await _request_get(session, track, artist, attempt_album, attempt_dur, timeout)
            if res == "not_found":
                break  # 404 — на этот запрос не нашлось, fallback к (b)
            if res is None:
                network_err = True
                if retry < retries:
                    await asyncio.sleep(0.8 * (retry + 1))
                    continue
                break
            # success
            data = res
            plain = data.get("plainLyrics") or None
            synced = data.get("syncedLyrics") or None
            timeline = _parse_lrc(synced) if synced else None
            return Lyrics(
                plain=plain,
                synced=synced,
                instrumental=bool(data.get("instrumental")),
                source="lrclib",
                timeline=timeline,
            )

    # Дошли сюда — все варианты дали 404 или network err.
    if network_err:
        _LOGGER.warning("lrclib all retries failed for %r — %r", track, artist)
        return None  # caller сможет ретраить позже
    _LOGGER.debug("lrclib not_found %r — %r", track, artist)
    return Lyrics(None, None, False, "lrclib", None)


def current_line(timeline: list[tuple[float, str]] | None, position_sec: float) -> str | None:
    """Возвращает строку, активную в данной позиции трека."""
    if not timeline:
        return None
    last: str | None = None
    for ts, text in timeline:
        if ts > position_sec:
            break
        if text:
            last = text
    return last
