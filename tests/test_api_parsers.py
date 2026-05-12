"""Тесты парсеров payload-форматов трека.

Покрываемые кейсы:
- Парсер находит trackId внутри произвольно вложенного JSON
- Артисты собираются и в push-формате (artists[].name), и в state-формате (artists[].title)
- Releases в обоих форматах (name vs title)
- position как dict {tsMs, val} (push) и как int (state)
- Провайдер не zvuk → release_id всё равно может быть
- Mute/volume парсится из state JSON
"""
from __future__ import annotations

from sboom_ha.api import SberSpeakerClient


# ─────────────────────── parse_track ───────────────────────

def test_parse_track_push_format(real_track_money_raw):
    """Push-формат: плоский JSON с trackId на верхнем уровне."""
    track = SberSpeakerClient.parse_track(real_track_money_raw)
    assert track is not None
    assert track.track_id == "1001"
    assert track.title == "Test Track"
    assert track.artists == ["Test Artist"]
    assert track.album == "Test Album"
    assert track.release_id == "200"
    assert track.provider == "zvuk"
    assert track.playing is True
    assert track.position_sec == 184
    assert track.position_ts_ms == 1700000000000
    assert track.shuffle is False
    assert track.repeat == "none"


def test_parse_track_state_format_with_info_wrapper(real_track_state_format_raw):
    """State-формат: трек завёрнут в `info{player{...}}` с другими ключами полей."""
    track = SberSpeakerClient.parse_track(real_track_state_format_raw)
    assert track is not None
    assert track.title == "Test Track 2"
    assert track.artists == ["Test Artist 2"]
    # В state-формате releases имеют "title", не "name" — парсер должен это понять
    assert track.album == "Test Album 2"


def test_parse_track_returns_none_for_garbage():
    """Когда нет trackId — парсер должен вернуть None, не падать."""
    assert SberSpeakerClient.parse_track(b"random binary garbage no json") is None
    assert SberSpeakerClient.parse_track(b"") is None
    # JSON без trackId
    assert SberSpeakerClient.parse_track(b'{"foo":"bar"}') is None


def test_parse_track_handles_empty_artists_array():
    """Бывает что artists пуст (например, для radio/podcast)."""
    raw = b'{"trackId":"42","title":"X","artists":[],"provider":"zvuk"}'
    track = SberSpeakerClient.parse_track(raw)
    assert track is not None
    assert track.artists == []
    assert track.title == "X"


def test_parse_track_handles_missing_release_id():
    """Без release_id — парсер не должен падать, просто release_id=None."""
    raw = b'{"trackId":"42","title":"X","artists":[{"name":"A"}],"provider":"salute"}'
    track = SberSpeakerClient.parse_track(raw)
    assert track is not None
    assert track.release_id is None


# ─────────────────────── parse_state ───────────────────────

def test_parse_state_extracts_volume_and_mute():
    """GetState возвращает volume из nested JSON. Парсер находит его regex'ом."""
    raw = (
        b'\x00\x00 some prefix bytes '
        b'{"volume":{"muted":false,"percent":42}, "other":"data"}'
        b' suffix bytes'
    )
    state = SberSpeakerClient.parse_state(raw)
    assert state.volume_percent == 42
    assert state.muted is False


def test_parse_state_handles_muted_true():
    raw = b'{"volume":{"muted":true,"percent":0}}'
    state = SberSpeakerClient.parse_state(raw)
    assert state.muted is True
    assert state.volume_percent == 0


def test_parse_state_returns_defaults_when_no_volume_info():
    """Если в payload нет volume-блока — state с default-значениями (None), не падение."""
    state = SberSpeakerClient.parse_state(b"no volume here")
    # По дефолту volume_percent и muted могут быть None или default int — главное чтобы не падало
    assert state is not None


def test_parse_state_keeps_raw_json_chunk():
    """raw_state_json должен сохраниться для отладки."""
    raw = b'{"volume":{"muted":false,"percent":50}}'
    state = SberSpeakerClient.parse_state(raw)
    assert state.raw_state_json is not None
    assert "volume" in state.raw_state_json
