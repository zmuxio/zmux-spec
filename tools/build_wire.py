#!/usr/bin/env python3
"""Generate WIRE_EXAMPLES.md and the wire corpus (fixtures/wire/*.ndjson)."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from zmuxwire import frames as F, crypto as C, registry as R  # noqa: E402
from zmuxwire import annotate as A  # noqa: E402
from zmuxwire import tracedsl as D  # noqa: E402
from zmuxwire.tracedsl import (STREAM, RESET, RESET_AT, STOP, MAX_DATA, MAX_STREAM_DATA,  # noqa
                               MAX_STREAMS, DATA_BLOCKED, STREAM_DATA_BLOCKED, STREAMS_BLOCKED,
                               CLOSE, PING, PONG, PADDING, DATAGRAM, GOAWAY, PRIO, PIPE_ACK,
                               PIPE_PING, PIPE_ABANDON, PIPE_STATUS, PIPE_LANE,
                               SESSION_EPOCH, TP, RAW, pre)
from zmuxwire.varint import encode_varint as V  # noqa: E402
from zmuxwire.preamble import encode_flow_prefix  # noqa: E402

TE, AE = R.transport_error, R.app_error
ALL_GATES = {g for f in R.FRAME_TYPES if f.get("gate")
             for g in ((f["gate"],) if isinstance(f["gate"], str) else f["gate"])}
ACK_T = R.frame_by_name("PIPE_ACK")["first"]
LANE_T = R.frame_by_name("PIPE_LANE")["first"]
EXAMPLE_LANE_KEY = bytes.fromhex("8d2f6a01c7e4b35a90f1d26e4b7c0a39")


# =============================================================================
# examples

def examples():
    ex = []

    def add(section, eid, title, text, frames=None, record=True, raw=None):
        ex.append(dict(section=section, id=eid, title=title, text=text, frames=frames,
                       record=record, raw=raw))

    zmux_tp = [
        {"id": "max_idle_timeout", "value": 30000},
        {"id": "initial_max_data", "value": 1048576},
        {"id": "initial_max_stream_data_bidi_local", "value": 262144},
        {"id": "initial_max_stream_data_bidi_remote", "value": 262144},
        {"id": "initial_max_stream_data_uni", "value": 262144},
        {"id": "initial_max_streams_bidi", "value": 256},
        {"id": "initial_max_streams_uni", "value": 256},
        {"id": "reset_stream_at", "value": None},
        {"id": "zmux_goaway", "value": None},
        {"id": "zmux_preamble", "value": 4096},
        {"id": "zmux_priority", "value": None},
        {"id": "zmux_pipe_ack", "value": None},
        {"id": "zmux_max_lanes", "value": D.lanes(258, EXAMPLE_LANE_KEY)},
        {"id": "max_record_size", "value": 65535},
        {"id": 27 + 31 * 3, "raw": bytes.fromhex("9a7b")},
    ]
    add("1", "tp-zmux-default", "Transport parameters of a zmux endpoint",
        "The first record of a bootstrap pipe: QX_TRANSPORT_PARAMETERS with the recommended "
        "values, `max_incoming_uni_streams` configured to 256 (unset, "
        "it would send 16 and raise the limit to 256 on the first accept"
        "), `zmux_max_lanes` with Max Lanes 258 and a Lane Key (a fixed example "
        "here; an endpoint chooses it at random for each session), "
        "and one GREASE parameter (31 x 3 + 27 = 120).",
        [TP(zmux_tp)])
    add("1", "tp-qmux-plain", "Transport parameters of a plain QMux peer",
        "What a plain QMux endpoint may send: only QUIC parameters, zero values omitted. A zmux "
        "endpoint receiving this runs the session in plain QMux mode.",
        [TP([{"id": "initial_max_data", "value": 1048576},
             {"id": "initial_max_stream_data_bidi_remote", "value": 262144},
             {"id": "initial_max_streams_bidi", "value": 100}])])
    add("1", "tp-zmux-multipath", "Multipath and role parameters",
        "zmux_role auto with nonce 0x0123456789abcdef; zmux_multipath with the PSK flag, a "
        "16-byte nonce and the 5-byte locator \"LOCAL\"; Join Suites 1 and 2 and Groups 1 "
        "and 2; companions; "
        "zmux_resume 30 s.",
        [TP([{"id": "zmux_role", "value": {"role": "auto", "nonce": 0x0123456789ABCDEF}},
             {"id": "zmux_multipath", "value": {"flags": 0x04, "nonce": D.LOCAL_NONCE,
                                                "locator": D.LOCAL_LOCATOR}},
             {"id": "zmux_session_crypto", "value": {"suites": [1, 2], "groups": [1, 2]}},
             {"id": "zmux_ack_threshold", "value": 65536},
             {"id": "zmux_max_ack_delay", "value": 25},
             {"id": "zmux_resume", "value": 30000}])])
    add("2", "stream-open-preamble", "Opening a stream with a preamble",
        "The first frame of stream 0: a preamble with OPEN_INFO \"example.com:443\", "
        "PRIORITY urgency 1, and a GREASE TLV (type 0x21, empty), then 5 bytes of data and FIN. "
        "Offset 0 is implicit (no OFF bit).",
        [STREAM(0, pre(open_info=b"example.com:443", priority=(1, False), grease=True)
                + b"hello", fin=True)])
    add("2", "early-data", "First record with early data",
        "A client whose application declares the peer to be a zmux endpoint opens stream 0 "
        "before the peer's parameters arrive, as early data: after its "
        "QX_TRANSPORT_PARAMETERS (abbreviated here to initial_max_data) comes the PIPE_ACK "
        "fence, one entry for pipe 0, lane 0, Position 0, ACK Delay 0, then the STREAM frame "
        "with the preamble (GREASE TLV) and 3 bytes of data, within the floor of 4096 bytes. "
        "A plain QMux peer closes at the PIPE_ACK and processes nothing after it.",
        [TP([{"id": "initial_max_data", "value": 1048576}]), PIPE_ACK((0, 0, 0)),
         STREAM(0, pre(grease=True) + b"GET", fin=True)])
    add("2", "stream-continue", "Continuing a stream",
        "Stream 4 data at offset 1200, followed by a second frame for stream 0 that omits its "
        "Length and therefore runs to the end of the record.",
        [STREAM(4, b"more data", off=1200), STREAM(0, b"tail", off=27, has_len=False)])
    add("2", "control-batch", "A batch of flow-control frames",
        "Coalesced credit updates in one record.",
        [MAX_DATA(1310720), MAX_STREAM_DATA(0, 327680), MAX_STREAMS(260),
         MAX_STREAMS(258, bidi=False)])
    add("2", "blocked", "BLOCKED frames",
        "Advisory frames at the limits currently in force.",
        [DATA_BLOCKED(1048576), STREAM_DATA_BLOCKED(4, 262144), STREAMS_BLOCKED(256)])
    add("2", "stop-and-reset", "STOP_SENDING answered by RESET_STREAM",
        "The peer stops stream 1 with ZMUX_CANCELLED; the sender answers with the same code.",
        [STOP(1, AE("ZMUX_CANCELLED")), RESET(1, AE("ZMUX_CANCELLED"), 4000)])
    add("2", "reset-stream-at", "RESET_STREAM_AT protecting a preamble",
        "Stream 0 reset with ZMUX_PROXY_CONNECTION_REFUSED; the first 19 bytes (the preamble) "
        "stay reliable.",
        [RESET_AT(0, AE("ZMUX_PROXY_CONNECTION_REFUSED"), 24, 19)])
    add("2", "close-transport", "CONNECTION_CLOSE with a transport error",
        "FLOW_CONTROL_ERROR caused by a STREAM frame (type 0x0e).",
        [CLOSE(TE("FLOW_CONTROL_ERROR"), frame_type=0x0E, reason=b"stream 4 window")])
    add("2", "close-application", "CONNECTION_CLOSE with an application code",
        "An application closing with code 0 and reason \"bye\".",
        [CLOSE(0, app=True, reason=b"bye")])
    add("2", "close-excessive-load", "Closing for excessive load",
        "ZMUX_EXCESSIVE_LOAD uses a four-byte code.",
        [CLOSE(TE("ZMUX_EXCESSIVE_LOAD"))])
    add("2", "qx-ping", "QX_PING request and response",
        "Eight-byte frame types from QMux-02 Section 4.3.", [PING(7), PONG(7)])
    add("2", "padding", "PADDING around a frame", "PADDING is one zero byte per frame.",
        [PADDING(3), MAX_DATA(2048), PADDING(2)])
    add("3", "goaway", "GOAWAY",
        "Final GOAWAY after the fence: streams of the receiver with index 6 or more "
        "(bidirectional) and 0 or more (unidirectional) were not processed.",
        [GOAWAY(6, 0, 0)])
    add("3", "priority-update", "PRIORITY_UPDATE", "Stream 4, sequence 2, urgency 0, incremental.",
        [PRIO(4, 2, 0, True)])
    add("3", "datagram-flow", "DATAGRAM with a flow prefix",
        "Associated with stream 4 (Flow 5), context 0, payload \"udp-payload\".",
        [DATAGRAM(encode_flow_prefix(4, 0) + b"udp-payload")])
    add("3", "datagram-session", "Session-wide DATAGRAM",
        "Flow 0 and no length field: the payload runs to the end of the record.",
        [DATAGRAM(encode_flow_prefix(None) + b"ping", has_len=False)])
    add("4", "pipe-ack", "PIPE_ACK",
        "Positions of inbound pipes 0 and 2 with their acknowledgement delays in microseconds; "
        "pipe 2 has a second lane, acknowledged in an entry of its own"
        ". Entries are in increasing (Pipe ID, Lane) order.",
        [PIPE_ACK((0, 0, 1048576, 1500), (2, 0, 65600, 250), (2, 1, 401920, 250))])
    add("4", "pipe-lane", "First record of a lane",
        "The writer of pipe 2 opens lane 1 of it on a new stream of the connection that "
        "carries the pipe's lane 0. The record holds only "
        "PIPE_LANE, with the receiver's Lane Key from the `zmux_max_lanes` example of "
        "the bootstrap record above; the writer sends nothing else on the lane until a PIPE_ACK confirms "
        "it. The lane's position counts from this record's first byte.",
        [PIPE_LANE(2, 1, EXAMPLE_LANE_KEY)])
    add("4", "pipe-control", "PIPE_PING, PIPE_STATUS, PIPE_ABANDON",
        "Probe; ask the peer to use its outbound direction of pipe 3 as a less-preferred "
        "tier (priority 10, available, sequence 1) and pipe 5 only on failover (standby, "
        "priority 0); abandon pipe 2 after a failure.",
        [PIPE_PING(), PIPE_STATUS(3, 1, "available", 10), PIPE_STATUS(5, 1, "standby", 0),
         PIPE_ABANDON(2, TE("ZMUX_PIPE_FAILED"))])
    add("4", "session-epoch-offer", "SESSION_EPOCH offer",
        "The offer of epoch 1 with the initiator share of Group "
        "2, the X25519 public key followed by the ML-KEM-768 encapsulation key (fixed example "
        "values), and a new Session Locator \"LOCAL-1\" for epoch 1.",
        [SESSION_EPOCH("offer", 1, D.PEER_OFFER_SHARE, b"LOCAL-1")])
    add("4", "session-epoch-answer", "SESSION_EPOCH answer",
        "The answer of epoch 1 with the responder share of Group 2, the X25519 public key "
        "followed by the ML-KEM-768 ciphertext (fixed example values); Locator Length 0 keeps "
        "the answering end's Session Locator.",
        [SESSION_EPOCH("answer", 1, D.PEER_ANSWER_SHARE)])
    add("5", "rejoin-record", "First record of a rejoined pipe",
        "After a suspension the initiator joins bidirectional pipe 6. Its first record after the "
        "handshake carries a complete PIPE_ACK snapshot"
        ": every inbound pipe that is not retired and has a position, the failed pipe 2 "
        "included, so the peer learns at once what arrived. The "
        "PIPE_PING asks for the peer's complete snapshot in return.",
        [PIPE_ACK((0, 4096, 0), (2, 77, 0)), PIPE_PING()])
    return ex


def key_vectors():
    secret = D.secret()
    return [
        ("initiator Session Nonce", D.PEER_NONCE.hex()),
        ("responder Session Nonce", D.LOCAL_NONCE.hex()),
        ("PSK (ASCII \"" + D.PSK.decode() + "\")", D.PSK.hex()),
        ("exporter value", "(none: Secret Flags are P)"),
        ("salt = \"zmux session\" || nonces",
         (R.LABELS["hkdf_salt_prefix"].encode() + D.PEER_NONCE + D.LOCAL_NONCE).hex()),
        ("session_secret", secret.hex()),
        ("session_crypto (Suite 1, Group 2 at each end)", C.session_crypto().hex()),
        ("join_psk_i, epoch 0, Suite 1", C.join_psk(secret, "initiator").hex()),
        ("join_psk_r, epoch 0, Suite 1", C.join_psk(secret, "responder").hex()),
    ]


HEADER = """# zmux Wire Examples

Status: informative. Every byte in this document is generated by
`tools/build_wire.py` and checked by `tools/validate_assets.py`, which
re-encodes each example from the JSON block above it, decodes the bytes back,
and recomputes the key-schedule vectors. Do not edit this file by hand.

Each example shows the JSON description (the frame objects of the trace
format, byte strings in hex), a field table, and the complete record
(`Size` followed by the frames).

"""

SECTIONS = {
    "1": "## 1. Transport parameters",
    "2": "## 2. Core frames",
    "3": "## 3. Negotiated features",
    "4": "## 4. Multipath",
    "5": "## 5. Resume",
}


def render_examples():
    out = [HEADER]
    cur = None
    n = {}
    for e in examples():
        if e["section"] != cur:
            cur = e["section"]
            out.append(SECTIONS[cur] + "\n")
        n[cur] = n.get(cur, 0) + 1
        spec = {"id": e["id"], "record": F.to_json(e["frames"])}
        rec = F.encode_record(e["frames"])
        out.append(f"### {cur}.{n[cur]} {e['title']}\n")
        out.append(e["text"] + "\n")
        out.append(f"<!-- zmux-example {e['id']} -->")
        out.append("```json\n" + json.dumps(spec) + "\n```\n")
        out.append(A.table(A.record_fields(e["frames"])) + "\n")
        out.append("```hex\n" + _wrap(rec.hex()) + "\n```\n")
    out.append("## 6. Key schedule test vectors\n")
    out.append("Inputs and outputs of the session secret, join key and epoch derivations for "
               "the trace fixtures (initiator = peer, responder = local endpoint).\n")
    out.append("<!-- zmux-keys -->")
    out.append("| Item | Value (hex) |\n|---|---|")
    for k, v in key_vectors():
        out.append(f"| {k} | `{v}` |")
    out.append("")
    out.append("## 7. First bytes of foreign protocols\n")
    out.append("The incremental first-record check of a listener rejects these "
               "prefixes at the byte shown.\n")
    out.append("| Input | First bytes | Rejected at byte |\n|---|---|---|")
    for name, b in [("HTTP/1.1 request", b"GET / HTTP/1.1"),
                    ("HTTP/2 preface", b"PRI * HTTP/2.0"), ("TLS ClientHello", bytes.fromhex("160301")),
                    ("SSH banner", b"SSH-2.0-")]:
        at = None
        for i in range(1, len(b) + 1):
            if F.classify_first_bytes(b[:i]) == "reject":
                at = i
                break
        out.append(f"| {name} | `{b[:6].hex(' ')}` | {at} |")
    out.append("")
    out.append("A listener decides on a zmux first record at the byte shown "
               "; the PIPE_LANE alternative applies only to a stream of a known "
               "carrier connection; elsewhere this record (Size 22) is "
               "rejected at its first frame-type byte.\n")
    out.append("| First record | Carrier | First bytes | Decided at byte | Result |\n"
               "|---|---|---|---|---|")
    for name, rec, lane_ok in [
            ("QX_TRANSPORT_PARAMETERS", F.encode_record([TP([])]), False),
            ("Join Hello (not a record)", bytes.fromhex("0201004c"), False),
            ("PIPE_LANE", F.encode_record([PIPE_LANE(0, 1, EXAMPLE_LANE_KEY)]), True),
            ("PIPE_LANE", F.encode_record([PIPE_LANE(0, 1, EXAMPLE_LANE_KEY)]), False)]:
        for i in range(1, len(rec) + 1):
            res = F.classify_first_bytes(rec[:i], accept_lane=lane_ok)
            if res != "need-more":
                break
        carrier = "stream of a known connection" if lane_ok else "any"
        out.append(f"| {name} | {carrier} | `{rec[:6].hex(' ')}` | {i} | {res} |")
    out.append("")
    return "\n".join(out)


def _wrap(h, width=64):
    return "\n".join(h[i:i + width] for i in range(0, len(h), width))


# =============================================================================
# corpus

def corpus():
    valid, invalid = [], []

    def ok(cid, frame, gates=None, note="", limits=None):
        b = F.encode_frame(frame)
        e = {"id": cid, "kind": "frame", "gates": sorted(gates or ALL_GATES),
             "hex": b.hex(), "frame": F.to_json(frame), "note": note}
        if limits:
            e["limits"] = limits
        valid.append(e)

    def ok_non_minimal(cid, hexbytes, frame, note=""):
        valid.append({"id": cid, "kind": "frame", "gates": sorted(ALL_GATES),
                      "hex": hexbytes.hex(), "frame": F.to_json(frame), "non_minimal": True,
                      "note": note})

    def okrec(cid, frames, note=""):
        b = F.encode_record(frames)
        valid.append({"id": cid, "kind": "record", "gates": sorted(ALL_GATES), "hex": b.hex(),
                      "record": F.to_json(frames), "note": note})

    def bad(cid, hexbytes, error, kind="frame", gates=None, note="", limits=None):
        e = {"id": cid, "kind": kind, "gates": sorted(ALL_GATES if gates is None else gates),
             "hex": hexbytes.hex(), "error": error, "note": note}
        if limits:
            e["limits"] = limits
        invalid.append(e)

    def okmsg(cid, messages, note=""):
        valid.append({"id": cid, "kind": "messages", "gates": sorted(ALL_GATES),
                      "messages": [F.encode_frames(m).hex() if isinstance(m, list) else m.hex()
                                   for m in messages],
                      "positions": [F.message_position(F.encode_frames(m) if isinstance(m, list)
                                                       else m) for m in messages],
                      "note": note})

    def badmsg(cid, messages, error, note="", max_record_size=None):
        e = {"id": cid, "kind": "messages", "gates": sorted(ALL_GATES),
             "messages": [m.hex() for m in messages], "error": error, "note": note}
        if max_record_size is not None:
            e["max_record_size"] = max_record_size
        invalid.append(e)

    # --- valid frames
    ok("padding-1", PADDING(1))
    ok("stream-min", STREAM(0, b"", fin=True), note="empty FIN at offset 0")
    ok("stream-off-len-fin", STREAM(4, b"abc", off=100, fin=True))
    ok("stream-no-len", STREAM(8, b"rest", has_len=False))
    ok("stream-off-zero-explicit", STREAM(0, b"q", has_off=True),
       note="OFF bit with offset 0, as some QMux implementations send")
    ok("stream-large-id-offset", STREAM((1 << 62) - 4, b"z", off=(1 << 62) - 2))
    ok("reset-stream", RESET(1, 0x5A4D0001, 1000))
    ok("reset-stream-at", RESET_AT(0, 7, 100, 19))
    ok("reset-stream-at-equal", RESET_AT(0, 7, 100, 100))
    ok("stop-sending", STOP(5, 0))
    ok("max-data-8byte", MAX_DATA((1 << 62) - 1))
    ok("max-stream-data", MAX_STREAM_DATA(3, 262144))
    ok("max-streams-bidi", MAX_STREAMS(1 << 60))
    ok("max-streams-uni", MAX_STREAMS(0, bidi=False))
    ok("data-blocked", DATA_BLOCKED(1048576))
    ok("stream-data-blocked", STREAM_DATA_BLOCKED(0, 0))
    ok("streams-blocked-uni", STREAMS_BLOCKED(256, bidi=False))
    ok("close-transport", CLOSE(TE("PROTOCOL_VIOLATION"), frame_type=0x08, reason=b"x"))
    ok("close-app-empty", CLOSE(0, app=True))
    ok("datagram-len", DATAGRAM(b"abc"))
    ok("datagram-nolen", DATAGRAM(b"abc", has_len=False))
    ok("datagram-empty", DATAGRAM(b""))
    ok("datagram-at-limit", DATAGRAM(b"d" * 10), limits={"max_datagram_frame_size": 12},
       note="12 bytes with type and Length, exactly the advertised max_datagram_frame_size")
    ok("qx-ping-0", PING(0))
    ok("qx-pong-max", PONG((1 << 62) - 1))
    ok("tp-empty", TP([]))
    ok("tp-grease-only", TP([{"id": 27, "raw": b""}]))
    ok("tp-deleted-max-pipe-id-ignored", TP([{"id": 0x2A60F18A, "raw": V(15)}]),
       note="0x2a60f18a was zmux_initial_max_pipe_id, deleted before release: now an unknown "
            "parameter, ignored like any other")
    ok("tp-max-lanes", TP([{"id": "zmux_pipe_ack", "value": None},
                           {"id": "zmux_max_lanes", "value": D.lanes(258, EXAMPLE_LANE_KEY)}]),
       note="the default of 258 lanes and a Lane Key, with pipe acknowledgement")
    ok("tp-multipath-locator-255",
       TP([{"id": "zmux_multipath", "value": {"flags": 4, "nonce": bytes(16), "locator": b"L" * 255}},
           {"id": "zmux_session_crypto", "value": {"suites": [1], "groups": [2]}}]),
       note="the longest Session Locator")
    ok("tp-multipath-reserved-secret-flag",
       TP([{"id": "zmux_multipath", "value": {"flags": 0x06, "nonce": bytes(16), "locator": b"L"}},
           {"id": "zmux_session_crypto", "value": {"suites": [1], "groups": [2]}}]),
       note="Secret Flags 0x02 is reserved: sent as 0, ignored on receipt")
    ok("session-epoch-answer-large-epoch", SESSION_EPOCH("answer", (1 << 62) - 1, b"S", b"L"))
    ok("session-epoch-offer-group-1-share", SESSION_EPOCH("offer", 2, bytes([9]) + bytes(31),
                                                         b"L" * 255),
       note="the codec does not judge the Share's length; the session does")
    ok("goaway", GOAWAY(0, 0, 0))
    ok("goaway-max", GOAWAY(1 << 60, 1 << 60, AE("ZMUX_REJECTED")))
    ok("priority-default", PRIO(0, 1, 3, False))
    ok("priority-u7-inc", PRIO(9, 1 << 40, 7, True))
    ok("pipe-ack-1", PIPE_ACK((0, 0, 0)))
    ok("pipe-ack-3", PIPE_ACK((0, 1000, 25000), (2, 1 << 40, 0), (5, 7, 1)))
    ok("pipe-ack-lanes", PIPE_ACK((0, 0, 1000, 0), (0, 1, 70, 0), (0, 3, 5, 0), (2, 0, 9, 0)),
       note="lanes of pipe 0 in increasing order before pipe 2; a snapshot may omit a lane "
            "(here lane 2) whose position has not changed")
    ok("pipe-lane", PIPE_LANE(2, 1, EXAMPLE_LANE_KEY))
    ok("pipe-lane-max", PIPE_LANE((1 << 62) - 1, (1 << 62) - 1, bytes([0xff]) * 16),
       note="any Lane decodes; one at or above the receiver's Max Lanes is a "
            "PROTOCOL_VIOLATION, not a decoding error")
    ok("pipe-lane-zero", PIPE_LANE(0, 0, EXAMPLE_LANE_KEY),
       note="decodes; opening lane 0 is a PROTOCOL_VIOLATION")
    ok("pipe-ping", PIPE_PING())
    ok("pipe-abandon", PIPE_ABANDON(2, 0))
    ok("pipe-status-available", PIPE_STATUS(1, 1, "available"),
       note="status available, default priority 0")
    ok("pipe-status-standby-priority-5", PIPE_STATUS(2, 7, "standby", 5))
    ok("pipe-status-priority-1000", PIPE_STATUS(4, 1, "available", 1000),
       note="priorities need not be small or consecutive")
    ok("pipe-status-priority-max", PIPE_STATUS(6, 2, "standby", (1 << 62) - 1),
       note="the largest variable-length integer: every priority value is valid")
    ok("pipe-abandon-max-id", PIPE_ABANDON((1 << 62) - 1, 0),
       note="pipe IDs use the whole variable-length integer range")
    ok_non_minimal("max-stream-data-non-minimal", V(0x11) + V(3, 2) + V(262144, 8),
                   MAX_STREAM_DATA(3, 262144),
                   note="stream ID in 2 bytes and limit in 8 bytes: non-minimal field encodings "
                        "are legal (RFC 9000 Section 16) and must be accepted; only the frame "
                        "type must be minimal")

    # --- valid records
    okrec("record-tp", [TP([{"id": "initial_max_data", "value": 1024}])],
          note="a QMux implementation's test vector framed in a record")
    okrec("record-mixed", [PADDING(1), STREAM(0, b"\x00hi"), MAX_DATA(4096), PING(1)])
    okrec("record-lane-first", [PIPE_LANE(0, 2, EXAMPLE_LANE_KEY), PADDING(10)],
          note="the first record of lane 2: PIPE_LANE and, at most, PADDING")
    okrec("record-max-size", [STREAM(0, b"x" * 16376, has_len=False)],
          note="Size 16382, the limit when max_record_size is absent")

    # --- invalid frames
    FE, PV = "FRAME_ENCODING_ERROR", "PROTOCOL_VIOLATION"
    for t, nm in [(0x01, "ping"), (0x02, "ack"), (0x06, "crypto"), (0x07, "new-token"),
                  (0x18, "new-connection-id"), (0x19, "retire-connection-id"),
                  (0x1A, "path-challenge"), (0x1B, "path-response"), (0x1E, "handshake-done")]:
        bad(f"prohibited-{nm}", V(t) + bytes(8), FE)
    bad("unknown-type-2f", b"\x2f", FE)
    bad("unknown-ack-frequency", V(0xAF) + bytes(4), FE)
    bad("unknown-zmux-like", V(0x30D71CCE), FE, note="an unassigned zmux-like type")
    for nm, code in [("max-pipe-id", 0x31B6478F), ("pipes-blocked", 0x0268CA60),
                     ("resume-state", 0x1FA58D5B)]:
        bad(f"deleted-{nm}", V(code) + V(15), FE,
            note="a frame type removed before release: an unknown frame")
    bad("type-not-minimal", bytes.fromhex("4010") + V(1), PV)
    bad("type-not-minimal-zmux", V(ACK_T, 8) + V(1) + V(0) * 4, PV,
        note="PIPE_ACK type in 8 bytes")
    bad("stream-truncated-len", bytes.fromhex("0a000a") + b"abc", FE)
    bad("stream-truncated-id", bytes.fromhex("0b"), FE)
    bad("stream-offset-overflow", V(0x0E) + V(0) + V((1 << 62) - 1) + V(2) + b"ab", FE)
    bad("reset-at-reliable-gt-final", V(0x24) + V(0) + V(0) + V(4) + V(5), FE)
    bad("max-streams-above-2^60", V(0x12) + V((1 << 60) + 1), FE)
    bad("streams-blocked-above-2^60", V(0x17) + V((1 << 60) + 1), FE)
    bad("close-reason-overrun", V(0x1D) + V(0) + V(10) + b"abc", FE)
    bad("goaway-above-2^60", V(0x1911F43E) + V((1 << 60) + 1) + V(0) + V(0), FE)
    bad("priority-reserved-bits", V(0x073D08CF) + V(0) + V(1) + b"\x61", FE)
    bad("priority-truncated", V(0x073D08CF) + V(0) + V(1), FE)
    bad("pipe-ack-empty", V(ACK_T) + V(0), FE)
    bad("pipe-ack-duplicate-entry", F.encode_frame(PIPE_ACK((0, 0, 1, 0), (0, 0, 2, 0))), FE,
        note="pipe 0 lane 0 twice")
    bad("pipe-ack-unordered", F.encode_frame(PIPE_ACK((2, 0, 1, 0), (0, 0, 1, 0))), FE,
        note="entries for pipes 2 then 0: entries must be in strictly increasing "
             "(Pipe ID, Lane) order")
    bad("pipe-ack-lanes-unordered", F.encode_frame(PIPE_ACK((0, 1, 1, 0), (0, 0, 1, 0))), FE,
        note="lanes 1 then 0 of pipe 0")
    bad("pipe-ack-lane-repeated",
        F.encode_frame(PIPE_ACK((0, 0, 1, 0), (0, 1, 1, 0), (0, 1, 2, 0))), FE,
        note="lane 1 of pipe 0 twice")
    bad("pipe-ack-without-lane", V(ACK_T) + V(1) + V(0) + V(100) + V(0), FE,
        note="an entry in the layout without the Lane field is one field short")
    bad("pipe-lane-truncated", V(LANE_T) + EXAMPLE_LANE_KEY + V(0), FE, note="Lane missing")
    bad("pipe-lane-short-key", V(LANE_T) + EXAMPLE_LANE_KEY[:15], FE,
        note="a Lane Key of 15 bytes and nothing after it")
    bad("pipe-status-invalid", V(0x227B2811) + V(0) + V(1) + V(2) + V(3), FE,
        note="Status 2 is undefined")
    bad("pipe-status-missing-priority", V(0x227B2811) + V(0) + V(1) + V(0), FE,
        note="the frame ends after Status: Priority is a mandatory field")
    EP_T = R.frame_by_name("SESSION_EPOCH")["first"]
    bad("session-epoch-phase-2", V(EP_T) + b"\x02" + V(1), PV, note="Phase 2 is undefined")
    bad("session-epoch-no-epoch", V(EP_T) + b"\x00", FE, note="the frame ends after Phase")
    bad("session-epoch-share-overrun", V(EP_T) + b"\x00" + V(1) + V(32) + bytes(10), FE)
    bad("session-epoch-no-locator-length", V(EP_T) + b"\x01" + V(1) + V(2) + b"ab", FE,
        note="an answer that ends after its Share")
    bad("session-epoch-locator-overrun", V(EP_T) + b"\x00" + V(1) + V(1) + b"a" + b"\x05LOC",
        FE)
    bad("datagram-len-overrun", V(0x31) + V(5) + b"ab", FE)
    bad("datagram-above-limit", F.encode_frame(DATAGRAM(b"d" * 11)), PV,
        limits={"max_datagram_frame_size": 12},
        note="13 bytes against an advertised max_datagram_frame_size of 12 (RFC 9221 Section 3)")
    bad("datagram-overrun-above-limit", V(0x31) + V(40) + b"d" * 20, FE,
        limits={"max_datagram_frame_size": 12},
        note="the Length runs past the end and the frame is also above the advertised size: "
             "the decode error takes precedence")
    # negotiation gates
    bad("goaway-not-in-use", F.encode_frame(GOAWAY(0, 0, 0)), FE,
        gates=ALL_GATES - {"zmux_goaway"})
    bad("pipe-ack-not-in-use", F.encode_frame(PIPE_ACK((0, 0, 0))), FE,
        gates=ALL_GATES - {"zmux_multipath", "zmux_pipe_ack"})
    bad("pipe-lane-not-available", F.encode_frame(PIPE_LANE(0, 1, EXAMPLE_LANE_KEY)), FE,
        gates=ALL_GATES - {"zmux_max_lanes"},
        note="on a pipe's lane 0 toward an endpoint without lanes, an unknown frame")
    bad("reset-stream-at-not-advertised", F.encode_frame(RESET_AT(0, 0, 1, 1)), FE,
        gates=ALL_GATES - {"reset_stream_at"})
    bad("datagram-not-advertised", F.encode_frame(DATAGRAM(b"x")), PV,
        gates=ALL_GATES - {"max_datagram_frame_size"})
    # transport parameters
    TPE = "TRANSPORT_PARAMETER_ERROR"

    def badtp(cid, params, note=""):
        bad(cid, F.encode_frame(TP(params)), TPE, note=note)

    badtp("tp-duplicate", [{"id": "initial_max_data", "value": 1}, {"id": "initial_max_data", "value": 2}])
    for nm, i in [("original-dcid", 0x00), ("stateless-reset-token", 0x02),
                  ("max-udp-payload", 0x03), ("ack-delay-exponent", 0x0A), ("max-ack-delay", 0x0B),
                  ("disable-migration", 0x0C), ("preferred-address", 0x0D),
                  ("active-cid-limit", 0x0E), ("initial-scid", 0x0F), ("retry-scid", 0x10)]:
        badtp(f"tp-prohibited-{nm}", [{"id": i, "raw": V(1)}])
    badtp("tp-varint-trailing", [{"id": "initial_max_data", "raw": V(1) + b"\x00"}])
    badtp("tp-max-record-size-small", [{"id": "max_record_size", "value": 16381}])
    badtp("tp-max-streams-above-2^60", [{"id": "initial_max_streams_bidi", "value": (1 << 60) + 1}])
    badtp("tp-reset-stream-at-nonempty", [{"id": "reset_stream_at", "raw": b"\x00"}])
    badtp("tp-goaway-nonempty", [{"id": "zmux_goaway", "raw": b"\x01"}])
    badtp("tp-pipe-ack-nonempty", [{"id": "zmux_pipe_ack", "raw": b"\x00"}])
    badtp("tp-role-invalid", [{"id": "zmux_role", "raw": V(3)}])
    badtp("tp-role-auto-no-nonce", [{"id": "zmux_role", "raw": V(2)}])
    badtp("tp-role-explicit-with-nonce", [{"id": "zmux_role", "raw": V(0) + bytes(8)}])
    badtp("tp-preamble-without-reset-stream-at",
          [{"id": "initial_max_stream_data_bidi_remote", "value": 8192},
           {"id": "initial_max_stream_data_uni", "value": 8192}, {"id": "zmux_preamble", "value": 512}])
    badtp("tp-preamble-zero", [{"id": "reset_stream_at", "value": None},
                               {"id": "zmux_preamble", "value": 0}])
    badtp("tp-preamble-above-window",
          [{"id": "reset_stream_at", "value": None},
           {"id": "initial_max_stream_data_bidi_remote", "value": 100},
           {"id": "initial_max_stream_data_uni", "value": 8192}, {"id": "zmux_preamble", "value": 512}])
    badtp("tp-multipath-short", [{"id": "zmux_multipath", "raw": bytes(10)}])
    badtp("tp-multipath-length-mismatch", [{"id": "zmux_multipath",
                                            "raw": b"\x04" + bytes(16) + b"\x05" + b"LOC"}])
    badtp("tp-ack-threshold-alone", [{"id": "zmux_ack_threshold", "value": 4096}])
    badtp("tp-resume-alone", [{"id": "zmux_resume", "value": 1000}])
    pack = {"id": "zmux_pipe_ack", "value": None}
    badtp("tp-max-lanes-alone", [{"id": "zmux_max_lanes", "value": D.lanes(4, EXAMPLE_LANE_KEY)}],
          note="zmux_max_lanes requires zmux_multipath or zmux_pipe_ack")
    badtp("tp-max-lanes-zero", [pack, {"id": "zmux_max_lanes",
                                       "value": D.lanes(0, EXAMPLE_LANE_KEY)}])
    badtp("tp-max-lanes-without-key", [pack, {"id": "zmux_max_lanes", "raw": V(4)}],
          note="Max Lanes without a Lane Key")
    badtp("tp-max-lanes-long-key", [pack, {"id": "zmux_max_lanes",
                                           "raw": V(4) + EXAMPLE_LANE_KEY + b"\x00"}])
    mp = {"id": "zmux_multipath", "value": {"flags": 4, "nonce": bytes(16), "locator": b"L"}}
    su = {"id": "zmux_session_crypto", "value": {"suites": [1], "groups": [2]}}
    badtp("tp-ack-threshold-small", [mp, su, {"id": "zmux_ack_threshold", "value": 1023}])
    badtp("tp-max-ack-delay-large", [mp, su, {"id": "zmux_max_ack_delay", "value": 1 << 14}])
    badtp("tp-resume-zero", [mp, su, {"id": "zmux_resume", "value": 0}])
    badtp("tp-session-crypto-missing", [mp], note="zmux_session_crypto goes with zmux_multipath")
    badtp("tp-session-crypto-alone", [su], note="zmux_session_crypto goes with zmux_multipath")
    badtp("tp-session-crypto-no-suite-1", [mp, {"id": "zmux_session_crypto",
                                                 "raw": V(1) + V(2) + V(1) + V(2)}])
    badtp("tp-session-crypto-no-group-2", [mp, {"id": "zmux_session_crypto",
                                                 "raw": V(1) + V(1) + V(1) + V(1)}])
    badtp("tp-session-crypto-zero", [mp, {"id": "zmux_session_crypto",
                                          "raw": V(2) + V(0) + V(1) + V(1) + V(2)}])
    badtp("tp-session-crypto-duplicate", [mp, {"id": "zmux_session_crypto",
                                               "raw": V(2) + V(1) + V(1) + V(1) + V(2)}])
    badtp("tp-session-crypto-descending", [mp, {"id": "zmux_session_crypto",
                                                "raw": V(1) + V(1) + V(2) + V(2) + V(1)}])
    badtp("tp-session-crypto-non-minimal", [mp, {"id": "zmux_session_crypto",
                                                 "raw": V(1) + b"\x40\x01" + V(1) + V(2)}])
    badtp("tp-session-crypto-no-group-set", [mp, {"id": "zmux_session_crypto",
                                                  "raw": V(1) + V(1)}])
    badtp("tp-session-crypto-trailing", [mp, {"id": "zmux_session_crypto",
                                              "raw": V(1) + V(1) + V(1) + V(2) + b"\x00"}])
    # records
    bad("record-zero-size", b"\x00", FE, kind="record")
    bad("record-oversize", V(65536), FE, kind="record",
        note="Size 65536 exceeds the receiver's max_record_size of 65535; rejected from the "
             "Size field alone")
    bad("record-truncated-frame", F.encode_record([RAW(bytes.fromhex("0b000a") + b"abc")]), FE,
        kind="record")
    bad("record-misplaced-tp-malformed",
        F.encode_record([STREAM(0, b"\x00hi"),
                         TP([{"id": "initial_max_data", "raw": V(1) + b"\x00"}])]),
        "TRANSPORT_PARAMETER_ERROR", kind="record",
        note="QX_TRANSPORT_PARAMETERS after the first record is a PROTOCOL_VIOLATION, but its "
             "malformed parameter block is a decode error, which takes precedence")
    bad("record-misplaced-tp-duplicate",
        F.encode_record([STREAM(0, b"\x00hi"),
                         TP([{"id": "initial_max_data", "value": 1},
                             {"id": "initial_max_data", "value": 2}])]),
        "TRANSPORT_PARAMETER_ERROR", kind="record",
        note="a misplaced QX_TRANSPORT_PARAMETERS whose block repeats a parameter: decoding the "
             "block includes the duplicate check, so TRANSPORT_PARAMETER_ERROR takes precedence "
             "over the placement error")
    # A carrier that ends inside a record is a pipe failure, not a frame error
    # (SPEC.md Section 3.2); the traces core.records.pipe_ends_mid_record and
    # multipath.ack.pipe_ends_mid_record cover it.
    # --- message-framed carriers (SPEC.md Section 3.4)
    okmsg("messages-tp-then-stream",
          [[TP([{"id": "initial_max_data", "value": 1024}])],
           [STREAM(0, b"\x00hi", fin=True), PADDING(2)]],
          note="each message is one record's Frames field; positions count Size as if present")
    okmsg("message-empty-no-op", [[TP([{"id": "initial_max_data", "value": 1024}])], b""],
          note="an empty message is a record without frames, a no-op that, like a "
          "PADDING-only record, is not chargeable; it counts 1 byte in positions")
    badmsg("message-oversize", [F.encode_frames([STREAM(0, b"x" * 16381, has_len=False)])], FE,
           note="16383 bytes against a receiver max_record_size of 16382", max_record_size=16382)
    return valid, invalid


def main():
    (ROOT / "WIRE_EXAMPLES.md").write_text(render_examples())
    valid, invalid = corpus()
    wdir = ROOT / "fixtures" / "wire"
    wdir.mkdir(parents=True, exist_ok=True)
    (wdir / "valid.ndjson").write_text("".join(json.dumps(v) + "\n" for v in valid))
    (wdir / "invalid.ndjson").write_text("".join(json.dumps(v) + "\n" for v in invalid))
    print(f"wire: WIRE_EXAMPLES.md, {len(valid)} valid and {len(invalid)} invalid corpus entries")


if __name__ == "__main__":
    main()
