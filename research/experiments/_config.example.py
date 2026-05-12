"""Шаблон config для experiments/. Копируй в `_config.py` и заполняй.

`_config.py` в .gitignore — не утечёт.
"""
from __future__ import annotations

HOST = "<DEVICE_IP>"
PORT = 0  # узнать через research/01_discover.py или nmap
TOKEN = "<PIN_TOKEN_FROM_PAIR>"  # получить через exp_03_pair_handshake.py

# Envelope tag-роли — для нашего устройства нащупаны exp_01.
# Для нового устройства — re-run exp_01 (multifield envelope hypothesis).
ENV_TYPE_TAG = 1
ENV_RID_TAG = 2
ENV_TOKEN_TAG = 3
ENV_BODY_TAG = 5
ENV_TOKEN_TYPE_TAG = 6
ENV_CLIENT_NAME_TAG = 7
ENV_IS_REQUEST_TAG = 10
ENV_CID_TAG = 11

OP_PAIR_INIT = 4
OP_PAIR_CANCEL = 5
OP_PAIR_ACK = 6
OP_GET_METADATA = 10
OP_GET_STATE = 12
OP_MEDIA_COMMAND = 16
OP_GET_QUEUE = 17
