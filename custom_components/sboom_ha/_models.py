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
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class SpeakerState:
    volume_percent: int = 0
    muted: bool = False
    track: Optional[TrackInfo] = None
    raw_state_json: Optional[str] = None
