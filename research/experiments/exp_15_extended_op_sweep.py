"""exp_15 — расширенный op-sweep ops 18..40 с auth.

КОНТЕКСТ: base sweep был 1..24. Расширяем до 40 чтобы увидеть что вне диапазона.
Также подробно decoded body5 для interesting ops.

РЕЗУЛЬТАТ (с empty inner):
  op=18: timeout (любой inner) — peculiar, требует specific format
  op=19: 45b ack {19: ''}
  op=20: 47b reply {20: {1: 1}} — persistent constant
  op=21: 47b reply {21: {1: 1}} — same constant
  op=22: 45b ack {22: ''}
  op=23: 44b reply {15: ''} — ECHO 15 ВМЕСТО 23! alias на op=15 (SEEK)?
  op=24..40: 44b status=1 ack без body — non-existent ops с default-ack?

Op=11 + str-input → reply {11: {1: 'foo'}} — echo command (test/diagnostic).
"""
from __future__ import annotations

import asyncio
import json

from _helpers import find_jsons, send_recv
from _shared import decode, field


async def main():
    print("=== Extended op-sweep 18..40 ===\n")

    # Per-op detailed
    for op in [11, 18, 19, 20, 21, 22, 23]:
        for label, inner in [
            ("empty",       field(1, 2, b"")),
            ("v1=1",        field(1, 0, 1)),
            ("str='foo'",   field(1, 2, b"foo")),
        ]:
            raw = await send_recv(op, inner=inner, timeout=2.0)
            if not raw:
                print(f"  op={op:2d} {label:12s}: timeout")
                continue
            d = decode(raw)
            print(f"  op={op:2d} {label:12s}: {len(raw):4d}b decoded={d}")
        print()

    # Quick ops 24..40
    print("=== Ops 24..40 (quick) ===")
    for op in range(24, 41):
        raw = await send_recv(op, timeout=1.5)
        if not raw:
            print(f"  op={op:2d}: timeout")
            continue
        d = decode(raw)
        print(f"  op={op:2d}: {len(raw):4d}b status={d.get(3,'?')} body5={d.get(5,'')!r}")


if __name__ == "__main__":
    asyncio.run(main())
