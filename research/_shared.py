"""Минимальный generic TLV-decoder + WebSocket helper для black-box research.

Только из публичных стандартов:
- proto-wire encoding (https://protobuf.dev/programming-guides/encoding/)
- WebSocket RFC 6455

Самостоятельный toolkit: ничего не предполагает о target-устройстве,
протоколе, портах или вендоре.
"""
from __future__ import annotations

import json
import ssl
from typing import Any


# ── Generic proto-wire codec (без знания .proto-схемы) ──────────────────

def varint_encode(n: int) -> bytes:
    out = bytearray()
    while n > 0x7F:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n & 0x7F)
    return bytes(out)


def field(tag: int, kind: int, payload: int | bytes) -> bytes:
    """proto-wire field. kind=0 (varint), kind=2 (length-delimited)."""
    key = (tag << 3) | kind
    if kind == 0:
        return varint_encode(key) + varint_encode(int(payload))  # type: ignore[arg-type]
    if kind == 2:
        return varint_encode(key) + varint_encode(len(payload)) + payload  # type: ignore[arg-type]
    raise ValueError(f"unsupported wire-type: {kind}")


def varint_decode(data: bytes, pos: int) -> tuple[int, int]:
    n, shift = 0, 0
    while pos < len(data):
        b = data[pos]
        pos += 1
        n |= (b & 0x7F) << shift
        if not (b & 0x80):
            return n, pos
        shift += 7
    raise ValueError("varint truncated")


def decode(data: bytes) -> dict[int, Any]:
    """Generic proto-wire decoder. Возвращает древо tag → value.

    Length-delimited пробуем как:
      1) UTF-8 printable string,
      2) nested proto-wire (рекурсивно),
      3) hex (last resort).
    Это даёт читаемое представление БЕЗ знания .proto.
    """
    out: dict[int, Any] = {}
    pos, n = 0, len(data)
    while pos < n:
        try:
            key, pos = varint_decode(data, pos)
        except ValueError:
            break
        tag, kind = key >> 3, key & 0x7
        if kind == 0:
            v, pos = varint_decode(data, pos)
            out[tag] = v
        elif kind == 2:
            ln, pos = varint_decode(data, pos)
            payload, pos = data[pos : pos + ln], pos + ln
            try:
                s = payload.decode("utf-8")
                if all(c.isprintable() or c in "\n\t" for c in s):
                    out[tag] = s
                    continue
            except UnicodeDecodeError:
                pass
            try:
                nested = decode(payload)
                out[tag] = nested if nested else payload.hex()
            except Exception:  # noqa: BLE001
                out[tag] = payload.hex()
        else:
            # group / 32-bit / 64-bit — для нашего исследования редко нужны
            break
    return out


def pretty(d: Any, indent: int = 0) -> str:
    """Распечатать TLV-дерево в читаемом виде."""
    sp = "  " * indent
    if isinstance(d, dict):
        out = []
        for k, v in d.items():
            if isinstance(v, dict):
                out.append(f"{sp}tag={k}:")
                out.append(pretty(v, indent + 1))
            else:
                out.append(f"{sp}tag={k}: {v!r}")
        return "\n".join(out)
    return f"{sp}{d!r}"


def find_jsons(data: bytes) -> list[str]:
    """Эвристика: найти все валидные JSON-объекты в бинарном payload.

    Устройство часто кладёт чистый JSON в length-delimited поля — это легко
    отличить (начинается с `{`, валидно парсится).
    """
    s = data.decode("utf-8", errors="ignore")
    out: list[str] = []
    pos = 0
    while True:
        start = s.find("{", pos)
        if start < 0:
            break
        # Балансируем скобки
        depth, in_str, esc = 0, False, False
        end = -1
        for i in range(start, len(s)):
            ch = s[i]
            if in_str:
                if esc: esc = False
                elif ch == "\\": esc = True
                elif ch == '"': in_str = False
                continue
            if ch == '"': in_str = True
            elif ch == "{": depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end < 0:
            pos = start + 1
            continue
        candidate = s[start:end]
        try:
            json.loads(candidate)
            out.append(candidate)
            pos = end
        except json.JSONDecodeError:
            pos = start + 1
    return out


# ── WebSocket helper ─────────────────────────────────────────────────────

def insecure_ssl() -> ssl.SSLContext:
    """TLS-context без verify (большинство embedded использует self-signed)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def ws_open(host: str, port: int):
    """Открыть WebSocket-соединение (TLS на любом self-signed cert)."""
    import websockets

    url = f"wss://{host}:{port}/"
    return await websockets.connect(
        url,
        ssl=insecure_ssl(),
        max_size=2 ** 20,
        open_timeout=10,
        ping_interval=None,
    )
