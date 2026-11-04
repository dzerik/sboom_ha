"""User-scoped history of accepted music launches, persisted in HA Store."""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from copy import deepcopy
from typing import Any
from urllib.parse import parse_qs, urlsplit

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)
_STORAGE_KEY = "sboom_ha.play_history"
_MAX_ITEMS = 100
_ID_PARAMS = {
    "track": "tid",
    "artist": "pid",
    "release": "pid",
    "playlist": "pid",
    "podcast": "tid",
    "abook": "tid",
}


def _identity(item: Any) -> tuple[str, str] | None:
    """Accept recognized kinds and bounded, ASCII numeric catalogue ids only."""
    if not isinstance(item, dict):
        return None
    kind, content_id = item.get("kind"), item.get("id")
    if not isinstance(kind, str) or kind not in _ID_PARAMS:
        return None
    if not isinstance(content_id, str) or not re.fullmatch(r"[0-9]{1,64}", content_id):
        return None
    return kind, content_id


def _metadata(item: dict) -> dict[str, str]:
    """Copy only display fields; never retain arbitrary launch metadata."""
    result = {}
    for key, limit in (("title", 512), ("subtitle", 512), ("cover_url", 2048)):
        value = item.get(key)
        if not isinstance(value, str):
            continue
        value = value.strip()[:limit]
        if key == "cover_url" and value:
            try:
                parsed = urlsplit(value)
                if (
                    parsed.scheme != "https"
                    or not parsed.netloc
                    or parsed.username
                    or parsed.password
                ):
                    continue
                # Covers are public; query strings may contain access tokens.
                value = parsed._replace(query="", fragment="").geturl()
            except ValueError:
                continue
        if value:
            result[key] = value
    return result


def _stored_item(item: Any) -> dict | None:
    """Validate persisted counters/times and discard unknown fields."""
    identity = _identity(item)
    if identity is None:
        return None
    count = item.get("count")
    first, last = item.get("first_played"), item.get("last_played")
    if type(count) is not int or count < 1:
        return None
    if any(
        type(value) not in (int, float) or not 0 <= value <= 1e12 or not math.isfinite(value)
        for value in (first, last)
    ):
        return None
    if first > last:
        return None
    kind, content_id = identity
    return {
        "kind": kind,
        "id": content_id,
        "title": content_id,
        "subtitle": "",
        "cover_url": "",
        **_metadata(item),
        "count": count,
        "first_played": first,
        "last_played": last,
    }


class PlayHistory:
    """Serialize initial load, all mutations and awaited writes under one lock."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store = Store(hass, 1, _STORAGE_KEY, private=True, atomic_writes=True)
        self._lock = asyncio.Lock()
        self._loaded = False
        self._users: dict[str, list[dict]] = {}

    async def _async_load(self) -> None:
        """Load while locked; a failed load remains retryable and cannot erase disk state."""
        if self._loaded:
            return
        data = await self._store.async_load()
        users = data.get("users") if isinstance(data, dict) else None
        if isinstance(users, dict):
            for user_id, rows in users.items():
                if not isinstance(user_id, str) or not isinstance(rows, list):
                    continue
                unique = {}
                for row in rows:
                    if (item := _stored_item(row)) is not None:
                        key = (item["kind"], item["id"])
                        if key not in unique or item["last_played"] > unique[key]["last_played"]:
                            unique[key] = item
                self._users[user_id] = sorted(
                    unique.values(), key=lambda item: item["last_played"], reverse=True
                )[:_MAX_ITEMS]
        self._loaded = True

    async def async_list(self, user_id: str, order: str = "recent") -> list[dict]:
        """Return an independent snapshot for this user only."""
        if order not in ("recent", "frequent"):
            raise ValueError("unknown history order")
        async with self._lock:
            await self._async_load()
            rows = self._users.get(user_id or "system", [])
            key = (
                (lambda item: (item["count"], item["last_played"]))
                if order == "frequent"
                else (lambda item: item["last_played"])
            )
            return deepcopy(sorted(rows, key=key, reverse=True))

    async def async_record(self, user_id: str, item: dict) -> None:
        """Record one accepted launch, preserving known metadata on bare replays."""
        identity = _identity(item)
        if identity is None:
            raise ValueError("invalid history item")
        kind, content_id = identity
        async with self._lock:
            await self._async_load()
            users = deepcopy(self._users)
            rows = users.setdefault(user_id or "system", [])
            previous = next((row for row in rows if (row["kind"], row["id"]) == identity), None)
            now = time.time()
            record = {
                "kind": kind,
                "id": content_id,
                "title": content_id,
                "subtitle": "",
                "cover_url": "",
                "count": 0,
                "first_played": now,
                "last_played": now,
            }
            if previous is not None:
                record.update(previous)
                rows.remove(previous)
            record.update(_metadata(item))
            record["count"] += 1
            record["last_played"] = max(now, record["first_played"], record["last_played"])
            rows.insert(0, record)
            del rows[_MAX_ITEMS:]
            await self._store.async_save({"users": users})
            self._users = users

    async def async_clear(self, user_id: str) -> None:
        """Clear only the caller's scope and await persistence before returning."""
        async with self._lock:
            await self._async_load()
            users = deepcopy(self._users)
            users.pop(user_id or "system", None)
            await self._store.async_save({"users": users})
            self._users = users


async def async_get_play_history(hass: HomeAssistant) -> PlayHistory:
    """Publish the singleton before awaiting the serialized initial load."""
    history = hass.data.get(_STORAGE_KEY)
    if history is None:
        history = hass.data[_STORAGE_KEY] = PlayHistory(hass)
    async with history._lock:
        await history._async_load()
    return history


async def async_record_play(
    hass: HomeAssistant, user_id: str | None, deeplink: str, metadata: dict | None = None
) -> None:
    """Best effort bookkeeping after acceptance; cancellation still propagates."""
    try:
        if not isinstance(deeplink, str) or len(deeplink) > 4096:
            return
        parsed = urlsplit(deeplink)
        if parsed.scheme != "staros" or parsed.netloc != "music" or parsed.path not in ("", "/"):
            return
        query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=20)
        if len(query.get("pt", [])) != 1:
            return
        kind = query["pt"][0]
        param = _ID_PARAMS.get(kind)
        if param is None or len(query.get(param, [])) != 1:
            return
        item = {"kind": kind, "id": query[param][0]}
        if _identity(item) is None:
            return
        if isinstance(metadata, dict):
            item.update(_metadata(metadata))
        history = await async_get_play_history(hass)
        await history.async_record(user_id or "system", item)
    except Exception:  # Persistence must never turn accepted playback into a failure.
        _LOGGER.warning("Unable to persist playback history")
