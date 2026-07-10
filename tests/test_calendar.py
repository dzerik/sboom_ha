"""Тесты Calendar-сущности расписания на реальной фикстуре GET_STATE."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests._fakes import build_coordinator, make_state
from tests._ha_stubs import install_stubs

install_stubs()

from sboom_ha._models import DeviceState  # noqa: E402
from sboom_ha.calendar import SboomScheduleCalendar  # noqa: E402

_STATE = json.loads((Path(__file__).parent / "fixtures" / "alarm_state.json").read_text())


def _calendar_with(alarm=None, reminders=None):
    state = make_state()
    state.device = DeviceState(
        alarms=(alarm or _STATE["alarm"]["alarms"]),
        timers=_STATE["alarm"]["timers"],
        reminders=(reminders if reminders is not None else _STATE["reminders"]),
    )
    coord = build_coordinator(state=state)
    return SboomScheduleCalendar(coord, coord.entry)


@pytest.mark.asyncio
async def test_calendar_aggregates_three_categories():
    """Окно недели содержит будильники, таймер и напоминание с корректными
    метками категорий в description."""
    cal = _calendar_with()
    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)  # таймер (осталось 6489с) сработает в окне
    start = datetime(2026, 7, 10, 0, 0, tzinfo=UTC)
    end = start + timedelta(days=7)
    events = cal._collect(start, end, now)  # явный now для детерминизма таймера
    cats = {e.category for e in events}
    assert cats == {"alarm", "timer", "reminder"}
    assert sum(e.category == "timer" for e in events) == 1
    assert sum(e.category == "reminder" for e in events) == 1
    assert any(e.summary == "встреча" for e in events)
    # HA-обёртка проставляет человекочитаемую метку категории в description
    ha = cal._to_ha(next(e for e in events if e.category == "timer"))
    assert ha.description == "Таймер"


@pytest.mark.asyncio
async def test_calendar_window_filters_events():
    """События вне запрошенного окна не возвращаются."""
    cal = _calendar_with()
    # узкое окно в прошлом — ни будильников, ни напоминания (оно 10-го)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 2, tzinfo=UTC)
    assert await cal.async_get_events(None, start, end) == []


def test_calendar_event_is_none_without_device():
    """Нет device_state (до первого poll) → event = None, без падения."""
    coord = build_coordinator(state=None)
    cal = SboomScheduleCalendar(coord, coord.entry)
    assert cal._collect(datetime.now(UTC), datetime.now(UTC) + timedelta(days=1)) == []
