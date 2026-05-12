"""exp_11 — подтвердить op=10 = subscribe-stream + узнать что покрывается.

КОНТЕКСТ: exp_10 нашёл op=10 как subscribe trigger. Подтверждаем:
один раз шлём op=10, не закрываем сессию, провоцируем разные events,
считаем pushes.

РЕЗУЛЬТАТ:
  next (action=2)  → 3 push'а
  pause (action=5) → 1 push
  play (action=4)  → 1 push
  mute (action=0)  → 0 push (зависит от state)
  unmute (action=1) → 1 push

  Push messages: top_tags=[1, 5] (БЕЗ field 2 = rid) ⇒ unsolicited
  Push size 1740b+ vs sync 860b ⇒ расширенная метадата на events
"""
from __future__ import annotations

import asyncio
import json

from _helpers import HOST, PORT, find_jsons, make_envelope
from _shared import decode, field, ws_open


async def fire(action: int):
    try:
        ws = await ws_open(HOST, PORT)
        await ws.send(make_envelope(16, field(1, 0, action)))
        try: await asyncio.wait_for(ws.recv(), timeout=1.0)
        except Exception: pass
        await ws.close()
    except Exception: pass


async def main():
    print("[+] Subscribing via op=10…")
    ws = await ws_open(HOST, PORT)
    await ws.send(make_envelope(10))

    pushes = []

    async def listener():
        while True:
            try:
                msg = await ws.recv()
            except Exception as e:
                print(f"  [listener exit: {e!r}]")
                return
            if isinstance(msg, str): msg = msg.encode()
            pushes.append(msg)
            d = decode(msg)
            top = list(d.keys()) if isinstance(d, dict) else []
            jkeys = []
            for j in find_jsons(msg):
                try:
                    o = json.loads(j)
                    if isinstance(o, dict):
                        jkeys = sorted(o.keys())[:5]
                        break
                except Exception:
                    pass
            print(f"  push #{len(pushes)}: {len(msg)}b, top_tags={top[:5]}, json_keys={jkeys}")

    listener_task = asyncio.create_task(listener())

    actions_to_test = [
        (2, "NEXT"),
        (5, "PAUSE"),
        (4, "PLAY"),
        (0, "MUTE"),
        (1, "UNMUTE"),
    ]
    await asyncio.sleep(0.5)
    for action, name in actions_to_test:
        print(f"\n[fire] {name} action={action}")
        await fire(action)
        await asyncio.sleep(2.5)

    listener_task.cancel()
    await ws.close()
    print(f"\n=== Total pushes during 12s test: {len(pushes)} ===")


if __name__ == "__main__":
    asyncio.run(main())
