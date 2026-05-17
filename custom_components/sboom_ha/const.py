"""Константы интеграции sboom_ha."""
from __future__ import annotations

DOMAIN = "sboom_ha"
DEFAULT_NAME = "SberBoom"
DEFAULT_PORT = 20000
DEFAULT_USER_AGENT = "WebSocket++/0.8.2"

# Config entry keys
CONF_HOST = "host"
CONF_PORT = "port"
CONF_CLIENT_ID = "client_id"
CONF_CLIENT_NAME = "client_name"
CONF_PIN_ACCESS_TOKEN = "pin_access_token"

# Fallback значение шкалы громкости (0-100)
VOLUME_SCALE_MAX = 100

# Интервалы
RECONNECT_BACKOFF_SEC  = (1, 2, 5, 10, 30, 60)
PAIR_BUTTON_TIMEOUT_SEC = 120   # сколько ждать нажатия "+" на колонке

# Опкоды операций (теги полей в request_data).
OP_PIN_CONNECT       = 4
OP_GET_META_DATA     = 10
OP_GET_STATE         = 12
OP_FIND_REMOTE       = 13  # поиск пульта ДУ
OP_SET_VOLUME        = 14
OP_SET_TRACK_POS     = 15
OP_MEDIA_COMMAND     = 16
OP_GET_PLAYING_QUEUE = 17
OP_KEEP_ALIVE        = 18
OP_GET_PAIRED_BT     = 19  # список спаренных Bluetooth-устройств
OP_BT_DEVICE_COMMAND = 20  # команда BT-устройству (connect/disconnect/remove)
OP_GET_SCANNED_BT    = 21  # список найденных Bluetooth-устройств
OP_BT_DISCOVERABLE   = 22  # режим Bluetooth-сопряжения
OP_SET_PLAYBACK_SPEED = 23  # скорость воспроизведения, float-кодировка (research exp_22)

# Скорость воспроизведения: границы и пресеты для select-entity.
# 0.0 — битое состояние колонки (см. research exp_22), поэтому минимум 0.5.
PLAYBACK_SPEED_MIN = 0.5
PLAYBACK_SPEED_MAX = 2.0
PLAYBACK_SPEED_OPTIONS = ["0.5", "0.75", "1.0", "1.25", "1.5", "1.75", "2.0"]

# Опкоды медиа-команд (поле action в media-command-операции).
MEDIA_CMD_MUTE             = 0
MEDIA_CMD_UNMUTE           = 1
MEDIA_CMD_NEXT             = 2
MEDIA_CMD_PREV             = 3
MEDIA_CMD_PLAY             = 4
MEDIA_CMD_PAUSE            = 5
MEDIA_CMD_LIKE             = 6
MEDIA_CMD_REMOVE_LIKE      = 7
MEDIA_CMD_START_MULTIROOM  = 8
MEDIA_CMD_SHUFFLE_ON       = 9
MEDIA_CMD_SHUFFLE_OFF      = 10
MEDIA_CMD_REPEAT_NONE      = 11
MEDIA_CMD_REPEAT_PLAYLIST  = 12
MEDIA_CMD_REPEAT_TRACK     = 13
MEDIA_CMD_DISLIKE          = 14
MEDIA_CMD_REMOVE_DISLIKE   = 15

# Команды BT-устройству (поле cmd в op=20).
BT_CMD_CONNECT    = 0
BT_CMD_DISCONNECT = 1
BT_CMD_REMOVE     = 2

# Тип токена для PIN-сессии (единственный поддерживаемый этой интеграцией).
TOKEN_TYPE_PIN_AUTH = 1

# mDNS discovery
ZEROCONF_TYPE = "_staros._tcp.local."

# Свойства из mDNS TXT записей (которые публикует колонка)
MDNS_PROP_NAME      = "name"        # "SberBoom Home"
MDNS_PROP_TYPE      = "type"        # "sberboom-r2"
MDNS_PROP_ID        = "id"          # device serial / id
MDNS_PROP_FIRMWARE  = "v"           # firmware version

# Конфиг entry
CONF_DEVICE_ID       = "device_id"
CONF_DEVICE_MODEL    = "device_model"
CONF_DEVICE_NAME     = "device_name"
CONF_DEVICE_FIRMWARE = "device_firmware"

# Public image CDN (без auth). Параметры: type=release|track|artist, id, size=NxN.
ZVUK_IMAGE_CDN = "https://cdn-image.zvuk.com/pic"
COVER_SIZE = "600x600"

# Lyrics: сколько треков держим в LRU-кэше coordinator.
LYRICS_CACHE_MAX = 64

# ─── Опции (entry.options), редактируются через Options Flow ───
OPT_VOLUME_POLL_INTERVAL = "volume_poll_interval"      # секунды, default 5
OPT_AVAILABILITY_THRESHOLD = "availability_threshold"  # подряд неудач, default 3
OPT_KEEPALIVE_INTERVAL = "keepalive_interval"          # секунды, default 25
OPT_LYRICS_ENABLED = "lyrics_enabled"                  # bool, default True

# Default 15s — track changes приходят push-events через subscribe-stream
# (см. research/PROTOCOL.md), polling нужен только для volume/mute которые
# в push-stream НЕ попадают.
DEFAULT_VOLUME_POLL_INTERVAL = 15
DEFAULT_AVAILABILITY_THRESHOLD = 3
DEFAULT_KEEPALIVE_INTERVAL = 25
DEFAULT_LYRICS_ENABLED = True
