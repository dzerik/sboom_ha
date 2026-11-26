"""Offline checks of the manual tester's frames and reconnect workflow."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from uuid import UUID

import pytest
from sboom_ha._tlv import decode, field

from research import tv_beta_check as checker

SCRIPT = Path(__file__).resolve().parents[1] / "research" / "tv_beta_check.py"
TOKEN = "synthetic-only-test-token"


class ManualClient:
    """Replace the wire and physical '+' press while recording actual request bytes."""

    client_id = "test-client"
    client_name = "test-name"

    def __init__(self, *, send_error=None, fail_reconnect=False):
        self.pin_access_token = None
        self.disconnected = asyncio.Event()
        self.disconnected.set()
        self.connect_count = self.pair_count = 0
        self.requests = []
        self.send_error = send_error
        self.fail_reconnect = fail_reconnect

    async def connect(self):
        self.connect_count += 1
        if self.fail_reconnect and self.connect_count == 2:
            raise OSError(f"synthetic network error: {TOKEN}")
        self.disconnected.clear()

    async def close(self):
        self.disconnected.set()

    async def pair_with_button(self):
        self.pair_count += 1
        self.pin_access_token = TOKEN
        return TOKEN

    def start_listening(self):
        assert not self.disconnected.is_set()
        assert self.pin_access_token == TOKEN

    async def send_raw_request(self, packet, msg_id, timeout=5.0):
        assert not self.disconnected.is_set()
        self.requests.append((self.connect_count, msg_id, packet))
        if self.send_error:
            raise self.send_error
        reply_field = 4 if decode(packet)[1] == 1 else 6
        return field(2, 2, msg_id.encode()) + field(reply_field, 2, field(1, 2, b"OK"))


def select(monkeypatch, *commands):
    choices = iter(commands)
    monkeypatch.setattr("builtins.input", lambda _: next(choices))


async def test_one_authenticated_frame_per_selection_across_standby(monkeypatch, capsys):
    client = ManualClient()
    select(monkeypatch, "other", "hdmi1", "off", "mark", "reconnect", "on", "q")
    await checker.run_session(client)

    hdmi, off, on = client.requests
    assert (client.connect_count, client.pair_count) == (2, 1)
    assert [connection for connection, _, _ in client.requests] == [1, 1, 2]
    assert len({UUID(msg_id) for _, msg_id, _ in client.requests}) == 3
    for _, msg_id, packet in client.requests:
        envelope = decode(packet)
        assert envelope[2] == msg_id
        assert (envelope[3], envelope[6], envelope[7], envelope[10], envelope[11]) == (
            TOKEN,
            1,
            "test-name",
            1,
            "test-client",
        )
    directive = decode(hdmi[2])
    assert directive[1] == 1
    body = json.loads(directive[4][1])
    assert body["name"] == "DEEPLINK"
    assert body["payload"] == {"deeplink": "action://sberdevices.intent.action.HDMI1_INPUT"}
    # Literal protobuf bytes inside field 8, including the multibyte tag 128.
    for (_, _, packet), expected in [
        (off, b'\x0a\x13\x82\x08\x10\x12\x0e{"type":"OFF"}'),
        (on, b'\x0a\x12\x82\x08\x0f\x12\x0d{"type":"ON"}'),
    ]:
        envelope = decode(packet)
        assert set(envelope) == {1, 2, 3, 6, 7, 8, 10, 11}
        assert envelope[1] == 3
        # Check wire type and length around the literal, not decoder heuristics.
        assert bytes([0x42, len(expected)]) + expected in packet
    report = capsys.readouterr().out
    assert report.count("= OK") == 3
    assert "после отметки" in report
    assert TOKEN not in report


@pytest.mark.parametrize(
    ("send_error", "expected"),
    [(TimeoutError(TOKEN), "Таймаут"), (RuntimeError(TOKEN), "RuntimeError")],
)
async def test_send_failure_and_failed_reconnect_never_replay_or_disclose(
    monkeypatch, capsys, send_error, expected
):
    client = ManualClient(send_error=send_error, fail_reconnect=True)
    select(monkeypatch, "off", "reconnect", "on", "reconnect", "q")
    await checker.run_session(client)
    assert len(client.requests) == 1
    assert (client.connect_count, client.pair_count) == (3, 1)
    assert client.disconnected.is_set()
    report = capsys.readouterr().out
    assert expected in report
    assert "OSError" in report
    assert TOKEN not in report


def test_cli_starts_without_home_assistant(tmp_path):
    result = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "host" in result.stdout


@pytest.mark.parametrize("choice", ["on", "off", "hdmi1"])
async def test_missing_token_fails_before_transport(choice):
    client = ManualClient()
    with pytest.raises(ValueError, match="сопряжение"):
        await checker.send_selection(client, choice)
    assert not client.requests


@pytest.mark.parametrize(
    ("reply", "description"),
    [
        ({1: "OK"}, "OK"),
        (" NOT OK ", "NOT OK"),
        ({1: f"NOT OK {TOKEN}"}, "другое описание"),
        ({1: {2: TOKEN}}, "описание отсутствует"),
        (None, "описание отсутствует"),
    ],
)
def test_reports_only_a_known_status(reply, description):
    assert checker.describe_ack(reply) == description
