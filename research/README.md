# research/ — black-box discovery toolkit

Скрипты, которые **с нуля** восстанавливают неизвестный proprietary протокол на TLS-WebSocket. Базовое знание у нас — только публичные стандарты Internet (TLS, WebSocket, Google proto-wire encoding, JSON, mDNS). Никакого инсайда о вендоре, портах, сервис-именах, тег-ролях, опкодах. Всё нащупывается эмпирически.

## Что **знаем заранее** (это публичные стандарты, не инсайд)

- Бинарный формат varint+length-delimited — **Google proto-wire encoding**, документирован: https://protobuf.dev/programming-guides/encoding/. Декодим без `.proto` через `protoc --decode_raw` или своим 50-строчным декодером.
- WebSocket поверх TLS — RFC 6455.
- mDNS / Zeroconf — RFC 6762/6763.
- JSON — RFC 8259.

## Зависимости

```bash
pip install websockets zeroconf

# Опционально — для richer decode + type-learning + re-encode:
pip install bbpb        # https://github.com/nccgroup/blackboxprotobuf
```

## Полный pipeline (рекомендуется)

`auto_discover.py` — единый скрипт-оркестратор. Проходит все этапы в одном запуске. Промежуточные результаты сохраняются после каждого этапа в `protocol_map.json` (если упадёт/прервёшь — частичный результат на диске).

```bash
python research/auto_discover.py
# или с готовым host (если уже знаем):
python research/auto_discover.py --host <host> --port <port>
```

| Шаг | Что | Кто решает |
|-----|-----|------------|
| 1 | mDNS broad browse | автомат, оператор выбирает [0/1/2/N] из списка кандидатов |
| 2 | WS handshake probe | автомат |
| 3 | Initial passive capture | автомат |
| 4 | Envelope-fuzz (single-field probes 1..N tag) | автомат |
| 4a | **Auto-inference envelope tag-roles** (rid/body/client_id) — через эвристики echo + nested-vs-flat reply | автомат, без ввода оператора |
| 5 | **Pair discovery** | **интерактив**: после каждого op_tag — «устройство отреагировало физически? [y/N/q]». На `y` — «нажми кнопку pair», скрипт ждёт 90 секунд токена |
| 6 | **Auto-detect token-tag** + authorized op-sweep | автомат, без ввода оператора |
| 7 | **Action fuzz** | автомат-идентификация state-op/action-op по эвристикам, оператор подтверждает или override'ит, потом «запусти трек на устройстве [Y/n]», и скрипт делает diff JSON state до/после каждого `action=N` |

Финал — `protocol_map.json`:

```jsonc
{
  "host": "<host>", "port": <int>,
  "envelope_roles_inferred": {
    "rid_tag": <int>, "body_tag": <int>, "client_id_tag": <int|null>
  },
  "auth_envelope": {
    "token_tag": <int>, "token_type_tag": <int|null>, "token_type_value": <int>
  },
  "pair_op": <int>,
  "pin_token": "<long-printable-string>",
  "get_state_op": <int>,
  "media_command_op": <int>,
  "media_actions": {
    "0": {"diff": ["<some.field>: <before>→<after>"]},
    ...
  }
}
```

## Гранулярный workflow (отдельные шаги)

| # | Файл | Что делает | Вход | Выход |
|---|------|-----------|------|-------|
| 1 | `01_discover.py` | Полный mDNS broad-browse + опционально TCP-scan подсети. Не фильтруем по конкретному service-type — собираем ВСЁ что отзывается. | подсеть | список host:port + mDNS props |
| 2 | `02_probe.py` | Что отвечает порт. TLS info, попытка plain HTTP `GET /`, попытка WebSocket Upgrade. | host:port | summary: TLS/WS/HTTP/raw |
| 3 | `03_capture.py` | Подключаемся WS, отправляем минимальный пакет, слушаем входящий поток. Все сообщения через **generic proto-wire decoder** — древо tag→value без знания семантики. | host:port | hex-дампы + TLV-дерево |
| 4 | `04_fuzz_envelope.py` | Систематический перебор top-level tag-номеров. | host:port | таблица tag → behaviour |
| 5 | `05_pair_discovery.py` | Эвристика: ищем «init»-операцию через перебор op-tag'ов в body. Оператор подтверждает физическую реакцию. Извлекаем токен (longest printable string в reply). Tag-роли — required CLI args (берутся из вывода 04). | host:port + envelope tag-роли + физический доступ | auth-токен |
| 6 | `06_action_fuzz.py` | Через найденный action-op перебираем int-action-коды. Diff JSON state до/после каждого action даёт label семантики. Все tag-роли и op-номера — required CLI args. | host:port + token + envelope-роли + op-роли + играющий трек | таблица action → семантика |
| 7 | `07_deep_fuzz.py` | **Берёт `protocol_map.json` и расширяет**: 1) hole-sweep op-tag в широком диапазоне; 2) subfield-fuzz; 3) push-subscribe-trigger; 4) TTS-scan интерактивный; 5) extended action range. Поддерживает `--use-bbpb` для type-learning между сессиями + `--jsonl` для streaming-friendly event-log. | `protocol_map.json` | `protocol_map_extended.json` |

## Опциональная интеграция с `bbpb` (07_deep_fuzz)

`bbpb` (blackboxprotobuf от nccgroup) — это решённая задача «декодинг proto-wire без `.proto`-файла». Если установлен — `07_deep_fuzz.py` может использовать его вместо нашего self-contained декодера:

```bash
python research/07_deep_fuzz.py --map protocol_map.json --use-bbpb \
    --typedef typedef.json --jsonl events.jsonl
```

Преимущества:
- **Type-learning между сессиями** — `--typedef typedef.json` загружает накопленную схему в начале и сохраняет обновлённую в конце.
- **Re-encode capability** — bbpb умеет собрать обратно byte-точный payload из распарсенного дерева.
- **Streaming events.jsonl** — отдельный файл с tail-friendly лентой всех send/recv/error событий с timestamp + hex.

Без `--use-bbpb` всё работает на нашем self-contained декодере — никаких внешних deps.
