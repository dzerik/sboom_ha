# Живая схема состояния колонки (реальные имена полей)

Снято с колонки (`GET_STATE` op12 / `GET_META_DATA` op10) — JSON-ключи = имена
proto-полей. Закрывает пробел «имена не-строковых полей» реконструкции.
Только имена+типы (значения приватны, не коммитятся; см. `capture_state.py`).

## GET_STATE (op12) — GeneralState (82 полей)

```
alarm: dict
alarm.alarms: list
alarm.alarmsCounter: int
alarm.clocks: list
alarm.playing: NoneType
alarm.status: int
alarm.timers: list
assistant: dict
assistant.auto_volume: bool
assistant.character: str
background_apps: list
background_apps.[].app_info: dict
background_apps.[].app_info.frontendType: str
background_apps.[].app_info.systemName: str
background_apps.[].state: dict
capabilities_state: dict
capabilities_state.led_display: dict
capabilities_state.led_display.brightness: int
capabilities_state.led_display.turned_on: bool
current_app: dict
current_app.app_info: dict
current_app.state: dict
deviceGroups: dict
deviceGroups.soundBar: NoneType
deviceSelector: dict
deviceSelector.castGroup: list
deviceSelector.dsGroup: list
deviceSelector.enabled: bool
deviceSelector.features: list
deviceSelector.locked: bool
deviceSelector.qcGroup: list
deviceSelector.roomGroup: list
deviceSleep: dict
deviceSleep.systemState: str
device_segments: list
homeSecurity: dict
homeSecurity.enabled: bool
locale: dict
locale.locale: str
location: dict
location.accuracy: float
location.lat: float
location.lon: float
location.source: str
location.timestamp: int
morning_show: dict
morning_show.from_show: bool
morning_show.in_show: bool
multiroom: dict
multiroom.enabled: bool
multiroom.mode: str
multiroom.stereoPair: dict
multiroom.stereoPair.active: bool
multiroom.stereoPair.channelFromConfig: str
multiroom.stereoPair.pairDeviceFromConfig: str
network: dict
network.connection_type: str
network.ip: str
network.updated_timestamp_ms: int
proactivityNotification: dict
proactivityNotification.hasNotification: bool
reminders: dict
reminders.reminders: dict
reminders.reminders.time_reminders: dict
sbercast: dict
sbercast.devices: list
sbercast.enabled: bool
subscrDeviceInfo: dict
subscrDeviceInfo.isSubscrDevice: bool
time: dict
time.timestamp: int
time.timezone_id: str
time.timezone_offset_sec: int
timesync: dict
timesync.unixtime: float
user_settings: dict
user_settings.age_mode: str
user_settings.enable_child_voice_explicit: bool
user_settings.multi_profile: bool
volume: dict
volume.muted: bool
volume.percent: int
```

## GET_META_DATA (op10) — Metadata (23 полей)

```
artists: list
artists.[].id: str
artists.[].name: str
childMode: bool
explicit: bool
like: bool
mediaSource: str
playbackSpeedRate: float
playing: bool
playingPending: bool
playlistId: str
playlistLike: bool
playlistTitle: str
playlistType: str
provider: str
releases: list
releases.[].id: str
releases.[].name: str
repeatType: str
shuffle: bool
stateChangedTimestamp: int
title: str
trackId: str
```

## GET_PLAYING_QUEUE (op17) (2 полей)

```
explicit: bool
trackId: int
```

## Ключевое для HA

- `capabilities_state.led_display.{brightness,turned_on}` — реальные возможности (экран/подсветка).
- `multiroom.{enabled,mode,stereoPair.{active,channelFromConfig,pairDeviceFromConfig}}` — мультирум/стереопара.
- `deviceSelector.{castGroup,dsGroup,qcGroup,roomGroup,features,locked}` — группы устройств.
- `alarm.{alarms,clocks,timers,status,alarmsCounter}` — будильники/таймеры.
- `user_settings.{age_mode,enable_child_voice_explicit,multi_profile}` — профиль/родительский контроль.
- `network.{connection_type,ip}`, `location.{lat,lon,accuracy,source}`, `volume.{muted,percent}`.
