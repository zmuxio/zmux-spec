"""QUIC variable-length integers (RFC 9000 Section 16)."""

MAX_VARINT = (1 << 62) - 1


class DecodeError(Exception):
    """Raised when bytes cannot be decoded.

    ``code`` names the transport error a receiver uses (for example
    ``FRAME_ENCODING_ERROR``); ``reason`` is a short machine-friendly tag.
    """

    def __init__(self, code, reason):
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason


def varint_len(value):
    if value < 0 or value > MAX_VARINT:
        raise ValueError(f"varint out of range: {value}")
    if value < (1 << 6):
        return 1
    if value < (1 << 14):
        return 2
    if value < (1 << 30):
        return 4
    return 8


def encode_varint(value, length=None):
    """Encode ``value``; ``length`` forces a (possibly non-minimal) width."""
    need = varint_len(value)
    if length is None:
        length = need
    if length not in (1, 2, 4, 8) or length < need:
        raise ValueError(f"cannot encode {value} in {length} bytes")
    prefix = {1: 0x00, 2: 0x40, 4: 0x80, 8: 0xC0}[length]
    raw = value.to_bytes(length, "big")
    return bytes([raw[0] | prefix]) + raw[1:]


def decode_varint(buf, pos, code="FRAME_ENCODING_ERROR"):
    """Return ``(value, new_pos, width)``; raise DecodeError when truncated."""
    if pos >= len(buf):
        raise DecodeError(code, "truncated varint")
    first = buf[pos]
    width = 1 << (first >> 6)
    if pos + width > len(buf):
        raise DecodeError(code, "truncated varint")
    value = first & 0x3F
    for b in buf[pos + 1 : pos + width]:
        value = (value << 8) | b
    return value, pos + width, width
