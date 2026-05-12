"""Бинарный TLV-кодек: varint + length-delimited.

kind=0 → varint, kind=2 → length-delimited (bytes / nested / utf-8).
Используется для упаковки/распаковки сообщений с колонкой.
"""
from __future__ import annotations

from typing import Any


def varint(n: int) -> bytes:
    out = bytearray()
    while n > 0x7f:
        out.append((n & 0x7f) | 0x80)
        n >>= 7
    out.append(n & 0x7f)
    return bytes(out)


def field(tag: int, kind: int, payload: int | bytes) -> bytes:
    """Закодировать одно поле. kind=0 (varint), kind=2 (length-delim)."""
    key = (tag << 3) | kind
    if kind == 0:
        return varint(key) + varint(int(payload))  # type: ignore[arg-type]
    if kind == 2:
        return varint(key) + varint(len(payload)) + payload  # type: ignore[arg-type]
    raise ValueError(f"unsupported kind: {kind}")


def decode(data: bytes) -> dict[int, Any]:
    """Рекурсивный TLV-декодер. Length-delim поля пробуются как UTF-8 → nested → hex."""
    out: dict[int, Any] = {}
    i = 0
    n = len(data)
    while i < n:
        key, shift = 0, 0
        while True:
            if i >= n:
                return out
            b = data[i]
            i += 1
            key |= (b & 0x7f) << shift
            if b & 0x80 == 0:
                break
            shift += 7
        f, kind = key >> 3, key & 0x7
        if kind == 0:
            v, shift = 0, 0
            while True:
                if i >= n:
                    return out
                b = data[i]
                i += 1
                v |= (b & 0x7f) << shift
                if b & 0x80 == 0:
                    break
                shift += 7
            out[f] = v
        elif kind == 2:
            ln, shift = 0, 0
            while True:
                if i >= n:
                    return out
                b = data[i]
                i += 1
                ln |= (b & 0x7f) << shift
                if b & 0x80 == 0:
                    break
                shift += 7
            payload = data[i : i + ln]
            i += ln
            try:
                s = payload.decode("utf-8")
                if all(c.isprintable() or c in "\n\t" for c in s):
                    out[f] = s
                    continue
            except UnicodeDecodeError:
                pass
            try:
                nested = decode(payload)
                out[f] = nested if nested else payload.hex()
            except Exception:  # pragma: no cover
                out[f] = payload.hex()
        else:
            break
    return out
