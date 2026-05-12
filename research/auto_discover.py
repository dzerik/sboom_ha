"""auto_discover.py — единый pipeline black-box discovery.

Прогоняет все этапы от network-разведки до карты media-команд в одном
запуске. Между этапами — короткие диалоги с оператором (нажать "+",
запустить трек, подтвердить физическую реакцию устройства).

Результат — `protocol_map.json` с полной картой нащупанного протокола.

Использование:
    pip install websockets zeroconf
    python research/auto_discover.py
    # или с готовым host:
    python research/auto_discover.py --host <host> --port <port>

Между шагами скрипт сам подсказывает что делать. Прервать в любой момент
можно через Ctrl-C — частичные результаты сохранятся в protocol_map.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
import uuid
from typing import Any

from _shared import decode, field, find_jsons, ws_open

PROTOCOL_MAP: dict[str, Any] = {}
OUT_FILE = "protocol_map.json"


# ─────────────────────── helpers ───────────────────────

def banner(text: str) -> None:
    line = "━" * 72
    print(f"\n{line}\n  {text}\n{line}")


def ask(prompt: str, default: str = "y") -> str:
    """Запрос ответа у оператора. Default — если просто Enter."""
    try:
        ans = input(f"  {prompt} ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print("\n  [aborted by user]")
        save_partial()
        sys.exit(1)
    return ans or default.lower()


def yes(ans: str) -> bool:
    return ans in ("y", "yes", "д", "да", "1", "true")


def save_partial() -> None:
    try:
        def _ser(v):
            if isinstance(v, dict):
                return {str(k): _ser(x) for k, x in v.items()}
            if isinstance(v, list):
                return [_ser(x) for x in v]
            if isinstance(v, bytes):
                return v.hex()
            return v
        with open(OUT_FILE, "w") as f:
            json.dump(_ser(PROTOCOL_MAP), f, indent=2, ensure_ascii=False)
        print(f"  [+] saved {OUT_FILE}")
    except Exception as e:  # noqa: BLE001
        print(f"  [!] couldn't save: {e!r}")


# ─────────────────────── 1. mDNS discovery ───────────────────────

async def step_discover(timeout: float) -> tuple[str, int] | None:
    banner("STEP 1 — Network discovery (mDNS broad browse)")
    try:
        from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
    except ImportError:
        print("  [!] zeroconf not installed — skip mDNS, fall back to manual input")
        return None

    HINTS = ("speaker", "audio", "media", "cast", "tv", "display",
             "player", "sound", "music", "stream", "remote", "iot",
             "smart", "device", "home")
    STANDARD = (
        "_http.", "_https.", "_ipp.", "_ipps.", "_printer.",
        "_airplay.", "_raop.", "_companion-link.", "_smb.", "_afpovertcp.",
        "_ssh.", "_sftp-ssh.", "_ftp.", "_workstation.",
        "_googlecast.", "_googlerpc.",
    )
    def _is_proprietary(stype: str) -> bool:
        s = stype.lower()
        return not any(std in s for std in STANDARD)
    found: list[dict[str, Any]] = []
    types_seen: set[str] = set()

    class TL(ServiceListener):
        def add_service(self, zc, type_, name): types_seen.add(name)
        def remove_service(self, zc, type_, name): pass
        def update_service(self, zc, type_, name): pass

    class SL(ServiceListener):
        def add_service(self, zc, type_, name):
            info = zc.get_service_info(type_, name, timeout=2000)
            if not info:
                return
            for addr in info.parsed_addresses() or []:
                found.append({
                    "type": type_, "name": name,
                    "host": addr, "port": info.port,
                    "props": {
                        (k.decode() if isinstance(k, bytes) else k):
                        (v.decode(errors="replace") if isinstance(v, bytes) else v)
                        for k, v in (info.properties or {}).items()
                    },
                })
        def remove_service(self, zc, type_, name): pass
        def update_service(self, zc, type_, name): pass

    zc = Zeroconf()
    print(f"  Listening for mDNS {timeout}s …")
    tb = ServiceBrowser(zc, "_services._dns-sd._udp.local.", TL())
    await asyncio.sleep(timeout)
    tb.cancel()
    browsers = [ServiceBrowser(zc, t, SL()) for t in list(types_seen)]
    await asyncio.sleep(timeout)
    for b in browsers:
        b.cancel()
    zc.close()

    if not found:
        print("  No services discovered. Manual input fallback.")
        return None

    # Двойная сортировка: 1) proprietary protocols сверху (не http/airplay/printer/...);
    # 2) bonus за hint-слова в name/type/props (speaker, audio, media, ...).
    # Showing ALL найденные сервисы — proprietary устройство может не иметь ни одного
    # hint-слова в имени (это не баг, это типичный кейс).
    def hint_score(s):
        text = f"{s['name']} {s['type']} {s['props']}".lower()
        return sum(1 for kw in HINTS if kw in text)
    def rank(s):
        return (1 if _is_proprietary(s["type"]) else 0, hint_score(s))
    found.sort(key=rank, reverse=True)

    print(f"\n  Found {len(found)} mDNS instances. ALL listed; "
          f"[PROP]=non-standard protocol (likely target), [hint]=device-class word:\n")
    for i, s in enumerate(found):
        prop_mark = "[PROP]" if _is_proprietary(s["type"]) else ("[hint]" if hint_score(s) > 0 else "      ")
        print(f"  [{i:2d}] {prop_mark} {s['type']:30s}  {s['host']}:{s['port']}  "
              f"name={s['name'][:40]!r}")
    PROTOCOL_MAP["discovery_candidates"] = found

    sel = ask("Use which? Enter index or N for none [0..N]:", "0")
    if sel.startswith("n"):
        return None
    try:
        s = found[int(sel)]
        return s["host"], s["port"]
    except (ValueError, IndexError):
        return None


# ─────────────────────── 2. WS probe ───────────────────────

async def step_probe(host: str, port: int) -> bool:
    banner(f"STEP 2 — Probe {host}:{port}")
    try:
        ws = await ws_open(host, port)
        await ws.close()
    except Exception as e:  # noqa: BLE001
        print(f"  [!] WebSocket handshake FAILED: {e!r}")
        return False
    print("  ✓ WebSocket over TLS — handshake accepted")
    PROTOCOL_MAP["probe"] = {"protocol": "wss", "host": host, "port": port}
    return True


# ─────────────────────── 3. Initial capture ───────────────────────

async def step_capture(host: str, port: int, duration: float) -> dict[str, Any]:
    banner(f"STEP 3 — Initial capture (listen {duration}s)")
    print("  Подключаемся, слушаем что само присылает устройство.")
    print("  Если ничего не приходит — это нормально, она ждёт запросов.")

    ws = await ws_open(host, port)
    msgs: list[dict[str, Any]] = []
    deadline = time.time() + duration
    while time.time() < deadline:
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=deadline - time.time())
        except asyncio.TimeoutError:
            break
        except Exception:  # noqa: BLE001
            break
        if isinstance(msg, str):
            msg = msg.encode()
        msgs.append({"size": len(msg), "tlv": decode(msg), "hex_preview": msg[:32].hex()})
    await ws.close()

    print(f"  Got {len(msgs)} unsolicited message(s)")
    PROTOCOL_MAP["initial_capture"] = msgs
    return {"messages": msgs}


# ─────────────────────── 4a. Envelope role inference (auto) ───────────────────────

async def _try_send_one_field(host, port, tag: int, kind: int, payload, timeout=1.5):
    """Свежее WS, отправить один TLV-field, вернуть raw reply (или None)."""
    try:
        ws = await ws_open(host, port)
    except Exception:  # noqa: BLE001
        return None
    try:
        await ws.send(field(tag, kind, payload))
        msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
        return msg if isinstance(msg, bytes) else msg.encode()
    except (asyncio.TimeoutError, Exception):  # noqa: BLE001
        return None
    finally:
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass


async def _try_send_combo(host, port, fields_def, timeout=2.0):
    try:
        ws = await ws_open(host, port)
    except Exception:  # noqa: BLE001
        return None
    try:
        payload = b"".join(field(t, k, v) for t, k, v in fields_def)
        await ws.send(payload)
        msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
        return msg if isinstance(msg, bytes) else msg.encode()
    except (asyncio.TimeoutError, Exception):  # noqa: BLE001
        return None
    finally:
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass


async def infer_envelope_roles(
    host: str, port: int, max_tag: int,
) -> dict[str, Any]:
    """Автоматически высчитываем tag-роли в envelope.

    KEY INSIGHT (из живой сессии): silent-серверы НЕ отвечают на single-field
    probes. Они ждут полную валидную обёртку из 3+ полей одновременно. Single-
    field эвристики тут бесполезны — все timeout'ят.

    Стратегия: пробуем **multi-field combos** на базе общих RPC-конвенций:
    - field(?,0,2) = type-varint (REQUEST код, типично 2 в gRPC-like API)
    - field(?,2,uuid) = request_id (string)
    - field(?,2,...) = body (bytes / nested message)

    Шаги:
    1) Brute-force перестановки (type_tag, rid_tag, body_tag) ∈ [1..max_tag]³
       с уникальным rid-маркером. Reply, в котором маркер echo-нулся → нашли
       rid_tag. Combos ≠ только тогда, когда сервер реально начал парсить
       обёртку (значит type_tag и body_tag тоже валидны).
    2) Найти client_id_tag: добавить ещё один поле, посмотреть как меняется
       размер reply (опционально).
    """
    banner("STEP 4a — Inferring envelope tag-roles (multi-field hypothesis)")

    rid_marker = uuid.uuid4().hex.encode()

    # Шаги 1+2 объединены: ищем минимальный envelope, который порождает
    # reply с echo нашего rid-маркера. Это сразу подтверждает type_tag,
    # rid_tag, body_tag.

    print("  Probing (type_tag, rid_tag, body_tag) permutations …")
    print("  (typical RPC convention: type=varint(2), rid=string, body=bytes)")

    # Используем small candidate-set чтобы не делать max_tag^3 запросов.
    # На практике большинство RPC-протоколов кладут эти поля в первые 12 тагов.
    candidates = list(range(1, min(max_tag, 12) + 1))

    found: dict[str, int] | None = None
    for type_tag in candidates:
        for rid_tag in candidates:
            if rid_tag == type_tag:
                continue
            for body_tag in candidates:
                if body_tag in (type_tag, rid_tag):
                    continue
                parts = [
                    (type_tag, 0, 2),         # type=REQUEST
                    (rid_tag, 2, rid_marker), # request_id с уникальным маркером
                    (body_tag, 2, b""),       # пустое тело
                ]
                reply = await _try_send_combo(host, port, parts, timeout=1.5)
                if reply and rid_marker in reply:
                    found = {"type_tag": type_tag, "rid_tag": rid_tag, "body_tag": body_tag}
                    print(f"      ★ MIN ENVELOPE: type={type_tag}, "
                          f"rid={rid_tag}, body={body_tag} "
                          f"(reply {len(reply)}b, rid echoed)")
                    break
            if found: break
        if found: break

    if not found:
        print("  [!] No 3-field envelope produced echo. Server may need more fields.")
        return {"rid_tag": None, "body_tag": None, "client_id_tag": None,
                "type_tag": None}

    # Дополнительные опциональные поля: token-type, client-name, is-request,
    # client-id. Не критичны для базовой работы, но улучшают reply quality.
    rid_tag = found["rid_tag"]
    body_tag = found["body_tag"]
    type_tag = found["type_tag"]

    # Поищем client_id_tag: какой ещё tag меняет reply при добавлении
    print("\n  Probing optional client_id-tag (reply size changes when added)…")
    base_parts = [
        (type_tag, 0, 2),
        (rid_tag, 2, uuid.uuid4().hex.encode()),
        (body_tag, 2, b""),
    ]
    base_reply = await _try_send_combo(host, port, base_parts, timeout=1.5)
    base_size = len(base_reply) if base_reply else 0

    cid_tag: int | None = None
    for tag in candidates:
        if tag in (type_tag, rid_tag, body_tag):
            continue
        new_parts = list(base_parts) + [(tag, 2, uuid.uuid4().hex.encode())]
        nr = await _try_send_combo(host, port, new_parts, timeout=1.5)
        if nr and abs(len(nr) - base_size) > 2:
            cid_tag = tag
            print(f"      client_id-tag candidate: {tag} "
                  f"(reply size {base_size}→{len(nr)})")
            break

    result = {
        "type_tag": type_tag,
        "rid_tag": rid_tag,
        "body_tag": body_tag,
        "client_id_tag": cid_tag,
    }
    print(f"\n  [+] Inferred envelope: {result}")
    return result


# ─────────────────────── 4. Envelope fuzz ───────────────────────

async def _send_fresh(host: str, port: int, payload: bytes, timeout: float = 1.5) -> bytes | None:
    try:
        ws = await ws_open(host, port)
    except Exception:  # noqa: BLE001
        return None
    try:
        await ws.send(payload)
        msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
        return msg if isinstance(msg, bytes) else msg.encode()
    except (asyncio.TimeoutError, Exception):  # noqa: BLE001
        return None
    finally:
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass


async def step_envelope_fuzz(host: str, port: int, max_tag: int) -> dict[str, Any]:
    banner(f"STEP 4 — Envelope fuzz (single-field probes, tags 1..{max_tag})")
    print("  Шлём пустой пакет с одним полем разных тегов и типов;")
    print("  ответ или пустота — намёк на роль каждого тега.\n")

    by_tag: dict[int, dict[str, Any]] = {}
    for tag in range(1, max_tag + 1):
        for kind in (0, 2):
            payload = field(tag, 0, 1) if kind == 0 else field(tag, 2, b"x")
            reply = await _send_fresh(host, port, payload)
            slot = by_tag.setdefault(tag, {})
            if reply:
                tlv = decode(reply)
                slot[f"kind{kind}"] = {
                    "size": len(reply),
                    "reply_tags": list(tlv.keys()) if isinstance(tlv, dict) else [],
                }
            else:
                slot[f"kind{kind}"] = "no-reply"
        # компактный print
        k0 = by_tag[tag].get("kind0", "?")
        k2 = by_tag[tag].get("kind2", "?")
        print(f"    tag={tag:2d}: varint={k0}, length-delim={k2}")

    PROTOCOL_MAP["envelope_fuzz"] = by_tag

    # Эвристики ролей: tag в reply'ях указывает на «error code», крайне частый
    # tag = поле req-id (echoed back), и т.п. Скрипту достаточно дать
    # финальные «лучшие» догадки оператору и подтверждения.
    print("\n  Эвристики (на основе fuzz'а):")
    most_response_tag = max(by_tag, key=lambda t: sum(
        1 for k in ("kind0", "kind2")
        if isinstance(by_tag[t].get(k), dict)
    ), default=None)
    print(f"    Most-responsive top-tag: {most_response_tag}")
    return by_tag


# ─────────────────────── 5. Pair discovery ───────────────────────

def _scan_for_token(tree: Any) -> str | None:
    if isinstance(tree, str):
        if len(tree) >= 32 and re.fullmatch(r"[A-Za-z0-9+/=._\-]+", tree):
            return tree
    if isinstance(tree, dict):
        for v in tree.values():
            t = _scan_for_token(v)
            if t:
                return t
    return None


async def step_pair_discovery(
    host: str, port: int, envelope_roles: dict[str, Any], max_op: int,
) -> tuple[int | None, str | None, dict[str, Any]]:
    banner("STEP 5 — Pair discovery (interactive)")
    print("  Будем перебирать op-tag'и и пробовать «init»-запросы. Если устройство")
    print("  физически отреагирует (мигнёт индикатор, прозвучит что-то) — отметь.\n")

    rid_tag = envelope_roles["rid_tag"]
    body_tag = envelope_roles["body_tag"]
    cid_tag = envelope_roles.get("client_id_tag")

    if rid_tag is None or body_tag is None:
        print("  [!] envelope tag-roles not inferred. Cannot proceed.")
        return None, None, {"error": "envelope inference failed"}

    print(f"  Using inferred envelope: rid_tag={rid_tag}, body_tag={body_tag}, "
          f"client_id_tag={cid_tag}\n")

    PROTOCOL_MAP["envelope_roles"] = envelope_roles

    confirmed_op: int | None = None
    token: str | None = None
    log: list[dict[str, Any]] = []

    print(f"\n  Перебор op_tag = 1..{max_op}. Перед каждым подождём 4 секунды,")
    print("  ты следишь за устройством.\n")

    for op_tag in range(1, max_op + 1):
        print(f"━━━ op_tag={op_tag} ━━━")
        # Соберём envelope
        parts = [field(rid_tag, 2, str(uuid.uuid4()).encode())]
        if cid_tag is not None:
            parts.append(field(cid_tag, 2, str(uuid.uuid4()).encode()))
        parts.append(field(body_tag, 2, field(op_tag, 2, field(1, 2, b""))))
        pkt = b"".join(parts)

        try:
            ws = await ws_open(host, port)
            await ws.send(pkt)
            replies: list[dict[str, Any]] = []
            deadline = time.time() + 4
            while time.time() < deadline:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=deadline - time.time())
                except asyncio.TimeoutError:
                    break
                except Exception:  # noqa: BLE001
                    break
                if isinstance(msg, str):
                    msg = msg.encode()
                replies.append({"size": len(msg), "tlv": decode(msg)})
            await ws.close()
        except Exception as e:  # noqa: BLE001
            print(f"    [error] {e!r}")
            log.append({"op": op_tag, "error": repr(e)})
            continue

        n_msgs = len(replies)
        first_keys = list(replies[0]["tlv"].keys()) if replies else []
        print(f"    {n_msgs} reply(ies), top tags: {first_keys}")
        log.append({"op": op_tag, "replies": replies})

        if n_msgs == 0:
            continue

        ans = ask(f"  Устройство отреагировала физически на op_tag={op_tag}? [y/N/q]:", "n")
        if ans == "q":
            break
        if not yes(ans):
            continue

        # Пользователь подтвердил физическую реакцию — это INIT! Просим нажать +
        print(f"\n  *** op_tag={op_tag} — pair-init candidate ***")
        print("  Сейчас нажми кнопку '+' на корпусе устройства.")
        print("  Слушаю ответ 90 секунд…\n")

        try:
            ws = await ws_open(host, port)
            await ws.send(pkt)
            tok_deadline = time.time() + 90
            while time.time() < tok_deadline:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=tok_deadline - time.time())
                except asyncio.TimeoutError:
                    break
                except Exception:  # noqa: BLE001
                    break
                if isinstance(msg, str):
                    msg = msg.encode()
                tree = decode(msg)
                t = _scan_for_token(tree)
                if t:
                    token = t
                    confirmed_op = op_tag
                    print(f"  [!!!] Token: {token}")
                    break
            await ws.close()
        except Exception as e:  # noqa: BLE001
            print(f"    [error during pair confirm: {e!r}]")
            continue

        if token:
            break

    return confirmed_op, token, {"log": log}


# ─────────────────────── 6. Op sweep + identify ───────────────────────

def _make_envelope(
    body: bytes, *, rid_tag: int, body_tag: int, cid_tag: int | None,
    extra: list[tuple[int, int, Any]],
) -> bytes:
    parts = [field(rid_tag, 2, str(uuid.uuid4()).encode())]
    if cid_tag is not None:
        parts.append(field(cid_tag, 2, str(uuid.uuid4()).encode()))
    for t, k, v in extra:
        parts.append(field(t, k, v))
    parts.append(field(body_tag, 2, body))
    return b"".join(parts)


async def infer_token_tag(
    host: str, port: int, token: str, env_roles: dict[str, Any],
    pair_op: int, max_tag: int = 16,
) -> dict[str, Any]:
    """Auto-detect token-tag: ищем tag, в который положенный токен меняет
    поведение запроса (без него — auth-error / короткий reply, с ним — длинный
    осмысленный)."""
    rid_tag = env_roles["rid_tag"]
    body_tag = env_roles["body_tag"]
    cid_tag = env_roles.get("client_id_tag")

    print("  (a) detecting token-tag (auth-required pattern)…")
    # Берём какой-нибудь op (используем pair_op как probe — он уже даёт reply)
    body = field(pair_op, 2, field(1, 2, b""))
    base_parts = [field(rid_tag, 2, uuid.uuid4().hex.encode())]
    if cid_tag is not None:
        base_parts.append(field(cid_tag, 2, uuid.uuid4().hex.encode()))
    base_parts.append(field(body_tag, 2, body))
    baseline = await _try_send_combo(host, port,
                                     [(rid_tag, 2, uuid.uuid4().hex.encode())]
                                     + ([(cid_tag, 2, uuid.uuid4().hex.encode())] if cid_tag else [])
                                     + [(body_tag, 2, body)])
    baseline_size = len(baseline) if baseline else 0

    best_tag: int | None = None
    best_diff = 0
    for tag in range(1, max_tag + 1):
        if tag in (rid_tag, body_tag) or tag == cid_tag:
            continue
        with_token = await _try_send_combo(
            host, port,
            [(rid_tag, 2, uuid.uuid4().hex.encode())]
            + ([(cid_tag, 2, uuid.uuid4().hex.encode())] if cid_tag else [])
            + [(tag, 2, token.encode()), (body_tag, 2, body)],
        )
        new_size = len(with_token) if with_token else 0
        diff = new_size - baseline_size
        if abs(diff) > best_diff:
            best_diff = abs(diff)
            best_tag = tag
    if best_tag is None:
        print("      [!] couldn't detect token-tag automatically")
        return {"token_tag": None, "token_type_tag": None, "token_type_value": 0}
    print(f"      token-tag = {best_tag} (size delta {best_diff})")

    # Token-type-tag — опциональная varint-флажка типа токена. Эвристика:
    # пробуем добавить varint=1 на каждый tag, ищем reply size change.
    print("  (b) detecting token-type-tag (optional varint flag)…")
    type_tag: int | None = None
    type_value = 0
    base_with_token = [(rid_tag, 2, uuid.uuid4().hex.encode())]
    if cid_tag is not None:
        base_with_token.append((cid_tag, 2, uuid.uuid4().hex.encode()))
    base_with_token += [(best_tag, 2, token.encode()), (body_tag, 2, body)]
    baseline2 = await _try_send_combo(host, port, base_with_token)
    base2_size = len(baseline2) if baseline2 else 0
    for tag in range(1, max_tag + 1):
        if tag in (rid_tag, body_tag, best_tag) or tag == cid_tag:
            continue
        # Проба с varint=1
        combo = list(base_with_token) + [(tag, 0, 1)]
        with_type = await _try_send_combo(host, port, combo)
        if with_type and abs(len(with_type) - base2_size) > 4:
            type_tag = tag
            type_value = 1
            print(f"      token-type-tag = {tag}, value=1")
            break

    return {"token_tag": best_tag, "token_type_tag": type_tag,
            "token_type_value": type_value}


async def step_op_sweep(
    host: str, port: int, token: str, max_op: int, pair_op: int,
) -> dict[int, dict[str, Any]]:
    banner(f"STEP 6 — Authorized op-sweep (1..{max_op})")
    roles = PROTOCOL_MAP["envelope_roles"]
    rid_tag = roles["rid_tag"]
    body_tag = roles["body_tag"]
    cid_tag = roles["client_id_tag"]

    print("  Auto-detecting auth envelope tags…")
    auth_roles = await infer_token_tag(host, port, token, roles, pair_op)
    token_tag = auth_roles["token_tag"]
    token_type_tag = auth_roles["token_type_tag"]
    token_type_value = auth_roles["token_type_value"]

    if token_tag is None:
        print("  [!] no token-tag inferred — op-sweep may return unauthorized.")

    extra: list[tuple[int, int, Any]] = []
    if token_tag is not None:
        extra.append((token_tag, 2, token.encode()))
    if token_type_tag is not None:
        extra.append((token_type_tag, 0, token_type_value))

    PROTOCOL_MAP["auth_envelope"] = auth_roles

    results: dict[int, dict[str, Any]] = {}
    print()
    for op in range(1, max_op + 1):
        body = field(op, 2, field(1, 2, b""))
        pkt = _make_envelope(body, rid_tag=rid_tag, body_tag=body_tag,
                             cid_tag=cid_tag, extra=extra)
        reply = await _send_fresh(host, port, pkt, timeout=2.0)
        if not reply:
            results[op] = {"reply": None}
            print(f"    op={op:2d}: no reply")
            continue
        tlv = decode(reply)
        jsons = find_jsons(reply)
        results[op] = {
            "size": len(reply),
            "top_tags": list(tlv.keys()) if isinstance(tlv, dict) else [],
            "json_count": len(jsons),
            "json_sample": jsons[0][:200] if jsons else None,
        }
        marker = "★" if jsons else " "
        print(f"    op={op:2d}{marker}: {len(reply)}b, jsons={len(jsons)}, tags={results[op]['top_tags']}")

    PROTOCOL_MAP["op_sweep"] = results
    return results


async def identify_volume_op(
    host: str, port: int, token: str,
    ops: dict[int, dict[str, Any]],
) -> int | None:
    """Find SET_VOLUME op via diff state.volume.percent.

    Strategy (proven in exp_13): sweep all ops returning ack-style reply
    (small size, status=1 or no body), send inner=field(1,0,50), check if
    volume.percent → 50.

    Live-test предпочитаемый ack ops пробуя varied percentages чтобы
    отличить от noise (физический пользователь меняет volume).
    """
    print("  Searching SET_VOLUME op via inner=field(1,0,percent) sweep…")
    roles = PROTOCOL_MAP["envelope_roles"]
    auth = PROTOCOL_MAP["auth_envelope"]
    extra: list[tuple[int, int, Any]] = []
    if auth.get("token_tag") is not None:
        extra.append((auth["token_tag"], 2, token.encode()))
    if auth.get("token_type_tag") is not None:
        extra.append((auth["token_type_tag"], 0, auth.get("token_type_value", 1)))

    state_op = PROTOCOL_MAP.get("get_state_op")
    if state_op is None:
        return None

    async def get_pct():
        body = field(state_op, 2, field(1, 2, b""))
        pkt = _make_envelope(body, rid_tag=roles["rid_tag"],
                             body_tag=roles["body_tag"],
                             cid_tag=roles["client_id_tag"], extra=extra)
        reply = await _send_fresh(host, port, pkt, timeout=2.0)
        if not reply:
            return None
        for j in find_jsons(reply):
            try:
                o = json.loads(j)
                if isinstance(o, dict) and "volume" in o:
                    return (o.get("volume") or {}).get("percent")
            except Exception:  # noqa: BLE001
                continue
        return None

    # Кандидаты: ops с small reply (44-46b)
    candidates = [op for op, info in ops.items()
                  if info.get("size") and info["size"] < 50
                  and not info.get("json_count")]

    # Test sequence: устанавливаем 25%, ожидаем reply
    for op in candidates:
        body = field(op, 2, field(1, 0, 25))
        pkt = _make_envelope(body, rid_tag=roles["rid_tag"],
                             body_tag=roles["body_tag"],
                             cid_tag=roles["client_id_tag"], extra=extra)
        await _send_fresh(host, port, pkt, timeout=2.0)
        await asyncio.sleep(0.6)
        pct1 = await get_pct()
        if pct1 != 25:
            continue
        # Подтверждение — другое значение
        body = field(op, 2, field(1, 0, 60))
        pkt = _make_envelope(body, rid_tag=roles["rid_tag"],
                             body_tag=roles["body_tag"],
                             cid_tag=roles["client_id_tag"], extra=extra)
        await _send_fresh(host, port, pkt, timeout=2.0)
        await asyncio.sleep(0.6)
        pct2 = await get_pct()
        if pct2 in (60, 50, 75):  # close (max-cap)
            print(f"      ★ SET_VOLUME op = {op} (percent {pct1}→{pct2})")
            return op

    return None


async def identify_seek_op(
    host: str, port: int, token: str,
    ops: dict[int, dict[str, Any]], get_metadata_op: int,
) -> int | None:
    """Find SEEK_TO_POSITION op via diff metadata.position.val."""
    print("  Searching SEEK_TO_POSITION op via inner=field(1,0,seconds)…")
    roles = PROTOCOL_MAP["envelope_roles"]
    auth = PROTOCOL_MAP["auth_envelope"]
    extra: list[tuple[int, int, Any]] = []
    if auth.get("token_tag") is not None:
        extra.append((auth["token_tag"], 2, token.encode()))
    if auth.get("token_type_tag") is not None:
        extra.append((auth["token_type_tag"], 0, auth.get("token_type_value", 1)))

    async def get_pos():
        body = field(get_metadata_op, 2, field(1, 2, b""))
        pkt = _make_envelope(body, rid_tag=roles["rid_tag"],
                             body_tag=roles["body_tag"],
                             cid_tag=roles["client_id_tag"], extra=extra)
        reply = await _send_fresh(host, port, pkt, timeout=2.0)
        if not reply:
            return None
        for j in find_jsons(reply):
            try:
                o = json.loads(j)
                if isinstance(o, dict) and "trackId" in o:
                    pos = o.get("position")
                    return pos.get("val") if isinstance(pos, dict) else pos
            except Exception:  # noqa: BLE001
                continue
        return None

    candidates = [op for op, info in ops.items()
                  if info.get("size") and info["size"] < 50
                  and not info.get("json_count")]

    for op in candidates:
        body = field(op, 2, field(1, 0, 90))
        pkt = _make_envelope(body, rid_tag=roles["rid_tag"],
                             body_tag=roles["body_tag"],
                             cid_tag=roles["client_id_tag"], extra=extra)
        await _send_fresh(host, port, pkt, timeout=2.0)
        await asyncio.sleep(0.6)
        pos = await get_pos()
        if pos and 85 <= pos <= 95:    # tolerate small drift
            print(f"      ★ SEEK_TO_POSITION op = {op} (position={pos})")
            return op

    return None


async def identify_subscribe_op(
    host: str, port: int, token: str, get_metadata_op: int,
    media_command_op: int | None,
) -> bool:
    """Test if get_metadata_op activates push-subscribe stream.

    Method (exp_10): open WS, send op, listen 5s, провоцируем track-change
    через media_command в отдельном соединении. Если получаем >=1 unsolicited
    push после первого reply — op activates subscribe.
    """
    if media_command_op is None:
        print("  Skipping subscribe test — no media-command op")
        return False
    print(f"  Testing if op={get_metadata_op} activates push-subscribe…")

    roles = PROTOCOL_MAP["envelope_roles"]
    auth = PROTOCOL_MAP["auth_envelope"]
    extra: list[tuple[int, int, Any]] = []
    if auth.get("token_tag") is not None:
        extra.append((auth["token_tag"], 2, token.encode()))
    if auth.get("token_type_tag") is not None:
        extra.append((auth["token_type_tag"], 0, auth.get("token_type_value", 1)))

    def envelope(op: int, inner: bytes) -> bytes:
        return _make_envelope(field(op, 2, inner),
                              rid_tag=roles["rid_tag"],
                              body_tag=roles["body_tag"],
                              cid_tag=roles["client_id_tag"], extra=extra)

    # Open WS, subscribe via get_metadata_op
    try:
        ws = await ws_open(host, port)
    except Exception:  # noqa: BLE001
        return False
    try:
        await ws.send(envelope(get_metadata_op, field(1, 2, b"")))
        # Получаем sync reply
        try:
            await asyncio.wait_for(ws.recv(), timeout=2.0)
        except Exception:  # noqa: BLE001
            pass

        # Provoke event через отдельное соединение (next-track action=2)
        async def fire():
            try:
                ws2 = await ws_open(host, port)
                await ws2.send(envelope(media_command_op, field(1, 0, 2)))
                try:
                    await asyncio.wait_for(ws2.recv(), timeout=1.0)
                except Exception:  # noqa: BLE001
                    pass
                await ws2.close()
            except Exception:  # noqa: BLE001
                pass

        await fire()

        # Listen for push messages
        push_count = 0
        deadline = time.time() + 5.0
        while time.time() < deadline:
            try:
                await asyncio.wait_for(ws.recv(), timeout=deadline - time.time())
                push_count += 1
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                break

        print(f"      → {push_count} push messages received in 5s")
        return push_count > 0
    finally:
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass


def identify_get_state(ops: dict[int, dict[str, Any]]) -> int | None:
    """GET_STATE = op возвращающий самый большой JSON с playback-полями."""
    best, best_size = None, 0
    for op, info in ops.items():
        if info.get("json_count") and info.get("size", 0) > best_size:
            sample = info.get("json_sample") or ""
            if any(k in sample for k in ('"volume"', '"playing"', '"muted"', '"position"')):
                best, best_size = op, info["size"]
    return best


def identify_media_command_candidate(ops: dict[int, dict[str, Any]]) -> int | None:
    """MEDIA_COMMAND = op принимающий short payload и возвращающий ack-like reply."""
    candidates: list[tuple[int, int]] = []
    for op, info in ops.items():
        size = info.get("size") or 0
        if 1 < size < 200 and not info.get("json_count"):
            candidates.append((op, size))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[1])
    return candidates[0][0]


# ─────────────────────── 7. Action fuzz ───────────────────────

async def step_action_fuzz(
    host: str, port: int, token: str,
    get_state_op: int, media_command_op: int,
    max_action: int,
) -> dict[int, dict[str, Any]]:
    banner(f"STEP 7 — Action fuzz on op={media_command_op} (interactive)")
    print(f"  GET_STATE op = {get_state_op}")
    print(f"  MEDIA_COMMAND op = {media_command_op}")
    print()
    print("  Действия будут менять состояние устройства. Запусти трек ВРУЧНУЮ")
    print("  через приложение/голос (нужно живое воспроизведение).")
    ans = ask("  Готов? Трек играет? [Y/n]:", "y")
    if not yes(ans):
        print("  Пропускаем action fuzz.")
        return {}

    roles = PROTOCOL_MAP["envelope_roles"]
    auth = PROTOCOL_MAP["auth_envelope"]

    extra: list[tuple[int, int, Any]] = [(auth["token_tag"], 2, token.encode())]
    if auth["token_type_tag"] is not None:
        extra.append((auth["token_type_tag"], 0, auth["token_type_value"]))

    def envelope_for(body: bytes) -> bytes:
        return _make_envelope(
            body, rid_tag=roles["rid_tag"], body_tag=roles["body_tag"],
            cid_tag=roles["client_id_tag"], extra=extra,
        )

    def get_state_pkt() -> bytes:
        return envelope_for(field(get_state_op, 2, field(1, 2, b"")))

    def action_pkt(action: int) -> bytes:
        return envelope_for(field(media_command_op, 2, field(1, 0, action)))

    ws = await ws_open(host, port)
    print("\n  baseline state…")
    base_raw = None
    try:
        await ws.send(get_state_pkt())
        base_raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
    except Exception:  # noqa: BLE001
        pass
    if isinstance(base_raw, str):
        base_raw = base_raw.encode()

    def parse_state(raw):
        if not raw:
            return None
        for js in find_jsons(raw):
            try:
                return json.loads(js)
            except json.JSONDecodeError:
                pass
        return None

    def flatten(d, prefix=""):
        out = {}
        for k, v in (d or {}).items():
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                out.update(flatten(v, key))
            else:
                out[key] = v
        return out

    baseline = parse_state(base_raw)
    if baseline:
        print(f"  baseline keys: {list(baseline.keys())[:8]}")

    results: dict[int, dict[str, Any]] = {}
    prev = baseline
    for action in range(max_action + 1):
        try:
            await ws.send(action_pkt(action))
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(0.6)
        try:
            await ws.send(get_state_pkt())
            after_raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
        except Exception:  # noqa: BLE001
            after_raw = None
        if isinstance(after_raw, str):
            after_raw = after_raw.encode()
        after = parse_state(after_raw)

        fb, fa = flatten(prev), flatten(after)
        keys = set(fb) | set(fa)
        diff = []
        for k in sorted(keys):
            if fb.get(k) != fa.get(k):
                diff.append(f"{k}: {fb.get(k)!r}→{fa.get(k)!r}")
        results[action] = {"diff": diff}
        marker = "→" if diff else " "
        print(f"    action={action:2d}{marker} {diff if diff else '(no change)'}")
        prev = after

    await ws.close()
    PROTOCOL_MAP["media_actions"] = results
    return results


# ─────────────────────── main ───────────────────────

async def main(args):
    print("\n  ╔══════════════════════════════════════════════════════════╗")
    print("  ║  black-box discovery pipeline (auto + interactive)      ║")
    print("  ║                                                         ║")
    print("  ║  Что произойдёт:                                        ║")
    print("  ║   1. mDNS browse (если zeroconf установлен)             ║")
    print("  ║   2. Probe — endpoint = WebSocket?                      ║")
    print("  ║   3. Capture — слушаем что само присылает               ║")
    print("  ║   4. Envelope fuzz — структура обёртки запроса          ║")
    print("  ║   4a. Auto-infer envelope tag-roles                     ║")
    print("  ║   5. Pair discovery — INTERACTIVE                       ║")
    print("  ║   6. Op-sweep — auto-detect token-tag, sweep ops        ║")
    print("  ║   7. Action fuzz — INTERACTIVE: запустишь трек          ║")
    print("  ║                                                         ║")
    print("  ║  Результат → protocol_map.json                          ║")
    print("  ╚══════════════════════════════════════════════════════════╝")

    host, port = args.host, args.port

    if not host:
        candidate = await step_discover(args.mdns_timeout)
        if candidate:
            host, port = candidate
        else:
            host = ask("Host:", "")
            port = int(ask("Port:", ""))

    PROTOCOL_MAP["host"] = host
    PROTOCOL_MAP["port"] = port

    if not await step_probe(host, port):
        save_partial()
        return

    await step_capture(host, port, args.capture_seconds)
    save_partial()

    await step_envelope_fuzz(host, port, args.envelope_max_tag)
    save_partial()

    # Step 4a: автоматически высчитываем tag-роли в envelope
    envelope_roles = await infer_envelope_roles(host, port, args.envelope_max_tag)
    PROTOCOL_MAP["envelope_roles_inferred"] = envelope_roles
    save_partial()

    op, token, _ = await step_pair_discovery(host, port, envelope_roles, args.pair_max_op)
    if not token:
        print("\n  [!] Pair didn't produce token. Check the run log in protocol_map.json.")
        save_partial()
        return
    PROTOCOL_MAP["pair_op"] = op
    PROTOCOL_MAP["pin_token"] = token
    save_partial()

    ops = await step_op_sweep(host, port, token, args.op_sweep_max, pair_op=op)
    save_partial()

    get_state = identify_get_state(ops)
    media_cmd = identify_media_command_candidate(ops)
    print(f"\n  ★ Auto-identified: GET_STATE op = {get_state}")
    print(f"  ★ Media-command candidate op = {media_cmd}")

    confirm = ask(f"Use these? Override? [enter for yes / 'gN,mN' to override]:", "")
    if confirm:
        m = re.match(r"g(\d+),m(\d+)", confirm)
        if m:
            get_state = int(m.group(1))
            media_cmd = int(m.group(2))

    if get_state is None or media_cmd is None:
        print("  [!] Could not identify GET_STATE / MEDIA_COMMAND ops; skipping action fuzz.")
        save_partial()
        return

    PROTOCOL_MAP["get_state_op"] = get_state
    PROTOCOL_MAP["media_command_op"] = media_cmd

    # ─── New steps from sequel-session findings ───────────────────────
    banner("STEP 6.5 — Identify SET_VOLUME / SEEK_TO_POSITION / SUBSCRIBE")

    # Find GET_METADATA op (отличается от GET_STATE — содержит trackId, не volume)
    get_metadata_op = None
    for op, info in ops.items():
        sample = info.get("json_sample") or ""
        if info.get("json_count") and "trackId" in sample and op != get_state:
            get_metadata_op = op
            break
    if get_metadata_op:
        print(f"  GET_METADATA op = {get_metadata_op}")
        PROTOCOL_MAP["get_metadata_op"] = get_metadata_op

    set_vol_op = await identify_volume_op(host, port, token, ops)
    if set_vol_op:
        PROTOCOL_MAP["set_volume_op"] = set_vol_op
    save_partial()

    seek_op = None
    if get_metadata_op:
        seek_op = await identify_seek_op(host, port, token, ops, get_metadata_op)
        if seek_op:
            PROTOCOL_MAP["seek_op"] = seek_op
    save_partial()

    has_subscribe = False
    if get_metadata_op:
        has_subscribe = await identify_subscribe_op(host, port, token, get_metadata_op, media_cmd)
        PROTOCOL_MAP["push_subscribe_via_get_metadata"] = has_subscribe
    save_partial()

    # ──────────────────────────────────────────────────────────────────
    await step_action_fuzz(host, port, token, get_state, media_cmd, args.action_max)
    save_partial()

    banner("DONE")
    print("\n  Final protocol map:")
    for k in ("host", "port", "envelope_roles", "auth_envelope",
              "pair_op", "get_state_op", "media_command_op"):
        print(f"    {k}: {PROTOCOL_MAP.get(k)}")
    if "media_actions" in PROTOCOL_MAP:
        print("    media_actions:")
        for action, info in PROTOCOL_MAP["media_actions"].items():
            print(f"      action={action}: {info['diff'] or '(none)'}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--host", default=None,
                   help="Skip discovery if host known. Otherwise mDNS-browse.")
    p.add_argument("--port", type=int, default=None,
                   help="Required if --host is set; otherwise from discovery.")
    p.add_argument("--mdns-timeout", type=float, default=8.0)
    p.add_argument("--capture-seconds", type=float, default=8.0)
    p.add_argument("--envelope-max-tag", type=int, default=12)
    p.add_argument("--pair-max-op", type=int, default=20)
    p.add_argument("--op-sweep-max", type=int, default=24)
    p.add_argument("--action-max", type=int, default=20)
    p.add_argument("--out", default="protocol_map.json")
    args = p.parse_args()
    OUT_FILE = args.out
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        save_partial()
