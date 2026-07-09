"""Dataclasses для состояния колонки и метаданных трека."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TrackInfo:
    title: str | None = None
    artists: list[str] = field(default_factory=list)
    album: str | None = None
    track_id: str | None = None
    release_id: str | None = None
    artist_ids: list[str] = field(default_factory=list)
    playlist_title: str | None = None
    provider: str | None = None
    duration_sec: int | None = None
    position_sec: int | None = None
    position_ts_ms: int | None = None  # timestamp когда position был зафиксирован
    playing: bool = False
    shuffle: bool = False
    repeat: str | None = None
    explicit: bool = False
    liked: bool = False
    has_lyrics: bool | None = None  # info.hasLyrics — есть ли у трека текст на стороне Sber
    playback_speed: float | None = None  # playbackSpeedRate из метаданных (0.5–2.0)
    # Момент получения данных на стороне HA. monotonic — для экстраполяции
    # позиции (часы колонки могут расходиться с часами HA, см. helpers.py),
    # unix-время — для media_position_updated_at.
    received_monotonic: float | None = None
    received_ts: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class DeviceState:
    """Подсистемы устройства из GET_STATE (op=12), кроме volume/muted."""

    led_brightness: int | None = None       # capabilities_state.led_display.brightness
    led_on: bool | None = None              # capabilities_state.led_display.turned_on
    alarms_count: int | None = None         # alarm.alarmsCounter / len(alarm.alarms)
    alarms: list[Any] = field(default_factory=list)
    timers_count: int | None = None         # len(alarm.timers)
    timers: list[Any] = field(default_factory=list)
    sleep_state: str | None = None          # deviceSleep.systemState ("working" = активна)
    stereo_pair_active: bool | None = None  # multiroom.stereoPair.active
    multiroom_mode: str | None = None       # multiroom.mode
    active_app: str | None = None           # background_apps[0].app_info.systemName
    assistant_character: str | None = None  # assistant.character
    is_subscription_device: bool | None = None  # subscrDeviceInfo.isSubscrDevice
    network_type: str | None = None         # network.connection_type
    network_ip: str | None = None           # network.ip
    home_security: bool | None = None       # homeSecurity.enabled
    in_morning_show: bool | None = None     # morning_show.in_show
    alarm_ringing: bool | None = None       # alarm.playing (null когда тихо, truthy при звонке)
    assistant_auto_volume: bool | None = None  # assistant.auto_volume
    proactivity_notification: bool | None = None  # proactivityNotification.hasNotification
    timezone_id: str | None = None          # time.timezone_id (напр. "Europe/Moscow")
    device_unixtime: float | None = None    # timesync.unixtime — часы колонки (для диагностики skew)
    age_mode: str | None = None             # user_settings.age_mode (adult/child)


@dataclass
class SpeakerState:
    # None = «не пришло в этом ответе» — coordinator домердживает из прежнего
    # state, чтобы частичный/битый push не обнулял громкость в UI.
    volume_percent: int | None = None
    muted: bool | None = None
    track: TrackInfo | None = None
    raw_state_json: str | None = None
    device: DeviceState | None = None


@dataclass
class QueueTrack:
    """Элемент очереди воспроизведения (op=17) — только id, без метаданных."""

    track_id: str
    explicit: bool = False


@dataclass
class BluetoothDevice:
    """BT-устройство колонки — спаренное (op=19) или найденное в скане (op=21)."""

    mac: str
    name: str = ""
    connected: bool | None = None  # для спаренных устройств
    rssi: int | None = None        # уровень сигнала для найденных в скане
