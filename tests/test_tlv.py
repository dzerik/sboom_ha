"""Тесты бинарного TLV-кодека (varint + length-delimited)."""
from __future__ import annotations

from sboom_ha._tlv import decode, field, varint


# ─────────────────────── varint ───────────────────────

def test_varint_zero():
    assert varint(0) == b"\x00"


def test_varint_single_byte_boundary():
    assert varint(127) == b"\x7f"


def test_varint_two_bytes():
    # 128 = 0x80; в varint LE: 0x80, 0x01
    assert varint(128) == b"\x80\x01"


def test_varint_large_value():
    assert varint(300) == b"\xac\x02"


# ─────────────────────── field ───────────────────────

def test_field_kind0_varint():
    # tag=1, kind=0 (varint), value=42
    # key = (1<<3) | 0 = 0x08; payload = varint(42) = 0x2a
    assert field(1, 0, 42) == b"\x08\x2a"


def test_field_kind2_length_delim():
    # tag=2, kind=2, value=b"hi"
    # key = (2<<3) | 2 = 0x12; len=2; payload=b"hi"
    assert field(2, 2, b"hi") == b"\x12\x02hi"


def test_field_kind2_empty_payload():
    assert field(5, 2, b"") == b"\x2a\x00"


def test_field_unsupported_kind_raises():
    import pytest
    with pytest.raises(ValueError, match="unsupported kind"):
        field(1, 5, 0)


# ─────────────────────── decode roundtrips ───────────────────────

def test_decode_single_varint():
    enc = field(1, 0, 99)
    assert decode(enc) == {1: 99}


def test_decode_single_string():
    enc = field(3, 2, b"hello")
    assert decode(enc) == {3: "hello"}


def test_decode_multiple_fields():
    enc = field(1, 0, 7) + field(2, 2, b"abc") + field(3, 0, 42)
    assert decode(enc) == {1: 7, 2: "abc", 3: 42}


def test_decode_nested_message():
    inner = field(1, 0, 100) + field(2, 2, b"xy")
    outer = field(5, 2, inner)
    decoded = decode(outer)
    assert decoded == {5: {1: 100, 2: "xy"}}


def test_decode_empty_bytes():
    assert decode(b"") == {}


def test_decode_handles_non_utf8_as_hex():
    # length-delim payload, который не декодируется как UTF-8 и не парсится как nested
    enc = field(1, 2, b"\xff\xfe\x00\x01")
    decoded = decode(enc)
    assert 1 in decoded
    # должно вернуть либо hex, либо nested с одним полем — главное что не упало
    assert decoded[1] != "" and decoded[1] is not None


def test_decode_truncated_input_returns_partial():
    # Ключ есть, но varint не заканчивается → возвращает то что успели распарсить
    truncated = b"\x08"  # tag=1, kind=0, без payload
    result = decode(truncated)
    assert isinstance(result, dict)


def test_decode_real_envelope_fragment():
    # Эмуляция "конверта": id=req-id (str), type=2 (varint)
    pkt = field(1, 0, 2) + field(2, 2, b"req-123")
    assert decode(pkt) == {1: 2, 2: "req-123"}
