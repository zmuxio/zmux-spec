"""Encoder and decoder for zmux records, frames and transport parameters.

Frames are plain dicts so they serialise to JSON unchanged (bytes values are
converted by ``to_json``/``from_json``). Every decode result re-encodes to the
identical bytes, which is what the wire corpus and WIRE_EXAMPLES rely on.

The decoder applies the context-free validity rules of SPEC.md Section 4 and
the negotiation gate of each frame type. Stream-state rules are applied by the
trace runner of each implementation, not here.
"""

from . import registry as R
from .varint import DecodeError, decode_varint, encode_varint, varint_len, MAX_VARINT

FE = "FRAME_ENCODING_ERROR"
PV = "PROTOCOL_VIOLATION"
TPE = "TRANSPORT_PARAMETER_ERROR"

STREAM_BASE = 0x08
OFF_BIT, LEN_BIT, FIN_BIT = 0x04, 0x02, 0x01


# --------------------------------------------------------------------------
# helpers

def _v(value, width=None):
    return encode_varint(value, width)


def _bytes(x):
    if x is None:
        return b""
    if isinstance(x, (bytes, bytearray)):
        return bytes(x)
    raise TypeError(f"expected bytes, got {type(x)!r}")


def type_code(frame):
    """The numeric frame type for a frame dict."""
    t = frame["type"]
    if t == "STREAM":
        off = frame.get("has_off", frame.get("off", 0) > 0)
        ln = frame.get("has_len", True)
        return STREAM_BASE | (OFF_BIT if off else 0) | (LEN_BIT if ln else 0) | (
            FIN_BIT if frame.get("fin") else 0)
    if t == "MAX_STREAMS":
        return 0x12 if frame["bidi"] else 0x13
    if t == "STREAMS_BLOCKED":
        return 0x16 if frame["bidi"] else 0x17
    if t == "CONNECTION_CLOSE":
        return 0x1D if frame.get("app") else 0x1C
    if t == "DATAGRAM":
        return 0x31 if frame.get("has_len", True) else 0x30
    if t == "QX_PING":
        return 0x348C67529EF8C7BE if frame.get("response") else 0x348C67529EF8C7BD
    return R.frame_by_name(t)["first"]


# --------------------------------------------------------------------------
# transport parameters

def encode_tp_value(name_or_id, value):
    meta = R.tp_by_name(name_or_id) if isinstance(name_or_id, str) else R.tp_by_id(name_or_id)
    fmt = meta.get("format") if meta else "opaque"
    if fmt == "varint":
        return _v(value)
    if fmt == "empty":
        return b""
    if fmt == "role":
        role = value["role"]
        code = {"initiator": 0, "responder": 1, "auto": 2}[role]
        out = _v(code)
        if role == "auto":
            out += int(value["nonce"]).to_bytes(8, "big")
        return out
    if fmt == "multipath":
        loc = _bytes(value["locator"])
        nonce = _bytes(value["nonce"])
        if len(nonce) != 16:
            raise ValueError("multipath nonce must be 16 bytes")
        return bytes([value["flags"]]) + nonce + bytes([len(loc)]) + loc
    if fmt == "lanes":
        return _v(value["max_lanes"]) + _lane_key(value["lane_key"])
    return _bytes(value)


def _lane_key(k):
    k = _bytes(k)
    if len(k) != R.LIMITS["lane_key_bytes"]:
        raise ValueError("a Lane Key is 16 bytes")
    return k


def encode_tps(params):
    """``params``: list of {"id": int|name, "value": ...} or {"id", "raw"}."""
    out = bytearray()
    for p in params:
        pid = p["id"]
        num = R.tp_by_name(pid)["id"] if isinstance(pid, str) else pid
        if "raw" in p:
            val = _bytes(p["raw"])
        else:
            val = encode_tp_value(pid if isinstance(pid, str) else num, p.get("value"))
        out += _v(num) + _v(len(val)) + val
    return bytes(out)


def decode_tp_value(num, raw):
    meta = R.tp_by_id(num)
    fmt = meta.get("format") if meta else None
    if meta is None or meta["scope"] == "prohibited" or fmt in (None, "opaque"):
        return {"id": num, "raw": raw}
    name = meta["name"]
    if fmt == "varint":
        v, pos, _ = decode_varint(raw, 0, TPE)
        if pos != len(raw):
            raise DecodeError(TPE, f"{name}: trailing bytes")
        return {"id": name, "value": v}
    if fmt == "empty":
        if raw:
            raise DecodeError(TPE, f"{name}: value must be empty")
        return {"id": name, "value": None}
    if fmt == "role":
        code, pos, _ = decode_varint(raw, 0, TPE)
        if code not in (0, 1, 2):
            raise DecodeError(TPE, "zmux_role: invalid role")
        role = R.ROLE_VALUES[code]
        val = {"role": role}
        if role == "auto":
            if len(raw) - pos != 8:
                raise DecodeError(TPE, "zmux_role: auto requires a 64-bit nonce")
            val["nonce"] = int.from_bytes(raw[pos:], "big")
        elif pos != len(raw):
            raise DecodeError(TPE, "zmux_role: trailing bytes")
        return {"id": name, "value": val}
    if fmt == "multipath":
        if len(raw) < 18:
            raise DecodeError(TPE, "zmux_multipath: too short")
        flags = raw[0]
        nonce = raw[1:17]
        ln = raw[17]
        if ln > R.LIMITS["max_locator_bytes"]:
            raise DecodeError(TPE, "zmux_multipath: locator longer than 16 bytes")
        if len(raw) != 18 + ln:
            raise DecodeError(TPE, "zmux_multipath: length mismatch")
        return {"id": name, "value": {"flags": flags, "nonce": bytes(nonce),
                                      "locator": bytes(raw[18:])}}
    if fmt == "lanes":
        n, pos, _ = decode_varint(raw, 0, TPE)
        if len(raw) - pos != R.LIMITS["lane_key_bytes"]:
            raise DecodeError(TPE, "zmux_max_lanes: Lane Key is not 16 bytes")
        if n == 0:
            raise DecodeError(TPE, "zmux_max_lanes: Max Lanes is 0")
        return {"id": name, "value": {"max_lanes": n, "lane_key": bytes(raw[pos:])}}
    return {"id": num, "raw": raw}


def decode_tps(buf):
    """Decode and validate a transport-parameter block (SPEC.md Section 5)."""
    pos = 0
    out = []
    seen = set()
    while pos < len(buf):
        num, pos, _ = decode_varint(buf, pos, TPE)
        ln, pos, _ = decode_varint(buf, pos, TPE)
        if pos + ln > len(buf):
            raise DecodeError(TPE, "truncated transport parameter")
        raw = bytes(buf[pos:pos + ln])
        pos += ln
        if num in seen:
            raise DecodeError(TPE, f"duplicate transport parameter 0x{num:x}")
        seen.add(num)
        meta = R.tp_by_id(num)
        if meta is not None and meta["scope"] == "prohibited":
            raise DecodeError(TPE, f"prohibited transport parameter {meta['name']}")
        out.append(decode_tp_value(num, raw))
    validate_tps(out)
    return out


def tp_dict(params):
    d = {}
    for p in params:
        if isinstance(p["id"], str):
            d[p["id"]] = p.get("value")
    return d


def validate_tps(params, local_max_record_size=None):
    """Value and dependency checks from SPEC.md 5.2, EXTENSIONS.md, MULTIPATH.md 3."""
    d = tp_dict(params)
    if "max_record_size" in d and d["max_record_size"] < R.LIMITS["default_max_record_size"]:
        raise DecodeError(TPE, "max_record_size below 16382")
    for k in ("initial_max_streams_bidi", "initial_max_streams_uni"):
        if k in d and d[k] > (1 << 60):
            raise DecodeError(TPE, f"{k} above 2^60")
    if "zmux_preamble" in d:
        if "reset_stream_at" not in d:
            raise DecodeError(TPE, "zmux_preamble requires reset_stream_at")
        if d["zmux_preamble"] < 1:
            raise DecodeError(TPE, "zmux_preamble below 1")
        own_mrs = d.get("max_record_size", R.LIMITS["default_max_record_size"])
        if d["zmux_preamble"] > own_mrs - R.LIMITS["max_preamble_tp_overhead"]:
            raise DecodeError(TPE, "zmux_preamble above max_record_size - 32")
        for k in ("initial_max_stream_data_bidi_remote", "initial_max_stream_data_uni"):
            if d["zmux_preamble"] > d.get(k, 0):
                raise DecodeError(TPE, f"zmux_preamble above {k}")
    for k in ("zmux_ack_threshold", "zmux_max_ack_delay", "zmux_max_lanes"):
        if k in d and "zmux_multipath" not in d and "zmux_pipe_ack" not in d:
            raise DecodeError(TPE, f"{k} requires zmux_multipath or zmux_pipe_ack")
    if "zmux_resume" in d and "zmux_multipath" not in d:
        raise DecodeError(TPE, "zmux_resume requires zmux_multipath")
    if "zmux_ack_threshold" in d and d["zmux_ack_threshold"] < 1024:
        raise DecodeError(TPE, "zmux_ack_threshold out of range")
    if "zmux_max_ack_delay" in d and d["zmux_max_ack_delay"] >= (1 << 14):
        raise DecodeError(TPE, "zmux_max_ack_delay out of range")
    if "zmux_resume" in d and d["zmux_resume"] < 1:
        raise DecodeError(TPE, "zmux_resume out of range")
    return d


# --------------------------------------------------------------------------
# frames

def encode_frame(f, type_width=None):
    t = f["type"]
    if t == "RAW":
        return _bytes(f["bytes"])
    if t == "REPEAT":
        return encode_frame(f["frame"]) * f["count"]
    if t == "PADDING":
        return b"\x00" * f.get("count", 1)
    code = type_code(f)
    out = bytearray(_v(code, type_width))
    if t == "STREAM":
        data = _bytes(f.get("data"))
        out += _v(f["id"])
        if code & OFF_BIT:
            out += _v(f.get("off", 0))
        if code & LEN_BIT:
            out += _v(len(data))
        out += data
    elif t == "RESET_STREAM":
        out += _v(f["id"]) + _v(f["code"]) + _v(f["final_size"])
    elif t == "RESET_STREAM_AT":
        out += _v(f["id"]) + _v(f["code"]) + _v(f["final_size"]) + _v(f["reliable_size"])
    elif t == "STOP_SENDING":
        out += _v(f["id"]) + _v(f["code"])
    elif t == "MAX_DATA":
        out += _v(f["max"])
    elif t == "MAX_STREAM_DATA":
        out += _v(f["id"]) + _v(f["max"])
    elif t == "MAX_STREAMS":
        out += _v(f["max"])
    elif t == "DATA_BLOCKED":
        out += _v(f["limit"])
    elif t == "STREAM_DATA_BLOCKED":
        out += _v(f["id"]) + _v(f["limit"])
    elif t == "STREAMS_BLOCKED":
        out += _v(f["limit"])
    elif t == "CONNECTION_CLOSE":
        reason = _bytes(f.get("reason"))
        out += _v(f["code"])
        if not f.get("app"):
            out += _v(f.get("frame_type", 0))
        out += _v(len(reason)) + reason
    elif t == "DATAGRAM":
        data = _bytes(f.get("data"))
        if f.get("has_len", True):
            out += _v(len(data))
        out += data
    elif t == "QX_TRANSPORT_PARAMETERS":
        body = encode_tps(f["params"]) if "params" in f else _bytes(f["raw"])
        out += _v(len(body)) + body
    elif t == "QX_PING":
        out += _v(f["seq"])
    elif t == "GOAWAY":
        out += _v(f["max_bidi"]) + _v(f["max_uni"]) + _v(f["code"])
    elif t == "PRIORITY_UPDATE":
        out += _v(f["id"]) + _v(f["seq"]) + bytes([priority_byte(f)])
    elif t == "PIPE_JOIN":
        out += encode_join_body(f)
    elif t == "PIPE_ACK":
        out += _v(len(f["entries"]))
        for e in f["entries"]:
            out += _v(e["pipe"]) + _v(e["lane"]) + _v(e["position"]) + _v(e["delay"])
    elif t == "PIPE_LANE":
        out += _lane_key(f["lane_key"]) + _v(f["pipe"]) + _v(f["lane"])
    elif t == "PIPE_PING":
        pass
    elif t == "PIPE_ABANDON":
        out += _v(f["pipe"]) + _v(f["code"])
    elif t == "PIPE_STATUS":
        st = f["status"]
        out += _v(f["pipe"]) + _v(f["seq"]) + _v({"available": 0, "standby": 1}.get(st, st)
                                                 if isinstance(st, str) else st)
        out += _v(f.get("priority", R.PIPE_PRIORITY_DEFAULT))
    else:
        raise ValueError(f"cannot encode frame type {t}")
    return bytes(out)


def priority_byte(f):
    u = f["urgency"]
    if not 0 <= u <= 7:
        raise ValueError("urgency out of range")
    return (u << 5) | (0x10 if f.get("incremental") else 0)


def encode_join_body(f):
    loc = _bytes(f["locator"])
    flags = {"up": 0, "down": 1, "bidi": 2}[f["direction"]] if isinstance(
        f["direction"], str) else f["direction"]
    if f.get("carrier_bound"):
        flags |= R.PIPE_JOIN_FLAG_CARRIER_BOUND
    if f.get("standby"):
        flags |= R.PIPE_JOIN_FLAG_STANDBY
    flags |= f.get("reserved_flags", 0)
    return (bytes([len(loc)]) + loc + _v(f["pipe"]) + bytes([flags]) + _v(f["fate_group"])
            + _v(f.get("priority", R.PIPE_PRIORITY_DEFAULT)) + _bytes(f["token"]))


def join_bytes_without_token(f):
    """PIPE_JOIN encoding from Type through Priority (the MAC input prefix)."""
    full = encode_frame(f)
    return full[:-16]


def _need(buf, pos, n, end):
    if pos + n > end:
        raise DecodeError(FE, "truncated frame")


def decode_frame(buf, pos, end, enabled=None, limits=None):
    """Decode one frame from ``buf[pos:end]``.

    ``enabled`` is the set of negotiation gates in effect (see
    ``registry.FRAME_TYPES[*]['gate']``); ``None`` means "everything enabled".
    ``limits`` holds sized gates, for example ``{"max_datagram_frame_size": n}``.
    Decode errors are raised before semantic ones (SPEC.md Section 10.3,
    precedence). Returns ``(frame, new_pos)``.
    """
    start = pos
    code, pos, width = decode_varint(buf, pos, FE)
    if width != varint_len(code):
        raise DecodeError(PV, "frame type not minimally encoded")
    if code == 0x00:
        n = 1
        while pos < end and buf[pos] == 0x00:
            pos += 1
            n += 1
        return {"type": "PADDING", "count": n}, pos
    meta = R.frame_by_type(code)
    if meta is None:
        raise DecodeError(FE, f"unknown frame type 0x{code:x}")
    if meta["kind"] == "prohibited":
        raise DecodeError(FE, f"prohibited frame type {meta['name']}")
    gate = meta.get("gate")
    gates = (gate,) if isinstance(gate, str) else (gate or ())
    if gates and enabled is not None and not any(g in enabled for g in gates):
        if meta["name"] == "DATAGRAM":
            raise DecodeError(PV, "DATAGRAM without max_datagram_frame_size")
        raise DecodeError(FE, f"{meta['name']} not negotiated")
    name = meta["name"]

    def v():
        nonlocal pos
        if pos >= end:
            raise DecodeError(FE, "truncated frame")
        val, pos2, w = decode_varint(buf, pos, FE)
        if pos2 > end:
            raise DecodeError(FE, "truncated frame")
        pos = pos2
        return val

    f = {"type": name}
    if name == "STREAM":
        f["id"] = v()
        has_off = bool(code & OFF_BIT)
        has_len = bool(code & LEN_BIT)
        f["off"] = v() if has_off else 0
        if has_len:
            ln = v()
            _need(buf, pos, ln, end)
        else:
            ln = end - pos
        f["data"] = bytes(buf[pos:pos + ln])
        pos += ln
        f["fin"] = bool(code & FIN_BIT)
        f["has_off"] = has_off
        f["has_len"] = has_len
        if f["off"] + ln > MAX_VARINT:
            raise DecodeError(FE, "stream offset beyond 2^62-1")
    elif name == "RESET_STREAM":
        f["id"], f["code"], f["final_size"] = v(), v(), v()
    elif name == "RESET_STREAM_AT":
        f["id"], f["code"], f["final_size"], f["reliable_size"] = v(), v(), v(), v()
        if f["reliable_size"] > f["final_size"]:
            raise DecodeError(FE, "reliable size above final size")
    elif name == "STOP_SENDING":
        f["id"], f["code"] = v(), v()
    elif name == "MAX_DATA":
        f["max"] = v()
    elif name == "MAX_STREAM_DATA":
        f["id"], f["max"] = v(), v()
    elif name == "MAX_STREAMS":
        f["bidi"] = code == 0x12
        f["max"] = v()
        if f["max"] > (1 << 60):
            raise DecodeError(FE, "MAX_STREAMS above 2^60")
    elif name == "DATA_BLOCKED":
        f["limit"] = v()
    elif name == "STREAM_DATA_BLOCKED":
        f["id"], f["limit"] = v(), v()
    elif name == "STREAMS_BLOCKED":
        f["bidi"] = code == 0x16
        f["limit"] = v()
        if f["limit"] > (1 << 60):
            raise DecodeError(FE, "STREAMS_BLOCKED above 2^60")
    elif name == "CONNECTION_CLOSE":
        f["app"] = code == 0x1D
        f["code"] = v()
        if not f["app"]:
            f["frame_type"] = v()
        ln = v()
        _need(buf, pos, ln, end)
        f["reason"] = bytes(buf[pos:pos + ln])
        pos += ln
    elif name == "DATAGRAM":
        f["has_len"] = code == 0x31
        if f["has_len"]:
            ln = v()
            _need(buf, pos, ln, end)
        else:
            ln = end - pos
        f["data"] = bytes(buf[pos:pos + ln])
        pos += ln
    elif name == "QX_TRANSPORT_PARAMETERS":
        ln = v()
        _need(buf, pos, ln, end)
        f["params"] = decode_tps(bytes(buf[pos:pos + ln]))
        pos += ln
    elif name == "QX_PING":
        f["response"] = code == 0x348C67529EF8C7BE
        f["seq"] = v()
    elif name == "GOAWAY":
        f["max_bidi"], f["max_uni"], f["code"] = v(), v(), v()
        if f["max_bidi"] > (1 << 60) or f["max_uni"] > (1 << 60):
            raise DecodeError(FE, "GOAWAY count above 2^60")
    elif name == "PRIORITY_UPDATE":
        f["id"], f["seq"] = v(), v()
        _need(buf, pos, 1, end)
        b = buf[pos]
        pos += 1
        if b & 0x0F:
            raise DecodeError(FE, "PRIORITY_UPDATE reserved bits set")
        f["urgency"] = b >> 5
        f["incremental"] = bool(b & 0x10)
    elif name == "PIPE_JOIN":
        _need(buf, pos, 1, end)
        ln = buf[pos]
        pos += 1
        if ln < 1 or ln > R.LIMITS["max_locator_bytes"]:
            raise DecodeError(FE, "PIPE_JOIN locator length out of range")
        _need(buf, pos, ln, end)
        f["locator"] = bytes(buf[pos:pos + ln])
        pos += ln
        f["pipe"] = v()
        _need(buf, pos, 1, end)
        flags = buf[pos]
        pos += 1
        d = flags & 0x03
        if d == 3:
            raise DecodeError(FE, "PIPE_JOIN invalid direction")
        if flags & R.PIPE_JOIN_RESERVED_MASK:
            raise DecodeError(FE, "PIPE_JOIN reserved flag set")
        f["direction"] = R.PIPE_DIRECTIONS[d]
        f["carrier_bound"] = bool(flags & R.PIPE_JOIN_FLAG_CARRIER_BOUND)
        f["standby"] = bool(flags & R.PIPE_JOIN_FLAG_STANDBY)
        f["fate_group"] = v()
        f["priority"] = v()
        _need(buf, pos, 16, end)
        f["token"] = bytes(buf[pos:pos + 16])
        pos += 16
        if pos - start > R.LIMITS["max_join_frame_bytes"]:
            raise DecodeError(FE, "PIPE_JOIN longer than 64 bytes")
    elif name == "PIPE_ACK":
        n = v()
        if n == 0:
            raise DecodeError(FE, "PIPE_ACK without entries")
        entries = []
        for _ in range(n):
            e = {"pipe": v(), "lane": v(), "position": v(), "delay": v()}
            if entries and (e["pipe"], e["lane"]) <= (entries[-1]["pipe"], entries[-1]["lane"]):
                raise DecodeError(FE, "PIPE_ACK entries not in strictly increasing "
                                      "(Pipe ID, Lane) order")
            entries.append(e)
        f["entries"] = entries
    elif name == "PIPE_LANE":
        n = R.LIMITS["lane_key_bytes"]
        _need(buf, pos, n, end)
        f["lane_key"] = bytes(buf[pos:pos + n])
        pos += n
        f["pipe"], f["lane"] = v(), v()
    elif name == "PIPE_PING":
        pass
    elif name == "PIPE_ABANDON":
        f["pipe"], f["code"] = v(), v()
    elif name == "PIPE_STATUS":
        f["pipe"], f["seq"] = v(), v()
        st = v()
        f["priority"] = v()
        if st not in R.PIPE_STATUS_VALUES:
            raise DecodeError(FE, "PIPE_STATUS invalid status")
        f["status"] = R.PIPE_STATUS_VALUES[st]
    else:  # pragma: no cover - registry and decoder out of sync
        raise DecodeError(FE, f"no decoder for {name}")
    if pos > end:
        raise DecodeError(FE, "truncated frame")
    if name == "DATAGRAM" and limits and "max_datagram_frame_size" in limits \
            and pos - start > limits["max_datagram_frame_size"]:
        raise DecodeError(PV, "DATAGRAM larger than max_datagram_frame_size")
    return f, pos


def decode_frames(payload, enabled=None, limits=None):
    pos = 0
    frames = []
    while pos < len(payload):
        f, pos = decode_frame(payload, pos, len(payload), enabled, limits)
        frames.append(f)
    return frames


# --------------------------------------------------------------------------
# records

def encode_record(frames, size_width=None):
    body = b"".join(encode_frame(f) for f in frames)
    return _v(len(body), size_width) + body


def encode_frames(frames):
    """The Frames field of a record without its Size (one message on a
    message-framed carrier, SPEC.md Section 3.4)."""
    return b"".join(encode_frame(f) for f in frames)


def decode_record(buf, pos, max_record_size=R.LIMITS["default_max_record_size"],
                  enabled=None):
    """Decode one record; returns ``(frames, new_pos)`` or ``(None, pos)`` when
    the buffer does not yet hold a complete record."""
    if pos >= len(buf):
        return None, pos
    first = buf[pos]
    width = 1 << (first >> 6)
    if pos + width > len(buf):
        return None, pos
    size, body_start, _ = decode_varint(buf, pos, FE)
    if size == 0:
        raise DecodeError(FE, "empty record")
    if size > max_record_size:
        raise DecodeError(FE, "record larger than max_record_size")
    if body_start + size > len(buf):
        return None, pos
    frames = decode_frames(bytes(buf[body_start:body_start + size]), enabled)
    return frames, body_start + size


def decode_message(msg, max_record_size=R.LIMITS["zmux_max_record_size"], enabled=None):
    """Decode one message of a message-framed carrier (SPEC.md Section 3.4):
    the message is a record's Frames field and its length is the Size. An empty
    message is a record without frames, a no-op (it is charged like a PADDING-only
    record, which is the receiver's budget, not decoding)."""
    if len(msg) == 0:
        return []
    if len(msg) > max_record_size:
        raise DecodeError(FE, "message longer than max_record_size")
    return decode_frames(msg, enabled)


def message_position(msg):
    """Bytes a message counts for in pipe positions: its byte-stream form."""
    return len(msg) + len(encode_varint(len(msg)))


def decode_stream(buf, max_record_size=R.LIMITS["default_max_record_size"], enabled=None,
                  require_complete=True):
    pos = 0
    records = []
    while pos < len(buf):
        frames, npos = decode_record(buf, pos, max_record_size, enabled)
        if frames is None:
            if require_complete:
                raise DecodeError(FE, "truncated record at end of pipe")
            break
        records.append(frames)
        pos = npos
    return records


QX_TP_PREFIX = encode_varint(R.frame_by_name("QX_TRANSPORT_PARAMETERS")["first"])
PIPE_JOIN_PREFIX = encode_varint(R.frame_by_name("PIPE_JOIN")["first"])
PIPE_LANE_PREFIX = encode_varint(R.frame_by_name("PIPE_LANE")["first"])
MIN_TP_RECORD = len(QX_TP_PREFIX) + 1      # type + Length(0)
MIN_JOIN_RECORD = len(PIPE_JOIN_PREFIX) + 1 + 1 + 1 + 1 + 1 + 1 + 16
MIN_LANE_RECORD = len(PIPE_LANE_PREFIX) + R.LIMITS["lane_key_bytes"] + 1 + 1  # + Pipe ID, Lane


def classify_first_bytes(prefix, accept_join=True, accept_lane=False,
                         max_record_size=R.LIMITS["zmux_max_record_size"]):
    """Incremental first-record check (SPEC.md Section 6.2, MULTIPATH.md 5.5).

    Returns "session" once ``prefix`` proves a QX_TRANSPORT_PARAMETERS first
    record, "join" once it proves a PIPE_JOIN first record, "lane" once it
    proves a PIPE_LANE first record (only with ``accept_lane``: a carrier
    delivered as a stream of a known carrier connection), "need-more" while
    undecided, and "reject" as soon as no valid first record can start with
    these bytes. ``max_record_size`` is the receiver's own limit.
    """
    if not prefix:
        return "need-more"
    width = 1 << (prefix[0] >> 6)
    if len(prefix) < width:
        return "need-more"
    size, p, _ = decode_varint(prefix, 0)
    rest = bytes(prefix[p:])
    candidates = []
    if MIN_TP_RECORD <= size <= max_record_size:
        candidates.append(("session", QX_TP_PREFIX))
    if accept_join and MIN_JOIN_RECORD <= size <= max_record_size:
        candidates.append(("join", PIPE_JOIN_PREFIX))
    if accept_lane and MIN_LANE_RECORD <= size <= max_record_size:
        candidates.append(("lane", PIPE_LANE_PREFIX))
    alive = [(k, m) for k, m in candidates if m[:len(rest)] == rest[:len(m)]]
    if not alive:
        return "reject"
    for k, m in alive:
        if len(rest) >= len(m):
            return k
    return "need-more"


# --------------------------------------------------------------------------
# JSON helpers (hex for bytes)

BYTES_FIELDS = {"data", "reason", "token", "locator", "mac", "raw", "bytes", "nonce_bytes",
                "lane_key"}


def to_json(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(v, (bytes, bytearray)):
                out[k] = bytes(v).hex()
            else:
                out[k] = to_json(v)
        return out
    if isinstance(obj, list):
        return [to_json(x) for x in obj]
    if isinstance(obj, (bytes, bytearray)):
        return bytes(obj).hex()
    return obj


def from_json(obj, parent_key=None):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in BYTES_FIELDS and isinstance(v, str):
                out[k] = bytes.fromhex(v)
            elif k == "value" and isinstance(v, dict) and "locator" in v:
                vv = dict(v)
                vv["locator"] = bytes.fromhex(v["locator"])
                vv["nonce"] = bytes.fromhex(v["nonce"]) if isinstance(v["nonce"], str) else v["nonce"]
                out[k] = vv
            else:
                out[k] = from_json(v, k)
        return out
    if isinstance(obj, list):
        return [from_json(x, parent_key) for x in obj]
    return obj
