"""Тесты Repairs platform: issue создаётся при долгой недоступности, удаляется при reconnect."""
from __future__ import annotations

from tests._fakes import build_coordinator, make_state, make_track
from tests._ha_stubs import _ISSUE_REGISTRY, _IssueSeverity

from sboom_ha.const import DOMAIN
from sboom_ha.coordinator import UNREACHABLE_ISSUE_THRESHOLD_SEC


def _issue_key(coord) -> tuple[str, str]:
    return (DOMAIN, f"unreachable_{coord.entry.entry_id}")


def setup_function(_func):
    """Перед каждым тестом — чистый registry."""
    _ISSUE_REGISTRY.clear()


# ─────────────────── creation ───────────────────

def test_no_issue_when_recently_disconnected():
    """Сразу после disconnect issue ещё не создаётся (threshold не истёк)."""
    coord = build_coordinator(track=make_track(), state=make_state())
    coord._set_connected(True)
    coord._set_connected(False)  # disconnect — _unreachable_since = now
    coord._maybe_create_unreachable_issue()
    assert _issue_key(coord) not in _ISSUE_REGISTRY


def test_issue_created_when_threshold_exceeded(monkeypatch):
    """Если прошло > UNREACHABLE_ISSUE_THRESHOLD_SEC — issue создаётся."""
    coord = build_coordinator(track=make_track(), state=make_state())
    coord._set_connected(True)
    coord._set_connected(False)

    # эмулируем что прошло threshold + 1 секунда
    import time as time_mod
    real_monotonic = time_mod.monotonic
    coord._unreachable_since = real_monotonic() - UNREACHABLE_ISSUE_THRESHOLD_SEC - 1

    coord._maybe_create_unreachable_issue()

    assert _issue_key(coord) in _ISSUE_REGISTRY
    issue = _ISSUE_REGISTRY[_issue_key(coord)]
    assert issue["is_fixable"] is True
    assert issue["severity"] == _IssueSeverity.WARNING
    assert issue["translation_key"] == "speaker_unreachable"
    assert "name" in issue["translation_placeholders"]
    assert "minutes" in issue["translation_placeholders"]


def test_issue_not_created_when_connected():
    """Не создавать issue для connected coordinator."""
    coord = build_coordinator(track=make_track(), state=make_state())
    coord._set_connected(True)
    coord._maybe_create_unreachable_issue()
    assert _issue_key(coord) not in _ISSUE_REGISTRY


# ─────────────────── deletion ───────────────────

def test_issue_cleared_on_reconnect():
    """Когда колонка возвращается online — issue удаляется."""
    coord = build_coordinator(track=make_track(), state=make_state())
    coord._set_connected(True)
    coord._set_connected(False)

    # Манипулируем _unreachable_since и форсим issue
    import time as time_mod
    coord._unreachable_since = time_mod.monotonic() - UNREACHABLE_ISSUE_THRESHOLD_SEC - 1
    coord._maybe_create_unreachable_issue()
    assert _issue_key(coord) in _ISSUE_REGISTRY

    # Reconnect → должно очистить
    coord._set_connected(True)
    assert _issue_key(coord) not in _ISSUE_REGISTRY


def test_unreachable_since_resets_on_reconnect():
    coord = build_coordinator(track=make_track(), state=make_state())
    coord._set_connected(True)
    coord._set_connected(False)
    assert coord._unreachable_since is not None
    coord._set_connected(True)
    assert coord._unreachable_since is None
