"""System Health для интеграции — Settings → System → System Information.

Показывает: количество сконфигурированных колонок, сколько подключено сейчас,
доступность Lrclib API.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components import system_health
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .coordinator import SboomCoordinator
from .lyrics_client import LRCLIB_BASE


@callback
def async_register(
    hass: HomeAssistant, register: system_health.SystemHealthRegistration
) -> None:
    register.async_register_info(system_health_info, "/api/integrations/sboom_ha")


async def system_health_info(hass: HomeAssistant) -> dict[str, Any]:
    coords: dict[str, SboomCoordinator] = hass.data.get(DOMAIN, {})
    total = len(coords)
    connected = sum(1 for c in coords.values() if c.connected)

    return {
        "configured_speakers": total,
        "connected_speakers": connected,
        "lrclib_reachable": system_health.async_check_can_reach_url(
            hass, LRCLIB_BASE
        ),
    }
