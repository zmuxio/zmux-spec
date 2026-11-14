#!/usr/bin/env python3
"""Generate fixtures/capsules.json: packet stream capsule vectors
(EXTENSIONS.md Section 12).

The file is self-describing: its "format" field states how a consumer
replays each case. The decoder below is the reference for that text and
checks every case before the file is written.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from zmuxwire.varint import encode_varint as V  # noqa: E402

DATAGRAM = 0x00
DEFAULT_MAX = 65535
CONTEXT_SLACK = 8  # the longest Context ID encoding

FORMAT = [
    "Each case is the byte sequence of one direction of a packet stream "
    "(\"hex\"), a sequence of RFC 9297 capsules: Type (i), Length (i), Value.",
    "A consumer decodes \"hex\" from the start with a receive bound of "
    "\"max_payload\" payload bytes, as if the bytes were followed by the end of "
    "the stream (FIN).",
    "Type 0x00 is DATAGRAM: its Value is Context ID (i) followed by the payload; "
    "each yields one entry of \"packets\" ({context, payload hex}), in order.",
    "A capsule of any other type is skipped; \"skipped\" counts them.",
    "\"error\" is null when the bytes decode cleanly to the end. Otherwise it is "
    "\"oversized\" or \"truncated\" and the packets listed are those decoded before "
    "the error; a receiver answers either with ZMUX_PACKET_ERROR "
    "(RESET_STREAM and STOP_SENDING).",
    "oversized: a Length above max_payload + 8 (any type), detected as soon as "
    "the Length is read, or a DATAGRAM whose payload (Value minus Context ID) "
    "exceeds max_payload.",
    "truncated: the bytes end inside a capsule (in Type, Length or Value), or a "
    "DATAGRAM Value ends inside its Context ID.",
    "\"encode\" true means \"hex\" is also the canonical encoding (minimal "
    "varints, no other capsules) of \"packets\": a sender that writes these "
    "packets as capsules produces exactly these bytes.",
]


def capsule(t, value):
    return V(t) + V(len(value)) + value


def dgram(ctx, payload, length=None):
    value = V(ctx) + payload
    if length is None:
        return capsule(DATAGRAM, value)
    return V(DATAGRAM) + V(len(value), length) + value


def read_varint(b, i):
    if i >= len(b):
        return None, i
    n = 1 << (b[i] >> 6)
    if i + n > len(b):
        return None, i
    v = b[i] & 0x3F
    for k in range(1, n):
        v = (v << 8) | b[i + k]
    return v, i + n


def decode(b, max_payload):
    packets, skipped, i = [], 0, 0
    while i < len(b):
        t, j = read_varint(b, i)
        if t is None:
            return packets, skipped, "truncated"
        ln, j = read_varint(b, j)
        if ln is None:
            return packets, skipped, "truncated"
        if ln > max_payload + CONTEXT_SLACK:
            return packets, skipped, "oversized"
        if j + ln > len(b):
            return packets, skipped, "truncated"
        value, i = b[j:j + ln], j + ln
        if t != DATAGRAM:
            skipped += 1
            continue
        ctx, k = read_varint(value, 0)
        if ctx is None:
            return packets, skipped, "truncated"
        if len(value) - k > max_payload:
            return packets, skipped, "oversized"
        packets.append({"context": ctx, "payload": value[k:].hex()})
    return packets, skipped, None


def cases():
    out = []

    def add(cid, note, data, max_payload=DEFAULT_MAX):
        packets, skipped, err = decode(data, max_payload)
        enc = err is None and skipped == 0 and data == b"".join(
            dgram(p["context"], bytes.fromhex(p["payload"])) for p in packets)
        out.append({"id": cid, "note": note, "max_payload": max_payload, "hex": data.hex(),
                    "packets": packets, "skipped": skipped, "error": err, "encode": enc})

    add("datagram-context-0", "one packet, Context ID 0", dgram(0, b"hello"))
    add("datagram-context-nonzero", "Context ID 37 (1-byte varint) and 0x1234 (2-byte)",
        dgram(37, b"\x01\x02") + dgram(0x1234, b"\xff"))
    add("datagram-context-large", "Context ID 2^62-1 (8-byte varint)", dgram((1 << 62) - 1, b"x"))
    add("datagram-empty", "empty payload: Value is the Context ID alone", dgram(0, b""))
    add("datagram-length-2byte", "payload of 100 bytes: 2-byte Length", dgram(0, bytes(range(100))))
    add("sequence", "three packets in order",
        dgram(0, b"a") + dgram(1, b"bc") + dgram(0, b""))
    add("empty-stream", "no capsule at all", b"")
    add("unknown-type-skipped", "an unknown type (0x29) between two packets is skipped",
        dgram(0, b"a") + capsule(0x29, b"ignored") + dgram(0, b"b"))
    add("unknown-type-2byte", "unknown type with a 2-byte Type and empty Value",
        capsule(0x4321, b"") + dgram(5, b"z"))
    add("non-minimal-length", "Length in a non-minimal 2-byte varint is accepted",
        dgram(0, b"ok", length=2))
    add("at-bound", "payload exactly max_payload (16)", dgram(3, bytes(16)), max_payload=16)
    add("truncated-in-type", "stream ends inside a 2-byte Type", dgram(0, b"a") + b"\x40")
    add("truncated-in-length", "stream ends after the Type", dgram(0, b"a") + b"\x00")
    add("truncated-in-value", "Length 6, 3 bytes of Value", b"\x00\x06\x00abc")
    add("truncated-context", "DATAGRAM Value of 1 byte that starts a 2-byte Context ID",
        b"\x00\x01\x40")
    add("truncated-empty-value", "DATAGRAM with Length 0 has no Context ID", b"\x00\x00")
    add("oversized-payload", "payload 17 bytes above max_payload 16",
        dgram(0, b"a") + dgram(0, bytes(17)), max_payload=16)
    add("oversized-length", "Length 25 above max_payload 16 + 8, value never sent",
        dgram(0, b"a") + b"\x00\x19", max_payload=16)
    add("oversized-unknown-type", "the bound applies to skipped types too",
        capsule(0x29, bytes(25)), max_payload=16)
    add("oversized-default", "Length 65544 above the default bound 65535 + 8",
        b"\x00" + V(65544, 4))
    return out


def main():
    doc = {"spec": "EXTENSIONS.md Section 12", "format": FORMAT, "cases": cases()}
    errs = {c["id"]: c["error"] for c in doc["cases"]}
    assert errs["oversized-payload"] == "oversized" and errs["truncated-in-value"] == "truncated"
    (ROOT / "fixtures" / "capsules.json").write_text(json.dumps(doc, indent=1) + "\n")


if __name__ == "__main__":
    main()
