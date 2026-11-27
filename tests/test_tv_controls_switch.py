"""Управление настройкой телевизора с карточки устройства."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.const import EntityCategory
from homeassistant.exceptions import Unauthorized, UnknownUser
from sboom_ha.const import DOMAIN, OPT_ENABLE_TV_CONTROLS
from sboom_ha.switch import async_setup_entry

from tests._fakes import build_coordinator, make_entry


async def _device_switch(coordinator):
    entities = []
    await async_setup_entry(coordinator.hass, coordinator.entry, entities.extend)
    (switch,) = (
        entity
        for entity in entities
        if getattr(entity, "_attr_translation_key", None) == OPT_ENABLE_TV_CONTROLS
    )
    return switch


async def test_config_switch_is_on_its_device_even_when_disconnected():
    first = build_coordinator(entry=make_entry(device_id="bedroom", entry_id="a"))
    second = build_coordinator(hass=first.hass, entry=make_entry(device_id="kitchen", entry_id="b"))
    second.entry.options = {OPT_ENABLE_TV_CONTROLS: True}

    for coordinator, serial, enabled in [(first, "bedroom", False), (second, "kitchen", True)]:
        switch = await _device_switch(coordinator)
        assert not coordinator.connected
        assert switch.available
        assert switch._attr_entity_category == EntityCategory.CONFIG
        assert getattr(switch, "_attr_entity_registry_enabled_default", True)
        assert getattr(switch, "_attr_entity_registry_visible_default", True)
        assert (DOMAIN, serial) in switch._attr_device_info.identifiers
        assert switch._attr_unique_id == f"{DOMAIN}_{serial}_{OPT_ENABLE_TV_CONTROLS}"
        assert switch.is_on is enabled


@pytest.mark.parametrize("user_id", ["admin", None])
async def test_switch_updates_only_its_option_and_only_when_changed(user_id):
    coordinator = build_coordinator()
    coordinator.entry.options = {"panel_enabled": False}
    coordinator.async_execute = AsyncMock()
    hass = coordinator.hass
    hass.auth = SimpleNamespace(
        async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=True))
    )
    update = Mock(wraps=hass.config_entries.async_update_entry)
    hass.config_entries.async_update_entry = update
    switch = await _device_switch(coordinator)
    switch.async_set_context(SimpleNamespace(user_id=user_id))

    await switch.async_turn_off()
    update.assert_not_called()
    await switch.async_turn_on()
    await switch.async_turn_on()
    update.assert_called_once_with(
        coordinator.entry, options={"panel_enabled": False, OPT_ENABLE_TV_CONTROLS: True}
    )
    assert switch.is_on
    switch = await _device_switch(coordinator)
    assert switch.is_on
    switch.async_set_context(SimpleNamespace(user_id=user_id))
    await switch.async_turn_off()
    assert update.call_count == 2
    assert coordinator.entry.options == {"panel_enabled": False, OPT_ENABLE_TV_CONTROLS: False}
    assert not switch.is_on
    coordinator.async_execute.assert_not_awaited()
    if user_id is None:
        hass.auth.async_get_user.assert_not_awaited()


@pytest.mark.parametrize(
    ("user", "error"), [(None, UnknownUser), (SimpleNamespace(is_admin=False), Unauthorized)]
)
async def test_only_admin_can_change_entry_options(user, error):
    coordinator = build_coordinator()
    coordinator.hass.auth = SimpleNamespace(async_get_user=AsyncMock(return_value=user))
    switch = await _device_switch(coordinator)
    context = SimpleNamespace(user_id="guest")
    switch.async_set_context(context)

    with pytest.raises(error) as exc_info:
        await switch.async_turn_on()

    assert type(exc_info.value) is error
    assert exc_info.value.context is context
    assert not switch.is_on
    assert coordinator.entry.options == {}


async def test_overlapping_calls_keep_each_callers_permissions():
    coordinator = build_coordinator()
    users = {"guest": SimpleNamespace(is_admin=False), "admin": SimpleNamespace(is_admin=True)}
    coordinator.hass.auth = SimpleNamespace(async_get_user=AsyncMock(side_effect=users.get))
    switch = await _device_switch(coordinator)

    # HA 2024.12 устанавливает контекст на entity, затем планирует её coroutine.
    switch.async_set_context(SimpleNamespace(user_id="guest"))
    guest_call = asyncio.create_task(switch.async_turn_on())
    switch.async_set_context(SimpleNamespace(user_id="admin"))
    admin_call = asyncio.create_task(switch.async_turn_off())

    guest_result, admin_result = await asyncio.gather(guest_call, admin_call, return_exceptions=True)
    assert isinstance(guest_result, Unauthorized)
    assert admin_result is None
    assert coordinator.entry.options == {}
