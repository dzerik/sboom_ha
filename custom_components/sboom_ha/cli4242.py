"""Клиент debug-CLI колонки (:4242) — инвентарь Zigbee и Matter.

ВАЖНО: CLI есть только у моделей с локальным Zigbee-координатором
(SberBoom R2). На остальных порт закрыт → probe вернёт None, сенсор не
создаётся. CLI даёт ТОЛЬКО инвентарь/топологию (read-only): состояние
устройств (on/off) и управление он не отдаёт — это идёт через облако Sber.

Протокол текстовый: команда + LF, ответ — ASCII-таблица `zigbee list`.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)

DEFAULT_CLI_PORT = 4242
_CONNECT_TIMEOUT = 4.0
_READ_TIMEOUT = 3.0


@dataclass(frozen=True)
class ZigbeeDevice:
    ieee: str
    nwk: str
    state: str
    manufacturer: str
    model: str
    power_source: str
    rssi: int | None


def parse_zigbee_list(text: str) -> list[ZigbeeDevice]:
    """ASCII-таблица `zigbee list` → список устройств.

    Заголовок: | # | IEEE Address | NWK | XID | State | Manufacturer name |
    Model ID | Pwr src | OTA perm. | App | Has OTA | RSSI |
    Строки-разделители (|---|) и пустые пропускаем.
    """
    devices: list[ZigbeeDevice] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|") or set(line) <= {"|", "-", " "}:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 12 or cells[0] in ("#", ""):
            continue  # заголовок или битая строка
        try:
            rssi = int(cells[11])
        except (ValueError, IndexError):
            rssi = None
        devices.append(
            ZigbeeDevice(
                ieee=cells[1],
                nwk=cells[2],
                state=cells[4],
                manufacturer=cells[5],
                model=cells[6],
                power_source=cells[7],
                rssi=rssi,
            )
        )
    return devices


def matter_count(text: str) -> int:
    """Число Matter-устройств из вывода `matter list`.

    Пустой список → 0. Формат строки устройства не подтверждён, поэтому
    считаем непустые не-заголовочные строки (табличные `| ... |` либо
    построчные) — грубая, но безопасная оценка до реального захвата.
    """
    if "empty" in text.lower():
        return 0
    rows = 0
    for line in text.splitlines():
        line = line.strip()
        if not line or line.lower().startswith(("valid commands", "node id", "not found")):
            continue
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if set("".join(cells)) <= set("-") or cells[0] in ("#", ""):
                continue
            rows += 1
        elif line[0].isdigit():
            rows += 1
    return rows


class Cli4242Client:
    """Одноразовые соединения к debug-CLI :4242 (per-poll).

    Обслуживает и Zigbee, и Matter — общий текстовый CLI на одном порту.
    """

    def __init__(self, host: str, port: int = DEFAULT_CLI_PORT) -> None:
        self._host = host
        self._port = port

    async def _run(self, command: str) -> str | None:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port), _CONNECT_TIMEOUT
            )
        except (OSError, TimeoutError) as exc:
            _LOGGER.debug("zigbee cli %s:%s недоступен: %s", self._host, self._port, exc)
            return None
        try:
            # проглотить приветствие «CLI connection established»
            await self._drain(reader, 0.6)
            writer.write((command + "\n").encode())
            await writer.drain()
            return (await self._drain(reader, _READ_TIMEOUT)).decode("utf-8", "ignore")
        except (OSError, TimeoutError) as exc:
            _LOGGER.debug("zigbee cli command failed: %s", exc)
            return None
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    @staticmethod
    async def _drain(reader: asyncio.StreamReader, timeout: float) -> bytes:
        buf = b""
        try:
            while True:
                chunk = await asyncio.wait_for(reader.read(4096), timeout)
                if not chunk:
                    break
                buf += chunk
        except TimeoutError:
            pass
        return buf

    async def async_probe(self) -> bool:
        """CLI доступен и это Zigbee-координатор? (валидный ответ version)."""
        resp = await self._run("zigbee version")
        return resp is not None and "version" in resp.lower()

    async def async_list_devices(self) -> list[ZigbeeDevice] | None:
        """Инвентарь Zigbee-устройств; None при недоступности CLI."""
        resp = await self._run("zigbee list")
        if resp is None:
            return None
        return parse_zigbee_list(resp)

    async def async_matter_probe(self) -> bool:
        """Matter-контроллер доступен? (`matter list` отвечает валидно)."""
        resp = await self._run("matter list")
        return resp is not None and "matter" in resp.lower()

    async def async_matter_list(self) -> tuple[int, str] | None:
        """Инвентарь Matter → (количество устройств, сырой вывод).

        Формат СТРОКИ устройства Matter не подтверждён (нет живого захвата с
        Matter-устройством — список пуст), поэтому структуру полей пока не
        разбираем: отдаём count + сырой текст для отладки. Структурируем,
        когда появится реальное устройство. None — CLI недоступен.
        """
        resp = await self._run("matter list")
        if resp is None:
            return None
        return matter_count(resp), resp.strip()
