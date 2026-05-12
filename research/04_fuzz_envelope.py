"""04 — Fuzz envelope structure.

Цель: понять обёртку запроса (какие поля устройство ожидает на верхнем уровне).
Без этого знания все наши подзапросы падают/игнорируются.

Метод:
1) Пробуем отправить только поле `field(N, kind, value)` для tag=N=1..16,
   kind=0 (varint) и kind=2 (bytes).
2) Смотрим:
   - закрылось ли соединение мгновенно → tag запрещён/нарушает контракт
   - пришло ли что-то осмысленное в ответ → tag принят
   - что в ответе (tag-номера + типы) — намёк на структуру error/state

Также пробуем простые комбинации: 2 поля, 3 поля. Из ответов вырисовывается
минимальная обёртка — тег-кандидат для "client_id", "request_id", "auth-token",
"request_data".

Использование:
    python research/04_fuzz_envelope.py --host <host> --port <port> --out envelope.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from typing import Any

from _shared import decode, field, ws_open


async def _send_recv(ws, payload: bytes, timeout: float = 1.5) -> bytes | str:
    """Отправляем payload, ждём один ответ или timeout."""
    try:
        await ws.send(payload)
    except Exception as e:  # noqa: BLE001
        return f"send-error: {e!r}"
    try:
        msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
        return msg if isinstance(msg, bytes) else msg.encode()
    except asyncio.TimeoutError:
        return "timeout"
    except Exception as e:  # noqa: BLE001
        return f"recv-error: {e!r}"


async def _probe_single_field(host: str, port: int, tag: int, kind: int) -> dict[str, Any]:
    """Открываем свежее WS-соединение, шлём один TLV-field, фиксируем reply."""
    try:
        ws = await ws_open(host, port)
    except Exception as e:  # noqa: BLE001
        return {"error": f"connect: {e!r}"}

    try:
        if kind == 0:
            payload = field(tag, 0, 1)  # varint=1 — нейтральный ненулевой
        else:
            payload = field(tag, 2, b"x")  # 1-байтовая строка
        result = await _send_recv(ws, payload)
    finally:
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass

    if isinstance(result, str):
        return {"reply": result}
    return {
        "reply_size": len(result),
        "reply_hex": result[:64].hex(),
        "reply_tlv": decode(result),
    }


async def _probe_combo(host: str, port: int, fields_def: list[tuple[int, int, Any]]) -> dict[str, Any]:
    """Send ну несколько TLV полей сразу — может ли быть валидный envelope."""
    try:
        ws = await ws_open(host, port)
    except Exception as e:  # noqa: BLE001
        return {"error": f"connect: {e!r}"}

    payload = b"".join(field(t, k, v) for t, k, v in fields_def)
    try:
        result = await _send_recv(ws, payload, timeout=2.0)
    finally:
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
    if isinstance(result, str):
        return {"reply": result}
    return {
        "reply_size": len(result),
        "reply_hex": result[:64].hex(),
        "reply_tlv": decode(result),
    }


async def main(args):
    print(f"[+] Fuzz envelope on {args.host}:{args.port}\n")
    results: dict[str, Any] = {"single": {}, "combos": {}}

    # ─── A) единичные tags ───
    print("─── A) Single-field probes ────────────────────")
    for tag in range(1, args.max_tag + 1):
        for kind in (0, 2):
            print(f"    tag={tag:2d} kind={kind} … ", end="", flush=True)
            r = await _probe_single_field(args.host, args.port, tag, kind)
            results["single"][f"tag{tag}_kind{kind}"] = r
            if "reply_size" in r:
                tlv_keys = list(r["reply_tlv"].keys()) if isinstance(r["reply_tlv"], dict) else []
                print(f"reply {r['reply_size']}b, top-tags={tlv_keys}")
            else:
                print(r.get("reply") or r.get("error") or "?")

    # ─── B) Комбинации с req_id (самый частый паттерн в RPC) ───
    print("\n─── B) Combos with req-id-like string field ───")
    rid = str(uuid.uuid4()).encode()
    for body_tag in range(1, args.max_tag + 1):
        # шаблон: field(2, 2, req_id) + field(N, 2, empty)
        combo = [(2, 2, rid), (body_tag, 2, b"")]
        r = await _probe_combo(args.host, args.port, combo)
        results["combos"][f"rid+tag{body_tag}_empty"] = r
        if "reply_size" in r:
            tlv = r["reply_tlv"]
            top = list(tlv.keys()) if isinstance(tlv, dict) else []
            print(f"    body_tag={body_tag:2d} → {r['reply_size']}b, top-tags={top}")

    if args.out:
        # Сериализуем — bytes в hex
        def _serialize(v):
            if isinstance(v, dict):
                return {str(k): _serialize(x) for k, x in v.items()}
            if isinstance(v, bytes):
                return v.hex()
            return v
        with open(args.out, "w") as f:
            json.dump(_serialize(results), f, indent=2, ensure_ascii=False)
        print(f"\n[+] saved {args.out}")

    # ─── C) Эвристика выводов ───
    print("\n─── Heuristic conclusions ──────────────────────")
    print("    Tags которые чаще всего отвечали осмысленно — кандидаты")
    print("    в роли 'request-id', 'request-data', 'client-id'.")
    print("    Body-tags с осмысленным reply — кандидаты в роли 'operation type'.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--host", required=True)
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--max-tag", type=int, default=16)
    p.add_argument("--out", default="envelope.json")
    asyncio.run(main(p.parse_args()))
