"""exp_16 — поиск play-by-id (нужно для media_browser в HA).

КОНТЕКСТ: ищем как сказать колонке «играть конкретный track из queue».
GET_QUEUE (op=17) даёт список trackId — но как переключиться на него?

ГИПОТЕЗЫ:
1. op=18 (peculiar timeout) принимает trackId-varint
2. op=10 (GET_METADATA) или op=12 (GET_STATE) принимает trackId как filter
3. op=17 (GET_QUEUE) принимает index как jump-to-queue-position
4. op=16 (MEDIA_COMMAND) с extra subfield 2 = trackId

РЕЗУЛЬТАТ:
★ op=18 + field(1, 0, int(trackId)) → reply timeout, НО **track change'ивает!**
  То есть op=18 — silent play-by-id! (timeout не критичен, команда выполняется)
  Но trackId !== target — это либо queue-jump-by-index, либо play-by-id с
  preserve-context (играется именно этот track в queue).

  Hypothesis: op=18 = PLAY_QUEUE_INDEX или PLAY_TRACK_BY_ID (silent ack).
  Дополнительный sweep требуется чтобы узнать конкретный input format.

- op=10/12 с nested-trackId → 858b reply (но не подтверждено что
  фильтрует по trackId — output может быть тот же)
- op=17 + idx-varint → no track change
- op=16 a=2 + sub2=trackId → track change (но это просто action=2 next, sub2 ignored)
"""
from __future__ import annotations

import asyncio
import json

from _helpers import find_jsons, first_dict, send_recv
from _shared import decode, field


async def get_track():
    raw = await send_recv(10, timeout=3.0)
    m = first_dict(raw, "trackId") or {}
    return m.get("trackId"), m.get("title")


async def get_queue_ids() -> list:
    raw = await send_recv(17, timeout=3.0)
    if isinstance(raw, str): raw = raw.encode()
    out = []
    for j in find_jsons(raw):
        try:
            o = json.loads(j)
            if isinstance(o, dict) and "trackId" in o:
                out.append(o["trackId"])
        except json.JSONDecodeError:
            pass
    return out


async def main():
    cur_id, cur_title = await get_track()
    print(f"current: {cur_title!r} id={cur_id}")

    queue = await get_queue_ids()
    print(f"queue head: {queue[:5]}")
    target = next((t for t in queue if str(t) != str(cur_id)), None)
    if target is None:
        print("Need different target track")
        return
    print(f"target: {target}\n")

    # === A) op=18 with varied trackId formats ===
    print("=== A) op=18 — peculiar silent op ===")
    for label, inner in [
        ("trackId varint",      field(1, 0, int(target))),
        ("trackId str",         field(1, 2, str(target).encode())),
        ("subf2 trackId-str",   field(2, 2, str(target).encode())),
    ]:
        before, _ = await get_track()
        raw = await send_recv(18, inner=inner, timeout=2.0)
        await asyncio.sleep(0.7)
        after, after_title = await get_track()
        marker = "★" if before != after else " "
        reply_info = f"reply {len(raw)}b" if raw else "timeout"
        print(f"  {marker} {label:22s}: {reply_info}, track {before}→{after} ({after_title[:25]!r})")

    # === B) op=10/12 with nested trackId ===
    print("\n=== B) op=10/12 with nested trackId ===")
    for op in [10, 12]:
        raw = await send_recv(op, inner=field(1, 2, str(target).encode()), timeout=2.0)
        if raw:
            print(f"  op={op}: nested trackId-str → {len(raw)}b reply")
        else:
            print(f"  op={op}: nested trackId-str → timeout")

    # === C) op=17 with index ===
    print("\n=== C) op=17 (GET_QUEUE) with index ===")
    for idx in [0, 1, 2, 5]:
        before, before_title = await get_track()
        await send_recv(17, inner=field(1, 0, idx), timeout=2.0)
        await asyncio.sleep(0.7)
        after, after_title = await get_track()
        marker = "★" if before != after else " "
        print(f"  {marker} op=17 idx={idx}: track {before_title[:20]!r} → {after_title[:20]!r}")

    # === D) op=16 (MEDIA_COMMAND) with action+trackId combo ===
    print("\n=== D) op=16 with action+trackId combo ===")
    for label, inner in [
        ("a=2 + sub2=trackId",   field(1, 0, 2) + field(2, 2, str(target).encode())),
        ("a=20 + sub2=trackId",  field(1, 0, 20) + field(2, 2, str(target).encode())),
    ]:
        before, before_title = await get_track()
        await send_recv(16, inner=inner, timeout=2.0)
        await asyncio.sleep(0.7)
        after, after_title = await get_track()
        marker = "★" if before != after else " "
        print(f"  {marker} {label:25s}: {before_title[:20]!r} → {after_title[:20]!r}")


if __name__ == "__main__":
    asyncio.run(main())
