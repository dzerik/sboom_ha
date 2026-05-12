"""Минимальные stub'ы HA-классов для unit-тестирования entity без установки homeassistant.

Регистрируем поддельные модули в sys.modules ДО импорта sboom_ha.* — тогда любой
`from homeassistant.components.media_player import MediaPlayerEntity` в коде проекта
получит наш заглушечный класс.

Покрытие: только то, что реально используют тестируемые модули. Расширять по мере
добавления тестов на новые платформы.
"""
from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from enum import Enum, IntFlag
from typing import Any, Callable


# ── core ─────────────────────────────────────────────────────────────────

class _FakeBus:
    """Сборщик event'ов вместо реальной шины событий HA."""

    def __init__(self) -> None:
        self.fired: list[tuple[str, dict[str, Any]]] = []

    def async_fire(self, event_type: str, event_data: dict[str, Any] | None = None) -> None:
        self.fired.append((event_type, dict(event_data or {})))


def callback(fn):
    """Stub @callback decorator (no-op)."""
    return fn


@dataclass
class ServiceCall:
    """Stub для ServiceCall."""
    domain: str = ""
    service: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class _FakeDeviceRegistry:
    def __init__(self) -> None:
        self._devices: dict[str, Any] = {}

    def async_get(self, device_id: str):
        return self._devices.get(device_id)


_DR_INSTANCE: _FakeDeviceRegistry | None = None


def _async_get_device_registry(hass) -> _FakeDeviceRegistry:
    global _DR_INSTANCE
    if _DR_INSTANCE is None:
        _DR_INSTANCE = _FakeDeviceRegistry()
    return _DR_INSTANCE


class _FakeServices:
    def __init__(self) -> None:
        self._registered: dict[tuple[str, str], Any] = {}

    def has_service(self, domain: str, service: str) -> bool:
        return (domain, service) in self._registered

    def async_register(self, domain: str, service: str, handler) -> None:
        self._registered[(domain, service)] = handler


class HomeAssistant:
    """Stub HA: только bus + data + services + create_background_task."""

    def __init__(self) -> None:
        self.bus = _FakeBus()
        self.services = _FakeServices()
        self.data: dict[str, Any] = {}
        self._tasks: list[Any] = []

    def async_create_background_task(self, coro, name: str | None = None):
        # Не запускаем — храним для интроспекции и закрываем coroutine.
        try:
            coro.close()
        except Exception:  # pragma: no cover
            pass
        self._tasks.append(name)
        return None


# ── config_entries ───────────────────────────────────────────────────────

class ConfigEntry:
    def __init__(
        self,
        data: dict[str, Any] | None = None,
        entry_id: str = "test_entry",
        options: dict[str, Any] | None = None,
        title: str = "Test Entry",
        version: int = 1,
        minor_version: int = 1,
    ) -> None:
        self.data = dict(data or {})
        self.options = dict(options or {})
        self.entry_id = entry_id
        self.title = title
        self.version = version
        self.minor_version = minor_version

    def async_on_unload(self, fn) -> None:
        pass

    def add_update_listener(self, fn):
        return lambda: None


class ConfigFlow:
    """Заглушка для config_flow.SboomConfigFlow (не тестируется здесь)."""

    def __init_subclass__(cls, **kwargs) -> None:
        # Поглощаем `domain=DOMAIN` kwarg в `class SboomConfigFlow(ConfigFlow, domain=...)`.
        kwargs.pop("domain", None)
        super().__init_subclass__(**kwargs)


# ── helpers.device_registry ──────────────────────────────────────────────

@dataclass
class DeviceInfo:
    identifiers: set = field(default_factory=set)
    name: str = ""
    manufacturer: str = ""
    model: str = ""
    sw_version: str | None = None
    hw_version: str | None = None
    serial_number: str | None = None
    configuration_url: str = ""


@dataclass
class DeviceEntry:
    """Stub для diagnostics device-level."""
    id: str = "test-device-entry-id"
    identifiers: set = field(default_factory=set)


# ── helpers.issue_registry ──────────────────────────────────────────────

class _IssueSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    CRITICAL = "critical"


# Контейнер для созданных issue (для тестов)
_ISSUE_REGISTRY: dict[tuple[str, str], dict[str, Any]] = {}


def async_create_issue(
    hass,
    domain: str,
    issue_id: str,
    *,
    is_fixable: bool = False,
    severity: Any = _IssueSeverity.WARNING,
    translation_key: str = "",
    translation_placeholders: dict[str, str] | None = None,
    **kwargs,
) -> None:
    _ISSUE_REGISTRY[(domain, issue_id)] = {
        "is_fixable": is_fixable,
        "severity": severity,
        "translation_key": translation_key,
        "translation_placeholders": dict(translation_placeholders or {}),
    }


def async_delete_issue(hass, domain: str, issue_id: str) -> None:
    _ISSUE_REGISTRY.pop((domain, issue_id), None)


# ── components.repairs ──────────────────────────────────────────────────

class RepairsFlow:
    """Stub базового класса."""


class ConfirmRepairFlow(RepairsFlow):
    pass


# ── components.diagnostics ──────────────────────────────────────────────

def async_redact_data(data: Any, to_redact: set[str]) -> Any:
    """Рекурсивно заменяет значения по ключам в to_redact на '**REDACTED**'."""
    REDACTED = "**REDACTED**"
    if isinstance(data, dict):
        return {
            k: (REDACTED if k in to_redact else async_redact_data(v, to_redact))
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [async_redact_data(v, to_redact) for v in data]
    return data


# ── helpers.update_coordinator ───────────────────────────────────────────

class _Generic:
    """Mixin: позволяет писать `Class[T]` без typing.Generic."""

    def __class_getitem__(cls, item):
        return cls


class DataUpdateCoordinator(_Generic):
    def __init__(
        self,
        hass: HomeAssistant,
        logger,
        *,
        name: str = "",
        update_interval=None,
    ) -> None:
        self.hass = hass
        self.logger = logger
        self.name = name
        self.update_interval = update_interval
        self.data: Any = None
        self.last_update_success = True

    def async_set_updated_data(self, data: Any) -> None:
        self.data = data
        # эмулируем уведомление подписчиков (async_update_listeners)
        self._notify_listeners()

    def async_update_listeners(self) -> None:
        self._notify_listeners()

    def _notify_listeners(self) -> None:
        # счётчик для тестов; реальный HA дёргает callbacks
        self._listener_calls = getattr(self, "_listener_calls", 0) + 1

    async def async_request_refresh(self) -> None:
        pass


class CoordinatorEntity(_Generic):
    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator
        self.hass = getattr(coordinator, "hass", None)

    @property
    def available(self) -> bool:
        return True

    async def async_added_to_hass(self) -> None:
        pass

    def async_on_remove(self, fn) -> None:
        pass

    def async_write_ha_state(self) -> None:
        pass


# ── helpers.aiohttp_client / entity_platform / event ─────────────────────

def async_get_clientsession(hass):
    return None


AddEntitiesCallback = Callable[..., None]


def async_track_time_interval(hass, fn, interval):
    return lambda: None


# ── const / data_entry_flow ──────────────────────────────────────────────

class Platform(str, Enum):
    MEDIA_PLAYER = "media_player"
    BUTTON = "button"
    NUMBER = "number"
    SWITCH = "switch"
    SELECT = "select"
    SENSOR = "sensor"
    CAMERA = "camera"


class EntityCategory(str, Enum):
    DIAGNOSTIC = "diagnostic"
    CONFIG = "config"


CONTENT_TYPE_MULTIPART = "multipart/x-mixed-replace; boundary={}"


class FlowResult(dict):
    pass


# ── components.media_player ──────────────────────────────────────────────

class MediaPlayerState(str, Enum):
    OFF = "off"
    ON = "on"
    IDLE = "idle"
    PLAYING = "playing"
    PAUSED = "paused"
    STANDBY = "standby"
    BUFFERING = "buffering"


class MediaType(str, Enum):
    MUSIC = "music"
    TVSHOW = "tvshow"
    MOVIE = "movie"


class RepeatMode(str, Enum):
    OFF = "off"
    ALL = "all"
    ONE = "one"


class MediaPlayerEntityFeature(IntFlag):
    PAUSE = 1
    SEEK = 2
    VOLUME_SET = 4
    VOLUME_MUTE = 8
    PREVIOUS_TRACK = 16
    NEXT_TRACK = 32
    VOLUME_STEP = 1024
    PLAY = 16384
    SHUFFLE_SET = 32768
    REPEAT_SET = 262144


class MediaPlayerEntity:
    """Пустой stub — атрибуты берутся через дескрипторы свойств в SboomMediaPlayer."""


# ── components.{camera,sensor,button,number,switch,select} ───────────────

class Camera:
    _attr_entity_registry_enabled_default = True

    def __init__(self) -> None:
        pass


class SensorEntity:
    pass


class ButtonEntity:
    pass


class NumberEntity:
    pass


class SwitchEntity:
    pass


class SelectEntity:
    pass


# ── service_info.zeroconf ────────────────────────────────────────────────

@dataclass
class ZeroconfServiceInfo:
    host: str = ""
    port: int = 0
    properties: dict[str, Any] = field(default_factory=dict)


# ── регистрация в sys.modules ────────────────────────────────────────────

def _make_module(name: str, **attrs: Any) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


def install_stubs() -> None:
    """Зарегистрировать stub-модули. Идемпотентно."""
    if "homeassistant" in sys.modules:
        return

    _make_module("homeassistant")
    _make_module(
        "homeassistant.const",
        Platform=Platform,
        EntityCategory=EntityCategory,
        CONTENT_TYPE_MULTIPART=CONTENT_TYPE_MULTIPART,
    )
    _make_module(
        "homeassistant.core",
        HomeAssistant=HomeAssistant,
        callback=callback,
        ServiceCall=ServiceCall,
    )
    _make_module(
        "homeassistant.config_entries",
        ConfigEntry=ConfigEntry,
        ConfigFlow=ConfigFlow,
    )
    _make_module("homeassistant.data_entry_flow", FlowResult=FlowResult)

    _make_module("homeassistant.helpers")
    _make_module(
        "homeassistant.helpers.update_coordinator",
        DataUpdateCoordinator=DataUpdateCoordinator,
        CoordinatorEntity=CoordinatorEntity,
    )
    _make_module(
        "homeassistant.helpers.device_registry",
        DeviceInfo=DeviceInfo,
        DeviceEntry=DeviceEntry,
        async_get=_async_get_device_registry,
    )
    _make_module(
        "homeassistant.helpers.aiohttp_client",
        async_get_clientsession=async_get_clientsession,
    )
    _make_module(
        "homeassistant.helpers.entity_platform",
        AddEntitiesCallback=AddEntitiesCallback,
    )
    _make_module(
        "homeassistant.helpers.event",
        async_track_time_interval=async_track_time_interval,
    )
    _make_module("homeassistant.helpers.service_info")
    _make_module(
        "homeassistant.helpers.service_info.zeroconf",
        ZeroconfServiceInfo=ZeroconfServiceInfo,
    )

    _make_module("homeassistant.components")
    _make_module(
        "homeassistant.components.media_player",
        MediaPlayerEntity=MediaPlayerEntity,
        MediaPlayerEntityFeature=MediaPlayerEntityFeature,
        MediaPlayerState=MediaPlayerState,
        MediaType=MediaType,
        RepeatMode=RepeatMode,
    )
    _make_module("homeassistant.components.camera", Camera=Camera)
    _make_module("homeassistant.components.sensor", SensorEntity=SensorEntity)
    _make_module("homeassistant.components.button", ButtonEntity=ButtonEntity)
    _make_module("homeassistant.components.number", NumberEntity=NumberEntity)
    _make_module("homeassistant.components.switch", SwitchEntity=SwitchEntity)
    _make_module("homeassistant.components.select", SelectEntity=SelectEntity)
    _make_module(
        "homeassistant.components.zeroconf",
        ZeroconfServiceInfo=ZeroconfServiceInfo,
    )
    _make_module(
        "homeassistant.components.diagnostics",
        async_redact_data=async_redact_data,
    )
    # Создаём подмодуль issue_registry с правильными типами
    issue_registry_mod = _make_module(
        "homeassistant.helpers.issue_registry",
        async_create_issue=async_create_issue,
        async_delete_issue=async_delete_issue,
        IssueSeverity=_IssueSeverity,
    )
    _make_module(
        "homeassistant.components.repairs",
        RepairsFlow=RepairsFlow,
        ConfirmRepairFlow=ConfirmRepairFlow,
    )

    class _SystemHealthRegistration:
        def __init__(self) -> None:
            self.info_callback = None
            self.manage_url = None

        def async_register_info(self, callback_, manage_url=None):
            self.info_callback = callback_
            self.manage_url = manage_url

    def _async_check_can_reach_url(hass, url):
        return "ok"

    _make_module(
        "homeassistant.components.system_health",
        SystemHealthRegistration=_SystemHealthRegistration,
        async_check_can_reach_url=_async_check_can_reach_url,
    )
