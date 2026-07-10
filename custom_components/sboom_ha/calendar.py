"""Calendar-сущность — будильники, таймеры и напоминания колонки.

Один календарь на колонку с тремя категориями событий из GET_STATE:
будильники (с раскрытием iCalendar RRULE), таймеры (обратный отсчёт),
напоминания. Read-only: колонка не даёт LAN-op для создания событий.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ._entity_base import SboomEntity
from ._schedule import (
    ScheduleEvent,
    alarm_events,
    reminder_events,
    timer_events,
)
from .coordinator import SboomCoordinator

# Read-only, данные из coordinator — параллелизм безразличен.
PARALLEL_UPDATES = 0

# Горизонт для вычисления «ближайшего» события (state сущности).
_DEFAULT_LOOKAHEAD = timedelta(days=90)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SboomCoordinator = entry.runtime_data
    async_add_entities([SboomScheduleCalendar(coordinator, entry)])


class SboomScheduleCalendar(SboomEntity, CalendarEntity):
    """Будильники + таймеры + напоминания колонки одним календарём."""

    _attr_translation_key = "schedule"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator: SboomCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._device_unique_prefix}_schedule"

    def _collect(
        self, start: datetime, end: datetime, now: datetime | None = None
    ) -> list[ScheduleEvent]:
        """Все события расписания в окне [start, end].

        Будильники и напоминания — с абсолютными временами (фильтр по окну).
        Таймеры относительны текущего момента (timeLeftSec), поэтому
        привязываются к `now`, а не к началу окна.
        """
        dev = self.device_state
        if dev is None:
            return []
        now = now or datetime.now(UTC)
        horizon = end - start
        absolute = [
            *alarm_events(dev.alarms, start, horizon),
            *reminder_events(dev.reminders),
        ]
        events = [e for e in absolute if start <= e.start <= end]
        # Таймер включаем, если его срабатывание попадает в окно.
        events += [
            e for e in timer_events(dev.timers, now) if start <= e.end <= end
        ]
        return events

    @staticmethod
    def _to_ha(e: ScheduleEvent) -> CalendarEvent:
        # Категория в description — чтобы различать будильник/таймер/напоминание.
        label = {"alarm": "Будильник", "timer": "Таймер", "reminder": "Напоминание"}
        return CalendarEvent(
            start=e.start,
            end=e.end,
            summary=e.summary,
            description=label.get(e.category, e.category),
            uid=e.uid,
        )

    @property
    def event(self) -> CalendarEvent | None:
        """Ближайшее будущее событие (для state сущности)."""
        now = datetime.now(UTC)
        upcoming = self._collect(now, now + _DEFAULT_LOOKAHEAD, now)
        future = sorted((e for e in upcoming if e.end >= now), key=lambda e: e.start)
        return self._to_ha(future[0]) if future else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """События в запрошенном окне (для карточки календаря)."""
        return [self._to_ha(e) for e in self._collect(start_date, end_date)]
