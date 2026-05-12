"""exp_06 — full action-sweep на op=16 c diff state.

КОНТЕКСТ: exp_04 нашёл op=16 как кандидата MEDIA_COMMAND. Проверяем —
шлём для action=0..15 inner field(1, 0, action), сравниваем GET_METADATA
+ volume из GET_STATE до/после.

РЕЗУЛЬТАТ:
   0 = MUTE             (volume.muted: false→true)
   1 = UNMUTE           (volume.muted: true→false)
   2 = NEXT             (trackId forward)
   3 = PREV             (trackId backward)
   4 = PLAY             (no observable diff если уже playing — нужно exp_07)
   5 = PAUSE            (playing: true→false)
   6 = LIKE             (like: false→true)
   7 = REMOVE_LIKE      (like: true→false)
   8 = ?                (no observable diff в media — focus management)
   9 = SHUFFLE_ON       (shuffle: false→true)
  10 = SHUFFLE_OFF      (shuffle: true→false)
  11 = REPEAT_NONE      (no diff если уже none — нужно exp_07)
  12 = REPEAT_PLAYLIST  (repeatType: none→playlist)
  13 = REPEAT_TRACK     (playlist→track)
  14 = ?                (track→none + side-effect track-jump)
  15 = ?                (no observable diff)
"""
from __future__ import annotations

import asyncio

from _helpers import find_jsons, first_dict, send_recv
from _shared import field


async def snapshot():
    """Combined state: track-fields из GET_METADATA + volume из GET_STATE."""
    m = first_dict(await send_recv(10), must_have="trackId") or {}
    s = first_dict(await send_recv(12), must_have="volume") or {}
    cs = (s.get("capabilities_state") or {}).get("led_display") or {}
    return {
        "playing": m.get("playing"),
        "shuffle": m.get("shuffle"),
        "repeatType": m.get("repeatType"),
        "trackId": m.get("trackId"),
        "title": m.get("title"),
        "like": m.get("like"),
        "muted": (s.get("volume") or {}).get("muted"),
        "vol_pct": (s.get("volume") or {}).get("percent"),
        "led_on": cs.get("turned_on"),
    }


async def main():
    print("Action-sweep on op=16 (action 0..15)…\n")
    base = await snapshot()
    print(f"baseline: {base}\n")

    findings = []
    for action in range(0, 16):
        await send_recv(16, inner=field(1, 0, action), timeout=2.5)
        await asyncio.sleep(0.5)
        cur = await snapshot()
        diff = {k: (base.get(k), cur.get(k)) for k in cur if cur.get(k) != base.get(k)}
        marker = "★" if diff else " "
        msg = str(diff) if diff else "(no observable change)"
        print(f"  {marker} action={action:2d}: {msg}")
        if diff:
            findings.append((action, diff))
        base = cur

    print(f"\n=== Total findings: {len(findings)} ===")


if __name__ == "__main__":
    asyncio.run(main())
