"""exp_10 — найти op активирующий push-subscribe stream.

КОНТЕКСТ: нашли через exp_05/06 что устройство имеет subscribe-механизм
(metadata содержит position.tsMs — значит обновляется). Ищем какой op
после запроса начинает push'ить unsolicited update'ы.

МЕТОД: для каждой op (1..24) — открыть свежее WS, отправить op, слушать
5 секунд. ПРОВОЦИРУЕМ track-change (action=2 next через ВТОРОЕ соединение)
после первого reply. Если приходит >1 messages — кандидат на subscribe.

РЕЗУЛЬТАТ:
  ★ op=4: 2 msgs (но это pair-flow specific)
  ★ op=10: **3 msgs** (sync 879b + 2 push'a 1838b/1840b после next-track)
       ⇒ op=10 = GET_METADATA + SUBSCRIBE_TO_MEDIA_EVENTS
  остальные ops: 1 msg только sync reply
"""
from __future__ import annotations

import asyncio
import time

from _helpers import send_recv, ws_open, make_envelope
from _shared import field


async def fire_action(action_code: int):
    """Через отдельное соединение посылаем action — provoke event."""
    pkt = make_envelope(16, field(1, 0, action_code))
    try:
        ws = await ws_open(make_envelope.__globals__["HOST"],
                           make_envelope.__globals__["PORT"])
        await ws.send(pkt)
        try: await asyncio.wait_for(ws.recv(), timeout=1.0)
        except Exception: pass
        await ws.close()
    except Exception:
        pass


async def probe_op_for_push(op: int) -> list[bytes]:
    """Открыть WS, послать op, listen 5s, provoke track-change в середине."""
    from _helpers import HOST, PORT
    try:
        ws = await ws_open(HOST, PORT)
    except Exception:
        return []
    try:
        await ws.send(make_envelope(op))
        msgs = []
        deadline = time.time() + 5.0
        provoked = False
        while time.time() < deadline:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=deadline - time.time())
            except (asyncio.TimeoutError, Exception):
                break
            if isinstance(msg, str): msg = msg.encode()
            msgs.append(msg)
            if len(msgs) == 1 and not provoked:
                provoked = True
                await fire_action(2)    # next-track to provoke event
        return msgs
    finally:
        try: await ws.close()
        except Exception: pass


async def main():
    print("Push-subscribe trigger detection…")
    print("Method: send op, listen 5s, fire track-change after 1st reply.\n")
    for op in range(1, 25):
        msgs = await probe_op_for_push(op)
        n = len(msgs)
        marker = "★" if n > 1 else " "
        sizes = [len(m) for m in msgs]
        print(f"  {marker} op={op:2d}: {n} msg(s), sizes={sizes}")


if __name__ == "__main__":
    asyncio.run(main())
