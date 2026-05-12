"""exp_20 — sweep ops 41..80, ищем any non-default reply.

КОНТЕКСТ: ops 24..40 в exp_15 показали 44b status=1 ack без body —
default-ack. Возможно это «не существующие ops». А реальные ops для
других subsystems (LED, alarm, multiroom, etc.) лежат **выше**.

МЕТОД: для op=41..80 шлём empty inner, смотрим:
- 44b status=1 → default-ack (не существует)
- ≠ 44b или body не пустой → реальный op
- timeout → может быть ждёт specific schema

EXPECTED: найти ops с non-default reply'ями для дальнейшего investigation.
"""
from __future__ import annotations

import asyncio

from _helpers import send_recv
from _shared import decode, field


async def main():
    print("=== Sweep ops 41..80 (looking for non-default replies) ===\n")
    interesting = []
    for op in range(41, 81):
        raw = await send_recv(op, inner=field(1, 2, b""), timeout=1.5)
        if not raw:
            print(f"  op={op:2d}: timeout")
            interesting.append((op, "timeout", 0, ""))
            continue
        d = decode(raw)
        body5 = d.get(5, "")
        kind = ("dict" if isinstance(body5, dict)
                else ("str" if isinstance(body5, str) and body5 else "empty"))
        is_default = (len(raw) == 44 and d.get(3) == 1 and not body5)
        marker = "★" if not is_default else " "
        info = f"{len(raw):3d}b status={d.get(3,'?'):>3} body={kind}"
        if isinstance(body5, dict):
            info += f" keys={list(body5.keys())}"
        print(f"  {marker} op={op:2d}: {info}")
        if not is_default:
            interesting.append((op, "interesting", len(raw), str(d)))

    print(f"\n=== Summary: {len(interesting)} non-default ops ===")
    for op, kind, sz, info in interesting[:20]:
        print(f"  op={op}: {kind} {sz}b")


if __name__ == "__main__":
    asyncio.run(main())
