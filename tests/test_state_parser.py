"""Тесты парсера подсистем GET_STATE (DeviceState) и обновлённого parse_state."""
from __future__ import annotations

from sboom_ha._parsers import parse_device_state, parse_state

# ─────────────────────── parse_device_state ───────────────────────


def test_parse_device_state_led_display():
    d = parse_device_state(
        {"capabilities_state": {"led_display": {"brightness": 80, "turned_on": True}}}
    )
    assert d.led_brightness == 80
    assert d.led_on is True


def test_parse_device_state_alarms_counter_and_list():
    d = parse_device_state(
        {"alarm": {"alarmsCounter": 2, "alarms": [{"id": 1}, {"id": 2}], "timers": []}}
    )
    assert d.alarms_count == 2
    assert len(d.alarms) == 2
    assert d.timers_count == 0


def test_parse_device_state_alarms_count_falls_back_to_list_len():
    """Нет alarmsCounter — считаем по длине списка alarms."""
    d = parse_device_state({"alarm": {"alarms": [{"id": 1}], "timers": [{"id": 9}]}})
    assert d.alarms_count == 1
    assert d.timers_count == 1


def test_parse_device_state_sleep_state():
    assert parse_device_state({"deviceSleep": {"systemState": "working"}}).sleep_state == "working"
    assert parse_device_state({"deviceSleep": {"systemState": "sleeping"}}).sleep_state == "sleeping"


def test_parse_device_state_multiroom():
    d = parse_device_state(
        {"multiroom": {"mode": "NONE", "stereoPair": {"active": True}}}
    )
    assert d.multiroom_mode == "NONE"
    assert d.stereo_pair_active is True


def test_parse_device_state_active_app_is_the_playing_app():
    """active_app — приложение с state.player.playing=true, НЕ background_apps[0].

    background_apps — самотасующийся z-order стек; [0] меняется каждый poll.
    """
    d = parse_device_state({"background_apps": [
        {"app_info": {"systemName": "geo_fixer_app"}, "state": {}},
        {"app_info": {"systemName": "music"},
         "state": {"player": {"playing": True}}},
        {"app_info": {"systemName": "news"}, "state": {}},
    ]})
    assert d.active_app == "music"


def test_parse_device_state_active_app_none_when_nothing_playing():
    """Ничего не играет → active_app=None (а не случайный background_apps[0])."""
    d = parse_device_state({"background_apps": [
        {"app_info": {"systemName": "geo_fixer_app"}, "state": {}},
        {"app_info": {"systemName": "music"},
         "state": {"player": {"playing": False}}},
        {"app_info": {"systemName": "morning_show"},
         "state": {"player": {"stateChangedTimestamp": 1}}},
    ]})
    assert d.active_app is None


def test_parse_device_state_active_app_none_when_empty():
    assert parse_device_state({"background_apps": []}).active_app is None


def test_parse_device_state_assistant_character():
    assert parse_device_state({"assistant": {"character": "afina"}}).assistant_character == "afina"


def test_parse_device_state_diagnostic_fields():
    d = parse_device_state({
        "subscrDeviceInfo": {"isSubscrDevice": True},
        "network": {"connection_type": "WIFI"},
        "homeSecurity": {"enabled": True},
        "morning_show": {"in_show": True},
    })
    assert d.is_subscription_device is True
    assert d.network_type == "WIFI"
    assert d.home_security is True
    assert d.in_morning_show is True


def test_parse_device_state_missing_subsystems_are_none():
    """Пустой GET_STATE — все поля None, парсер не падает."""
    d = parse_device_state({})
    assert d.led_brightness is None
    assert d.led_on is None
    assert d.alarms_count is None
    assert d.timers_count is None
    assert d.sleep_state is None
    assert d.stereo_pair_active is None
    assert d.multiroom_mode is None
    assert d.active_app is None
    assert d.assistant_character is None
    assert d.is_subscription_device is None
    assert d.network_type is None
    assert d.home_security is None
    assert d.in_morning_show is None


def test_parse_device_state_partial_subsystem_does_not_crash():
    """Подсистема есть, но без ожидаемых вложенных ключей — None, не исключение."""
    d = parse_device_state({"capabilities_state": {}, "multiroom": {}, "alarm": {}})
    assert d.led_brightness is None
    assert d.stereo_pair_active is None
    assert d.alarms_count == 0  # alarm есть, но alarms/timers пусты


# ─────────────────────── parse_state + device ───────────────────────


def test_parse_state_populates_device_from_full_payload(device_state_raw):
    """parse_state на полном GET_STATE заполняет .device всеми подсистемами."""
    state = parse_state(device_state_raw)
    assert state.volume_percent == 3
    assert state.muted is False
    assert state.device is not None
    assert state.device.led_brightness == 100
    assert state.device.led_on is True
    assert state.device.active_app == "music"
    assert state.device.assistant_character == "afina"
    assert state.device.sleep_state == "working"
    assert state.device.network_type == "WIFI"


def test_parse_state_device_none_on_broken_json():
    """Битый JSON — volume через regex-fallback, device=None, без падения."""
    raw = b'garbage {"volume":{"muted":true,"percent":7} no closing'
    state = parse_state(raw)
    assert state.device is None


def test_parse_state_volume_still_works_minimal_payload():
    """Регрессия: минимальный payload без подсистем — volume парсится, device пустой."""
    state = parse_state(b'{"volume":{"muted":false,"percent":42}}')
    assert state.volume_percent == 42
    assert state.muted is False
