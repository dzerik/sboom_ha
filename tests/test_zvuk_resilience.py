"""Catalogue retry, dual-provider search and bounded cache behavior."""

import asyncio

import httpx
import pytest
import respx
from sboom_ha import zvuk_client as zvuk
from sboom_ha.zvuk_client import ZvukClient

PROFILE = zvuk.PROFILE_URL
REST = zvuk.SEARCH_URL
GQL = zvuk.GRAPHQL_URL


def rest_body(*ids):
    return {
        "result": {
            "search": {"playlists": {"items": [{"id": i, "title": f"Playlist {i}"} for i in ids]}}
        }
    }


def gql_body(*ids):
    return {
        "data": {
            "search": {
                "playlists": {
                    "items": [
                        {"id": i, "title": f"Graph {i}", "image": {"src": "https://cdn/{size}"}}
                        for i in ids
                    ]
                }
            }
        }
    }


@pytest.fixture
def routes():
    with respx.mock as router:
        router.get(PROFILE).respond(200, json={"result": {"token": "T"}})
        yield router


@pytest.mark.asyncio
async def test_rest_failure_uses_graphql_normalized_result(routes):
    routes.get(REST).respond(503)
    routes.post(GQL).respond(200, json=gql_body("2"))
    client = ZvukClient()
    try:
        result = await client.search("hello")
        assert result["playlists"][0] == {
            "id": "2",
            "type": "playlist",
            "title": "Graph 2",
            "subtitle": "Плейлист",
            "artist": None,
            "pt": "playlist",
            "explicit": False,
            "duration": None,
            "cover_url": "https://cdn/400x400",
        }
        assert set(result) == {"best", "artists", "releases", "tracks", "playlists"}
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_merge_keeps_rest_order_deduplicates_and_fills_cover(routes):
    routes.get(REST).respond(200, json=rest_body("1", "1", "2"))
    routes.post(GQL).respond(200, json=gql_body("1", "3"))
    client = ZvukClient()
    try:
        result = await client.search("hello", limit=3)
        assert [i["id"] for i in result["playlists"]] == ["1", "2", "3"]
        assert result["playlists"][0]["title"] == "Playlist 1"
        assert result["playlists"][0]["cover_url"] == "https://cdn/400x400"
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 502, 503, 504])
async def test_transient_retry_is_bounded_and_recovers(routes, status):
    rest = routes.get(REST).mock(
        side_effect=[
            httpx.Response(status),
            httpx.Response(status),
            httpx.Response(200, json=rest_body("1")),
        ]
    )
    routes.post(GQL).respond(400)
    client = ZvukClient()
    try:
        assert (await client.search("hello"))["playlists"][0]["id"] == "1"
        assert rest.call_count == 3
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_network_errors_retry_three_times_without_negative_cache(routes):
    rest = routes.get(REST).mock(side_effect=httpx.ConnectError("down"))
    gql = routes.post(GQL).mock(side_effect=httpx.ReadTimeout("down"))
    client = ZvukClient()
    try:
        assert (await client.search("hello"))["playlists"] == []
        assert (rest.call_count, gql.call_count) == (3, 3)
        rest.respond(200, json=rest_body("1"))
        gql.respond(200, json=gql_body())
        assert (await client.search("hello"))["playlists"][0]["id"] == "1"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_fresh_cache_is_copied_keyed_by_limit_and_serializes_duplicates(routes):
    rest = routes.get(REST).respond(200, json=rest_body("1", "2"))
    gql = routes.post(GQL).respond(200, json=gql_body())
    client = ZvukClient()
    try:
        first, second = await asyncio.gather(client.search("hello", 1), client.search("hello", 1))
        first["playlists"][0]["title"] = "corrupt"
        second["playlists"].clear()
        assert (await client.search("hello", 1))["playlists"][0]["title"] == "Playlist 1"
        assert (rest.call_count, gql.call_count) == (1, 1)
        assert len((await client.search("hello", 2))["playlists"]) == 2
        assert rest.call_count == 2
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stale_only_for_transient_errors_and_until_15_minutes(routes, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(zvuk, "monotonic", lambda: now[0], raising=False)
    rest = routes.get(REST).respond(200, json=rest_body("1"))
    gql = routes.post(GQL).respond(200, json=gql_body())
    client = ZvukClient()
    try:
        await client.search("hello")
        now[0] = 61
        rest.respond(503)
        gql.respond(429)
        assert (await client.search("hello"))["playlists"][0]["id"] == "1"
        rest.respond(400)
        gql.respond(400)
        before = (rest.call_count, gql.call_count)
        assert (await client.search("hello"))["playlists"] == []
        assert (rest.call_count, gql.call_count) == (before[0] + 1, before[1] + 1)
        rest.respond(503)
        gql.respond(503)
        now[0] = 901
        assert (await client.search("hello"))["playlists"] == []
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_lru_evicts_oldest_but_reads_refresh_recency(routes):
    rest = routes.get(REST).respond(200, json=rest_body("1"))
    routes.post(GQL).respond(200, json=gql_body())
    client = ZvukClient()
    try:
        for i in range(64):
            await client.search(f"q{i}")
        await client.search("q0")
        await client.search("q64")
        await client.search("q0")
        assert rest.call_count == 65
        await client.search("q1")
        assert rest.call_count == 66
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_cancel_propagates_releases_lock_and_cancels_requests(routes):
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def blocked(request):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    rest = routes.get(REST).mock(side_effect=blocked)
    routes.post(GQL).respond(200, json=gql_body())
    client = ZvukClient()
    try:
        task = asyncio.create_task(client.search("hello"))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stopped.is_set()
        rest.respond(200, json=rest_body("1"))
        assert (await client.search("hello"))["playlists"][0]["id"] == "1"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_total_search_timeout_is_bounded(routes, monkeypatch):
    monkeypatch.setattr(zvuk, "_SEARCH_TIMEOUT", 0.02, raising=False)

    async def blocked(request):
        await asyncio.Event().wait()

    routes.get(REST).mock(side_effect=blocked)
    routes.post(GQL).respond(200, json=gql_body())
    client = ZvukClient()
    try:
        result = await asyncio.wait_for(client.search("hello"), timeout=0.2)
        assert result["playlists"] == []
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_graphql_track_cover_and_artist_survive_missing_enrichment(routes):
    routes.get(REST).respond(503)
    routes.post(GQL).respond(
        200,
        json={
            "data": {
                "search": {
                    "tracks": {
                        "items": [
                            {
                                "id": "7",
                                "title": "Track",
                                "artists": [{"id": "8", "title": "Artist"}],
                                "duration": 123,
                                "explicit": True,
                                "release": {
                                    "id": "9",
                                    "title": "Album",
                                    "image": {"src": "https://cdn/{size}"},
                                },
                            }
                        ]
                    }
                }
            }
        },
    )
    client = ZvukClient()
    try:
        track = (await client.search("hello"))["tracks"][0]
        assert track["artist"] == "Artist"
        assert track["subtitle"] == "Artist"
        assert track["cover_url"] == "https://cdn/400x400"
        assert track["duration"] == 123
        assert track["explicit"] is True
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_repeated_401_refreshes_only_once(routes):
    profile = routes.get(PROFILE).respond(200, json={"result": {"token": "T"}})
    rest = routes.get(REST).respond(401)
    routes.post(GQL).respond(200, json=gql_body("1"))
    client = ZvukClient()
    try:
        assert (await client.search("hello"))["playlists"][0]["id"] == "1"
        assert rest.call_count == 2
        assert profile.call_count == 2
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_malformed_provider_response_does_not_poison_success_or_cache(routes):
    rest = routes.get(REST).respond(200, json=[])
    gql = routes.post(GQL).respond(200, json={"errors": [{"message": "invalid"}]})
    client = ZvukClient()
    try:
        assert (await client.search("hello"))["playlists"] == []
        rest.respond(200, json=rest_body("1"))
        gql.respond(200, json=gql_body())
        assert (await client.search("hello"))["playlists"][0]["id"] == "1"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_request_deadline_keeps_drilldown_error_contract(routes, monkeypatch):
    monkeypatch.setattr(zvuk, "_REQUEST_TIMEOUT", 0.02)

    async def blocked(request):
        await asyncio.Event().wait()

    routes.post(GQL).mock(side_effect=blocked)
    client = ZvukClient()
    try:
        assert await asyncio.wait_for(client.get_release("1"), timeout=0.2) is None
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [[], {"result": []}, {"result": {"token": 123}}])
async def test_invalid_profile_is_an_uncached_failure(routes, body):
    profile = routes.get(PROFILE).respond(200, json=body)
    # Neither search endpoint can be reached without a valid token.
    routes.get(REST).respond(200, json=rest_body("1"))
    routes.post(GQL).respond(200, json=gql_body())
    client = ZvukClient()
    try:
        assert (await client.search("hello"))["playlists"] == []
        profile.respond(200, json={"result": {"token": "T"}})
        assert (await client.search("hello"))["playlists"][0]["id"] == "1"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_optional_enrichment_timeout_retains_and_caches_search_success(routes, monkeypatch):
    monkeypatch.setattr(zvuk, "_SEARCH_TIMEOUT", 0.02)
    rest = routes.get(REST).respond(200, json={"result": {"search": {
        "tracks": {"items": [{"id": "1", "title": "Track", "aname": "Artist"}]}
    }}})
    async def graphql(request):
        if b'"ids"' in request.content:
            await asyncio.Event().wait()
        return httpx.Response(200, json=gql_body())
    routes.post(GQL).mock(side_effect=graphql)
    client = ZvukClient()
    try:
        result = await asyncio.wait_for(client.search("hello"), timeout=0.2)
        assert result["tracks"][0]["title"] == "Track"
        assert (await client.search("hello"))["tracks"][0]["id"] == "1"
        assert rest.call_count == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("hanging_source", ["rest", "graphql"])
async def test_completed_source_survives_other_provider_deadline(routes, monkeypatch, hanging_source):
    monkeypatch.setattr(zvuk, "_SEARCH_TIMEOUT", 0.02)
    stopped = asyncio.Event()

    async def blocked(request):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    rest = routes.get(REST)
    gql = routes.post(GQL)
    if hanging_source == "rest":
        rest.mock(side_effect=blocked)
        gql.respond(200, json=gql_body("2"))
    else:
        rest.respond(200, json=rest_body("1"))
        gql.mock(side_effect=blocked)
    client = ZvukClient()
    try:
        result = await asyncio.wait_for(client.search("hello"), timeout=0.2)
        assert [item["id"] for item in result["playlists"]] == [
            "2" if hanging_source == "rest" else "1"
        ]
        assert stopped.is_set()
        counts = rest.call_count, gql.call_count
        assert await client.search("hello") == result
        assert (rest.call_count, gql.call_count) == counts
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_unrelated_query_can_finish_while_another_query_is_blocked(routes):
    started = asyncio.Event()
    slow_query = "slow"
    # Fixed striping allows collisions; select two different stripes for this contract.
    fast_query = next(
        f"fast{i}" for i in range(100)
        if hash((f"fast{i}", 8)) % 16 != hash((slow_query, 8)) % 16
    )

    async def rest(request):
        if request.url.params["query"] == slow_query:
            started.set()
            await asyncio.Event().wait()
        return httpx.Response(200, json=rest_body("1"))

    routes.get(REST).mock(side_effect=rest)
    routes.post(GQL).respond(200, json=gql_body())
    client = ZvukClient()
    slow = asyncio.create_task(client.search(slow_query))
    try:
        await started.wait()
        result = await asyncio.wait_for(client.search(fast_query), timeout=0.2)
        assert result["playlists"][0]["id"] == "1"
        assert not slow.done()
    finally:
        slow.cancel()
        with pytest.raises(asyncio.CancelledError):
            await slow
        await client.aclose()


@pytest.mark.asyncio
async def test_permanent_failure_plus_other_provider_deadline_does_not_use_stale(routes, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(zvuk, "monotonic", lambda: now[0])
    rest = routes.get(REST).respond(200, json=rest_body("1"))
    gql = routes.post(GQL).respond(200, json=gql_body())
    client = ZvukClient()
    try:
        await client.search("hello")
        now[0] = 61
        monkeypatch.setattr(zvuk, "_SEARCH_TIMEOUT", 0.02)
        rest.respond(400)

        async def blocked(request):
            await asyncio.Event().wait()

        gql.mock(side_effect=blocked)
        assert (await asyncio.wait_for(client.search("hello"), 0.2))["playlists"] == []
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_fresh_cached_result_does_not_wait_for_busy_colliding_stripe(routes):
    rest = routes.get(REST).respond(200, json=rest_body("1"))
    routes.post(GQL).respond(200, json=gql_body())
    client = ZvukClient()
    try:
        await client.search("cached")
        stripe = client._search_locks[hash(("cached", 8)) % len(client._search_locks)]
        async with stripe:
            result = await asyncio.wait_for(client.search("cached"), timeout=0.1)
            assert result["playlists"][0]["id"] == "1"
        assert rest.call_count == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_search_preserves_public_limit_100(routes):
    rest = routes.get(REST).respond(200, json=rest_body(*range(1, 101)))
    routes.post(GQL).respond(200, json=gql_body())
    client = ZvukClient()
    try:
        result = await client.search("hello", limit=100)
        assert len(result["playlists"]) == 100
        assert rest.calls[0].request.url.params["limit"] == "100"
    finally:
        await client.aclose()
