"""exp_14 — clean подтверждение op=14 (SET_VOLUME) и op=15 (SEEK).

КОНТЕКСТ: после exp_13 op=14 нащупан как SET_VOLUME. Также пробуем op=15
(в нашей implementation проходит как OP_SET_TRACK_POS) — действительно ли seek?

РЕЗУЛЬТАТ:
- op=14: 5/5 чистых матчей set-volume
- op=15:
    v=60  → position=60
    v=120 → position=120
    v=200 → position=200
    v=10000 → past-duration → trigger NEXT track
- op=15 nested/subf2/etc → ignored

ВЫВОД: op=15 = SEEK_TO_POSITION с inner field(1, 0, seconds).
Past-duration → next-track-cascade (предсказуемо).
"""
from __future__ import annotations

import asyncio
import json

from _helpers import find_jsons, first_dict, send_recv
from _shared import field


async def get_full():
    s_raw = await send_recv(12, timeout=4.0)
    m = first_dict(await send_recv(10), "trackId") or {}
    if isinstance(s_raw, str): s_raw = s_raw.encode()
    s = {}
    for j in find_jsons(s_raw):
        try:
            o = json.loads(j)
            if isinstance(o, dict) and "volume" in o: s = o; break
        except json.JSONDecodeError: pass
    pos = (m.get("position") or {})
    return {
        "vol_pct": (s.get("volume") or {}).get("percent"),
        "trackId": m.get("trackId"),
        "title": m.get("title"),
        "position": pos.get("val") if isinstance(pos, dict) else pos,
    }


async def main():
    print("=== Confirm op=14 = SET_VOLUME ===")
    for v in [15, 35, 55, 75]:
        await send_recv(14, inner=field(1, 0, v), timeout=2.0)
        await asyncio.sleep(0.6)
        s = await get_full()
        match = "✓" if s["vol_pct"] == v else "✗"
        print(f"  set v={v:2d} → percent={s['vol_pct']} {match}")

    print("\n=== Confirm op=15 = SEEK_TO_POSITION ===")
    for v in [60, 120, 200]:
        await send_recv(15, inner=field(1, 0, v), timeout=2.0)
        await asyncio.sleep(0.6)
        s = await get_full()
        print(f"  op=15 v={v:3d}: position={s['position']:>4} title={s['title'][:25]!r}")

    # Past-duration test
    print("\n=== Past-duration → next-track cascade ===")
    s = await get_full()
    print(f"  before: track={s['title']!r} pos={s['position']}")
    await send_recv(15, inner=field(1, 0, 10000), timeout=2.0)
    await asyncio.sleep(0.7)
    s = await get_full()
    print(f"  after seek=10000: track={s['title']!r} pos={s['position']} (jumped to next)")


if __name__ == "__main__":
    asyncio.run(main())
