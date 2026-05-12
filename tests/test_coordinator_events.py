"""Тесты event bus в SboomCoordinator: track/playback/volume changes."""
from __future__ import annotations

import asyncio

import pytest

from tests._fakes import build_coordinator, make_state, make_track

from sboom_ha.coordinator import (
    EVENT_PLAYBACK_CHANGED,
    EVENT_TRACK_CHANGED,
    EVENT_VOLUME_CHANGED,
)


def _fire(coord, event_type: str, count: int = 1):
    """Помощник: проверяет что в bus.fired есть N событий с заданным типом, возвращает payload'ы."""
    found = [data for et, data in coord.hass.bus.fired if et == event_type]
    assert len(found) == count, (
        f"expected {count} `{event_type}` events, got {len(found)}; "
        f"fired={coord.hass.bus.fired}"
    )
    return found


# ─────────────────────── _fire_change_events напрямую ───────────────────────

def test_track_changed_fires_on_new_track():
    coord = build_coordinator(track=make_track(track_id="A"), state=make_state())
    coord.hass.bus.fired.clear()
    coord.track = make_track(track_id="B", title="New", artists=["X"], album="Y")
    coord._fire_change_events(prev_track=make_track(track_id="A"), prev_state=coord.state)

    [payload] = _fire(coord, EVENT_TRACK_CHANGED)
    assert payload["track_id"] == "B"
    assert payload["title"] == "New"
    assert payload["artists"] == ["X"]
    assert payload["album"] == "Y"
    assert payload["previous_track_id"] == "A"
    assert payload["entry_id"] == coord.entry.entry_id
    assert payload["host"] == "192.0.2.10"


def test_track_changed_fires_when_initial_track_appears():
    """prev=None, current=валидный трек — это тоже событие смены."""
    coord = build_coordinator(track=make_track(track_id="A"), state=make_state())
    coord.hass.bus.fired.clear()
    coord._fire_change_events(prev_track=None, prev_state=coord.state)

    [payload] = _fire(coord, EVENT_TRACK_CHANGED)
    assert payload["track_id"] == "A"
    assert payload["previous_track_id"] is None


def test_no_track_event_when_track_id_unchanged_and_no_other_changes():
    coord = build_coordinator(track=make_track(track_id="A", playing=True), state=make_state())
    coord.hass.bus.fired.clear()
    coord._fire_change_events(
        prev_track=make_track(track_id="A", playing=True),
        prev_state=coord.state,
    )
    assert coord.hass.bus.fired == []


def test_playback_changed_fires_when_only_playing_flips():
    coord = build_coordinator(track=make_track(track_id="A", playing=False), state=make_state())
    coord.hass.bus.fired.clear()
    coord._fire_change_events(
        prev_track=make_track(track_id="A", playing=True),
        prev_state=coord.state,
    )
    [payload] = _fire(coord, EVENT_PLAYBACK_CHANGED)
    assert payload["playing"] is False
    assert payload["track_id"] == "A"
    # Не должно быть track_changed для того же track_id
    assert all(et != EVENT_TRACK_CHANGED for et, _ in coord.hass.bus.fired)


def test_playback_changed_fires_on_shuffle_or_repeat_change():
    coord = build_coordinator(
        track=make_track(track_id="A", shuffle=True, repeat="track"),
        state=make_state(),
    )
    coord.hass.bus.fired.clear()
    coord._fire_change_events(
        prev_track=make_track(track_id="A", shuffle=False, repeat="none"),
        prev_state=coord.state,
    )
    [payload] = _fire(coord, EVENT_PLAYBACK_CHANGED)
    assert payload["shuffle"] is True
    assert payload["repeat"] == "track"


def test_volume_changed_fires_on_volume_diff():
    coord = build_coordinator(track=make_track(), state=make_state(volume=70, muted=False))
    coord.hass.bus.fired.clear()
    coord._fire_change_events(
        prev_track=coord.track,
        prev_state=make_state(volume=50, muted=False),
    )
    [payload] = _fire(coord, EVENT_VOLUME_CHANGED)
    assert payload["volume_percent"] == 70
    assert payload["muted"] is False


def test_volume_changed_fires_on_mute_toggle():
    coord = build_coordinator(track=make_track(), state=make_state(volume=50, muted=True))
    coord.hass.bus.fired.clear()
    coord._fire_change_events(
        prev_track=coord.track,
        prev_state=make_state(volume=50, muted=False),
    )
    [payload] = _fire(coord, EVENT_VOLUME_CHANGED)
    assert payload["muted"] is True


def test_volume_changed_fires_when_initial_state_appears():
    coord = build_coordinator(track=make_track(), state=make_state(volume=33, muted=False))
    coord.hass.bus.fired.clear()
    coord._fire_change_events(prev_track=coord.track, prev_state=None)
    [payload] = _fire(coord, EVENT_VOLUME_CHANGED)
    assert payload["volume_percent"] == 33


def test_no_volume_event_when_state_unchanged():
    coord = build_coordinator(track=make_track(), state=make_state(volume=50, muted=False))
    coord.hass.bus.fired.clear()
    coord._fire_change_events(
        prev_track=coord.track,
        prev_state=make_state(volume=50, muted=False),
    )
    assert all(et != EVENT_VOLUME_CHANGED for et, _ in coord.hass.bus.fired)


def test_event_payload_includes_device_context():
    """Каждое событие должно содержать entry_id, device_id, host."""
    coord = build_coordinator(track=make_track(track_id="A"), state=make_state())
    coord.hass.bus.fired.clear()
    coord._fire_change_events(prev_track=None, prev_state=None)
    for _, payload in coord.hass.bus.fired:
        assert "entry_id" in payload
        assert "device_id" in payload
        assert "host" in payload


# ─────────────────────── _handle_event end-to-end ───────────────────────

def _push_track_payload(track_id: str, title: str = "T") -> tuple[bytes, dict]:
    """Сборка валидного push-payload + parsed-обёртки для _handle_event."""
    raw = (
        b'\x00\x00{"artists":[{"id":"1","name":"A"}],'
        + b'"playing":true,"position":{"tsMs":1700000000000,"val":10},'
        + b'"provider":"zvuk","releases":[{"id":"5","name":"R"}],'
        + b'"repeatType":"none","shuffle":false,'
        + f'"title":"{title}","trackId":"{track_id}"'.encode()
        + b'}'
    )
    parsed = {5: {10: True}}  # field 10 = metadata-update маркер
    return raw, parsed


@pytest.mark.asyncio
async def test_handle_event_metadata_push_fires_track_changed():
    coord = build_coordinator(track=None, state=make_state())
    coord.hass.bus.fired.clear()
    raw, parsed = _push_track_payload("999", title="First")
    await coord._handle_event(raw, parsed)

    [payload] = _fire(coord, EVENT_TRACK_CHANGED)
    assert payload["track_id"] == "999"
    assert payload["title"] == "First"


@pytest.mark.asyncio
async def test_handle_event_idempotent_when_same_track():
    coord = build_coordinator(track=None, state=make_state())
    raw, parsed = _push_track_payload("777")
    await coord._handle_event(raw, parsed)  # first push: track A → fire
    coord.hass.bus.fired.clear()
    await coord._handle_event(raw, parsed)  # second push: still A → no fire
    assert all(et != EVENT_TRACK_CHANGED for et, _ in coord.hass.bus.fired)
