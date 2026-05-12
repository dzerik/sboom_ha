"""03 — Capture & generic decode.

Подключаемся WS, отправляем минимальный пробный пакет (один varint=0
для триггера ответа, либо просто пустой byte), слушаем входящий поток
N секунд. Каждое сообщение проходит через generic proto-wire декодер
(без знания .proto-схемы).

Также из payload пытаемся вынуть JSON-объекты (часто устройство кладёт
metadata в JSON прямо внутри length-delimited поля — это легко увидеть).

Запускайте этот скрипт ПОКА вы вручную играетесь с устройством через
официальное приложение/голосом — в trafficе вы увидите push-обновления
(track changes, volume, и т.п.). Это золотая жила для понимания семантики.

Использование:
    python research/03_capture.py --host <host> --port <port> --duration 60
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time

from _shared import decode, find_jsons, pretty, ws_open


async def main(args):
    print(f"[+] Connecting to wss://{args.host}:{args.port}/ …")
    ws = await ws_open(args.host, args.port)
    print(f"    handshake OK\n")

    if args.send_probe:
        # Пустой varint=0 — почти любой proto-парсер на той стороне это съест
        # без crash, потенциально ответив каким-то error/echo.
        await ws.send(b"\x00")
        print("    [probe] sent 1-byte varint=0")

    deadline = time.time() + args.duration
    msg_idx = 0

    seen_jsons: set[str] = set()
    seen_top_tags: dict[int, int] = {}

    print(f"─── Listening for {args.duration}s ───────────────────────────────\n")
    while time.time() < deadline:
        remain = deadline - time.time()
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=remain)
        except asyncio.TimeoutError:
            break
        except Exception as e:  # noqa: BLE001
            print(f"    [recv error: {e!r}]")
            break

        msg_idx += 1
        if isinstance(msg, str):
            msg = msg.encode()
        print(f"━━━ #{msg_idx} ({len(msg)} bytes) ━━━")
        print("hex:", msg[:120].hex(), "…" if len(msg) > 120 else "")
        tree = decode(msg)
        print("tlv:")
        print(pretty(tree, indent=1))
        for tag in tree:
            seen_top_tags[tag] = seen_top_tags.get(tag, 0) + 1

        # JSON-payload extraction
        for js in find_jsons(msg):
            if js in seen_jsons:
                continue
            seen_jsons.add(js)
            try:
                obj = json.loads(js)
                keys = list(obj.keys())[:8]
                print(f"json: keys={keys} …")
            except json.JSONDecodeError:
                pass
        print()

    await ws.close()
    print(f"\n─── Summary ────────────────────────────")
    print(f"messages: {msg_idx}")
    print(f"top-level tags seen: {seen_top_tags}")
    print(f"distinct JSON payloads: {len(seen_jsons)}")
    if args.dump_jsons:
        for js in seen_jsons:
            print(f"\nJSON: {js[:200]}…")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--host", required=True)
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--duration", type=float, default=60.0)
    p.add_argument("--send-probe", action="store_true",
                   help="Send a 1-byte probe to nudge the server")
    p.add_argument("--dump-jsons", action="store_true")
    asyncio.run(main(p.parse_args()))
