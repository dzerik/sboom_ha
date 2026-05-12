"""06 — Action fuzz: восстановить семантику команд по diff state.

Предусловие:
- Auth-токен получен (05).
- Из 03/04 у нас есть гипотеза: какой op_tag возвращает большой JSON с playback-
  фактами (state-op), и какой принимает inner-action-int (action-op).

Метод:
1) Делаем state-op → запоминаем "до"
2) Шлём action-op(action=N) для N=0..max
3) state-op → запоминаем "после"
4) Diff JSON: какие поля изменились — это label семантики action=N
   (например 'volume.muted: false→true' = это mute-команда).

ВАЖНО: запускайте при играющем треке/активном состоянии устройства, иначе
часть реакций будет невидима в state-снапшотах.

Все tag-номера и op-номера — required, без default'ов. Берутся из вывода
предыдущих этапов (04, 05, или из protocol_map.json после auto_discover).

Использование:
    python research/06_action_fuzz.py --host <host> --port <port> \\
        --token <token> \\
        --rid-tag <int> --body-tag <int> [--client-id-tag <int>] \\
        --token-tag <int> [--token-type-tag <int>] [--token-type-value <int>] \\
        --get-state-op <int> --media-command-op <int>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from typing import Any

from _shared import decode, field, find_jsons, ws_open


def _build_envelope(
    body: bytes, *,
    rid_tag: int, body_tag: int,
    client_id_tag: int | None,
    extra: list[tuple[int, int, Any]] | None,
) -> bytes:
    parts = [field(rid_tag, 2, str(uuid.uuid4()).encode())]
    if extra:
        for t, k, v in extra:
            parts.append(field(t, k, v))
    if client_id_tag is not None:
        parts.append(field(client_id_tag, 2, str(uuid.uuid4()).encode()))
    parts.append(field(body_tag, 2, body))
    return b"".join(parts)


async def _send_recv(ws, payload: bytes, timeout: float = 3.0) -> bytes | None:
    try:
        await ws.send(payload)
    except Exception:  # noqa: BLE001
        return None
    try:
        msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
        return msg if isinstance(msg, bytes) else msg.encode()
    except asyncio.TimeoutError:
        return None


def _parse_state_json(raw: bytes | None) -> dict[str, Any] | None:
    if not raw:
        return None
    for js in find_jsons(raw):
        try:
            return json.loads(js)
        except json.JSONDecodeError:
            pass
    return None


def _flatten(d: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, key))
        else:
            out[key] = v
    return out


def _diff(before: dict[str, Any] | None, after: dict[str, Any] | None) -> list[str]:
    if not before or not after:
        return []
    fb, fa = _flatten(before), _flatten(after)
    changes = []
    keys = set(fb) | set(fa)
    for k in sorted(keys):
        if fb.get(k) != fa.get(k):
            changes.append(f"{k}: {fb.get(k)!r} → {fa.get(k)!r}")
    return changes


async def main(args):
    extra_envelope: list[tuple[int, int, Any]] = []
    if args.token and args.token_tag is not None:
        extra_envelope.append((args.token_tag, 2, args.token.encode()))
    if args.token_type_tag is not None:
        extra_envelope.append((args.token_type_tag, 0, args.token_type_value))
    if args.client_name_tag is not None:
        extra_envelope.append((args.client_name_tag, 2, b"research"))

    def envelope_for(body: bytes) -> bytes:
        return _build_envelope(
            body,
            rid_tag=args.rid_tag,
            body_tag=args.body_tag,
            client_id_tag=args.client_id_tag,
            extra=extra_envelope,
        )

    def get_state_packet() -> bytes:
        # пустой подзапрос: field(1, 2, empty) внутри field(GET_STATE_OP, 2, ...)
        inner = field(1, 2, b"")
        body = field(args.get_state_op, 2, inner)
        return envelope_for(body)

    def media_action_packet(action: int) -> bytes:
        # Гипотеза: media-command принимает action как varint в subfield=1
        inner = field(1, 0, action)
        body = field(args.media_command_op, 2, inner)
        return envelope_for(body)

    print(f"[+] Action fuzz on {args.host}:{args.port}")
    print(f"    GET_STATE op={args.get_state_op}, MEDIA_COMMAND op={args.media_command_op}")
    print(f"    Sweeping action = 0 .. {args.max_action}\n")

    ws = await ws_open(args.host, args.port)

    results: dict[int, dict[str, Any]] = {}

    # baseline state
    print("[+] baseline state…")
    baseline_raw = await _send_recv(ws, get_state_packet())
    baseline = _parse_state_json(baseline_raw)
    if baseline:
        print(f"    state keys: {list(baseline.keys())[:10]}")

    for action in range(args.max_action + 1):
        print(f"\n━━━ action={action} ━━━")
        await _send_recv(ws, media_action_packet(action), timeout=1.0)
        await asyncio.sleep(args.settle_delay)
        after_raw = await _send_recv(ws, get_state_packet())
        after = _parse_state_json(after_raw)
        changes = _diff(baseline, after)
        print(f"    diff: {changes if changes else '(no observable change)'}")
        results[action] = {"diff": changes}
        baseline = after  # следующее сравнение от текущего, не от исходного

    await ws.close()

    if args.out:
        with open(args.out, "w") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"\n[+] saved {args.out}")

    print("\n─── Summary ────────────────────────────────────")
    print("    Сопоставьте diff с известными концепциями:")
    print("    - 'volume.muted' меняется → mute/unmute")
    print("    - 'shuffle' меняется → shuffle on/off")
    print("    - 'repeatType' меняется → repeat-mode")
    print("    - 'trackId' меняется → next/prev (track jump)")
    print("    - 'playing' меняется → play/pause")
    print("    Без изменений → action может быть для другой подсистемы")
    print("    (например multi-room — реакция вне state JSON).")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--host", required=True)
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--token", required=True)
    p.add_argument("--max-action", type=int, default=20)
    p.add_argument("--settle-delay", type=float, default=0.6)
    p.add_argument("--out", default="actions.json")

    # envelope tag-роли (вход из 04 inferred output)
    p.add_argument("--rid-tag", type=int, required=True)
    p.add_argument("--body-tag", type=int, required=True)
    p.add_argument("--client-id-tag", type=int, default=None)
    p.add_argument("--token-tag", type=int, required=True,
                   help="Tag for auth-token in envelope")
    p.add_argument("--token-type-tag", type=int, default=None)
    p.add_argument("--token-type-value", type=int, default=0)
    p.add_argument("--client-name-tag", type=int, default=None)

    # op-tag-роли (из state-op identification в 04)
    p.add_argument("--get-state-op", type=int, required=True,
                   help="op-tag returning state JSON")
    p.add_argument("--media-command-op", type=int, required=True,
                   help="op-tag accepting inner action varint")

    asyncio.run(main(p.parse_args()))
