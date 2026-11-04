# Что перенять из sboom_node

Проверено 2026-11-04. Сравнение исходников `sergvax/sboom_node` версии 1.1.0,
commit [`a647c4646bd4ee743cd3fadc621dfa33964a1cb4`](https://github.com/sergvax/sboom_node/tree/a647c4646bd4ee743cd3fadc621dfa33964a1cb4),
с локальным `sboom_ha` v0.37.5, HEAD `9d3797373de38111c14aba2e04d402626132dfd7`.
Исследован весь пользовательский интерфейс, набор HTTP routes, клиент протокола,
парсеры, каталог, менеджер соединений, мультирум, история, mock и тесты.
Изменений в код интеграции в рамках этого исследования не вносилось.

## Вывод

Перенять можно прежде всего программный мультирум, полноценную Bluetooth-панель,
историю успешных запусков и отдельные способы повышения устойчивости каталога.
Большая часть базового протокола уже есть у нас. У `sboom_node` нет найденной
команды включения/выключения питания Bluetooth, редактирования очереди,
локального эквалайзера или нативного SberCast-мультирума.

Происхождение подтверждено самим исходным проектом: [README, «Как это работает»](https://github.com/sergvax/sboom_node/blob/a647c4646bd4ee743cd3fadc621dfa33964a1cb4/README.md#как-это-работает)
называет `dzerik/sboom_ha` основой, а `TohaRG2/sboom_ha` — источником Bluetooth-опкодов;
`lib/protocol.js`, `lib/parsers.js`, `lib/speaker.js` явно описаны как порты наших модулей.
[LICENSE](https://github.com/sergvax/sboom_node/blob/a647c4646bd4ee743cd3fadc621dfa33964a1cb4/LICENSE)
— MIT с требованием сохранять copyright и permission notice при копировании
существенных частей. Адаптировать алгоритм под Python/HA нужно отдельно от
копирования Express-сервера или браузерной оболочки.

## Полный инвентарь возможностей

«Есть у нас» относится к указанному локальному HEAD; внешний вид и способ вызова
могут отличаться. «Нет у них» означает отсутствие реализации в проверенном
пользовательском интерфейсе, HTTP routes и соответствующем командном слое,
а не утверждение о возможностях самой прошивки.

| Возможность sboom_node | Что реализовано в исходниках | Сопоставление с sboom_ha / ценность переноса |
| --- | --- | --- |
| Поиск колонок | mDNS `_staros._tcp`, имя, IP, модель, прошивка; ручной host/port, нормализация `wss://` | Zeroconf и ручная настройка уже есть; отдельная функция не новая |
| Сопряжение | `op=4`, физическая кнопка `+`, таймаут 120с, повторное сопряжение, статусы в UI | Уже есть config flow и reauth |
| Управление списком колонок | Добавить, удалить, переименовать локальное имя контроллера; JSON token store | Эквивалентно config entries/названиям HA; это не переименование колонки в прошивке |
| Соединение | Один runtime/WS на колонку, supervisor, reconnect/backoff/jitter, WS ping/pong, обязательный state sync | Основной механизм уже есть; сравнивать отдельные условия восстановления, а не переносить целиком |
| Плеер | Play/pause/toggle, next/prev, seek, volume/mute/toggle, shuffle, repeat | Уже есть; toggle выводится из состояния |
| Оценки | Like/unlike и dislike через HTTP command; UI кнопка like | Уже есть; у нас дополнительно есть remove_dislike |
| Скорость | `SpeakerClient.setPlaybackSpeed()` (`op=23`) | В manager command switch, HTTP-UX и UI выбора скорости не включено; у нас уже есть select |
| Поиск пульта | `findRemote` доступен как generic HTTP command | Уже есть button/op13; отдельной кнопки в их карточке нет |
| Текущий трек | Title/artists/album/cover, позиция, duration, buffering, explicit, источник, playlist context | Уже есть, включая расширенные датчики |
| BT/радио now-playing | Поиск `bluetooth_media_control`/`music` в `current_app` и `background_apps`, fallback из GET_STATE | Уже есть у нас; переносить повторно не нужно. У нас дополнительно обложки/lyrics для BT |
| Каталог Звука | Одновременные REST `/api/tiny/search` и GraphQL, объединение и дедупликация разделов | У нас поиск/каталог есть; параллельный запасной канал и merge — кандидат на улучшение |
| Разделы каталога | Поиск track/playlist/artist/release; карточка результата запускает контент | У нас дополнительно есть drill-down по исполнителям/альбомам; их UI не является более полным браузером каталога |
| Запуск по ссылке/ID | `staros://music`: track/playlist/artist/release/podcast; неоднозначный голый ID даёт ошибку с кандидатами | Уже есть URL/query/id и kind; проверка неоднозначности полезна отдельно |
| Подкасты/книги | `PLAY_KINDS` содержит podcast; URL `/episode/` и `/book/` сводятся к podcast | Это не отдельная полноценная реализация abook; у нас kind abook уже разрешён |
| Волна/радио | Отображение уже играющего radio; мультирум может повторять текущий MUSIC track из волны | Новых команд выбора радиостанции или запуска волны в их UI/HTTP API не найдено |
| Каталог без сети | Полные типизированные ссылки превращаются в deeplink без запроса каталога; голый ID требует resolve | Уже есть значительная часть пути; полезно сохранить его независимость от поиска |
| Очередь | `op=17`, получение ID, обогащение названиями/обложками/длительностью, отметка текущего трека | Просмотр/каталог очереди уже есть; UI в деталях можно сравнить |
| «Играть» из очереди | Клик строки вызывает `playContent({kind:'track',id})` | Это новый запуск отдельного трека с заменой контекста, а не переход к позиции/редактирование существующей очереди |
| Редактирование очереди | Append, remove, move/reorder, clear в коде отсутствуют | Заимствовать такую функцию из этого проекта нельзя |
| История запусков | Общий файл `search-history.json`, recent/frequent, counters, лимит, clear, атомарная запись | Отсутствует у нас как история успешных запусков; хороший независимый кандидат |
| Bluetooth-сопряжение | `op=22`, fire-and-forget, кнопка и индикатор видимости | Команда и скрытая button `bt_pairing` у нас уже есть; ценность — сделать функцию доступной в панели |
| Список BT | `op=19`: MAC, имя, connected; refresh и действия возле каждой строки | Низкоуровневый запрос/сервис у нас есть; отдельная удобная панель — кандидат |
| BT-действия | `op=20`: connect/disconnect/remove, optimistic UI, перечитывание списка после 600мс | Команды у нас уже есть; UI и подтверждение результата можно улучшить |
| Скан Bluetooth | `op=21`, parser repeated TLV, соседние устройства с RSSI, 15с timeout, запрет параллельного scan | У нас уже есть OP_GET_SCANNED_BT и parse_scanned_bt; нет метода отправки запроса и пользовательского интерфейса. Нужно завершить существующий путь |
| Питание Bluetooth | Отдельного radio power/enable/disable метода нет | Не путать с discoverable. Нельзя обосновать switch Bluetooth on/off их op22 |
| Общие команды | `/api/group/command`, broadcast по выбранным IDs/всем online; UI «все колонки»: play/pause, mute, volume | Multi-target HA services/play_music уже есть. Новая ценность — выбор нескольких колонок и общая панель |
| Программный мультирум | Один источник, все прочие online как ведомые; deeplink того же track, mute/seek/alignment, monitor | Существенно новая функция поверх нашего существующего транспорта |
| Управление мультирумом | Start/stop/leave, группы в UI, commands от участника перенаправляются группе; next/prev ведёт источник | У нас только информация о мультируме/зарезервированная action8; требуется отдельный Python module/coordinator |
| Нативный мультирум | Нативные SberCast/PTP группы не создаются, `START_MULTIROOM` не используется | Не новая расшифровка протокола; это синхронизация независимых потоков |
| Эквалайзер/настройки | Новых setters EQ, микрофона, Wi-Fi, timezone, LEDs, родительского контроля не найдено | У нас EQ уже выводит облачные entities HA-sberhome; этот проект не даёт локальной альтернативы |
| Будильники/таймеры/календарь | Пользовательской реализации нет | У нас более развитые HA-платформы и чтение schedule; переносить нечего |
| Живой UI | SSE snapshots + 15с HTTP fallback, локальная анимация progress, optimistic controls, dialogs BT/search/queue | HA уже имеет свою подписку WebSocket; переносить UX-приёмы, не второй сервер/SSE |
| Диагностика | CLI-скрипты замеров seek/pause/позиции и мультирума в `diag/` | Полезны методики измерений. Экспорт настроек/диагностики в продукте не найден; HA diagnostics уже есть |
| Mock/тесты | Мок WSS-колонки с pair/state/metadata/queue/BT/lag; parser/unit/e2e/UI tests, CI Node20/22 | Хороший источник сценариев для наших тестов без HA/железа |

Источники инвентаря:
[HTTP routes](https://github.com/sergvax/sboom_node/blob/a647c4646bd4ee743cd3fadc621dfa33964a1cb4/server.js#L49-L223),
[полный command switch](https://github.com/sergvax/sboom_node/blob/a647c4646bd4ee743cd3fadc621dfa33964a1cb4/lib/manager.js#L736-L839),
[клиент команд](https://github.com/sergvax/sboom_node/blob/a647c4646bd4ee743cd3fadc621dfa33964a1cb4/lib/speaker.js#L434-L533),
[карточка колонки](https://github.com/sergvax/sboom_node/blob/a647c4646bd4ee743cd3fadc621dfa33964a1cb4/public/index.html#L260-L341),
[каталог и допустимые типы](https://github.com/sergvax/sboom_node/blob/a647c4646bd4ee743cd3fadc621dfa33964a1cb4/lib/zvuk.js#L25-L113),
[парсинг источника](https://github.com/sergvax/sboom_node/blob/a647c4646bd4ee743cd3fadc621dfa33964a1cb4/lib/parsers.js#L36-L74),
[discovery](https://github.com/sergvax/sboom_node/blob/a647c4646bd4ee743cd3fadc621dfa33964a1cb4/lib/discovery.js),
[diagnostic scripts](https://github.com/sergvax/sboom_node/tree/a647c4646bd4ee743cd3fadc621dfa33964a1cb4/diag).

## Bluetooth: точные сообщения и ограничения

Все сообщения идут по существующему WSS, не требуют BT-адаптера на HA-сервере.
Внутри request_data:

| Операция | Запрос | Ответ/подтверждение |
| --- | --- | --- |
| Список paired, op19 | outer field19 length-delimited → field1 empty message | envelope field5 → op19 → repeated field1 device; device: field1 MAC, field2 name, field3 connected |
| Scan, op21 | outer field21 length-delimited → field1 empty message | envelope field5 → op21 → repeated field2 device; device: field1 MAC, field2 name, field3 RSSI |
| Discoverable, op22 | outer field22 length-delimited → field1 empty message | fire-and-forget; подтверждённое состояние радиомодуля не читается |
| Device command, op20 | outer field20 → field1 MAC string + field2 command varint | connect=0, disconnect=1, remove=2; fire-and-forget, затем op19 |

Доказательства:
[формирование запросов](https://github.com/sergvax/sboom_node/blob/a647c4646bd4ee743cd3fadc621dfa33964a1cb4/lib/speaker.js#L52-L58),
[методы BT](https://github.com/sergvax/sboom_node/blob/a647c4646bd4ee743cd3fadc621dfa33964a1cb4/lib/speaker.js#L449-L478),
[BT parser](https://github.com/sergvax/sboom_node/blob/a647c4646bd4ee743cd3fadc621dfa33964a1cb4/lib/parsers.js#L194-L246),
[BT command enum](https://github.com/sergvax/sboom_node/blob/a647c4646bd4ee743cd3fadc621dfa33964a1cb4/lib/protocol.js#L23-L28).

Индикатор discoverable у них вычисляется как `discoverableAt + 120_000`,
а не читается из прошивки. После connect/disconnect/remove при ошибке op19
остаётся optimistic список. Эти состояния нельзя переносить в HA как достоверные
показания. В mock RSSI проверяется положительным значением `42`; формат и знак
RSSI на реальном устройстве следует проверить отдельным захватом.
([runtime BT state](https://github.com/sergvax/sboom_node/blob/a647c4646bd4ee743cd3fadc621dfa33964a1cb4/lib/manager.js#L32-L33),
[snapshot](https://github.com/sergvax/sboom_node/blob/a647c4646bd4ee743cd3fadc621dfa33964a1cb4/lib/manager.js#L144-L167),
[команды/перечитывание](https://github.com/sergvax/sboom_node/blob/a647c4646bd4ee743cd3fadc621dfa33964a1cb4/lib/manager.js#L657-L689),
[mock-тест](https://github.com/sergvax/sboom_node/blob/a647c4646bd4ee743cd3fadc621dfa33964a1cb4/test/bluetooth.test.js#L49-L53).)

## Мультирум: что действительно стоит перенести

`MultiroomController` — не новая команда прошивки. Он запускает одинаковый
трек Звука на независимых колонках и управляет их положением:

1. Источник должен иметь trackId; источник `BLUETOOTH`/`RADIO` отклоняется.
2. Все остальные online-колонки становятся кандидатами; перед deeplink ведомые
   глушатся с сохранением прежнего mute. Появление trackId недостаточно: ждут
   реально движущуюся позицию, поскольку seek во время buffering может потеряться.
3. На играющем источнике звук продолжается. Ведомые выравниваются seek с
   оценкой задержки применения команды и снимаются с mute только после выравнивания.
4. Позицию меряют через GET_STATE, учитывают midpoint RTT; short cache 150мс
   уменьшает лишние запросы. Фаза выводится по переходам целых секунд, а не по
   одному округлённому измерению.
5. Monitor имеет дедлайны и защиту от перекрытия. Каждые 4с проверяются
   play/pause, track mismatch и drift; порог коррекции в коде **1200мс**, цель
   fine alignment **200мс**, не гарантия звуковой синхронизации.
6. Next/prev сначала отправляются источнику, затем новый trackId запускается
   на ведомых. Для paused/changed/missing follower есть отдельные ветки.
7. Stop/leave/remove восстанавливают установленный контроллером mute и чистят
   группы/измерения. Offline источника останавливает группу.

Источники:
[start](https://github.com/sergvax/sboom_node/blob/a647c4646bd4ee743cd3fadc621dfa33964a1cb4/lib/multiroom.js#L230-L349),
[фаза/пороги](https://github.com/sergvax/sboom_node/blob/a647c4646bd4ee743cd3fadc621dfa33964a1cb4/lib/multiroom.js#L22-L118),
[measure](https://github.com/sergvax/sboom_node/blob/a647c4646bd4ee743cd3fadc621dfa33964a1cb4/lib/manager.js#L414-L453),
[monitor](https://github.com/sergvax/sboom_node/blob/a647c4646bd4ee743cd3fadc621dfa33964a1cb4/lib/multiroom.js#L717-L846),
[command routing](https://github.com/sergvax/sboom_node/blob/a647c4646bd4ee743cd3fadc621dfa33964a1cb4/lib/multiroom.js#L883-L926).

README местами описывает прежний алгоритм: паузу источника при старте, редкую
точную проверку и порог 0.6с. Приведённые выше действия и числа взяты из кода
зафиксированного commit. Заявленные автором 0.2–0.5с на железе независимо не
подтверждены. Независимые потоки, целосекундный seek и дрейф не дают обещать
синхронность настоящей акустической мультирум-системы. Для волны/плейлиста
ведомые следуют за текущим track источника, а не получают его полную очередь.

## Каталог, история и устойчивость

История — именно запуски из поиска/истории, не вся история прослушивания:
очередь/прямые ссылки/автоплей туда не пишутся. Имеет key `(kind,id)`, count,
firstAt/lastAt, recent/frequent UI, bounded size, serial writes, atomic rename,
0600 и clear. В HA стоит перенести в `Store` HA и добавить в существующую панель,
а не создавать независимый JSON-сервер.
([history](https://github.com/sergvax/sboom_node/blob/a647c4646bd4ee743cd3fadc621dfa33964a1cb4/lib/history.js),
[условия записи](https://github.com/sergvax/sboom_node/blob/a647c4646bd4ee743cd3fadc621dfa33964a1cb4/server.js#L95-L126),
[UI](https://github.com/sergvax/sboom_node/blob/a647c4646bd4ee743cd3fadc621dfa33964a1cb4/public/app.js#L955-L1050).)

Каталог хранит cookie/anonymous session на диске, переиспользует после restart,
обновляет token при 401/307, повторяет transient status/429, одновременно
спрашивает REST+GraphQL и может вернуть stale cache при отказе обоих каналов.
Сопоставление голого ID проверяет track/playlist/artist/release параллельно:
если найдено несколько типов, требует полную ссылку. У нас уже есть httpx cookie
jar/follow_redirects/401 retry; переносить нужно разницу, не переписывать весь клиент.
([session](https://github.com/sergvax/sboom_node/blob/a647c4646bd4ee743cd3fadc621dfa33964a1cb4/lib/zvuk.js#L375-L455),
[retry](https://github.com/sergvax/sboom_node/blob/a647c4646bd4ee743cd3fadc621dfa33964a1cb4/lib/zvuk.js#L514-L543),
[dual search/stale](https://github.com/sergvax/sboom_node/blob/a647c4646bd4ee743cd3fadc621dfa33964a1cb4/lib/zvuk.js#L614-L667),
[ID resolution](https://github.com/sergvax/sboom_node/blob/a647c4646bd4ee743cd3fadc621dfa33964a1cb4/lib/zvuk.js#L812-L870).)

Transport/UX идеи: длинный timeout scan только для scan; локальная optimistic
реакция с последующим чтением состояния; обработка частичных ошибок broadcast;
восстановление после нескольких неудач poll; явные UI-состояния offline/pairing/
buffering; тесты задержки seek и сброса mute. Более медленный poll громкости
10с у них не следует переносить вместо нашего настраиваемого интервала. Их SSE и открытый Express HTTP
не нужны внутри HA: использовать существующие HA WebSocket и авторизацию.

## Приоритеты переноса

1. **BT-панель и scan**: вывести уже существующие pairing/paired commands,
   подключить существующий op21 parser к client/coordinator и UI рядом со списком
   спаренных. Не создавать ложный `bt_enabled` switch из op22.

   Локальные точки входа: [`bt_make_discoverable`](../custom_components/sboom_ha/api.py),
   [`parse_scanned_bt`](../custom_components/sboom_ha/_parsers.py),
   [`bt_pairing` и видимость кнопок](../custom_components/sboom_ha/button.py).
2. **История успешных запусков**: самостоятельное улучшение панели без нового
   протокола и минимальный риск для транспорта.
3. **Каталог**: transient retry/stale cache и параллельный поисковый fallback;
   session persistence и распознавание неоднозначных ID проверить отдельно.
4. **Программный мультирум**: отдельная экспериментальная функция с явными
   ограничениями источников и точности, fresh state measurements, lifecycle
   cleanup и тестами задержек/частичного offline. Это самый большой перенос.
5. **Панель нескольких колонок и mock-сценарии**: использовать существующие
   HA multi-target команды; заимствовать сценарии тестов, а не дублировать API.

Службы HA, автоматики, календарь, diagnostics/repairs/system_health, аппаратные
датчики, lyrics и обложки уже дают `sboom_ha` возможности, которых у Node-проекта
нет. Проект полезен как набор отдельных доработок, а не как полная замена нашей
архитектуры.

## Проверка тестов

Исходники получены через GitHub connector и сохранены только в
`/tmp/sboom-node-research`; приложение не подключалось к реальным колонкам.
`npm ci --ignore-scripts --no-audit --no-fund` выполнен на Node v22.22.1.
`npm test` (`node --test`) завершился успешно: **53 passed, 0 failed**,
42.87с. Включены BT, mocked WSS e2e, мультирум с задержкой seek, UI/jsdom,
парсеры, история и mock HTTP-клиент Звука.

Для нашей интеграции отдельно выполнен ограниченный набор
`python -m pytest tests/test_api_commands.py tests/test_api_parsers.py -q`:
**37 passed**, 0.42с, Python 3.14, отдельный venv с `requirements-test.txt`.
Это проверка сериализации команд/парсеров, а не всего HA или реального BT.

Мок-тесты и комментарии об авторских измерениях на firmware 1.104 следует
отличать от независимой проверки на пользовательском железе.
