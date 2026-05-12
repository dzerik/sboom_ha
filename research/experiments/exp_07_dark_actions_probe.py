"""exp_07 — probe «тёмных» action 4, 8, 11, 14, 15 в подходящих state'ах.

КОНТЕКСТ: exp_06 показал что action 4, 11 не дают diff если initial state
уже = (playing, repeat=none). Меняем initial state, retry'им.
Action 8/14/15 не дают видимых diff'ов в media — нужно exp_12 для apps.

РЕЗУЛЬТАТ:
- action=4 из paused state → playing: false→true ✓ = PLAY
- action=11 из repeat=playlist → repeatType: playlist→none ✓ = REPEAT_NONE
- action=8, 15 → нет diff даже после смены state — focus management
"""
from __future__ import annotations

import asyncio

from _helpers import first_dict, send_recv
from _shared import field


async def get_simple_state():
    m = first_dict(await send_recv(10), "trackId") or {}
    return {
        "playing": m.get("playing"),
        "like": m.get("like"),
        "trackId": m.get("trackId"),
        "repeatType": m.get("repeatType"),
    }


async def diff_after(action: int):
    base = await get_simple_state()
    await send_recv(16, field(1, 0, action))
    await asyncio.sleep(0.5)
    cur = await get_simple_state()
    diff = {k: (base.get(k), cur.get(k)) for k in cur if base.get(k) != cur.get(k)}
    return base, cur, diff


async def main():
    print("=== action=4 from PAUSED state ===")
    await send_recv(16, field(1, 0, 5))    # pause first
    await asyncio.sleep(0.5)
    base, cur, diff = await diff_after(4)
    print(f"  baseline (paused): {base}")
    print(f"  diff: {diff}")    # ожидаем playing: False→True

    print("\n=== action=11 from repeat=playlist ===")
    await send_recv(16, field(1, 0, 12))    # repeat=playlist first
    await asyncio.sleep(0.5)
    base, cur, diff = await diff_after(11)
    print(f"  baseline (playlist): {base.get('repeatType')}")
    print(f"  diff: {diff}")    # ожидаем repeatType: playlist→none

    print("\n=== action=8 (with like=False baseline) ===")
    await send_recv(16, field(1, 0, 7))    # remove_like first
    await asyncio.sleep(0.5)
    base, cur, diff = await diff_after(8)
    print(f"  baseline: like={base.get('like')}")
    print(f"  diff: {diff}")    # обычно empty — это focus management

    print("\n=== action=15 ===")
    base, cur, diff = await diff_after(15)
    print(f"  diff: {diff}")    # обычно empty


if __name__ == "__main__":
    asyncio.run(main())
