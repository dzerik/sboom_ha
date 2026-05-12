"""Общие helpers для experiments/. Не для production — для read-and-learn."""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

# Чтобы можно было `from _shared import ...` из родительского research/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _shared import decode, field, find_jsons, ws_open  # noqa: E402

from _config import (  # noqa: E402
    ENV_BODY_TAG, ENV_CID_TAG, ENV_CLIENT_NAME_TAG, ENV_IS_REQUEST_TAG,
    ENV_RID_TAG, ENV_TOKEN_TAG, ENV_TOKEN_TYPE_TAG, ENV_TYPE_TAG,
    HOST, PORT, TOKEN,
)


def make_envelope(op: int, inner: bytes | None = None, token: str | None = TOKEN) -> bytes:
    """Собрать стандартный envelope для нашего устройства."""
    if inner is None:
        inner = field(1, 2, b"")
    body = field(op, 2, inner)
    parts = [
        field(ENV_TYPE_TAG, 0, 2),                            # type=REQUEST
        field(ENV_RID_TAG, 2, str(uuid.uuid4()).encode()),    # rid
    ]
    if token:
        parts.append(field(ENV_TOKEN_TAG, 2, token.encode()))
    parts += [
        field(ENV_BODY_TAG, 2, body),
        field(ENV_TOKEN_TYPE_TAG, 0, 1),
        field(ENV_CLIENT_NAME_TAG, 2, b"research"),
        field(ENV_IS_REQUEST_TAG, 0, 1),
        field(ENV_CID_TAG, 2, str(uuid.uuid4()).encode()),
    ]
    return b"".join(parts)


async def send_recv(op: int, inner: bytes | None = None,
                    token: str | None = TOKEN, timeout: float = 4.0) -> bytes | None:
    """Открыть свежее WS, отправить request, получить один reply (или None)."""
    pkt = make_envelope(op, inner, token)
    try:
        ws = await ws_open(HOST, PORT)
    except Exception:  # noqa: BLE001
        return None
    try:
        await ws.send(pkt)
        msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
        return msg if isinstance(msg, bytes) else msg.encode()
    except (asyncio.TimeoutError, Exception):  # noqa: BLE001
        return None
    finally:
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass


def first_dict(raw: bytes | None, must_have: str | None = None) -> dict | None:
    """Извлечь первый JSON-dict из raw payload (опц. требовать наличия ключа)."""
    if not raw:
        return None
    for j in find_jsons(raw):
        try:
            o = json.loads(j)
            if isinstance(o, dict) and (must_have is None or must_have in o):
                return o
        except json.JSONDecodeError:
            continue
    return None


def flatten(d, prefix: str = "") -> dict:
    """dict-of-dicts → flat {a.b.c: value}."""
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, (dict, list)):
                out.update(flatten(v, key))
            else:
                out[key] = v
    elif isinstance(d, list):
        for i, v in enumerate(d):
            out.update(flatten(v, f"{prefix}[{i}]"))
    return out
