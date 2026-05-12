"""Тесты SboomMediaPlayer entity-properties через stubs HA + Fake coordinator."""
from __future__ import annotations

# Пользуемся builders из _fakes — они уже подняли HA stubs.
from tests._fakes import build_coordinator, make_entry, make_state, make_track

from homeassistant.components.media_player import MediaPlayerState, MediaType, RepeatMode
from sboom_ha.media_player import SboomMediaPlayer


# ─────────────────────── state derivation ───────────────────────

def test_state_idle_when_no_track():
    coord = build_coordinator(track=None, state=make_state())
    mp = SboomMediaPlayer(coord, coord.entry)
    assert mp.state == MediaPlayerState.IDLE


def test_state_playing_when_track_playing():
    coord = build_coordinator(track=make_track(playing=True), state=make_state())
    mp = SboomMediaPlayer(coord, coord.entry)
    assert mp.state == MediaPlayerState.PLAYING


def test_state_paused_when_track_paused():
    coord = build_coordinator(track=make_track(playing=False), state=make_state())
    mp = SboomMediaPlayer(coord, coord.entry)
    assert mp.state == MediaPlayerState.PAUSED


# ─────────────────────── volume / mute ───────────────────────

def test_volume_level_normalized_to_0_1():
    coord = build_coordinator(track=make_track(), state=make_state(volume=42))
    mp = SboomMediaPlayer(coord, coord.entry)
    assert mp.volume_level == 0.42


def test_volume_level_none_when_state_missing():
    coord = build_coordinator(track=make_track(), state=None)
    mp = SboomMediaPlayer(coord, coord.entry)
    assert mp.volume_level is None


def test_is_volume_muted_reflects_state():
    coord = build_coordinator(track=make_track(), state=make_state(muted=True))
    mp = SboomMediaPlayer(coord, coord.entry)
    assert mp.is_volume_muted is True


# ─────────────────────── metadata ───────────────────────

def test_media_metadata_basics():
    coord = build_coordinator(
        track=make_track(
            title="Some Song",
            artists=["A1", "A2"],
            album="An Album",
            track_id="42",
            duration_sec=300,
            position_sec=75,
        ),
        state=make_state(),
    )
    mp = SboomMediaPlayer(coord, coord.entry)
    assert mp.media_title == "Some Song"
    assert mp.media_artist == "A1, A2"
    assert mp.media_album_name == "An Album"
    assert mp.media_content_id == "42"
    assert mp.media_content_type == MediaType.MUSIC
    assert mp.media_duration == 300
    assert mp.media_position == 75


def test_media_metadata_none_when_no_track():
    coord = build_coordinator(track=None, state=make_state())
    mp = SboomMediaPlayer(coord, coord.entry)
    assert mp.media_title is None
    assert mp.media_artist is None
    assert mp.media_content_type is None


def test_media_artist_empty_artists_returns_none():
    coord = build_coordinator(track=make_track(artists=[]), state=make_state())
    mp = SboomMediaPlayer(coord, coord.entry)
    assert mp.media_artist is None


# ─────────────────────── shuffle / repeat ───────────────────────

def test_shuffle_propagates_from_track():
    coord = build_coordinator(track=make_track(shuffle=True), state=make_state())
    mp = SboomMediaPlayer(coord, coord.entry)
    assert mp.shuffle is True


def test_repeat_mode_mapping():
    cases = {
        "none": RepeatMode.OFF,
        "playlist": RepeatMode.ALL,
        "all": RepeatMode.ALL,
        "track": RepeatMode.ONE,
        "one": RepeatMode.ONE,
        "GARBAGE": RepeatMode.OFF,
    }
    for raw, expected in cases.items():
        coord = build_coordinator(track=make_track(repeat=raw), state=make_state())
        mp = SboomMediaPlayer(coord, coord.entry)
        assert mp.repeat == expected, f"repeat={raw!r}"


def test_repeat_none_when_no_track():
    coord = build_coordinator(track=None, state=make_state())
    mp = SboomMediaPlayer(coord, coord.entry)
    assert mp.repeat is None


# ─────────────────────── cover_url ───────────────────────

def test_media_image_url_uses_release_id_for_zvuk():
    coord = build_coordinator(
        track=make_track(provider="zvuk", release_id="9999"),
        state=make_state(),
    )
    mp = SboomMediaPlayer(coord, coord.entry)
    assert mp.media_image_url is not None
    assert "type=release" in mp.media_image_url
    assert "id=9999" in mp.media_image_url


def test_media_image_url_none_for_non_zvuk_provider():
    coord = build_coordinator(
        track=make_track(provider="salute", release_id="1"),
        state=make_state(),
    )
    mp = SboomMediaPlayer(coord, coord.entry)
    assert mp.media_image_url is None


# ─────────────────────── app_name ───────────────────────

def test_app_name_humanizes_known_providers():
    expectations = {
        "zvuk": "Sber Звук",
        "salute": "Салют",
        "youtube": "YouTube",
        "spotify": "Spotify",
        "unknown_provider": "unknown_provider",
    }
    for provider, expected in expectations.items():
        coord = build_coordinator(
            track=make_track(provider=provider),
            state=make_state(),
        )
        mp = SboomMediaPlayer(coord, coord.entry)
        assert mp.app_name == expected, provider


def test_app_name_none_when_no_provider():
    coord = build_coordinator(track=make_track(provider=None), state=make_state())
    mp = SboomMediaPlayer(coord, coord.entry)
    assert mp.app_name is None


# ─────────────────────── unique_id / device_info ─────────

def test_unique_id_uses_device_id_from_entry():
    entry = make_entry(device_id="dev-abc")
    coord = build_coordinator(entry=entry, track=make_track(), state=make_state())
    mp = SboomMediaPlayer(coord, entry)
    assert mp._attr_unique_id == "sboom_ha_dev-abc"


def test_unique_id_falls_back_to_host_when_no_device_id():
    entry = make_entry(host="10.0.0.5", device_id=None)
    # device_id=None в data — _entity_base падает обратно на host
    entry.data["device_id"] = None
    coord = build_coordinator(entry=entry, track=make_track(), state=make_state())
    mp = SboomMediaPlayer(coord, entry)
    assert mp._attr_unique_id == "sboom_ha_10.0.0.5"
