"""Парсеры payload-форматов колонки.

Колонка отдаёт state и track-метаданные как JSON (внутри бинарной TLV-обёртки).
Здесь — функции `parse_state` и `parse_track`, не зависящие от транспорта.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from ._models import SpeakerState, TrackInfo

_LOGGER = logging.getLogger(__name__)


def parse_state(raw: bytes) -> SpeakerState:
    """Извлекает volume + muted из state-payload. Полный JSON — в .raw_state_json."""
    st = SpeakerState()
    s = raw.decode("utf-8", errors="ignore")
    m = re.search(r'"volume":\s*\{\s*"muted":\s*(true|false)\s*,\s*"percent":\s*(\d+)', s)
    if m:
        st.muted = m.group(1) == "true"
        st.volume_percent = int(m.group(2))
    idx = s.find("{")
    if idx >= 0:
        st.raw_state_json = s[idx:]
    return st


def parse_track(raw: bytes) -> Optional[TrackInfo]:
    """Парсит трек из payload. Поддерживает push-формат и state-обёртку.

    Стратегия: ищем `"trackId":"NNN"`, балансируем фигурные скобки чтобы захватить
    окружающий JSON-объект целиком, потом разбираем поля.
    """
    s = raw.decode("utf-8", errors="ignore")

    m = re.search(r'"trackId":"\d+"', s)
    if not m:
        return None

    # backward scan для открывающей `{`
    depth = 0
    start = -1
    for i in range(m.start() - 1, -1, -1):
        ch = s[i]
        if ch == '}':
            depth += 1
        elif ch == '{':
            if depth == 0:
                start = i
                break
            depth -= 1
    if start < 0:
        return None

    # forward scan — балансируем скобки чтобы найти конец объекта
    depth = 1
    in_str = False
    esc = False
    end = -1
    for i in range(start + 1, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end < 0:
        return None

    try:
        data = json.loads(s[start:end])
    except json.JSONDecodeError:
        _LOGGER.debug("track JSON parse failed: %s", s[start:end][:200])
        return None
    if "trackId" not in data:
        return None

    # ──────────────────────────────────────────────────────────────
    # Два наблюдаемых формата:
    # 1) Push-формат (flat): {"artists":[...], "trackId":..., "playing":...}
    # 2) State-формат (info-обёртка): {"artists":[...], "trackId":..., "duration":...}
    #    при этом поля "playing", "position", "shuffle" находятся уровнем
    #    ВЫШЕ — в player{}. У нас уже выбран только info{}, ищем
    #    окружающий player{} в том же исходном тексте.
    # ──────────────────────────────────────────────────────────────
    outer: dict[str, Any] = {}
    if "playing" not in data:
        # state-формат — ищем окружающий player{} JSON
        head = s[:start]
        pm = list(re.finditer(r'"player"\s*:\s*\{', head))
        if pm:
            player_open = pm[-1].end() - 1   # позиция '{'
            depth_p = 1
            in_str_p = False
            esc_p = False
            p_end = -1
            for i in range(player_open + 1, len(s)):
                ch = s[i]
                if in_str_p:
                    if esc_p: esc_p = False
                    elif ch == '\\': esc_p = True
                    elif ch == '"': in_str_p = False
                    continue
                if ch == '"': in_str_p = True
                elif ch == '{': depth_p += 1
                elif ch == '}':
                    depth_p -= 1
                    if depth_p == 0: p_end = i + 1; break
            if p_end > 0:
                try:
                    outer = json.loads(s[player_open:p_end])
                except json.JSONDecodeError:
                    outer = {}

    ti = TrackInfo(raw=data)
    ti.title = data.get("title")

    artists_list = data.get("artists") or []
    ti.artists = [a.get("name") for a in artists_list if a.get("name")]
    ti.artist_ids = [
        str(a.get("id")) for a in artists_list if a.get("id") is not None
    ]

    # releases: ключ названия — "name" (push) или "title" (state)
    rels = data.get("releases") or []
    if rels:
        r0 = rels[0]
        ti.album = r0.get("name") or r0.get("title")
        rel_id = r0.get("id")
        if rel_id is not None:
            ti.release_id = str(rel_id)

    ti.track_id = str(data.get("trackId")) if data.get("trackId") else None
    ti.playlist_title = data.get("playlistTitle") or (outer or {}).get("playlistTitle")
    ti.provider = data.get("provider") or (outer or {}).get("provider")

    dur = data.get("duration") or (outer or {}).get("duration") or 0
    ti.duration_sec = int(dur) if dur else None

    # position: push → dict {tsMs, val}; state → int секунды (в outer)
    pos_data = data.get("position")
    if isinstance(pos_data, dict):
        pv = pos_data.get("val")
        if pv is not None:
            ti.position_sec = int(pv)
        tsms = pos_data.get("tsMs")
        if tsms is not None:
            ti.position_ts_ms = int(tsms)
    elif isinstance(pos_data, (int, float)):
        ti.position_sec = int(pos_data)
    elif outer:
        opos = outer.get("position")
        if isinstance(opos, (int, float)):
            ti.position_sec = int(opos)
            # для outer (state-формат) timestamp position не приходит,
            # используем stateChangedTimestamp как лучший доступный
            changed = outer.get("stateChangedTimestamp")
            if isinstance(changed, (int, float)):
                ti.position_ts_ms = int(changed)

    # status-поля в data (push) или outer (state player{})
    status_src = data if "playing" in data else (outer or {})
    ti.playing = bool(status_src.get("playing", False))
    ti.shuffle = bool(status_src.get("shuffle", False))
    ti.repeat = status_src.get("repeatType")

    ti.explicit = bool(data.get("explicit", False))
    ti.liked = bool(data.get("like", False))
    return ti
