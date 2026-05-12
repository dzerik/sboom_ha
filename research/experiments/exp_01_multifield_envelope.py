"""exp_01 — поиск минимального envelope (H1-H7 hypothesis testing).

КОНТЕКСТ: устройство принимает WS-handshake, но молчит на любые «частичные»
single-field probes (см. 04_fuzz_envelope.py выводы). Single-field эвристики
не работают — нужны multi-field combos.

ГИПОТЕЗА: типичный RPC envelope = {type: REQUEST, request_id, body}.
Это публичная конвенция (gRPC, Google-RPC), не вендор-знание.

РЕЗУЛЬТАТ:
- H1 (только type+rid): timeout — мало полей
- H2 (+ empty body=field 5): ★ reply 44b со status=5
- H6 (type=1 вместо 2): timeout — type-varint должен быть = 2
- H7 (full envelope с полями 1,2,3,5,6,7,10,11): ★ reply 44b со status=4

Финальный inferred envelope:
  field(1,0,2) = type=REQUEST
  field(2,2,uuid) = request_id (echo'нётся в reply field 2 — это и подтверждает rid_tag=2)
  field(5,2,body) = request_data wrapper (без него silent)
  + опц: 3=token, 6=token_type, 7=client_name, 10=is_request, 11=client_id

Reply structure: {1:2, 2:'<наш-rid>', 3:status, 5:body}
  status=5 = missing required fields (мало обёртки)
  status=4 = error (auth required / unknown op)
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _shared import decode, field, ws_open

from _config import HOST, PORT


async def try_send(parts: list[bytes], label: str) -> bytes | None:
    try:
        ws = await ws_open(HOST, PORT)
    except Exception:
        return None
    try:
        await ws.send(b"".join(parts))
        msg = await asyncio.wait_for(ws.recv(), timeout=4.0)
        return msg if isinstance(msg, bytes) else msg.encode()
    except (asyncio.TimeoutError, Exception):
        return None
    finally:
        try: await ws.close()
        except Exception: pass


async def main():
    rid = str(uuid.uuid4()).encode()
    cid = str(uuid.uuid4()).encode()

    # 7 hypotheses от минимального до полного envelope
    cases = [
        ("H1 type+rid",          [field(1,0,2), field(2,2,rid)]),
        ("H2 +body5=empty",       [field(1,0,2), field(2,2,rid), field(5,2,b"")]),
        ("H3 +cid11",             [field(1,0,2), field(2,2,rid), field(5,2,b""), field(11,2,cid)]),
        ("H4 body5=nested(op4)",  [field(1,0,2), field(2,2,rid), field(5,2,field(4,2,field(1,2,b""))), field(11,2,cid)]),
        ("H5 body5=nested(op1)",  [field(1,0,2), field(2,2,rid), field(5,2,field(1,2,field(1,2,b""))), field(11,2,cid)]),
        ("H6 type=1 + body5+cid", [field(1,0,1), field(2,2,rid), field(5,2,b""), field(11,2,cid)]),
        ("H7 full envelope",      [field(1,0,2), field(2,2,rid), field(3,2,b""), field(5,2,b""),
                                    field(6,0,1), field(7,2,b"research"), field(10,0,1), field(11,2,cid)]),
    ]

    for label, parts in cases:
        pkt = b"".join(parts)
        reply = await try_send(parts, label)
        if reply:
            decoded = decode(reply)
            print(f"  ★★★ {label} ({len(pkt)}b → {len(reply)}b)")
            print(f"      decoded: {decoded}")
        else:
            print(f"  [- ] {label} ({len(pkt)}b → timeout)")


if __name__ == "__main__":
    asyncio.run(main())
