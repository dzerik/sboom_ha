# Changelog

Все заметные изменения в этом проекте документируются здесь.
Формат основан на [Keep a Changelog](https://keepachangelog.com/),
версионирование — [SemVer](https://semver.org/).

## [0.7.2]

### Changed
- **Default `volume_poll_interval` поднят с 5s до 15s.** Эмпирически подтверждено в research-сессии (см. `research/PROTOCOL.md`): метаданные трека приходят **push-events** через subscribe-stream (активируется первым `get_metadata`-запросом). Polling нужен только для volume/mute, которые в push-stream НЕ попадают. 15s — разумный компромисс. Юзер может настроить через Options Flow если хочет иначе.
- В supervisor добавлен комментарий объясняющий subscribe-механизм.

### Migration
- Существующие установки с явно установленным `volume_poll_interval` в options — продолжат работать с тем значением что юзер выбрал. Только default-значение изменилось.

## [0.7.1]

### Fixed
- **Hassfest validation**: `services.yaml` исправлен — `target.device.integration` ушёл, вместо него `fields.device_id.selector.device.integration`. Тот же UX, корректный формат.

## [0.7.0]

Большое обновление жизненного цикла интеграции — закрыли почти все Bronze/Silver правила HA Quality Scale.

### Added
- **Diagnostics** — `Settings → Devices → SBoom → ⋮ → Download diagnostics` отдаёт JSON со state coordinator, track/state, options и entry.data (с redaction токена/host/serial).
- **Options Flow** — `Settings → Integrations → SBoom → Configure` для тонкой настройки: `volume_poll_interval`, `keepalive_interval`, `availability_threshold`, `lyrics_enabled`. Авто-reload при изменении.
- **Reconfigure Flow** — изменить IP/порт уже привязанной колонки без удаления интеграции (через ⋮ меню в Devices). PIN-токен сохраняется.
- **Reauth Flow** — переавторизация без удаления entry. Запускается через service `sboom_ha.reauth` или будет триггериться автоматически когда выяснится сигнал колонки на invalid token.
- **Repairs platform** — issue `speaker_unreachable` создаётся при недоступности колонки > 5 минут, удаляется при reconnect. Видно в `Settings → System → Repairs`.
- **System Health** — метрики `configured_speakers`, `connected_speakers`, `lrclib_reachable` в `Settings → System → System Information`.
- **Custom services**: `sboom_ha.refresh_metadata` (force-poll без ожидания цикла), `sboom_ha.reauth` (запустить переавторизацию). Поддержка таргетинга по device_id.
- **`zeroconf reload_on_update=True`** — при смене IP колонки через mDNS-rediscover интеграция перезагружается автоматически, client начинает ходить на новый адрес.
- **`async_migrate_entry` + `MINOR_VERSION=1`** — заглушка миграций config entry для будущих изменений формата.
- **`quality_scale: silver`** в manifest + `quality_scale.yaml` с пометками `done/todo/exempt` для движения к Gold.

### Changed
- **Хардкоды → опции**: `volume_poll_interval` (5s), `keepalive_interval` (25s), `availability_threshold` (3) перенесены из const.py в options. Дефолты сохранены.
- **Тестов**: 105 → **135** (+30). Добавлены `test_diagnostics`, `test_options`, `test_repairs`, `test_services`, `test_system_health`.
- **Stubs HA**: расширены до полной поддержки `issue_registry`, `system_health`, `services`, `device_registry.async_get`, `ServiceCall`, `RepairsFlow`, `ConfirmRepairFlow`.

## [0.6.6]

### Fixed
- **Brand-ассеты перенесены в правильное место**: HA 2026.3+ использует [Brands Proxy API](https://developers.home-assistant.io/blog/2026/02/24/brands-proxy-api/) — иконки кладутся в `custom_components/{domain}/brand/`, а `home-assistant/brands` репо больше **не принимает** PR для custom integrations. Папка `brand-assets/` (не туда положенная в 0.6.5) удалена, файлы переехали в `custom_components/sboom_ha/brand/{icon,icon@2x}.png`.
- Иконка теперь автоматически отображается в HA UI через `/api/brands/integration/sboom_ha/icon.png` — никаких сторонних PR не требуется.

## [0.6.5]

### Added
- **Brand-ассеты для HA UI**: `icon.png` (256×256) и `icon@2x.png` (512×512), заимствованные из `custom_integrations/sberdevices` репо [home-assistant/brands](https://github.com/home-assistant/brands).
- _Изначально файлы были в `brand-assets/`, в 0.6.6 переехали в правильное место `custom_components/sboom_ha/brand/`._

## [0.6.4]

### Added
- **Индикация недоступности колонки**: все entity (`media_player`, `camera`, `sensor`, `button`, `switch`, `number`, `select`) теперь показываются как `Unavailable` если WS-сессия с колонкой мертва. Реализовано через `SboomCoordinator.connected` + `SboomEntity.available`.
- **Event bus**: `sboom_connection_changed` (поля `connected`, `entry_id`, `device_id`, `host`) — для автоматизаций «колонка отвалилась → push в Telegram» и подобных.

### Changed
- **Debounce доступности (3 неудачные попытки)**: чтобы транзиентные мерцания сети не дёргали entity в `Unavailable` и обратно, флаг `connected` переключается в `False` только после **3 подряд** неудачных reconnect'ов (`DISCONNECT_THRESHOLD`). Успешный connect мгновенно возвращает entity в available-состояние.
- При `connected`-flip принудительно вызывается `async_update_listeners()` — UI перерисовывается мгновенно, без ожидания следующего poll-цикла.

## [0.6.3]

### Changed
- **CI**: обновлены GitHub Actions до Node.js 24-совместимых версий — `actions/checkout@v4 → @v6`, `actions/setup-python@v5 → @v6`. Убрано предупреждение о deprecated Node.js 20 (forced removal 16 сентября 2026).

## [0.6.2]

### Fixed
- **Hassfest validation**: ключи в `manifest.json` пересортированы (`domain`, `name`, далее алфавит) — требование Home Assistant для прохождения CI.

### Changed
- **CI**: убран `hacs/action` job — для приватного репо он бессмысленен (action ходит через публичный API без аутентификации). Hassfest и unit-тесты остались. Если репо когда-нибудь перейдёт в public и появится цель публиковаться через HACS-store — вернуть job обратно (см. git history) и добавить brand assets.

## [0.6.1]

### Added
- **Расширенный DeviceInfo** в карточке устройства HA: firmware (`sw_version`) из mDNS-broadcast'а колонки и серийный номер (`serial_number = device_id`). Теперь видно прошивку и серийный сразу в Settings → Devices.
- **Soft-migration** для существующих entries: при каждом zeroconf-rediscover (колонка регулярно сама анонсирует mDNS) firmware и model автоматически обновляются — старые установки получат firmware без переустановки.

### Fixed
- Firmware из mDNS извлекался при discovery, но **не сохранялся** в config entry — баг исправлен.

## [0.6.0]

### Added
- **HA event bus** — интеграция выпускает события `sboom_track_changed`, `sboom_playback_changed`, `sboom_volume_changed` при изменениях на колонке. Триггерится через `platform: event` в автоматизациях. См. README раздел «События для автоматизаций».

### Changed
- **Рефакторинг `api.py`** — выделены модули `_tlv.py` (бинарный кодек), `_parsers.py` (JSON-парсеры payload'ов), `_models.py` (dataclasses `TrackInfo`/`SpeakerState`). `api.py` уменьшился с 692 до 399 строк. Публичный API сохранён через re-export в `SberSpeakerClient.parse_track`/`parse_state`.
- **Тестовая инфраструктура** — добавлены `tests/_ha_stubs.py` (легковесные HA-stubs) и `tests/_fakes.py` (builders coordinator/track/state). Покрытие выросло с 48 до 94 unit-тестов: добавлены тесты `media_player` (entity properties), `_tlv` (изолированный кодек), `coordinator_events` (event bus).

### Internal
- `pyproject.toml` с конфигом для pytest и ruff (без `[project]` — версия остаётся в `manifest.json`).

## [0.5.3]

### Changed
- **Дополнительные сущности скрыты на dashboard по умолчанию** (`entity_registry_visible_default = False`):
  - 7 buttons (next/prev/play_pause/like/dislike/remove_like/remove_dislike)
  - 1 number (volume)
  - 2 switches (shuffle, mute)
  - 1 select (repeat mode)
- Эти entities остаются **enabled** (работают в автоматизациях, services, шаблонах) — просто не загромождают dashboard "Auto" / overview-карточки
- Главные видимые entities: `media_player` + `sensor.lyrics_current_line`
- Чтобы вернуть на dashboard: Settings → Devices → SBoom → нужная entity → toggle "Visible"

## [0.5.2]

### Added — first test suite
- 48 unit-тестов: `tests/test_helpers.py`, `test_lyrics_client.py`, `test_api_parsers.py`, `test_image_render.py`
- CI workflow для запуска тестов на каждом push (GitHub Actions)
- Синтетические фикстуры payload-форматов (push + state-обёртка)

## [0.5.1]

### Refactor — подготовка к HACS publication
- Вынесены общие утилиты в `helpers.py` (`track_position`, `cover_url`) — устранено дублирование между `sensor.py`, `camera.py`, `media_player.py`
- `SboomMediaPlayer` теперь наследует от общего `SboomEntity` (DRY device_info)
- `device_info` использует `DeviceInfo` dataclass вместо raw dict
- Все вызовы `asyncio.get_event_loop()` заменены на `asyncio.get_running_loop()`
- `aiohttp.ClientTimeout` вместо `int` в `camera._fetch_cover_raw` (совместимость с aiohttp 4.x)
- Публичные методы `media_remove_like()`, `media_remove_dislike()`, `parse_track()`, `parse_state()` вместо вызова приватных
- Использование `MEDIA_CMD_*` констант в `api.py` вместо magic numbers
- Публичное свойство `coordinator.http_session` вместо обращения к `_http`
- Исправлен двойной `client.close()` в `config_flow.py` pair-flow

## [0.5.0]

### Added
- Tighter polling: `update_interval` 5s (было 30s) — громкость подтягивается быстрее
- Прогресс-бар на camera — тонкая полоса + время `MM:SS / MM:SS` в стиле Яндекс.Музыки
- Lyrics retry с fallback по `artist+title` если с `album+duration` не найдено

### Fixed
- Lrclib network errors теперь не кэшируются как "не найдено" — следующий push даёт retry

## [0.4.1]

### Added
- Camera lyrics-overlay в стиле Яндекс.Музыки: blur-обложка + два уровня lyrics + footer с title/artist
- Idle-режим с большой обложкой по центру + подписи

## [0.4.0]

### Added
- **Camera entity** `camera.<name>_lyrics_na_tv` для karaoke-стрима
- MJPEG-стрим с двумя строками lyrics (текущая + следующая) с переключением по таймингу LRC
- Snapshot-mode для предпросмотра в HA UI
- Возможность отправки на ТВ через `media_player.play_media`
- Зависимость Pillow для PIL-рендера, шрифт DejaVuSans включён в пакет

## [0.3.0]

### Added
- **Lyrics через Lrclib.net** (open API, без auth, синхронизированные LRC)
- `sensor.<name>_lyrics_current_line` — текущая строка (тикает 1 Гц, state пишется только при смене)
- `sensor.<name>_lyrics` (диагностический, включается вручную) — полный текст в attributes
- Кэш lyrics в coordinator с фоновым fetch при смене трека

## [0.2.1]

### Fixed
- Добавлено `media_content_type=MUSIC` — теперь HA more-info показывает артиста в secondary-строке вместо `app_name`

## [0.2.0]

### Added
- Auto-discovery через Zeroconf (`_staros._tcp.local.`)
- MediaPlayer с volume/mute/play/pause/next/prev/seek/shuffle/repeat
- Cover image из public Zvuk CDN (`https://cdn-image.zvuk.com/pic`)
- Sub-plaекtforms: button (like/dislike/transport), number (volume), switch (shuffle/mute), select (repeat)

## [0.1.0]

### Added
- Первичная реализация через PIN_AUTH WebSocket-сессию
- Pair-flow с нажатием `+` на колонке
- Базовый media_player entity
