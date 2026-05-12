"""exp_05 — посмотреть структуру GET_STATE (op=12) и GET_METADATA (op=10).

КОНТЕКСТ: exp_04 нашёл что op=12 даёт ~3600b reply. Decode'им JSON и
смотрим какие top-level keys там есть.

РЕЗУЛЬТАТ:
  GET_STATE keys: alarm, assistant, background_apps, capabilities_state,
    current_app, deviceGroups, deviceSelector, deviceSleep, device_segments,
    homeSecurity, locale, location, morning_show, multiroom, network,
    proactivityNotification, reminders, sbercast, subscrDeviceInfo, time,
    timesync, user_settings, volume

  Volume в GET_STATE: {muted: false, percent: 0}

  GET_METADATA: artists, title, duration, playing, position, playlistTitle,
    provider, releases, repeatType, shuffle, like, trackId, ...

  Многие подсистемы (alarm, multiroom, homeSecurity, ...) явно имеют свои
  command-ops которые мы не нашли в обычном sweep'e.
"""
from __future__ import annotations

import asyncio
import json

from _helpers import find_jsons, send_recv


async def main():
    print("=== GET_STATE (op=12) ===")
    raw = await send_recv(12, timeout=5.0)
    if isinstance(raw, str): raw = raw.encode()
    print(f"size: {len(raw)}b")
    for i, j in enumerate(find_jsons(raw)):
        try:
            obj = json.loads(j)
            if not isinstance(obj, dict):
                continue
            print(f"\n--- JSON #{i} ({len(j)} chars), top-level keys ---")
            for k in sorted(obj.keys()):
                print(f"    {k}")
            if "volume" in obj:
                print(f"  volume: {obj['volume']}")
            if "capabilities_state" in obj:
                cs = obj["capabilities_state"]
                if isinstance(cs, dict):
                    print(f"  capabilities_state: {list(cs.keys())}")
        except Exception as e:
            print(f"parse err: {e!r}")

    print("\n\n=== GET_METADATA (op=10) ===")
    raw = await send_recv(10, timeout=5.0)
    if isinstance(raw, str): raw = raw.encode()
    print(f"size: {len(raw)}b")
    for j in find_jsons(raw):
        try:
            obj = json.loads(j)
            if isinstance(obj, dict) and "trackId" in obj:
                print(json.dumps(obj, ensure_ascii=False, indent=2))
                break
        except Exception:
            pass

    print("\n\n=== GET_QUEUE (op=17) ===")
    raw = await send_recv(17, timeout=5.0)
    if isinstance(raw, str): raw = raw.encode()
    print(f"size: {len(raw)}b")
    for j in find_jsons(raw):
        try:
            obj = json.loads(j)
            print(f"  → {obj}")
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
