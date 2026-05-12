"""Координатор: один WS-клиент на колонку, push state-updates."""
from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import SberSpeakerClient, SpeakerState, TrackInfo
from .const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_NAME,
    CONF_DEVICE_ID,
    CONF_HOST,
    CONF_PIN_ACCESS_TOKEN,
    CONF_PORT,
    DEFAULT_AVAILABILITY_THRESHOLD,
    DEFAULT_KEEPALIVE_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_VOLUME_POLL_INTERVAL,
    DOMAIN,
    LYRICS_CACHE_MAX,
    DEFAULT_LYRICS_ENABLED,
    OPT_AVAILABILITY_THRESHOLD,
    OPT_KEEPALIVE_INTERVAL,
    OPT_LYRICS_ENABLED,
    OPT_VOLUME_POLL_INTERVAL,
    RECONNECT_BACKOFF_SEC,
)

# Event types для HA event bus.
EVENT_TRACK_CHANGED = "sboom_track_changed"
EVENT_PLAYBACK_CHANGED = "sboom_playback_changed"
EVENT_VOLUME_CHANGED = "sboom_volume_changed"
EVENT_CONNECTION_CHANGED = "sboom_connection_changed"

# Issue: колонка недоступна больше N секунд → создаём info-issue в Repairs.
UNREACHABLE_ISSUE_THRESHOLD_SEC = 300  # 5 минут
from .lyrics_client import Lyrics, fetch_lyrics

_LOGGER = logging.getLogger(__name__)


class SboomCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Один coordinator на одну колонку.

    Хранит:
      - latest SpeakerState (volume, muted)
      - latest TrackInfo
    Поднимает long-lived WS-сессию, шлёт KeepAlive, обрабатывает unsolicited
    state-update'ы от колонки и форсит refresh подписчиков.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        # Читаем опции, fallback на дефолты — для существующих entries без options.
        opts = entry.options
        self._volume_poll_interval = int(
            opts.get(OPT_VOLUME_POLL_INTERVAL, DEFAULT_VOLUME_POLL_INTERVAL)
        )
        self._keepalive_interval = int(
            opts.get(OPT_KEEPALIVE_INTERVAL, DEFAULT_KEEPALIVE_INTERVAL)
        )
        self._availability_threshold = int(
            opts.get(OPT_AVAILABILITY_THRESHOLD, DEFAULT_AVAILABILITY_THRESHOLD)
        )
        self._lyrics_enabled = bool(opts.get(OPT_LYRICS_ENABLED, DEFAULT_LYRICS_ENABLED))

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}:{entry.data.get(CONF_HOST)}",
            # State-pushes (volume/mute) от колонки НЕ приходят — приходит только
            # metadata. Поллим volume по сконфигурированному интервалу.
            update_interval=timedelta(seconds=self._volume_poll_interval),
        )
        self.entry = entry
        self.client = SberSpeakerClient(
            host=entry.data[CONF_HOST],
            port=entry.data.get(CONF_PORT, DEFAULT_PORT),
            client_id=entry.data[CONF_CLIENT_ID],
            client_name=entry.data.get(CONF_CLIENT_NAME, "Home Assistant"),
            pin_access_token=entry.data[CONF_PIN_ACCESS_TOKEN],
            on_event=self._handle_event,
        )

        self.state: SpeakerState | None = None
        self.track: TrackInfo | None = None
        self.connected: bool = False  # доступность колонки (для entity.available)
        self._unreachable_since: float | None = None  # monotonic timestamp
        self._supervisor_task: asyncio.Task | None = None
        self._stopping = False

        # Lyrics: кэш track_id -> Lyrics (None = ищется/не нашли).
        self.lyrics_by_track: dict[str, Lyrics | None] = {}
        self._lyrics_inflight: set[str] = set()
        self._http = async_get_clientsession(hass)

    @property
    def http_session(self):
        """Shared aiohttp session — для подплатформ (camera/sensor)."""
        return self._http

    # ─────────────────────── lifecycle ───────────────────────

    async def async_start(self) -> None:
        self._stopping = False
        self._supervisor_task = self.hass.async_create_background_task(
            self._supervisor(), name=f"{DOMAIN}-supervisor"
        )

    async def async_stop(self) -> None:
        self._stopping = True
        self._set_connected(False)
        if self._supervisor_task:
            self._supervisor_task.cancel()
            self._supervisor_task = None
        await self.client.close()

    # ─────────────────────── connection supervisor ───────────────────────

    async def _supervisor(self) -> None:
        attempt = 0
        while not self._stopping:
            try:
                await self.client.connect()
                self.client.start_listening()
                attempt = 0
                self._set_connected(True)
                _LOGGER.info("connected to %s", self.client.host)

                # Стартовый sync. ВАЖНО: вызов get_metadata (внутри
                # _refresh_state_and_track) активирует push-subscribe stream:
                # после него устройство пушит unsolicited updates на каждое
                # изменение track/play/pause/volume. Нам остаётся только
                # poll'ить volume через get_state (push-stream его не покрывает).
                await self._refresh_state_and_track()

                # Держим соединение через KeepAlive. Track-changes приходят
                # push-events через _handle_event (см. ниже).
                while not self._stopping:
                    await asyncio.sleep(self._keepalive_interval)
                    try:
                        await self.client.keep_alive()
                    except Exception:
                        _LOGGER.warning("keepalive failed, dropping connection")
                        break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _LOGGER.warning("WS error: %s", exc)
            finally:
                await self.client.close()

            if self._stopping:
                break

            attempt += 1
            # после N подряд неудач помечаем колонку недоступной
            if attempt >= self._availability_threshold:
                self._set_connected(False)
                self._maybe_create_unreachable_issue()

            backoff = RECONNECT_BACKOFF_SEC[min(attempt - 1, len(RECONNECT_BACKOFF_SEC) - 1)]
            backoff = backoff + random.uniform(0, backoff * 0.3)
            _LOGGER.info("reconnect in %.1fs (attempt %d)", backoff, attempt)
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                raise

    # ─────────────────────── lyrics ───────────────────────

    def current_lyrics(self) -> Lyrics | None:
        """Lyrics для активного трека (или None если ещё не загружено / не нашлось)."""
        if not self.track or not self.track.track_id:
            return None
        return self.lyrics_by_track.get(self.track.track_id)

    def _maybe_fetch_lyrics(self) -> None:
        """Запустить background fetch lyrics для текущего трека, если ещё не загружали."""
        if not self._lyrics_enabled:
            return
        t = self.track
        if not t or not t.track_id or not t.title or not t.artists:
            return
        tid = t.track_id
        if tid in self.lyrics_by_track or tid in self._lyrics_inflight:
            return
        # Простая защита от роста кэша: при превышении дропаем самый старый.
        if len(self.lyrics_by_track) >= LYRICS_CACHE_MAX:
            self.lyrics_by_track.pop(next(iter(self.lyrics_by_track)), None)
        self._lyrics_inflight.add(tid)
        self.hass.async_create_background_task(
            self._fetch_lyrics(tid, t.title, ", ".join(t.artists), t.album, t.duration_sec),
            name=f"{DOMAIN}-lyrics-{tid}",
        )

    async def _fetch_lyrics(
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
            self.lyrics_by_track[track_id] = result
            _LOGGER.debug(
                "lyrics for %s (%r — %r): %s",
                track_id, title, artist,
                "found" if result.plain or result.synced
                else ("instrumental" if result.instrumental else "not_found"),
            )
            self.async_set_updated_data({"state": self.state, "track": self.track})
        finally:
            self._lyrics_inflight.discard(track_id)

    # ─────────────────────── data handlers ───────────────────────

    async def _refresh_state_and_track(self) -> None:
        prev_track = self.track
        prev_state = self.state
        try:
            self.state = await self.client.get_state()
            _LOGGER.debug("get_state -> volume=%s muted=%s",
                          self.state.volume_percent if self.state else "?",
                          self.state.muted if self.state else "?")
        except Exception:
            _LOGGER.exception("get_state failed")
        try:
            self.track = await self.client.get_metadata()
            self._maybe_fetch_lyrics()
            if self.track:
                _LOGGER.debug(
                    "get_metadata -> title=%r artists=%s album=%r track_id=%s "
                    "release_id=%s playing=%s pos=%s/%s prov=%s",
                    self.track.title, self.track.artists, self.track.album,
                    self.track.track_id, self.track.release_id,
                    self.track.playing, self.track.position_sec,
                    self.track.duration_sec, self.track.provider,
                )
            else:
                _LOGGER.warning("get_metadata returned None — парсер не нашёл trackId")
        except Exception:
            _LOGGER.exception("get_metadata failed")
        self._fire_change_events(prev_track, prev_state)
        self.async_set_updated_data({"state": self.state, "track": self.track})

    async def _handle_event(self, raw: bytes, parsed: dict[int, Any]) -> None:
        """Колонка отправила unsolicited / push message — обновляем state/track."""
        req_data = parsed.get(5)
        if not isinstance(req_data, dict):
            return
        prev_track = self.track
        prev_state = self.state
        changed = False
        if 10 in req_data:    # MetaData update
            try:
                new_track = self.client.parse_track(raw)
                if new_track is not None:
                    self.track = new_track
                    self._maybe_fetch_lyrics()
                    changed = True
            except Exception:  # pragma: no cover
                _LOGGER.exception("metadata push parse failed")
        if 12 in req_data:    # State update
            try:
                self.state = self.client.parse_state(raw)
                changed = True
            except Exception:
                _LOGGER.exception("state push parse failed")
        if changed:
            self._fire_change_events(prev_track, prev_state)
            self.async_set_updated_data({"state": self.state, "track": self.track})

    # ─────────────────────── event bus ───────────────────────

    def _set_connected(self, connected: bool) -> None:
        """Обновить флаг доступности и стрельнуть событием при изменении."""
        if self.connected == connected:
            return
        self.connected = connected
        if connected:
            self._unreachable_since = None
            self._clear_unreachable_issue()
        else:
            self._unreachable_since = time.monotonic()
        self.hass.bus.async_fire(EVENT_CONNECTION_CHANGED, {
            **self._event_payload_base(),
            "connected": connected,
        })
        # форсим entity-перерисовку (чтобы available подхватился сразу)
        self.async_update_listeners()

    def _maybe_create_unreachable_issue(self) -> None:
        """Если колонка недоступна больше UNREACHABLE_ISSUE_THRESHOLD_SEC — issue в Repairs."""
        if self.connected or self._unreachable_since is None:
            return
        elapsed = time.monotonic() - self._unreachable_since
        if elapsed < UNREACHABLE_ISSUE_THRESHOLD_SEC:
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"unreachable_{self.entry.entry_id}",
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="speaker_unreachable",
            translation_placeholders={
                "name": self.entry.data.get("device_name") or self.entry.data.get(CONF_HOST, "speaker"),
                "minutes": str(int(elapsed // 60)),
            },
        )

    def _clear_unreachable_issue(self) -> None:
        ir.async_delete_issue(
            self.hass, DOMAIN, f"unreachable_{self.entry.entry_id}"
        )

    def _event_payload_base(self) -> dict[str, Any]:
        """Общая часть полезной нагрузки события: device-контекст."""
        return {
            "entry_id": self.entry.entry_id,
            "device_id": self.entry.data.get(CONF_DEVICE_ID),
            "host": self.entry.data.get(CONF_HOST),
        }

    def _fire_change_events(self, prev_track: TrackInfo | None, prev_state: SpeakerState | None) -> None:
        """Сравнить prev с current и выпустить соответствующие события в HA bus."""
        # Track / playback
        cur_t = self.track
        if cur_t is not None:
            prev_id = prev_track.track_id if prev_track else None
            if cur_t.track_id != prev_id:
                self.hass.bus.async_fire(EVENT_TRACK_CHANGED, {
                    **self._event_payload_base(),
                    "track_id": cur_t.track_id,
                    "title": cur_t.title,
                    "artists": list(cur_t.artists),
                    "album": cur_t.album,
                    "provider": cur_t.provider,
                    "previous_track_id": prev_id,
                })
            elif prev_track is not None and (
                prev_track.playing != cur_t.playing
                or prev_track.shuffle != cur_t.shuffle
                or prev_track.repeat != cur_t.repeat
            ):
                self.hass.bus.async_fire(EVENT_PLAYBACK_CHANGED, {
                    **self._event_payload_base(),
                    "track_id": cur_t.track_id,
                    "playing": cur_t.playing,
                    "shuffle": cur_t.shuffle,
                    "repeat": cur_t.repeat,
                })

        # Volume / mute
        cur_s = self.state
        if cur_s is not None and (
            prev_state is None
            or prev_state.volume_percent != cur_s.volume_percent
            or prev_state.muted != cur_s.muted
        ):
            self.hass.bus.async_fire(EVENT_VOLUME_CHANGED, {
                **self._event_payload_base(),
                "volume_percent": cur_s.volume_percent,
                "muted": cur_s.muted,
            })

    async def _async_update_data(self) -> dict[str, Any]:
        """Fallback poll — если push не приходят."""
        await self._refresh_state_and_track()
        return {"state": self.state, "track": self.track}
