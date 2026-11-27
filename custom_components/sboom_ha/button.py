"""Button-entities для лайков и других one-shot команд."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ._entity_base import SboomEntity, SboomTVControlEntity
from .const import DOMAIN, OPT_ENABLE_TV_CONTROLS
from .coordinator import SboomCoordinator
from .services import SERVICE_TV_ACTION, TV_ACTION_LINKS

# Команды идут к колонке через единый WS с собственным lock — HA-параллелизм не нужен.
PARALLEL_UPDATES = 0
_TV_BUTTON_KEYS = frozenset(f"tv_{command}" for command in TV_ACTION_LINKS)


@dataclass(frozen=True, kw_only=True)
class SboomButton(ButtonEntityDescription):
    """Кнопка-команда колонки."""

    press_fn: Callable[[SboomCoordinator], Awaitable[None]]


async def _play_pause(c) -> None:
    """Toggle: если играет → пауза, иначе play."""
    if c.track and c.track.playing:
        await c.client.media_pause()
    else:
        await c.client.media_play()


BUTTONS: tuple[SboomButton, ...] = (
    SboomButton(
        key="next_track",
        translation_key="next_track",
        icon="mdi:skip-next",
        press_fn=lambda c: c.client.media_next(),
    ),
    SboomButton(
        key="previous_track",
        translation_key="previous_track",
        icon="mdi:skip-previous",
        press_fn=lambda c: c.client.media_prev(),
    ),
    SboomButton(
        key="play_pause",
        translation_key="play_pause",
        icon="mdi:play-pause",
        press_fn=_play_pause,
    ),
    SboomButton(
        key="like",
        translation_key="like",
        icon="mdi:heart",
        press_fn=lambda c: c.client.media_like(),
    ),
    SboomButton(
        key="dislike",
        translation_key="dislike",
        icon="mdi:heart-broken",
        press_fn=lambda c: c.client.media_dislike(),
    ),
    SboomButton(
        key="remove_like",
        translation_key="remove_like",
        icon="mdi:heart-off",
        press_fn=lambda c: c.client.media_remove_like(),
    ),
    SboomButton(
        key="remove_dislike",
        translation_key="remove_dislike",
        icon="mdi:close-circle-outline",
        press_fn=lambda c: c.client.media_remove_dislike(),
    ),
    SboomButton(
        key="bt_pairing",
        translation_key="bt_pairing",
        icon="mdi:bluetooth",
        press_fn=lambda c: c.client.bt_make_discoverable(),
    ),
    SboomButton(
        key="find_remote",
        translation_key="find_remote",
        icon="mdi:remote",
        press_fn=lambda c: c.client.find_remote(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SboomCoordinator = entry.runtime_data
    tv_enabled = entry.options.get(OPT_ENABLE_TV_CONTROLS, False)
    registry = er.async_get(hass)
    for registered in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            registered.domain != "button"
            or registered.platform != DOMAIN
            or registered.translation_key not in _TV_BUTTON_KEYS
        ):
            continue
        if tv_enabled and registered.disabled_by is er.RegistryEntryDisabler.INTEGRATION:
            registry.async_update_entity(registered.entity_id, disabled_by=None)
        elif not tv_enabled and registered.disabled_by is None:
            registry.async_update_entity(
                registered.entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION
            )

    buttons: list[ButtonEntity] = [SboomButtonEntity(coordinator, entry, desc) for desc in BUTTONS]
    if tv_enabled:
        buttons.extend(SboomTvButton(coordinator, entry, command) for command in TV_ACTION_LINKS)
    async_add_entities(buttons)


class SboomButtonEntity(SboomEntity, ButtonEntity):
    # Кнопки доступны через media_player (play/pause/next/prev) и сервисы
    # like/dislike — на dashboard "Auto" не показываем, чтобы не дублировать.
    # Юзер может включить отображение в Settings → Entities → Visible.
    _attr_entity_registry_visible_default = False

    def __init__(
        self,
        coordinator: SboomCoordinator,
        entry: ConfigEntry,
        description: SboomButton,
    ) -> None:
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._attr_unique_id = f"{self._device_unique_prefix}_{description.key}"

    async def async_press(self) -> None:
        await self._run_command(
            self.entity_description.press_fn(self.coordinator),
            action=self.entity_description.key,
        )


class SboomTvButton(SboomTVControlEntity, ButtonEntity):
    """Кнопка одного ТВ-действия на карточке сопряжённого устройства."""

    _attr_icon = "mdi:remote-tv"
    _attr_entity_registry_visible_default = True

    def __init__(self, coordinator: SboomCoordinator, entry: ConfigEntry, command: str) -> None:
        super().__init__(coordinator, entry)
        self._command = command
        self._attr_translation_key = f"tv_{command}"
        self._attr_unique_id = f"{self._device_unique_prefix}_{self._attr_translation_key}"

    async def async_press(self) -> None:
        if not self.registry_entry or not self.registry_entry.device_id:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="tv_device_required"
            )
        await self.hass.services.async_call(
            DOMAIN, SERVICE_TV_ACTION,
            {"device_id": self.registry_entry.device_id, "command": self._command},
            blocking=True, context=self.tv_call_context,
        )
