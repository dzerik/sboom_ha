"""07 — Deep fuzz: ищем то, что не нашёл baseline auto_discover.

Берём protocol_map.json и расширяем карту:

A. **HOLE SWEEP**: повторяем op-sweep в широком диапазоне (1..64), помечаем
   все ops которые отвечают. Сравниваем с already-known — выделяем НОВЫЕ.

B. **SUBFIELD FUZZ**: для каждого known/новой op подсовываем разные subfield'ы
   в request_data. Пробуем (subfield_tag=1..3) × (kind 0=varint / 2=string)
   × (value=0,1,2,empty,'test'). Смотрим как меняется reply size/structure —
   значит ли это что op параметризуется.

C. **PUSH-SUBSCRIBE TRIGGER**: после каждой op слушаем 3 секунды что прилетает
   unsolicited. Если op активирует push-стрим — увидим больше сообщений.

D. **TTS-CANDIDATE SCAN** (interactive): для каждой op подаём string в первый
   subfield, оператор слушает — произнесла ли устройство что-то.

E. **EXTENDED ACTION FUZZ**: расширяем media-action range (16..63) — может
   быть hidden команды (stop, factory_reset, equalizer, и т.п.).

Использование:
    python research/07_deep_fuzz.py --map protocol_map.json
    python research/07_deep_fuzz.py --map protocol_map.json --skip-tts
    python research/07_deep_fuzz.py --map protocol_map.json --max-op 128
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from _shared import decode as _builtin_decode, field, find_jsons, ws_open
import _shared_bbpb as bbpb_wrap


# Глобальная type-схема (накапливается за прогон) и режим
USE_BBPB: bool = False
LEARNED_TYPEDEF: dict[str, Any] = {}


def decode(raw: bytes) -> dict[str, Any]:
    """Дроп-ин: если включён --use-bbpb, использует bbpb, иначе наш decoder.

    Накопленные types сохраняются в LEARNED_TYPEDEF (для save_typedef в конце).
    """
    if USE_BBPB and bbpb_wrap.is_available():
        global LEARNED_TYPEDEF
        decoded, learned = bbpb_wrap.decode_smart(raw, LEARNED_TYPEDEF)
        if learned:
            LEARNED_TYPEDEF = bbpb_wrap.merge_typedefs(LEARNED_TYPEDEF, learned)
        return decoded
    return _builtin_decode(raw)


# ─────────────────────── chronological event log ───────────────────────
# Каждое действие fuzz'а пишется сюда как отдельная запись.
# Формат записи (jsonl-friendly):
#   { "ts": ISO8601, "stage": "A|B|C|D|E", "phase": "send|recv|note",
#     "op": int|None, "label": str|None,
#     "request_hex": str|None, "reply_hex": str|None,
#     "tlv": dict|None, "json_keys": list|None, "notes": str|None }

EVENT_LOG: list[dict[str, Any]] = []
JSONL_HANDLE = None  # set by main()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def log_event(**fields) -> None:
    """Записать событие в основной лог + опц. в jsonl-файл (streaming)."""
    fields.setdefault("ts", _now_iso())
    EVENT_LOG.append(fields)
    if JSONL_HANDLE is not None:
        try:
            JSONL_HANDLE.write(json.dumps(fields, ensure_ascii=False, default=str) + "\n")
            JSONL_HANDLE.flush()
        except Exception:  # noqa: BLE001
            pass


def _safe_decode(raw: bytes | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        return decode(raw)
    except Exception:  # noqa: BLE001
        return None


def _first_json_dict(raw: bytes | None) -> tuple[list[str], str | None]:
    """Возвращает (json_keys, json_preview)."""
    if not raw:
        return [], None
    for j in find_jsons(raw):
        try:
            obj = json.loads(j)
            if isinstance(obj, dict):
                return list(obj.keys())[:10], j[:300]
        except Exception:  # noqa: BLE001
            pass
    return [], None


# ─────────────────────── envelope construction ───────────────────────

def make_envelope(
    body: bytes, *,
    rid_tag: int, body_tag: int, cid_tag: int | None,
    token: str | None, token_tag: int | None,
    token_type_tag: int | None, token_type_value: int = 1,
) -> bytes:
    # Полная RPC-обёртка с минимальным required-set. Без type-field, client_name,
    # is_request — серверы возвращают короткий error 44b (мы это видели в
    # exp_01 multifield + текущем 07 без полей). Tags 7, 10 hardcoded по
    # findings: они есть во всех known-good envelope'ах нашего устройства.
    TAG_TYPE = 1            # field(1, 0, 2) = type=REQUEST
    TAG_CLIENT_NAME = 7     # field(7, 2, "...") = client name
    TAG_IS_REQUEST = 10     # field(10, 0, 1) = is_request flag

    parts = [field(TAG_TYPE, 0, 2)]
    parts.append(field(rid_tag, 2, str(uuid.uuid4()).encode()))
    if cid_tag is not None:
        parts.append(field(cid_tag, 2, str(uuid.uuid4()).encode()))
    if token and token_tag is not None:
        parts.append(field(token_tag, 2, token.encode()))
    if token_type_tag is not None:
        parts.append(field(token_type_tag, 0, token_type_value))
    parts.append(field(TAG_CLIENT_NAME, 2, b"research"))
    parts.append(field(TAG_IS_REQUEST, 0, 1))
    parts.append(field(body_tag, 2, body))
    return b"".join(parts)


# ─────────────────────── A. Hole sweep ───────────────────────

async def hole_sweep(host, port, env_kwargs, max_op: int) -> dict[int, dict[str, Any]]:
    print(f"\n━━━ A. HOLE SWEEP — op_tag 1..{max_op} ━━━")
    log_event(stage="A", phase="note", notes=f"hole-sweep start, max_op={max_op}")
    results: dict[int, dict[str, Any]] = {}
    for op in range(1, max_op + 1):
        body = field(op, 2, field(1, 2, b""))
        pkt = make_envelope(body, **env_kwargs)
        reply = await _send_recv_fresh(host, port, pkt, timeout=1.5,
                                       stage="A", op=op, label="empty-body")
        if not reply:
            results[op] = {"reply": None}
            continue
        tlv = decode(reply)
        jsons = find_jsons(reply)
        results[op] = {
            "size": len(reply),
            "top_tags": list(tlv.keys()) if isinstance(tlv, dict) else [],
            "json_count": len(jsons),
            "json_keys": [list(json.loads(j).keys())[:5]
                          for j in jsons[:1] if _safe_json(j)],
        }
        if len(reply) > 4:  # tiny acks мало интересны
            print(f"  op={op:3d}: {len(reply):4d}b  jsons={len(jsons)}  "
                  f"tags={results[op]['top_tags']}")
    return results


def _safe_json(s: str) -> bool:
    try:
        json.loads(s)
        return True
    except Exception:  # noqa: BLE001
        return False


# ─────────────────────── B. Subfield fuzz ───────────────────────

async def subfield_fuzz(host, port, env_kwargs, op: int) -> dict[str, Any]:
    """Для одной op-tag — варьируем subfield, ищем зависит ли reply от input."""
    cases = [
        ("empty",        field(1, 2, b"")),
        ("v=0",          field(1, 0, 0)),
        ("v=1",          field(1, 0, 1)),
        ("v=42",         field(1, 0, 42)),
        ("s=test",       field(1, 2, b"test")),
        ("s=long",       field(1, 2, b"x" * 64)),
        ("nested-empty", field(1, 2, field(1, 2, b""))),
        ("subf2=0",      field(2, 0, 0)),
        ("subf3=str",    field(3, 2, b"hello")),
    ]
    out: dict[str, Any] = {}
    for label, body_inner in cases:
        pkt = make_envelope(field(op, 2, body_inner), **env_kwargs)
        reply = await _send_recv_fresh(host, port, pkt, timeout=1.5,
                                       stage="B", op=op, label=label)
        if not reply:
            out[label] = None
            continue
        tlv = decode(reply)
        out[label] = {
            "size": len(reply),
            "top_tags": list(tlv.keys()) if isinstance(tlv, dict) else [],
            "json_keys": _first_json_keys(reply),
        }
    return out


def _first_json_keys(raw: bytes) -> list[str]:
    for j in find_jsons(raw):
        try:
            return list(json.loads(j).keys())[:8]
        except Exception:  # noqa: BLE001
            pass
    return []


# ─────────────────────── C. Push-subscribe trigger ───────────────────────

async def push_subscribe_probe(host, port, env_kwargs, op: int, listen_seconds: float) -> int:
    """После запроса op слушаем N секунд unsolicited — если приходят push'и,
    возможно эта op активирует subscribe-стрим. Каждое push-сообщение логируется."""
    try:
        ws = await ws_open(host, port)
    except Exception as e:  # noqa: BLE001
        log_event(stage="C", phase="error", op=op, notes=f"connect: {e!r}")
        return 0
    try:
        body = field(op, 2, field(1, 2, b""))
        pkt = make_envelope(body, **env_kwargs)
        log_event(stage="C", phase="send", op=op, label="probe-then-listen",
                  request_hex=pkt.hex(), notes=f"size={len(pkt)}")
        await ws.send(pkt)
        # первый reply — это синхронный ответ, не push
        try:
            first = await asyncio.wait_for(ws.recv(), timeout=2.0)
            if isinstance(first, str):
                first = first.encode()
            log_event(stage="C", phase="recv", op=op, label="initial-reply",
                      reply_hex=first.hex(), tlv=_safe_decode(first))
        except Exception:  # noqa: BLE001
            pass

        # считаем + логируем каждое push-сообщение
        push_count = 0
        deadline = time.time() + listen_seconds
        while time.time() < deadline:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=deadline - time.time())
                if isinstance(msg, str):
                    msg = msg.encode()
                push_count += 1
                keys, _ = _first_json_dict(msg)
                log_event(stage="C", phase="push", op=op,
                          label=f"push#{push_count}", reply_hex=msg.hex(),
                          tlv=_safe_decode(msg), json_keys=keys)
            except asyncio.TimeoutError:
                break
            except Exception:  # noqa: BLE001
                break
        log_event(stage="C", phase="note", op=op,
                  notes=f"total push-msgs in {listen_seconds}s = {push_count}")
        return push_count
    finally:
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass


# ─────────────────────── D. TTS-candidate scan (interactive) ───────────────────────

async def tts_scan(host, port, env_kwargs, ops: list[int]) -> dict[int, str]:
    print("\n━━━ D. TTS SCAN — пробуем послать строку через каждый op ━━━")
    print("  После каждой op слушай устройство: произнесла ли она что-то?")
    print("  Ответ: y / n / q (quit)\n")
    log_event(stage="D", phase="note", notes=f"TTS scan over {len(ops)} ops")
    found: dict[int, str] = {}
    test_string = "hello world from research"
    for op in ops:
        body = field(op, 2, field(1, 2, test_string.encode()))
        pkt = make_envelope(body, **env_kwargs)
        reply = await _send_recv_fresh(host, port, pkt, timeout=2.0,
                                       stage="D", op=op, label=f"tts:{test_string!r}")
        sz = len(reply) if reply else 0
        print(f"  op={op:3d} → reply {sz}b. Устройство проговорило? [y/N/q]: ", end="", flush=True)
        try:
            ans = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            log_event(stage="D", phase="note", op=op, notes="operator aborted")
            break
        log_event(stage="D", phase="operator", op=op,
                  notes=f"answered={ans!r} (q=quit, y=spoke, n=silent)")
        if ans == "q":
            break
        if ans in ("y", "yes", "д"):
            found[op] = "TTS confirmed by operator"
            log_event(stage="D", phase="finding", op=op,
                      notes="TTS candidate — operator confirmed audible speech")
            print(f"  *** op={op} = TTS-кандидат ***\n")
    return found


# ─────────────────────── E. Extended action range ───────────────────────

async def extended_action_fuzz(
    host, port, env_kwargs, *,
    media_command_op: int, get_state_op: int,
    start: int, end: int, known_actions: set[int],
) -> dict[int, dict[str, Any]]:
    print(f"\n━━━ E. EXTENDED ACTION FUZZ — action {start}..{end} (excl. known) ━━━")
    print("  Запусти трек на устройстве. Buffer: 0.6с между каждой командой.\n")
    log_event(stage="E", phase="note",
              notes=f"extended action fuzz {start}..{end}, "
                    f"media_command_op={media_command_op}, get_state_op={get_state_op}")

    ws = await ws_open(host, port)
    out: dict[int, dict[str, Any]] = {}

    async def get_state(label: str) -> dict | None:
        body = field(get_state_op, 2, field(1, 2, b""))
        pkt = make_envelope(body, **env_kwargs)
        log_event(stage="E", phase="send", op=get_state_op, label=f"get_state:{label}",
                  request_hex=pkt.hex())
        await ws.send(pkt)
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
        except Exception as e:  # noqa: BLE001
            log_event(stage="E", phase="error", op=get_state_op,
                      label=f"get_state:{label}", notes=repr(e))
            return None
        if isinstance(raw, str):
            raw = raw.encode()
        keys, _ = _first_json_dict(raw)
        log_event(stage="E", phase="recv", op=get_state_op,
                  label=f"get_state:{label}", reply_hex=raw.hex(), json_keys=keys)
        for j in find_jsons(raw):
            try:
                return json.loads(j)
            except Exception:  # noqa: BLE001
                pass
        return None

    def flatten(d, prefix=""):
        out_ = {}
        for k, v in (d or {}).items():
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                out_.update(flatten(v, key))
            else:
                out_[key] = v
        return out_

    prev = await get_state("baseline")
    for action in range(start, end + 1):
        if action in known_actions:
            continue
        body = field(media_command_op, 2, field(1, 0, action))
        pkt = make_envelope(body, **env_kwargs)
        log_event(stage="E", phase="send", op=media_command_op,
                  label=f"action={action}", request_hex=pkt.hex(),
                  notes=f"action varint={action}")
        try:
            await ws.send(pkt)
            try:
                ack = await asyncio.wait_for(ws.recv(), timeout=0.6)
                if isinstance(ack, str):
                    ack = ack.encode()
                log_event(stage="E", phase="recv", op=media_command_op,
                          label=f"action={action}-ack", reply_hex=ack.hex(),
                          notes=f"ack {len(ack)}b")
            except asyncio.TimeoutError:
                log_event(stage="E", phase="timeout", op=media_command_op,
                          label=f"action={action}", notes="no ack within 0.6s")
        except Exception as e:  # noqa: BLE001
            log_event(stage="E", phase="error", op=media_command_op,
                      label=f"action={action}", notes=repr(e))
        await asyncio.sleep(0.4)
        cur = await get_state(f"after-action-{action}")
        fb, fa = flatten(prev), flatten(cur)
        diff = [f"{k}: {fb.get(k)!r}→{fa.get(k)!r}"
                for k in sorted(set(fb) | set(fa)) if fb.get(k) != fa.get(k)]
        out[action] = {"diff": diff}
        if diff:
            log_event(stage="E", phase="finding", op=media_command_op,
                      label=f"action={action}",
                      notes="state changed: " + "; ".join(diff))
        marker = "★" if diff else " "
        print(f"  action={action:3d}{marker} {diff if diff else '(no change)'}")
        prev = cur

    await ws.close()
    return out


# ─────────────────────── helpers ───────────────────────

async def _send_recv_fresh(host, port, payload, timeout=2.0,
                           *, stage: str = "?", op: int | None = None,
                           label: str | None = None) -> bytes | None:
    """Открыть свежее WS, отправить payload, дождаться одного reply. Логирует."""
    log_event(stage=stage, phase="send", op=op, label=label,
              request_hex=payload.hex(), notes=f"size={len(payload)}")
    try:
        ws = await ws_open(host, port)
    except Exception as e:  # noqa: BLE001
        log_event(stage=stage, phase="error", op=op, label=label,
                  notes=f"connect failed: {e!r}")
        return None
    try:
        await ws.send(payload)
        msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
        raw = msg if isinstance(msg, bytes) else msg.encode()
        keys, _ = _first_json_dict(raw)
        log_event(stage=stage, phase="recv", op=op, label=label,
                  reply_hex=raw.hex(), tlv=_safe_decode(raw), json_keys=keys,
                  notes=f"size={len(raw)}")
        return raw
    except asyncio.TimeoutError:
        log_event(stage=stage, phase="timeout", op=op, label=label)
        return None
    except Exception as e:  # noqa: BLE001
        log_event(stage=stage, phase="error", op=op, label=label, notes=repr(e))
        return None
    finally:
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass


# ─────────────────────── main ───────────────────────

async def main(args):
    # Включаем jsonl-streaming если попросили (для tail -f во время прогона)
    global JSONL_HANDLE, USE_BBPB, LEARNED_TYPEDEF
    if args.jsonl:
        JSONL_HANDLE = open(args.jsonl, "w", encoding="utf-8")
        print(f"[+] streaming event-log to {args.jsonl}")

    # bbpb mode: rich type-learning + re-encode capability
    if args.use_bbpb:
        if bbpb_wrap.is_available():
            USE_BBPB = True
            print("[+] using bbpb (blackboxprotobuf) for proto-wire decoding")
            if args.typedef:
                LEARNED_TYPEDEF = bbpb_wrap.load_typedef(args.typedef)
                if LEARNED_TYPEDEF:
                    print(f"    loaded prior typedef from {args.typedef} "
                          f"({len(LEARNED_TYPEDEF)} entries)")
        else:
            print("[!] --use-bbpb requested but bbpb not installed; "
                  "running with fallback decoder. `pip install bbpb`.")

    print(f"[+] Loading {args.map}")
    log_event(stage="-", phase="note", notes=f"deep-fuzz start, map={args.map}")
    with open(args.map) as f:
        m = json.load(f)
    host, port = m["host"], m["port"]
    roles = m["envelope_roles"]
    auth = m.get("auth_envelope") or {}
    token = m.get("pin_token")

    if not token:
        print("[!] No pin_token in map — running unauthenticated.")
    env_kwargs = dict(
        rid_tag=roles["rid_tag"],
        body_tag=roles["body_tag"],
        cid_tag=roles.get("client_id_tag"),
        token=token,
        token_tag=auth.get("token_tag"),
        token_type_tag=auth.get("token_type_tag"),
        token_type_value=auth.get("token_type_value", 1),
    )

    known_ops = {int(k) for k in (m.get("op_sweep") or {}).keys()
                 if (m["op_sweep"][k] or {}).get("size")}
    known_actions = {int(k) for k in (m.get("media_actions") or {}).keys()}

    print(f"  host={host} port={port}")
    print(f"  known ops with reply: {sorted(known_ops)}")
    print(f"  known media-actions: {sorted(known_actions)}")

    extended: dict[str, Any] = {"source_map": args.map, "host": host, "port": port}

    # A. Hole sweep
    sweep = await hole_sweep(host, port, env_kwargs, args.max_op)
    extended["hole_sweep"] = sweep

    responding = sorted([op for op, r in sweep.items() if r.get("size")])
    new_ops = sorted(set(responding) - known_ops)
    print(f"\n  Total responding: {len(responding)}; new (not in baseline): {new_ops}")
    extended["new_responding_ops"] = new_ops

    # B. Subfield fuzz для интересных
    print("\n━━━ B. SUBFIELD FUZZ for new + already-known ops ━━━")
    sub: dict[int, Any] = {}
    targets = list(set(responding))
    targets.sort()
    for op in targets:
        print(f"  op={op}…")
        sub[op] = await subfield_fuzz(host, port, env_kwargs, op)
    extended["subfield_fuzz"] = sub

    # C. Push-subscribe probe
    print("\n━━━ C. PUSH-SUBSCRIBE TRIGGER probe ━━━")
    push: dict[int, int] = {}
    for op in responding:
        cnt = await push_subscribe_probe(host, port, env_kwargs, op, args.push_listen_seconds)
        push[op] = cnt
        marker = "★" if cnt > 0 else " "
        print(f"  op={op:3d}{marker} push-msgs in {args.push_listen_seconds}s = {cnt}")
    extended["push_subscribe_probe"] = push

    # D. TTS scan (interactive)
    if not args.skip_tts and responding:
        try:
            tts = await tts_scan(host, port, env_kwargs, responding)
            extended["tts_candidates"] = tts
        except KeyboardInterrupt:
            pass

    # E. Extended action fuzz
    if args.action_extra > 0 and m.get("media_command_op") and m.get("get_state_op"):
        try:
            ans = input("\nЗапустить extended action fuzz (требуется играющий трек)? [Y/n] ").strip().lower()
            if ans in ("", "y", "yes"):
                actions = await extended_action_fuzz(
                    host, port, env_kwargs,
                    media_command_op=m["media_command_op"],
                    get_state_op=m["get_state_op"],
                    start=max(known_actions, default=-1) + 1,
                    end=max(known_actions, default=-1) + args.action_extra,
                    known_actions=known_actions,
                )
                extended["extended_actions"] = actions
        except (EOFError, KeyboardInterrupt):
            pass

    # Save
    def _ser(v):
        if isinstance(v, dict):
            return {str(k): _ser(x) for k, x in v.items()}
        if isinstance(v, list):
            return [_ser(x) for x in v]
        if isinstance(v, bytes):
            return v.hex()
        return v

    # Полный event-log идёт в финальный JSON отдельным разделом
    log_event(stage="-", phase="note",
              notes=f"deep-fuzz finished, total events={len(EVENT_LOG)}")
    extended["event_log"] = list(EVENT_LOG)
    extended["event_log_size"] = len(EVENT_LOG)

    # Сохраняем накопленный typedef если bbpb использовался
    if USE_BBPB and args.typedef and LEARNED_TYPEDEF:
        bbpb_wrap.save_typedef(args.typedef, LEARNED_TYPEDEF)
        print(f"[+] saved learned typedef → {args.typedef} "
              f"({len(LEARNED_TYPEDEF)} entries)")
        extended["typedef_path"] = args.typedef
        extended["typedef_size"] = len(LEARNED_TYPEDEF)

    with open(args.out, "w") as f:
        json.dump(_ser(extended), f, indent=2, ensure_ascii=False)
    if JSONL_HANDLE:
        JSONL_HANDLE.close()

    # Summary
    print("\n" + "═" * 64)
    print(f"  Saved → {args.out}\n")
    print(f"  Found NEW responding ops: {new_ops}")
    push_active = [op for op, c in push.items() if c > 0]
    print(f"  Ops triggering push-stream: {push_active}")
    if "tts_candidates" in extended:
        print(f"  TTS candidates (operator-confirmed): {list(extended['tts_candidates'].keys())}")
    if "extended_actions" in extended:
        new_actions = [a for a, info in extended["extended_actions"].items() if info.get("diff")]
        print(f"  NEW media-actions with effect: {new_actions}")
    print("═" * 64)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--map", default="protocol_map.json",
                   help="Output of auto_discover.py")
    p.add_argument("--out", default="protocol_map_extended.json")
    p.add_argument("--max-op", type=int, default=64)
    p.add_argument("--push-listen-seconds", type=float, default=3.0)
    p.add_argument("--action-extra", type=int, default=24,
                   help="How many actions to try beyond max known (set 0 to skip)")
    p.add_argument("--skip-tts", action="store_true")
    p.add_argument("--jsonl", default=None,
                   help="Path для streaming event-log (jsonl) — для tail -f во время прогона")
    p.add_argument("--use-bbpb", action="store_true",
                   help="Use bbpb (blackboxprotobuf) for richer decode + type-learning. "
                        "pip install bbpb")
    p.add_argument("--typedef", default=None,
                   help="Path к json-файлу с накопленной type-схемой. "
                        "Загружается в начале, обновляется в конце.")
    asyncio.run(main(p.parse_args()))
