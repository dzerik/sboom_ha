"""exp_09 — fuzz inner-полей для pair-flow ops (4, 5, 6).

КОНТЕКСТ: после exp_02 знаем что op=4/5/6 — pair-flow триплет, но детали
их inner-payload'ов не ясны. Перебираем разные inner contents.

РЕЗУЛЬТАТ:
- op=4 (pair-init):
    field(1, 2, b"") empty       → {4:{1:1, 2:'session-uuid'}} ← awaiting (status=1)
    field(1, 0, N) any varint    → {4:{1:4}}                   ← error
    field(1, 2, "string") any    → {4:{1:4}}                   ← error
  ⇒ op=4 принимает СТРОГО empty length-delim subfield 1

- op=5 (pair-cancel):
    любой inner → {5:''} ack — отменяет pair-mode (озвучивает)

- op=6 (pair-confirm-ack):
    любой inner → {6:''} ack — без visible effect
    str-input иногда timeout (зависит от device state)

СТРУКТУРНЫЙ ИНСАЙТ: response.body симметричен — содержит echo op-tag
из request. Внутри — field(1,0,status) + опц field(2,2,data).
"""
from __future__ import annotations

import asyncio

from _helpers import send_recv
from _shared import decode, field


async def main():
    cases = [
        ("empty",       field(1, 2, b"")),
        ("v1=0",        field(1, 0, 0)),
        ("v1=1",        field(1, 0, 1)),
        ("v1=2",        field(1, 0, 2)),
        ("v2=1",        field(2, 0, 1)),
        ("str1='no'",   field(1, 2, b"no")),
    ]
    for op in [4, 5, 6]:
        print(f"\n=== op={op} ===")
        for label, inner in cases:
            raw = await send_recv(op, inner=inner, timeout=2.5)
            if raw is None:
                print(f"  {label:14s}: timeout")
                continue
            d = decode(raw)
            print(f"  {label:14s}: {len(raw):4d}b  decoded={d}")


if __name__ == "__main__":
    asyncio.run(main())
