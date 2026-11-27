"""Кнопки Sber TV в карточке устройства и права при нажатии."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from sboom_ha import button
from sboom_ha.const import OPT_ENABLE_TV_CONTROLS

from tests._fakes import build_coordinator, make_entry

COMMANDS = ("show_inputs", "next_input", "channel_up", "channel_down", "settings")


async def _load_buttons(coord):
    entities = []
    await button.async_setup_entry(coord.hass, coord.entry, entities.extend)
    return {
        entity._attr_translation_key: entity
        for entity in entities
        if getattr(entity, "_attr_translation_key", "").startswith("tv_")
    }


async def test_tv_buttons_are_visible_only_on_opted_in_device():
    tv = build_coordinator(entry=make_entry(device_id="tv-serial"))
    speaker = build_coordinator(hass=tv.hass, entry=make_entry(entry_id="speaker"))
    assert not await _load_buttons(tv)

    tv.entry.options[OPT_ENABLE_TV_CONTROLS] = True
    entities = await _load_buttons(tv)

    assert set(entities) == {f"tv_{command}" for command in COMMANDS}
    for key, entity in entities.items():
        assert entity._attr_unique_id == f"sboom_ha_tv-serial_{key}"
        assert entity._attr_entity_registry_visible_default
        assert ("sboom_ha", "tv-serial") in entity._attr_device_info.identifiers
    assert not await _load_buttons(speaker)


async def test_switching_tv_off_and_on_preserves_customizations_and_user_disable(monkeypatch):
    tv = build_coordinator()
    renamed = SimpleNamespace(
        domain="button", platform="sboom_ha", translation_key="tv_settings",
        entity_id="button.my_settings", disabled_by=None, name="Мои настройки",
    )
    disabled = SimpleNamespace(
        domain="button", platform="sboom_ha", translation_key="tv_next_input",
        entity_id="button.no_next_input", disabled_by=er.RegistryEntryDisabler.USER,
    )
    ordinary = SimpleNamespace(
        domain="button", platform="sboom_ha", translation_key="next_track",
        entity_id="button.next_track", disabled_by=None,
    )
    rows = {entry.entity_id: entry for entry in (renamed, disabled, ordinary)}
    changes = []

    def update(entity_id, *, disabled_by):
        changes.append((entity_id, disabled_by))
        rows[entity_id].disabled_by = disabled_by

    registry = SimpleNamespace(async_update_entity=update)
    monkeypatch.setattr(er, "async_get", lambda hass: registry)

    def entries_for_config_entry(actual_registry, entry_id):
        assert actual_registry is registry
        assert entry_id == tv.entry.entry_id
        return rows.values()

    monkeypatch.setattr(er, "async_entries_for_config_entry", entries_for_config_entry)
    assert not await _load_buttons(tv)
    assert renamed.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    tv.entry.options[OPT_ENABLE_TV_CONTROLS] = True
    assert len(await _load_buttons(tv)) == len(COMMANDS)
    assert len(await _load_buttons(tv)) == len(COMMANDS)
    assert changes == [
        ("button.my_settings", er.RegistryEntryDisabler.INTEGRATION),
        ("button.my_settings", None),
    ]
    assert renamed.name == "Мои настройки"
    assert disabled.disabled_by is er.RegistryEntryDisabler.USER


@pytest.mark.parametrize("command", COMMANDS)
async def test_button_press_forwards_command_registry_device_and_user_context(command):
    tv = build_coordinator()
    tv.entry.options[OPT_ENABLE_TV_CONTROLS] = True
    entity = (await _load_buttons(tv))[f"tv_{command}"]
    entity.registry_entry = SimpleNamespace(device_id="ha-registry-device")
    context = SimpleNamespace(user_id="restricted-user")
    entity.async_set_context(context)
    tv.hass.services.async_call = AsyncMock(side_effect=HomeAssistantError("refused"))

    with pytest.raises(HomeAssistantError, match="refused"):
        await entity.async_press()

    tv.hass.services.async_call.assert_awaited_once_with(
        "sboom_ha", "tv_action", {"device_id": "ha-registry-device", "command": command},
        blocking=True, context=context,
    )


async def test_button_with_missing_device_fails_before_service_call():
    tv = build_coordinator()
    tv.entry.options[OPT_ENABLE_TV_CONTROLS] = True
    entity = (await _load_buttons(tv))["tv_settings"]
    tv.hass.services.async_call = AsyncMock()

    with pytest.raises(ServiceValidationError):
        await entity.async_press()

    tv.hass.services.async_call.assert_not_awaited()
