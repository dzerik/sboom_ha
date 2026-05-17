"""Camera-entity: рендерит обложку + lyrics для отправки на ТВ через media_player.play_media."""
from __future__ import annotations

import asyncio
import logging

import aiohttp
from aiohttp import web
from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONTENT_TYPE_MULTIPART
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ._entity_base import SboomEntity
from .const import DOMAIN
from .coordinator import SboomCoordinator
from .helpers import cover_url, track_position
from .image_render import draw_blank, draw_cover_yandex, draw_lyrics_with_cover

_LOGGER = logging.getLogger(__name__)

# Один MJPEG-стрим на сущность, данные из coordinator — параллелизм безразличен.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SboomCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SboomLyricsCamera(coordinator, entry)])


class SboomLyricsCamera(SboomEntity, Camera):
    """MJPEG-стрим с обложкой и синхронизированными lyrics."""

    _attr_translation_key = "lyrics_camera"
    _attr_entity_registry_enabled_default = False  # включается вручную, тяжеловесная сущность

    def __init__(self, coordinator: SboomCoordinator, entry: ConfigEntry) -> None:
        SboomEntity.__init__(self, coordinator, entry)
        Camera.__init__(self)
        self._attr_unique_id = f"{self._device_unique_prefix}_lyrics_camera"

        # Cache: track_id -> raw cover bytes (для повторного использования при отрисовке lyrics)
        self._cover_cache_track: str | None = None
        self._cover_raw: bytes | None = None
        # Cache: track_id -> готовый idle-JPEG (когда lyrics нет)
        self._idle_jpeg_track: str | None = None
        self._idle_jpeg: bytes | None = None

    # ─────────── Snapshot (для предпросмотра в HA) ───────────

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        return await self._build_idle_jpeg() or draw_blank()

    # ─────────── MJPEG stream (для отправки на ТВ) ───────────

    async def handle_async_mjpeg_stream(
        self, request: web.Request
    ) -> web.StreamResponse | None:
        response = web.StreamResponse()
        response.content_type = CONTENT_TYPE_MULTIPART.format("--frameboundary")
        await response.prepare(request)
        try:
            while True:
                try:
                    track = self.coordinator.track
                    if not track:
                        await _write_jpeg(response, draw_blank())
                        await asyncio.sleep(2)
                        continue
                    lyrics = self.coordinator.current_lyrics()
                    if lyrics and lyrics.timeline:
                        await self._stream_lyrics_with_cover(response)
                    else:
                        await self._stream_idle(response)
                except (asyncio.CancelledError, ConnectionResetError):
                    raise
                except Exception:
                    # Сбой рендера/отрисовки кадра не должен ронять весь стрим
                    # с HTTP 500 — логируем и пробуем снова после паузы.
                    _LOGGER.exception("lyrics-стрим: ошибка кадра, повтор через 2s")
                    await asyncio.sleep(2)
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        return response

    async def _stream_lyrics_with_cover(self, response: web.StreamResponse) -> None:
        """Lyrics поверх blur-обложки. Тикаем пока трек/lyrics не сменились."""
        track = self.coordinator.track
        if not track:
            return
        track_id = track.track_id
        lyrics = self.coordinator.current_lyrics()
        if not lyrics or not lyrics.timeline:
            return
        timeline = lyrics.timeline
        cover_raw = await self._fetch_cover_raw(track)
        artist = ", ".join(track.artists) if track.artists else None
        last_cur: object = object()  # sentinel

        while (
            self.coordinator.track
            and self.coordinator.track.track_id == track_id
            and self.coordinator.current_lyrics() is lyrics
        ):
            pos = track_position(self.coordinator)
            if pos is None:
                await asyncio.sleep(1)
                continue

            cur, nxt = None, None
            for i, (ts, text) in enumerate(timeline):
                if ts > pos:
                    nxt = text
                    if i > 0:
                        cur = timeline[i - 1][1]
                    break
            else:
                cur = timeline[-1][1] if timeline else None

            duration = float(track.duration_sec) if track.duration_sec else None
            progress = (pos / duration) if duration and duration > 0 else None
            if cur != last_cur:
                jpeg = await asyncio.to_thread(
                    draw_lyrics_with_cover,
                    cover_raw, cur, nxt, track.title, artist,
                    progress, pos, duration,
                )
                await _write_jpeg(response, jpeg)
                last_cur = cur

            next_ts = next((ts for ts, _ in timeline if ts > pos), None)
            delay = min(1.0, max(0.1, next_ts - pos)) if next_ts else 1.0
            await asyncio.sleep(delay)

    async def _stream_idle(self, response: web.StreamResponse) -> None:
        """Нет synced lyrics — обновляем кадр каждую секунду (для движения прогресс-бара)."""
        track = self.coordinator.track
        track_id = track.track_id if track else None
        last_sec: int | None = None
        while (
            self.coordinator.track
            and self.coordinator.track.track_id == track_id
            and not (
                self.coordinator.current_lyrics()
                and self.coordinator.current_lyrics().timeline
            )
        ):
            pos = track_position(self.coordinator)
            cur_sec = int(pos) if pos is not None else None
            if cur_sec != last_sec:
                jpeg = await self._build_idle_jpeg()
                if jpeg:
                    await _write_jpeg(response, jpeg)
                last_sec = cur_sec
            await asyncio.sleep(1)

    async def _build_idle_jpeg(self) -> bytes | None:
        track = self.coordinator.track
        if not track:
            return None
        # idle JPEG нельзя кэшировать как раньше — теперь зависит от позиции.
        cover_raw = await self._fetch_cover_raw(track)
        artist = ", ".join(track.artists) if track.artists else None
        pos = track_position(self.coordinator)
        duration = float(track.duration_sec) if track.duration_sec else None
        progress = (pos / duration) if (pos is not None and duration and duration > 0) else None
        return await asyncio.to_thread(
            draw_cover_yandex, cover_raw, track.title, artist, progress, pos, duration,
        )

    async def _fetch_cover_raw(self, track) -> bytes | None:
        if self._cover_cache_track == track.track_id and self._cover_raw is not None:
            return self._cover_raw
        url = cover_url(track)
        if not url:
            self._cover_cache_track = track.track_id
            self._cover_raw = None
            return None
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with self.coordinator.http_session.get(url, timeout=timeout) as r:
                if r.status == 200:
                    raw = await r.read()
                    self._cover_cache_track = track.track_id
                    self._cover_raw = raw
                    return raw
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            _LOGGER.debug("cover fetch failed: %s", exc)
        self._cover_cache_track = track.track_id
        self._cover_raw = None
        return None


async def _write_jpeg(response: web.StreamResponse, image: bytes) -> None:
    data = (
        b"--frameboundary\r\nContent-Type: image/jpeg\r\nContent-Length: "
        + str(len(image)).encode()
        + b"\r\n\r\n"
        + image
        + b"\r\n"
    )
    # Двойная запись — обход известного бага MJPEG-парсера в Chrome.
    await response.write(data)
    await response.write(data)
