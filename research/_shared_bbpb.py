"""Опциональная обёртка над `bbpb` (nccgroup blackboxprotobuf).

bbpb — это решённая задача «декодинг proto-wire без .proto-файла» от nccgroup.
Преимущества над нашим self-contained `_shared.decode()`:

  1. **Re-encode** — bbpb может из распарсенного дерева собрать обратно byte-точный
     payload. Полезно для итеративного фуззинга (берёшь reply, чуть мутируешь, шлёшь).
  2. **Type-def auto-learning** — bbpb накапливает hypotheses о типах полей через
     несколько сообщений (видел varint? видел string? оба? — выбирает 'bytes'/'string'/
     'message'). Можно сохранить typedef.json и переиспользовать в следующих сессиях.
  3. **Поддерживается** — активно maintained nccgroup, последний релиз 2025.

Используется как drop-in:

    from _shared_bbpb import decode_smart, save_typedef, load_typedef
    decoded, learned_typedef = decode_smart(raw_bytes, prior_typedef)

Если bbpb не установлен — falls back на наш self-contained decode().

Установка bbpb:
    pip install bbpb

См. https://github.com/nccgroup/blackboxprotobuf
"""
from __future__ import annotations

import json
from typing import Any

try:
    import blackboxprotobuf  # noqa: F401
    HAS_BBPB = True
except ImportError:
    HAS_BBPB = False

# Fallback на наш собственный decoder
from _shared import decode as _fallback_decode


def is_available() -> bool:
    """True если bbpb установлен и можно использовать rich-decode."""
    return HAS_BBPB


def decode_smart(
    raw: bytes, typedef: dict[str, Any] | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Декодировать бинарный payload.

    Если bbpb доступен — возвращает (decoded_dict, learned_typedef).
    Иначе — (fallback_dict, {}).

    Параметр `typedef` — известная type-схема из предыдущих запусков (sticky
    types между сессиями).
    """
    if HAS_BBPB:
        # bbpb API: decode_message(buf, typedef=None) → (decoded, typedef)
        try:
            decoded, learned = blackboxprotobuf.decode_message(raw, typedef or {})
            return decoded, learned
        except Exception:  # noqa: BLE001
            # bbpb может бросать на разных edge-cases — fallback
            pass
    return _fallback_decode(raw), {}


def encode_smart(decoded: dict[str, Any], typedef: dict[str, Any]) -> bytes | None:
    """Re-encode распарсенного дерева обратно в bytes.

    Без bbpb это невозможно (наш fallback decoder теряет wire-type для ambiguous
    payload'ов). Возвращает None если bbpb недоступен.
    """
    if not HAS_BBPB:
        return None
    try:
        return blackboxprotobuf.encode_message(decoded, typedef)
    except Exception:  # noqa: BLE001
        return None


def save_typedef(path: str, typedef: dict[str, Any]) -> None:
    """Сохранить накопленную type-схему. Делать после прогона."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(typedef, f, indent=2, ensure_ascii=False)


def load_typedef(path: str) -> dict[str, Any]:
    """Загрузить накопленную type-схему. Делать в начале нового прогона."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def merge_typedefs(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Слить две type-схемы. bbpb обычно делает это сам через accumulator-pattern,
    но если хочется мерджить из нескольких источников — простая глубокая union.
    """
    if not HAS_BBPB:
        return {**a, **b}
    out = dict(a)
    for k, v in b.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = merge_typedefs(out[k], v)
        else:
            out[k] = v
    return out
