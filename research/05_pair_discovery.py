"""05 — Pair discovery (finding the auth handshake).

После 03/04 у нас есть гипотеза по обёртке (req_id-tag, body-tag, client_id-tag).
Здесь:

1) Эмулируем разные кандидаты «init»-сообщений (пустые подзапросы под разными
   body-tag номерами).
2) Для каждого — слушаем 30 сек ответ, ищем индикаторы:
   - reply содержит status-enum (короткий varint в первом subfield ответа)
   - reply содержит длинную (>=16 chars) printable-string — типичный токен/session
   - устройство физически реагирует (мигает индикатор) — оператор подтверждает
3) Когда найден candidate-init: оператор физически жмёт кнопку pair на устройстве.
   Скрипт ловит follow-up reply и извлекает токен (longest printable string).

Этот скрипт интерактивный — диалог с оператором.

Использование:
    python research/05_pair_discovery.py --host <host> --port <port> \\
        --envelope-rid-tag <int> --envelope-body-tag <int> \\
        [--client-id-tag <int>]

Все tag-номера — required и берутся из вывода 04_fuzz_envelope.py.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import time
import uuid
from typing import Any

from _shared import decode, field, ws_open


def _build_envelope(
    body: bytes,
    *,
    rid_tag: int,
    body_tag: int,
    client_id_tag: int | None,
    extra_fields: list[tuple[int, int, Any]] | None = None,
) -> bytes:
    """Собрать обёртку из нащупанных fuzz'ом тегов."""
    parts = [field(rid_tag, 2, str(uuid.uuid4()).encode())]
    if client_id_tag is not None:
        parts.append(field(client_id_tag, 2, str(uuid.uuid4()).encode()))
    if extra_fields:
        for t, k, v in extra_fields:
            parts.append(field(t, k, v))
    parts.append(field(body_tag, 2, body))
    return b"".join(parts)


def _scan_for_token(tree: Any) -> str | None:
    """Эвристика: ищем длинную printable-string глубоко в TLV-tree.
    Большинство pair-токенов — base64 / hex длиной >= 16."""
    if isinstance(tree, str):
        if len(tree) >= 16 and re.fullmatch(r"[A-Za-z0-9+/=._\-]+", tree):
            return tree
        return None
    if isinstance(tree, dict):
        for v in tree.values():
            t = _scan_for_token(v)
            if t:
                return t
    return None


async def _try_init(
    host: str, port: int, op_tag: int, *,
    rid_tag: int, body_tag: int, client_id_tag: int | None,
    listen_seconds: float = 60.0,
) -> dict[str, Any]:
    """Шлём envelope с пустым подзапросом под body-tag = op_tag, слушаем."""
    try:
        ws = await ws_open(host, port)
    except Exception as e:  # noqa: BLE001
        return {"error": f"connect: {e!r}"}

    inner_empty = field(1, 2, b"")  # subfield "1" пустой — догадка про request-args
    body_payload = field(op_tag, 2, inner_empty)
    pkt = _build_envelope(
        body_payload,
        rid_tag=rid_tag, body_tag=body_tag, client_id_tag=client_id_tag,
    )
    try:
        await ws.send(pkt)
    except Exception as e:  # noqa: BLE001
        return {"error": f"send: {e!r}"}

    msgs: list[dict[str, Any]] = []
    deadline = time.time() + listen_seconds
    token: str | None = None

    while time.time() < deadline:
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=deadline - time.time())
        except asyncio.TimeoutError:
            break
        except Exception:  # noqa: BLE001
            break
        if isinstance(msg, str):
            msg = msg.encode()
        tree = decode(msg)
        msgs.append({"size": len(msg), "tree": tree})
        t = _scan_for_token(tree)
        if t and len(t) >= 32:
            token = t
            break

    try:
        await ws.close()
    except Exception:  # noqa: BLE001
        pass

    return {"messages": msgs, "token": token}


async def main(args):
    print(f"[+] Pair discovery on {args.host}:{args.port}")
    print(f"    Envelope tags: rid={args.envelope_rid_tag}, body={args.envelope_body_tag}, "
          f"client_id={args.client_id_tag}")
    print(f"    Probing op_tag in 1..{args.max_op}\n")

    candidates: list[tuple[int, dict[str, Any]]] = []
    for op_tag in range(1, args.max_op + 1):
        print(f"\n━━━ Trying init op_tag={op_tag} ━━━━━━━━━━━━━━━━")
        result = await _try_init(
            args.host, args.port, op_tag,
            rid_tag=args.envelope_rid_tag,
            body_tag=args.envelope_body_tag,
            client_id_tag=args.client_id_tag,
            listen_seconds=args.listen_seconds,
        )
        if result.get("error"):
            print(f"    error: {result['error']}")
            continue

        n_msgs = len(result["messages"])
        token = result.get("token")
        if n_msgs == 0:
            print("    no replies — probably wrong op_tag")
            continue

        first_keys = list(result["messages"][0]["tree"].keys()) if result["messages"] else []
        print(f"    {n_msgs} message(s), first reply tags={first_keys}")

        if token:
            print(f"    [!!!] Found long printable string (token candidate):")
            print(f"          {token!r}")
            candidates.append((op_tag, {"token": token, "messages": result["messages"]}))

        if args.confirm_each and n_msgs > 0:
            ans = input("    >>> Did the device do anything physical (LED, sound)? [y/N/q] ")
            if ans.lower() == "q":
                break
            if ans.lower() == "y":
                print(f"    *** op_tag={op_tag} candidate for INIT (LED reaction confirmed) ***")
                print(f"    *** Now press the pair button on the device physically. Listening 60s … ***")
                # Снова открываем сессию и ждём дольше
                result2 = await _try_init(
                    args.host, args.port, op_tag,
                    rid_tag=args.envelope_rid_tag,
                    body_tag=args.envelope_body_tag,
                    client_id_tag=args.client_id_tag,
                    listen_seconds=60.0,
                )
                if result2.get("token"):
                    print(f"    *** TOKEN: {result2['token']} ***")
                    candidates.append((op_tag, result2))

    print("\n─── Summary ────────────────────────────────────")
    if not candidates:
        print("    No init candidates found. Try larger --max-op or different tags.")
    for op_tag, c in candidates:
        print(f"    op_tag={op_tag} token={c.get('token')!r}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--host", required=True)
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--max-op", type=int, default=20)
    p.add_argument("--listen-seconds", type=float, default=4.0)
    p.add_argument("--envelope-rid-tag", type=int, required=True,
                   help="Tag for request-id (from 04_fuzz_envelope output)")
    p.add_argument("--envelope-body-tag", type=int, required=True,
                   help="Tag for request-data wrapper (from 04_fuzz_envelope)")
    p.add_argument("--client-id-tag", type=int, default=None,
                   help="Tag for client-id (from 04_fuzz_envelope, optional)")
    p.add_argument("--confirm-each", action="store_true",
                   help="Ask operator after each candidate (interactive)")
    asyncio.run(main(p.parse_args()))
