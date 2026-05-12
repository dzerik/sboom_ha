"""Pytest fixtures для тестов sboom_ha."""
from __future__ import annotations

import sys
from pathlib import Path

# Добавляем custom_components в sys.path чтобы можно было импортировать sboom_ha
# напрямую без HA-stub (часть тестов работает без HA окружения).
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "custom_components"))

# Регистрируем HA-stubs ДО любого импорта sboom_ha.* в тестах.
# Тесты, которые импортируют только api/helpers/lyrics_client/image_render —
# не используют HA и stubs им не мешают; тесты на media_player/sensor/etc. —
# нуждаются в stubs.
sys.path.insert(0, str(ROOT))
from tests._ha_stubs import install_stubs  # noqa: E402

install_stubs()


import pytest


@pytest.fixture
def real_track_money_raw() -> bytes:
    """Синтетический payload в push-формате (плоский JSON с trackId)."""
    json_part = (
        b'{"artists":[{"id":"1","name":"Test Artist"}],'
        b'"childMode":false,"duration":0,"explicit":true,"like":false,'
        b'"mediaSource":"MUSIC","playbackSpeedRate":1.0,'
        b'"playing":true,"playingPending":false,'
        b'"playlistId":"100","playlistLike":false,'
        b'"playlistTitle":"Test Playlist","playlistType":"endless",'
        b'"position":{"tsMs":1700000000000,"val":184},'
        b'"provider":"zvuk",'
        b'"releases":[{"id":"200","name":"Test Album"}],'
        b'"repeatType":"none","shuffle":false,'
        b'"stateChangedTimestamp":1699999990000,'
        b'"title":"Test Track","trackId":"1001"}'
    )
    # Эмулируем wrapping: parse_track ищет JSON по trackId, любой "хвост" игнорируется.
    return b"\x00\x00" + json_part + b"\x00\x00"


@pytest.fixture
def real_track_state_format_raw() -> bytes:
    """Синтетический payload в state-формате (info{} обертка с другими названиями полей)."""
    return (
        b'{"info":{"player":{'
        b'"artists":[{"id":"2","name":"Test Artist 2"}],'
        b'"playing":true,'
        b'"position":120,'
        b'"duration":328,'
        b'"provider":"zvuk",'
        b'"releases":[{"id":"300","title":"Test Album 2"}],'
        b'"title":"Test Track 2",'
        b'"trackId":"2002"}}}'
    )


@pytest.fixture
def lrclib_track_response() -> dict:
    """Типичный ответ Lrclib.net (публичный API) для синхронизированных lyrics."""
    return {
        "id": 1,
        "trackName": "Test Track",
        "artistName": "Test Artist",
        "albumName": "Test Album",
        "duration": 313.0,
        "instrumental": False,
        "plainLyrics": "Hey, hey, you there\nTell me how have you been?",
        "syncedLyrics": (
            "[00:11.20]Hey, hey, you there\n"
            "[00:13.50]Tell me how have you been?\n"
            "[00:18.00]You could have a steam train\n"
            "[02:15.40]I wanna be your sledgehammer"
        ),
    }


@pytest.fixture
def lrclib_instrumental_response() -> dict:
    """Lrclib для инструментальной композиции — без текста."""
    return {
        "id": 1,
        "trackName": "Some Instrumental",
        "artistName": "Composer",
        "instrumental": True,
        "plainLyrics": None,
        "syncedLyrics": None,
    }
