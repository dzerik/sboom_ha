"""Тесты бинарного формата исходящих команд к колонке."""
from __future__ import annotations

import asyncio
import json
import struct

import pytest
from sboom_ha._deeplink import build_server_action, send_server_action
from sboom_ha._tlv import decode, field
from sboom_ha.api import SberSpeakerClient
from sboom_ha.const import (
    BT_CMD_DISCONNECT,
    OP_BT_DEVICE_COMMAND,
    OP_BT_DISCOVERABLE,
    OP_FIND_REMOTE,
    OP_GET_SCANNED_BT,
    OP_SET_PLAYBACK_SPEED,
    OP_SET_TRACK_POS,
)


class _CapturingWS:
    """Фейковый WS — запоминает все отправленные пакеты, не ходит в сеть."""

    def __init__(self) -> None:
        self.sent: list[bytes] = []

    async def send(self, data: bytes) -> None:
        self.sent.append(data)


def _make_client() -> SberSpeakerClient:
    return SberSpeakerClient(
        host="192.0.2.10", client_id="x", pin_access_token="t" * 16
    )


async def test_queued_deeplink_expires_without_sending_after_lock_release():
    client = _make_client()
    ws = client._ws = _CapturingWS()

    async with client._lock, asyncio.timeout(1):
        with pytest.raises(TimeoutError):
            await send_server_action(client, "DEEPLINK", {"deeplink": "livetv://hdmi"}, timeout=0.01)

    await asyncio.sleep(0)
    assert ws.sent == []
    assert not client._pending


async def test_deeplink_expires_while_socket_send_is_blocked():
    class StalledSend(_CapturingWS):
        async def send(self, data):
            await super().send(data)
            await asyncio.Event().wait()

    client = _make_client()
    ws = client._ws = StalledSend()

    async with asyncio.timeout(1):
        with pytest.raises(TimeoutError):
            await send_server_action(client, "DEEPLINK", {"deeplink": "livetv://hdmi"}, timeout=0.01)

    assert len(ws.sent) == 1
    assert not client._lock.locked()
    assert not client._pending


@pytest.mark.parametrize("disconnect", [False, True], ids=["ack", "closed"])
async def test_deeplink_listener_matches_binary_reply_to_request(disconnect):
    sent = asyncio.Event()

    class ReplyingWS(_CapturingWS):
        async def send(self, data):
            await super().send(data)
            sent.set()

        async def __aiter__(self):
            await sent.wait()
            yield field(2, 2, b"other-request") + field(4, 2, field(1, 2, b"NOT OK"))
            if not disconnect:
                msg_id = decode(self.sent[0])[2].encode()
                yield field(2, 2, msg_id) + field(4, 2, field(1, 2, b"OK"))

    client = _make_client()
    ws = client._ws = ReplyingWS()
    async with asyncio.timeout(1), asyncio.TaskGroup() as tasks:
        tasks.create_task(client._listen_loop())
        action = send_server_action(client, "DEEPLINK", {"deeplink": "livetv://hdmi"})
        if disconnect:
            with pytest.raises(ConnectionError):
                await action
        else:
            assert await action == {1: "OK"}

    assert len(ws.sent) == 1
    assert not client._pending


def test_tv_deeplink_envelope_uses_the_paired_client():
    client = _make_client()
    message_id = "00000000-0000-0000-0000-000000000001"

    frame = decode(build_server_action(client, "DEEPLINK", {"deeplink": "livetv://hdmi"}, message_id))

    assert frame[1] == frame[6] == frame[10] == 1
    assert frame[2] == message_id
    assert frame[3] == client.pin_access_token
    assert frame[7] == client.client_name
    assert frame[11] == client.client_id
    assert json.loads(frame[4][1]) == {
        "name": "DEEPLINK",
        "payload": {"deeplink": "livetv://hdmi"},
        "smartAppInfo": {
            "appVersionId": "",
            "applicationId": "",
            "frontendEndpoint": "",
            "frontendType": "",
            "projectId": "",
            "systemName": "",
        },
        "type": 1,
    }


# ─────────────────────── set_playback_speed ───────────────────────


@pytest.mark.asyncio
async def test_set_playback_speed_uses_float_encoding():
    """op=23 кодирует скорость как float (wire-type 5), НЕ varint.

    varint-кодировка ломает playbackSpeedRate колонки в 0.0 (research exp_22).
    """
    client = _make_client()
    ws = _CapturingWS()
    client._ws = ws

    await client.set_playback_speed(1.5)

    assert len(ws.sent) == 1
    # request_data = field(op=23, field(tag=1, kind=5 float, 1.5))
    inner = field(OP_SET_PLAYBACK_SPEED, 2, b"\x0d" + struct.pack("<f", 1.5))
    assert field(5, 2, inner) in ws.sent[0]


@pytest.mark.asyncio
async def test_set_playback_speed_clamps_above_maximum():
    """Скорость выше 2.0 ограничивается до 2.0."""
    client = _make_client()
    ws = _CapturingWS()
    client._ws = ws

    await client.set_playback_speed(5.0)

    inner = field(OP_SET_PLAYBACK_SPEED, 2, b"\x0d" + struct.pack("<f", 2.0))
    assert field(5, 2, inner) in ws.sent[0]


@pytest.mark.asyncio
async def test_set_playback_speed_clamps_below_minimum():
    """Скорость ниже 0.5 ограничивается до 0.5 (0.0 — битое состояние колонки)."""
    client = _make_client()
    ws = _CapturingWS()
    client._ws = ws

    await client.set_playback_speed(0.1)

    inner = field(OP_SET_PLAYBACK_SPEED, 2, b"\x0d" + struct.pack("<f", 0.5))
    assert field(5, 2, inner) in ws.sent[0]


# ─────────────────────── find_remote / bt_make_discoverable ───────────────────────


@pytest.mark.asyncio
async def test_find_remote_sends_op13():
    """op=13 — команда поиска пульта ДУ, пустой inner."""
    client = _make_client()
    ws = _CapturingWS()
    client._ws = ws

    await client.find_remote()

    assert len(ws.sent) == 1
    inner = field(OP_FIND_REMOTE, 2, field(1, 2, b""))
    assert field(5, 2, inner) in ws.sent[0]


@pytest.mark.asyncio
async def test_bt_make_discoverable_sends_op22():
    """op=22 — режим BT-сопряжения, пустой inner."""
    client = _make_client()
    ws = _CapturingWS()
    client._ws = ws

    await client.bt_make_discoverable()

    assert len(ws.sent) == 1
    inner = field(OP_BT_DISCOVERABLE, 2, field(1, 2, b""))
    assert field(5, 2, inner) in ws.sent[0]


@pytest.mark.asyncio
async def test_bt_device_command_encodes_mac_and_cmd():
    """op=20 — request {mac (field 1), cmd (field 2 varint)}."""
    client = _make_client()
    ws = _CapturingWS()
    client._ws = ws

    await client.bt_device_command("AA:BB:CC:00:11:22", BT_CMD_DISCONNECT)

    assert len(ws.sent) == 1
    inner = field(
        OP_BT_DEVICE_COMMAND, 2,
        field(1, 2, b"AA:BB:CC:00:11:22") + field(2, 0, BT_CMD_DISCONNECT),
    )
    assert field(5, 2, inner) in ws.sent[0]


# ─────────────────────── seek_to: clamp позиции (аудит #12) ───────────────────────

@pytest.mark.asyncio
async def test_seek_to_clamps_negative_position_to_zero():
    """varint кодирует отрицательные числа мусором (-1 → 127 на колонке)."""
    client = _make_client()
    ws = _CapturingWS()
    client._ws = ws

    await client.seek_to(-5)

    assert len(ws.sent) == 1
    inner = field(OP_SET_TRACK_POS, 2, field(1, 0, 0))
    assert field(5, 2, inner) in ws.sent[0]


@pytest.mark.asyncio
async def test_get_scanned_bt_devices_encodes_op21_and_parses_response(monkeypatch):
    client = _make_client()
    requests = []
    device = field(1, 2, b"AA:BB:CC:00:11:22") + field(2, 2, b"Headphones") + field(3, 0, 200)
    response = field(5, 2, field(21, 2, field(2, 2, device)))

    async def send(envelope, msg_id, timeout):
        requests.append((envelope, timeout))
        return response

    monkeypatch.setattr(client, "send_raw_request", send)
    devices = await client.get_scanned_bt_devices()

    assert len(requests) == 1
    assert field(5, 2, field(OP_GET_SCANNED_BT, 2, field(1, 2, b""))) in requests[0][0]
    assert requests[0][1] == 15
    assert [(device.mac, device.name, device.rssi) for device in devices] == [
        ("AA:BB:CC:00:11:22", "Headphones", 200)
    ]


@pytest.mark.asyncio
async def test_get_scanned_bt_timeout_cleans_pending_request(monkeypatch):
    client = _make_client()
    client._ws = _CapturingWS()
    send = client.send_raw_request

    async def send_with_short_deadline(envelope, msg_id, timeout):
        assert timeout == 15
        return await send(envelope, msg_id, timeout=0.001)

    monkeypatch.setattr(client, "send_raw_request", send_with_short_deadline)
    with pytest.raises(TimeoutError):
        await client.get_scanned_bt_devices()

    assert len(client._ws.sent) == 1
    assert client._pending == {}
