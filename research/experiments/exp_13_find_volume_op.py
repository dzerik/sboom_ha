"""exp_13 — поиск set-volume op (НЕ через op=16).

КОНТЕКСТ: action=0/1 на op=16 = mute/unmute. Но set-volume(N%) должен быть
отдельной командой. Sweep всех ops 1..40 с inner=field(1,0,50) → diff
volume.percent.

РЕЗУЛЬТАТ:
- Section A показал шум (физический пользователь крутил volume параллельно)
- Section C (deliberate v=10/30/80) дал ЧИСТЫЙ pattern на op=14:
    v=10 → percent=10
    v=30 → percent=30
    v=80 → percent=75 (capped! max-volume safety)
- nested-input на op=14 → ignored (volume не меняется)

ВЫВОД: op=14 = SET_VOLUME с inner field(1, 0, percent), max=75
"""
from __future__ import annotations

import asyncio
import json

from _helpers import find_jsons, send_recv
from _shared import field


async def get_state():
    raw = await send_recv(12, timeout=4.0)
    if isinstance(raw, str): raw = raw.encode()
    for j in find_jsons(raw):
        try:
            o = json.loads(j)
            if isinstance(o, dict) and "volume" in o:
                return o
        except json.JSONDecodeError:
            pass
    return {}


async def get_volume_pct():
    s = await get_state()
    v = (s.get("volume") or {})
    return v.get("percent"), v.get("muted")


async def main():
    base_pct, base_muted = await get_volume_pct()
    print(f"baseline: percent={base_pct} muted={base_muted}\n")

    # Section A: noisy sweep
    print("=== A) Sweep ops 1..40 with inner=field(1,0,50) ===")
    print("    NOTE: this section is noisy — human/device may change volume in parallel.")
    for op in range(1, 41):
        await send_recv(op, inner=field(1, 0, 50), timeout=2.0)
        await asyncio.sleep(0.4)
        pct, _ = await get_volume_pct()
        if pct != base_pct:
            print(f"  ★ op={op:2d}: volume {base_pct}→{pct}")
            base_pct = pct

    # Section C: clean confirmation on op=14
    print("\n=== C) op=14 with varied params (clean test) ===")
    for v in [10, 30, 50, 80]:
        await send_recv(14, inner=field(1, 0, v), timeout=2.0)
        await asyncio.sleep(0.5)
        pct, _ = await get_volume_pct()
        match = "✓" if (pct == v or (v >= 75 and pct == 75)) else "✗"
        print(f"  op=14 v={v:2d}: percent={pct} {match}")

    # Test nested ignoring
    await send_recv(14, inner=field(1, 2, field(1, 0, 50)), timeout=2.0)
    await asyncio.sleep(0.5)
    pct, _ = await get_volume_pct()
    print(f"  op=14 nested → percent={pct} (expected: ignored, no change)")


if __name__ == "__main__":
    asyncio.run(main())
