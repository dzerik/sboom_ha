"""Общие утилиты для media_player, sensor, camera."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .const import COVER_SIZE, ZVUK_IMAGE_CDN

if TYPE_CHECKING:
    from .api import TrackInfo
    from .coordinator import SboomCoordinator


# Защита от мусорных значений position_ts_ms (например при reboot колонки).
_MAX_EXTRAPOLATION_SEC = 600


def track_position(coordinator: SboomCoordinator) -> float | None:
    """Текущая позиция трека в секундах с экстраполяцией от position_ts_ms.

    Колонка отдаёт позицию + timestamp последнего обновления. Между push'ами
    позицию экстраполируем по wall-clock; ограничиваем диапазон чтобы не
    "уехать" при долгом отсутствии push'ей или после reboot колонки.
    """
    track = coordinator.track
    if not track or track.position_sec is None:
        return None
    pos = float(track.position_sec)
    if track.playing and track.position_ts_ms:
        delta = (datetime.now(timezone.utc).timestamp() * 1000 - track.position_ts_ms) / 1000.0
        if 0 <= delta < _MAX_EXTRAPOLATION_SEC:
            pos += delta
    if track.duration_sec:
        pos = min(pos, float(track.duration_sec))
    return pos


def cover_url(track: TrackInfo) -> str | None:
    """URL обложки трека из public Zvuk CDN (без auth)."""
    if not track or track.provider != "zvuk":
        return None
    if track.release_id:
        return f"{ZVUK_IMAGE_CDN}?type=release&id={track.release_id}&size={COVER_SIZE}"
    if track.artist_ids:
        return f"{ZVUK_IMAGE_CDN}?type=artist&id={track.artist_ids[0]}&size={COVER_SIZE}"
    return None
