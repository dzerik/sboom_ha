"""
Низкоуровневый WebSocket-клиент для локального LAN-управления колонкой.

Обмен с колонкой SberBoom через wss://<host>:20000/ — WebSocket поверх TLS,
бинарный payload в формате tag-length-value (varint + length-delimited).
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import ssl
import uuid
from typing import Any, Awaitable, Callable, Optional

import websockets


def _detect_headers_kwarg() -> str:
    """websockets ≥ 13 → additional_headers; legacy → extra_headers."""
    try:
        params = inspect.signature(websockets.connect).parameters
        if "additional_headers" in params:
            return "additional_headers"
    except (ValueError, TypeError):
        pass
    return "extra_headers"


_HEADERS_KWARG = _detect_headers_kwarg()

from ._models import SpeakerState, TrackInfo
from ._parsers import parse_state as _parse_state
from ._parsers import parse_track as _parse_track
from ._tlv import decode as _decode_tlv
from ._tlv import field as _field
from .const import (
    DEFAULT_PORT,
    DEFAULT_USER_AGENT,
    KEEPALIVE_INTERVAL_SEC,
    MEDIA_CMD_DISLIKE,
    MEDIA_CMD_LIKE,
    MEDIA_CMD_MUTE,
    MEDIA_CMD_NEXT,
    MEDIA_CMD_PAUSE,
    MEDIA_CMD_PLAY,
    MEDIA_CMD_PREV,
    MEDIA_CMD_REMOVE_DISLIKE,
    MEDIA_CMD_REMOVE_LIKE,
    MEDIA_CMD_REPEAT_NONE,
    MEDIA_CMD_REPEAT_PLAYLIST,
    MEDIA_CMD_REPEAT_TRACK,
    MEDIA_CMD_SHUFFLE_OFF,
    MEDIA_CMD_SHUFFLE_ON,
    MEDIA_CMD_UNMUTE,
    OP_GET_META_DATA,
    OP_GET_PLAYING_QUEUE,
    OP_GET_STATE,
    OP_KEEP_ALIVE,
    OP_MEDIA_COMMAND,
    OP_PIN_CONNECT,
    OP_SET_TRACK_POS,
    OP_SET_VOLUME,
    PAIR_BUTTON_TIMEOUT_SEC,
    TOKEN_TYPE_PIN_AUTH,
)

_LOGGER = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Pair-handshake и команды
# ─────────────────────────────────────────────────────────────────────

class PairTimeout(Exception):
    """Колонка не подтвердила pair (нет нажатия '+' за timeout)."""


class AuthError(Exception):
    """Колонка отвергла наш токен/UUID."""


class SberSpeakerClient:
    """Persistent WS-клиент колонки.

    Вызовы доступные после успешного `connect()`:
      get_state(), get_metadata(), get_queue()
      set_volume(), media_play(), media_pause(), media_next(), media_prev()
      seek_to(sec), keep_alive()

    Также можно подписаться на async state-updates через callback в `__init__`.
    """

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        client_id: Optional[str] = None,
        client_name: str = "Home Assistant",
        pin_access_token: Optional[str] = None,
        on_event: Optional[Callable[[bytes, dict[int, Any]], Awaitable[None]]] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id or str(uuid.uuid4())
        self.client_name = client_name
        self.pin_access_token = pin_access_token
        self._on_event = on_event

        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._listener_task: Optional[asyncio.Task] = None
        self._keepalive_task: Optional[asyncio.Task] = None
        self._pending: dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()

    # ────────────────────────────── connection ──────────────────────────────

    @staticmethod
    def _make_ssl_context() -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    async def connect(self) -> None:
        # ssl.create_default_context() читает CA bundle с диска (blocking I/O).
        # HA в dev-mode логирует/блокирует это, поэтому уходим в executor.
        ssl_ctx = await asyncio.to_thread(self._make_ssl_context)

        url = f"wss://{self.host}:{self.port}/"
        _LOGGER.debug("connecting to %s as client_id=%s", url, self.client_id)
        connect_kwargs: dict[str, Any] = {
            "ssl": ssl_ctx,
            "max_size": 2**20,
            "open_timeout": 10,
            "ping_interval": None,
            _HEADERS_KWARG: [("User-Agent", DEFAULT_USER_AGENT)],
        }
        self._ws = await websockets.connect(url, **connect_kwargs)
        # NB: listener НЕ стартуется автоматически — он конфликтует с прямыми
        # recv()-вызовами в pair_with_button. После завершения pair (или сразу
        # при reconnect к спареной колонке) надо вызвать start_listening().

    def start_listening(self) -> None:
        """Запускает фоновый dispatcher async-сообщений от колонки.
        Должен быть вызван ПОСЛЕ pair_with_button() (если pair проходил)
        либо сразу после connect() для уже спареной колонки.
        """
        if self._listener_task and not self._listener_task.done():
            return
        self._listener_task = asyncio.create_task(self._listen_loop(), name="sboom-listener")

    async def close(self) -> None:
        if self._keepalive_task:
            self._keepalive_task.cancel()
            self._keepalive_task = None
        if self._listener_task:
            self._listener_task.cancel()
            self._listener_task = None
        if self._ws:
            try:
                await self._ws.close()
            except Exception:  # pragma: no cover
                pass
            self._ws = None

    # ────────────────────────────── pair flow ──────────────────────────────

    async def pair_with_button(self) -> str:
        """Запустить pair-handshake. Пользователь должен нажать '+' на колонке.

        Возвращает свежий `pin_access_token`. Также сохраняет его в self.
        """
        if not self._ws:
            raise RuntimeError("not connected")

        # отправляем pair-init (пустой запрос)
        cast = _field(OP_PIN_CONNECT, 2, _field(1, 2, b""))
        req_id = str(uuid.uuid4())
        pkt = self._envelope(req_id, cast)
        await self._ws.send(pkt)
        _LOGGER.info("pair: init sent (req_id=%s, %d bytes) — нажмите '+'",
                     req_id, len(pkt))

        loop = asyncio.get_running_loop()
        deadline = loop.time() + PAIR_BUTTON_TIMEOUT_SEC
        msg_idx = 0
        while loop.time() < deadline:
            remain = deadline - loop.time()
            try:
                resp = await asyncio.wait_for(self._ws.recv(), timeout=remain)
            except asyncio.TimeoutError:
                _LOGGER.warning("pair: timed out waiting for pair-confirm response")
                break
            msg_idx += 1
            parsed = _decode_tlv(resp if isinstance(resp, (bytes, bytearray)) else resp.encode())
            _LOGGER.info("pair: msg #%d (%d bytes) parsed=%s", msg_idx, len(resp), parsed)

            req_data = parsed.get(5)
            if not isinstance(req_data, dict):
                continue

            # Первый ответ (поле 4): статус-код в sub[1], опц. данные в sub[2].
            # Эмпирически наблюдаемые статусы:
            #   1 → ждём подтверждения (нажатия "+"), sub[2] — идентификатор сессии
            #   2 → авторизовано, sub[2] — pin-токен
            #   3 → сессия уже занята
            #   5 → pair-режим выключен на колонке
            pin_resp = req_data.get(4)
            if isinstance(pin_resp, dict):
                status = pin_resp.get(1)
                if status == 2:
                    token = pin_resp.get(2)
                    if isinstance(token, str) and len(token) >= 16:
                        _LOGGER.info("pair: token received (init-stage) -> %s", token)
                        self.pin_access_token = token
                        return token
                elif status == 1:
                    sess = pin_resp.get(2)
                    _LOGGER.info(
                        "pair: waiting for '+' button press (session=%s)", sess
                    )
                    # просто ждём — колонка пришлёт следующий ответ после нажатия
                    continue
                elif status == 3:
                    raise PairTimeout("pair: session already active")
                elif status == 5:
                    raise PairTimeout("pair: mode disabled on speaker")

            # Второй ответ (поле 6) — после нажатия "+":
            #   1 → авторизовано, sub[2] — финальный pin-токен
            #   2 → отказано
            confirm = req_data.get(6)
            if isinstance(confirm, dict):
                status = confirm.get(1)
                if status == 1:
                    token = confirm.get(2)
                    if isinstance(token, str) and len(token) >= 16:
                        _LOGGER.info("pair: token received (confirm-stage) -> %s", token)
                        self.pin_access_token = token
                        return token
                elif status == 2:
                    raise PairTimeout("pair: rejected by speaker")
        raise PairTimeout("pair timed out — кнопка '+' не была нажата")

    # ────────────────────────────── high-level commands ──────────────────────────────

    async def get_state(self) -> SpeakerState:
        resp = await self._request_response(_field(OP_GET_STATE, 2, _field(1, 2, b"")))
        return self._extract_state(resp)

    async def get_metadata(self) -> Optional[TrackInfo]:
        resp = await self._request_response(_field(OP_GET_META_DATA, 2, _field(1, 2, b"")))
        return self._extract_track(resp)

    async def get_queue(self) -> dict[int, Any]:
        resp = await self._request_response(_field(OP_GET_PLAYING_QUEUE, 2, _field(1, 2, b"")))
        return _decode_tlv(resp)

    async def set_volume(self, percent: int) -> None:
        percent = max(0, min(100, int(percent)))
        cast = _field(OP_SET_VOLUME, 2, _field(1, 0, percent))
        await self._fire_and_forget(cast)

    async def media_play(self) -> None:
        await self._send_media_command(MEDIA_CMD_PLAY)

    async def media_pause(self) -> None:
        await self._send_media_command(MEDIA_CMD_PAUSE)

    async def media_next(self) -> None:
        await self._send_media_command(MEDIA_CMD_NEXT)

    async def media_prev(self) -> None:
        await self._send_media_command(MEDIA_CMD_PREV)

    async def media_mute(self) -> None:
        await self._send_media_command(MEDIA_CMD_MUTE)

    async def media_unmute(self) -> None:
        await self._send_media_command(MEDIA_CMD_UNMUTE)

    async def media_like(self) -> None:
        await self._send_media_command(MEDIA_CMD_LIKE)

    async def media_remove_like(self) -> None:
        await self._send_media_command(MEDIA_CMD_REMOVE_LIKE)

    async def media_dislike(self) -> None:
        await self._send_media_command(MEDIA_CMD_DISLIKE)

    async def media_remove_dislike(self) -> None:
        await self._send_media_command(MEDIA_CMD_REMOVE_DISLIKE)

    async def media_shuffle(self, on: bool) -> None:
        await self._send_media_command(MEDIA_CMD_SHUFFLE_ON if on else MEDIA_CMD_SHUFFLE_OFF)

    async def media_repeat(self, mode: str) -> None:
        """mode: 'none' | 'playlist'/'all' | 'track'/'one'"""
        cmd_by_mode = {
            "none":     MEDIA_CMD_REPEAT_NONE,
            "playlist": MEDIA_CMD_REPEAT_PLAYLIST,
            "all":      MEDIA_CMD_REPEAT_PLAYLIST,
            "track":    MEDIA_CMD_REPEAT_TRACK,
            "one":      MEDIA_CMD_REPEAT_TRACK,
        }
        await self._send_media_command(cmd_by_mode.get(mode.lower(), MEDIA_CMD_REPEAT_NONE))

    async def seek_to(self, position_sec: int) -> None:
        # seek-операция: единица — секунды (наблюдаемое поведение)
        cast = _field(OP_SET_TRACK_POS, 2, _field(1, 0, int(position_sec)))
        await self._fire_and_forget(cast)

    async def keep_alive(self) -> None:
        await self._fire_and_forget(_field(OP_KEEP_ALIVE, 2, _field(1, 2, b"")))

    # ────────────────────────────── internals ──────────────────────────────

    async def _send_media_command(self, action: int) -> None:
        # медиа-команда: action — опкод из MEDIA_CMD_*, поле 1 в media-command-операции
        request_inner = _field(1, 0, action)
        cast = _field(OP_MEDIA_COMMAND, 2, request_inner)
        await self._fire_and_forget(cast)

    def _envelope(self, req_id: str, request_data: bytes, *, with_token: bool = True) -> bytes:
        parts = [
            _field(1, 0, 2),                                 # type=REQUEST
            _field(2, 2, req_id.encode()),                   # id
        ]
        if with_token and self.pin_access_token:
            parts.append(_field(3, 2, self.pin_access_token.encode()))
        parts += [
            _field(5, 2, request_data),                      # request_data
            _field(6, 0, TOKEN_TYPE_PIN_AUTH),               # token_type
            _field(7, 2, self.client_name.encode()),         # client_name
            _field(10, 0, 1),                                # is_request=true
            _field(11, 2, self.client_id.encode()),          # client_id
        ]
        return b"".join(parts)

    async def _fire_and_forget(self, request_data: bytes) -> None:
        if not self._ws:
            raise RuntimeError("not connected")
        async with self._lock:
            await self._ws.send(self._envelope(str(uuid.uuid4()), request_data))

    async def _request_response(self, request_data: bytes, timeout: float = 5.0) -> bytes:
        if not self._ws:
            raise RuntimeError("not connected")
        req_id = str(uuid.uuid4())
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        try:
            async with self._lock:
                await self._ws.send(self._envelope(req_id, request_data))
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(req_id, None)

    async def _listen_loop(self) -> None:
        assert self._ws is not None
        try:
            async for msg in self._ws:
                if isinstance(msg, str):
                    msg = msg.encode()
                parsed = _decode_tlv(msg)
                # сматчить с pending по id, либо считать unsolicited
                msg_id = parsed.get(2)
                if isinstance(msg_id, str) and msg_id in self._pending:
                    fut = self._pending.pop(msg_id)
                    if not fut.done():
                        fut.set_result(msg)
                    continue

                if self._on_event:
                    try:
                        await self._on_event(msg, parsed)
                    except Exception:  # pragma: no cover
                        _LOGGER.exception("on_event handler raised")
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("listen loop crashed")

    # ────────────────────────────── parsers ──────────────────────────────

    @staticmethod
    def parse_state(raw: bytes) -> SpeakerState:
        """Public wrapper — делегирует в _parsers.parse_state."""
        return _parse_state(raw)

    @staticmethod
    def parse_track(raw: bytes) -> Optional[TrackInfo]:
        """Public wrapper — делегирует в _parsers.parse_track."""
        return _parse_track(raw)

    # backwards-compat алиасы для внутренних вызовов в этом классе
    _extract_state = staticmethod(_parse_state)
    _extract_track = staticmethod(_parse_track)
