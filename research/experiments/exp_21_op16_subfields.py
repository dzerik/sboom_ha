"""exp_21 — op=16 (MEDIA_COMMAND) с разными subfield-tags 2..7.

КОНТЕКСТ: мы fuzz'или op=16 только с subfield(1, 0, action). Остальные
subfields могут управлять LED, alarms, multi-room, etc. через тот же op
с разной inner-structure.

МЕТОД: для каждого subfield-tag (2..7) и kind (varint/string) — отправить
op=16 с inner=field(N, KIND, V), сравнить full state до/после (поля
volume, led_display, alarm, multiroom, current_app).

Также combo-инпут: subf1(action=N) + subf2(varint=M).
"""
from __future__ import annotations

import asyncio
import json

from _helpers import find_jsons, send_recv, flatten
from _shared import field


async def get_summary():
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
    return {
        "volume_pct": (s.get("volume") or {}).get("percent"),
        "muted": (s.get("volume") or {}).get("muted"),
        "led_brightness": ((s.get("capabilities_state") or {}).get("led_display") or {}).get("brightness"),
        "led_on": ((s.get("capabilities_state") or {}).get("led_display") or {}).get("turned_on"),
        "alarm_playing": (s.get("alarm") or {}).get("playing"),
        "current_app": (s.get("current_app") or {}).get("systemName"),
    }


async def diff_after(label: str, inner: bytes):
    base = await get_summary()
    await send_recv(16, inner=inner, timeout=2.0)
    await asyncio.sleep(0.6)
    cur = await get_summary()
    diff = {k: (base[k], cur[k]) for k in cur if base[k] != cur[k]}
    if diff:
        print(f"  ★ {label:30s}: {diff}")
    return diff


async def main():
    print("=== op=16 with subfield 2..7 (varint and string) ===\n")
    for subf in range(2, 8):
        for v in [0, 1, 5, 50]:
            await diff_after(f"subf{subf} v={v}", field(subf, 0, v))
        await diff_after(f"subf{subf} str='x'", field(subf, 2, b"x"))
        print()

    print("\n=== op=16 with combos: subf1=action + subf2=N ===")
    # Гипотеза: subfield 2 даёт extended параметр для action
    for action in [4, 5, 16, 20]:
        for v2 in [0, 1, 50]:
            inner = field(1, 0, action) + field(2, 0, v2)
            await diff_after(f"a={action} sub2={v2}", inner)


if __name__ == "__main__":
    asyncio.run(main())
