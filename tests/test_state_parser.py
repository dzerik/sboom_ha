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


# ─────────── новые подсистемы GET_STATE (0.16.0, схемы из реальных захватов) ───────────
#
# Схемы взяты из research/events.jsonl (декодированный op=12):
# location, assistant.auto_volume, proactivityNotification, network.ip,
# time.timezone_id, timesync.unixtime, user_settings.age_mode, alarm.playing.


def test_parse_device_state_assistant_auto_volume():
    """assistant.auto_volume → отдельный флаг, character по-прежнему читается."""
    d = parse_device_state({"assistant": {"auto_volume": True, "character": "afina"}})
    assert d.assistant_auto_volume is True
    assert d.assistant_character == "afina"
    # Без поля — None (не False), чтобы не путать «выключено» с «нет данных».
    assert parse_device_state({"assistant": {"character": "joy"}}).assistant_auto_volume is None


def test_parse_device_state_proactivity_notification():
    assert parse_device_state(
        {"proactivityNotification": {"hasNotification": True}}
    ).proactivity_notification is True
    assert parse_device_state(
        {"proactivityNotification": {"hasNotification": False}}
    ).proactivity_notification is False
    assert parse_device_state({}).proactivity_notification is None


def test_parse_device_state_alarm_ringing_bool_coercion():
    """alarm.playing: null → False (поле есть), truthy → True. Форма truthy
    неизвестна из захватов, поэтому bool() покрывает любой вариант."""
    assert parse_device_state({"alarm": {"playing": None}}).alarm_ringing is False
    assert parse_device_state({"alarm": {"playing": "session-42"}}).alarm_ringing is True
    assert parse_device_state({"alarm": {"playing": 1}}).alarm_ringing is True
    # Поля playing нет вовсе → None (не False).
    assert parse_device_state({"alarm": {"alarms": []}}).alarm_ringing is None


def test_parse_device_state_network_ip():
    d = parse_device_state({"network": {"connection_type": "WIFI", "ip": "95.165.105.44"}})
    assert d.network_type == "WIFI"
    assert d.network_ip == "95.165.105.44"


def test_parse_device_state_time_and_timesync():
    d = parse_device_state({
        "time": {"timezone_id": "Europe/Moscow", "timezone_offset_sec": 10800},
        "timesync": {"unixtime": 1778182355.536},
    })
    assert d.timezone_id == "Europe/Moscow"
    assert d.device_unixtime == 1778182355.536


def test_parse_device_state_age_mode():
    assert parse_device_state(
        {"user_settings": {"age_mode": "adult"}}
    ).age_mode == "adult"


def test_parse_device_state_full_real_capture():
    """Полный реальный GET_STATE (сокращённый до целевых подсистем) —
    все новые поля извлекаются вместе, ничего не ломает существующие."""
    real = {
        "location": {"accuracy": 8.0, "lat": 55.660927, "lon": 37.469685,
                     "source": "wifi", "timestamp": 1777494329616},
        "assistant": {"auto_volume": False, "character": "afina"},
        "proactivityNotification": {"hasNotification": False},
        "timesync": {"unixtime": 1778182355.536},
        "time": {"timezone_id": "Europe/Moscow"},
        "network": {"connection_type": "WIFI", "ip": "95.165.105.44"},
        "user_settings": {"age_mode": "adult"},
        "alarm": {"alarms": [], "alarmsCounter": 0, "playing": None, "timers": []},
        "capabilities_state": {"led_display": {"brightness": 100, "turned_on": True}},
    }
    d = parse_device_state(real)
    assert d.assistant_auto_volume is False
    assert d.assistant_character == "afina"
    assert d.proactivity_notification is False
    assert d.device_unixtime == 1778182355.536
    assert d.timezone_id == "Europe/Moscow"
    assert d.network_ip == "95.165.105.44"
    assert d.age_mode == "adult"
    assert d.alarm_ringing is False
    assert d.led_brightness == 100  # регрессия: старые поля не пострадали


def test_parse_device_state_location():
    """location {lat,lon,accuracy,source} → координаты (схема из реального захвата)."""
    d = parse_device_state({
        "location": {"accuracy": 8.0, "lat": 55.660927, "lon": 37.469685,
                     "source": "wifi", "timestamp": 1777494329616}
    })
    assert d.latitude == 55.660927
    assert d.longitude == 37.469685
    assert d.location_accuracy == 8  # округлён до int (метры)
    assert d.location_source == "wifi"


def test_parse_device_state_location_partial_ignored():
    """Битая/неполная координата не даёт полу-заполненного положения."""
    d = parse_device_state({"location": {"lat": 55.6, "source": "wifi"}})  # без lon
    assert d.latitude is None and d.longitude is None
    assert d.location_source == "wifi"  # source читается независимо
    assert parse_device_state({}).latitude is None


def test_coordinates_sensor_value_format():
    """Диагностический сенсор координат: state='lat, lon', детали в атрибутах
    (в отличие от device_tracker, где state = имя зоны)."""
    from sboom_ha._models import DeviceState
    from sboom_ha.sensor import SENSOR_SPECS

    from tests._fakes import build_coordinator, make_state
    spec = next(s for s in SENSOR_SPECS if s.key == "coordinates")
    state = make_state()
    state.device = DeviceState(latitude=55.66, longitude=37.47,
                               location_accuracy=8, location_source="wifi")
    coord = build_coordinator(state=state)
    assert spec.value_fn(coord) == "55.66, 37.47"
    attrs = spec.attrs_fn(coord)
    assert attrs["latitude"] == 55.66 and attrs["gps_accuracy"] == 8
    # Нет координат → None (сенсор пустой, не строка "None, None")
    state.device = DeviceState()
    assert spec.value_fn(coord) is None
    assert spec.attrs_fn(coord) is None


def test_parse_device_state_reminders_raw_block():
    """reminders.reminders сохраняется сырым блоком для календаря."""
    d = parse_device_state({"reminders": {"reminders": {"time_reminders": {"k": []}}}})
    assert d.reminders == {"reminders": {"time_reminders": {"k": []}}}
    assert parse_device_state({}).reminders == {}


def test_next_alarm_timer_sensors_from_fixture():
    """Сенсоры next_alarm/next_timer читают время из dev.alarms/dev.timers."""
    import json
    from datetime import UTC, datetime
    from pathlib import Path

    from sboom_ha._models import DeviceState
    from sboom_ha._schedule import next_alarm, next_timer

    from tests._fakes import build_coordinator, make_state

    st = json.loads((Path(__file__).parent / "fixtures" / "alarm_state.json").read_text())
    state = make_state()
    state.device = DeviceState(alarms=st["alarm"]["alarms"], timers=st["alarm"]["timers"])
    coord = build_coordinator(state=state)
    from datetime import timedelta
    now = datetime(2026, 7, 13, 3, 0, tzinfo=UTC)  # понедельник 03:00
    # ближайший будильник — будничный 04:00:40 того же дня
    assert next_alarm(coord.state.device.alarms, now) == datetime(2026, 7, 13, 4, 0, 40, tzinfo=UTC)
    # таймер: осталось 6489с от now
    assert next_timer(coord.state.device.timers, now) == now + timedelta(seconds=6489)


def test_parse_device_state_new_unused_fields():
    """device_segments, current_app, user_settings-флаги, morning_show.from_show,
    time.timezone_offset_sec, background_apps z-order — из реального GET_STATE."""
    d = parse_device_state({
        "device_segments": ["OpenBeta"],
        "current_app": {"app_info": {"systemName": "music"}, "state": {}},
        "user_settings": {"age_mode": "adult", "multi_profile": False,
                          "enable_child_voice_explicit": True},
        "morning_show": {"in_show": False, "from_show": True},
        "time": {"timezone_id": "Europe/Moscow", "timezone_offset_sec": 10800},
        "background_apps": [
            {"app_info": {"systemName": "pager"}, "state": {}},
            {"app_info": {"systemName": "music"}, "state": {"player": {"playing": True}}},
        ],
    })
    assert d.firmware_channel == "OpenBeta"
    assert d.foreground_app == "music"
    assert d.multi_profile is False
    assert d.child_voice_explicit is True
    assert d.morning_show_from is True
    assert d.timezone_offset_sec == 10800
    assert d.app_stack == ["pager", "music"]  # весь стек в z-order
    assert d.active_app == "music"             # играющее — по-прежнему music


def test_parse_device_state_empty_current_app():
    """current_app пуст (ничего не открыто) → foreground_app None, не падаем."""
    d = parse_device_state({"current_app": {"app_info": {}, "state": {}}})
    assert d.foreground_app is None
    assert parse_device_state({"device_segments": []}).firmware_channel is None
