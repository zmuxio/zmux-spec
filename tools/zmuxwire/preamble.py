"""Stream preamble (EXTENSIONS.md Section 4) and DATAGRAM flow prefix (Section 6)."""

from . import registry as R
from .varint import DecodeError, decode_varint, encode_varint

PV = "PROTOCOL_VIOLATION"


def encode_preamble(tlvs):
    """``tlvs``: list of (type, value_bytes)."""
    body = b"".join(encode_varint(t) + encode_varint(len(v)) + v for t, v in tlvs)
    return encode_varint(len(body)) + body


def decode_preamble(first_frame_data, limit):
    """Parse the preamble from the data of the STREAM frame at offset 0.

    Returns ``(parsed, total_len)`` where ``parsed`` maps known TLV names to
    values and lists ignored types. Raises DecodeError(PROTOCOL_VIOLATION).
    """
    if not first_frame_data:
        raise DecodeError(PV, "preamble missing")
    try:
        body_len, pos, _ = decode_varint(first_frame_data, 0, PV)
    except DecodeError:
        raise DecodeError(PV, "preamble length split across frames")
    total = pos + body_len
    if total > limit:
        raise DecodeError(PV, "preamble exceeds zmux_preamble limit")
    if total > len(first_frame_data):
        raise DecodeError(PV, "preamble split across frames")
    end = total
    parsed = {"ignored": []}
    seen = set()
    while pos < end:
        t, pos, _ = decode_varint(first_frame_data, pos, PV)
        if pos >= end:
            raise DecodeError(PV, "truncated preamble TLV")
        ln, pos, _ = decode_varint(first_frame_data, pos, PV)
        if pos > end or pos + ln > end:
            raise DecodeError(PV, "preamble TLV overruns preamble")
        val = first_frame_data[pos:pos + ln]
        pos += ln
        known = {x["type"]: x for x in R.PREAMBLE_TLVS if x["status"] == "active"}
        if t in known:
            if t in seen:
                raise DecodeError(PV, f"duplicate preamble TLV {known[t]['name']}")
            seen.add(t)
            name = known[t]["name"]
            if name == "PRIORITY":
                if ln != 1 or val[0] & 0x0F:
                    raise DecodeError(PV, "invalid PRIORITY TLV")
                parsed["priority"] = {"urgency": val[0] >> 5, "incremental": bool(val[0] & 0x10)}
            elif name == "OPEN_INFO":
                parsed["open_info"] = bytes(val)
            elif name == "PIPE_CARRIER":
                if ln != 0:
                    raise DecodeError(PV, "invalid PIPE_CARRIER TLV")
                parsed["pipe_carrier"] = True
        elif R.APP_TLV_FIRST <= t <= R.APP_TLV_LAST and not R.is_grease_tlv(t):
            parsed.setdefault("app_tlvs", []).append((t, bytes(val)))
        else:
            parsed["ignored"].append(t)
    return parsed, total


def encode_flow_prefix(stream_id=None, context_id=0):
    if stream_id is None:
        return encode_varint(0)
    return encode_varint(stream_id + 1) + encode_varint(context_id)


def decode_flow_prefix(data):
    flow, pos, _ = decode_varint(data, 0, PV)
    if flow == 0:
        return None, None, data[pos:]
    ctx, pos, _ = decode_varint(data, pos, PV)
    return flow - 1, ctx, data[pos:]
