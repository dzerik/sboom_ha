"""Dataclasses для состояния колонки и метаданных трека."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class TrackInfo:
    title: Optional[str] = None
    artists: list[str] = field(default_factory=list)
    album: Optional[str] = None
    track_id: Optional[str] = None
    release_id: Optional[str] = None
    artist_ids: list[str] = field(default_factory=list)
    playlist_title: Optional[str] = None
    provider: Optional[str] = None
    duration_sec: Optional[int] = None
    position_sec: Optional[int] = None
    position_ts_ms: Optional[int] = None  # timestamp когда position был зафиксирован
    playing: bool = False
    shuffle: bool = False
    repeat: Optional[str] = None
    explicit: bool = False
    liked: bool = False
    playback_speed: Optional[float] = None  # playbackSpeedRate из метаданных (0.5–2.0)
    # Момент получения данных на стороне HA. monotonic — для экстраполяции
    # позиции (часы колонки могут расходиться с часами HA, см. helpers.py),
    # unix-время — для media_position_updated_at.
    received_monotonic: Optional[float] = None
    received_ts: Optional[float] = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class DeviceState:
    """Подсистемы устройства из GET_STATE (op=12), кроме volume/muted."""

    led_brightness: Optional[int] = None       # capabilities_state.led_display.brightness
    led_on: Optional[bool] = None              # capabilities_state.led_display.turned_on
    alarms_count: Optional[int] = None         # alarm.alarmsCounter / len(alarm.alarms)
    alarms: list[Any] = field(default_factory=list)
    timers_count: Optional[int] = None         # len(alarm.timers)
    timers: list[Any] = field(default_factory=list)
    sleep_state: Optional[str] = None          # deviceSleep.systemState ("working" = активна)
    stereo_pair_active: Optional[bool] = None  # multiroom.stereoPair.active
    multiroom_mode: Optional[str] = None       # multiroom.mode
    active_app: Optional[str] = None           # background_apps[0].app_info.systemName
    assistant_character: Optional[str] = None  # assistant.character
    is_subscription_device: Optional[bool] = None  # subscrDeviceInfo.isSubscrDevice
    network_type: Optional[str] = None         # network.connection_type
    home_security: Optional[bool] = None       # homeSecurity.enabled
    in_morning_show: Optional[bool] = None     # morning_show.in_show


@dataclass
class SpeakerState:
    # None = «не пришло в этом ответе» — coordinator домердживает из прежнего
    # state, чтобы частичный/битый push не обнулял громкость в UI.
    volume_percent: Optional[int] = None
    muted: Optional[bool] = None
    track: Optional[TrackInfo] = None
    raw_state_json: Optional[str] = None
    device: Optional[DeviceState] = None


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
    connected: Optional[bool] = None  # для спаренных устройств
    rssi: Optional[int] = None        # уровень сигнала для найденных в скане
