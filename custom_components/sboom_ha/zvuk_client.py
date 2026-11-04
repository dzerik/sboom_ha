"""Асинхронный клиент Sber Звук (zvuk.com) на httpx.

Публичный анонимный API веб-плеера Звука. Токен добывается лениво через
`/api/tiny/profile` (анонимный, привязан к cookie), затем передаётся в
GraphQL-запросы заголовком `X-Auth-Token`. Anti-bot защита (307-редирект +
cookie `spid`) обходится общим cookie-jar внутри `httpx.AsyncClient` и
браузерным User-Agent.

Клиент standalone — НЕ импортирует Home Assistant. Используется для обогащения
now-playing метаданными и для сборки staros:// deeplink'ов из zvuk.com-ссылок.
"""
from __future__ import annotations

import asyncio
import colorsys
import io
import logging
import re
from collections import OrderedDict
from copy import deepcopy
from time import monotonic
from typing import Any
from urllib.parse import urlsplit

import httpx

_LOGGER = logging.getLogger(__name__)

PROFILE_URL = "https://zvuk.com/api/tiny/profile"
GRAPHQL_URL = "https://zvuk.com/api/v1/graphql"
SEARCH_URL = "https://zvuk.com/api/tiny/search"

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_TIMEOUT = 10.0
_COVER_SIZE = "400x400"
_REQUEST_TIMEOUT = 12.0
_SEARCH_TIMEOUT = 15.0
_SEARCH_FRESH = 60.0
_SEARCH_STALE = 15 * 60.0
_SEARCH_CACHE_MAX = 64
_TRANSIENT_STATUS = {429, 502, 503, 504}

_SEARCH_QUERY = """
query search($query: String, $limit: Int = 8) {
  search(query: $query) {
    tracks(limit: $limit) {
      items { id title duration explicit artists { id title }
        release { id title image { src } } }
    }
    playlists(limit: $limit) { items { id title image { src } } }
    artists(limit: $limit) { items { id title image { src } } }
    releases(limit: $limit) {
      items { id title image { src } artists { id title } }
    }
  }
}
"""

# getTracks — ПРОВЕРЕНО вживую.
_GET_TRACKS_QUERY = (
    "query getTracks($ids:[ID!]!){getTracks(ids:$ids){"
    "id title duration explicit hasFlac lyrics "
    "artists{id title} release{id title image{src}}}}"
)
# Обогащение обложками результатов поиска — ПРОВЕРЕНО вживую.
_GET_ARTISTS_QUERY = (
    "query getArtists($ids:[ID!]!){getArtists(ids:$ids){id image{src}}}"
)
_GET_RELEASES_QUERY = (
    "query getReleases($ids:[ID!]!){getReleases(ids:$ids){id image{src}}}"
)
# Drill-down: детали артиста (релизы + топ-треки) и релиза (треклист).
# ПРОВЕРЕНО вживую.
_GET_ARTIST_QUERY = (
    "query getArtist($ids:[ID!]!){getArtists(ids:$ids){"
    "id title image{src} "
    "releases{id title date type image{src}} "
    "popularTracks(limit:20){id title duration explicit "
    "artists{title} release{image{src}}}}}"
)
_GET_RELEASE_QUERY = (
    "query getRelease($ids:[ID!]!){getReleases(ids:$ids){"
    "id title date type image{src} artists{id title} "
    "tracks{id title duration explicit hasFlac artists{title}}}}"
)

# Категории поиска: doc_type Звука → (ключ ответа, pt для deeplink).
# release == альбом. Порядок задаёт секции в панели.
_SEARCH_CATEGORIES: tuple[tuple[str, str, str], ...] = (
    ("artist", "artists", "artist"),
    ("release", "releases", "release"),
    ("track", "tracks", "track"),
    ("playlist", "playlists", "playlist"),
)

# zvuk.com/{kind}/<id> → (pt, id_param). id_param: 'tid' для треков/подкастов,
# 'pid' для сущностей-контейнеров (артист/релиз/плейлист).
_URL_KIND_MAP: dict[str, tuple[str, str]] = {
    "track": ("track", "tid"),
    "artist": ("artist", "pid"),
    "release": ("release", "pid"),  # TODO: pt=release для альбома не подтверждён
    "playlist": ("playlist", "pid"),
    "abook": ("podcast", "tid"),
    # kind сервиса play_music (в URL zvuk.com такого сегмента нет).
    "podcast": ("podcast", "tid"),
}
_URL_RE = re.compile(
    r"zvuk\.com/(track|artist|release|playlist|abook)/(\d+)", re.IGNORECASE
)
# pt (playlist type), которые колонка принимает в staros://music deeplink.
# tid — конкретный трек/подкаст-выпуск, pid — коллекция.
_VALID_PT = frozenset({"track", "podcast", "artist", "release", "playlist"})
_PT_TID = frozenset({"track", "podcast"})

# URL для dominant_cover_color приходит из фронтенда панели — серверный GET
# по нему делать можно только на известные CDN обложек (иначе SSRF в LAN).
# Суффиксы покрывают cdn-image.zvuk.com, *.mzstatic.com (iTunes, см.
# cover_client) и *.dzcdn.net (Deezer).
_ALLOWED_COVER_HOST_SUFFIXES = (".zvuk.com", ".mzstatic.com", ".dzcdn.net")
_MAX_COVER_BYTES = 5 * 1024 * 1024
_COLOR_CACHE_MAX = 256


def _is_allowed_cover_url(url: str) -> bool:
    """https + хост из allowlist CDN обложек. Всё остальное — отказ."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        return False
    return any(host.endswith(suffix) for suffix in _ALLOWED_COVER_HOST_SUFFIXES)


def _dominant_color_from_bytes(data: bytes) -> str | None:
    """Доминирующий «живой» цвет картинки → hex. Выполняется в executor.

    Взвешенное по насыщенности среднее (тянем к цветному, а не к серому),
    затем поднимаем насыщенность и держим светлоту в приятном для glow
    диапазоне. Pillow импортируется лениво — модуль остаётся импортируемым
    без Pillow (нужен только для этого пути).
    """
    from PIL import Image  # ленивый импорт: Pillow нужен только здесь

    im = Image.open(io.BytesIO(data)).convert("RGB")
    im.thumbnail((48, 48))
    px = im.load()
    w, h = im.size
    r = g = b = wsum = 0.0
    for y in range(h):
        for x in range(w):
            rr, gg, bb = px[x, y]
            mx, mn = max(rr, gg, bb), min(rr, gg, bb)
            sat = 0.0 if mx == 0 else (mx - mn) / mx
            weight = 0.15 + sat * sat  # приоритет насыщенным пикселям
            r += rr * weight
            g += gg * weight
            b += bb * weight
            wsum += weight
    if wsum == 0:
        return None
    r, g, b = r / wsum, g / wsum, b / wsum
    hh, ll, ss = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    ss = min(1.0, ss * 1.35 + 0.1)
    ll = min(0.68, max(0.45, ll))
    rr, gg, bb = colorsys.hls_to_rgb(hh, ll, ss)
    return f"#{round(rr * 255):02x}{round(gg * 255):02x}{round(bb * 255):02x}"


class ZvukClient:
    """Клиент публичного API Sber Звук (анонимный токен + GraphQL)."""

    def __init__(self) -> None:
        """Создать клиент. Сетевые ресурсы поднимаются лениво."""
        self._token: str | None = None
        self._client: httpx.AsyncClient | None = None
        self._color_cache: dict[str, str | None] = {}
        self._token_lock = asyncio.Lock()
        # Fixed stripes bound bookkeeping without serializing unrelated queries.
        self._search_locks = tuple(asyncio.Lock() for _ in range(16))
        self._search_cache: OrderedDict[tuple[str, int], tuple[float, dict[str, Any]]] = (
            OrderedDict()
        )

    def _http(self) -> httpx.AsyncClient:
        """Лениво создать общий httpx-клиент с cookie-jar и браузерным UA."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                follow_redirects=True,
                timeout=_TIMEOUT,
                headers={"User-Agent": _BROWSER_UA},
            )
        return self._client

    async def aclose(self) -> None:
        """Закрыть httpx-клиент (освободить соединения)."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get_token(self, *, force: bool = False) -> str:
        """Вернуть анонимный токен; сериализовать получение общего cookie/token."""
        if self._token and not force:
            return self._token
        async with self._token_lock:
            if self._token and not force:
                return self._token
            resp = await self._request("GET", PROFILE_URL)
            data = resp.json()
            result = data.get("result") if isinstance(data, dict) else None
            token = result.get("token") if isinstance(result, dict) else None
            if not isinstance(token, str) or not token:
                raise ValueError("Звук: токен не найден в /profile")
            self._token = token
            return token

    @staticmethod
    def _transient(exc: BaseException) -> bool:
        """Only explicitly recoverable failures may use stale search results."""
        return isinstance(exc, (httpx.RequestError, TimeoutError)) or (
            isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in _TRANSIENT_STATUS
        )

    async def _request(
        self, method: str, url: str, *, auth: bool = False, **kwargs: Any
    ) -> httpx.Response:
        """Read-only catalogue request: at most three attempts, bounded wall time."""
        try:
            async with asyncio.timeout(_REQUEST_TIMEOUT):
                token = await self.get_token() if auth else None
                refreshed = False
                for attempt in range(3):
                    try:
                        headers = {"X-Auth-Token": token} if token else {}
                        resp = await self._http().request(method, url, headers=headers, **kwargs)
                        if resp.status_code == 401 and auth and not refreshed:
                            refreshed = True
                            # Another concurrent request may already have refreshed it.
                            if token == self._token:
                                token = await self.get_token(force=True)
                            else:
                                token = self._token
                            if attempt < 2:
                                continue
                        resp.raise_for_status()
                        return resp
                    except httpx.HTTPError as exc:
                        if not self._transient(exc) or attempt == 2:
                            raise
                    await asyncio.sleep(0.1 * 2**attempt)
        except TimeoutError as exc:
            # Preserve the existing public httpx error contract for drill-down.
            raise httpx.ReadTimeout("Звук: превышено время запроса") from exc
        raise RuntimeError("Звук: исчерпаны попытки запроса")

    async def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        """Выполнить read-only GraphQL с bounded retry и одним refresh401."""
        resp = await self._request(
            "POST",
            GRAPHQL_URL,
            auth=True,
            json={"query": query, "variables": variables},
        )
        body = resp.json()
        if not isinstance(body, dict):
            raise ValueError("Звук: неверный GraphQL ответ")
        if body.get("errors"):
            _LOGGER.debug("Звук GraphQL errors: %s", body["errors"])
            if not body.get("data"):
                raise ValueError("Звук: ошибка GraphQL")
        data = body.get("data") or {}
        if not isinstance(data, dict):
            raise ValueError("Звук: неверный GraphQL data")
        return data

    async def get_tracks(self, ids: list[str]) -> list[dict[str, Any]]:
        """Метаданные треков по каталожным id. Вернуть нормализованные dict'ы."""
        if not ids:
            return []
        data = await self._graphql(_GET_TRACKS_QUERY, {"ids": [str(i) for i in ids]})
        raw = data.get("getTracks") or []
        return [self._normalize_track(t) for t in raw if isinstance(t, dict)]

    @staticmethod
    def _normalize_track(t: dict[str, Any]) -> dict[str, Any]:
        """Привести getTracks-элемент к плоскому dict со стабильными ключами."""
        release = t.get("release") or {}
        image = release.get("image") or {}
        cover_src = image.get("src")
        cover_url = (
            cover_src.replace("{size}", _COVER_SIZE)
            if isinstance(cover_src, str) and cover_src
            else None
        )
        artists = [
            {"id": a.get("id"), "title": a.get("title")}
            for a in (t.get("artists") or [])
            if isinstance(a, dict)
        ]
        lyrics = t.get("lyrics")
        return {
            "id": t.get("id"),
            "title": t.get("title"),
            "artists": artists,
            "album": {"id": release.get("id"), "title": release.get("title")},
            "cover_url": cover_url,
            "duration": t.get("duration"),
            "explicit": bool(t.get("explicit")),
            "has_flac": bool(t.get("hasFlac")),
            "has_lyrics": bool(lyrics),
        }

    @staticmethod
    def _year(date_str: Any) -> int | None:
        """ISO-дата → год (int) или None."""
        if isinstance(date_str, str) and len(date_str) >= 4 and date_str[:4].isdigit():
            return int(date_str[:4])
        return None

    def _cover(self, src: Any) -> str | None:
        """image.src (с плейсхолдером {size}) → готовый URL обложки."""
        return (
            src.replace("{size}", _COVER_SIZE)
            if isinstance(src, str) and src
            else None
        )

    def _flat_track(self, t: dict[str, Any], cover: str | None) -> dict[str, Any]:
        """Элемент трека (popularTracks/release.tracks) → плоский dict панели."""
        rel = t.get("release") or {}
        rel_cover = cover if cover is not None else self._cover(
            (rel.get("image") or {}).get("src")
        )
        return {
            "id": str(t.get("id")),
            "type": "track",
            "title": t.get("title") or "",
            "artists": [
                a.get("title")
                for a in (t.get("artists") or [])
                if isinstance(a, dict) and a.get("title")
            ],
            "duration": t.get("duration"),
            "explicit": bool(t.get("explicit")),
            "has_flac": bool(t.get("hasFlac")),
            "cover_url": rel_cover,
            "pt": "track",
        }

    async def get_artist(self, artist_id: str) -> dict[str, Any] | None:
        """Детали артиста для drill-down: релизы (новые сверху) + топ-треки."""
        if not artist_id:
            return None
        try:
            data = await self._graphql(_GET_ARTIST_QUERY, {"ids": [str(artist_id)]})
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            _LOGGER.debug("Звук get_artist(%s) не удался: %s", artist_id, exc)
            return None
        rows = data.get("getArtists") or []
        if not rows or not isinstance(rows[0], dict):
            return None
        a = rows[0]
        releases = []
        for r in a.get("releases") or []:
            if not isinstance(r, dict) or not r.get("id"):
                continue
            year = self._year(r.get("date"))
            rtype = r.get("type") or "album"
            label = "Сингл" if rtype == "single" else "Альбом"
            releases.append({
                "id": str(r["id"]),
                "type": "release",
                "title": r.get("title") or "",
                "subtitle": f"{label} · {year}" if year else label,
                "year": year,
                "release_type": rtype,
                "cover_url": self._cover((r.get("image") or {}).get("src")),
                "pt": "release",
            })
        tracks = [
            self._flat_track(t, None)
            for t in (a.get("popularTracks") or [])
            if isinstance(t, dict) and t.get("id")
        ]
        return {
            "id": str(a.get("id")),
            "type": "artist",
            "title": a.get("title") or "",
            "cover_url": self._cover((a.get("image") or {}).get("src")),
            "releases": releases,
            "tracks": tracks,
        }

    async def get_release(self, release_id: str) -> dict[str, Any] | None:
        """Детали релиза для drill-down: шапка + полный треклист."""
        if not release_id:
            return None
        try:
            data = await self._graphql(
                _GET_RELEASE_QUERY, {"ids": [str(release_id)]}
            )
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            _LOGGER.debug("Звук get_release(%s) не удался: %s", release_id, exc)
            return None
        rows = data.get("getReleases") or []
        if not rows or not isinstance(rows[0], dict):
            return None
        r = rows[0]
        cover = self._cover((r.get("image") or {}).get("src"))
        artists = [
            {"id": str(a.get("id")), "title": a.get("title") or ""}
            for a in (r.get("artists") or [])
            if isinstance(a, dict)
        ]
        tracks = [
            self._flat_track(t, cover)
            for t in (r.get("tracks") or [])
            if isinstance(t, dict) and t.get("id")
        ]
        year = self._year(r.get("date"))
        return {
            "id": str(r.get("id")),
            "type": "release",
            "title": r.get("title") or "",
            "year": year,
            "release_type": r.get("type") or "album",
            "cover_url": cover,
            "artists": artists,
            "artist": ", ".join(a["title"] for a in artists if a["title"]),
            "tracks": tracks,
        }

    async def dominant_cover_color(self, url: str) -> str | None:
        """Доминирующий цвет обложки (hex) для ambient-glow панели.

        Считается на сервере (Pillow), т.к. CDN Звука не отдаёт CORS-заголовки
        и клиентский canvas-разбор невозможен. Кешируется по URL. Тяжёлый
        Pillow-разбор уходит в executor, чтобы не блокировать event loop.
        None — если картинку не удалось получить/разобрать.

        URL приходит из фронтенда: скачиваются только https-обложки с
        известных CDN (allowlist), тело ограничено, сетевые сбои не кэшируются.
        """
        if not url or not _is_allowed_cover_url(url):
            return None
        if url in self._color_cache:
            return self._color_cache[url]
        try:
            data = await self._fetch_cover_limited(url)
        except httpx.HTTPError as exc:
            # Сетевая ошибка ≠ not_found: не кэшируем, retry при следующем вызове.
            _LOGGER.debug("Звук: не удалось скачать обложку %s: %s", url, exc)
            return None
        color: str | None = None
        if data is not None:
            try:
                color = await asyncio.get_running_loop().run_in_executor(
                    None, _dominant_color_from_bytes, data
                )
            except (OSError, ValueError) as exc:
                _LOGGER.debug("Звук: не удалось разобрать обложку %s: %s", url, exc)
        if len(self._color_cache) >= _COLOR_CACHE_MAX:
            self._color_cache.pop(next(iter(self._color_cache)))
        self._color_cache[url] = color
        return color

    async def _fetch_cover_limited(self, url: str) -> bytes | None:
        """Скачать обложку потоково с лимитом размера.

        None — редирект увёл за allowlist или тело больше `_MAX_COVER_BYTES`.
        """
        async with self._http().stream("GET", url) as resp:
            resp.raise_for_status()
            if not _is_allowed_cover_url(str(resp.url)):
                return None
            buf = bytearray()
            async for chunk in resp.aiter_bytes():
                buf += chunk
                if len(buf) > _MAX_COVER_BYTES:
                    return None
            return bytes(buf)

    async def search(self, query: str, limit: int = 8) -> dict[str, Any]:
        """REST + GraphQL search with copied 60s fresh / 15min transient stale LRU.

        The public shape stays {best, artists, releases, tracks, playlists}.
        One total deadline includes lock wait, token, retries and cover enrichment.
        """
        query = query.strip()
        if not query:
            return self._empty_search()
        limit = max(1, min(int(limit), 100))
        key = (query, limit)
        # No await: a fresh hit need not wait for an unrelated colliding stripe.
        cached = self._search_cache.get(key)
        if cached and monotonic() - cached[0] < _SEARCH_FRESH:
            self._search_cache.move_to_end(key)
            return deepcopy(cached[1])
        out: dict[str, Any] | None = None
        source_tasks: list[asyncio.Task] = []
        try:
            async with asyncio.timeout(_SEARCH_TIMEOUT):
                async with self._search_locks[hash(key) % len(self._search_locks)]:
                    cached = self._search_cache.get(key)
                    if cached and monotonic() - cached[0] < _SEARCH_FRESH:
                        self._search_cache.move_to_end(key)
                        return deepcopy(cached[1])
                    async with asyncio.TaskGroup() as group:
                        rest = group.create_task(self._search_source(query, limit, False))
                        gql = group.create_task(self._search_source(query, limit, True))
                        source_tasks = [rest, gql]
                    sources = [rest.result(), gql.result()]
                    results = [result for result, _ in sources if result is not None]
                    if not results:
                        if all(self._transient(exc) for _, exc in sources):
                            return self._stale_search(key)
                        return self._empty_search()
                    out = self._merge_search(results, limit)
                    await self._enrich_search_covers(out)
                    self._cache_search(key, out)
                    return out
        except TimeoutError:
            # TaskGroup has cancelled and joined unfinished providers. A completed
            # provider remains useful even when its sibling exceeded the deadline.
            completed = [task.result() for task in source_tasks if not task.cancelled()]
            results = [result for result, _ in completed if result is not None]
            if out is None and results:
                out = self._merge_search(results, limit)
            if out is not None:
                # Enrichment and the other provider cannot erase already found music.
                self._cache_search(key, out)
                return out
            if any(not self._transient(exc) for _, exc in completed):
                return self._empty_search()
            return self._stale_search(key)

    def _cache_search(self, key: tuple[str, int], out: dict[str, Any]) -> None:
        """Atomic, non-awaiting LRU update; store a private copy of a success."""
        self._search_cache[key] = (monotonic(), deepcopy(out))
        self._search_cache.move_to_end(key)
        while len(self._search_cache) > _SEARCH_CACHE_MAX:
            self._search_cache.popitem(last=False)

    def _stale_search(self, key: tuple[str, int]) -> dict[str, Any]:
        """Return a copy only while the last success is within the stale window."""
        cached = self._search_cache.get(key)
        if cached and monotonic() - cached[0] < _SEARCH_STALE:
            self._search_cache.move_to_end(key)
            return deepcopy(cached[1])
        return self._empty_search()

    async def _search_source(
        self, query: str, limit: int, graphql: bool
    ) -> tuple[dict[str, Any] | None, BaseException | None]:
        """Keep expected provider failures distinct from valid empty results."""
        try:
            if graphql:
                data = await self._graphql(_SEARCH_QUERY, {"query": query, "limit": limit})
                raw = data.get("search")
            else:
                resp = await self._request(
                    "GET",
                    SEARCH_URL,
                    auth=True,
                    params={
                        "query": query,
                        "type": ",".join(cat[0] for cat in _SEARCH_CATEGORIES),
                        "limit": limit,
                    },
                )
                body = resp.json()
                if not isinstance(body, dict):
                    raise ValueError("Звук: неверный REST ответ")
                result = body.get("result")
                raw = result.get("search") if isinstance(result, dict) else None
            if not isinstance(raw, dict):
                raise ValueError("Звук: отсутствует search")
            return self._normalize_search(raw, limit, graphql), None
        except (httpx.HTTPError, ValueError, TypeError, TimeoutError) as exc:
            _LOGGER.debug(
                "Звук search %s не удался для %r: %s", "GraphQL" if graphql else "REST", query, exc
            )
            return None, exc

    def _normalize_search(self, raw: dict[str, Any], limit: int, graphql: bool) -> dict[str, Any]:
        """Normalize the two catalogue schemas into existing panel items."""
        out = self._empty_search()
        best = raw.get("best_item")
        kinds = {cat[0] for cat in _SEARCH_CATEGORIES}
        if (
            isinstance(best, dict)
            and best.get("doc_type") in kinds
            and isinstance(best.get("id"), (str, int))
            and best["id"]
        ):
            out["best"] = {"type": best["doc_type"], "id": str(best["id"])}
        for kind, key, pt in _SEARCH_CATEGORIES:
            section = raw.get(key)
            items = section.get("items") if isinstance(section, dict) else []
            if not isinstance(items, list):
                continue
            seen: set[str] = set()
            for row in items:
                if not isinstance(row, dict):
                    continue
                ident = row.get("id")
                if not isinstance(ident, (str, int)) or isinstance(ident, bool) or not ident:
                    continue
                ident = str(ident)
                if ident in seen:
                    continue
                seen.add(ident)
                normalized = dict(row)
                normalized["title"] = row.get("title") if isinstance(row.get("title"), str) else ""
                if graphql:
                    artists = row.get("artists")
                    normalized["aname"] = (
                        ", ".join(
                            artist["title"]
                            for artist in artists
                            if isinstance(artist, dict) and isinstance(artist.get("title"), str)
                        )
                        if isinstance(artists, list)
                        else None
                    )
                elif not isinstance(row.get("aname"), str):
                    normalized["aname"] = None
                item = self._search_item(normalized, kind, pt)
                if graphql:
                    source = row.get("release") if kind == "track" else row
                    image = source.get("image") if isinstance(source, dict) else None
                    item["cover_url"] = (
                        self._cover(image.get("src")) if isinstance(image, dict) else None
                    )
                out[key].append(item)
                if len(out[key]) >= limit:
                    break
        return out

    def _merge_search(self, results: list[dict[str, Any]], limit: int) -> dict[str, Any]:
        """Prefer REST metadata, fill missing fields and append unique GQL items."""
        out = self._empty_search()
        for result in results:
            if out["best"] is None:
                out["best"] = result["best"]
            for _, key, _ in _SEARCH_CATEGORIES:
                by_id = {item["id"]: item for item in out[key]}
                for item in result[key]:
                    previous = by_id.get(item["id"])
                    if previous is not None:
                        for field in ("cover_url", "subtitle", "artist", "duration", "title"):
                            if not previous.get(field) and item.get(field):
                                previous[field] = item[field]
                    elif len(out[key]) < limit:
                        copied = dict(item)
                        out[key].append(copied)
                        by_id[item["id"]] = copied
        return out

    @staticmethod
    def _empty_search() -> dict[str, Any]:
        """Пустой каркас результата поиска (все категории — списки)."""
        return {"best": None, "artists": [], "releases": [],
                "tracks": [], "playlists": []}

    @staticmethod
    def _search_item(it: dict[str, Any], doc_type: str, pt: str) -> dict[str, Any]:
        """Элемент /api/tiny/search → плоский item для панели (без обложки)."""
        aname = it.get("aname")
        if doc_type == "artist":
            subtitle = "Исполнитель"
        elif doc_type == "playlist":
            n = it.get("tracks_number")
            subtitle = f"Плейлист · {n} треков" if n else "Плейлист"
        elif doc_type == "release":
            subtitle = f"{aname} · Альбом" if aname else "Альбом"
        else:  # track
            subtitle = aname or ""
        return {
            "id": str(it["id"]),
            "type": doc_type,
            "title": it.get("title") or "",
            "subtitle": subtitle,
            "artist": aname,
            "cover_url": None,
            "pt": pt,
            "explicit": bool(it.get("explicit")),
            "duration": it.get("duration"),
        }

    async def _enrich_search_covers(self, out: dict[str, Any]) -> None:
        """Добрать обложки для artists/releases/tracks одним заходом (concurrent)."""

        async def enrich(key: str, query: str, gql_key: str) -> None:
            items = [it for it in out.get(key) or [] if not it.get("cover_url")]
            ids = [it["id"] for it in items]
            if not ids:
                return
            try:
                data = await self._graphql(query, {"ids": ids})
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                _LOGGER.debug("Звук обогащение %s не удалось: %s", key, exc)
                return
            covers: dict[str, str] = {}
            for row in data.get(gql_key) or []:
                if not isinstance(row, dict):
                    continue
                src = (row.get("image") or {}).get("src")
                if isinstance(src, str) and src:
                    covers[str(row.get("id"))] = src.replace("{size}", _COVER_SIZE)
            for it in items:
                it["cover_url"] = covers.get(it["id"])

        await asyncio.gather(
            enrich("artists", _GET_ARTISTS_QUERY, "getArtists"),
            enrich("releases", _GET_RELEASES_QUERY, "getReleases"),
            self._enrich_track_covers(out.get("tracks") or []),
        )

    async def _enrich_track_covers(self, tracks: list[dict[str, Any]]) -> None:
        """Обложки треков через getTracks (обложка = обложка релиза)."""
        tracks = [it for it in tracks if not it.get("cover_url")]
        ids = [it["id"] for it in tracks]
        if not ids:
            return
        try:
            metas = await self.get_tracks(ids)
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            _LOGGER.debug("Звук обогащение tracks не удалось: %s", exc)
            return
        covers = {str(m.get("id")): m.get("cover_url") for m in metas}
        for it in tracks:
            it["cover_url"] = covers.get(it["id"])

    async def search_first_deeplink(self, query: str) -> str | None:
        """Поиск по названию → deeplink первого результата (best → track → …).

        Для сервиса play_music с текстовым запросом. None — если пусто.
        """
        result = await self.search(query, limit=1)
        best = result.get("best")
        if isinstance(best, dict) and best.get("id"):
            for doc_type, _key, pt in _SEARCH_CATEGORIES:
                if doc_type == best["type"]:
                    return self.deeplink_for(pt, best["id"])
        for _doc_type, key, pt in _SEARCH_CATEGORIES:
            items = result.get(key) or []
            if items:
                return self.deeplink_for(pt, items[0]["id"])
        return None

    @staticmethod
    def parse_zvuk_url(
        url_or_id: str, kind: str | None = None
    ) -> tuple[str, str, str] | None:
        """Разобрать zvuk.com-ссылку ИЛИ голый id+kind.

        Возвращает `(pt, id_param, value)` для сборки deeplink, где
        `id_param` ∈ {'tid','pid'}. None — если разобрать не удалось.
        """
        text = (url_or_id or "").strip()
        match = _URL_RE.search(text)
        if match:
            url_kind = match.group(1).lower()
            value = match.group(2)
            pt, id_param = _URL_KIND_MAP[url_kind]
            return pt, id_param, value
        # Голый id + явный kind.
        if kind and text.isdigit():
            mapping = _URL_KIND_MAP.get(kind.lower())
            if mapping:
                pt, id_param = mapping
                return pt, id_param, text
        return None

    @staticmethod
    def build_deeplink(pt: str, id_param: str, value: str) -> str:
        """Собрать staros://music deeplink из (pt, id_param, value)."""
        return f"staros://music?{id_param}={value}&pt={pt}"

    @staticmethod
    def deeplink_for(pt: str | None, content_id: str | None) -> str | None:
        """Валидированная сборка deeplink из пары (pt, id).

        Единственный путь для недоверенных входов (панель, сервисы): pt только
        из whitelist, id только цифры — инъекция query-параметров невозможна.
        None — если вход невалиден.
        """
        pt_norm = (pt or "").lower()
        cid = str(content_id) if content_id is not None else ""
        if pt_norm not in _VALID_PT or not cid.isdigit():
            return None
        id_param = "tid" if pt_norm in _PT_TID else "pid"
        return ZvukClient.build_deeplink(pt_norm, id_param, cid)
