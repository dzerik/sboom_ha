"""Тесты state-merge и страховки от half-open в SboomCoordinator.

Регрессии из код-ревью:
- битый/частичный push обнулял громкость (нет merge со старым state);
- half-open сокет: все запросы таймаутят, но соединение «живое» —
  после N подряд полностью неудачных poll-циклов нужен принудительный close().
"""
from __future__ import annotations

import pytest
from sboom_ha._models import DeviceState
from sboom_ha.api import SpeakerState
from sboom_ha.const import POLL_FAILURES_BEFORE_RECONNECT

from tests._fakes import build_coordinator, make_state, make_track

# ─────────────────────── _merge_state ───────────────────────

def test_merge_state_partial_push_keeps_old_volume():
    """Регрессия: push без volume-блока обнулял громкость/mute в UI."""
    coord = build_coordinator(track=make_track(), state=make_state(volume=42, muted=True))
    merged = coord._merge_state(SpeakerState(volume_percent=None, muted=None))
    assert merged is not None
    assert merged.volume_percent == 42
    assert merged.muted is True


def test_merge_state_none_returns_previous_state_unchanged():
    """parse_state → None (нераспознанный payload) — старый state целиком."""
    old = make_state(volume=17, muted=False)
    coord = build_coordinator(track=make_track(), state=old)
    assert coord._merge_state(None) is old


def test_merge_state_new_values_replace_old():
    coord = build_coordinator(track=make_track(), state=make_state(volume=42, muted=True))
    merged = coord._merge_state(SpeakerState(volume_percent=80, muted=False))
    assert merged.volume_percent == 80
    assert merged.muted is False


def test_merge_state_keeps_old_device_when_missing_in_new():
    """device-сенсоры не должны пропадать из-за push без device-блока."""
    device = DeviceState(led_brightness=90)
    old = SpeakerState(volume_percent=10, muted=False, device=device)
    coord = build_coordinator(track=make_track(), state=old)
    merged = coord._merge_state(SpeakerState(volume_percent=20))
    assert merged.device is device
    assert merged.volume_percent == 20


def test_merge_state_no_previous_state_passthrough():
    """Первый state (прежнего нет) — частичный payload принимается как есть."""
    coord = build_coordinator(track=make_track(), state=None)
    merged = coord._merge_state(SpeakerState(volume_percent=None, muted=None))
    assert merged is not None
    assert merged.volume_percent is None


# ─────────────────────── poll failures → форс-reconnect ───────────────────────

def _patch_client(coord, *, state_ok: bool = False):
    """Мокает методы клиента: всё падает (или get_state успешен), close пишется в список."""
    closed: list[bool] = []

    async def fail(*args, **kwargs):
        raise ConnectionError("half-open: request timed out")

    async def ok_state(*args, **kwargs):
        return make_state(volume=50)

    async def record_close():
        closed.append(True)

    coord.client.get_state = ok_state if state_ok else fail
    coord.client.get_metadata = fail
    coord.client.get_paired_bt_devices = fail
    coord.client.close = record_close
    return closed


@pytest.mark.asyncio
async def test_poll_failures_trigger_forced_close():
    """N подряд полностью неудачных циклов → client.close() (страховка от half-open)."""
    coord = build_coordinator(track=make_track(), state=make_state(volume=50))
    closed = _patch_client(coord)

    for i in range(POLL_FAILURES_BEFORE_RECONNECT - 1):
        await coord._refresh_state_and_track(notify=False)
        assert closed == [], f"close после {i + 1} циклов — рано"

    await coord._refresh_state_and_track(notify=False)
    assert closed == [True], "после порогового цикла должен быть принудительный close()"
    # счётчик сброшен — следующая серия отсчитывается заново
    assert coord._poll_failures == 0


@pytest.mark.asyncio
async def test_poll_failure_does_not_wipe_state():
    """Неудачный цикл не должен затирать последний известный state."""
    old = make_state(volume=50, muted=False)
    coord = build_coordinator(track=make_track(), state=old)
    _patch_client(coord)
    await coord._refresh_state_and_track(notify=False)
    assert coord.state is old


@pytest.mark.asyncio
async def test_successful_poll_resets_failure_counter():
    """Успешный цикл сбрасывает счётчик — редкие одиночные сбои не копятся до close()."""
    coord = build_coordinator(track=make_track(), state=make_state(volume=50))
    all_closed: list[bool] = []

    closed = _patch_client(coord)  # всё падает
    all_closed = closed
    await coord._refresh_state_and_track(notify=False)
    assert coord._poll_failures == 1

    _patch_client(coord, state_ok=True)  # get_state успешен → сброс
    await coord._refresh_state_and_track(notify=False)
    assert coord._poll_failures == 0

    closed2 = _patch_client(coord)  # снова всё падает — серия начинается с нуля
    await coord._refresh_state_and_track(notify=False)
    assert coord._poll_failures == 1
    assert all_closed == [] and closed2 == [], "close не должен вызываться при сброшенной серии"
