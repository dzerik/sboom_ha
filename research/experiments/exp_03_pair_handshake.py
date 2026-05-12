"""exp_03 — полный pair-handshake (получить токен).

КОНТЕКСТ: exp_02 нашёл что op=4 = pair-init. Шлём pair-init с пустым inner,
держим WS открытым. Оператор слышит «нажмите плюс», нажимает кнопку.
Колонка пушит второе сообщение со status=1 + UUID-токеном.

ВАЖНО: токен сохранить и положить в _config.py:TOKEN.
Каждый pair-init сбрасывает предыдущий токен.

РЕЗУЛЬТАТ при успешном pair:
  msg #1 (sync): {1:2, 2:rid, 5: {4: {1:1, 2:'<session-id>'}}} — status=1=awaiting
  msg #2 (push после кнопки): {1:2, 2:'<новый-rid>', 5: {6: {1:1, 2:'<TOKEN>'}}}
                                                       ↑                ↑
                                                       op=6 confirm    PIN-токен (UUID)
"""
from __future__ import annotations

import asyncio
import re
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _shared import decode, field, ws_open

from _config import HOST, PORT


def _scan_for_token(tree):
    """Найти длинную printable-строку (UUID-format) в дереве."""
    if isinstance(tree, str):
        if len(tree) >= 32 and re.fullmatch(r"[A-Za-z0-9+/=._\-]+", tree):
            return tree
    if isinstance(tree, dict):
        for v in tree.values():
            t = _scan_for_token(v)
            if t:
                return t
    return None


async def main():
    rid = str(uuid.uuid4()).encode()
    cid = str(uuid.uuid4()).encode()
    body = field(4, 2, field(1, 2, b""))    # op=4 pair-init с empty inner
    pkt = b"".join([
        field(1, 0, 2), field(2, 2, rid), field(3, 2, b""),
        field(5, 2, body), field(6, 0, 1), field(7, 2, b"research"),
        field(10, 0, 1), field(11, 2, cid),
    ])

    print(f"[+] Connecting to wss://{HOST}:{PORT}/")
    ws = await ws_open(HOST, PORT)
    print(f"[+] Sending pair-init op=4 ({len(pkt)} bytes)…")
    await ws.send(pkt)

    msgs = 0
    deadline = time.time() + 100
    token = None
    while time.time() < deadline:
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=deadline - time.time())
        except asyncio.TimeoutError:
            break
        except Exception as e:
            print(f"[!] recv error: {e!r}")
            break
        msgs += 1
        if isinstance(msg, str): msg = msg.encode()
        d = decode(msg)
        print(f"\n━━━ msg #{msgs} ({len(msg)}b) ━━━")
        print(f"  decoded: {d}")
        cand = _scan_for_token(d)
        if cand:
            print(f"  ★ candidate token: {cand}")
            if msgs > 1:    # second message = post-button confirm
                token = cand
                break
        if msgs == 1:
            print("\n[!] PRESS THE PAIR BUTTON ON THE DEVICE NOW.")
            print("[!] Listening for up to 90 more seconds…\n")

    await ws.close()
    if token:
        print(f"\n★★★ TOKEN: {token}")
        print(f"\nPut this in research/experiments/_config.py:TOKEN")
    else:
        print(f"\n[!] no token captured. ({msgs} messages received)")


if __name__ == "__main__":
    asyncio.run(main())
