"""Field-by-field annotation of frame encodings for WIRE_EXAMPLES.md."""

from . import frames as F
from . import registry as R
from .varint import encode_varint as V


def _vf(name, value, width=None):
    return (name, V(value, width), str(value))


def _bf(name, b, note=None):
    return (name, bytes(b), note if note is not None else f"{len(b)} bytes")


def frame_fields(f):
    t = f["type"]
    code = F.type_code(f) if t not in ("PADDING", "RAW") else None
    out = []
    if t == "PADDING":
        return [("PADDING", b"\x00" * f.get("count", 1), f"{f.get('count', 1)} x 0x00")]
    tname = t
    if t == "STREAM":
        flags = []
        if code & F.OFF_BIT:
            flags.append("OFF")
        if code & F.LEN_BIT:
            flags.append("LEN")
        if code & F.FIN_BIT:
            flags.append("FIN")
        tname = f"STREAM ({'|'.join(flags) or 'no flags'})"
    elif t == "QX_PING":
        tname = "QX_PING " + ("response" if f.get("response") else "request")
    elif t == "CONNECTION_CLOSE":
        tname = "CONNECTION_CLOSE (" + ("application" if f.get("app") else "transport") + ")"
    out.append(("Type", V(code), f"0x{code:x} {tname}"))
    if t == "STREAM":
        out.append(_vf("Stream ID", f["id"]))
        if code & F.OFF_BIT:
            out.append(_vf("Offset", f.get("off", 0)))
        data = f.get("data", b"")
        if code & F.LEN_BIT:
            out.append(_vf("Length", len(data)))
        out.append(_bf("Stream Data", data))
    elif t == "RESET_STREAM":
        out += [_vf("Stream ID", f["id"]), _vf("Application Error Code", f["code"]),
                _vf("Final Size", f["final_size"])]
    elif t == "RESET_STREAM_AT":
        out += [_vf("Stream ID", f["id"]), _vf("Application Error Code", f["code"]),
                _vf("Final Size", f["final_size"]), _vf("Reliable Size", f["reliable_size"])]
    elif t == "STOP_SENDING":
        out += [_vf("Stream ID", f["id"]), _vf("Application Error Code", f["code"])]
    elif t == "MAX_DATA":
        out.append(_vf("Maximum Data", f["max"]))
    elif t == "MAX_STREAM_DATA":
        out += [_vf("Stream ID", f["id"]), _vf("Maximum Stream Data", f["max"])]
    elif t == "MAX_STREAMS":
        out.append(_vf("Maximum Streams", f["max"]))
    elif t == "DATA_BLOCKED":
        out.append(_vf("Maximum Data", f["limit"]))
    elif t == "STREAM_DATA_BLOCKED":
        out += [_vf("Stream ID", f["id"]), _vf("Maximum Stream Data", f["limit"])]
    elif t == "STREAMS_BLOCKED":
        out.append(_vf("Maximum Streams", f["limit"]))
    elif t == "CONNECTION_CLOSE":
        out.append(_vf("Error Code", f["code"]))
        if not f.get("app"):
            out.append(_vf("Frame Type", f.get("frame_type", 0)))
        reason = f.get("reason", b"")
        out.append(_vf("Reason Phrase Length", len(reason)))
        out.append(_bf("Reason Phrase", reason, repr(reason.decode("utf-8", "replace"))))
    elif t == "DATAGRAM":
        data = f.get("data", b"")
        if f.get("has_len", True):
            out.append(_vf("Length", len(data)))
        out.append(_bf("Datagram Data", data))
    elif t == "QX_TRANSPORT_PARAMETERS":
        body = F.encode_tps(f["params"])
        out.append(_vf("Length", len(body)))
        for p in f["params"]:
            pid = p["id"]
            num = R.tp_by_name(pid)["id"] if isinstance(pid, str) else pid
            name = pid if isinstance(pid, str) else (
                f"GREASE (31*{(num - 27) // 31}+27)" if R.is_grease_tp(num) else f"0x{num:x}")
            val = p["raw"] if "raw" in p else F.encode_tp_value(pid, p.get("value"))
            out.append((f"  {name}: ID", V(num), f"0x{num:x}"))
            out.append((f"  {name}: Length", V(len(val)), str(len(val))))
            if val:
                out.append((f"  {name}: Value", val, _tp_note(pid, p)))
    elif t == "QX_PING":
        out.append(_vf("Sequence Number", f["seq"]))
    elif t == "GOAWAY":
        out += [_vf("Maximum Bidirectional Streams", f["max_bidi"]),
                _vf("Maximum Unidirectional Streams", f["max_uni"]),
                _vf("Application Protocol Error Code", f["code"])]
    elif t == "PRIORITY_UPDATE":
        b = F.priority_byte(f)
        out += [_vf("Stream ID", f["id"]), _vf("Sequence Number", f["seq"]),
                ("Priority", bytes([b]),
                 f"urgency {f['urgency']}, incremental {int(bool(f.get('incremental')))}")]
    elif t == "PIPE_JOIN":
        loc = f["locator"]
        flags = {"up": 0, "down": 1, "bidi": 2}[f["direction"]] | (
            0x04 if f.get("carrier_bound") else 0) | (0x08 if f.get("standby") else 0)
        out += [_vf("Locator Length", len(loc)), _bf("Session Locator", loc, repr(loc)),
                _vf("Pipe ID", f["pipe"]),
                ("Flags", bytes([flags]), f"direction {f['direction']}"
                 + (", carrier-bound" if f.get("carrier_bound") else "")
                 + (", standby" if f.get("standby") else "")),
                _vf("Fate Group", f["fate_group"]), _vf("Priority", f.get("priority", 0)),
                _bf("Join Token", f["token"])]
    elif t == "PIPE_ACK":
        out.append(_vf("Entry Count", len(f["entries"])))
        for e in f["entries"]:
            out += [_vf("  Pipe ID", e["pipe"]), _vf("  Lane", e["lane"]),
                    _vf("  Position", e["position"]), _vf("  ACK Delay (us)", e["delay"])]
    elif t == "PIPE_LANE":
        out += [_bf("Lane Key", f["lane_key"]), _vf("Pipe ID", f["pipe"]),
                _vf("Lane", f["lane"])]
    elif t == "PIPE_PING":
        pass
    elif t == "PIPE_ABANDON":
        name = R.transport_error_name(f["code"]) or ""
        out += [_vf("Pipe ID", f["pipe"]), ("Error Code", V(f["code"]), f"0x{f['code']:x} {name}")]
    elif t == "PIPE_STATUS":
        st = f["status"]
        num = {"available": 0, "standby": 1}[st] if isinstance(st, str) else st
        out += [_vf("Pipe ID", f["pipe"]), _vf("Sequence Number", f["seq"]),
                ("Status", V(num), f"{num} ({st})"), _vf("Priority", f.get("priority", 0))]
    else:
        raise ValueError(t)
    return out


def _tp_note(pid, p):
    if "raw" in p:
        return "opaque"
    v = p.get("value")
    if isinstance(v, dict):
        if "role" in v:
            return f"role {v['role']}" + (f", nonce {v['nonce']}" if "nonce" in v else "")
        if "lane_key" in v:
            return f"Max Lanes {v['max_lanes']}, Lane Key {v['lane_key'].hex()}"
        return f"flags 0x{v['flags']:02x}, nonce {v['nonce'].hex()}, locator {v['locator']!r}"
    return str(v)


def table(fields):
    lines = ["| Field | Bytes | Value |", "|---|---|---|"]
    for name, b, note in fields:
        hx = " ".join(f"{x:02x}" for x in b) if len(b) <= 24 else (
            " ".join(f"{x:02x}" for x in b[:20]) + f" ... ({len(b)} bytes)")
        lines.append(f"| {name} | `{hx}` | {note} |")
    return "\n".join(lines)


def record_fields(frames):
    body = b"".join(F.encode_frame(f) for f in frames)
    out = [("Record Size", V(len(body)), str(len(body)))]
    for f in frames:
        out += frame_fields(f)
    return out
