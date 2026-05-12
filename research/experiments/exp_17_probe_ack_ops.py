"""exp_17 — детальный probe ops 1-9 (status=1 ack без видимого payload).

КОНТЕКСТ: в exp_04 ops [1, 2, 3, 7, 8, 9, 24, ...] возвращали короткий
ack с status=1 без payload. Это значит они **что-то делают**, но мы не
видим что.

Также КОРРЕКТИРОВКА: первый probe op=18 показал track change'и, но
повторный clean тест в exp_16 показал что это была natural track
progression — op=18 НЕ меняет трек по trackId/index.

ГИПОТЕЗА: ops 1-9 могут быть control-команды без видимых side-effects:
- DEVICE_SLEEP / WAKE / RESET
- PLAY_RANDOM / PLAY_NEXT_PLAYLIST
- HOMESCREEN / FOCUS_MAIN
- VOICE_TRIGGER / VOICE_RESET

МЕТОД: для каждой ops [1, 2, 3, 7, 8, 9] — отправить с разными inner
(empty, varint, string), сравнить FULL state до/после (background_apps,
current_app, capabilities_state, deviceSleep, и т.п.).
"""
from __future__ import annotations

import asyncio
import json

from _helpers import find_jsons, send_recv, flatten
from _shared import decode, field


async def full_state():
    """Полный flatten GET_STATE."""
    raw = await send_recv(12, timeout=4.0)
    if isinstance(raw, str): raw = raw.encode()
    s = {}
    for j in find_jsons(raw):
        try:
            o = json.loads(j)
            if isinstance(o, dict) and "volume" in o:
                s = o
                break
        except json.JSONDecodeError:
            pass
    return flatten(s)


async def diff_op(op: int, inner: bytes, label: str):
    base = await full_state()
    raw = await send_recv(op, inner=inner, timeout=2.0)
    await asyncio.sleep(0.7)
    after = await full_state()

    # Filter out always-changing time fields
    def stable(d):
        return {k: v for k, v in d.items()
                if not any(s in k for s in ("time", "timestamp", "tsMs", "_unixtime"))}

    fb, fa = stable(base), stable(after)
    keys = set(fb) | set(fa)
    diff = {k: (fb.get(k), fa.get(k)) for k in sorted(keys) if fb.get(k) != fa.get(k)}

    if raw:
        d = decode(raw)
        body5 = d.get(5, "")
        kind = (f"<dict {list(body5.keys())}>" if isinstance(body5, dict)
                else (f"str[{len(body5)}]" if isinstance(body5, str) and body5
                      else repr(body5)))
        reply_summary = f"{len(raw):3d}b body5={kind}"
    else:
        reply_summary = "timeout"

    if diff:
        print(f"  ★ op={op} {label:14s}: {reply_summary}")
        for k, (a, b) in list(diff.items())[:6]:
            print(f"      {k}: {a!r} → {b!r}")
        if len(diff) > 6:
            print(f"      ... and {len(diff)-6} more")
    else:
        print(f"    op={op} {label:14s}: {reply_summary} — no observable diff")


async def main():
    print("=== Detailed probe ops 1-9 with FULL state diff ===\n")
    for op in [1, 2, 3, 7, 8, 9]:
        for label, inner in [
            ("empty",     field(1, 2, b"")),
            ("v1=0",      field(1, 0, 0)),
            ("v1=1",      field(1, 0, 1)),
            ("v1=2",      field(1, 0, 2)),
            ("str='x'",   field(1, 2, b"x")),
        ]:
            await diff_op(op, inner, label)
        print()


if __name__ == "__main__":
    asyncio.run(main())
