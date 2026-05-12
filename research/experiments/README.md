# research/experiments/ — журнал живой разведки

Реальные скрипты, которые гонялись против live-устройства в сессии black-box discovery. Каждый файл — **один эксперимент** с конкретным findings'ом и docstring'ом, объясняющим что искали и что нашли. Read-and-learn — не production-код, а **сохранённый контекст** того как протокол был распакован эмпирически.

## Setup

```bash
# 1. Скопировать пример config'а и заполнить под своё устройство
cp _config.example.py _config.py
# ↑ отредактируй HOST/PORT/TOKEN (TOKEN получишь после exp_03)

# _config.py в .gitignore — твои токены не утекут в коммиты
```

## Sequence — как запускались

| # | Файл | Что искали | Финдинг |
|---|------|-----------|---------|
| 1 | `exp_01_multifield_envelope.py` | минимальный envelope (single-field probes молчат) | `{type=field(1,0,2), rid=field(2,2,uuid), body=field(5,2,...)}` |
| 2 | `exp_02_op_sweep_unauth.py` | какие ops работают без auth | op=4 = pair-init, op=5/6 = cancel/ack |
| 3 | `exp_03_pair_handshake.py` | получить токен через physical button | UUID-токен после нажатия `+` |
| 4 | `exp_04_op_sweep_authed.py` | все ops с auth — найти state-ops vs commands | op=10/12=state, op=16=command, op=17=queue |
| 5 | `exp_05_get_state_inspect.py` | структура GET_STATE и GET_METADATA | volume.muted/percent + 23 top-keys (alarm, multiroom, ...) |
| 6 | `exp_06_action_sweep.py` | семантика media-actions через diff state | 12 из 16 actions (mute/unmute/next/prev/play/pause/like/...) |
| 7 | `exp_07_dark_actions_probe.py` | action 4/8/11/15 в правильных state'ах | 4=play, 11=repeat_none confirmed; 8/15 без diff |
| 8 | `exp_08_subfield_fuzz_get_ops.py` | параметризуются ли op=10/12 | НЕТ для простых типов; string/bytes → timeout |
| 9 | `exp_09_pair_flow_brothers.py` | inner-fields op=4/5/6 | op=4 принимает строго empty; op=5=cancel; op=6=ack |
| 10 | `exp_10_push_subscribe_detect.py` | какой op активирует push-stream | **op=10 = SUBSCRIBE**: после запроса пушит unsolicited на media-events |
| 11 | `exp_11_push_subscribe_confirm.py` | что покрывается subscribe-stream | track/play/pause/volume — да; mute conditional |
| 12 | `exp_12_dark_actions_apps.py` | что делают action 8/14/15 | переупорядочивают **z-order стек** background_apps (focus management) |

## Финальная карта (для нашего устройства)

См. `../PROTOCOL.md` — research log с полным hop-by-hop восстановлением.

## Помощники

- `_config.py` — HOST/PORT/TOKEN + tag-роли (gitignored)
- `_config.example.py` — placeholder template
- `_helpers.py` — `make_envelope()`, `send_recv()`, `first_dict()`, `flatten()`

## Воспроизведение на новом устройстве

1. Запусти `01_discover.py` (родительский dir) или nmap `--port 20000` для поиска
2. Заполни HOST/PORT в `_config.py`
3. `python exp_01_multifield_envelope.py` → найди envelope tag-роли, обнови ENV_*_TAG в `_config.py`
4. `python exp_02_op_sweep_unauth.py` → найди pair-init op
5. `python exp_03_pair_handshake.py` → нажми кнопку, получи токен в `_config.py:TOKEN`
6. `python exp_04_op_sweep_authed.py` → все ops картируются
7. Дальше по sequence
