"""Repairs platform — создаёт fixable/info issues для пользователя.

Активные issue видно в Settings → System → Repairs. Сейчас регистрируем
один тип: `unreachable_<entry_id>` (info) — колонка недоступна > 5 минут.
Issue удаляется автоматически когда coordinator восстанавливает связь.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.core import HomeAssistant


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, Any] | None,
) -> RepairsFlow:
    """Фабрика fix-flow для issue.

    Сейчас всё через ConfirmRepairFlow (юзер нажимает "Submit" → issue
    помечается как resolved). В будущем для fixable issue (например,
    invalid_token) можно вернуть кастомный RepairsFlow с шагами.
    """
    return ConfirmRepairFlow()
