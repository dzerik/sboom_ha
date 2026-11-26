"""Ручная проверка HDMI1 и питания одного сопряжённого Sber TV."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from pathlib import Path
from time import monotonic
from types import ModuleType

from websockets.exceptions import WebSocketException

# У пакета интеграции __init__.py запускает Home Assistant. В отдельном CLI
# загружаем только его автономные модули транспорта под собственным именем.
_transport = ModuleType("_sboom_tv_transport")
_transport.__path__ = [str(Path(__file__).resolve().parents[1] / "custom_components" / "sboom_ha")]
sys.modules[_transport.__name__] = _transport
from _sboom_tv_transport._deeplink import send_server_action  # noqa: E402
from _sboom_tv_transport._tlv import decode, field  # noqa: E402
from _sboom_tv_transport.api import PairTimeout, SberSpeakerClient  # noqa: E402

MENU = "hdmi1 / off / on / reconnect (новый WSS) / mark (начало ожидания) / q (выход)> "
POWER_PAYLOAD = {"on": b'{"type":"ON"}', "off": b'{"type":"OFF"}'}
HDMI1_URI = "action://sberdevices.intent.action.HDMI1_INPUT"


def describe_ack(response: object) -> str:
    """Показываем только известные статусы, без текста или идентификаторов от ТВ."""
    description = response.get(1) if isinstance(response, dict) else response
    if isinstance(description, str):
        status = description.strip()
        if status in {"OK", "NOT OK", "ParseError"}:
            return status
        if status:
            return "другое описание"
    return "описание отсутствует"


async def send_selection(client: SberSpeakerClient, choice: str) -> str:
    """Отправить один из трёх фиксированных кандидатов с действующим PIN-токеном."""
    if not client.pin_access_token:
        raise ValueError("Требуется сопряжение по +")
    if choice == "hdmi1":
        reply = await send_server_action(client, "DEEPLINK", {"deeplink": HDMI1_URI})
        return f"Поле 4 = {describe_ack(reply)}"

    payload = field(2, 2, POWER_PAYLOAD[choice])
    star_command = field(128, 2, payload)
    msg_id = str(uuid.uuid4())
    envelope = b"".join(
        (
            field(1, 0, 3),
            field(2, 2, msg_id.encode()),
            field(3, 2, client.pin_access_token.encode()),
            field(6, 0, 1),
            field(7, 2, client.client_name.encode()),
            field(8, 2, field(1, 2, star_command)),
            field(10, 0, 1),
            field(11, 2, client.client_id.encode()),
        )
    )
    reply = decode(await client.send_raw_request(envelope, msg_id))
    code = reply.get(3)
    code_info = f", код {code}" if isinstance(code, int) else ""
    return f"Поле 6 = {describe_ack(reply.get(6))}{code_info}"


async def run_session(client: SberSpeakerClient) -> int:
    """Отдельное сопряжение только в памяти. Переподключение использует тот же клиент."""
    try:
        await client.connect()
        print("Нажмите + на телевизоре для сопряжения (до 120 секунд).", flush=True)
        await client.pair_with_button()
        client.start_listening()
        print("Сопряжение завершено. Выбирайте по одному действию и следите за экраном.")
        standby_since = None
        while True:
            try:
                choice = (await asyncio.to_thread(input, MENU)).strip().lower()
            except EOFError:
                return 0
            if choice == "q":
                return 0
            if choice == "mark":
                standby_since = monotonic()
                print("Отмечено начало ожидания.")
                continue
            if choice not in ("reconnect", "on", "off", "hdmi1"):
                print("Введите команду из списка.")
                continue
            if standby_since is not None:
                print(f"{choice}: {monotonic() - standby_since:.1f} с после отметки.")
            try:
                if choice == "reconnect":
                    await client.close()
                    await client.connect()
                    client.start_listening()
                    print("Новое соединение WSS открыто.")
                elif client.disconnected.is_set():
                    print("WSS закрыт. Для попытки подключения выберите reconnect.")
                else:
                    print(await send_selection(client, choice))
            except TimeoutError:
                print("Таймаут. Проверьте экран и запишите результат.")
            except (OSError, RuntimeError, WebSocketException) as exc:
                print(f"Ошибка WSS ({type(exc).__name__}). Проверьте экран и запишите результат.")
    except (OSError, RuntimeError, WebSocketException, PairTimeout) as exc:
        print(f"Сопряжение не завершено ({type(exc).__name__}).")
        return 1
    finally:
        await client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", help="IP-адрес телевизора в локальной сети")
    host = parser.parse_args().host
    # Стандартный отчёт состоит из строк CLI, без сетевых журналов.
    logging.disable(logging.CRITICAL)
    client = SberSpeakerClient(host, client_name="Sber TV beta check")
    try:
        return asyncio.run(run_session(client))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
