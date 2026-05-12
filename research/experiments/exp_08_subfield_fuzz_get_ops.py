"""exp_08 — sub-field fuzz для GET_METADATA (op=10) и GET_STATE (op=12).

КОНТЕКСТ: проверяем — параметризуются ли state-ops (фильтры/индексы)?

РЕЗУЛЬТАТ:
| input | op=10 | op=12 |
|-------|-------|-------|
| empty                | 882b  | 3603b |
| varint(0/1/10)       | 882b same | ~3603b same |
| string на subfield   | timeout | timeout |
| bytes-blob           | timeout | timeout |
| nested(empty)        | 882b same | 3603b same |

ВЫВОД: ни op=10, ни op=12 не реагируют на простые varint/string/bytes
в subfield 1/2/3. String и bytes → сервер получает но не отвечает
(вероятно ожидает specific schema-payload).
"""
from __future__ import annotations

import asyncio
import json

from _helpers import find_jsons, send_recv
from _shared import field


def summary(raw: bytes | None) -> tuple[int, list]:
    if not raw:
        return 0, []
    keys = []
    for j in find_jsons(raw):
        try:
            o = json.loads(j)
            if isinstance(o, dict):
                keys.append(tuple(sorted(o.keys()))[:6])
        except json.JSONDecodeError:
            continue
    return len(raw), keys


async def main():
    cases = [
        ("empty",         field(1, 2, b"")),
        ("v=0",           field(1, 0, 0)),
        ("v=1",           field(1, 0, 1)),
        ("v=10",          field(1, 0, 10)),
        ("str=uuid",      field(1, 2, b"f47ac10b-58cc-4372-a567-0e02b2c3d479")),
        ("str='all'",     field(1, 2, b"all")),
        ("subf2=v1",      field(2, 0, 1)),
        ("subf2=str",     field(2, 2, b"x")),
        ("subf3=v1",      field(3, 0, 1)),
        ("nested",        field(1, 2, field(1, 0, 1))),
        ("subf1=bytes_4", field(1, 2, b"\x00\x01\x02\x03")),
    ]
    for op_name, op in [("GET_METADATA op=10", 10), ("GET_STATE op=12", 12)]:
        print(f"\n=== {op_name} ===")
        for label, inner in cases:
            raw = await send_recv(op, inner=inner, timeout=2.5)
            if raw is None:
                print(f"  {label:18s}: timeout/closed")
                continue
            sz, keys = summary(raw)
            print(f"  {label:18s}: {sz:5d}b  json_keys[0]={keys[0] if keys else '∅'}")


if __name__ == "__main__":
    asyncio.run(main())
