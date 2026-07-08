"""Менеджер lyrics: кэш по трекам, фоновые загрузки, персист в HA Store.

Выделен из SboomCoordinator (SRP): координатор отвечает за WS-сессию и
state, менеджер — за жизненный цикл текстов песен. Координатор дергает
`maybe_fetch`/`current_for`, менеджер сообщает о готовности через
callback `on_update` (перерисовка entities).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, LYRICS_CACHE_MAX
from .lyrics_client import Lyrics, fetch_lyrics, lyrics_from_dict, lyrics_to_dict

if TYPE_CHECKING:
    import aiohttp
    from homeassistant.config_entries import ConfigEntry

    from .api import TrackInfo

_LOGGER = logging.getLogger(__name__)

# Lyrics-кеш персистится в HA Store с debounce — чтобы не писать на диск
# на каждый найденный трек.
LYRICS_STORE_VERSION = 1
LYRICS_SAVE_DELAY_SEC = 30


class LyricsManager:
    """Кэш track_id → Lyrics (None = искали, не нашли) + загрузка и персист."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        http_session: aiohttp.ClientSession,
        *,
        enabled: bool,
        on_update: Callable[[], None],
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._http = http_session
        self._enabled = enabled
        self._on_update = on_update
        self.by_track: dict[str, Lyrics | None] = {}
        self._inflight: set[str] = set()
        # Персистентный кеш (JSON в .storage/, переживает рестарты HA).
        self._store: Store = Store(
            hass, LYRICS_STORE_VERSION, f"{DOMAIN}_lyrics_{entry.entry_id}"
        )

    @property
    def inflight_count(self) -> int:
        return len(self._inflight)

    def current_for(self, track: TrackInfo | None) -> Lyrics | None:
        """Lyrics для трека (или None если ещё не загружено / не нашлось)."""
        if not track or not track.track_id:
            return None
        return self.by_track.get(track.track_id)

    def maybe_fetch(self, track: TrackInfo | None) -> None:
        """Запустить background fetch для трека, если ещё не загружали."""
        if not self._enabled:
            return
        if not track or not track.track_id or not track.title or not track.artists:
            return
        tid = track.track_id
        if tid in self.by_track or tid in self._inflight:
            return
        # Простая защита от роста кэша: при превышении дропаем самый старый.
        if len(self.by_track) >= LYRICS_CACHE_MAX:
            self.by_track.pop(next(iter(self.by_track)), None)
        self._inflight.add(tid)
        # Задача привязана к entry: при unload/reload HA сам её отменит —
        # иначе fetch жил бы дольше владельца и писал в мёртвый Store.
        self._entry.async_create_background_task(
            self._hass,
            self._fetch(
                tid,
                track.title,
                ", ".join(track.artists),
                track.album,
                track.duration_sec,
            ),
            name=f"{DOMAIN}-lyrics-{tid}",
        )

    async def _fetch(
        self,
        track_id: str,
        title: str,
        artist: str,
        album: str | None,
        duration_sec: int | None,
    ) -> None:
        try:
            result = await fetch_lyrics(self._http, title, artist, album, duration_sec)
            if result is None:
                # Сетевая ошибка — НЕ кэшируем, дадим retry при следующем track-update.
                _LOGGER.debug("lyrics fetch error for %s — will retry later", track_id)
                return
            self.by_track[track_id] = result
            # Персист в Store с debounce — не пишем на диск на каждый трек.
            self._store.async_delay_save(self.cache_snapshot, LYRICS_SAVE_DELAY_SEC)
            _LOGGER.debug(
                "lyrics for %s (%r — %r): %s",
                track_id, title, artist,
                "found" if result.plain or result.synced
                else ("instrumental" if result.instrumental else "not_found"),
            )
            self._on_update()
        finally:
            self._inflight.discard(track_id)

    def cache_snapshot(self) -> dict[str, dict[str, Any]]:
        """Снимок кеша для персиста (только реальные Lyrics, без None)."""
        return {
            tid: lyrics_to_dict(lyr)
            for tid, lyr in self.by_track.items()
            if lyr is not None
        }

    async def async_load(self) -> None:
        """Загрузить персистентный кеш из HA Store при старте."""
        try:
            stored = await self._store.async_load()
        except Exception:  # повреждённый файл — не критично, стартуем с пустым
            _LOGGER.warning("lyrics cache load failed", exc_info=True)
            return
        if not isinstance(stored, dict):
            return
        for tid, payload in list(stored.items())[:LYRICS_CACHE_MAX]:
            if isinstance(payload, dict):
                try:
                    self.by_track[tid] = lyrics_from_dict(payload)
                except Exception:  # битая запись — пропускаем
                    continue
        _LOGGER.debug("lyrics cache loaded: %d entries", len(self.by_track))

    async def async_flush(self) -> None:
        """Немедленный save (при unload — иначе debounced save писал бы
        в Store уже мёртвого entry)."""
        if not self.by_track:
            return
        try:
            await self._store.async_save(self.cache_snapshot())
        except Exception:  # не мешаем выгрузке из-за диска
            _LOGGER.debug("lyrics cache flush failed", exc_info=True)
