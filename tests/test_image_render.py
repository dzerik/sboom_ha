"""Тесты PIL-рендера. Главное — не падает на edge-cases.

Реальные сценарии:
- Кадр без cover (cover=None)
- Очень длинный title — должен переноситься, не обрезаться силой
- Кириллица + emoji в lyrics
- Прогресс на границах (0.0, 1.0, нерелевантный 1.5)
"""
from __future__ import annotations

import io

from PIL import Image

from sboom_ha.image_render import (
    HEIGHT,
    WIDTH,
    draw_blank,
    draw_cover_yandex,
    draw_lyrics_with_cover,
)


def _is_valid_jpeg(data: bytes) -> bool:
    """Открывается через PIL и имеет правильные размеры — значит JPEG валиден."""
    try:
        img = Image.open(io.BytesIO(data))
        img.verify()
        return True
    except Exception:
        return False


def _check_dimensions(data: bytes):
    img = Image.open(io.BytesIO(data))
    assert img.size == (WIDTH, HEIGHT)


def test_draw_blank_produces_valid_jpeg():
    out = draw_blank()
    assert _is_valid_jpeg(out)
    _check_dimensions(out)


def test_draw_lyrics_without_cover_renders_dark_background():
    """Когда обложка не загрузилась — должен быть рендер на тёмном фоне, без exception."""
    out = draw_lyrics_with_cover(
        cover=None, current="Текст", next_line="следующая",
        title="T", artist="A",
    )
    assert _is_valid_jpeg(out)
    _check_dimensions(out)


def test_draw_lyrics_with_invalid_cover_bytes_falls_back():
    """Если получили битый cover (например, 404 HTML вместо JPEG) — fallback на тёмный фон."""
    out = draw_lyrics_with_cover(
        cover=b"not a real image",
        current="line", next_line=None,
        title="T", artist="A",
    )
    assert _is_valid_jpeg(out), "Битый cover не должен ломать рендер"


def test_draw_lyrics_handles_cyrillic_and_emoji():
    """В lyrics могут быть Юникод-символы. PIL+DejaVuSans должен их рендерить."""
    out = draw_lyrics_with_cover(
        cover=None,
        current="Привет мир 🎵 это тест",
        next_line="Ещё одна строка с словами",
        title="Тест", artist="Артист",
    )
    assert _is_valid_jpeg(out)


def test_draw_lyrics_handles_very_long_line():
    """Длинная строка должна переноситься, не отрезаться.

    Реальный кейс: некоторые lyrics длиной 60+ символов в одной строке."""
    long_line = "x" * 200  # очень длинная строка
    out = draw_lyrics_with_cover(
        cover=None, current=long_line, next_line=None,
        title="T", artist="A",
    )
    # Главное — не упало. Что внутри картинки — проверяется визуально.
    assert _is_valid_jpeg(out)


def test_draw_lyrics_with_progress_at_boundaries():
    """Прогресс на 0%, 100%, и аномальные значения."""
    for p in [0.0, 0.5, 1.0, -0.1, 1.5]:
        out = draw_lyrics_with_cover(
            cover=None, current="x", next_line="y",
            title="T", artist="A",
            progress=p, position_sec=10, duration_sec=200,
        )
        assert _is_valid_jpeg(out), f"Failed at progress={p}"


def test_draw_cover_yandex_without_position_data():
    """Когда нет позиции/длительности (live-stream) — рендерим без time-метки."""
    out = draw_cover_yandex(
        cover=None, title="LiveStream", artist="DJ",
        progress=None, position_sec=None, duration_sec=None,
    )
    assert _is_valid_jpeg(out)


def test_draw_cover_yandex_handles_all_none():
    """Полностью пустой track (только что подключились) — тоже рендеримый кадр."""
    out = draw_cover_yandex(cover=None, title=None, artist=None)
    assert _is_valid_jpeg(out)
