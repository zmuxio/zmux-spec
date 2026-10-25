#!/usr/bin/env python3
"""Generate fixtures/traces/** and fixtures/profiles.json (TRACES.md)."""

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from zmuxwire import frames as F  # noqa: E402
from zmuxwire.varint import encode_varint as V  # noqa: E402
from zmuxwire.tracedsl import *  # noqa: E402,F401,F403
from zmuxwire import tracedsl as D  # noqa: E402

CANCELLED = AE("ZMUX_CANCELLED")
REJECTED = AE("ZMUX_REJECTED")
PIPE_FAILED = TE("ZMUX_PIPE_FAILED")

BASE_FEATURES = ["goaway", "preamble", "priority"]
MP_FEATURES = BASE_FEATURES + ["multipath"]
ACK_FEATURES = BASE_FEATURES + ["pipe_ack"]
LOCAL_FIRST_PRE = pre(grease=True)          # first local preamble, no open info
TP_ANY = {"type": "QX_TRANSPORT_PARAMETERS"}


def local_tp_first(t):
    t.expect_local_tp()


def err(kind, **kw):
    return {"error": dict(kind=kind, **kw)}


RESET_ANY = {"oneof": ["RESET_STREAM", "RESET_STREAM_AT"]}


# =============================================================================
# core.handshake

def core_handshake():
    out = []
    t = Trace("core.handshake.basic", "Parameters are the first frame in both directions; "
              "zmux features are in use between zmux endpoints",
              ["SPEC.md#5.1", "SPEC.md#5.2", "SPEC.md#5.5"])
    t.handshake(peer_small(), features=BASE_FEATURES, role="responder")
    t.is_open()
    out.append(t)

    t = Trace("core.handshake.legacy_zmux_rejected", "A legacy zmux preface fails at its third byte",
              ["SPEC.md#6.2"])
    t.up(0, "bidi", via="bootstrap")
    local_tp_first(t)
    t.raw(0, b"Z")
    t.is_open()
    t.raw(0, b"M")
    t.is_open()
    t.raw(0, b"U")
    t.closed("PROTOCOL_VIOLATION", wire=True)
    out.append(t)

    t = Trace("core.handshake.http1_rejected", "An HTTP/1.1 request fails at its third byte",
              ["SPEC.md#6.2"])
    t.up(0, "bidi", via="bootstrap")
    local_tp_first(t)
    t.raw(0, b"G")
    t.is_open()
    t.raw(0, b"E")
    t.is_open()
    t.raw(0, b"T")
    t.closed("PROTOCOL_VIOLATION", wire=True)
    out.append(t)

    t = Trace("core.handshake.first_record_too_small",
              "A first record whose Size is below 9 fails at its first byte", ["SPEC.md#6.2"])
    t.up(0, "bidi", via="bootstrap")
    local_tp_first(t)
    t.raw(0, b"\x08")
    t.closed("PROTOCOL_VIOLATION", wire=True)
    out.append(t)

    t = Trace("core.handshake.first_record_too_large",
              "A first record whose Size exceeds the local max_record_size fails once the Size "
              "is complete", ["SPEC.md#6.2"])
    t.up(0, "bidi", via="bootstrap")
    local_tp_first(t)
    big = V(65536)
    for b in big[:-1]:
        t.raw(0, bytes([b]))
        t.is_open()
    t.raw(0, big[-1:])
    t.closed("PROTOCOL_VIOLATION", wire=True)
    out.append(t)

    t = Trace("core.handshake.first_frame_not_parameters",
              "A first record that does not start with QX_TRANSPORT_PARAMETERS is rejected",
              ["SPEC.md#5.1", "SPEC.md#6.2"])
    t.up(0, "bidi", via="bootstrap")
    local_tp_first(t)
    t.send(0, [STREAM(0, b"0123456789", fin=True)])
    t.closed("PROTOCOL_VIOLATION", wire=True)
    out.append(t)

    t = Trace("core.handshake.second_parameters_frame",
              "QX_TRANSPORT_PARAMETERS after the first frame is a PROTOCOL_VIOLATION",
              ["SPEC.md#5.1"])
    t.handshake(peer_small())
    t.send(0, [TP(tp_list(peer_small()))])
    t.closed("PROTOCOL_VIOLATION")
    out.append(t)

    t = Trace("core.handshake.tp_duplicate", "A duplicate transport parameter is fatal",
              ["SPEC.md#5.2"])
    t.up(0, "bidi", via="bootstrap")
    local_tp_first(t)
    params = tp_list(peer_small()) + [{"id": "initial_max_data", "value": 1}]
    t.send(0, [TP(params)], invalid=True)
    t.closed("TRANSPORT_PARAMETER_ERROR")
    out.append(t)

    t = Trace("core.handshake.tp_prohibited", "QUIC max_ack_delay (0x0b) is prohibited",
              ["SPEC.md#5.2"])
    t.up(0, "bidi", via="bootstrap")
    local_tp_first(t)
    t.send(0, [TP(tp_list(peer_small()) + [{"id": 0x0B, "raw": V(25)}])], invalid=True)
    t.closed("TRANSPORT_PARAMETER_ERROR")
    out.append(t)

    t = Trace("core.handshake.tp_max_record_size_small", "max_record_size below 16382 is fatal",
              ["SPEC.md#3.2"])
    t.up(0, "bidi", via="bootstrap")
    local_tp_first(t)
    t.send(0, [TP(tp_list(peer_small(max_record_size=1000)))], invalid=True)
    t.closed("TRANSPORT_PARAMETER_ERROR")
    out.append(t)

    t = Trace("core.handshake.tp_unknown_and_grease_ignored",
              "GREASE and unknown parameters, including the application_protocols parameter of some QMux implementations, "
              "are ignored", ["SPEC.md#5.2", "SPEC.md#13.2"])
    t.up(0, "bidi", via="bootstrap")
    local_tp_first(t)
    params = tp_list(peer_small(), grease=(27 + 31 * 5, bytes(range(16))))
    params.append({"id": 0x3D4F9C2A8B1E6075, "raw": V(5) + b"app-x"})
    params.append({"id": 0x7FFFFFFF, "raw": b"\x01\x02"})
    t.send(0, [TP(params)])
    t.event("established", features=BASE_FEATURES)
    t.is_open()
    out.append(t)

    t = Trace("core.handshake.tp_preamble_without_reset_stream_at",
              "zmux_preamble requires reset_stream_at from the same endpoint", ["EXTENSIONS.md#4.1"])
    t.up(0, "bidi", via="bootstrap")
    local_tp_first(t)
    d = peer_small()
    del d["reset_stream_at"]
    t.send(0, [TP(tp_list(d))], invalid=True)
    t.closed("TRANSPORT_PARAMETER_ERROR")
    out.append(t)

    t = Trace("core.handshake.tp_preamble_above_window",
              "zmux_preamble may not exceed the sender's own stream windows", ["EXTENSIONS.md#4.1"])
    t.up(0, "bidi", via="bootstrap")
    local_tp_first(t)
    t.send(0, [TP(tp_list(peer_small(zmux_preamble=4000)))], invalid=True)
    t.closed("TRANSPORT_PARAMETER_ERROR")
    out.append(t)

    t = Trace("core.handshake.tp_companion_alone",
              "zmux_ack_threshold without zmux_multipath or zmux_pipe_ack is fatal",
              ["MULTIPATH.md#3.2", "SPEC.md#5.5"])
    t.up(0, "bidi", via="bootstrap")
    local_tp_first(t)
    t.send(0, [TP(tp_list(peer_small(zmux_ack_threshold=4096)))], invalid=True)
    t.closed("TRANSPORT_PARAMETER_ERROR")
    out.append(t)

    t = Trace("core.handshake.tp_max_lanes_alone",
              "zmux_max_lanes without zmux_multipath or zmux_pipe_ack is fatal",
              ["MULTIPATH.md#3.6", "SPEC.md#5.5"])
    t.up(0, "bidi", via="bootstrap")
    local_tp_first(t)
    t.send(0, [TP(tp_list(peer_small(zmux_max_lanes=lanes(4))))], invalid=True)
    t.closed("TRANSPORT_PARAMETER_ERROR")
    out.append(t)

    t = Trace("core.handshake.tp_max_lanes_zero", "A zmux_max_lanes with Max Lanes 0 is fatal",
              ["MULTIPATH.md#3.6"])
    t.up(0, "bidi", via="bootstrap")
    local_tp_first(t)
    t.send(0, [TP(tp_list(peer_ack(zmux_max_lanes=lanes(0))))], invalid=True)
    t.closed("TRANSPORT_PARAMETER_ERROR")
    out.append(t)

    t = Trace("core.handshake.tp_max_lanes_short_key",
              "A zmux_max_lanes whose Lane Key is not 16 bytes is fatal", ["MULTIPATH.md#3.6"])
    t.up(0, "bidi", via="bootstrap")
    local_tp_first(t)
    t.send(0, [TP(tp_list(peer_ack(), grease=None) + [
        {"id": "zmux_max_lanes", "raw": V(4) + D.PEER_LANE_KEY[:15]}])], invalid=True)
    t.closed("TRANSPORT_PARAMETER_ERROR")
    out.append(t)

    t = Trace("core.handshake.no_stream_data_before_peer_parameters",
              "Stream data waits for the peer's parameters", ["SPEC.md#5.1"])
    t.up(0, "bidi", via="bootstrap")
    local_tp_first(t)
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=b"hi", fin=False, expect={"accepted": 2})
    t.no_frames(0, ["STREAM"])
    t.send(0, [TP(tp_list(peer_small()))])
    t.frames(0, SDATA(1, LOCAL_FIRST_PRE + b"hi", fin=False))
    out.append(t)

    EARLY = {"config": {"early_data": {"zmux_peer": True, "replayable": False}}}
    E6 = "SPEC.md#6.1.2"
    FENCE = PIPE_ACK((0, 0, 0))
    FLOOR_PEER = peer_ack(initial_max_data=65536, initial_max_stream_data_bidi_remote=8192,
                          initial_max_streams_bidi=8, zmux_preamble=1024)

    t = Trace("core.handshake.early_data_floor",
              "With the peer declared a zmux endpoint, stream data goes out before the peer's "
              "parameters, behind the PIPE_ACK fence and within the floor (4096 bytes a stream)",
              [E6, "SPEC.md#5.1"], local_role="initiator", overrides=EARLY)
    t.up(0, "bidi", via="bootstrap")
    data = bytes(i % 251 for i in range(5000))
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=data, fin=False, expect={"accepted": 5000})
    full = LOCAL_FIRST_PRE + data
    t.frames(0, TP_ANY, FENCE, SDATA(0, full[:4096]), match="contains")
    t.send(0, [TP(tp_list(FLOOR_PEER))])
    t.event("established", features=ACK_FEATURES, role="initiator")
    t.frames(0, SDATA(0, full[4096:], off=4096))
    t.is_open()
    out.append(t)

    t = Trace("core.handshake.early_data_floor_limits",
              "Under the floor at most four bidirectional streams carry early data, 4096 bytes "
              "each and 16384 in all; the rest waits for the peer's parameters",
              [E6], local_role="initiator", overrides=EARLY)
    t.up(0, "bidi", via="bootstrap")
    data = bytes(i % 241 for i in range(5000))
    pres = [LOCAL_FIRST_PRE] + [EMPTY_PRE] * 4
    for k in range(5):
        t.api("open_bidi", bind=f"s{k}", expect={"ok": True})
        t.api("write", stream=f"s{k}", data=data, fin=True, expect={"accepted": 5000})
    t.frames(0, TP_ANY, FENCE, *[SDATA(4 * k, (pres[k] + data)[:4096]) for k in range(4)],
             {"type": "STREAM", "id": 16, "count": 0}, match="unordered")
    t.send(0, [TP(tp_list(FLOOR_PEER))])
    t.event("established", features=ACK_FEATURES, role="initiator")
    t.frames(0, *[SDATA(4 * k, (pres[k] + data)[4096:], off=4096, fin=True) for k in range(4)],
             SDATA(16, EMPTY_PRE + data, fin=True), match="unordered")
    out.append(t)

    t = Trace("core.handshake.early_data_plain_peer_rejected",
              "A peer declared zmux that turns out to be a plain QMux peer has rejected the "
              "early data at the fence: the endpoint closes with NO_ERROR and reports early "
              "data rejected", [E6, "SPEC.md#13.1"], local_role="initiator", overrides=EARLY)
    t.up(0, "bidi", via="bootstrap")
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=b"hi", fin=True, expect={"accepted": 2})
    t.frames(0, TP_ANY, FENCE, SDATA(0, LOCAL_FIRST_PRE + b"hi", fin=True), match="contains")
    t.send(0, [TP(tp_list(peer_plain(), grease=None))])
    t.frames(0, CLOSE(0))
    t.closed("EARLY_DATA_REJECTED", space="local")
    out.append(t)

    t = Trace("core.handshake.early_data_no_streams_rejected",
              "A zmux peer that accepts no bidirectional streams has rejected early data at "
              "the first STREAM frame", [E6], local_role="initiator", overrides=EARLY)
    t.up(0, "bidi", via="bootstrap")
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=b"hi", fin=True, expect={"accepted": 2})
    t.frames(0, TP_ANY, FENCE, SDATA(0, LOCAL_FIRST_PRE + b"hi", fin=True), match="contains")
    t.send(0, [TP(tp_list(dict(FLOOR_PEER, initial_max_streams_bidi=0)))])
    t.frames(0, CLOSE(0))
    t.closed("EARLY_DATA_REJECTED", space="local")
    out.append(t)

    t = Trace("core.handshake.early_data_peer_below_floor",
              "After sending early data, a zmux peer whose parameters are below the floor is "
              "a TRANSPORT_PARAMETER_ERROR", [E6], local_role="initiator", overrides=EARLY)
    t.up(0, "bidi", via="bootstrap")
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=b"hi", fin=True, expect={"accepted": 2})
    t.frames(0, TP_ANY, FENCE, SDATA(0, LOCAL_FIRST_PRE + b"hi", fin=True), match="contains")
    t.send(0, [TP(tp_list(peer_ack()))])
    t.closed("TRANSPORT_PARAMETER_ERROR")
    out.append(t)

    t = Trace("core.handshake.early_data_not_with_role_auto",
              "An endpoint whose role depends on the peer (role auto) sends no early data",
              [E6, "EXTENSIONS.md#2.3"], profile="zmux-role-auto", overrides=EARLY)
    t.up(0, "bidi", via="bootstrap")
    local_tp_first(t)
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=b"x", fin=True, expect={"accepted": 1})
    t.no_frames(0, ["STREAM", "PIPE_ACK"])
    out.append(t)

    t = Trace("core.handshake.early_data_received",
              "Early data from a zmux peer, behind its fence in the record of its parameters, "
              "is processed like any data; the fence's Position 0 is ignored", [E6],
              )
    t.up(0, "bidi", via="bootstrap")
    local_tp_first(t)
    t.send(0, [TP(tp_list(peer_ack())), FENCE, STREAM(0, EMPTY_PRE + b"hello", fin=True)])
    t.event("established", features=ACK_FEATURES)
    t.api("accept_bidi", bind="s", expect={"stream": 0})
    t.api("read", stream="s", max=10, expect={"data": b"hello", "fin": True})
    t.is_open()
    out.append(t)

    t = Trace("core.handshake.early_fence_stops_peer_without_pipe_ack",
              "Where PIPE_ACK is not in use the fence is an unknown frame: the session closes "
              "before the early STREAM frame after it is processed", [E6, "SPEC.md#4.1"])
    t.up(0, "bidi", via="bootstrap")
    local_tp_first(t)
    t.send(0, [TP(tp_list(peer_small())), FENCE, STREAM(0, EMPTY_PRE + b"hello", fin=True)])
    t.closed("FRAME_ENCODING_ERROR")
    out.append(t)

    t = Trace("core.handshake.timeout", "Establishment is abandoned after the handshake timeout",
              ["SPEC.md#6.1"])
    t.up(0, "bidi", via="bootstrap")
    local_tp_first(t)
    t.adv(9999)
    t.is_open()
    t.adv(1)
    t.closed("HANDSHAKE_TIMEOUT", space="local", wire=False)
    out.append(t)
    return out


# =============================================================================
# core.records

def core_records():
    out = []

    def bad(name, title, refs, payload, code):
        t = Trace(f"core.records.{name}", title, refs)
        t.handshake(peer_small())
        t.raw(0, payload)
        t.closed(code)
        out.append(t)

    bad("zero_size", "A record with Size 0 is fatal", ["SPEC.md#3.2"], b"\x00",
        "FRAME_ENCODING_ERROR")
    bad("oversize", "A Size above the local max_record_size (65535) is fatal before the body "
        "arrives", ["SPEC.md#3.2"], V(65536), "FRAME_ENCODING_ERROR")
    bad("stream_offset_overflow", "A STREAM frame ending beyond 2^62 - 1 is fatal",
        ["SPEC.md#4.3"],
        F.encode_record([RAW(V(0x0E) + V(0) + V((1 << 62) - 1) + V(2) + b"ab")]),
        "FRAME_ENCODING_ERROR")
    bad("truncated_frame", "A frame truncated at the end of a record is fatal", ["SPEC.md#3.2"],
        F.encode_record([RAW(bytes.fromhex("0b000a") + b"abc")]), "FRAME_ENCODING_ERROR")
    bad("type_not_minimal", "A frame type in a longer encoding than necessary is fatal",
        ["SPEC.md#4.1"], F.encode_record([RAW(bytes.fromhex("4010") + V(9000))]),
        "PROTOCOL_VIOLATION")
    bad("prohibited_ping", "QUIC PING (0x01) is prohibited", ["SPEC.md#4.4"],
        F.encode_record([RAW(b"\x01")]), "FRAME_ENCODING_ERROR")
    bad("prohibited_ack", "QUIC ACK (0x02) is prohibited", ["SPEC.md#4.4"],
        F.encode_record([RAW(bytes.fromhex("0200000000"))]), "FRAME_ENCODING_ERROR")
    bad("unknown_type", "An unknown frame type is fatal", ["SPEC.md#4.1"],
        F.encode_record([RAW(b"\x2f")]), "FRAME_ENCODING_ERROR")
    bad("unknown_ack_frequency", "ACK_FREQUENCY (0xaf) is unknown to zmux", ["SPEC.md#4.1"],
        F.encode_record([RAW(V(0xAF) + bytes(4))]), "FRAME_ENCODING_ERROR")

    t = Trace("core.records.feature_frame_not_in_use",
              "A GOAWAY from a peer that did not announce zmux_goaway is an unknown frame",
              ["SPEC.md#4.1", "SPEC.md#5.5"])
    d = peer_small()
    del d["zmux_goaway"]
    t.handshake(d, features=["preamble", "priority"])
    t.send(0, [GOAWAY(0, 0)])
    t.closed("FRAME_ENCODING_ERROR")
    out.append(t)

    t = Trace("core.records.padding_and_byte_by_byte",
              "PADDING anywhere, several frames per record, and one-byte reads are handled",
              ["SPEC.md#3.1", "SPEC.md#9.1"])
    t.handshake(peer_small())
    recs = [[PADDING(3), STREAM(0, EMPTY_PRE + b"abc"), PADDING(1)],
            [STREAM(0, b"def", off=4, fin=True), PADDING(2)]]
    n = len(D.record_bytes(recs))
    t.send(0, *recs, chunks=[1] * n)
    t.api("accept_bidi", expect={"stream": 0})
    t.api("read", stream=0, max=100, expect={"data": b"abcdef", "fin": True})
    out.append(t)

    t = Trace("core.records.message_framed_session",
              "On a message-framed carrier each message is one record's Frames field, without "
              "Size", ["SPEC.md#3.4"])
    t.up(0, "bidi", via="bootstrap", framing="records")
    t.expect_local_tp()
    t.send_messages(0, [TP(tp_list(peer_small()))])
    t.event("established", features=BASE_FEATURES)
    t.send_messages(0, [STREAM(0, EMPTY_PRE + b"hello", fin=True)], [PADDING(2), PING(4)])
    t.api("accept_bidi", bind="s", expect={"stream": 0})
    t.api("read", stream="s", max=10, expect={"data": b"hello", "fin": True})
    t.api("write", stream="s", data=b"bye", fin=True, expect={"accepted": 3})
    t.frames(0, PONG(4), SDATA(0, b"bye", fin=True), match="unordered")
    out.append(t)

    t = Trace("core.records.message_empty",
              "An empty message is a record without frames, accepted as a no-op",
              ["SPEC.md#3.4"])
    t.up(0, "bidi", via="bootstrap", framing="records")
    t.expect_local_tp()
    t.send_messages(0, [TP(tp_list(peer_small()))])
    t.event("established", features=BASE_FEATURES)
    t.send_messages(0, raw=[b"", b""])
    t.is_open()
    t.send_messages(0, [STREAM(0, EMPTY_PRE + b"hi", fin=True)])
    t.api("accept_bidi", bind="s", expect={"stream": 0})
    t.api("read", stream="s", max=10, expect={"data": b"hi", "fin": True})
    t.no_frames(0, ["CONNECTION_CLOSE"])
    out.append(t)

    t = Trace("core.records.message_empty_charged",
              "An empty message is charged like a PADDING-only record: with the budget "
              "exhausted, one more closes the session", ["SPEC.md#3.4", "SPEC.md#9.4"])
    t.up(0, "bidi", via="bootstrap", framing="records")
    t.expect_local_tp()
    t.send_messages(0, [TP(tp_list(peer_small()))])
    t.event("established", features=BASE_FEATURES)
    t.send_messages(0, [REPEAT(4096, MAX_DATA(8192))])
    t.is_open()
    t.send_messages(0, raw=[b""])
    t.closed("ZMUX_EXCESSIVE_LOAD")
    out.append(t)

    t = Trace("core.records.message_empty_first",
              "An empty first message fails the first-record check (Size below 9)",
              ["SPEC.md#3.4", "SPEC.md#6.2"])
    t.up(0, "bidi", via="bootstrap", framing="records")
    t.expect_local_tp()
    t.send_messages(0, raw=[b""])
    t.closed("PROTOCOL_VIOLATION", wire=True)
    out.append(t)

    t = Trace("core.records.message_oversize",
              "A message longer than the receiver's max_record_size is fatal", ["SPEC.md#3.4"],
              overrides={"tp": {"max_record_size": 16382}})
    t.up(0, "bidi", via="bootstrap", framing="records")
    t.expect_local_tp()
    t.send_messages(0, [TP(tp_list(peer_small()))])
    t.event("established", features=BASE_FEATURES)
    t.send_messages(0, raw=[F.encode_frames([STREAM(0, EMPTY_PRE + b"x" * 16380,
                                                     has_len=False)])])
    t.closed("FRAME_ENCODING_ERROR")
    out.append(t)

    t = Trace("core.records.pipe_ends_mid_record",
              "Outside multipath a pipe that ends inside a record, even gracefully, ends the "
              "session and the partial record is never processed", ["SPEC.md#3.2", "SPEC.md#6.6"])
    t.handshake(peer_small())
    t.send(0, [STREAM(0, EMPTY_PRE + b"ab")])
    t.api("accept_bidi", bind="s", expect={"stream": 0})
    t.api("read", stream="s", max=10, expect={"data": b"ab", "fin": False})
    t.raw(0, F.encode_record([STREAM(0, b"cd", off=3, fin=True)])[:-1])
    t.down(0, "in", "eof")
    t.closed("PIPES_LOST", space="local", wire=False)
    t.api("read", stream="s", max=10,
          expect=err("session", space="local", code="PIPES_LOST", remote=False))
    out.append(t)
    return out


# =============================================================================
# core.streams

def core_streams():
    out = []
    t = Trace("core.streams.peer_open_read", "A peer stream with preamble, data and FIN",
              ["SPEC.md#7.2", "EXTENSIONS.md#4.4"])
    t.handshake(peer_small())
    t.send(0, [STREAM(0, pre(open_info=b"example.com:443", grease=True) + b"hello", fin=True)])
    t.api("accept_bidi", bind="s", expect={"stream": 0, "open_info": b"example.com:443"})
    t.api("read", stream="s", max=100, expect={"data": b"hello", "fin": True})
    out.append(t)

    t = Trace("core.streams.local_open_write", "A local stream carries its preamble at offset 0, "
              "with GREASE in the first preamble", ["EXTENSIONS.md#4.2", "EXTENSIONS.md#4.3"])
    t.handshake(peer_small())
    t.api("open_bidi", bind="a", open_info=b"10.0.0.1:22", expect={"ok": True})
    t.api("write", stream="a", data=b"ssh", fin=True, expect={"accepted": 3})
    t.frames(0, SDATA(1, pre(open_info=b"10.0.0.1:22", grease=True) + b"ssh", fin=True))
    out.append(t)

    t = Trace("core.streams.implicit_open_and_accept_order",
              "Higher streams open lower ones; each stream is offered once its own preamble "
              "arrives, without waiting for lower-numbered streams", ["SPEC.md#7.2",
                                                                       "EXTENSIONS.md#4.4"])
    t.handshake(peer_small())
    t.note("A correct opener sends first frames in identifier order (SPEC.md 7.2 rule 1); "
           "out of order, as here or across pipes and lanes, each stream is still offered "
           "when its own preamble arrives.")
    t.send(0, [STREAM(8, EMPTY_PRE + b"c", fin=True)])
    t.api("accept_bidi", expect={"stream": 8})
    t.api("accept_bidi", expect={"pending": True})
    t.send(0, [STREAM(0, EMPTY_PRE + b"a", fin=True)])
    t.api("accept_bidi", expect={"stream": 0})
    t.api("accept_bidi", expect={"pending": True})
    t.send(0, [STREAM(4, EMPTY_PRE + b"b", fin=True)])
    t.api("accept_bidi", expect={"stream": 4})
    out.append(t)

    simple = [
        ("stream_limit_exceeded", "Opening stream index 4 with a limit of 4 is fatal",
         ["SPEC.md#7.2"], [[STREAM(16, EMPTY_PRE)]], "STREAM_LIMIT_ERROR"),
        ("out_of_order_single_pipe", "Outside multipath a gap in stream data is fatal",
         ["SPEC.md#7.3"], [[STREAM(0, EMPTY_PRE + b"abc")], [STREAM(0, b"x", off=10)]],
         "PROTOCOL_VIOLATION"),
        ("duplicate_single_pipe", "Outside multipath repeated stream data is fatal",
         ["SPEC.md#7.3"], [[STREAM(0, EMPTY_PRE + b"abc")], [STREAM(0, EMPTY_PRE + b"abc")]],
         "PROTOCOL_VIOLATION"),
        ("frame_for_unopened_local_stream",
         "STREAM on a locally initiated stream that does not exist is fatal", ["SPEC.md#7.2"],
         [[STREAM(1, b"x")]], "STREAM_STATE_ERROR"),
        ("max_stream_data_on_receive_only",
         "MAX_STREAM_DATA for a peer unidirectional stream is fatal", ["SPEC.md#7.2"],
         [[MAX_STREAM_DATA(2, 5000)]], "STREAM_STATE_ERROR"),
        ("final_size_changed", "A reset that changes the final size is fatal",
         ["SPEC.md#7.5"], [[STREAM(0, EMPTY_PRE + b"abcdefghi", fin=True),
                            RESET_AT(0, 5, 12, 1)]], "FINAL_SIZE_ERROR"),
        ("data_beyond_final_size", "Data beyond the final size is fatal", ["SPEC.md#7.5"],
         [[STREAM(0, EMPTY_PRE + b"abc", fin=True)], [STREAM(0, b"d", off=4)]],
         "FINAL_SIZE_ERROR"),
    ]
    for name, title, refs, recs, code in simple:
        t = Trace(f"core.streams.{name}", title, refs)
        t.handshake(peer_small())
        t.send(0, *recs)
        t.closed(code)
        out.append(t)

    t = Trace("core.streams.frame_on_local_unidirectional",
              "STREAM sent by the peer on a local unidirectional stream is fatal", ["SPEC.md#7.2"])
    t.handshake(peer_small())
    t.api("open_uni", bind="u", expect={"ok": True})
    t.api("write", stream="u", data=b"x", fin=False, expect={"accepted": 1})
    t.frames(0, SDATA(3, LOCAL_FIRST_PRE + b"x"))
    t.send(0, [STREAM(3, b"y")])
    t.closed("STREAM_STATE_ERROR")
    out.append(t)

    t = Trace("core.streams.stop_sending_elicits_reset",
              "STOP_SENDING on a sending stream elicits a reset with the same code, and later "
              "writes report the peer's code", ["SPEC.md#4.3", "STATE_MACHINE.md#2.5"])
    t.handshake(peer_small())
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=b"hello", fin=False, expect={"accepted": 5})
    t.frames(0, SDATA(1, LOCAL_FIRST_PRE + b"hello"))
    t.send(0, [STOP(1, 77)])
    t.frames(0, {"type": RESET_ANY, "id": 1, "code": 77, "final_size": 8})
    t.api("write", stream="a", data=b"more", expect=err("stream_stopped", code=77, remote=True))
    t.is_open()
    out.append(t)

    t = Trace("core.streams.stop_sending_after_fin",
              "STOP_SENDING that crosses a FIN changes nothing and is not an error",
              ["SPEC.md#4.3", "SPEC.md#9.4"])
    t.handshake(peer_small())
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=b"hi", fin=True, expect={"accepted": 2})
    t.frames(0, SDATA(1, LOCAL_FIRST_PRE + b"hi", fin=True))
    t.send(0, [STOP(1, CANCELLED)])
    t.no_frames(0, ["RESET_STREAM", "RESET_STREAM_AT", "CONNECTION_CLOSE"])
    t.is_open()
    out.append(t)

    data = bytes(range(256)) * 9
    t = Trace("core.streams.no_data_after_stop_sending",
              "After STOP_SENDING no further data or FIN is sent, even for a write in progress",
              ["SPEC.md#4.3"])
    t.handshake(peer_small())
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=data[:2100], fin=False, expect={"accepted": 2100})
    t.frames(0, SDATA(1, (LOCAL_FIRST_PRE + data[:2100])[:2048]))
    t.send(0, [STOP(1, 9)])
    t.frames(0, {"type": RESET_ANY, "id": 1, "code": 9, "final_size": 2048})
    t.send(0, [MAX_STREAM_DATA(1, 8000)])
    t.no_frames(0, ["STREAM"])
    t.api("finish", stream="a", expect=err("stream_stopped", code=9, remote=True))
    out.append(t)

    t = Trace("core.streams.reject_by_application",
              "An application that declines a stream before reading sends REJECTED both ways",
              ["EXTENSIONS.md#7.2"])
    t.handshake(peer_small())
    t.send(0, [STREAM(0, EMPTY_PRE + b"req")])
    t.api("accept_bidi", bind="s", expect={"stream": 0})
    t.api("reject", stream="s", expect={"ok": True})
    t.frames(0, RESET(0, REJECTED, 0), STOP(0, REJECTED), match="unordered")
    out.append(t)

    t = Trace("core.streams.no_identifier_gap",
              "A stream cancelled before its first frame consumes no identifier",
              ["SPEC.md#7.2"])
    t.handshake(peer_small())
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("open_bidi", bind="b", expect={"ok": True})
    t.api("reset", stream="a", code=CANCELLED, expect={"ok": True})
    t.api("write", stream="b", data=b"x", fin=True, expect={"accepted": 1})
    t.frames(0, SDATA(1, LOCAL_FIRST_PRE + b"x", fin=True))
    t.no_frames(0, ["RESET_STREAM", "RESET_STREAM_AT"])
    out.append(t)

    t = Trace("core.streams.release_without_finish_resets",
              "Dropping an unfinished send handle resets with code 0; dropping an unread receive "
              "handle stops with code 0", ["EXTENSIONS.md#7.4", "SPEC.md#7.5"])
    t.handshake(peer_small())
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=b"partial", fin=False, expect={"accepted": 7})
    t.frames(0, SDATA(1, LOCAL_FIRST_PRE + b"partial"))
    t.api("release", stream="a", expect={"ok": True})
    t.frames(0, {"type": "RESET_STREAM_AT", "id": 1, "code": 0, "final_size": 10,
                 "reliable_size": {"min": 3}}, STOP(1, 0), match="unordered")
    t.send(0, [STREAM(0, EMPTY_PRE + b"unread")])
    t.api("accept_bidi", bind="s", expect={"stream": 0})
    t.api("release", stream="s", expect={"ok": True})
    t.frames(0, STOP(0, 0), {"type": RESET_ANY, "id": 0, "code": 0, "final_size": 0},
             match="unordered")
    out.append(t)

    t = Trace("core.streams.reset_is_never_complete",
              "A stream reset after its FIN is reported as reset, not as a clean end",
              ["SPEC.md#7.5", "STATE_MACHINE.md#2.5"])
    t.handshake(peer_small())
    t.send(0, [STREAM(0, EMPTY_PRE + b"abc", fin=True), RESET_AT(0, 0, 4, 4)])
    t.api("accept_bidi", bind="s", expect={"stream": 0})
    t.api("read", stream="s", max=10, expect={"data": b"abc", "fin": False})
    t.api("read", stream="s", max=10, expect=err("stream_reset", code=0, remote=True))
    out.append(t)

    t = Trace("core.streams.open_info_after_terminal",
              "A stream that became terminal before accept still offers its open info",
              ["EXTENSIONS.md#4.4", "SPEC.md#7.6"])
    t.handshake(peer_small())
    t.send(0, [STREAM(0, pre(open_info=b"target:443") + b"payload", fin=True), STOP(0, 5)])
    t.frames(0, {"type": RESET_ANY, "id": 0, "code": 5, "final_size": 0})
    t.api("accept_bidi", bind="s", expect={"stream": 0, "open_info": b"target:443"})
    t.api("read", stream="s", max=100, expect={"data": b"payload", "fin": True})
    t.api("release", stream="s", expect={"ok": True})
    out.append(t)

    t = Trace("core.streams.pinned_id_cancel",
              "A stream whose identifier the application read is announced and reset when it is "
              "cancelled before sending anything", ["SPEC.md#7.2", "EXTENSIONS.md#4.5"])
    t.handshake(peer_small())
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("stream_id", stream="a", expect={"stream": 1})
    t.api("reset", stream="a", code=5, expect={"ok": True})
    n = len(LOCAL_FIRST_PRE)
    t.frames(0, SDATA(1, LOCAL_FIRST_PRE),
             {"type": "RESET_STREAM_AT", "id": 1, "code": 5, "final_size": n, "reliable_size": n},
             match="unordered")
    t.api("open_bidi", bind="b", expect={"ok": True})
    t.api("write", stream="b", data=b"x", fin=True, expect={"accepted": 1})
    t.frames(0, SDATA(5, EMPTY_PRE + b"x", fin=True))
    out.append(t)

    t = Trace("core.streams.local_stop_outcome",
              "After a local stop, reads report the local stop and the peer's reset is no error",
              ["STATE_MACHINE.md#2.5", "SPEC.md#4.3"])
    t.handshake(peer_small())
    t.send(0, [STREAM(0, EMPTY_PRE + b"abc")])
    t.api("accept_bidi", bind="s", expect={"stream": 0})
    t.api("stop", stream="s", code=6, expect={"ok": True})
    t.frames(0, STOP(0, 6))
    t.api("read", stream="s", max=10, expect=err("closed_locally"))
    t.send(0, [RESET(0, 6, 4)])
    t.is_open()
    out.append(t)

    t = Trace("core.streams.reliable_data_before_reset",
              "Outside multipath the data below the Reliable Size is sent before RESET_STREAM_AT",
              ["SPEC.md#4.3"])
    t.handshake(peer_small())
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=b"abc", fin=False, expect={"accepted": 3})
    t.api("set_reliable_boundary", stream="a", expect={"ok": True})
    t.api("reset", stream="a", code=5, expect={"ok": True})
    n3 = len(LOCAL_FIRST_PRE) + 3
    t.frames(0, SDATA(1, LOCAL_FIRST_PRE + b"abc"), RESET_AT(1, 5, n3, n3), match="contains")
    out.append(t)

    t = Trace("core.streams.pinned_id_opens_stream",
              "Reading a stream ID before the first write sends the stream's preamble at once, "
              "so later streams are never held back", ["SPEC.md#7.2"])
    t.handshake(peer_small())
    t.api("open_bidi", bind="a", open_info=b"ctl", expect={"ok": True})
    t.api("stream_id", stream="a", expect={"stream": 1})
    pa = pre(open_info=b"ctl", grease=True)
    t.frames(0, SDATA(1, pa))
    t.api("open_bidi", bind="b", expect={"ok": True})
    t.api("write", stream="b", data=b"req", fin=True, expect={"accepted": 3})
    t.frames(0, SDATA(5, EMPTY_PRE + b"req", fin=True))
    t.api("write", stream="a", data=b"later", fin=True, expect={"accepted": 5})
    t.frames(0, SDATA(1, b"later", off=len(pa), fin=True))
    out.append(t)
    return out


# =============================================================================
# core.flow

def core_flow():
    out = []
    body = bytes(i % 251 for i in range(4096))

    t = Trace("core.flow.stream_credit_quarter",
              "MAX_STREAM_DATA is sent once a quarter of the window is consumed",
              ["SPEC.md#8.3"])
    t.handshake(peer_small())
    t.send(0, [STREAM(0, EMPTY_PRE + body[:1000])])
    t.api("accept_bidi", bind="s", expect={"stream": 0})
    t.api("read", stream="s", max=400, expect={"data": body[:400], "fin": False})
    t.api("read", stream="s", max=200, expect={"data": body[400:600], "fin": False})
    t.frames(0, MAX_STREAM_DATA(0, {"min": 2049}))
    out.append(t)

    t = Trace("core.flow.zero_window_liveness",
              "A drained stream window is reopened without any BLOCKED frame",
              ["SPEC.md#8.3"])
    t.handshake(peer_small())
    t.send(0, [STREAM(0, EMPTY_PRE + body[:2047])])
    t.api("accept_bidi", bind="s", expect={"stream": 0})
    t.api("read", stream="s", max=4096, expect={"data": body[:2047], "fin": False})
    t.frames(0, MAX_STREAM_DATA(0, {"min": 2049}))
    out.append(t)

    t = Trace("core.flow.credit_when_peer_blocked",
              "Released credit is sent at once when the peer has no credit left, even below the "
              "quarter-window update threshold and with unread data buffered",
              ["SPEC.md#8.3"])
    t.handshake(peer_small())
    t.send(0, [STREAM(0, EMPTY_PRE + body[:2047])])
    t.api("accept_bidi", bind="s", expect={"stream": 0})
    t.note("The peer has used all 2048 bytes of the stream window. Reading 100 bytes releases "
           "101 (with the 1-byte preamble), below 512, but the peer is blocked (SPEC.md 8.3 "
           "rule 3); 1947 bytes stay unread.")
    t.api("read", stream="s", max=100, expect={"data": body[:100], "fin": False})
    t.frames(0, MAX_STREAM_DATA(0, {"min": 2049}))
    out.append(t)

    t = Trace("core.flow.uni_credit_staged",
              "With max_incoming_uni_streams unset, 16 unidirectional streams are advertised "
              "at once; the first accept of a unidirectional stream raises the limit to 256",
              ["SPEC.md#8.5", "SPEC.md#5.3"],
              overrides={"tp": {"initial_max_streams_uni": 16},
                         "config": {"max_incoming_uni_streams": None}})
    t.handshake(peer_small())
    t.note("The peer opens a unidirectional stream at once, within the 16 advertised.")
    t.send(0, [STREAM(2, EMPTY_PRE + b"uni", fin=True)])
    t.is_open()
    t.no_frames(0, ["MAX_STREAMS"])
    t.api("accept_uni", bind="u", expect={"stream": 2})
    t.frames(0, MAX_STREAMS(256, bidi=False))
    t.api("read", stream="u", max=10, expect={"data": b"uni", "fin": True})
    out.append(t)

    t = Trace("core.flow.uni_credit_eager_when_configured",
              "An explicitly configured max_incoming_uni_streams is advertised in the transport "
              "parameters: the peer may open unidirectional streams before any accept",
              ["SPEC.md#8.5", "SPEC.md#5.3"])
    t.handshake(peer_small())
    t.send(0, [STREAM(2, EMPTY_PRE + b"early", fin=True)])
    t.is_open()
    t.api("accept_uni", bind="u", expect={"stream": 2})
    t.api("read", stream="u", max=10, expect={"data": b"early", "fin": True})
    t.no_frames(0, ["MAX_STREAMS"])
    out.append(t)

    t = Trace("core.flow.uni_credit_explicit_zero",
              "max_incoming_uni_streams explicitly set to 0 is eager too: nothing is granted on "
              "accept, and a peer unidirectional stream exceeds the limit",
              ["SPEC.md#8.5", "SPEC.md#7.2"],
              overrides={"tp": {"initial_max_streams_uni": 0},
                         "config": {"max_incoming_uni_streams": 0}})
    t.handshake(peer_small())
    t.api("accept_uni", bind="u", expect={"pending": True})
    t.no_frames(0, ["MAX_STREAMS"])
    t.send(0, [STREAM(2, EMPTY_PRE + b"x")])
    t.closed("STREAM_LIMIT_ERROR")
    out.append(t)

    pre_b = pre(open_info=b"o" * 40)
    t = Trace("core.flow.preamble_blocked_sends_data_blocked",
              "A stream whose preamble does not fit in the remaining session credit sends "
              "DATA_BLOCKED at the current limit, although some credit remains",
              ["SPEC.md#8.4", "EXTENSIONS.md#4.1"])
    t.handshake(peer_small(initial_max_data=256, zmux_preamble=64))
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=body[:220], fin=False, expect={"accepted": 220})
    t.frames(0, SDATA(1, LOCAL_FIRST_PRE + body[:220]))
    t.note(f"256 - 223 = 33 bytes of session credit remain; the next stream's preamble takes "
           f"{len(pre_b)} bytes and must be sent whole.")
    t.api("open_bidi", bind="b", open_info=b"o" * 40, expect={"ok": True})
    t.api("write", stream="b", data=b"data", fin=False, expect={"accepted": 4})
    t.frames(0, DATA_BLOCKED(256), {"type": "STREAM", "id": 5, "count": 0}, match="unordered")
    t.send(0, [MAX_DATA(1024)])
    t.frames(0, SDATA(5, pre_b + b"data"))
    out.append(t)

    t = Trace("core.flow.session_liveness",
              "When the peer used the whole session window and everything is read, MAX_DATA rises",
              ["SPEC.md#8.3"])
    t.handshake(peer_small())
    t.send(0, [STREAM(i, EMPTY_PRE + body[:2047]) for i in (0, 4, 8, 12)])
    for i in (0, 4, 8, 12):
        t.api("accept_bidi", bind=f"s{i}", expect={"stream": i})
        t.api("read", stream=f"s{i}", max=4096, expect={"data": body[:2047], "fin": False})
    t.frames(0, MAX_DATA({"min": 8193}))
    out.append(t)

    t = Trace("core.flow.stream_limit_violation", "Exceeding a stream window is fatal",
              ["SPEC.md#8.1"])
    t.handshake(peer_small())
    t.send(0, [STREAM(0, EMPTY_PRE + body[:2048])])
    t.closed("FLOW_CONTROL_ERROR")
    out.append(t)

    t = Trace("core.flow.session_limit_violation", "Exceeding the session window is fatal",
              ["SPEC.md#8.1", "SPEC.md#8.2"], overrides={"tp": {"initial_max_data": 4096}})
    t.handshake(peer_small())
    t.send(0, [STREAM(0, EMPTY_PRE + body[:2047]), STREAM(4, EMPTY_PRE + body[:2047]),
               STREAM(8, EMPTY_PRE)])
    t.closed("FLOW_CONTROL_ERROR")
    out.append(t)

    t = Trace("core.flow.final_size_counts_for_session",
              "A reset's final size counts against the session window",
              ["SPEC.md#8.2"], overrides={"tp": {"initial_max_data": 4096}})
    t.handshake(peer_small())
    t.send(0, [STREAM(0, EMPTY_PRE + body[:2047]), STREAM(4, EMPTY_PRE + b"x" * 9),
               RESET_AT(4, 7, 2048, 1), STREAM(8, EMPTY_PRE)])
    t.closed("FLOW_CONTROL_ERROR")
    out.append(t)

    t = Trace("core.flow.blocked_above_advertised",
              "A BLOCKED frame reporting a limit never advertised is fatal", ["SPEC.md#4.3"])
    t.handshake(peer_small())
    t.send(0, [STREAM_DATA_BLOCKED(0, 5000)])
    t.closed("PROTOCOL_VIOLATION")
    out.append(t)

    t = Trace("core.flow.data_blocked_above_advertised",
              "DATA_BLOCKED above the advertised session limit is fatal", ["SPEC.md#4.3"])
    t.handshake(peer_small())
    t.send(0, [DATA_BLOCKED(9000)])
    t.closed("PROTOCOL_VIOLATION")
    out.append(t)

    t = Trace("core.flow.backpressure_not_error",
              "Writes beyond the peer's credit wait for credit instead of failing the session",
              ["SPEC.md#8.1"])
    t.handshake(peer_small())
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=body[:4000], fin=False, expect={"accepted": 4000})
    full = LOCAL_FIRST_PRE + body[:4000] + body[:100]
    t.frames(0, SDATA(1, full[:2048]))
    t.is_open()
    t.note("A second write while blocked at the same limit adds no BLOCKED frame (SPEC 8.4).")
    t.api("write", stream="a", data=body[:100], fin=False, expect={"accepted": 100})
    t.no_frames(0, ["STREAM", "STREAM_DATA_BLOCKED", "DATA_BLOCKED"])
    t.send(0, [MAX_STREAM_DATA(1, 4096)])
    t.frames(0, SDATA(1, full[2048:4096], off=2048), match="contains")
    t.is_open()
    out.append(t)

    n = 256 - len(LOCAL_FIRST_PRE)
    t = Trace("core.flow.fin_only_at_session_limit",
              "A FIN-only STREAM frame adds nothing to the session sum: it is sent at once when "
              "the session credit is used up",
              ["SPEC.md#8.2"])
    t.handshake(peer_small(initial_max_data=256))
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=body[:n], fin=False, expect={"accepted": n})
    t.frames(0, SDATA(1, LOCAL_FIRST_PRE + body[:n]), match="contains")
    t.note("The session sum is now 256, the peer's whole MAX_DATA. The FIN-only frame adds "
           "nothing to the sum, so finishing the stream does not wait for MAX_DATA.")
    t.api("finish", stream="a", expect={"ok": True})
    t.frames(0, STREAM(1, b"", off=256, fin=True), match="contains")
    out.append(t)

    n = 2048 - len(LOCAL_FIRST_PRE)
    t = Trace("core.flow.fin_only_at_stream_limit",
              "A FIN-only STREAM frame at the stream's credit limit is sent at once",
              ["SPEC.md#8.2"])
    t.handshake(peer_small())
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=body[:n], fin=False, expect={"accepted": n})
    t.frames(0, SDATA(1, LOCAL_FIRST_PRE + body[:n]), match="contains")
    t.api("finish", stream="a", expect={"ok": True})
    t.frames(0, STREAM(1, b"", off=2048, fin=True), match="contains")
    out.append(t)

    t = Trace("core.flow.stream_credit_after_release",
              "Stream credit returns only after the application releases streams",
              ["SPEC.md#8.5"])
    t.handshake(peer_small())
    t.send(0, [STREAM(i, EMPTY_PRE + b"q", fin=True) for i in (0, 4, 8, 12)])
    for i in (0, 4, 8, 12):
        t.api("accept_bidi", bind=f"s{i}", expect={"stream": i})
        t.api("read", stream=f"s{i}", max=10, expect={"data": b"q", "fin": True})
        t.api("finish", stream=f"s{i}", expect={"ok": True})
    t.frames(0, *[STREAM(i, b"", fin=True) for i in (0, 4, 8, 12)], match="unordered")
    t.no_frames(0, ["MAX_STREAMS"])
    t.api("release", stream="s0", expect={"ok": True})
    t.api("release", stream="s4", expect={"ok": True})
    t.frames(0, MAX_STREAMS(6))
    out.append(t)

    t = Trace("core.flow.reset_before_accept_keeps_credit",
              "A stream reset before accept keeps its credit until the application releases it",
              ["SPEC.md#8.5"])
    t.handshake(peer_small())
    recs = []
    for i in (0, 4):
        p = pre(open_info=b"t")
        recs += [STREAM(i, p + b"abc"), RESET_AT(i, 9, len(p) + 3, len(p) + 3), STOP(i, 9)]
    t.send(0, recs)
    t.frames(0, {"type": RESET_ANY, "id": 0, "code": 9}, {"type": RESET_ANY, "id": 4, "code": 9},
             match="unordered")
    t.no_frames(0, ["MAX_STREAMS"])
    for i in (0, 4):
        t.api("accept_bidi", bind=f"s{i}", expect={"stream": i, "open_info": b"t"})
        t.api("read", stream=f"s{i}", max=10, expect={"data": b"abc"})
        t.api("read", stream=f"s{i}", max=10, expect=err("stream_reset", code=9, remote=True))
    t.no_frames(0, ["MAX_STREAMS"])
    t.api("release", stream="s0", expect={"ok": True})
    t.api("release", stream="s4", expect={"ok": True})
    t.frames(0, MAX_STREAMS(6))
    out.append(t)

    t = Trace("core.flow.rapid_reset_bounded",
              "Open-and-reset churn cannot exceed the stream limit without application release",
              ["SPEC.md#8.5", "SPEC.md#14.1"])
    t.handshake(peer_small())
    recs = []
    for i in (0, 4, 8, 12):
        recs += [STREAM(i, EMPTY_PRE), RESET_AT(i, 1, 1, 1), STOP(i, 1)]
    t.send(0, recs)
    t.send(0, [STREAM(16, EMPTY_PRE)])
    t.closed("STREAM_LIMIT_ERROR")
    out.append(t)

    t = Trace("core.flow.one_credit_frame_per_record",
              "Credit updates are coalesced: no record carries two MAX_STREAM_DATA frames for "
              "one stream", ["SPEC.md#8.3"])
    t.handshake(peer_small())
    t.send(0, [STREAM(0, EMPTY_PRE + body[:1900])])
    t.api("accept_bidi", bind="s", expect={"stream": 0})
    for k in range(3):
        t.api("read", stream="s", max=600, expect={"data": body[600 * k:600 * (k + 1)],
                                                   "fin": False})
    t.frames(0, MAX_STREAM_DATA(0, {"min": 2049}), one_credit_per_record=True)
    out.append(t)
    return out


# =============================================================================
# core.robustness

def core_robustness():
    out = []
    t = Trace("core.robustness.ping_responses_coalesced",
              "Many QX_PING requests produce one response with the largest sequence number",
              ["SPEC.md#4.5", "SPEC.md#9.2"])
    t.handshake(peer_small())
    t.send(0, [PING(i) for i in range(10)])
    t.frames(0, {"type": "QX_PING", "response": True, "count": 1}, PONG(9), match="unordered")
    out.append(t)

    t = Trace("core.robustness.ping_sequence_not_increasing",
              "A QX_PING request that does not increase the sequence number is fatal",
              ["SPEC.md#4.5"])
    t.handshake(peer_small())
    t.send(0, [PING(5)], [PING(5)])
    t.closed("PROTOCOL_VIOLATION")
    out.append(t)

    t = Trace("core.robustness.ping_response_unsolicited",
              "A QX_PING response for a request never sent is fatal", ["SPEC.md#4.5"])
    t.handshake(peer_small())
    t.send(0, [PONG(3)])
    t.closed("PROTOCOL_VIOLATION")
    out.append(t)

    t = Trace("core.robustness.local_ping_numbering",
              "Local QX_PING requests are numbered from the test hook and answered",
              ["SPEC.md#4.5"])
    t.handshake(peer_small())
    t.api("ping", expect={"ok": True})
    t.frames(0, PING(0))
    t.send(0, [PONG(0)])
    t.is_open()
    t.send(0, [PONG(1)])
    t.closed("PROTOCOL_VIOLATION")
    out.append(t)

    t = Trace("core.robustness.bounded_responses_while_blocked",
              "While the outbound pipe is blocked, response state stays bounded",
              ["SPEC.md#9.2"])
    t.handshake(peer_small())
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=b"x", fin=False, expect={"accepted": 1})
    t.frames(0, SDATA(1, LOCAL_FIRST_PRE + b"x"))
    t.writable(0, False)
    for i in range(50):
        t.send(0, [PING(i)])
    t.send(0, [STOP(1, 3)], [STOP(1, 3)], [STOP(1, 3)])
    t.writable(0, True)
    t.frames(0, {"type": "QX_PING", "response": True, "count": 1},
             {"type": "QX_PING", "response": True, "seq": 49},
             {"type": RESET_ANY, "id": 1, "count": 1}, match="unordered")
    out.append(t)

    t = Trace("core.robustness.budget_stale_limits_trip",
              "4096 no-effect frames exhaust the budget exactly; the next one closes the session",
              ["SPEC.md#9.4"])
    t.handshake(peer_small())
    t.send(0, [REPEAT(4096, MAX_DATA(8192))])
    t.is_open()
    t.send(0, [MAX_DATA(8192)])
    t.closed("ZMUX_EXCESSIVE_LOAD")
    out.append(t)

    t = Trace("core.robustness.budget_ping_flood_trips",
              "A QX_PING request that arrives while an earlier one is still unanswered is "
              "chargeable (only the largest is answered); a flood trips the budget",
              ["SPEC.md#4.5", "SPEC.md#9.4"])
    t.handshake(peer_small())
    pings = [PING(i) for i in range(4096)]
    t.note("4096 requests in four records with no response in between: the first arrives "
           "with no response owed and is free; each of the other 4095 arrives while one is "
           "owed and is charged (only the last request is answered); one token is left.")
    t.send(0, *[pings[k:k + 1024] for k in range(0, 4096, 1024)])
    t.is_open()
    t.note("The response to 4095 has been written. Of the next three requests in one record "
           "the first is free and the other two are charged: the budget goes below zero.")
    t.send(0, [PING(4096), PING(4097), PING(4098)])
    t.closed("ZMUX_EXCESSIVE_LOAD")
    out.append(t)

    t = Trace("core.robustness.budget_answered_pings_free",
              "QX_PING requests that are each answered before the next one arrives are not "
              "chargeable, even with the budget exhausted (a plain QMux peer that measures "
              "RTT every 20 ms)", ["SPEC.md#4.5", "SPEC.md#9.4"])
    t.handshake(peer_small())
    t.send(0, [REPEAT(4096, MAX_DATA(8192))])
    t.is_open()
    for i in range(10):
        t.send(0, [PING(i)])
        t.frames(0, PONG(i))
    t.is_open()
    t.send(0, [PING(10), PING(11)])
    t.closed("ZMUX_EXCESSIVE_LOAD")
    out.append(t)

    t = Trace("core.robustness.budget_padding_records_trip",
              "Records of PADDING only are chargeable", ["SPEC.md#9.4"])
    t.handshake(peer_small())
    t.send(0, *([[PADDING(1)]] * 4096))
    t.is_open()
    t.send(0, [PADDING(1)])
    t.closed("ZMUX_EXCESSIVE_LOAD")
    out.append(t)

    t = Trace("core.robustness.budget_refills_with_time",
              "The budget refills at 32 tokens per second", ["SPEC.md#9.4"])
    t.handshake(peer_small())
    t.send(0, [REPEAT(4096, MAX_DATA(8192))])
    t.adv(1000)
    t.send(0, [REPEAT(32, MAX_DATA(8192))])
    t.is_open()
    t.send(0, [MAX_DATA(8192)])
    t.closed("ZMUX_EXCESSIVE_LOAD")
    out.append(t)

    t = Trace("core.robustness.budget_churn_never_trips",
              "Stream churn with crossing STOP_SENDING never drains the budget (X1): frames for "
              "terminal or discarded streams are not chargeable", ["SPEC.md#9.4"])
    t.handshake(peer_small())
    for k in range(100):
        sid = 4 * k
        t.send(0, [STREAM(sid, EMPTY_PRE + b"request-%03d" % k, fin=True)])
        t.api("accept_bidi", bind="s", expect={"stream": sid})
        t.api("read", stream="s", max=100, expect={"data": b"request-%03d" % k, "fin": True})
        t.api("write", stream="s", data=b"ok", fin=True, expect={"accepted": 2})
        t.frames(0, SDATA(sid, b"ok", fin=True))
        t.api("release", stream="s", expect={"ok": True})
        t.send(0, [STOP(sid, CANCELLED), MAX_STREAM_DATA(sid, 9000)])
    t.is_open()
    t.send(0, [REPEAT(4096, MAX_DATA(8192))])
    t.is_open()
    t.send(0, [MAX_DATA(8192)])
    t.closed("ZMUX_EXCESSIVE_LOAD")
    out.append(t)

    t = Trace("core.robustness.budget_blocked_repeats_trip",
              "The first BLOCKED frame for a limit is free; repeats are chargeable",
              ["SPEC.md#9.4"])
    t.handshake(peer_small())
    t.send(0, [STREAM(0, EMPTY_PRE + b"a"), REPEAT(4097, STREAM_DATA_BLOCKED(0, 2048))])
    t.is_open()
    t.send(0, [STREAM_DATA_BLOCKED(0, 2048)])
    t.closed("ZMUX_EXCESSIVE_LOAD")
    out.append(t)

    t = Trace("core.robustness.discarded_stream_frames_not_charged",
              "Frames for a discarded stream are ignored and never chargeable",
              ["SPEC.md#7.6", "SPEC.md#9.4"])
    t.handshake(peer_small())
    t.send(0, [STREAM(0, EMPTY_PRE + b"q", fin=True)])
    t.api("accept_bidi", bind="s", expect={"stream": 0})
    t.api("read", stream="s", max=10, expect={"data": b"q", "fin": True})
    t.api("finish", stream="s", expect={"ok": True})
    t.frames(0, STREAM(0, b"", fin=True))
    t.api("release", stream="s", expect={"ok": True})
    t.send(0, [REPEAT(2500, STOP(0, 1)), REPEAT(2500, MAX_STREAM_DATA(0, 3000))])
    t.is_open()
    t.no_frames(0, ["RESET_STREAM", "RESET_STREAM_AT", "CONNECTION_CLOSE"])
    out.append(t)

    t = Trace("core.robustness.no_acknowledgements_without_multipath",
              "Without multipath, and with a peer that did not send zmux_pipe_ack, no PIPE_ACK "
              "is ever sent", ["SPEC.md#9.6", "MULTIPATH.md#3.5"])
    t.handshake(peer_small())
    t.send(0, [STREAM(0, EMPTY_PRE + b"z" * 1500), PING(1)])
    t.no_frames("*", ["PIPE_ACK", "PIPE_PING"])
    t.adv(100)
    t.no_frames("*", ["PIPE_ACK", "PIPE_PING"])
    out.append(t)

    t = Trace("core.robustness.one_local_ping_in_flight",
              "The endpoint has at most one QX_PING request outstanding; a ping while one is "
              "outstanding joins it", ["SPEC.md#4.5", "SPEC.md#9.4.3"])
    t.handshake(peer_small())
    t.api("ping", expect={"ok": True})
    t.api("ping", expect={"ok": True})
    t.frames(0, {"type": "QX_PING", "response": False, "count": 1}, PING(0), match="unordered")
    t.api("ping", expect={"ok": True})
    t.no_frames(0, ["QX_PING"])
    t.send(0, [PONG(0)])
    t.api("ping", expect={"ok": True})
    t.frames(0, PING(1))
    out.append(t)

    def trip(name, title, ref_rows, setup, charged):
        t = Trace(f"core.robustness.{name}", title, ["SPEC.md#9.4"])
        t.handshake(peer_small())
        if ref_rows:
            ref_rows(t)
        t.send(0, setup + [REPEAT(4096, charged)])
        t.is_open()
        t.send(0, [charged])
        t.closed("ZMUX_EXCESSIVE_LOAD")
        out.append(t)

    trip("budget_empty_stream_frames_trip",
         "NE-5: STREAM frames that carry neither new data nor new final-size information are "
         "chargeable", None,
         [STREAM(0, EMPTY_PRE + b"a")], STREAM(0, b"", off=2))
    trip("budget_stale_priority_updates_trip",
         "NE-7: PRIORITY_UPDATE frames that do not increase the sequence number are chargeable; "
         "the first update is not", None,
         [STREAM(0, EMPTY_PRE + b"a"), PRIO(0, 1, 2)], PRIO(0, 1, 3))
    trip("budget_goaway_repeats_trip",
         "NE-8: a GOAWAY that lowers no watermark is chargeable; the first one is not", None,
         [GOAWAY(3, 3, 0)], GOAWAY(3, 3, 0))

    def ping_first(t):
        t.api("ping", expect={"ok": True})
        t.frames(0, PING(0))

    trip("budget_stale_ping_responses_trip",
         "NE-8: a QX_PING response that does not exceed every earlier response is chargeable; "
         "the first response is not", ping_first, [PONG(0)], PONG(0))
    return out


# =============================================================================
# core.close

def core_close():
    out = []
    t = Trace("core.close.half_close_then_linger",
              "Close sends CONNECTION_CLOSE, shuts down writing gracefully, and keeps reading "
              "until the peer ends", ["SPEC.md#6.5"])
    t.handshake(peer_small())
    t.api("close", code=42, reason=b"bye", app=True, expect={"ok": True})
    t.frames(0, CLOSE(42, app=True, reason=b"bye"))
    t.pipe_closed(0, "out", "graceful")
    t.closed(42, space="application", wire=True, remote=False)
    t.send(0, [STREAM(0, EMPTY_PRE + b"late")])
    t.no_frames(0)
    t.down(0, "in", "eof")
    t.state(session="CLOSED")
    out.append(t)

    t = Trace("core.close.linger_timeout",
              "Lingering ends after min(2 s, max(250 ms, 3 x SRTT)), with SRTT 333 ms by default",
              ["SPEC.md#6.5"])
    t.handshake(peer_small())
    t.api("close", code=0, reason=b"", app=True, expect={"ok": True})
    t.frames(0, CLOSE(0, app=True))
    t.closed(0, space="application", remote=False)
    t.adv(998)
    t.state(session="CLOSING")
    t.adv(1)
    t.state(session="CLOSED")
    out.append(t)

    t = Trace("core.close.peer_code_survives_carrier_reset",
              "A peer CONNECTION_CLOSE followed by a carrier reset reports the peer's code",
              ["SPEC.md#6.5"])
    t.handshake(peer_small())
    t.send(0, [STREAM(0, EMPTY_PRE + b"abc")])
    t.api("accept_bidi", bind="s", expect={"stream": 0})
    t.send(0, [CLOSE(9, app=True, reason=b"done")])
    t.down(0, "bidi", "error")
    t.closed(9, space="application", remote=True)
    t.api("write", stream="s", data=b"x",
          expect=err("session", space="application", code=9, remote=True))
    out.append(t)

    t = Trace("core.close.respond_to_peer_close",
              "On a peer CONNECTION_CLOSE the endpoint shuts down writing gracefully",
              ["SPEC.md#6.5"])
    t.handshake(peer_small())
    t.send(0, [CLOSE(0)])
    t.pipe_closed(0, "out", "graceful")
    t.closed("NO_ERROR", remote=True)
    out.append(t)

    t = Trace("core.close.idle_timeout", "The effective idle timeout is the smaller value",
              ["SPEC.md#6.4"])
    t.handshake(peer_small(max_idle_timeout=10000))
    t.adv(9999)
    t.is_open()
    t.adv(1)
    t.closed("IDLE_TIMEOUT", space="local", wire=False)
    t.pipe_closed(0, "out", "graceful")
    out.append(t)

    t = Trace("core.close.keep_alive_unanswered_idles_out",
              "In a session without acknowledgement, sending a keep-alive QX_PING does not "
              "restart the idle timer: a peer that never answers is closed at the idle timeout",
              ["SPEC.md#6.4"], overrides={"config": {"keep_alive_period": 5000}})
    t.handshake(peer_small(max_idle_timeout=10000))
    t.adv(5000)
    t.frames(0, PING(0))
    t.note("The peer does not answer. The record carrying only the QX_PING request does not "
           "restart the idle timer, which still runs from the handshake at 0 ms.")
    t.adv(4999)
    t.is_open()
    t.adv(1)
    t.closed("IDLE_TIMEOUT", space="local", wire=False)
    t.pipe_closed(0, "out", "graceful")
    out.append(t)

    t = Trace("core.close.keep_alive", "Keep-alive sends QX_PING after the configured period",
              ["SPEC.md#6.4"], overrides={"config": {"keep_alive_period": 5000}})
    t.handshake(peer_small())
    t.adv(4999)
    t.no_frames(0, ["QX_PING"])
    t.adv(1)
    t.frames(0, PING(0))
    t.send(0, [PONG(0)])
    t.is_open()
    out.append(t)

    t = Trace("core.close.pending_read_fails_with_session_error",
              "A pending read fails with the peer's close code", ["STATE_MACHINE.md#2.5"])
    t.handshake(peer_small())
    t.send(0, [STREAM(0, EMPTY_PRE + b"abc")])
    t.api("accept_bidi", bind="s", expect={"stream": 0})
    t.api("read", stream="s", max=10, expect={"data": b"abc", "fin": False})
    t.api("read", stream="s", max=10, expect={"pending": True})
    t.send(0, [CLOSE(3, app=True)])
    t.api("read", stream="s", max=10,
          expect=err("session", space="application", code=3, remote=True))
    t.closed(3, space="application", remote=True)
    out.append(t)

    t = Trace("core.close.carrier_failure", "Outside multipath a carrier error ends the session",
              ["SPEC.md#6.6"])
    t.handshake(peer_small())
    t.down(0, "bidi", "error")
    t.closed("PIPES_LOST", space="local", wire=False)
    out.append(t)

    t = Trace("core.close.peer_shutdown_is_idle_close",
              "In a session without acknowledgement, a graceful end of the peer's pipe without "
              "CONNECTION_CLOSE is the peer's idle close: the endpoint shuts down gracefully "
              "without frames",
              ["SPEC.md#6.4", "SPEC.md#6.6"])
    t.handshake(peer_small())
    t.down(0, "in", "eof")
    t.pipe_closed(0, "out", "graceful")
    t.closed("IDLE_TIMEOUT", space="local", wire=False, remote=True)
    out.append(t)
    return out


# =============================================================================
# ext.*

def ext_role():
    out = []
    t = Trace("ext.role.auto_larger_nonce_initiates",
              "With both auto, the larger nonce becomes the initiator", ["EXTENSIONS.md#2.3"],
              profile="zmux-role-auto")
    t.handshake(peer_small(zmux_role={"role": "auto", "nonce": 5}),
                features=["role"] + BASE_FEATURES, role="initiator")
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=b"x", fin=True, expect={"accepted": 1})
    t.frames(0, SDATA(0, LOCAL_FIRST_PRE + b"x", fin=True))
    out.append(t)

    t = Trace("ext.role.auto_equal_nonce_conflict", "Equal nonces are a role conflict",
              ["EXTENSIONS.md#2.3"], profile="zmux-role-auto")
    t.up(0, "bidi", via="bootstrap")
    local_tp_first(t)
    t.send(0, [TP(tp_list(peer_small(zmux_role={"role": "auto", "nonce": 1000})))])
    t.closed("ZMUX_ROLE_CONFLICT")
    out.append(t)

    t = Trace("ext.role.explicit_conflict", "Two explicit initiators conflict",
              ["EXTENSIONS.md#2.3"], profile="zmux-small",
              overrides={"tp": {"zmux_role": {"role": "initiator"}},
                         "config": {"role": "initiator"}})
    t.up(0, "bidi", via="bootstrap")
    t.frames(0, TP_ANY, match="first")
    t.send(0, [TP(tp_list(peer_small(zmux_role={"role": "initiator"})))])
    t.closed("ZMUX_ROLE_CONFLICT")
    out.append(t)

    t = Trace("ext.role.explicit_contradicts_transport",
              "An explicit role that contradicts the transport role, with a peer that sent no "
              "zmux_role, is a role conflict", ["EXTENSIONS.md#2.3"], profile="zmux-small",
              overrides={"tp": {"zmux_role": {"role": "initiator"}},
                         "config": {"role": "initiator"}})
    t.up(0, "bidi", via="bootstrap")
    t.frames(0, TP_ANY, match="first")
    t.send(0, [TP(tp_list(peer_small()))])
    t.closed("ZMUX_ROLE_CONFLICT")
    out.append(t)

    t = Trace("ext.role.auto_with_plain_peer",
              "Without the peer's zmux_role the transport role applies", ["EXTENSIONS.md#2.3"],
              profile="zmux-role-auto")
    t.handshake(peer_small(), features=BASE_FEATURES, role="responder")
    out.append(t)
    return out


def ext_goaway():
    out = []
    t = Trace("ext.goaway.receive_watermark",
              "Streams at or above the watermark are rejected locally; no new streams open",
              ["EXTENSIONS.md#3.5"])
    t.handshake(peer_small())
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=b"one", fin=False, expect={"accepted": 3})
    t.frames(0, SDATA(1, LOCAL_FIRST_PRE + b"one"))
    t.api("open_bidi", bind="b", expect={"ok": True})
    t.api("write", stream="b", data=b"two", fin=False, expect={"accepted": 3})
    t.frames(0, SDATA(5, EMPTY_PRE + b"two"))
    t.send(0, [GOAWAY(1, 4, 0)])
    t.event("goaway_received", max_bidi=1, max_uni=4, code=0)
    t.api("write", stream="b", data=b"x", expect=err("stream_rejected"))
    t.api("write", stream="a", data=b"x", fin=False, expect={"accepted": 1})
    t.api("open_bidi", bind="c", expect=err("going_away"))
    t.state(session="DRAINING")
    out.append(t)

    t = Trace("ext.goaway.unemitted_stream_fails",
              "After any GOAWAY no new stream opens: a stream that was opened but has not "
              "emitted its first frame fails with going away, even below the watermark",
              ["EXTENSIONS.md#3.5", "STATE_MACHINE.md#2.5"])
    t.handshake(peer_small())
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=b"one", fin=False, expect={"accepted": 3})
    t.frames(0, SDATA(1, LOCAL_FIRST_PRE + b"one"))
    t.api("open_bidi", bind="b", expect={"ok": True})
    t.note("Phase-1 GOAWAY: watermarks equal to the granted limits, refusing nothing in flight.")
    t.send(0, [GOAWAY(4, 4, 0)])
    t.event("goaway_received", max_bidi=4, max_uni=4, code=0)
    t.api("write", stream="b", data=b"two", expect=err("going_away"))
    t.no_frames(0, ["STREAM"])
    t.api("write", stream="a", data=b"x", fin=False, expect={"accepted": 1})
    t.frames(0, SDATA(1, b"x", off=len(LOCAL_FIRST_PRE) + 3))
    t.api("open_bidi", bind="c", expect=err("going_away"))
    out.append(t)

    t = Trace("ext.goaway.minimum_watermark",
              "Receivers keep the minimum watermark; a larger one has no effect",
              ["EXTENSIONS.md#3.5"])
    t.handshake(peer_small())
    t.send(0, [GOAWAY(3, 3, 0)])
    t.event("goaway_received", max_bidi=3, max_uni=3, code=0)
    t.send(0, [GOAWAY(4, 4, 0)])
    t._add(op="expect_no_event", event="goaway_received")
    t.send(0, [GOAWAY(2, 4, 0)])
    t.event("goaway_received", max_bidi=2, max_uni=3, code=0)
    t.is_open()
    out.append(t)

    t = Trace("ext.goaway.two_phase_with_fence",
              "Phase 1 keeps credit, the PING fence completes, phase 2 rejects higher streams",
              ["EXTENSIONS.md#3.3", "EXTENSIONS.md#3.4"])
    t.handshake(peer_small())
    t.send(0, [STREAM(0, EMPTY_PRE + b"a")])
    t.api("accept_bidi", expect={"stream": 0})
    t.api("shutdown", code=0, expect={"ok": True})
    t.frames(0, GOAWAY(4, 4, 0), PING(0))
    t.send(0, [STREAM(4, EMPTY_PRE + b"b")])
    t.api("accept_bidi", expect={"stream": 4})
    t.send(0, [PONG(0)])
    t.frames(0, GOAWAY(2, 0, 0))
    t.send(0, [STREAM(8, EMPTY_PRE + b"c")])
    t.frames(0, RESET(8, REJECTED, 0), STOP(8, REJECTED), match="unordered")
    t.api("accept_bidi", expect={"pending": True})
    t.note("After the local STOP_SENDING a plain reset from the opener is allowed (EXTENSIONS 4.4).")
    t.send(0, [RESET(8, REJECTED, 2)])
    t.is_open()
    out.append(t)

    t = Trace("ext.goaway.rejected_late_preamble",
              "Streams opened implicitly above the watermark are rejected; their late preamble "
              "is discarded and they are never offered", ["EXTENSIONS.md#3.4", "EXTENSIONS.md#4.4"])
    t.handshake(peer_small())
    t.send(0, [STREAM(0, EMPTY_PRE + b"a")])
    t.api("accept_bidi", expect={"stream": 0})
    t.api("shutdown", code=0, expect={"ok": True})
    t.frames(0, GOAWAY(4, 4, 0), PING(0))
    t.send(0, [PONG(0)])
    t.frames(0, GOAWAY(1, 0, 0))
    t.send(0, [STREAM(8, EMPTY_PRE + b"d")])
    t.frames(0, RESET(4, REJECTED, 0), STOP(4, REJECTED), RESET(8, REJECTED, 0),
             STOP(8, REJECTED), match="unordered")
    t.send(0, [STREAM(4, EMPTY_PRE + b"late", fin=True)])
    t.api("accept_bidi", expect={"pending": True})
    t.is_open()
    out.append(t)

    t = Trace("ext.goaway.hole_below_watermark_offered",
              "A stream below the final watermark whose preamble arrives after a higher stream "
              "was offered is offered, not rejected", ["EXTENSIONS.md#3.3", "SPEC.md#7.2"])
    t.handshake(peer_small())
    t.note("Stream 4 opens stream 0 implicitly; stream 0's preamble arrives only after the "
           "final GOAWAY, as it can across pipes or lanes.")
    t.send(0, [STREAM(4, EMPTY_PRE + b"b")])
    t.api("accept_bidi", expect={"stream": 4})
    t.api("shutdown", code=0, expect={"ok": True})
    t.frames(0, GOAWAY(4, 4, 0), PING(0))
    t.send(0, [PONG(0)])
    t.frames(0, GOAWAY(2, 0, 0))
    t.send(0, [STREAM(0, EMPTY_PRE + b"a")])
    t.api("accept_bidi", expect={"stream": 0})
    t.no_frames(0, ["RESET_STREAM", "STOP_SENDING"])
    t.is_open()
    out.append(t)

    t = Trace("ext.goaway.no_credit_after_goaway",
              "After its first GOAWAY an endpoint raises no MAX_STREAMS", ["EXTENSIONS.md#3.3",
                                                                          "SPEC.md#8.5"])
    t.handshake(peer_small())
    t.send(0, [STREAM(i, EMPTY_PRE + b"q", fin=True) for i in (0, 4)])
    for i in (0, 4):
        t.api("accept_bidi", bind=f"s{i}", expect={"stream": i})
        t.api("read", stream=f"s{i}", max=10, expect={"data": b"q", "fin": True})
        t.api("finish", stream=f"s{i}", expect={"ok": True})
    t.frames(0, STREAM(0, b"", fin=True), STREAM(4, b"", fin=True), match="unordered")
    t.api("shutdown", code=0, expect={"ok": True})
    t.frames(0, GOAWAY(4, 4, 0), PING(0))
    t.api("release", stream="s0", expect={"ok": True})
    t.api("release", stream="s4", expect={"ok": True})
    t.no_frames(0, ["MAX_STREAMS"])
    out.append(t)
    return out


def ext_preamble():
    out = []
    oi = pre(open_info=b"x" * 10)

    def bad(name, title, recs, refs=("EXTENSIONS.md#4.4",)):
        t = Trace(f"ext.preamble.{name}", title, refs)
        t.handshake(peer_small())
        t.send(0, *recs)
        t.closed("PROTOCOL_VIOLATION")
        out.append(t)

    bad("split_across_frames", "A preamble not contained in the frame at offset 0 is fatal",
        [[STREAM(0, oi[:5])], [STREAM(0, oi[5:], off=5)]])
    big = pre(open_info=b"a" * 600)
    bad("above_limit", "A preamble larger than the receiver's limit is fatal", [[STREAM(0, big)]])
    bad("plain_reset", "RESET_STREAM on an opener's direction with the preamble in use is fatal",
        [[STREAM(0, EMPTY_PRE + b"abc"), RESET(0, 1, 4)]], ("EXTENSIONS.md#4.4", "EXTENSIONS.md#4.5"))
    p6 = pre(open_info=b"abc")
    bad("reliable_size_too_small", "A Reliable Size smaller than the preamble is fatal",
        [[STREAM(0, p6), RESET_AT(0, 4, len(p6), 2)]], ("EXTENSIONS.md#4.4", "EXTENSIONS.md#4.5"))
    bad("zero_final_size", "A stream ending at offset 0 has no preamble and is fatal",
        [[STREAM(0, b"", fin=True)]])
    bad("reset_before_any_data", "A plain reset with final size 0 is fatal",
        [[RESET(0, 1, 0)]])
    bad("duplicate_open_info", "A second OPEN_INFO TLV is fatal",
        [[STREAM(0, pre(open_info=b"a", extra=[(0x01, b"b")]))]])
    bad("bad_priority_tlv", "A PRIORITY TLV with reserved bits set is fatal",
        [[STREAM(0, pre(extra=[(0x02, b"\x61")]))]])
    bad("tlv_overrun", "A TLV that overruns the preamble is fatal",
        [[STREAM(0, V(3) + V(0x01) + V(9) + b"a")]])
    bad("pipe_carrier_with_value", "A PIPE_CARRIER TLV with a value is fatal",
        [[STREAM(0, pre(extra=[(0x05, b"x")]))]])
    bad("duplicate_pipe_carrier", "A second PIPE_CARRIER TLV is fatal",
        [[STREAM(0, pre(extra=[(0x05, b""), (0x05, b"")]))]])

    t = Trace("ext.preamble.reset_at_protects_open_info",
              "RESET_STREAM_AT covering the preamble delivers open info, then the reset",
              ["EXTENSIONS.md#4.5"])
    t.handshake(peer_small())
    p = pre(open_info=b"svc")
    t.send(0, [STREAM(0, p), RESET_AT(0, 4, len(p), len(p))])
    t.api("accept_bidi", bind="s", expect={"stream": 0, "open_info": b"svc"})
    t.api("read", stream="s", max=10, expect=err("stream_reset", code=4, remote=True))
    out.append(t)

    t = Trace("ext.preamble.unknown_reserved_and_grease_ignored",
              "Unknown, DEADLINE and GREASE TLVs are ignored", ["EXTENSIONS.md#4.3"])
    t.handshake(peer_small())
    p = pre(open_info=b"x", extra=[(0x04, b"zz"), (0x3F, b""), (0x03, V(5000)), (0x40, b"g")],
            grease=True)
    t.send(0, [STREAM(0, p + b"d", fin=True)])
    t.api("accept_bidi", bind="s", expect={"stream": 0, "open_info": b"x"})
    t.api("read", stream="s", max=10, expect={"data": b"d", "fin": True})
    out.append(t)

    t = Trace("ext.preamble.application_tlvs_delivered",
              "Application TLVs (0x1000 to 0x3fff) are offered with the stream in the order "
              "received, repeated types included; GREASE values inside the range and types "
              "outside it are not; PIPE_CARRIER is reported", ["EXTENSIONS.md#4.3", "EXTENSIONS.md#4.4"])
    t.handshake(peer_small())
    grease_in_range = 0x1F * 132 + 0x21
    assert 0x1000 <= grease_in_range <= 0x3FFF
    p = pre(open_info=b"x", extra=[(0x1000, b"dst-a"), (0x05, b""), (0x1002, b"\x05"),
                                   (grease_in_range, b"g"), (0x4000, b"out"), (0x0FFF, b"low"),
                                   (0x1000, b"dst-b")])
    t.send(0, [STREAM(0, p + b"d", fin=True)])
    t.api("accept_bidi", bind="s", expect={
        "stream": 0, "open_info": b"x", "pipe_carrier": True,
        "app_tlvs": [{"type": 0x1000, "value": b"dst-a"}, {"type": 0x1002, "value": b"\x05"},
                     {"type": 0x1000, "value": b"dst-b"}]})
    t.api("read", stream="s", max=10, expect={"data": b"d", "fin": True})
    out.append(t)

    t = Trace("ext.preamble.priority_delivered", "The PRIORITY TLV is offered with the stream",
              ["EXTENSIONS.md#4.3", "EXTENSIONS.md#5.4"])
    t.handshake(peer_small())
    t.send(0, [STREAM(0, pre(priority=(0, True)) + b"x")])
    t.api("accept_bidi", expect={"stream": 0, "priority": {"urgency": 0, "incremental": True}})
    out.append(t)

    t = Trace("ext.preamble.grease_only_in_first",
              "GREASE appears in the first local preamble of the session",
              ["EXTENSIONS.md#4.3"])
    t.handshake(peer_small())
    t.api("open_bidi", bind="a", open_info=b"A", expect={"ok": True})
    t.api("write", stream="a", data=b"1", fin=True, expect={"accepted": 1})
    t.frames(0, SDATA(1, pre(open_info=b"A", grease=True) + b"1", fin=True))
    t.api("open_bidi", bind="b", open_info=b"B", priority={"urgency": 1, "incremental": False},
          expect={"ok": True})
    t.api("write", stream="b", data=b"2", fin=True, expect={"accepted": 1})
    t.frames(0, SDATA(5, pre(open_info=b"B", priority=(1, False)) + b"2", fin=True))
    out.append(t)

    t = Trace("ext.preamble.counts_against_flow_control",
              "Preamble bytes count against the stream window", ["EXTENSIONS.md#4.2"])
    t.handshake(peer_small())
    p = pre(open_info=b"a" * 500)
    t.send(0, [STREAM(0, p + b"b" * (2049 - len(p)))])
    t.closed("FLOW_CONTROL_ERROR")
    out.append(t)
    return out


def ext_priority():
    out = []

    def bad(name, title, recs, code, refs=("EXTENSIONS.md#5.4",)):
        t = Trace(f"ext.priority.{name}", title, refs)
        t.handshake(peer_small())
        t.send(0, *recs)
        t.closed(code)
        out.append(t)

    rec = F.encode_record([RAW(V(0x073D08CF) + V(0) + V(1) + b"\x01")])
    t = Trace("ext.priority.reserved_bits", "PRIORITY_UPDATE with reserved bits set is fatal",
              ["EXTENSIONS.md#5.2"])
    t.handshake(peer_small())
    t.raw(0, rec)
    t.closed("FRAME_ENCODING_ERROR")
    out.append(t)
    bad("unopened_local_stream", "PRIORITY_UPDATE for an unopened local stream is fatal",
        [[PRIO(1, 1, 0)]], "STREAM_STATE_ERROR")
    bad("peer_unidirectional", "PRIORITY_UPDATE for the peer's own unidirectional stream is fatal",
        [[PRIO(2, 1, 0)]], "STREAM_STATE_ERROR")
    bad("opens_beyond_limit", "PRIORITY_UPDATE opening a stream beyond the limit is fatal",
        [[PRIO(16, 1, 0)]], "STREAM_LIMIT_ERROR")

    t = Trace("ext.priority.opens_peer_stream",
              "PRIORITY_UPDATE may arrive before the stream's data", ["EXTENSIONS.md#5.4"])
    t.handshake(peer_small())
    t.send(0, [PRIO(0, 1, 2)], [STREAM(0, EMPTY_PRE + b"x")])
    t.api("accept_bidi", expect={"stream": 0})
    t.is_open()
    out.append(t)

    t = Trace("ext.priority.send_update_sequence",
              "Local PRIORITY_UPDATE sequence numbers start at 1 and increase",
              ["EXTENSIONS.md#5.4"])
    t.handshake(peer_small())
    t.send(0, [STREAM(0, EMPTY_PRE + b"x")])
    t.api("accept_bidi", bind="s", expect={"stream": 0})
    t.api("set_peer_priority", stream="s", urgency=1, incremental=True, expect={"ok": True})
    t.frames(0, PRIO(0, 1, 1, True))
    t.api("set_peer_priority", stream="s", urgency=6, incremental=False, expect={"ok": True})
    t.frames(0, PRIO(0, 2, 6, False))
    out.append(t)

    t = Trace("ext.priority.not_in_use",
              "Without both zmux_priority parameters, PRIORITY_UPDATE is an unknown frame",
              ["SPEC.md#5.5"])
    d = peer_small()
    del d["zmux_priority"]
    t.handshake(d, features=["goaway", "preamble"])
    t.send(0, [STREAM(0, EMPTY_PRE + b"x")])
    t.api("accept_bidi", bind="s", expect={"stream": 0})
    t.api("set_peer_priority", stream="s", urgency=1, incremental=False,
          expect=err("unsupported"))
    t.send(0, [PRIO(0, 1, 0)])
    t.closed("FRAME_ENCODING_ERROR")
    out.append(t)

    t = Trace("ext.priority.stale_ignored",
              "A PRIORITY_UPDATE with an old sequence number is ignored, not an error",
              ["EXTENSIONS.md#5.4"])
    t.handshake(peer_small())
    t.send(0, [STREAM(0, EMPTY_PRE + b"x")])
    t.api("accept_bidi", expect={"stream": 0})
    t.send(0, [PRIO(0, 2, 1), PRIO(0, 1, 6), PRIO(0, 2, 6)])
    t.is_open()
    out.append(t)
    return out


def ext_datagram():
    out = []
    peer_dg = peer_small(max_datagram_frame_size=1200, zmux_datagram_flows=None)

    def F_(stream, ctx, payload):
        return P_flow(stream, ctx) + payload

    def P_flow(stream, ctx):
        from zmuxwire.preamble import encode_flow_prefix
        return encode_flow_prefix(stream, ctx)

    t = Trace("ext.datagram.opaque_without_flows", "Without flows, datagram data is opaque",
              ["EXTENSIONS.md#6.1"], profile="zmux-small-dgram")
    t.handshake(peer_small(max_datagram_frame_size=1200), features=BASE_FEATURES)
    t.send(0, [DATAGRAM(b"hello")])
    t.api("recv_datagram", expect={"data": b"hello"})
    out.append(t)

    t = Trace("ext.datagram.not_advertised", "A DATAGRAM without local support is fatal",
              ["EXTENSIONS.md#6.1"])
    t.handshake(peer_small())
    t.send(0, [DATAGRAM(b"x")])
    t.closed("PROTOCOL_VIOLATION")
    out.append(t)

    t = Trace("ext.datagram.too_large", "A DATAGRAM larger than advertised is fatal",
              ["EXTENSIONS.md#6.1"], profile="zmux-small-dgram")
    t.handshake(peer_dg, features=BASE_FEATURES + ["datagram_flows"])
    t.send(0, [DATAGRAM(b"\x00" + b"x" * 1200)])
    t.closed("PROTOCOL_VIOLATION")
    out.append(t)

    t = Trace("ext.datagram.flow_associated", "A flow prefix associates a datagram with a stream",
              ["EXTENSIONS.md#6.3"], profile="zmux-small-dgram")
    t.handshake(peer_dg, features=BASE_FEATURES + ["datagram_flows"])
    t.send(0, [STREAM(0, pre(open_info=b"8.8.8.8:53"))], [DATAGRAM(F_(0, 0, b"query"))],
           [DATAGRAM(F_(0, 7, b"other-context"))], [DATAGRAM(F_(None, 0, b"session-wide"))])
    t.api("accept_bidi", bind="s", expect={"stream": 0, "open_info": b"8.8.8.8:53"})
    t.note("Stream datagrams go only to the stream's queue; the session queue gets Flow 0.")
    t.api("recv_datagram", expect={"data": b"session-wide"})
    t.api("recv_datagram", expect={"pending": True})
    t.api("recv_datagram", stream="s", expect={"data": b"query", "context": 0})
    t.api("recv_datagram", stream="s", expect={"data": b"other-context", "context": 7})
    t.api("recv_datagram", stream="s", expect={"pending": True})
    out.append(t)

    for name, title, payload in [
        ("flow_unidirectional", "A flow naming a unidirectional stream is fatal", F_(2, 0, b"x")),
        ("flow_unopened_local", "A flow naming an unopened local stream is fatal", F_(1, 0, b"x")),
        ("flow_beyond_limit", "A flow naming a stream beyond the limit is fatal", F_(16, 0, b"x")),
        ("flow_prefix_truncated", "A datagram without a complete flow prefix is fatal", b""),
    ]:
        t = Trace(f"ext.datagram.{name}", title, ["EXTENSIONS.md#6.3", "EXTENSIONS.md#6.4"],
                  profile="zmux-small-dgram")
        t.handshake(peer_dg, features=BASE_FEATURES + ["datagram_flows"])
        t.send(0, [DATAGRAM(payload)])
        t.closed("PROTOCOL_VIOLATION")
        out.append(t)

    t = Trace("ext.datagram.flow_closed_stream_dropped",
              "Datagrams for a finished stream are dropped silently",
              ["EXTENSIONS.md#6.4"], profile="zmux-small-dgram")
    t.handshake(peer_dg, features=BASE_FEATURES + ["datagram_flows"])
    t.send(0, [STREAM(0, EMPTY_PRE + b"q", fin=True)])
    t.api("accept_bidi", bind="s", expect={"stream": 0})
    t.api("read", stream="s", max=10, expect={"data": b"q", "fin": True})
    t.api("finish", stream="s", expect={"ok": True})
    t.frames(0, STREAM(0, b"", fin=True))
    t.api("release", stream="s", expect={"ok": True})
    t.send(0, [DATAGRAM(F_(0, 0, b"late"))])
    t.api("recv_datagram", expect={"pending": True})
    t.is_open()
    out.append(t)

    t = Trace("ext.datagram.send_too_large", "A datagram that does not fit is refused locally",
              ["EXTENSIONS.md#6.1"], profile="zmux-small-dgram")
    t.handshake(peer_dg, features=BASE_FEATURES + ["datagram_flows"])
    t.api("send_datagram", data=b"y" * 2000, expect=err("too_large"))
    t.no_frames(0, ["DATAGRAM"])
    out.append(t)

    t = Trace("ext.datagram.send_with_flow", "Sent datagrams carry the flow prefix",
              ["EXTENSIONS.md#6.3"], profile="zmux-small-dgram")
    t.handshake(peer_dg, features=BASE_FEATURES + ["datagram_flows"])
    t.send(0, [STREAM(0, EMPTY_PRE + b"x")])
    t.api("accept_bidi", bind="s", expect={"stream": 0})
    t.api("send_datagram", data=b"abc", stream="s", context=0, expect={"ok": True})
    t.frames(0, {"type": "DATAGRAM", "data": F_(0, 0, b"abc")})
    out.append(t)

    t = Trace("ext.datagram.send_queue_drops_oldest",
              "Datagrams wait in a 64-entry send queue while the pipe is not writable; the oldest "
              "is dropped, and sending never blocks", ["EXTENSIONS.md#6.5"],
              profile="zmux-small-dgram")
    t.handshake(peer_small(max_datagram_frame_size=1200), features=BASE_FEATURES)
    t.writable(0, False)
    for i in range(65):
        t.api("send_datagram", data=b"%02d" % i, expect={"ok": True})
    t.writable(0, True)
    t.frames(0, {"type": "DATAGRAM", "count": 64}, {"type": "DATAGRAM", "data": b"00", "count": 0},
             {"type": "DATAGRAM", "data": b"01"}, {"type": "DATAGRAM", "data": b"64"},
             match="unordered")
    out.append(t)

    t = Trace("ext.datagram.receive_queue_overflow",
              "A full 128-entry session receive queue drops its oldest datagram and counts it, "
              "never an error", ["EXTENSIONS.md#6.5"], profile="zmux-small-dgram")
    t.handshake(peer_small(max_datagram_frame_size=1200), features=BASE_FEATURES)
    t.send(0, [DATAGRAM(b"%03d" % i) for i in range(129)])
    t.event("datagram_dropped", count=1)
    for i in range(1, 129):
        t.api("recv_datagram", expect={"data": b"%03d" % i})
    t.api("recv_datagram", expect={"pending": True})
    t.is_open()
    out.append(t)

    t = Trace("ext.datagram.stream_queue_overflow",
              "A stream's 32-entry datagram queue drops its oldest datagram; stream datagrams "
              "never spill into the session queue", ["EXTENSIONS.md#6.5"],
              profile="zmux-small-dgram")
    t.handshake(peer_dg, features=BASE_FEATURES + ["datagram_flows"])
    t.send(0, [STREAM(0, EMPTY_PRE + b"x")], [DATAGRAM(F_(0, 0, b"%02d" % i)) for i in range(33)])
    t.api("accept_bidi", bind="s", expect={"stream": 0})
    t.event("datagram_dropped", count=1)
    for i in range(1, 33):
        t.api("recv_datagram", stream="s", expect={"data": b"%02d" % i, "context": 0})
    t.api("recv_datagram", stream="s", expect={"pending": True})
    t.api("recv_datagram", expect={"pending": True})
    t.is_open()
    out.append(t)
    return out


def core_reset_stream_at():
    out = []
    t = Trace("core.reset_stream_at.reliable_above_final",
              "RESET_STREAM_AT with Reliable Size above Final Size is fatal", ["SPEC.md#4.3"])
    t.handshake(peer_small())
    t.raw(0, F.encode_record([RAW(V(0x24) + V(0) + V(0) + V(4) + V(5))]))
    t.closed("FRAME_ENCODING_ERROR")
    out.append(t)

    t = Trace("core.reset_stream_at.code_change",
              "A second reset with a different error code is fatal", ["SPEC.md#7.5"])
    t.handshake(peer_small())
    t.send(0, [STREAM(0, EMPTY_PRE + b"abcdef"), RESET_AT(0, 4, 7, 4), RESET_AT(0, 5, 7, 3)])
    t.closed("STREAM_STATE_ERROR")
    out.append(t)

    t = Trace("core.reset_stream_at.reliable_size_reduction",
              "Reducing the Reliable Size is allowed", ["SPEC.md#7.5"])
    t.handshake(peer_small())
    t.send(0, [STREAM(0, EMPTY_PRE + b"abcdef"), RESET_AT(0, 4, 7, 7), RESET_AT(0, 4, 7, 4)])
    t.api("accept_bidi", expect={"stream": 0})
    t.is_open()
    out.append(t)
    return out


# =============================================================================
# compat.qmux

def core_pipe_ack():
    out = []
    P0 = "MULTIPATH.md#3.5"

    t = Trace("core.pipe_ack.delay", "With pipe acknowledgement, received ack-eliciting bytes "
              "are acknowledged within zmux_max_ack_delay (25 ms)", [P0, "MULTIPATH.md#6.3"])
    t.handshake(peer_ack(), features=ACK_FEATURES)
    t.send(0, [STREAM(0, EMPTY_PRE + b"hello")])
    t.note("Five bytes need no credit update, so no record carries the PIPE_ACK earlier; "
           "it may still come earlier (an endpoint may acknowledge more often).")
    t.adv(25)
    t.frames(0, PIPE_ACK((0, t.peer_sent[0], "*")), match="unordered")
    t.adv(100)
    t.no_frames(0, ["PIPE_ACK", "PIPE_PING"])
    t.is_open()
    out.append(t)

    t = Trace("core.pipe_ack.threshold", "With pipe acknowledgement, the endpoint's own "
              "zmux_ack_threshold of ack-eliciting bytes triggers a PIPE_ACK at once",
              [P0, "MULTIPATH.md#6.3"], overrides={"tp": {"zmux_ack_threshold": 1024}})
    t.handshake(peer_ack(), features=ACK_FEATURES)
    t.send(0, [STREAM(0, EMPTY_PRE + b"a" * 1500)])
    t.frames(0, PIPE_ACK((0, t.peer_sent[0], "*")), match="unordered")
    t.is_open()
    out.append(t)

    t = Trace("core.pipe_ack.peer_acks", "A PIPE_ACK for pipe 0 up to the bytes sent is "
              "accepted and changes nothing visible; nothing is retained or re-sent", [P0])
    t.handshake(peer_ack(), features=ACK_FEATURES)
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=b"x" * 100, fin=False, expect={"accepted": 100})
    t.frames(0, SDATA(1, LOCAL_FIRST_PRE + b"x" * 100))
    t.send(0, [PIPE_ACK((0, SENT(0), 0))])
    t.send(0, [PIPE_ACK((0, SENT(0), 0))])
    t.adv(1000)
    t.no_frames(0, ["STREAM", "PIPE_PING"])
    t.is_open()
    out.append(t)

    t = Trace("core.pipe_ack.other_pipe", "With pipe acknowledgement, a PIPE_ACK entry for any "
              "pipe but 0 is a protocol violation", [P0])
    t.handshake(peer_ack(), features=ACK_FEATURES)
    t.send(0, [PIPE_ACK((0, SENT(0), 0), (2, 1, 0))], invalid=True)
    t.closed("PROTOCOL_VIOLATION")
    out.append(t)

    t = Trace("core.pipe_ack.ahead_of_sent", "With pipe acknowledgement, acknowledging bytes "
              "that were never sent is a protocol violation", [P0, "MULTIPATH.md#6.2"])
    t.handshake(peer_ack(), features=ACK_FEATURES)
    t.send(0, [PIPE_ACK((0, SENT(0, 1), 0))], invalid=True)
    t.closed("PROTOCOL_VIOLATION")
    out.append(t)

    t = Trace("core.pipe_ack.companion_parameters", "zmux_ack_threshold and "
              "zmux_max_ack_delay may accompany zmux_pipe_ack alone", [P0, "MULTIPATH.md#3.2"])
    t.handshake(peer_ack(zmux_ack_threshold=4096, zmux_max_ack_delay=10),
                features=ACK_FEATURES)
    t.is_open()
    out.append(t)

    t = Trace("core.pipe_ack.peer_without_parameter", "When the peer did not send "
              "zmux_pipe_ack, a PIPE_ACK from it is a frame of a feature not in use",
              [P0, "SPEC.md#4.1"])
    t.handshake(peer_small(), features=BASE_FEATURES)
    t.send(0, [PIPE_ACK((0, SENT(0), 0))], invalid=True)
    t.closed("FRAME_ENCODING_ERROR")
    out.append(t)

    t = Trace("core.pipe_ack.idle_timeout_sends_close", "With pipe acknowledgement, the idle "
              "timeout sends CONNECTION_CLOSE(ZMUX_IDLE_TIMEOUT) before the graceful shutdown",
              ["SPEC.md#6.4", P0, "SPEC.md#10.2"])
    t.handshake(peer_ack(max_idle_timeout=10000), features=ACK_FEATURES)
    t.adv(9999)
    t.is_open()
    t.adv(1)
    t.closed("IDLE_TIMEOUT", space="local")
    t.frames(0, CLOSE(TE("ZMUX_IDLE_TIMEOUT")))
    t.pipe_closed(0, "out", "graceful")
    out.append(t)

    t = Trace("core.pipe_ack.peer_idle_close", "With pipe acknowledgement, CONNECTION_CLOSE "
              "with ZMUX_IDLE_TIMEOUT is reported as the peer's idle timeout",
              ["SPEC.md#6.4", P0, "SPEC.md#10.2"])
    t.handshake(peer_ack(), features=ACK_FEATURES)
    t.send(0, [CLOSE(TE("ZMUX_IDLE_TIMEOUT"))])
    t.closed("IDLE_TIMEOUT", space="local", remote=True)
    out.append(t)

    t = Trace("core.pipe_ack.peer_shutdown_is_failure", "With pipe acknowledgement, a graceful "
              "end of the peer's pipe without CONNECTION_CLOSE is a pipe failure, not the "
              "peer's idle close", ["SPEC.md#6.4", "SPEC.md#6.6", P0])
    t.handshake(peer_ack(), features=ACK_FEATURES)
    t.down(0, "in", "eof")
    t.closed("PIPES_LOST", space="local", wire=False)
    out.append(t)

    t = Trace("core.pipe_ack.idle_counts_acknowledged_activity", "With pipe acknowledgement, "
              "the PIPE_ACK of a keep-alive's record restarts the idle and keep-alive timers, "
              "and a received record without application activity does not",
              ["SPEC.md#6.4", P0], overrides={"config": {"keep_alive_period": 5000}})
    t.handshake(peer_ack(max_idle_timeout=10000), features=ACK_FEATURES)
    t.adv(5000)
    t.frames(0, PING(0))
    t.note("The peer acknowledges the record that carried the request instead of answering "
           "it: the timers restart at 5000 ms.")
    t.send(0, [PIPE_ACK((0, SENT(0), 0))])
    t.adv(4999)
    t.no_frames(0, ["QX_PING"])
    t.is_open()
    t.adv(1)
    t.frames(0, PING(1))
    t.note("A record that carries only a credit update restarts nothing; without "
           "acknowledgement any received record would.")
    t.send(0, [MAX_DATA(16384)])
    t.adv(4999)
    t.is_open()
    t.adv(1)
    t.closed("IDLE_TIMEOUT", space="local")
    t.frames(0, CLOSE(TE("ZMUX_IDLE_TIMEOUT")))
    out.append(t)

    t = Trace("core.pipe_ack.not_with_multipath", "When multipath is in use, pipe "
              "acknowledgement is not: the session acknowledges by MULTIPATH.md Section 6",
              [P0, "SPEC.md#5.5"], profile="zmux-mp")
    mp_handshake(t, peer=peer_mp(zmux_pipe_ack=None))
    out.append(t)
    return out


# =============================================================================
# core.lanes: lanes of the pipe pair of a session with pipe acknowledgement

# Pipe 0 is a stream of carrier connection "q"; the peer opens its lanes as further streams
# of the same connection.
LANE_CONN = "q"
BOOT_CONN = {"conn": LANE_CONN}


def lanes_ready(t, pipe=0):
    """The local endpoint acknowledges lane 0 of the peer's pipe: from then on the peer may
    open lanes on it (MULTIPATH.md 5.8)."""
    t.adv(25)
    t.frames("*", PIPE_ACK((pipe, t.peer_sent[pipe], "*")), match="unordered")


def peer_lane(t, carrier, pipe, lane, key=D.LOCAL_LANE_KEY, conn=LANE_CONN):
    """The peer opens a lane: a new stream of ``conn`` whose first record holds only the
    PIPE_LANE (MULTIPATH.md 5.8)."""
    t.lane_up(carrier, conn)
    t.send(None, [PIPE_LANE(pipe, lane, key)], carrier=carrier)


def confirm_peer_lanes(t, *entries):
    """The local endpoint acknowledges the PIPE_LANE records within its acknowledgement
    delay: ``entries`` are the (pipe, lane, carrier or pipe key) positions it must list."""
    t.adv(25)
    t.frames("*", PIPE_ACK(*[(p, ln, t.peer_sent[k], "*") for p, ln, k in entries]),
             match="unordered")


def core_lanes():
    out = []
    P0, P8 = "MULTIPATH.md#2.5", "MULTIPATH.md#5.8"

    def ack_session(t, peer=None):
        t.handshake(peer or peer_ack(), features=ACK_FEATURES, **BOOT_CONN)

    def two_lanes(t):
        ack_session(t)
        lanes_ready(t)
        peer_lane(t, "l1", 0, 1)
        confirm_peer_lanes(t, (0, 0, 0), (0, 1, "l1"))

    t = Trace("core.lanes.peer_opens_lane", "The peer opens lane 1 of pipe 0 with a record "
              "holding only PIPE_LANE, which is acknowledged; the lane's records are then "
              "processed as the pipe's, and every PIPE_ACK lists lanes 0 and 1 with their own "
              "positions", [P0, P8, "MULTIPATH.md#3.5", "MULTIPATH.md#3.6", "MULTIPATH.md#6.2"])
    two_lanes(t)
    t.send(None, [STREAM(0, EMPTY_PRE + b"on-lane-1", fin=True)], carrier="l1")
    t.api("accept_bidi", bind="s", expect={"stream": 0})
    t.api("read", stream="s", max=20, expect={"data": b"on-lane-1", "fin": True})
    t.adv(25)
    t.frames(0, PIPE_ACK((0, 0, t.peer_sent[0], "*"), (0, 1, t.peer_sent["l1"], "*")),
             match="unordered")
    t.no_frames("*", ["PIPE_LANE"])
    t.is_open()
    out.append(t)

    t = Trace("core.lanes.head_of_line", "A record held incomplete on lane 1 does not hold "
              "back lane 2: lane 2's records are processed and acknowledged, and lane 1's "
              "position stays at its last complete record until the rest arrives",
              [P0, "MULTIPATH.md#6.1", "SPEC.md#4.1"])
    ack_session(t)
    lanes_ready(t)
    peer_lane(t, "l1", 0, 1)
    peer_lane(t, "l2", 0, 2)
    confirm_peer_lanes(t, (0, 0, 0), (0, 1, "l1"), (0, 2, "l2"))
    t.send(None, [STREAM(0, EMPTY_PRE + b"one")], carrier="l1")
    complete = t.peer_sent["l1"]
    held = F.encode_record([STREAM(0, b"-held", off=len(EMPTY_PRE) + 3, fin=True)])
    t.raw(None, held[:-2], carrier="l1")
    t.send(None, [STREAM(4, EMPTY_PRE + b"two", fin=True)], carrier="l2")
    t.api("accept_bidi", bind="s0", expect={"stream": 0})
    t.api("accept_bidi", bind="s4", expect={"stream": 4})
    t.api("read", stream="s4", max=10, expect={"data": b"two", "fin": True})
    t.api("read", stream="s0", max=10, expect={"data": b"one", "fin": False})
    t.api("read", stream="s0", max=10, expect={"pending": True})
    t.adv(25)
    t.frames(0, PIPE_ACK((0, 0, t.peer_sent[0], "*"), (0, 1, complete, "*"),
                         (0, 2, t.peer_sent["l2"], "*")), match="unordered")
    t.raw(None, held[-2:], carrier="l1")
    t.api("read", stream="s0", max=10, expect={"data": b"-held", "fin": True})
    t.adv(25)
    t.frames(0, PIPE_ACK((0, 0, t.peer_sent[0], "*"), (0, 1, t.peer_sent["l1"], "*"),
                         (0, 2, t.peer_sent["l2"], "*")), match="unordered")
    out.append(t)

    def local_lane(t):
        """The local endpoint opens lane 1 once lane 0 has an acknowledged position; data
        stays on lane 0 until a PIPE_ACK confirms the lane, then moves to it. All RTT samples
        are 0 ms."""
        ack_session(t, peer_ack(zmux_max_lanes=lanes(2)))
        t.api("open_bidi", bind="a", expect={"ok": True})
        t.api("write", stream="a", data=b"first", fin=False, expect={"accepted": 5})
        t.frames(0, SDATA(1, LOCAL_FIRST_PRE + b"first"))
        t.no_frames("*", ["PIPE_LANE"])
        t.send(0, [PIPE_ACK((0, SENT(0), 0))])
        t.api("write", stream="a", data=b"second", fin=False, expect={"accepted": 6})
        t.frames(0, PIPE_LANE(0, 1, D.PEER_LANE_KEY), lane=1, match="first")
        t.frames(0, SDATA(1, b"second", off=len(LOCAL_FIRST_PRE) + 5))
        t.no_frames(0, lane=1)

    def confirm_local_lane(t):
        t.send(0, [PIPE_ACK((0, 0, SENT(0), 0), (0, 1, SENT(0, lane=1), 0))])
        t.api("write", stream="a", data=b"third", fin=False, expect={"accepted": 5})
        t.frames(0, SDATA(1, b"third", off=len(LOCAL_FIRST_PRE) + 11), lane=1)
        t.no_frames(0, ["STREAM"])

    t = Trace("core.lanes.local_opens_after_ack", "The local endpoint opens a lane only once a "
              "PIPE_ACK has acknowledged a position above 0 on lane 0; the lane's first record "
              "holds only PIPE_LANE with the peer's Lane Key, and stream data moves to the lane "
              "only once a PIPE_ACK confirms it and covers the data on lane 0",
              [P8, "MULTIPATH.md#3.6", "STATE_MACHINE.md#3.3"])
    local_lane(t)
    confirm_local_lane(t)
    t.send(0, [PIPE_ACK((0, 0, SENT(0), 0), (0, 1, SENT(0, lane=1), 0))])
    t.is_open()
    out.append(t)

    t = Trace("core.lanes.local_lane_refused", "A lane that the peer closes before it is "
              "confirmed is refused: the pipe does not fail, data stays on lane 0, and no "
              "further lane is opened on the pipe", [P8, "STATE_MACHINE.md#3.3"])
    local_lane(t)
    t.down(0, lane=1, dir="out", how="error")
    t.is_open()
    t.send(0, [PIPE_ACK((0, SENT(0), 0))])
    t.api("write", stream="a", data=b"third", fin=False, expect={"accepted": 5})
    t.api("open_bidi", bind="b", expect={"ok": True})
    t.api("write", stream="b", data=b"b", fin=False, expect={"accepted": 1})
    t.frames(0, SDATA(1, b"third", off=len(LOCAL_FIRST_PRE) + 11), SDATA(5, EMPTY_PRE + b"b"),
             match="unordered")
    t.no_frames("*", ["PIPE_LANE", "CONNECTION_CLOSE"])
    t.is_open()
    out.append(t)

    t = Trace("core.lanes.late_ack_after_refusal", "An acknowledgement that arrives for a "
              "lane after it was refused is ignored: no error, and the lane stays unused",
              [P8, "STATE_MACHINE.md#3.3"])
    local_lane(t)
    t.down(0, lane=1, dir="out", how="error")
    t.send(0, [PIPE_ACK((0, 0, SENT(0), 0), (0, 1, SENT(0, lane=1), 0))])
    t.is_open()
    t.api("write", stream="a", data=b"third", fin=False, expect={"accepted": 5})
    t.frames(0, SDATA(1, b"third", off=len(LOCAL_FIRST_PRE) + 11))
    t.no_frames(0, lane=1)
    t.no_frames("*", ["PIPE_LANE", "CONNECTION_CLOSE"])
    t.is_open()
    out.append(t)

    t = Trace("core.lanes.local_lane_unconfirmed_timeout", "A lane that is not confirmed "
              "within the handshake timeout (10 s) is reset by the local endpoint and refused: "
              "the pipe does not fail and no further lane is opened", [P8, "STATE_MACHINE.md#3.3"])
    local_lane(t)
    t.send(0, [PIPE_ACK((0, SENT(0), 0))])
    t.note("The lane was opened at 0 ms; the peer never acknowledges it.")
    t.adv(9999)
    t.is_open()
    t.adv(1)
    t.pipe_closed(0, "out", "abort", lane=1)
    t.api("write", stream="a", data=b"third", fin=False, expect={"accepted": 5})
    t.frames(0, SDATA(1, b"third", off=len(LOCAL_FIRST_PRE) + 11))
    t.no_frames("*", ["PIPE_LANE", "CONNECTION_CLOSE"])
    t.is_open()
    out.append(t)

    t = Trace("core.lanes.local_lane_failure_ends_session", "An error on a confirmed lane of "
              "the local endpoint fails the pipe, which without multipath ends the session",
              [P8, "SPEC.md#6.6", "STATE_MACHINE.md#3.3"])
    local_lane(t)
    confirm_local_lane(t)
    t.down(0, lane=1, dir="out", how="error")
    t.closed("PIPES_LOST", space="local", wire=False)
    out.append(t)

    t = Trace("core.lanes.ack_unopened_lane", "A PIPE_ACK entry for a lane the receiver never "
              "opened is ignored with Position 0 and a protocol violation otherwise",
              ["MULTIPATH.md#6.2", "MULTIPATH.md#3.5"])
    ack_session(t, peer_ack(zmux_max_lanes=lanes(2)))
    t.send(0, [PIPE_ACK((0, 0, SENT(0), 0), (0, 1, 0, 0))])
    t.is_open()
    t.send(0, [PIPE_ACK((0, 0, SENT(0), 0), (0, 1, 1, 0))], invalid=True)
    t.closed("PROTOCOL_VIOLATION")
    out.append(t)

    for name, title, entries in [
        ("ack_lanes_decreasing", "PIPE_ACK entries for lanes 1 then 0 of a pipe are out of "
         "order", [(0, 1, 0, 0), (0, 0, 0, 0)]),
        ("ack_lane_repeated", "A PIPE_ACK entry repeating (Pipe ID, Lane) is out of order",
         [(0, 0, 0, 0), (0, 1, 0, 0), (0, 1, 0, 0)]),
    ]:
        t = Trace(f"core.lanes.{name}", title + ": a FRAME_ENCODING_ERROR", ["MULTIPATH.md#6.2"])
        ack_session(t, peer_ack(zmux_max_lanes=lanes(2)))
        t.send(0, [PIPE_ACK(*entries)], invalid=True)
        t.closed("FRAME_ENCODING_ERROR")
        out.append(t)

    for name, title, lane, setup, overrides, code in [
        ("lane_zero", "A PIPE_LANE opening lane 0 is a protocol violation", 0, None, None,
         "PROTOCOL_VIOLATION"),
        ("lane_beyond_max", "A PIPE_LANE whose Lane is the receiver's Max Lanes (4) or more "
         "is a protocol violation", 4, None, None, "PROTOCOL_VIOLATION"),
        ("lane_repeated", "A second PIPE_LANE for a lane already open on the pipe is a "
         "protocol violation", 1, 1, None, "PROTOCOL_VIOLATION"),
        ("not_available", "A lane toward an endpoint that announced Max Lanes 1 is a frame of "
         "a feature not in use", 1, None,
         {"tp": {"zmux_max_lanes": lanes(1, D.LOCAL_LANE_KEY)}}, "FRAME_ENCODING_ERROR"),
    ]:
        refs = [P8, "MULTIPATH.md#3.6"] + (["SPEC.md#4.1"] if code != "PROTOCOL_VIOLATION"
                                           else [])
        t = Trace(f"core.lanes.{name}", title, refs, overrides=overrides)
        ack_session(t)
        lanes_ready(t)
        if setup is not None:
            peer_lane(t, "l1", 0, setup)
        peer_lane(t, "lx", 0, lane)
        t.closed(code)
        out.append(t)

    t = Trace("core.lanes.first_record_extra_frame", "A lane whose first record holds a frame "
              "other than PIPE_LANE and PADDING is a protocol violation", [P8, "SPEC.md#4.2"])
    ack_session(t)
    lanes_ready(t)
    t.lane_up("l1", LANE_CONN)
    t.send(None, [PIPE_LANE(0, 1), PADDING(2), STREAM(0, EMPTY_PRE + b"early")], carrier="l1")
    t.closed("PROTOCOL_VIOLATION")
    out.append(t)

    t = Trace("core.lanes.peer_lane_ends_unused", "A peer's lane that ends, even with an error, "
              "before any record after its first has arrived is no event of the pipe: the "
              "session goes on", [P8, "STATE_MACHINE.md#3.3"])
    two_lanes(t)
    t.down(carrier="l1", dir="in", how="error")
    t.is_open()
    t.send(0, [STREAM(0, EMPTY_PRE + b"still-here", fin=True)])
    t.api("accept_bidi", bind="s", expect={"stream": 0})
    t.api("read", stream="s", max=20, expect={"data": b"still-here", "fin": True})
    t.no_frames("*", ["CONNECTION_CLOSE"])
    t.is_open()
    out.append(t)

    t = Trace("core.lanes.pipe_lane_on_lane_zero", "PIPE_LANE on a pipe's lane 0 is a "
              "protocol violation", [P8])
    ack_session(t)
    t.send(0, [PIPE_LANE(0, 1)])
    t.closed("PROTOCOL_VIOLATION")
    out.append(t)

    t = Trace("core.lanes.pipe_lane_not_first", "PIPE_LANE after the first frame of a lane is a "
              "protocol violation", [P8])
    two_lanes(t)
    t.send(None, [PIPE_LANE(0, 2)], carrier="l1")
    t.closed("PROTOCOL_VIOLATION")
    out.append(t)

    t = Trace("core.lanes.pipe_lane_truncated", "A PIPE_LANE that does not decode is a "
              "FRAME_ENCODING_ERROR, which takes precedence over its placement on lane 0",
              [P8, "SPEC.md#4.1"])
    ack_session(t)
    t.send(0, [RAW(F.encode_frame(PIPE_LANE(0, 1))[:-1])])
    t.closed("FRAME_ENCODING_ERROR")
    out.append(t)

    t = Trace("core.lanes.unknown_lane_key", "A lane whose Lane Key is not the session's, or "
              "that names a pipe the session does not have, is closed and nothing else "
              "happens", [P8, "MULTIPATH.md#5.5", "STATE_MACHINE.md#3.3"])
    ack_session(t)
    lanes_ready(t)
    peer_lane(t, "lx", 0, 1, key=D.PEER_LANE_KEY)
    t.reject_carrier("lx")
    peer_lane(t, "ly", 2, 1)
    t.reject_carrier("ly")
    t.is_open()
    peer_lane(t, "l1", 0, 1)
    confirm_peer_lanes(t, (0, 0, 0), (0, 1, "l1"))
    t.is_open()
    out.append(t)

    t = Trace("core.lanes.lane_failure_ends_session", "An error on a confirmed lane of the "
              "peer fails the pipe, which without multipath ends the session",
              [P0, "SPEC.md#6.6", "STATE_MACHINE.md#3.4"])
    two_lanes(t)
    t.send(None, [STREAM(0, EMPTY_PRE + b"x")], carrier="l1")
    t.down(carrier="l1", dir="in", how="error")
    t.closed("PIPES_LOST", space="local", wire=False)
    out.append(t)

    t = Trace("core.lanes.graceful_end_every_lane", "Without multipath, the peer's pipe ends "
              "only when every lane has ended at a record boundary, in any order; with pipe "
              "acknowledgement and no CONNECTION_CLOSE, that end is a pipe failure, not an idle "
              "close", [P0, "SPEC.md#6.4", "SPEC.md#6.6"])
    two_lanes(t)
    t.send(None, [STREAM(0, EMPTY_PRE + b"x", fin=True)], carrier="l1")
    t.down(carrier="l1", dir="in", how="eof")
    t.is_open()
    t.down(0, "in", "eof")
    t.closed("PIPES_LOST", space="local", wire=False)
    out.append(t)

    def lane_data_unacknowledged(t):
        """The local endpoint has written stream data on its confirmed lane 1 that no
        PIPE_ACK covers. With an initial smoothed RTT of 50 ms and RTT samples of 0 ms, the
        linger timeout is min(2 s, max(250 ms, 3 x SRTT)) = 250 ms whichever way the samples
        are folded in."""
        local_lane(t)
        confirm_local_lane(t)

    t = Trace("core.lanes.close_waits_for_lane_ack", "Without multipath, an application "
              "close goes on lane 0 only once a PIPE_ACK covers every record written on the "
              "other lanes", ["SPEC.md#6.5", P8], overrides={"test": {"rtt_ms": 50}})
    lane_data_unacknowledged(t)
    t.api("close", code=0, reason=b"", app=True, expect={"ok": True})
    t.no_frames("*", ["CONNECTION_CLOSE"])
    t.send(0, [PIPE_ACK((0, 0, SENT(0), 0), (0, 1, SENT(0, lane=1), 0))])
    t.frames(0, CLOSE(0, app=True))
    t.no_frames(0, ["CONNECTION_CLOSE"], lane=1)
    t.pipe_closed(0, "out", "graceful")
    t.closed(0, space="application", remote=False)
    out.append(t)

    t = Trace("core.lanes.close_after_linger_without_lane_ack", "Without multipath, a close "
              "waiting for the acknowledgement of the other lanes goes on lane 0 when the "
              "linger timeout (250 ms) has passed", ["SPEC.md#6.5", P8],
              overrides={"test": {"rtt_ms": 50}})
    lane_data_unacknowledged(t)
    t.api("close", code=0, reason=b"", app=True, expect={"ok": True})
    t.adv(249)
    t.no_frames("*", ["CONNECTION_CLOSE"])
    t.adv(1)
    t.frames(0, CLOSE(0, app=True))
    t.pipe_closed(0, "out", "graceful")
    t.closed(0, space="application", remote=False)
    out.append(t)
    return out


def compat():
    out = []
    plain = peer_plain()
    t = Trace("compat.qmux.plain_peer_basic",
              "Towards a plain QMux peer: no preamble, no zmux frames, QMux STREAM forms accepted",
              ["SPEC.md#13.2"])
    t.up(0, "bidi", via="bootstrap")
    local_tp_first(t)
    params = tp_list(plain, grease=None) + [{"id": 0x3D4F9C2A8B1E6075, "raw": V(7) + b"qmux-02"}]
    t.send(0, [TP(params)])
    t.event("established", features=[], role="responder")
    t.send(0, [STREAM(0, b"hello", fin=True, has_off=True, has_len=True), PING(0)])
    t.api("accept_bidi", bind="s", expect={"stream": 0})
    t.api("read", stream="s", max=10, expect={"data": b"hello", "fin": True})
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=b"hi", fin=True, expect={"accepted": 2})
    t.frames(0, PONG(0), SDATA(1, b"hi", fin=True), match="unordered")
    t.no_frames("*", ["GOAWAY", "PRIORITY_UPDATE", "PIPE_ACK", "PIPE_PING", "PIPE_JOIN",
                      "PIPE_LANE", "RESET_STREAM_AT", "PIPE_STATUS", "PIPE_ABANDON"])
    out.append(t)

    t = Trace("compat.qmux.open_info_unsupported",
              "Open info needs the preamble, which a plain QMux peer does not support",
              ["SPEC.md#13.2"])
    t.handshake(plain, grease=None, features=[])
    t.api("open_bidi", bind="a", open_info=b"x", expect=err("unsupported"))
    out.append(t)

    t = Trace("compat.qmux.shutdown_without_goaway",
              "Graceful shutdown towards a plain QMux peer closes without GOAWAY",
              ["SPEC.md#13.2", "SPEC.md#6.7"])
    t.handshake(plain, grease=None, features=[])
    t.api("shutdown", code=0, expect={"ok": True})
    t.frames(0, CLOSE(0, app=True), match="contains")
    t.no_frames("*", ["GOAWAY"])
    t.pipe_closed(0, "out", "graceful")
    t.closed(0, space="application", remote=False)
    out.append(t)

    t = Trace("compat.qmux.close_with_frame_type_zero",
              "CONNECTION_CLOSE 0x1c with Frame Type 0, as some QMux implementations send it",
              ["SPEC.md#13.2"])
    t.handshake(plain, grease=None, features=[])
    t.send(0, [CLOSE(0, frame_type=0)])
    t.closed("NO_ERROR", remote=True)
    out.append(t)

    t = Trace("compat.qmux.no_pipe_ack", "A plain QMux peer ignores zmux_pipe_ack and never "
              "receives a PIPE_ACK", ["MULTIPATH.md#3.5", "SPEC.md#13.2"])
    t.handshake(plain, grease=None, features=[])
    t.send(0, [STREAM(0, b"hello", has_off=True, has_len=True)])
    t.adv(100)
    t.no_frames("*", ["PIPE_ACK"])
    t.is_open()
    out.append(t)

    t = Trace("compat.qmux.close_reason_with_details",
              "A Reason Phrase with a diagnostic line, well-formed or not, is only reported: "
              "the close is processed as any other", ["SPEC.md#4.3"])
    t.handshake(plain, grease=None, features=[])
    t.send(0, [CLOSE(TE("FLOW_CONTROL_ERROR"), frame_type=0x08,
                     reason=b"stream data beyond the limit\nstream=0 offset=2048 limit=2048 x=")])
    t.closed("FLOW_CONTROL_ERROR", remote=True)
    out.append(t)

    t = Trace("compat.qmux.in_order_enforced", "QMux-02 Section 4.1 applies to plain peers",
              ["SPEC.md#7.3", "SPEC.md#13.2"])
    t.handshake(plain, grease=None, features=[])
    t.send(0, [STREAM(0, b"abc")], [STREAM(0, b"x", off=9)])
    t.closed("PROTOCOL_VIOLATION")
    out.append(t)

    t = Trace("compat.qmux.reset_without_reset_stream_at",
              "Without the peer's reset_stream_at, resets are plain RESET_STREAM",
              ["SPEC.md#13.2"])
    t.handshake(plain, grease=None, features=[])
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=b"abc", fin=False, expect={"accepted": 3})
    t.frames(0, SDATA(1, b"abc"))
    t.api("reset", stream="a", code=7, expect={"ok": True})
    t.frames(0, RESET(1, 7, 3))
    t.no_frames("*", ["RESET_STREAM_AT"])
    out.append(t)

    t = Trace("compat.qmux.implicit_stream_offered_at_once",
              "Without the preamble a stream opened implicitly by a higher one is offered at "
              "once, as in QUIC", ["SPEC.md#7.2"])
    t.handshake(plain, grease=None, features=[])
    t.send(0, [STREAM(4, b"second", fin=True)])
    t.api("accept_bidi", bind="s0", expect={"stream": 0})
    t.api("accept_bidi", bind="s4", expect={"stream": 4})
    t.api("read", stream="s0", max=10, expect={"pending": True})
    t.send(0, [STREAM(0, b"first", fin=True)])
    t.api("read", stream="s0", max=10, expect={"data": b"first", "fin": True})
    out.append(t)

    t = Trace("compat.qmux.no_multipath_with_plain_peer",
              "A multipath-enabled endpoint stays single-pipe with a plain QMux peer",
              ["SPEC.md#13.2", "SPEC.md#9.6"], profile="zmux-mp")
    t.handshake(plain, grease=None, features=[])
    t.api("add_pipe", pipe=1, dir="out", fate_group=0, expect=err("unsupported"))
    t.send(0, [STREAM(0, b"z" * 2000)])
    t.adv(100)
    t.no_frames("*", ["PIPE_ACK", "PIPE_PING", "PIPE_JOIN"])
    out.append(t)
    return out


# =============================================================================
# multipath

def mp_handshake(t, peer=None, features=MP_FEATURES, **carrier):
    t.handshake(peer or peer_mp(), features=features, **carrier)


def join_up(t, carrier, pipe, direction="up", extra=(), via="listener", fate_group=0,
            confirm=True, priority=0, standby=False, **carrier_stream):
    t.up(carrier=carrier, dir={"up": "in", "down": "out", "bidi": "bidi"}[direction],
         via=via, **carrier_stream)
    rec = [[join(pipe, direction, fate_group=fate_group, priority=priority,
                 standby=standby)] + list(extra)]
    t._add(op="peer_send", carrier=carrier, records=rec, hex=D.record_bytes(rec))
    t.peer_sent[pipe] = t.peer_sent.get(pipe, 0) + len(D.record_bytes(rec))
    if direction != "up" and confirm:
        # The acceptor's first record on a down or bidi attachment carries a complete
        # PIPE_ACK snapshot and a PIPE_PING within its max_ack_delay (MULTIPATH.md 5.4
        # step 8); the peer acknowledges it.
        t.adv(25)
        t.frames(pipe, PIPE_PING())
        t.send(pipe if direction == "bidi" else 0, [PIPE_ACK((pipe, SENT(pipe), 0))])


def multipath():
    out = []
    P = "zmux-mp"

    t = Trace("multipath.negotiation.ack_within_max_ack_delay",
              "Multipath is in use and data is acknowledged within max_ack_delay",
              ["MULTIPATH.md#3.3", "MULTIPATH.md#6.3"], profile=P)
    mp_handshake(t)
    t.send(0, [STREAM(0, EMPTY_PRE + b"hello")])
    t.adv(25)
    t.frames(0, PIPE_ACK((0, t.peer_sent[0], "*")))
    out.append(t)

    t = Trace("multipath.negotiation.one_sided",
              "With only one zmux_multipath parameter, PIPE_ACK is an unknown frame",
              ["MULTIPATH.md#3.3"], profile=P)
    t.handshake(peer_small(), features=BASE_FEATURES)
    t.send(0, [PIPE_ACK((0, 0, 0))])
    t.closed("FRAME_ENCODING_ERROR")
    out.append(t)

    t = Trace("multipath.negotiation.no_secret_source",
              "Without a common secret source multipath is not in use",
              ["MULTIPATH.md#10.1"], profile=P)
    t.handshake(peer_mp(zmux_multipath={"flags": 0x08, "nonce": D.PEER_NONCE,
                                        "locator": D.PEER_LOCATOR}), features=BASE_FEATURES)
    t.send(0, [PIPE_PING()])
    t.closed("FRAME_ENCODING_ERROR")
    out.append(t)

    t = Trace("multipath.negotiation.max_lanes_feature_not_in_use",
              "zmux_max_lanes sent with zmux_multipath is valid although multipath ends up not "
              "in use (no common secret source) and pipe acknowledgement is not announced; "
              "lanes are then not available and a PIPE_LANE is a frame of a feature not in use",
              ["SPEC.md#5.5", "MULTIPATH.md#3.6", "MULTIPATH.md#5.8", "SPEC.md#4.1"], profile=P)
    t.handshake(peer_mp(zmux_multipath={"flags": 0x08, "nonce": D.PEER_NONCE,
                                        "locator": D.PEER_LOCATOR},
                        zmux_max_lanes=lanes(4)), features=BASE_FEATURES, **BOOT_CONN)
    t.is_open()
    peer_lane(t, "l1", 0, 1)
    t.closed("FRAME_ENCODING_ERROR")
    out.append(t)

    t = Trace("multipath.join.inband_up_with_zero_rtt_data",
              "An authenticated in-band join carries data in its first record",
              ["MULTIPATH.md#5.2", "MULTIPATH.md#5.4"], profile=P)
    mp_handshake(t)
    join_up(t, "c2", 2, "up", [STREAM(0, EMPTY_PRE + b"zero-rtt", fin=True)])
    t.api("accept_bidi", bind="s", expect={"stream": 0})
    t.api("read", stream="s", max=20, expect={"data": b"zero-rtt", "fin": True})
    t.adv(25)
    t.frames(0, PIPE_ACK((0, t.peer_sent[0], "*"), (2, t.peer_sent[2], "*")))
    out.append(t)

    t = Trace("multipath.join.bad_token", "A join with a wrong token is closed silently",
              ["MULTIPATH.md#5.4", "MULTIPATH.md#10.5"], profile=P)
    mp_handshake(t)
    t.up(carrier="c2", dir="in", via="listener")
    j = join(2, "up", bad_token=True)
    t._add(op="peer_send", carrier="c2", records=[[j]], hex=D.record_bytes([[j]]))
    t.reject_carrier("c2")
    t.event("join_auth_failed", reason="mac")
    t.is_open()
    out.append(t)

    t = Trace("multipath.join.replay", "A replayed join for a used pipe ID is closed",
              ["MULTIPATH.md#5.4", "MULTIPATH.md#10.6"], profile=P)
    mp_handshake(t)
    join_up(t, "c2", 2, "up")
    t.up(carrier="c2-replay", dir="in", via="listener")
    j = join(2, "up")
    t._add(op="peer_send", carrier="c2-replay", records=[[j]], hex=D.record_bytes([[j]]))
    t.reject_carrier("c2-replay")
    t.is_open()
    out.append(t)

    for name, title, pipe, code in [
        ("wrong_parity", "An authenticated join with the other role's parity is fatal", 3,
         "PROTOCOL_VIOLATION"),
    ]:
        t = Trace(f"multipath.join.{name}", title, ["MULTIPATH.md#5.4"], profile=P)
        mp_handshake(t)
        t.up(carrier="cx", dir="in", via="listener")
        j = join(pipe, "up")
        t._add(op="peer_send", carrier="cx", records=[[j]], hex=D.record_bytes([[j]]))
        t.closed(code)
        out.append(t)

    t = Trace("multipath.join.unknown_locator", "A join for an unknown locator is closed",
              ["MULTIPATH.md#5.4"], profile=P)
    mp_handshake(t)
    t.up(carrier="cx", dir="in", via="listener")
    j = join(2, "up", locator=b"NOPE")
    t._add(op="peer_send", carrier="cx", records=[[j]], hex=D.record_bytes([[j]]))
    t.reject_carrier("cx")
    t.is_open()
    out.append(t)

    t = Trace("multipath.join.listener_rejects_garbage",
              "The listener rejects a carrier that is neither a session nor a join",
              ["MULTIPATH.md#5.5"], profile=P)
    mp_handshake(t)
    t.up(carrier="cx", dir="in", via="listener")
    t._add(op="peer_send", carrier="cx", hex=b"GET / HTTP/1.1\r\n")
    t.reject_carrier("cx")
    out.append(t)

    t = Trace("multipath.join.held_until_established",
              "A join that arrives before the joiner's parameters is held, then verified",
              ["MULTIPATH.md#5.4"], profile=P)
    t.up(0, "bidi", via="bootstrap")
    local_tp_first(t)
    t.up(carrier="c2", dir="in", via="listener")
    rec = [[join(2, "up"), STREAM(0, EMPTY_PRE + b"early", fin=True)]]
    t._add(op="peer_send", carrier="c2", records=rec, hex=D.record_bytes(rec))
    t.send(0, [TP(tp_list(peer_mp()))])
    t.event("established", features=MP_FEATURES)
    t.api("accept_bidi", bind="s", expect={"stream": 0})
    t.api("read", stream="s", max=10, expect={"data": b"early", "fin": True})
    out.append(t)

    t = Trace("multipath.join.out_of_band_down",
              "An out-of-band join for a downlink makes it usable at once",
              ["MULTIPATH.md#5.3"], profile=P)
    mp_handshake(t)
    t.up(carrier="c4", dir="out", via="listener",
         oob_join=F.encode_frame(join(4, "down")))
    t.writable(0, False)
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=b"via-4", fin=True, expect={"accepted": 5})
    t.frames(4, SDATA(1, LOCAL_FIRST_PRE + b"via-4", fin=True))
    out.append(t)

    t = Trace("multipath.join.join_not_first",
              "PIPE_JOIN anywhere but first on a joined pipe is fatal", ["MULTIPATH.md#5.2"],
              profile=P)
    mp_handshake(t)
    t.send(0, [join(2, "up")])
    t.closed("PROTOCOL_VIOLATION")
    out.append(t)

    t = Trace("multipath.join.parameters_on_joined_pipe",
              "QX_TRANSPORT_PARAMETERS on a joined pipe is fatal", ["SPEC.md#5.1"], profile=P)
    mp_handshake(t)
    join_up(t, "c2", 2, "up")
    t.send(2, [TP(tp_list(peer_mp()))])
    t.closed("PROTOCOL_VIOLATION")
    out.append(t)

    t = Trace("multipath.bootstrap.split_pairing",
              "A split bootstrap is paired by its token; a third carrier with the token is closed",
              ["SPEC.md#6.1.1"], profile=P)
    token = bytes(range(16))
    t.up(carrier="up0", dir="in", via="listener", pairing=token)
    t.up(carrier="down0", dir="out", via="listener", pairing=token)
    t._add(op="peer_send", carrier="up0", records=[[TP(tp_list(peer_mp()))]],
           hex=D.record_bytes([[TP(tp_list(peer_mp()))]]))
    t.peer_sent[0] = len(D.record_bytes([[TP(tp_list(peer_mp()))]]))
    t.frames(0, TP_ANY, match="first")
    t.event("established", features=MP_FEATURES)
    t.up(carrier="third", dir="in", via="listener", pairing=token)
    t.reject_carrier("third")
    t.is_open()
    out.append(t)

    t = Trace("multipath.bootstrap.split_fails_as_unit",
              "When one carrier of a split bootstrap fails, the attachment fails as a unit and "
              "the other carrier is closed too", ["MULTIPATH.md#4.2", "MULTIPATH.md#7.3"],
              profile=P)
    t.up(carrier="up0", dir="in", via="listener", pairing=token)
    t.up(carrier="down0", dir="out", via="listener", pairing=token)
    tp_rec = [[TP(tp_list(peer_mp()))]]
    t._add(op="peer_send", carrier="up0", records=tp_rec, hex=D.record_bytes(tp_rec))
    t.peer_sent[0] = len(D.record_bytes(tp_rec))
    t.frames(0, TP_ANY, match="first")
    t.event("established", features=MP_FEATURES)
    join_up(t, "c2", 2, "bidi")
    t.down(0, "in", "error")
    t.pipe_closed(0, "out", "any")
    t.frames(2, PIPE_ABANDON(0, PIPE_FAILED))
    t.is_open()
    out.append(t)

    t = Trace("multipath.join.local_inband",
              "A local join carries a token keyed with the local role", ["MULTIPATH.md#5.7"],
              profile=P)
    mp_handshake(t)
    t.api("add_pipe", pipe=1, dir="out", fate_group=0, expect={"ok": True})
    lj = join(1, "up", locator=D.PEER_LOCATOR, role="responder")
    t.frames(1, lj, match="first")
    out.append(t)

    t = Trace("multipath.join.local_out_of_band",
              "A local out-of-band join is handed to the adapter", ["MULTIPATH.md#5.3"],
              profile=P)
    mp_handshake(t)
    t.api("add_pipe", pipe=1, dir="in", fate_group=7, oob=True, expect={"ok": True})
    lj = join(1, "down", fate_group=7, locator=D.PEER_LOCATOR, role="responder")
    t.event("oob_join", pipe=1, frame=F.encode_frame(lj))
    out.append(t)

    t = Trace("multipath.join.local_join_becomes_active",
              "A local join is JOINING until the peer acknowledges bytes on it",
              ["MULTIPATH.md#5.7", "STATE_MACHINE.md#3.1"], profile=P)
    mp_handshake(t)
    t.api("add_pipe", pipe=1, dir="bidi", fate_group=0, expect={"ok": True})
    t.frames(1, join(1, "bidi", locator=D.PEER_LOCATOR, role="responder"), match="first")
    t.state(pipe=1, dir="out", pstate="JOINING")
    t.send(1, [PIPE_ACK((1, SENT(1), 0))])
    t.state(pipe=1, dir="out", pstate="ACTIVE")
    out.append(t)

    t = Trace("multipath.join.leading_parameters_discarded",
              "A shared listener may send its transport parameters before it sees the join: "
              "the joiner discards that leading record, which confirms nothing and does not "
              "count in the pipe's position", ["MULTIPATH.md#5.7", "SPEC.md#13.2"], profile=P)
    mp_handshake(t)
    t.api("add_pipe", pipe=1, dir="bidi", fate_group=0, expect={"ok": True})
    t.frames(1, join(1, "bidi", locator=D.PEER_LOCATOR, role="responder"), match="first")
    t.send(1, [TP(tp_list(peer_small()))])
    t.state(pipe=1, dir="out", pstate="JOINING")
    t.is_open()
    ping_rec = D.record_bytes([[PIPE_PING()]])
    t.send(1, [PIPE_PING()])
    t.state(pipe=1, dir="out", pstate="ACTIVE")
    t.note("The answer to the PIPE_PING is a complete snapshot; pipe 1's position counts only "
           "the PIPE_PING record, not the discarded parameters.")
    t.frames("*", PIPE_ACK((0, t.peer_sent[0], "*"), (1, len(ping_rec), "*")),
             match="unordered")
    out.append(t)

    t = Trace("multipath.join.acceptor_confirms_downlink",
              "The acceptor writes a record carrying PIPE_PING on a joined bidirectional pipe "
              "within its max_ack_delay, and the acknowledgement gives the pipe its first RTT "
              "sample", ["MULTIPATH.md#5.4"], profile=P)
    mp_handshake(t)
    join_up(t, "c2", 2, "bidi", confirm=False)
    t.adv(25)
    t.frames(2, PIPE_PING())
    t.send(2, [PIPE_ACK((2, SENT(2), 0))])
    t.state(pipe=2, dir="out", pstate="ACTIVE")
    out.append(t)

    t = Trace("multipath.join.direction_mismatch",
              "An authenticated join whose direction the carrier cannot provide is abandoned, "
              "not fatal", ["MULTIPATH.md#5.4"], profile=P)
    mp_handshake(t)
    t.up(carrier="c2", dir="out", via="listener", oob_join=F.encode_frame(join(2, "up")))
    t.reject_carrier("c2")
    t.frames(0, PIPE_ABANDON(2, PIPE_FAILED))
    t.is_open()
    out.append(t)

    t = Trace("multipath.join.abandoned_id_is_used",
              "PIPE_ABANDON for a pipe ID never seen, within the replay window, sets its bit and "
              "is answered; a later join with it is a replay",
              ["MULTIPATH.md#8.1", "MULTIPATH.md#4.3", "MULTIPATH.md#5.4"], profile=P)
    mp_handshake(t)
    t.send(0, [PIPE_ABANDON(4, PIPE_FAILED)])
    t.frames(0, PIPE_ABANDON(4, PIPE_FAILED))
    t.up(carrier="c4", dir="in", via="listener")
    j = join(4, "up")
    t._add(op="peer_send", carrier="c4", records=[[j]], hex=D.record_bytes([[j]]))
    t.reject_carrier("c4")
    t.is_open()
    out.append(t)

    # --- receive side of spreading
    t = Trace("multipath.spread.reorder_across_pipes",
              "Frames of one stream arrive interleaved on two pipes, the preamble last",
              ["MULTIPATH.md#13", "SPEC.md#7.3"], profile=P)
    mp_handshake(t)
    join_up(t, "c2", 2, "up", [STREAM(0, b"efgh", off=5, fin=True)])
    t.api("accept_bidi", expect={"pending": True})
    t.send(0, [STREAM(0, EMPTY_PRE + b"abcd")])
    t.api("accept_bidi", bind="s", expect={"stream": 0})
    t.api("read", stream="s", max=20, expect={"data": b"abcdefgh", "fin": True})
    out.append(t)

    t = Trace("multipath.spread.duplicates_are_harmless",
              "Duplicate data on two pipes is delivered once", ["MULTIPATH.md#7.6"], profile=P)
    mp_handshake(t)
    join_up(t, "c2", 2, "up", [STREAM(0, EMPTY_PRE + b"abc", fin=True), MAX_DATA(8192)])
    t.send(0, [STREAM(0, EMPTY_PRE + b"abc", fin=True), MAX_DATA(8192)])
    t.api("accept_bidi", bind="s", expect={"stream": 0})
    t.api("read", stream="s", max=20, expect={"data": b"abc", "fin": True})
    t.is_open()
    out.append(t)

    t = Trace("multipath.spread.beyond_window", "Reordered data beyond the window is fatal",
              ["MULTIPATH.md#13"], profile=P)
    mp_handshake(t)
    join_up(t, "c2", 2, "up", [STREAM(0, b"x", off=2048)])
    t.closed("FLOW_CONTROL_ERROR")
    out.append(t)


    t = Trace("multipath.ack.pipe_ping_answered_on_its_pair",
              "A PIPE_PING that arrives on a backup pipe is answered on that pipe pair too, "
              "not only on the pipes of the control order", ["MULTIPATH.md#6.5"], profile=P)
    mp_handshake(t)
    join_up(t, "c2", 2, "bidi")
    join_up(t, "c4", 4, "bidi")
    t.api("set_pipe_status", pipe=4, send_priority=5, expect={"ok": True})
    t.note("Pipe 4 is a less-preferred tier: it is last in the control order, behind the "
           "primary pipe and the pipe of the redundant copy (pipes 0 and 2).")
    t.send(4, [PIPE_PING()])
    t.frames(4, {"type": "PIPE_ACK"})
    out.append(t)

    t = Trace("multipath.credit.reset_lowering_reliable_size_releases",
              "A reset that lowers the Reliable Size releases the session credit of the lost "
              "bytes between the old and the new Reliable Size", ["SPEC.md#8.3"], profile=P)
    mp_handshake(t)
    join_up(t, "c2", 2, "bidi")
    t.send(0, [STREAM(i, EMPTY_PRE + b"a") for i in (0, 4, 8, 12)])
    t.down(2, "bidi", "error")
    t.frames(0, PIPE_ABANDON(2, PIPE_FAILED))
    t.note("RESET_STREAM_AT with final size 2048 and Reliable Size 1500 on each: the lost "
           "bytes [1500, 2048) are released, 4 x 548, on top of the 4 x 2 released on "
           "receipt; the peer is blocked, so MAX_DATA = 8192 + 2200 goes out at once.")
    t.send(0, [RESET_AT(i, 9, 2048, 1500) for i in (0, 4, 8, 12)])
    t.frames(0, MAX_DATA({"min": 10392, "max": 10392}))
    t.note("RESET_STREAM_AT lowers the Reliable Size to the preamble size (1 byte; with the "
           "preamble in use RESET_STREAM would be a PROTOCOL_VIOLATION, EXTENSIONS.md 4.4): "
           "[2, 1500) is lost too, 4 x 1498 bytes more, a regular update (more than a quarter "
           "of the window).")
    t.send(0, [RESET_AT(i, 9, 2048, len(EMPTY_PRE)) for i in (0, 4, 8, 12)])
    t.frames(0, MAX_DATA({"min": 16384, "max": 16384}))
    t.is_open()
    out.append(t)

    t = Trace("multipath.credit.reset_releases_lost_bytes",
              "Bytes lost with a failed pipe and then covered by a reset release session credit "
              "at once; a late copy of them releases nothing",
              ["SPEC.md#8.3", "MULTIPATH.md#7.4"], profile=P)
    mp_handshake(t)
    join_up(t, "c2", 2, "bidi")
    t.send(0, [STREAM(i, EMPTY_PRE + b"a") for i in (0, 4, 8, 12)])
    t.note("The rest of each stream was on pipe 2, which fails with it; the peer then resets "
           "the four streams with final size 2048 each, the whole 8192-byte session window. "
           "The preamble is in use, so the reset is RESET_STREAM_AT with the preamble size "
           "(1 byte) as Reliable Size (EXTENSIONS.md 4.4, 4.5).")
    t.down(2, "bidi", "error")
    t.frames(0, PIPE_ABANDON(2, PIPE_FAILED))
    t.send(0, [RESET_AT(i, 9, 2048, len(EMPTY_PRE)) for i in (0, 4, 8, 12)])
    t.note("Every byte below the final sizes is released: the bytes received on receipt, "
           "the lost ones by the reset. The peer is blocked, so MAX_DATA = 8192 + 8192 goes "
           "out at once (without the reset rule it would be 8192 + 4 x (preamble + 1)).")
    t.frames(0, MAX_DATA({"min": 16384}))
    t.note("Late copies of 2 x 2040 lost bytes arrive (a pipe's held data, for example): "
           "they release nothing, so no MAX_DATA follows; releasing them again would put "
           "4080 bytes, more than a quarter of the window, into a regular update.")
    t.send(0, [STREAM(i, b"z" * 2040, off=len(EMPTY_PRE) + 1) for i in (0, 4)])
    t.no_frames("*", ["MAX_DATA"])
    t.is_open()
    out.append(t)

    t = Trace("multipath.ping.out_of_order_request_ignored",
              "In a multipath session a QX_PING request that does not exceed an earlier one "
              "arrived over another pipe and is ignored, not fatal",
              ["SPEC.md#4.5", "SPEC.md#9.4.1"], profile=P)
    mp_handshake(t)
    join_up(t, "c2", 2, "bidi")
    t.send(2, [PING(2)])
    t.frames("*", PONG(2))
    t.send(0, [PING(1)])
    t.no_frames("*", ["QX_PING"])
    t.is_open()
    out.append(t)

    # --- acknowledgement
    t = Trace("multipath.ack.threshold", "Reaching zmux_ack_threshold triggers PIPE_ACK at once",
              ["MULTIPATH.md#6.3"], profile=P)
    mp_handshake(t)
    blob = bytes(2047)
    join_up(t, "c2", 2, "up", [STREAM(0, EMPTY_PRE + blob), STREAM(4, EMPTY_PRE + blob)])
    t.frames("*", PIPE_ACK((0, t.peer_sent[0], "*"), (2, t.peer_sent[2], "*")))
    out.append(t)

    t = Trace("multipath.ack.pipe_ping", "PIPE_PING elicits an immediate PIPE_ACK",
              ["MULTIPATH.md#6.4"], profile=P)
    mp_handshake(t)
    join_up(t, "c2", 2, "up", [PIPE_PING()])
    t.frames("*", PIPE_ACK((0, t.peer_sent[0], "*"), (2, t.peer_sent[2], "*")))
    out.append(t)

    t = Trace("multipath.ack.ahead_of_sent",
              "Acknowledging bytes that were never sent is fatal", ["MULTIPATH.md#6.2"], profile=P)
    mp_handshake(t)
    t.send(0, [PIPE_ACK((0, SENT(0, 1), 0))])
    t.closed("PROTOCOL_VIOLATION")
    out.append(t)

    t = Trace("multipath.ack.pipe_without_outbound",
              "Acknowledging bytes on a pipe the receiver does not send on is fatal; a zero "
              "position for it is ignored", ["MULTIPATH.md#6.2"], profile=P)
    mp_handshake(t)
    join_up(t, "c2", 2, "up")
    t.send(0, [PIPE_ACK((2, 0, 0))])
    t.is_open()
    t.send(0, [PIPE_ACK((2, 1, 0))])
    t.closed("PROTOCOL_VIOLATION")
    out.append(t)

    t = Trace("multipath.ack.outdated_ignored",
              "A PIPE_ACK with an older position is ignored", ["MULTIPATH.md#6.2"], profile=P)
    mp_handshake(t)
    t.send(0, [PIPE_ACK((0, SENT(0), 0))])
    t.send(0, [PIPE_ACK((0, 1, 0))])
    t.is_open()
    out.append(t)

    t = Trace("multipath.ack.pipe_ends_mid_record",
              "A pipe that ends inside a record fails; the partial record is discarded and not "
              "counted, and the final position is acknowledged promptly",
              ["MULTIPATH.md#6.1", "MULTIPATH.md#6.3", "MULTIPATH.md#7.3"], profile=P)
    mp_handshake(t)
    join_up(t, "c2", 2, "up", [STREAM(0, EMPTY_PRE + b"ab")])
    complete = t.peer_sent[2]
    t.raw(2, F.encode_record([STREAM(0, b"cd", off=3, fin=True)])[:-1])
    t.down(2, "in", "eof")
    t.frames("*", PIPE_ABANDON(2, PIPE_FAILED),
             PIPE_ACK((0, t.peer_sent[0], "*"), (2, complete, "*")), match="unordered")
    t.api("accept_bidi", bind="s", expect={"stream": 0})
    t.api("read", stream="s", max=10, expect={"data": b"ab", "fin": False})
    t.api("read", stream="s", max=10, expect={"pending": True})
    t.is_open()
    out.append(t)

    # --- failure and reinjection
    def two_pipes(t):
        mp_handshake(t)
        join_up(t, "c2", 2, "bidi")
        t.writable(2, False)
        t.api("open_bidi", bind="a", expect={"ok": True})
        t.api("write", stream="a", data=b"payload-123", fin=False, expect={"accepted": 11})
        t.frames(0, SDATA(1, LOCAL_FIRST_PRE + b"payload-123"))
        t.writable(2, True)

    t = Trace("multipath.failover.carrier_error_reinjects",
              "A failed pipe's unacknowledged data is re-sent on another pipe",
              ["MULTIPATH.md#7.2", "MULTIPATH.md#7.4"], profile=P)
    two_pipes(t)
    t.down(0, "bidi", "error")
    t.frames(2, SDATA(1, LOCAL_FIRST_PRE + b"payload-123"),
             PIPE_ABANDON(0, PIPE_FAILED), match="unordered")
    t.is_open()
    out.append(t)

    # --- removal
    t = Trace("multipath.remove.graceful_local",
              "Graceful removal drains, closes and retires the attachment",
              ["MULTIPATH.md#8.2", "MULTIPATH.md#8.4"], profile=P)
    mp_handshake(t)
    join_up(t, "c2", 2, "bidi")
    t.api("remove_pipe", pipe=2, graceful=True, expect={"ok": True})
    t.frames("*", PIPE_ABANDON(2, 0))
    t.state(pipe=2, dir="out", pstate="DRAINING")
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=b"after", fin=True, expect={"accepted": 5})
    t.frames(0, SDATA(1, LOCAL_FIRST_PRE + b"after", fin=True))
    t.no_frames(2, ["STREAM"])
    t.note("The write side closes only once everything is acknowledged and the peer's "
           "PIPE_ABANDON has arrived (MULTIPATH 8.2).")
    t.send(0, [PIPE_ACK((0, SENT(0), 0), (2, SENT(2), 0))])
    t.state(pipe=2, dir="out", pstate="DRAINING")
    t.send(0, [PIPE_ABANDON(2, 0)])
    t.pipe_closed(2, "out", "graceful")
    t.down(2, "in", "eof")
    t.is_open()
    out.append(t)

    t = Trace("multipath.remove.linger_after_exchange_is_graceful",
              "After both PIPE_ABANDON frames, a peer that never ends its direction is cut off at "
              "the linger timeout as a graceful end, not a failure", ["MULTIPATH.md#8.2",
                                                                       "MULTIPATH.md#8.4"],
              profile=P)
    mp_handshake(t)
    join_up(t, "c2", 2, "bidi")
    t.api("remove_pipe", pipe=2, graceful=True, expect={"ok": True})
    t.frames("*", PIPE_ABANDON(2, 0))
    t.send(0, [PIPE_ACK((0, SENT(0), 0), (2, SENT(2), 0)), PIPE_ABANDON(2, 0)])
    t.pipe_closed(2, "out", "graceful")
    t.note("Linger = min(2 s, max(250 ms, 3 x 10 ms)) = 250 ms from the exchange.")
    t.adv(249)
    t.state(pipe=2, dir="in", pstate="OPEN")
    t.adv(1)
    t.note("The linger expiry ends the inbound direction gracefully; with the outbound "
           "direction CLOSED, both PIPE_ABANDON frames exchanged and the own one acknowledged, "
           "the attachment is retired at once (MULTIPATH.md 8.4), and no PIPE_ABANDON "
           "(ZMUX_PIPE_FAILED) follows.")
    t.pipe_closed(2, "in", "any")
    t.state(pipe=2, dir="in", pstate="RETIRED")
    t.no_frames("*", ["PIPE_ABANDON"])
    out.append(t)

    t = Trace("multipath.remove.peer_graceful",
              "On a peer's graceful PIPE_ABANDON the endpoint stops using the pipe and answers",
              ["MULTIPATH.md#8.2"], profile=P)
    mp_handshake(t)
    join_up(t, "c2", 2, "bidi")
    t.send(0, [PIPE_ABANDON(2, 0)])
    t.frames("*", PIPE_ABANDON(2, 0))
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=b"z", fin=True, expect={"accepted": 1})
    t.frames(0, SDATA(1, LOCAL_FIRST_PRE + b"z", fin=True))
    t.no_frames(2, ["STREAM"])
    out.append(t)

    t = Trace("multipath.remove.abrupt_local",
              "Abrupt removal reinjects and abandons with ZMUX_PIPE_FAILED",
              ["MULTIPATH.md#8.3"], profile=P)
    mp_handshake(t)
    join_up(t, "c2", 2, "bidi")
    t.writable(0, False)
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=b"on-two", fin=False, expect={"accepted": 6})
    t.frames(2, SDATA(1, LOCAL_FIRST_PRE + b"on-two"))
    t.writable(0, True)
    t.api("remove_pipe", pipe=2, graceful=False, expect={"ok": True})
    t.frames(0, SDATA(1, LOCAL_FIRST_PRE + b"on-two"), PIPE_ABANDON(2, PIPE_FAILED),
             match="unordered")
    t.pipe_closed(2, "out", "any")
    out.append(t)

    t = Trace("multipath.remove.last_outbound_without_resume",
              "Losing the last outbound pipe without resume ends the session locally (pipes "
              "lost); no pipe is left for CONNECTION_CLOSE",
              ["MULTIPATH.md#8.5"], profile=P)
    mp_handshake(t)
    join_up(t, "c2", 2, "up")
    t.down(0, "bidi", "error")
    t.closed("PIPES_LOST", space="local", wire=False)
    out.append(t)

    t = Trace("multipath.remove.last_inbound_without_resume",
              "Losing the last inbound pipe without resume sends CONNECTION_CLOSE(NO_VIABLE_PATH)",
              ["MULTIPATH.md#8.5"], profile=P)
    mp_handshake(t)
    t.up(carrier="c4", dir="out", via="listener", oob_join=F.encode_frame(join(4, "down")))
    t.down(0, "bidi", "error")
    t.frames(4, {"type": "CONNECTION_CLOSE", "app": False, "code": TE("NO_VIABLE_PATH")})
    t.closed("PIPES_LOST", space="local")
    out.append(t)


    t = Trace("multipath.status.local_standby",
              "A pipe marked standby locally carries no new stream data while another pipe works, "
              "and the peer is asked to treat its direction as standby too",
              ["MULTIPATH.md#9"], profile=P)
    mp_handshake(t)
    join_up(t, "c2", 2, "bidi")
    t.api("set_pipe_status", pipe=2, send_standby=True, receive_standby=True, expect={"ok": True})
    t.frames("*", PIPE_STATUS(2, 1, "standby", 0))
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=b"primary", fin=True, expect={"accepted": 7})
    t.frames(0, SDATA(1, LOCAL_FIRST_PRE + b"primary", fin=True))
    t.no_frames(2, ["STREAM"])
    out.append(t)

    t = Trace("multipath.status.stale_ignored", "An older PIPE_STATUS is ignored, not an error",
              ["MULTIPATH.md#9"], profile=P)
    mp_handshake(t)
    join_up(t, "c2", 2, "bidi")
    t.send(0, [PIPE_STATUS(2, 2, "available", 2), PIPE_STATUS(2, 1, "standby", 6)])
    t.is_open()
    out.append(t)

    t = Trace("multipath.status.invalid_value", "A PIPE_STATUS value other than 0 or 1 is fatal",
              ["MULTIPATH.md#9"], profile=P)
    mp_handshake(t)
    t.raw(0, F.encode_record([RAW(V(0x227B2811) + V(0) + V(1) + V(7) + V(3))]))
    t.closed("FRAME_ENCODING_ERROR")
    out.append(t)


    t = Trace("multipath.close.on_every_pipe", "CONNECTION_CLOSE goes on every live outbound pipe",
              ["SPEC.md#6.5", "MULTIPATH.md#3.4"], profile=P)
    mp_handshake(t)
    join_up(t, "c2", 2, "bidi")
    t.api("close", code=0, reason=b"", app=True, expect={"ok": True})
    t.frames(0, CLOSE(0, app=True))
    t.frames(2, CLOSE(0, app=True))
    t.closed(0, space="application", remote=False)
    out.append(t)

    t = Trace("multipath.idle.received_records_only",
              "In multipath sessions only received records restart the idle timer",
              ["SPEC.md#6.4"], profile=P)
    mp_handshake(t, peer=peer_mp(max_idle_timeout=10000))
    t.writable(0, False)
    t.send(0, [PIPE_ACK((0, SENT(0), 0)), STREAM(0, EMPTY_PRE + b"x")])
    t.note("The acknowledgement for this record is written only at 4900 ms, before the "
           "last pipe's stall limit of the test-mode sender (TRACES.md Section 3.4) fails the "
           "write-stalled pipe; in a session without acknowledgement that write would "
           "restart the idle timer.")
    t.adv(4900)
    t.writable(0, True)
    t.frames(0, {"type": "PIPE_ACK"})
    t.send(0, [PIPE_ACK((0, SENT(0), 0))])
    t.state(pipe=0, dir="out", pstate="ACTIVE")
    t.adv(5099)
    t.is_open()
    t.adv(1)
    t.closed("IDLE_TIMEOUT", space="local")
    t.frames(0, {"type": "CONNECTION_CLOSE", "app": False, "code": TE("ZMUX_IDLE_TIMEOUT")})
    out.append(t)

    t = Trace("multipath.idle.keep_alive_counts_activity",
              "In a multipath session the keep-alive timer counts the same activity as the "
              "idle timer: probes and their acknowledgements do not postpone it",
              ["SPEC.md#6.4", "MULTIPATH.md#3.4"], profile=P,
              overrides={"config": {"keep_alive_period": 20000}})
    mp_handshake(t)
    t.send(0, [PIPE_ACK((0, SENT(0), 0))])
    t.adv(15000)
    t.frames(0, PIPE_PING())
    t.send(0, [PIPE_ACK((0, SENT(0), 0))])
    t.adv(4999)
    t.no_frames(0, ["QX_PING"])
    t.adv(1)
    t.frames(0, PING(0))
    t.send(0, [PONG(0), PIPE_ACK((0, SENT(0), 0))])
    t.adv(15000)
    t.frames(0, PIPE_PING())
    t.send(0, [PIPE_ACK((0, SENT(0), 0))])
    t.adv(5000)
    t.frames(0, PING(1))
    t.send(0, [PONG(1), PIPE_ACK((0, SENT(0), 0))])
    t.adv(15000)
    t.frames(0, PIPE_PING())
    t.send(0, [PIPE_ACK((0, SENT(0), 0))])
    t.adv(5000)
    t.frames(0, PING(2))
    t.send(0, [PONG(2), PIPE_ACK((0, SENT(0), 0))])
    t.adv(1000)
    t.is_open()
    out.append(t)

    t = Trace("multipath.idle.probes_do_not_keep_alive",
              "With a 30 s idle timeout and the 15 s test-mode probe, PIPE_PING and "
              "PIPE_ACK never keep a multipath session alive", ["SPEC.md#6.4", "MULTIPATH.md#7.1"],
              profile=P)
    mp_handshake(t)
    t.send(0, [PIPE_ACK((0, SENT(0), 0))])
    t.adv(14999)
    t.no_frames(0, ["PIPE_PING"])
    t.adv(1)
    t.frames(0, PIPE_PING())
    t.send(0, [PIPE_ACK((0, SENT(0), 0)), PIPE_PING()])
    t.frames(0, {"type": "PIPE_ACK"})
    t.adv(14999)
    t.is_open()
    t.adv(1)
    t.closed("IDLE_TIMEOUT", space="local")
    t.frames(0, {"type": "CONNECTION_CLOSE", "app": False, "code": TE("ZMUX_IDLE_TIMEOUT")})
    out.append(t)

    t = Trace("multipath.idle.peer_idle_close",
              "CONNECTION_CLOSE with ZMUX_IDLE_TIMEOUT is reported as the peer's idle timeout",
              ["SPEC.md#6.4", "SPEC.md#10.2"], profile=P)
    mp_handshake(t)
    t.send(0, [CLOSE(TE("ZMUX_IDLE_TIMEOUT"))])
    t.closed("IDLE_TIMEOUT", space="local", remote=True)
    out.append(t)

    t = Trace("multipath.budget.duplicates_not_charged",
              "In multipath sessions duplicates and outdated copies are never chargeable, while "
              "the budget still counts PADDING-only records", ["SPEC.md#9.4"], profile=P)
    mp_handshake(t)
    join_up(t, "c2", 2, "up")
    t.send(0, *([[PADDING(1)]] * 4096))
    t.is_open()
    recs = [[MAX_DATA(8192 + k + 1), MAX_STREAMS(4 + k + 1)] for k in range(60)]
    for r in recs:
        t.send(0, r)
    for r in recs:
        t.send(2, r)
    t.send(0, [REPEAT(500, MAX_DATA(8192)), REPEAT(500, DATA_BLOCKED(8192))])
    t.is_open()
    t.send(2, [PADDING(1)])
    t.closed("ZMUX_EXCESSIVE_LOAD")
    out.append(t)

    # --- coverage of pipe credit, join failure, retirement and abandonment
    def local_join(pid, direction="up", fate_group=0):
        return join(pid, direction, fate_group=fate_group, locator=D.PEER_LOCATOR,
                    role="responder")

    t = Trace("multipath.join.refusal_backoff",
              "After a join is refused with ZMUX_PIPE_RESOURCE_LIMIT, the joiner joins again "
              "only after a backoff of 1 s (or an own retirement); pipe IDs are not credit",
              ["MULTIPATH.md#5.7", "MULTIPATH.md#4.1"], profile=P)
    mp_handshake(t)
    t.api("add_pipe", pipe=1, dir="out", fate_group=0, expect={"ok": True})
    t.frames(1, local_join(1), match="first")
    t.send(0, [PIPE_ABANDON(1, TE("ZMUX_PIPE_RESOURCE_LIMIT"))])
    t.frames("*", {"type": "PIPE_ABANDON", "pipe": 1})
    t.note("The peer never acknowledges the answer, so attachment 1 is not retired: only the "
           "backoff, 1 s after the refusal, allows the next join, which takes a new pipe ID.")
    t.api("add_pipe", pipe=3, dir="out", fate_group=0, expect={"pending": True})
    t.no_frames(3)
    t.adv(999)
    t.no_frames(3)
    t.adv(1)
    t.frames(3, local_join(3), match="first")
    out.append(t)

    t = Trace("multipath.join.local_join_timeout",
              "A local join that is not confirmed within the handshake timeout fails and is "
              "abandoned", ["MULTIPATH.md#5.7", "STATE_MACHINE.md#3.2"], profile=P)
    mp_handshake(t)
    t.send(0, [PIPE_ACK((0, SENT(0), 0))])
    t.api("add_pipe", pipe=1, dir="in", fate_group=0, oob=True, expect={"ok": True})
    t.event("oob_join", pipe=1, frame=F.encode_frame(local_join(1, "down")))
    t.adv(9999)
    t.state(pipe=1, dir="in", pstate="JOINING")
    t.adv(1)
    t.state(pipe=1, dir="in", pstate="ENDED")
    t.frames(0, PIPE_ABANDON(1, PIPE_FAILED))
    t.is_open()
    out.append(t)

    t = Trace("multipath.ack.retired_entry_ignored",
              "After retirement, PIPE_ACK entries for the retired pipe are ignored",
              ["MULTIPATH.md#6.2", "MULTIPATH.md#8.4"], profile=P)
    mp_handshake(t)
    join_up(t, "c2", 2, "bidi")
    t.api("remove_pipe", pipe=2, graceful=True, expect={"ok": True})
    t.frames("*", PIPE_ABANDON(2, 0))
    t.send(0, [PIPE_ACK((0, SENT(0), 0), (2, SENT(2), 0)), PIPE_ABANDON(2, 0)])
    t.pipe_closed(2, "out", "graceful")
    t.down(2, "in", "eof")
    t.state(pipe=2, dir="out", pstate="RETIRED")
    t.send(0, [PIPE_ACK((0, SENT(0), 0), (2, 1000000, 0))])
    t.is_open()
    out.append(t)

    t = Trace("multipath.remove.failed_attachment_retired",
              "A failed attachment is retired once both PIPE_ABANDON frames are exchanged and "
              "acknowledged; a join that reuses its pipe ID is then a replay",
              ["MULTIPATH.md#8.3", "MULTIPATH.md#8.4", "MULTIPATH.md#4.3"], profile=P)
    mp_handshake(t)
    join_up(t, "c2", 2, "up")
    t.down(2, "in", "error")
    t.frames(0, PIPE_ABANDON(2, PIPE_FAILED))
    t.send(0, [PIPE_ACK((0, SENT(0), 0)), PIPE_ABANDON(2, PIPE_FAILED)])
    t.state(pipe=2, dir="in", pstate="RETIRED")
    t.up(carrier="c2-again", dir="in", via="listener")
    j = join(2, "up")
    t._add(op="peer_send", carrier="c2-again", records=[[j]], hex=D.record_bytes([[j]]))
    t.reject_carrier("c2-again")
    t.is_open()
    out.append(t)

    t = Trace("multipath.remove.abandon_answered_once",
              "A peer's PIPE_ABANDON is answered once: a copy that arrives again gets no "
              "second answer, since the own PIPE_ABANDON is retained until acknowledged",
              ["MULTIPATH.md#8.4"], profile=P)
    mp_handshake(t)
    join_up(t, "c2", 2, "bidi")
    t.send(0, [PIPE_ABANDON(2, 0)])
    t.frames("*", PIPE_ABANDON(2, 0))
    t.note("Sending the answer completes the PIPE_ABANDON exchange, and pipe 2 carries no "
           "unacknowledged stream data, so its drain completes at once (MULTIPATH.md 8.2).")
    t.state(pipe=2, dir="out", pstate="CLOSED")
    t.pipe_closed(2, "out", "graceful")
    t.send(2, [PIPE_ABANDON(2, 0)])
    t.no_frames("*", ["PIPE_ABANDON"])
    t.is_open()
    out.append(t)

    t = Trace("multipath.remove.abandon_unseen_above_window_ignored",
              "PIPE_ABANDON for a peer pipe ID above the replay window is ignored, not answered, "
              "and does not block a later join with that ID",
              ["MULTIPATH.md#8.1", "MULTIPATH.md#4.3"], profile=P)
    mp_handshake(t)
    t.note("The peer is the initiator: h is 0 and the window covers its next 64 IDs (2 to "
           "128). An injected PIPE_ABANDON for 1000 is above it: ignored.")
    t.send(0, [PIPE_ABANDON(1000, PIPE_FAILED)])
    t.no_frames("*", ["PIPE_ABANDON"])
    join_up(t, "c1000", 1000, "up")
    t.state(pipe=1000, dir="in", pstate="OPEN")
    t.is_open()
    out.append(t)

    t = Trace("multipath.join.replay_window",
              "Only verified joins move the replay window; a join more than W IDs below the "
              "highest is stale, one within the window is accepted, and an attachment keeps "
              "working however far below the window its ID falls",
              ["MULTIPATH.md#4.3", "MULTIPATH.md#5.4"], profile=P)
    mp_handshake(t)
    join_up(t, "c2", 2, "bidi")
    t.note("W = max(64, 2 x (8 + 8)) = 64 IDs of the peer's parity. The join of 200 raises h "
           "to 200: IDs below 200 - 2 x 64 = 72 are stale.")
    join_up(t, "c200", 200, "up")
    t.up(carrier="c4", dir="in", via="listener")
    j4 = join(4, "up")
    t._add(op="peer_send", carrier="c4", records=[[j4]], hex=D.record_bytes([[j4]]))
    t.reject_carrier("c4")
    join_up(t, "c100", 100, "up")
    t.state(pipe=100, dir="in", pstate="OPEN")
    t.send(2, [STREAM(0, EMPTY_PRE + b"old-pipe")])
    t.api("accept_bidi", bind="s", expect={"stream": 0})
    t.api("read", stream="s", max=10, expect={"data": b"old-pipe", "fin": False})
    t.adv(25)
    t.frames("*", {"type": "PIPE_ACK"})
    t.is_open()
    out.append(t)

    t = Trace("multipath.ack.unknown_peer_id_nonzero_position",
              "A PIPE_ACK position above 0 for a peer pipe ID without attachment state claims "
              "data never sent", ["MULTIPATH.md#6.2", "MULTIPATH.md#4.3"], profile=P)
    mp_handshake(t)
    t.send(0, [PIPE_ACK((4, 5, 0))])
    t.closed("PROTOCOL_VIOLATION")
    out.append(t)

    t = Trace("multipath.ack.untaken_own_parity",
              "A PIPE_ACK entry for a pipe ID of the receiver's own parity that it has not taken "
              "is fatal", ["MULTIPATH.md#6.2", "MULTIPATH.md#4.3"], profile=P)
    mp_handshake(t)
    t.send(0, [PIPE_ACK((0, 0, 0), (1, 0, 0))])
    t.closed("PROTOCOL_VIOLATION")
    out.append(t)

    t = Trace("multipath.remove.peer_resource_limit",
              "PIPE_ABANDON with ZMUX_PIPE_RESOURCE_LIMIT fails the local attachment: its data is "
              "re-sent elsewhere and the abandon is answered", ["MULTIPATH.md#5.4",
                                                                "MULTIPATH.md#8.3"], profile=P)
    mp_handshake(t)
    t.writable(0, False)
    t.api("add_pipe", pipe=1, dir="out", fate_group=0, expect={"ok": True})
    t.frames(1, local_join(1), match="first")
    t.note("The peer acknowledges the join, so pipe 1 is ACTIVE (and, as the test-mode sender has "
           "no probation, usable): stream data may go there while pipe 0 is not writable. A "
           "JOINING pipe would not take it, since pipe 0 is usable (MULTIPATH.md Section 11 rule S1).")
    t.send(0, [PIPE_ACK((1, SENT(1), 0))])
    t.state(pipe=1, dir="out", pstate="ACTIVE")
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=b"via-one", fin=False, expect={"accepted": 7})
    t.frames(1, SDATA(1, LOCAL_FIRST_PRE + b"via-one"))
    t.writable(0, True)
    t.send(0, [PIPE_ABANDON(1, TE("ZMUX_PIPE_RESOURCE_LIMIT"))])
    t.frames(0, SDATA(1, LOCAL_FIRST_PRE + b"via-one"), {"type": "PIPE_ABANDON", "pipe": 1},
             match="unordered")
    t.state(pipe=1, dir="out", pstate="FAILED")
    t.pipe_closed(1, "out", "any")
    t.is_open()
    out.append(t)

    t = Trace("multipath.join.abandon_backoff",
              "After an attached pipe is abandoned with ZMUX_PIPE_RESOURCE_LIMIT, the joiner "
              "joins again only after the refusal backoff of 1 s", ["MULTIPATH.md#5.7",
                                                                   "MULTIPATH.md#5.4"], profile=P)
    mp_handshake(t)
    t.api("add_pipe", pipe=1, dir="out", fate_group=0, expect={"ok": True})
    t.frames(1, local_join(1), match="first")
    t.send(0, [PIPE_ACK((1, SENT(1), 0))])
    t.state(pipe=1, dir="out", pstate="ACTIVE")
    t.note("The acceptor attached pipe 1 and then could not keep it (a loop it found only "
           "after attaching, MULTIPATH 5.4 step 7): the same backoff as for a refusal, so that "
           "a joiner that cannot see the loop does not rejoin at once.")
    t.send(0, [PIPE_ABANDON(1, TE("ZMUX_PIPE_RESOURCE_LIMIT"))])
    t.frames(0, {"type": "PIPE_ABANDON", "pipe": 1})
    t.note("The peer acknowledges the answer, so attachment 1 retires at once; its own "
           "retirement does not end the backoff (MULTIPATH 5.7).")
    t.send(0, [PIPE_ACK((0, SENT(0), 0))])
    t.api("add_pipe", pipe=3, dir="out", fate_group=0, expect={"pending": True})
    t.no_frames(3)
    t.adv(999)
    t.no_frames(3)
    t.adv(1)
    t.frames(3, local_join(3), match="first")
    out.append(t)

    t = Trace("multipath.join.message_framed_positions",
              "On a message-framed pipe each message counts as its byte-stream form in "
              "positions", ["SPEC.md#3.4", "MULTIPATH.md#2.3"], profile=P)
    mp_handshake(t)
    t.up(carrier="c2", dir="in", via="listener", framing="records")
    m1 = [join(2, "up")]
    m2 = [STREAM(0, EMPTY_PRE + b"ab")]
    t.send_messages(None, m1, m2, carrier="c2")
    pos2 = sum(F.message_position(F.encode_frames(m)) for m in (m1, m2))
    t.api("accept_bidi", bind="s", expect={"stream": 0})
    t.adv(25)
    t.frames("*", PIPE_ACK((0, t.peer_sent[0], "*"), (2, pos2, "*")))
    out.append(t)

    t = Trace("multipath.failover.control_frames_reinjected",
              "When a pipe fails, its control information is re-sent with current values: credit, "
              "STOP_SENDING, RESET_STREAM_AT with the preamble only, PRIORITY_UPDATE, GOAWAY, and "
              "a new fence request", ["MULTIPATH.md#7.4", "SPEC.md#4.5"], profile=P)
    mp_handshake(t)
    join_up(t, "c2", 2, "bidi")
    t.writable(2, False)
    body = bytes(i % 251 for i in range(1000))
    t.send(0, [STREAM(0, EMPTY_PRE + body), STREAM(4, EMPTY_PRE + b"z")])
    t.api("accept_bidi", bind="s0", expect={"stream": 0})
    t.api("read", stream="s0", max=600, expect={"data": body[:600], "fin": False})
    t.api("accept_bidi", bind="s4", expect={"stream": 4})
    t.api("stop", stream="s4", code=6, expect={"ok": True})
    t.api("set_peer_priority", stream="s0", urgency=1, incremental=False, expect={"ok": True})
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=b"q", fin=False, expect={"accepted": 1})
    t.frames(0, MAX_STREAM_DATA(0, {"min": 2049}), STOP(4, 6), PRIO(0, 1, 1),
             SDATA(1, LOCAL_FIRST_PRE + b"q"), match="unordered")
    n = len(LOCAL_FIRST_PRE)
    t.api("reset", stream="a", code=5, expect={"ok": True})
    t.api("shutdown", code=0, expect={"ok": True})
    t.frames(0, RESET_AT(1, 5, n + 1, n), GOAWAY(4, 4, 0), PING(0), match="unordered")
    t.writable(2, True)
    t.down(0, "bidi", "error")
    t.frames(2, MAX_STREAM_DATA(0, {"min": 2049}), STOP(4, 6), PRIO(0, 1, 1),
             SDATA(1, LOCAL_FIRST_PRE), RESET_AT(1, 5, n + 1, n), GOAWAY(4, 4, 0), PING(1),
             PIPE_ABANDON(0, PIPE_FAILED), match="unordered")
    out.append(t)

    t = Trace("multipath.ack.lowest_delay_pipe_and_copy",
              "PIPE_ACK and credit frames use the pipe with the lowest one-way delay, and the "
              "snapshot is copied to a second pipe within 50 ms", ["MULTIPATH.md#6.5"], profile=P,
              overrides={"test": {"pipe_rtt_ms": {"0": 50, "2": 10}}})
    mp_handshake(t)
    join_up(t, "c2", 2, "bidi")
    t.send(0, [STREAM(0, EMPTY_PRE + body)])
    t.api("accept_bidi", bind="s", expect={"stream": 0})
    t.api("read", stream="s", max=600, expect={"data": body[:600], "fin": False})
    t.adv(25)
    t.frames(2, {"type": "PIPE_ACK"}, MAX_STREAM_DATA(0, {"min": 2049}), match="unordered")
    t.adv(50)
    t.frames(0, {"type": "PIPE_ACK"})
    out.append(t)

    t = Trace("multipath.credit.copy_with_redundant_snapshot",
              "While the window is the bottleneck, the latest limit is copied to the second pipe "
              "in the record of the redundant PIPE_ACK snapshot, not at once",
              ["MULTIPATH.md#6.5"], profile=P,
              overrides={"test": {"pipe_rtt_ms": {"0": 50, "2": 10}}})
    mp_handshake(t)
    join_up(t, "c2", 2, "bidi")
    fill = bytes(i % 251 for i in range(2048 - len(EMPTY_PRE)))
    t.send(0, [STREAM(0, EMPTY_PRE + fill), STREAM_DATA_BLOCKED(0, 2048)])
    t.api("accept_bidi", bind="s", expect={"stream": 0})
    t.api("read", stream="s", max=len(fill), expect={"data": fill, "fin": False})
    t.note("The peer is blocked on the stream (STREAM_DATA_BLOCKED at the limit), so the "
           "credit goes out at once on the first pipe of the control order (pipe 2, the "
           "lower delay) and its copy waits for the redundant snapshot on pipe 0.")
    t.frames(2, {"type": "PIPE_ACK"}, MAX_STREAM_DATA(0, {"min": 2049}), match="unordered")
    t.no_frames(0, ["MAX_STREAM_DATA"])
    t.adv(50)
    t.frames(0, {"type": "PIPE_ACK"}, MAX_STREAM_DATA(0, {"min": 2049}), match="unordered")
    out.append(t)

    t = Trace("multipath.join.carrier_bound_token",
              "A carrier-bound token verifies only on its own carrier; a copy on another carrier "
              "is a join authentication failure", ["MULTIPATH.md#10.5", "MULTIPATH.md#5.4"],
              profile=P)
    mp_handshake(t)
    cb2 = bytes(range(32))
    t.up(carrier="c2", dir="in", via="listener", carrier_binding=cb2)
    j2 = join(2, "up", carrier_bound=True, carrier_binding=cb2)
    rec = [[j2, STREAM(0, EMPTY_PRE + b"bound", fin=True)]]
    t._add(op="peer_send", carrier="c2", records=rec, hex=D.record_bytes(rec))
    t.api("accept_bidi", bind="s", expect={"stream": 0})
    t.api("read", stream="s", max=10, expect={"data": b"bound", "fin": True})
    j4 = join(4, "up", carrier_bound=True, carrier_binding=cb2)
    t.up(carrier="c4", dir="in", via="listener", carrier_binding=bytes(range(32, 64)))
    t._add(op="peer_send", carrier="c4", records=[[j4]], hex=D.record_bytes([[j4]]))
    t.reject_carrier("c4")
    t.event("join_auth_failed", reason="mac_carrier_bound")
    t.is_open()
    out.append(t)

    t = Trace("multipath.budget.fin_only_duplicates_not_charged",
              "5000 short streams whose FIN-only frames are reinjected on a second pipe: the "
              "duplicates are never chargeable", ["SPEC.md#9.4", "MULTIPATH.md#7.6"], profile=P,
              overrides={"tp": {"initial_max_streams_bidi": 5000, "initial_max_data": 65536}})
    mp_handshake(t)
    join_up(t, "c2", 2, "up")
    ids = [4 * i for i in range(5000)]
    for k in range(0, 5000, 1000):
        t.send(0, [STREAM(i, EMPTY_PRE) for i in ids[k:k + 1000]],
               [STREAM(i, b"", off=1, fin=True) for i in ids[k:k + 1000]])
    for k in range(0, 5000, 1000):
        t.send(2, [STREAM(i, b"", off=1, fin=True) for i in ids[k:k + 1000]])
    t.is_open()
    out.append(t)

    t = Trace("multipath.remove.abandon_unknown_code",
              "PIPE_ABANDON with an unknown code is treated as ZMUX_PIPE_FAILED",
              ["SPEC.md#10.2", "MULTIPATH.md#8.1"], profile=P)
    mp_handshake(t)
    join_up(t, "c2", 2, "bidi")
    t.send(0, [PIPE_ABANDON(2, 0x3FFF)])
    t.state(pipe=2, dir="out", pstate="FAILED")
    t.frames(0, {"type": "PIPE_ABANDON", "pipe": 2})
    t.is_open()
    out.append(t)

    t = Trace("multipath.ack.pipe_ping_rate_limit",
              "Fifteen joins accepted at once: the first records of at most 8 carry their "
              "PIPE_PING in the first second, the rest as soon as the limit allows",
              ["MULTIPATH.md#5.4", "MULTIPATH.md#6.4"], profile=P,
              overrides={"config": {"max_pipes": 16}})
    mp_handshake(t)
    t.send(0, [PIPE_ACK((0, SENT(0), 0))])
    pids = list(range(2, 32, 2))
    for pid in pids:
        join_up(t, f"c{pid}", pid, "bidi", confirm=False)
    t.adv(25)
    t.frames("*", {"type": "PIPE_PING", "count": 8}, match="unordered")
    t.send(0, [PIPE_ACK(*[(pid, SENT(pid), 0) for pid in pids])])
    t.adv(974)
    t.no_frames("*", ["PIPE_PING"])
    t.adv(51)
    t.frames("*", {"type": "PIPE_PING", "count": 7}, match="unordered")
    t.send(0, [PIPE_ACK(*[(pid, SENT(pid), 0) for pid in pids])])
    out.append(t)

    t = Trace("multipath.status.standby_takeover",
              "When every non-standby pipe has failed, the standby pipe carries the traffic and "
              "the endpoint announces it with PIPE_STATUS", ["MULTIPATH.md#9"],
              profile=P)
    mp_handshake(t)
    join_up(t, "c2", 2, "bidi")
    t.api("set_pipe_status", pipe=2, send_standby=True, receive_standby=True, expect={"ok": True})
    t.frames("*", PIPE_STATUS(2, 1, "standby", 0))
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=b"primary", fin=False, expect={"accepted": 7})
    t.frames(0, SDATA(1, LOCAL_FIRST_PRE + b"primary"))
    t.no_frames(2, ["STREAM"])
    t.down(0, "bidi", "error")
    t.api("write", stream="a", data=b"-takeover", fin=False, expect={"accepted": 9})
    t.frames(2, SDATA(1, LOCAL_FIRST_PRE + b"primary-takeover"),
             PIPE_STATUS(2, 2, "available", 0), PIPE_ABANDON(0, PIPE_FAILED), match="unordered")
    out.append(t)

    t = Trace("multipath.status.standby_restore",
              "A standby takeover is temporary: a JOINING pipe does not end it, and once a "
              "non-standby pipe is usable again the endpoint restores its standby request",
              ["MULTIPATH.md#9", "MULTIPATH.md#5.7"], profile=P)
    mp_handshake(t)
    join_up(t, "c2", 2, "bidi")
    t.api("set_pipe_status", pipe=2, send_standby=True, receive_standby=True,
          expect={"ok": True})
    t.frames("*", PIPE_STATUS(2, 1, "standby", 0))
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=b"primary", fin=False, expect={"accepted": 7})
    t.frames(0, SDATA(1, LOCAL_FIRST_PRE + b"primary"))
    t.down(0, "bidi", "error")
    t.api("write", stream="a", data=b"-takeover", fin=False, expect={"accepted": 9})
    t.frames(2, SDATA(1, LOCAL_FIRST_PRE + b"primary-takeover"),
             PIPE_STATUS(2, 2, "available", 0), PIPE_ABANDON(0, PIPE_FAILED), match="unordered")
    t.note("A new pipe is joined. While it is JOINING it is not usable, so the standby pipe "
           "keeps the data (MULTIPATH.md 5.7 step 3, Section 11 rule S1).")
    t.api("add_pipe", pipe=1, dir="bidi", fate_group=0, expect={"ok": True})
    t.frames(1, join(1, "bidi", locator=D.PEER_LOCATOR, role="responder"), match="first")
    t.state(pipe=1, dir="out", pstate="JOINING")
    off = len(LOCAL_FIRST_PRE)
    t.api("write", stream="a", data=b"-joining", fin=False, expect={"accepted": 8})
    t.frames(2, SDATA(1, b"-joining", off=off + 16))
    t.no_frames(1, ["STREAM"])
    t.send(1, [PIPE_ACK((1, SENT(1), 0))])
    t.state(pipe=1, dir="out", pstate="ACTIVE")
    t.note("Pipe 1 is ACTIVE and, as the test-mode sender has no probation, usable: the takeover "
           "ends and the standby request is restored with the configured priority (on "
           "whichever pipe the control order picks, so it is checked before the data).")
    t.frames("*", PIPE_STATUS(2, 3, "standby", 0))
    t.api("write", stream="a", data=b"-back", fin=False, expect={"accepted": 5})
    t.frames(1, SDATA(1, b"-back", off=off + 24))
    t.no_frames(2, ["STREAM"])
    out.append(t)

    t = Trace("multipath.status.untaken_own_parity",
              "PIPE_STATUS for a pipe ID of the receiver's own parity that the receiver has not "
              "taken is fatal, as for PIPE_ABANDON", ["MULTIPATH.md#9", "MULTIPATH.md#4.3"],
              profile=P)
    mp_handshake(t)
    t.send(0, [PIPE_STATUS(1, 1, "available", 0)])
    t.closed("PROTOCOL_VIOLATION")
    out.append(t)

    t = Trace("multipath.status.request_without_attachment_ignored",
              "A PIPE_STATUS for a peer pipe ID that has no attachment is ignored and not "
              "stored; the joined pipe starts from the request in its PIPE_JOIN",
              ["MULTIPATH.md#9", "MULTIPATH.md#5.1"], profile=P)
    mp_handshake(t)
    t.send(0, [PIPE_STATUS(2, 1, "available", 6)])
    t.is_open()
    join_up(t, "c2", 2, "bidi")
    t.note("Pipe 0 is moved to priority 3 locally; had the early request (6) been kept, pipe 2 "
           "would be the less-preferred one. Its join asked for the default 0, so it takes the "
           "data.")
    t.api("set_pipe_status", pipe=0, send_priority=3, expect={"ok": True})
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=b"x", fin=False, expect={"accepted": 1})
    t.frames(2, SDATA(1, LOCAL_FIRST_PRE + b"x"))
    t.frames(0, {"type": "STREAM", "count": 0}, match="unordered")
    out.append(t)

    t = Trace("multipath.status.change_while_joining_sent_on_active",
              "A joiner states its initial request in PIPE_JOIN; a change made while the "
              "attachment is JOINING is sent as PIPE_STATUS once it is ACTIVE",
              ["MULTIPATH.md#5.7", "MULTIPATH.md#9"], profile=P)
    mp_handshake(t)
    t.api("add_pipe", pipe=1, dir="bidi", fate_group=0, receive_priority=6,
          expect={"ok": True})
    t.frames(1, join(1, "bidi", locator=D.PEER_LOCATOR, role="responder", priority=6),
             match="first")
    t.state(pipe=1, dir="out", pstate="JOINING")
    t.api("set_pipe_status", pipe=1, receive_priority=3, expect={"ok": True})
    t.no_frames("*", ["PIPE_STATUS"])
    t.send(1, [PIPE_ACK((1, SENT(1), 0))])
    t.state(pipe=1, dir="out", pstate="ACTIVE")
    t.frames("*", PIPE_STATUS(1, 1, "available", 3))
    out.append(t)

    # --- pipe priority requests (MULTIPATH.md 9)
    t = Trace("multipath.tier.join_with_priority_local",
              "A local join carries the requested priority for the peer's outbound direction "
              "in its PIPE_JOIN; no PIPE_STATUS is needed", ["MULTIPATH.md#5.1", "MULTIPATH.md#9"],
              profile=P)
    mp_handshake(t)
    t.api("add_pipe", pipe=1, dir="bidi", fate_group=0, send_priority=2, receive_priority=6,
          expect={"ok": True})
    t.frames(1, join(1, "bidi", locator=D.PEER_LOCATOR, role="responder", priority=6),
             match="first")
    t.no_frames("*", ["PIPE_STATUS"])
    out.append(t)

    t = Trace("multipath.join.out_of_band_with_priority",
              "An out-of-band join states the acceptor's priority in the PIPE_JOIN it carries "
              "out of band, before any byte flows", ["MULTIPATH.md#5.1", "MULTIPATH.md#5.3"],
              profile=P)
    mp_handshake(t)
    t.up(carrier="c4", dir="out", via="listener",
         oob_join=F.encode_frame(join(4, "down", priority=6)))
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=b"tier", fin=False, expect={"accepted": 4})
    t.frames(0, SDATA(1, LOCAL_FIRST_PRE + b"tier"))
    t.frames(4, {"type": "STREAM", "count": 0}, match="unordered")
    out.append(t)

    t = Trace("multipath.join.local_resource_limit",
              "An authenticated join beyond the acceptor's pipe capacity is refused with "
              "PIPE_ABANDON(ZMUX_PIPE_RESOURCE_LIMIT), not a session error",
              ["MULTIPATH.md#5.4"], profile=P, overrides={"test": {"pipe_cap": 2}})
    mp_handshake(t)
    join_up(t, "c2", 2, "up")
    t.up(carrier="c4", dir="in", via="listener")
    j4 = join(4, "up")
    t._add(op="peer_send", carrier="c4", records=[[j4]], hex=D.record_bytes([[j4]]))
    t.reject_carrier("c4")
    t.frames(0, PIPE_ABANDON(4, TE("ZMUX_PIPE_RESOURCE_LIMIT")))
    t.is_open()
    out.append(t)

    t = Trace("multipath.listener.per_source_limit",
              "A listener holds at most 8 unauthenticated carriers per source and closes those "
              "that send no first record within 2 s", ["MULTIPATH.md#5.6"], profile=P)
    mp_handshake(t)
    for k in range(1, 9):
        t.up(carrier=f"a{k}", dir="in", via="listener", source="client-a")
    t.up(carrier="a9", dir="in", via="listener", source="client-a")
    t.reject_carrier("a9")
    t.up(carrier="b1", dir="in", via="listener", source="client-b")
    t.adv(1999)
    t.is_open()
    t.adv(1)
    t.reject_carrier("a1")
    t.reject_carrier("b1")
    out.append(t)

    data4 = bytes(i % 239 for i in range(4000))
    n4 = len(LOCAL_FIRST_PRE) + 4000

    t = Trace("multipath.streams.reset_stream_at_waits_for_credit",
              "A RESET_STREAM_AT whose Reliable Size exceeds the stream credit waits until the "
              "credit covers it; its Final Size never exceeds the peer's limit",
              ["SPEC.md#4.3", "SPEC.md#8.2"], profile=P)
    mp_handshake(t)
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=data4, fin=False, expect={"accepted": 4000})
    t.frames(0, SDATA(1, (LOCAL_FIRST_PRE + data4)[:2048]))
    t.api("set_reliable_boundary", stream="a", expect={"ok": True})
    t.api("reset", stream="a", code=5, expect={"ok": True})
    t.no_frames(0, ["RESET_STREAM", "RESET_STREAM_AT"])
    t.send(0, [MAX_STREAM_DATA(1, 8000)])
    t.frames(0, SDATA(1, (LOCAL_FIRST_PRE + data4)[2048:], off=2048),
             RESET_AT(1, 5, n4, n4), match="contains")
    out.append(t)

    t = Trace("multipath.streams.stop_sending_reduces_reliable_size",
              "STOP_SENDING while a reset waits for credit to cover its Reliable Size is "
              "answered with a reset that owes no data and stays within the stream limit",
              ["SPEC.md#4.3", "SPEC.md#8.2"], profile=P)
    mp_handshake(t)
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=data4, fin=False, expect={"accepted": 4000})
    t.frames(0, SDATA(1, (LOCAL_FIRST_PRE + data4)[:2048]))
    t.api("set_reliable_boundary", stream="a", expect={"ok": True})
    t.api("reset", stream="a", code=5, expect={"ok": True})
    t.no_frames(0, ["RESET_STREAM", "RESET_STREAM_AT"])
    t.send(0, [STOP(1, 9)])
    t.note("Final Size 2048: the data already sent, within the stream limit; a "
           "RESET_STREAM_AT then carries a Reliable Size of at most 2048 (RFC 9000 4.5).")
    t.frames(0, {"type": RESET_ANY, "id": 1, "final_size": 2048})
    t.send(0, [MAX_STREAM_DATA(1, 8000)])
    t.no_frames(0, ["STREAM"])
    out.append(t)

    t = Trace("multipath.remove.abandon_own_untaken_id",
              "PIPE_ABANDON for a pipe ID of the receiver's own parity that it has not taken is "
              "fatal", ["MULTIPATH.md#8.1", "MULTIPATH.md#4.3"], profile=P)
    mp_handshake(t)
    t.send(0, [PIPE_ABANDON(1, 0)])
    t.closed("PROTOCOL_VIOLATION")
    out.append(t)
    return out


# =============================================================================
# resume

# =============================================================================
# multipath.lanes

def multipath_lanes():
    out = []
    P = "zmux-mp"
    P0, P8 = "MULTIPATH.md#2.5", "MULTIPATH.md#5.8"

    t = Trace("multipath.lanes.peer_lane_acknowledged", "In a multipath session the peer's "
              "lane is confirmed by an acknowledgement of its PIPE_LANE record, its records "
              "are processed (streams offered in the order their preambles arrive), and its "
              "position is acknowledged next to lane 0's",
              [P0, P8, "MULTIPATH.md#6.2", "MULTIPATH.md#6.3", "SPEC.md#7.2"], profile=P)
    mp_handshake(t, **BOOT_CONN)
    lanes_ready(t)
    peer_lane(t, "l1", 0, 1)
    t.send(0, [STREAM(4, EMPTY_PRE + b"on-lane-0", fin=True)])
    confirm_peer_lanes(t, (0, 0, 0), (0, 1, "l1"))
    t.send(None, [STREAM(0, EMPTY_PRE + b"on-lane-1", fin=True)], carrier="l1")
    t.send(0, [STREAM(8, EMPTY_PRE + b"again", fin=True)])
    t.api("accept_bidi", bind="s4", expect={"stream": 4})
    t.api("read", stream="s4", max=20, expect={"data": b"on-lane-0", "fin": True})
    t.api("accept_bidi", bind="s0", expect={"stream": 0})
    t.api("read", stream="s0", max=20, expect={"data": b"on-lane-1", "fin": True})
    t.adv(25)
    t.frames("*", PIPE_ACK((0, 0, t.peer_sent[0], "*"), (0, 1, t.peer_sent["l1"], "*")),
             match="unordered")
    out.append(t)

    t = Trace("multipath.lanes.ack_releases_own_lane", "Data written while a lane is "
              "unconfirmed stays on lane 0, a stream direction binds to the lane once it is "
              "confirmed, and an acknowledgement of a lane releases only that lane's frames: "
              "when the pipe fails, what lane 0 carried is reinjected",
              [P0, P8, "MULTIPATH.md#6.2", "MULTIPATH.md#7.4"], profile=P)
    mp_handshake(t, peer_mp(zmux_max_lanes=lanes(2)), **BOOT_CONN)
    join_up(t, "c2", 2, "bidi")
    t.writable(2, False)
    t.send(0, [PIPE_ACK((0, SENT(0), 0))])
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=b"aaaa", fin=False, expect={"accepted": 4})
    t.frames(0, PIPE_LANE(0, 1, D.PEER_LANE_KEY), lane=1, match="first")
    t.frames(0, SDATA(1, LOCAL_FIRST_PRE + b"aaaa"))
    t.send(0, [PIPE_ACK((0, 1, SENT(0, lane=1), 0))])
    t.api("open_bidi", bind="b", expect={"ok": True})
    t.api("write", stream="b", data=b"bbbb", fin=False, expect={"accepted": 4})
    t.frames(0, SDATA(5, EMPTY_PRE + b"bbbb"), lane=1)
    t.no_frames(0, ["STREAM"])
    t.send(0, [PIPE_ACK((0, 1, SENT(0, lane=1), 0))])
    t.writable(2, True)
    t.down(0, "bidi", "error")
    t.frames(2, SDATA(1, LOCAL_FIRST_PRE + b"aaaa"), PIPE_ABANDON(0, PIPE_FAILED),
             {"type": "STREAM", "id": 5, "count": 0}, match="unordered")
    t.is_open()
    out.append(t)

    t = Trace("multipath.lanes.other_connection", "A lane with the session's Lane Key that "
              "names a pipe whose lane 0 is on another carrier connection is closed and "
              "nothing else happens", [P8, "MULTIPATH.md#17"], profile=P)
    mp_handshake(t, **BOOT_CONN)
    join_up(t, "c2", 2, "bidi", conn="r")
    peer_lane(t, "lx", 0, 1, conn="r")
    t.reject_carrier("lx")
    peer_lane(t, "ly", 2, 1)
    t.reject_carrier("ly")
    t.is_open()
    out.append(t)

    t = Trace("multipath.lanes.graceful_end_every_lane", "After the peer's PIPE_ABANDON, an "
              "inbound pipe ends gracefully only once every lane has ended at a record "
              "boundary", [P0, "MULTIPATH.md#7.3", "MULTIPATH.md#8.2", "STATE_MACHINE.md#3.3"],
              profile=P)
    mp_handshake(t, **BOOT_CONN)
    join_up(t, "c2", 2, "bidi", conn=LANE_CONN)
    peer_lane(t, "l1", 2, 1)
    t.adv(25)
    t.frames("*", {"type": "PIPE_ACK"}, match="unordered")
    t.send(None, [STREAM(0, EMPTY_PRE + b"x")], carrier="l1")
    t.send(0, [PIPE_ABANDON(2, 0)])
    t.frames("*", PIPE_ABANDON(2, 0), match="unordered")
    t.down(2, "in", "eof")
    t.state(pipe=2, dir="in", pstate="OPEN")
    t.down(carrier="l1", dir="in", how="eof")
    t.state(pipe=2, dir="in", pstate={"oneof": ["ENDED", "RETIRED"]})
    t.no_frames("*", ["PIPE_ABANDON"])
    t.is_open()
    out.append(t)

    t = Trace("multipath.lanes.lane_end_fails_pipe", "An end of a confirmed lane inside a "
              "record, without a graceful removal, fails the whole pipe: the incomplete record "
              "is discarded and the attachment is abandoned",
              [P0, "MULTIPATH.md#7.3", "STATE_MACHINE.md#3.3"], profile=P)
    mp_handshake(t, **BOOT_CONN)
    join_up(t, "c2", 2, "bidi", conn=LANE_CONN)
    peer_lane(t, "l1", 2, 1)
    t.adv(25)
    t.frames("*", {"type": "PIPE_ACK"}, match="unordered")
    t.send(None, [STREAM(0, EMPTY_PRE + b"ab")], carrier="l1")
    t.raw(None, F.encode_record([STREAM(0, b"cd", off=len(EMPTY_PRE) + 2, fin=True)])[:-1],
          carrier="l1")
    t.down(carrier="l1", dir="in", how="eof")
    t.frames("*", PIPE_ABANDON(2, PIPE_FAILED), match="unordered")
    t.state(pipe=2, dir="out", pstate={"oneof": ["FAILED", "RETIRED"]})
    t.api("accept_bidi", bind="s", expect={"stream": 0})
    t.api("read", stream="s", max=10, expect={"data": b"ab", "fin": False})
    t.api("read", stream="s", max=10, expect={"pending": True})
    t.is_open()
    out.append(t)
    return out


def resume():
    out = []
    P = "zmux-mp-resume"
    peer = peer_mp(zmux_resume=60000)
    feats = MP_FEATURES + ["resume"]

    def suspended(t, peer_tp=peer):
        t.handshake(peer_tp, features=feats)
        t.api("open_bidi", bind="a", expect={"ok": True})
        t.api("write", stream="a", data=b"before", fin=False, expect={"accepted": 6})
        t.frames(0, SDATA(1, LOCAL_FIRST_PRE + b"before"))
        t.down(0, "bidi", "error")
        t.event("suspended")
        t.state(session="SUSPENDED")
        t.api("write", stream="a", data=b"-during", fin=False, expect={"accepted": 7})
        t.no_frames("*")

    t = Trace("resume.suspend.on_last_pipe_loss",
              "Losing every pipe suspends the session; writes are buffered",
              ["RESUME.md#4.1", "RESUME.md#4.2"], profile=P)
    suspended(t)
    out.append(t)

    t = Trace("resume.rejoin.first_record_complete_snapshot",
              "After a suspension the joiner's first record reports its positions in a "
              "complete PIPE_ACK snapshot, so only what the peer lacks is re-sent, and the "
              "acceptor's first record carries its own complete snapshot",
              ["RESUME.md#5", "RESUME.md#6", "MULTIPATH.md#5.2", "MULTIPATH.md#5.4"], profile=P)
    suspended(t)
    t.note("The peer received everything on pipe 0, including \"before\": its join record "
           "says so.")
    rec = [[join(2, "bidi"), PIPE_ACK((0, SENT(0), 0)), PIPE_PING()]]
    t.up(carrier="c2", dir="bidi", via="listener")
    t._add(op="peer_send", carrier="c2", records=rec)
    t.event("resumed")
    n = len(LOCAL_FIRST_PRE)
    t.frames(2, PIPE_ACK((0, t.peer_sent[0], "*"), (2, "*", "*")), PIPE_PING(),
             SDATA(1, b"-during", off=n + 6), PIPE_ABANDON(0, PIPE_FAILED), match="unordered")
    out.append(t)

    t = Trace("resume.rejoin.up_only_ping_answered_complete",
              "A PIPE_PING in the first record of a rejoined uplink is answered with a complete "
              "snapshot, on whichever outbound pipe the acceptor has",
              ["MULTIPATH.md#6.3", "RESUME.md#5"], profile=P)
    t.handshake(peer, features=feats)
    t.up(carrier="c4", dir="out", via="listener", oob_join=F.encode_frame(join(4, "down")))
    t.down(0, "bidi", "error")
    t.event("suspended")
    t.frames(4, {"type": "PIPE_ACK"}, PIPE_ABANDON(0, PIPE_FAILED), match="unordered")
    t.note("Pipe 0's final position was reported already; the answer to the PIPE_PING of the "
           "new uplink still lists it, because it must be complete.")
    rec = [[join(6, "up"), PIPE_ACK((4, SENT(4), 0)), PIPE_PING()]]
    t.up(carrier="c6", dir="in", via="listener")
    t._add(op="peer_send", carrier="c6", records=rec)
    t.event("resumed")
    t.frames(4, PIPE_ACK((0, t.peer_sent[0], "*"), (6, "*", "*")), match="unordered")
    out.append(t)

    for name, title, entries in [
        ("peer_ahead", "With resume in use, a PIPE_ACK position beyond what was sent fails "
         "closed", [(0, 10 ** 6, 0)]),
        ("unknown_pipe", "With resume in use, a PIPE_ACK position on a pipe the receiver never "
         "sent on fails closed", [(0, 0, 0), (6, 5, 0)]),
    ]:
        t = Trace(f"resume.fail_closed.{name}", title, ["RESUME.md#7", "MULTIPATH.md#6.2"],
                  profile=P)
        suspended(t)
        rec = [[join(2, "bidi"), PIPE_ACK(*entries)]]
        t.up(carrier="c2", dir="bidi", via="listener")
        t._add(op="peer_send", carrier="c2", records=rec, hex=D.record_bytes(rec))
        t.closed("ZMUX_RESUME_FAILED", wire=True)
        out.append(t)

    t = Trace("resume.grace.expiry",
              "The grace period is the smaller of the two values; the idle timer is stopped "
              "while suspended; expiry closes locally", ["RESUME.md#3", "RESUME.md#4.1",
                                                         "RESUME.md#8"], profile=P)
    suspended(t, peer_mp(zmux_resume=20000, max_idle_timeout=10000))
    t.adv(10000)
    t.state(session="SUSPENDED")
    t.adv(9999)
    t.state(session="SUSPENDED")
    t.adv(1)
    t.closed("ZMUX_RESUME_TIMEOUT", wire=False)
    out.append(t)


    t = Trace("resume.credit.unretired_cap",
              "A peer that joins and drops attachments without ever answering PIPE_ABANDON is "
              "held to max_pipes + 8 unretired attachments, the bootstrap pair included: "
              "further joins are refused with ZMUX_PIPE_RESOURCE_LIMIT, and unilateral "
              "retirement frees the state", ["MULTIPATH.md#5.4", "MULTIPATH.md#8.4",
                                             "RESUME.md#4.3"], profile=P)
    t.handshake(peer, features=feats)
    t.send(0, [PIPE_ACK((0, SENT(0), 0))])
    t.note("Each join is followed by its failure; the peer never answers PIPE_ABANDON. The "
           "bootstrap pair counts: after the join of pipe ID p, 1 + p / 2 attachments are "
           "unretired, and the join of 30 reaches the cap of 16.")
    for pid in range(2, 32, 2):
        join_up(t, f"c{pid}", pid, "up")
        t.down(pid, "in", "error")
        t.frames("*", PIPE_ABANDON(pid, PIPE_FAILED))
    t.up(carrier="c32", dir="in", via="listener")
    j32 = join(32, "up")
    t._add(op="peer_send", carrier="c32", records=[[j32]], hex=D.record_bytes([[j32]]))
    t.reject_carrier("c32")
    t.frames(0, PIPE_ABANDON(32, TE("ZMUX_PIPE_RESOURCE_LIMIT")))
    t.is_open()
    t.send(0, [PIPE_ACK((0, SENT(0), 0))])
    t.note("Every PIPE_ABANDON is acknowledged and none is answered: max(2 s, 3 x PTO) = 2 s "
           "later (SPEC.md 1.3) the failed attachments are retired unilaterally and only the "
           "bootstrap pair remains, so the next join is accepted.")
    t.adv(2000)
    join_up(t, "c34", 34, "up")
    t.state(pipe=34, dir="in", pstate="OPEN")
    t.no_frames("*", ["PIPE_ABANDON"])
    t.is_open()
    out.append(t)

    t = Trace("resume.rejoin.cut_off_rejoins_still_resume",
              "While suspended at the cap of unretired attachments, a rejoin is not refused: "
              "the oldest failed attachment is retired locally, so nine rejoins that are cut "
              "off right after they connect do not stop the tenth from resuming the session",
              ["MULTIPATH.md#5.4", "RESUME.md#4.3", "RESUME.md#4.4"], profile=P,
              overrides={"config": {"max_pipes": 1}})
    t.handshake(peer, features=feats)
    t.send(0, [PIPE_ACK((0, SENT(0), 0))])
    t.down(0, "bidi", "error")
    t.event("suspended")
    t.note("max_pipes = 1: the cap is 9 unretired attachments of the peer, and the failed "
           "bootstrap pair is the first. Each rejoin briefly gives the acceptor a pipe, so it "
           "leaves SUSPENDED, writes its first record (its complete PIPE_ACK snapshot, a "
           "PIPE_PING and its pending PIPE_ABANDON frames) and suspends again when the "
           "carrier is cut. No PIPE_ABANDON is ever acknowledged.")
    RL = TE("ZMUX_PIPE_RESOURCE_LIMIT")
    for pid in range(2, 20, 2):
        t.up(carrier=f"c{pid}", dir="bidi", via="listener")
        rec = [[join(pid, "bidi")]]
        t._add(op="peer_send", carrier=f"c{pid}", records=rec, hex=D.record_bytes(rec))
        t.peer_sent[pid] = len(D.record_bytes(rec))
        t.event("resumed")
        t.adv(25)
        pats = [{"type": "PIPE_ACK"}, PIPE_PING(),
                {"type": "PIPE_ABANDON", "code": RL, "count": 0}]
        if pid == 18:
            t.note("Rejoin 9 arrives at the cap: the oldest failed attachment (the bootstrap "
                   "pair) is retired locally and the join is accepted.")
        t.frames(pid, *pats, match="unordered")
        t.down(pid, "bidi", "error")
        t.event("suspended")
        t.adv(125)
    t.note("Rejoins come 150 ms apart, so the first record of each can carry its PIPE_PING "
           "within the limit of 8 per second (MULTIPATH.md 6.4). The tenth rejoin stays up: it "
           "retires pipe 2 locally, and the peer acknowledges.")
    t.up(carrier="c20", dir="bidi", via="listener")
    rec = [[join(20, "bidi")]]
    t._add(op="peer_send", carrier="c20", records=rec, hex=D.record_bytes(rec))
    t.peer_sent[20] = len(D.record_bytes(rec))
    t.event("resumed")
    t.adv(25)
    t.frames(20, {"type": "PIPE_ACK"}, PIPE_PING(),
             {"type": "PIPE_ABANDON", "code": RL, "count": 0}, match="unordered")
    t.send(20, [PIPE_ACK((20, SENT(20), 0))])
    t.adv(1000)
    t.send(20, [PIPE_ACK((20, SENT(20), 0))])
    t.state(session="ACTIVE")
    t.state(pipe=20, dir="out", pstate="ACTIVE")
    t.is_open()
    out.append(t)

    t = Trace("resume.rejoin.live_one_way_attachment_displaced",
              "While suspended at the cap of unretired attachments with none failed, a rejoin "
              "that provides the missing direction displaces the live one-way attachment with "
              "the oldest last progress instead of being refused",
              ["MULTIPATH.md#5.4", "RESUME.md#4.3"], profile=P,
              overrides={"test": {"pipe_cap": 2}})
    t.handshake(peer, features=feats)
    join_up(t, "c2", 2, "up")
    t.note("The peer removes the bootstrap pair gracefully: once the exchange completes it is "
           "retired, and the acceptor, left with the uplink 2 only, has no outbound pipe.")
    t.send(2, [PIPE_ABANDON(0, 0)])
    t.frames("*", PIPE_ABANDON(0, 0), match="contains")
    t.send(2, [PIPE_ACK((0, SENT(0), 0))])
    t.pipe_closed(0, "out", "graceful")
    t.event("suspended")
    t.down(0, "in", "eof")
    t.state(session="SUSPENDED")
    t.note("A second uplink brings the peer to the cap of 2 live attachments; neither can "
           "carry what the acceptor would send.")
    join_up(t, "c4", 4, "up")
    t.state(session="SUSPENDED")
    t.adv(100)
    t.send(4, [PADDING(1)])
    t.note("A bidirectional rejoin arrives at the cap with no failed attachment: the uplink "
           "with the oldest last progress (2) is failed and retired locally without "
           "PIPE_ABANDON, and the join is accepted.")
    t.up(carrier="c6", dir="bidi", via="listener")
    rec = [[join(6, "bidi")]]
    t._add(op="peer_send", carrier="c6", records=rec, hex=D.record_bytes(rec))
    t.peer_sent[6] = len(D.record_bytes(rec))
    t.pipe_closed(2, "in", "any")
    t.event("resumed")
    t.adv(25)
    t.frames(6, PIPE_ACK((4, t.peer_sent[4], "*"), (6, "*", "*")), PIPE_PING(),
             match="unordered")
    t.no_frames("*", ["PIPE_ABANDON"])
    t.send(6, [PIPE_ACK((6, SENT(6), 0))])
    t.state(session="ACTIVE")
    t.state(pipe=4, dir="in", pstate="OPEN")
    t.is_open()
    out.append(t)

    t = Trace("resume.suspend.draining_survives_suspension",
              "A session that was DRAINING returns to DRAINING after resumption",
              ["RESUME.md#4.1"], profile=P)
    t.handshake(peer, features=feats)
    t.send(0, [GOAWAY(4, 4, 0)])
    t.state(session="DRAINING")
    t.down(0, "bidi", "error")
    t.state(session="SUSPENDED")
    rec = [[join(2, "bidi"), PIPE_ACK((0, SENT(0), 0))]]
    t.up(carrier="c2", dir="bidi", via="listener")
    t._add(op="peer_send", carrier="c2", records=rec)
    t.event("resumed")
    t.state(session="DRAINING")
    out.append(t)

    t = Trace("resume.rejoin.old_pipe_id_replay_rejected",
              "Pipe IDs used before the suspension are never accepted again", ["RESUME.md#10"],
              profile=P)
    t.handshake(peer, features=feats)
    join_up(t, "c2", 2, "up")
    t.down(2, "in", "error")
    t.down(0, "bidi", "error")
    t.state(session="SUSPENDED")
    t.up(carrier="c2-again", dir="in", via="listener")
    j = join(2, "up")
    t._add(op="peer_send", carrier="c2-again", records=[[j]], hex=D.record_bytes([[j]]))
    t.reject_carrier("c2-again")
    t.state(session="SUSPENDED")
    out.append(t)

    t = Trace("resume.suspend.half_suspended_keeps_reading",
              "An endpoint that lost its outbound pipes but kept an inbound one suspends and keeps "
              "delivering what arrives", ["RESUME.md#4.2"], profile=P)
    t.handshake(peer, features=feats)
    join_up(t, "c2", 2, "up")
    t.down(0, "bidi", "error")
    t.event("suspended")
    t.state(session="SUSPENDED")
    t.send(2, [STREAM(0, EMPTY_PRE + b"still-arriving", fin=True)])
    t.api("accept_bidi", bind="s", expect={"stream": 0})
    t.api("read", stream="s", max=20, expect={"data": b"still-arriving", "fin": True})
    t.no_frames("*")
    t.state(session="SUSPENDED")
    out.append(t)

    t = Trace("resume.suspend.half_suspended_keeps_sending",
              "An endpoint that lost its inbound pipes but kept an outbound one suspends, "
              "reports the final positions at once, keeps sending, and does not fail the pipe "
              "for lack of acknowledgements", ["RESUME.md#4.2", "MULTIPATH.md#6.3",
                                              "MULTIPATH.md#7.1"],
              profile=P)
    t.handshake(peer, features=feats)
    t.up(carrier="c4", dir="out", via="listener", oob_join=F.encode_frame(join(4, "down")))
    t.down(0, "bidi", "error")
    t.event("suspended")
    t.state(session="SUSPENDED")
    t.frames(4, PIPE_ACK((0, t.peer_sent[0], "*")), PIPE_ABANDON(0, PIPE_FAILED),
             match="unordered")
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=b"half", fin=False, expect={"accepted": 4})
    t.frames(4, SDATA(1, LOCAL_FIRST_PRE + b"half"))
    t.adv(5000)
    t.state(pipe=4, dir="out", pstate="ACTIVE")
    t.state(session="SUSPENDED")
    out.append(t)


    t = Trace("resume.suspend.application_close",
              "An application close while suspended without pipes ends the session locally",
              ["RESUME.md#11"], profile=P)
    suspended(t)
    t.api("close", code=5, reason=b"", app=True, expect={"ok": True})
    t.closed(5, space="application", remote=False, wire=False)
    out.append(t)
    return out


# =============================================================================
# regressions

def regression():
    out = []
    t = Trace("regression.stop_race_is_stream_error",
              "STOP_SENDING racing a local finish is a stream error, never a session error",
              ["SPEC.md#9.3"])
    t.handshake(peer_small())
    t.api("open_bidi", bind="a", expect={"ok": True})
    t.api("write", stream="a", data=b"x", fin=False, expect={"accepted": 1})
    t.frames(0, SDATA(1, LOCAL_FIRST_PRE + b"x"))
    t.send(0, [STOP(1, 7)])
    t.api("finish", stream="a", expect=err("stream_stopped", code=7, remote=True))
    t.frames(0, {"type": RESET_ANY, "id": 1, "code": 7})
    t.no_frames(0, ["CONNECTION_CLOSE"])
    t.is_open()
    out.append(t)

    t = Trace("regression.reset_does_not_refund_session_credit",
              "Bytes of a reset stream stay counted against the peer's session limit",
              ["SPEC.md#8.2"])
    t.handshake(peer_small(initial_max_stream_data_bidi_remote=8192, initial_max_data=4096))
    oi = b"o" * 300
    pa = pre(open_info=oi, grease=True)
    payload = bytes(range(200))
    t.api("open_bidi", bind="a", open_info=oi, expect={"ok": True})
    t.api("write", stream="a", data=payload, fin=False, expect={"accepted": 200})
    t.frames(0, SDATA(1, pa + payload))
    t.api("reset", stream="a", code=CANCELLED, expect={"ok": True})
    t.frames(0, {"type": "RESET_STREAM_AT", "id": 1, "code": CANCELLED,
                 "final_size": len(pa) + 200, "reliable_size": {"min": len(pa)}})
    big = bytes(i % 241 for i in range(5000))
    t.api("open_bidi", bind="b", expect={"ok": True})
    t.api("write", stream="b", data=big, fin=False, expect={"accepted": 5000})
    allowed = 4096 - (len(pa) + 200)
    t.frames(0, SDATA(5, (EMPTY_PRE + big)[:allowed]))
    out.append(t)

    t = Trace("regression.peer_reset_code_zero",
              "A peer reset with code 0 is a stream reset, not a session close",
              ["STATE_MACHINE.md#2.5"])
    t.handshake(peer_small())
    t.send(0, [STREAM(0, EMPTY_PRE + b"ab"), RESET_AT(0, 0, 3, 3)])
    t.api("accept_bidi", bind="s", expect={"stream": 0})
    t.api("read", stream="s", max=10, expect={"data": b"ab"})
    t.api("read", stream="s", max=10, expect=err("stream_reset", code=0, remote=True))
    t.is_open()
    out.append(t)
    return out


GROUPS = [core_handshake, core_records, core_streams, core_flow, core_robustness, core_close,
          core_reset_stream_at, core_pipe_ack, core_lanes, ext_role, ext_goaway, ext_preamble,
          ext_priority, ext_datagram,
          compat, multipath, multipath_lanes, resume, regression]


def all_traces():
    traces = []
    for g in GROUPS:
        traces.extend(g())
    ids = [t.id for t in traces]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        raise SystemExit(f"duplicate trace ids: {sorted(dup)}")
    return traces


def main():
    base = ROOT / "fixtures" / "traces"
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True)
    (ROOT / "fixtures" / "profiles.json").write_text(
        json.dumps(D.profiles_json(), indent=2) + "\n")
    index = []
    for t in all_traces():
        parts = t.id.split(".")
        path = base.joinpath(*parts[:-1], parts[-1] + ".ndjson")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(t.to_lines())
        index.append({"id": t.id, "file": str(path.relative_to(ROOT)), "title": t.header["title"],
                      "refs": t.header["refs"],
                      "profile": t.profile, "steps": len(t.steps)})
    (base / "index.json").write_text(json.dumps({"traces": index}, indent=2) + "\n")
    print(f"traces: {len(index)} in fixtures/traces")


if __name__ == "__main__":
    main()
