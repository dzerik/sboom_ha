"""exp_04 — op-sweep С токеном. Найти GET_STATE / GET_METADATA / MEDIA_COMMAND.

КОНТЕКСТ: после exp_03 имеем токен. Повторяем sweep — теперь все ops
которые требовали auth должны отвечать осмысленно. Анализируем по
размеру reply: большие reply'и (>500b) → JSON-state-ops.

РЕЗУЛЬТАТ:
  op=10 → 882-899b   ← GET_METADATA (JSON с current track)
  op=12 → ~3600b     ← GET_STATE (full device state)
  op=16 → 45b ack    ← MEDIA_COMMAND candidate (принимает inner action)
  op=17 → ~287b      ← GET_QUEUE (track-id list)
  op=4  → 84b        ← pair-init (re-arms)
  ops 1, 2, 24 → 44b status=1 ack
  ops 8 → 46b ack    ← кандидат на media-cmd
  ops остальные → 44-46b с разными status code
"""
from __future__ import annotations

import asyncio

from _helpers import send_recv, find_jsons
from _shared import decode, field


async def main():
    print("Op-sweep WITH auth token — comparing reply types…\n")
    distinct = {}
    for op in range(1, 25):
        reply = await send_recv(op, timeout=4.0)
        if not reply:
            print(f"    op={op:2d}: timeout")
            continue
        d = decode(reply)
        body = d.get(5, "")
        has_json = bool(find_jsons(reply))
        kind = "dict" if isinstance(body, dict) else ("str" if isinstance(body, str) else "other")
        sig = (d.get(3, "?"), kind, has_json, len(reply))
        marker = "★" if sig not in distinct else " "
        distinct.setdefault(sig, []).append(op)
        print(f"  {marker} op={op:2d}: {len(reply):4d}b status={d.get(3,'?'):>3} "
              f"body={kind} json={has_json}")
    print()
    print("=== Hypothesis based on size + json presence ===")
    print("    Large + JSON → state-ops (GET_STATE, GET_METADATA, GET_QUEUE)")
    print("    Small + ack → command-ops (MEDIA_COMMAND, etc.)")


if __name__ == "__main__":
    asyncio.run(main())
