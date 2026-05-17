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

from sboom_ha._tlv import field
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


def test_parse_track_extracts_playback_speed(real_track_money_raw):
    """playbackSpeedRate из метаданных трека → TrackInfo.playback_speed (float)."""
    track = SberSpeakerClient.parse_track(real_track_money_raw)
    assert track is not None
    assert track.playback_speed == 1.0


def test_parse_track_extracts_non_default_playback_speed():
    """Колонка играет на ускорении — playback_speed != 1.0."""
    raw = b'{"trackId":"42","title":"X","artists":[],"playbackSpeedRate":1.5}'
    track = SberSpeakerClient.parse_track(raw)
    assert track is not None
    assert track.playback_speed == 1.5


def test_parse_track_playback_speed_none_when_absent():
    """Нет playbackSpeedRate в payload — playback_speed остаётся None."""
    raw = b'{"trackId":"42","title":"X","artists":[]}'
    track = SberSpeakerClient.parse_track(raw)
    assert track is not None
    assert track.playback_speed is None


# ─────────────────────── parse_queue ───────────────────────


def test_parse_queue_extracts_track_ids(queue_raw):
    """op=17: очередь — упорядоченный список trackId + флаг explicit."""
    queue = SberSpeakerClient.parse_queue(queue_raw)
    assert len(queue) == 6
    assert queue[0].track_id == "112774234"
    assert queue[0].explicit is False
    assert [q.track_id for q in queue] == [
        "112774234", "112774241", "112774249",
        "112774276", "112774292", "112774302",
    ]


def test_parse_queue_preserves_explicit_flag():
    arr = b'[{"explicit":true,"trackId":5},{"explicit":false,"trackId":6}]'
    # реальный op=17 всегда содержит поле 4 (бинарь) перед полем 5 — см. exp_23
    inner = field(4, 2, b"\x04\x05") + field(5, 2, arr)
    raw = field(5, 2, field(17, 2, inner))
    queue = SberSpeakerClient.parse_queue(raw)
    assert queue[0].explicit is True
    assert queue[1].explicit is False


def test_parse_queue_empty_array():
    raw = field(5, 2, field(17, 2, field(5, 2, b"[]")))
    assert SberSpeakerClient.parse_queue(raw) == []


def test_parse_queue_broken_payload_returns_empty():
    """Мусор / пустой / отсутствующее поле — пустой список, не падение."""
    assert SberSpeakerClient.parse_queue(b"random garbage") == []
    assert SberSpeakerClient.parse_queue(b"") == []
    # envelope без вложенного op=17
    assert SberSpeakerClient.parse_queue(field(5, 2, field(99, 2, b""))) == []


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


# ─────────────────────── parse_paired_bt / parse_scanned_bt ───────────────────────

# Пустой ответ op=21 (status=1, устройств нет) — снят с колонки, без PII.
_OP21_EMPTY_SAMPLE = bytes.fromhex(
    "0802122439393039386237392d646233662d346565362d626363652d313736323163"
    "3266653261342a05aa01020801"
)


def _paired_dev(mac: str, name: str, connected: bool) -> bytes:
    return (field(1, 2, mac.encode()) + field(2, 2, name.encode())
            + field(3, 0, 1 if connected else 0))


def test_parse_paired_bt_multiple_devices():
    """Парсер op=19 на реальной wire-структуре (envelope→5→19→repeated field 1)."""
    inner = (field(1, 2, _paired_dev("AA:BB:CC:00:11:22", "Phone", True))
             + field(1, 2, _paired_dev("DD:EE:FF:33:44:55", "Headset", False)))
    raw = field(5, 2, field(19, 2, inner))
    devices = SberSpeakerClient.parse_paired_bt(raw)
    assert [d.mac for d in devices] == ["AA:BB:CC:00:11:22", "DD:EE:FF:33:44:55"]
    assert [d.name for d in devices] == ["Phone", "Headset"]
    assert devices[0].connected is True
    assert devices[1].connected is False


def test_parse_paired_bt_empty():
    raw = field(5, 2, field(19, 2, b""))
    assert SberSpeakerClient.parse_paired_bt(raw) == []
    assert SberSpeakerClient.parse_paired_bt(b"garbage") == []


def test_parse_scanned_bt_empty_sample():
    """op=21: status=1, repeated-устройств нет → []."""
    assert SberSpeakerClient.parse_scanned_bt(_OP21_EMPTY_SAMPLE) == []


def test_parse_scanned_bt_with_devices():
    dev = field(1, 2, b"11:22:33:44:55:66") + field(2, 2, b"Speaker") + field(3, 0, 200)
    # ScannedDevice — field 2 (repeated) внутри ответа; field 1 = status
    inner = field(1, 0, 1) + field(2, 2, dev)
    raw = field(5, 2, field(21, 2, inner))
    devices = SberSpeakerClient.parse_scanned_bt(raw)
    assert len(devices) == 1
    assert devices[0].mac == "11:22:33:44:55:66"
    assert devices[0].name == "Speaker"
    assert devices[0].rssi == 200
