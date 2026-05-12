"""exp_02 — op-sweep БЕЗ токена. Найти ops которые отвечают разным reply'ем.

КОНТЕКСТ: после exp_01 знаем минимальный envelope. Шлём для op=1..24
с body=field(op, 2, field(1, 2, b"")). Большинство возвращает status=4
(auth-required/unknown). Те что отвечают по-другому — кандидаты в pair-init.

РЕЗУЛЬТАТ:
- ops [1..3, 7..24]: status=4 standard error (44b)
- op=4: 84b, body содержит {4:{1:1, 2:'<session-uuid>'}} ← pair-init!
- op=5: 44b, body={5:...} ← pair-cancel (озвучивает «отключение отменено»)
- op=6: 44b, body={6:...} ← pair-confirm-ack

Симметричное wrapping: response.body = field(op_echo, 2, {1:status, 2:data}).
"""
from __future__ import annotations

import asyncio
import json

from _helpers import HOST, PORT, send_recv
from _shared import decode, field, find_jsons


async def main():
    print("Op-sweep WITHOUT auth — looking for non-status=4 reply…\n")
    distinct = {}
    for op in range(1, 25):
        # body = nested(op=N, content=empty)
        inner = field(1, 2, b"")
        reply = await send_recv(op, inner=inner, token=None, timeout=2.5)
        if not reply:
            print(f"    op={op:2d}: timeout")
            continue
        d = decode(reply)
        sig = tuple(sorted((k, type(v).__name__) for k, v in d.items() if k != 2))
        marker = "★" if sig not in distinct else " "
        distinct.setdefault(sig, []).append(op)
        body5 = d.get(5, "")
        body_summary = (f"<dict {list(body5.keys())}>" if isinstance(body5, dict)
                        else (f"str[{len(body5)}]" if isinstance(body5, str) and body5
                              else repr(body5)))
        print(f"  {marker} op={op:2d}: {len(reply):3d}b status={d.get(3,'?'):>3} body5={body_summary}")
    print()
    for sig, ops in distinct.items():
        print(f"  signature_class={sig} → ops {ops}")


if __name__ == "__main__":
    asyncio.run(main())
