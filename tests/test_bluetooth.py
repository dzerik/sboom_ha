"""Bluetooth coordinator actions use confirmed state and serialize requests."""

from __future__ import annotations

import asyncio

import pytest
from sboom_ha.api import BluetoothDevice

from tests._fakes import build_coordinator, make_state

MAC = "AA:BB:CC:00:11:22"


def _coordinator(monkeypatch):
    coord = build_coordinator()
    coord.connected = True
    calls = []

    async def paired():
        calls.append("paired")
        return [BluetoothDevice(MAC, "Headphones", connected=False)]

    async def scanned():
        calls.append("scan")
        return [BluetoothDevice(MAC, "Headphones", rssi=200)]

    async def pair():
        calls.append("pair")

    async def command(mac, cmd):
        calls.append((mac, cmd))

    monkeypatch.setattr(coord.client, "get_paired_bt_devices", paired)
    monkeypatch.setattr(coord.client, "get_scanned_bt_devices", scanned, raising=False)
    monkeypatch.setattr(coord.client, "bt_make_discoverable", pair)
    monkeypatch.setattr(coord.client, "bt_device_command", command)
    return coord, calls


@pytest.mark.asyncio
async def test_bluetooth_scan_returns_confirmed_snapshot_and_notifies(monkeypatch):
    coord, calls = _coordinator(monkeypatch)
    notifications = []
    monkeypatch.setattr(
        coord, "async_update_listeners", lambda: notifications.append(list(coord.paired_bt))
    )

    result = await coord.async_bluetooth("scan")

    assert calls == ["scan", "paired"]
    assert result == {
        "paired": [{"mac": MAC, "name": "Headphones", "connected": False}],
        "scanned": [{"mac": MAC, "name": "Headphones", "rssi": 200}],
    }
    assert len(notifications) == 1
    assert notifications[0][0].connected is False
    result["scanned"][0]["name"] = "Changed by caller"
    status = await coord.async_bluetooth("status")
    assert status["scanned"][0]["name"] == "Headphones"
    assert calls == ["scan", "paired", "paired"]


@pytest.mark.asyncio
@pytest.mark.parametrize("action, command", [("connect", 0), ("disconnect", 1), ("remove", 2)])
async def test_bluetooth_device_actions_reread_paired_without_optimism(
    monkeypatch, action, command
):
    coord, calls = _coordinator(monkeypatch)

    result = await coord.async_bluetooth(action, MAC.lower())

    assert calls == [(MAC, command), "paired"]
    assert result == {
        "paired": [{"mac": MAC, "name": "Headphones", "connected": False}],
        "scanned": [],
    }
    assert coord.paired_bt[0].connected is False


@pytest.mark.asyncio
async def test_bluetooth_pair_only_enables_discoverability_and_rereads_paired(monkeypatch):
    coord, calls = _coordinator(monkeypatch)
    result = await coord.async_bluetooth("pair")
    assert calls == ["pair", "paired"]
    assert set(result) == {"paired", "scanned"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action, mac",
    [
        ("power_on", None),
        ("", None),
        ("connect", None),
        ("disconnect", "bad"),
        ("remove", "AA:BB:CC:00:11:ZZ"),
        ("connect", "AA:BB:CC:00:11:22\n"),
        ("connect", 123),
        ([], None),
    ],
)
async def test_bluetooth_rejects_invalid_action_or_mac_before_transport(monkeypatch, action, mac):
    coord, calls = _coordinator(monkeypatch)
    with pytest.raises(ValueError):
        await coord.async_bluetooth(action, mac)
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("stopping", [False, True])
async def test_bluetooth_rejects_unavailable_coordinator(monkeypatch, stopping):
    coord, calls = _coordinator(monkeypatch)
    coord.connected = stopping
    coord._stopping = stopping
    with pytest.raises(RuntimeError):
        await coord.async_bluetooth("status")
    assert calls == []


@pytest.mark.asyncio
async def test_bluetooth_serializes_scan_and_device_command(monkeypatch):
    coord, calls = _coordinator(monkeypatch)
    started, release = asyncio.Event(), asyncio.Event()

    async def scan():
        calls.append("scan-start")
        started.set()
        await release.wait()
        calls.append("scan-end")
        return []

    monkeypatch.setattr(coord.client, "get_scanned_bt_devices", scan)
    scanning = asyncio.create_task(coord.async_bluetooth("scan"))
    await asyncio.wait_for(started.wait(), 1)
    connecting = asyncio.create_task(coord.async_bluetooth("connect", MAC))
    await asyncio.sleep(0)
    try:
        assert calls == ["scan-start"]
    finally:
        release.set()
        await asyncio.gather(scanning, connecting)
    assert calls == ["scan-start", "scan-end", "paired", (MAC, 0), "paired"]


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [TimeoutError, ConnectionError, asyncio.CancelledError])
async def test_bluetooth_scan_error_releases_lock_without_publishing(monkeypatch, error):
    coord, calls = _coordinator(monkeypatch)
    previous = BluetoothDevice(MAC, "Old", connected=True)
    coord.paired_bt = [previous]
    notifications = []
    monkeypatch.setattr(coord, "async_update_listeners", lambda: notifications.append(True))

    async def failing_scan():
        raise error()

    monkeypatch.setattr(coord.client, "get_scanned_bt_devices", failing_scan)
    with pytest.raises(error):
        await coord.async_bluetooth("scan")
    assert coord.paired_bt == [previous]
    assert notifications == []
    result = await asyncio.wait_for(coord.async_bluetooth("status"), 1)
    assert result["paired"][0]["name"] == "Headphones"
    assert calls == ["paired"]


@pytest.mark.asyncio
async def test_bluetooth_refresh_error_preserves_confirmed_state(monkeypatch):
    coord, calls = _coordinator(monkeypatch)
    previous = BluetoothDevice(MAC, "Old", connected=True)
    coord.paired_bt = [previous]

    async def failing_paired():
        raise ConnectionError("speaker closed socket")

    monkeypatch.setattr(coord.client, "get_paired_bt_devices", failing_paired)
    with pytest.raises(ConnectionError):
        await coord.async_bluetooth("disconnect", MAC)
    assert coord.paired_bt == [previous]
    assert calls == [(MAC, 1)]


@pytest.mark.asyncio
async def test_bluetooth_poll_cannot_overwrite_a_newer_command_snapshot(monkeypatch):
    coord, calls = _coordinator(monkeypatch)
    started, release = asyncio.Event(), asyncio.Event()
    reads = 0

    async def paired():
        nonlocal reads
        reads += 1
        if reads == 1:
            calls.append("poll-start")
            started.set()
            await release.wait()
            calls.append("poll-end")
            return [BluetoothDevice(MAC, "Old", connected=True)]
        calls.append("paired")
        return [BluetoothDevice(MAC, "New", connected=False)]

    async def empty():
        return None

    monkeypatch.setattr(coord.client, "get_state", empty)
    monkeypatch.setattr(coord.client, "get_metadata", empty)
    monkeypatch.setattr(coord.client, "get_paired_bt_devices", paired)
    polling = asyncio.create_task(coord._refresh_state_and_track())
    await asyncio.wait_for(started.wait(), 1)
    disconnecting = asyncio.create_task(coord.async_bluetooth("disconnect", MAC))
    await asyncio.sleep(0)
    try:
        assert calls == ["poll-start"]
    finally:
        release.set()
        await asyncio.gather(polling, disconnecting)

    assert calls == ["poll-start", "poll-end", (MAC, 1), "paired"]
    assert coord.paired_bt[0].name == "New"


@pytest.mark.asyncio
async def test_bluetooth_queued_action_rechecks_availability(monkeypatch):
    coord, calls = _coordinator(monkeypatch)
    started, release = asyncio.Event(), asyncio.Event()

    async def scan():
        started.set()
        await release.wait()
        return []

    monkeypatch.setattr(coord.client, "get_scanned_bt_devices", scan)
    scanning = asyncio.create_task(coord.async_bluetooth("scan"))
    await asyncio.wait_for(started.wait(), 1)
    connecting = asyncio.create_task(coord.async_bluetooth("connect", MAC))
    await asyncio.sleep(0)
    coord.connected = False
    release.set()
    await scanning
    with pytest.raises(RuntimeError):
        await connecting
    assert calls == ["paired"]


@pytest.mark.asyncio
async def test_bluetooth_scan_does_not_delay_volume_poll_or_listeners(monkeypatch):
    coord, calls = _coordinator(monkeypatch)
    coord.state = make_state(volume=10)
    started, release = asyncio.Event(), asyncio.Event()

    async def scan():
        started.set()
        await release.wait()
        return []

    async def state():
        return make_state(volume=74)

    async def empty():
        return None

    monkeypatch.setattr(coord.client, "get_scanned_bt_devices", scan)
    monkeypatch.setattr(coord.client, "get_state", state)
    monkeypatch.setattr(coord.client, "get_metadata", empty)
    scanning = asyncio.create_task(coord.async_bluetooth("scan"))
    await asyncio.wait_for(started.wait(), 1)
    try:
        await asyncio.wait_for(coord._refresh_state_and_track(), 0.1)
        assert coord.state.volume_percent == 74
        assert coord.data["state"].volume_percent == 74
        assert coord._listener_calls == 1
        assert calls == []  # Background paired refresh is deferred to the scan.
        assert not scanning.done()
    finally:
        release.set()
        await scanning

    assert calls == ["paired"]
