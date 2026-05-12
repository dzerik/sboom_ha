"""exp_12 — dark actions 8/14/15: что они меняют в `state.background_apps`.

КОНТЕКСТ: exp_06/07 показали что action 8/14/15 не дают diff в media-state.
Гипотеза: они влияют на ДРУГИЕ подсистемы — например, focus management.
Сравниваем full GET_STATE до/после.

РЕЗУЛЬТАТ:
  state.background_apps это **z-order стек активных apps**:
    music, morning_show, bluetooth_media_control, voice_auth, pager,
    geo_fixer_app

  action=8 → переупорядочивает apps (voice_auth поднимается)
            ⇒ возможно «trigger voice assistant»
  action=14 → track-change (как next) + apps tail rearrange
            ⇒ возможно «next-batch / новый источник queue»
  action=15 → переупорядочивает apps без media-change
            ⇒ focus toggle

ВЫВОД: `background_apps` — это не просто список, а **активные приложения**
устройства с их состояниями. Каждое app может иметь свой own player/state.
"""
from __future__ import annotations

import asyncio

from _helpers import find_jsons, first_dict, send_recv
from _shared import field


def find_state(raw: bytes | None) -> dict:
    if not raw:
        return {}
    import json
    for j in find_jsons(raw):
        try:
            o = json.loads(j)
            if isinstance(o, dict) and "volume" in o:
                return o
        except Exception:
            pass
    return {}


async def snapshot():
    m = first_dict(await send_recv(10), "trackId") or {}
    s = find_state(await send_recv(12))
    apps = s.get("background_apps", []) or []
    apps_compact = []
    for a in apps:
        info = (a or {}).get("app_info") or {}
        ps = ((a or {}).get("state") or {}).get("player") or {}
        apps_compact.append({
            "app": info.get("systemName"),
            "playing": ps.get("playing"),
            "trackId": (ps.get("info") or {}).get("trackId") or ps.get("trackId"),
            "title": (ps.get("info") or {}).get("title") or ps.get("title"),
        })
    return {
        "current_app": (s.get("current_app") or {}).get("systemName"),
        "track": m.get("title"),
        "apps": apps_compact,
    }


async def test_action(action: int):
    print(f"\n=== action={action} ===")
    base = await snapshot()
    print(f"  before: cur_app={base['current_app']!r} track={base['track']!r}")
    print(f"  apps order:")
    for a in base["apps"]:
        print(f"    - {a}")
    await send_recv(16, field(1, 0, action))
    await asyncio.sleep(0.7)
    after = await snapshot()
    if after["apps"] != base["apps"]:
        print(f"  ★ APPS REARRANGED:")
        for a in after["apps"]:
            print(f"    - {a}")
    if base["track"] != after["track"]:
        print(f"  ★ TRACK CHANGED: {base['track']!r} → {after['track']!r}")


async def main():
    for a in (8, 14, 15):
        await test_action(a)


if __name__ == "__main__":
    asyncio.run(main())
