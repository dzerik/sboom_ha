"""exp_19 — поиск op для LED-control.

КОНТЕКСТ: GET_STATE содержит `capabilities_state.led_display`:
  { brightness: 100, turned_on: true }
Это явно managed → должны быть ops для set-brightness и turn-on/off.

МЕТОД:
1. Sweep ops 1..40 с inner=field(1, 0, 50) — diff led_display.brightness
2. Sweep ops 1..40 с inner=field(1, 0, 0) и field(1, 0, 1) — diff led_display.turned_on
3. Также пробуем nested-payload `{brightness: 50}` через JSON-string

EXPECTED: какой-то op (вероятно в диапазоне 18..40 которые мы пометили как
non-existent) должен реагировать.
"""
from __future__ import annotations

import asyncio
import json

from _helpers import find_jsons, send_recv
from _shared import field


async def get_led_state():
    raw = await send_recv(12, timeout=4.0)
    if isinstance(raw, str): raw = raw.encode()
    for j in find_jsons(raw):
        try:
            o = json.loads(j)
            if isinstance(o, dict) and "capabilities_state" in o:
                cs = o.get("capabilities_state") or {}
                led = cs.get("led_display") or {}
                return led.get("brightness"), led.get("turned_on")
        except json.JSONDecodeError:
            pass
    return None, None


async def main():
    base_b, base_on = await get_led_state()
    print(f"baseline: brightness={base_b} turned_on={base_on}\n")

    # === A) Sweep ops 1..40 with inner=field(1, 0, 50) — looking for brightness change ===
    print("=== A) ops 1..40 with v=50 (looking for brightness=50) ===")
    for op in range(1, 41):
        await send_recv(op, inner=field(1, 0, 50), timeout=1.5)
        await asyncio.sleep(0.4)
        b, on = await get_led_state()
        if b != base_b or on != base_on:
            print(f"  ★ op={op:2d}: brightness {base_b}→{b}, turned_on {base_on}→{on}")
            base_b, base_on = b, on

    # Reset to known state
    print("\n[+] reset baseline…")
    base_b, base_on = await get_led_state()
    print(f"baseline now: brightness={base_b} turned_on={base_on}\n")

    # === B) Sweep ops 1..40 with inner=field(1, 0, 0) — looking for turned_on=False ===
    print("=== B) ops 1..40 with v=0 (looking for turned_on flip) ===")
    for op in range(1, 41):
        await send_recv(op, inner=field(1, 0, 0), timeout=1.5)
        await asyncio.sleep(0.4)
        b, on = await get_led_state()
        if b != base_b or on != base_on:
            print(f"  ★ op={op:2d}: brightness {base_b}→{b}, turned_on {base_on}→{on}")
            base_b, base_on = b, on

    # === C) Try nested with brightness key ===
    print("\n=== C) ops 1..40 with nested {brightness: 50} ===")
    base_b, base_on = await get_led_state()
    for op in range(1, 41):
        # Try nested = field(1, 2, JSON_string) с brightness
        inner = field(1, 2, b'{"brightness":50,"turned_on":false}')
        await send_recv(op, inner=inner, timeout=1.5)
        await asyncio.sleep(0.4)
        b, on = await get_led_state()
        if b != base_b or on != base_on:
            print(f"  ★ op={op:2d}: brightness {base_b}→{b}, turned_on {base_on}→{on}")
            base_b, base_on = b, on


if __name__ == "__main__":
    asyncio.run(main())
