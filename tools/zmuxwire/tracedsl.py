"""A small DSL that writes executable traces (TRACES.md)."""

import json

from . import frames as F
from . import crypto as C
from . import preamble as P
from . import registry as R

TE = R.transport_error
AE = R.app_error

# --------------------------------------------------------------------------
# deterministic test values

PEER_NONCE = bytes.fromhex("00112233445566778899aabbccddeeff")
LOCAL_NONCE = bytes.fromhex("ffeeddccbbaa99887766554433221100")
PSK = b"zmux-trace-psk-0123456789abcdef!"
PEER_LOCATOR = b"PEER"
LOCAL_LANE_KEY = b"zmux-lane-key-LL"     # the local endpoint's Lane Key (test hook lane_key)
PEER_LANE_KEY = b"zmux-lane-key-PP"
LOCAL_LOCATOR = b"LOCAL"
GREASE_TLV_TYPE = 0x21

SMALL_TP = {
    "max_idle_timeout": 30000,
    "initial_max_data": 8192,
    "initial_max_stream_data_bidi_local": 2048,
    "initial_max_stream_data_bidi_remote": 2048,
    "initial_max_stream_data_uni": 2048,
    "initial_max_streams_bidi": 4,
    "initial_max_streams_uni": 4,
    "reset_stream_at": None,
    "zmux_goaway": None,
    "zmux_preamble": 512,
    "zmux_priority": None,
    "max_record_size": 65535,
}
TEST_HOOKS = {"grease_tp": {"id": 27, "value": ""},
              "grease_tlv": {"type": GREASE_TLV_TYPE, "value": ""},
              "ping_seq_start": 0, "lane_key": LOCAL_LANE_KEY}


def lanes(n, key=PEER_LANE_KEY):
    """A zmux_max_lanes value (MULTIPATH.md 3.6); the peer's Lane Key by default."""
    return {"max_lanes": n, "lane_key": key}


# What the local endpoint adds to SMALL_TP: zmux endpoints send zmux_pipe_ack and
# zmux_max_lanes in every session (SPEC.md 5.5); 4 lanes instead of 258 keep Max Lanes
# one byte long.
LOCAL_SMALL_TP = dict(SMALL_TP, zmux_pipe_ack=None, zmux_max_lanes=lanes(4, LOCAL_LANE_KEY))


def _mp_tp(nonce, locator):
    return {"flags": C.FLAG_P, "nonce": nonce, "locator": locator}


PROFILES = {
    "zmux-default": {
        "tp": {"max_idle_timeout": 30000, "initial_max_data": 1048576,
               "initial_max_stream_data_bidi_local": 262144,
               "initial_max_stream_data_bidi_remote": 262144,
               "initial_max_stream_data_uni": 262144, "initial_max_streams_bidi": 256,
               "initial_max_streams_uni": 16, "reset_stream_at": None, "zmux_goaway": None,
               "zmux_preamble": 4096, "zmux_priority": None, "zmux_pipe_ack": None,
               "zmux_max_lanes": lanes(258, LOCAL_LANE_KEY), "max_record_size": 65535},
        "config": {"keep_alive_period": 0, "handshake_timeout": 10000,
                   "max_incoming_uni_streams": None},
        "test": dict(TEST_HOOKS),
    },
    "zmux-small": {"tp": dict(LOCAL_SMALL_TP),
                   "config": {"keep_alive_period": 0, "handshake_timeout": 10000,
                              "max_incoming_uni_streams": 4},
                   "test": dict(TEST_HOOKS)},
    "zmux-small-dgram": {"tp": dict(LOCAL_SMALL_TP, max_datagram_frame_size=1200,
                                    zmux_datagram_flows=None),
                         "config": {"keep_alive_period": 0, "handshake_timeout": 10000,
                                    "enable_datagrams": True, "max_incoming_uni_streams": 4},
                         "test": dict(TEST_HOOKS)},
    "zmux-role-auto": {"tp": dict(LOCAL_SMALL_TP, zmux_role={"role": "auto", "nonce": 1000}),
                       "config": {"keep_alive_period": 0, "handshake_timeout": 10000,
                                  "role": "auto", "max_incoming_uni_streams": 4},
                       "test": dict(TEST_HOOKS, role_nonce=1000)},
    "zmux-mp": {"tp": dict(LOCAL_SMALL_TP, zmux_multipath=_mp_tp(LOCAL_NONCE, LOCAL_LOCATOR),
                           zmux_ack_threshold=4096),
                "config": {"keep_alive_period": 0, "handshake_timeout": 10000, "max_pipes": 8,
                           "sender": "test-mode", "pipe_auth": {"psk": PSK},
                           "max_incoming_uni_streams": 4},
                "test": dict(TEST_HOOKS, session_nonce=LOCAL_NONCE, rtt_ms=10)},
    "zmux-mp-resume": {"tp": dict(LOCAL_SMALL_TP, zmux_multipath=_mp_tp(LOCAL_NONCE, LOCAL_LOCATOR),
                                  zmux_ack_threshold=4096, zmux_resume=30000),
                       "config": {"keep_alive_period": 0, "handshake_timeout": 10000,
                                  "max_pipes": 8, "sender": "test-mode",
                                  "pipe_auth": {"psk": PSK}, "resume_timeout": 30000,
                                  "max_incoming_uni_streams": 4},
                       "test": dict(TEST_HOOKS, session_nonce=LOCAL_NONCE, rtt_ms=10)},
}


def profiles_json():
    return F.to_json(PROFILES)


# --------------------------------------------------------------------------
# frame constructors

def STREAM(id, data=b"", off=0, fin=False, **kw):
    return dict(type="STREAM", id=id, off=off, data=data, fin=fin, **kw)


def SDATA(id, data, fin=None, off=None):
    d = dict(type="STREAM_DATA", id=id, data=data)
    if off is not None:
        d["off"] = off
    if fin is not None:
        d["fin"] = fin
    return d


def RESET(id, code, final_size):
    return dict(type="RESET_STREAM", id=id, code=code, final_size=final_size)


def RESET_AT(id, code, final_size, reliable_size):
    return dict(type="RESET_STREAM_AT", id=id, code=code, final_size=final_size,
                reliable_size=reliable_size)


def STOP(id, code):
    return dict(type="STOP_SENDING", id=id, code=code)


def MAX_DATA(m):
    return dict(type="MAX_DATA", max=m)


def MAX_STREAM_DATA(id, m):
    return dict(type="MAX_STREAM_DATA", id=id, max=m)


def MAX_STREAMS(m, bidi=True):
    return dict(type="MAX_STREAMS", bidi=bidi, max=m)


def DATA_BLOCKED(limit):
    return dict(type="DATA_BLOCKED", limit=limit)


def STREAM_DATA_BLOCKED(id, limit):
    return dict(type="STREAM_DATA_BLOCKED", id=id, limit=limit)


def STREAMS_BLOCKED(limit, bidi=True):
    return dict(type="STREAMS_BLOCKED", bidi=bidi, limit=limit)


def CLOSE(code, app=False, reason=b"", frame_type=0):
    f = dict(type="CONNECTION_CLOSE", app=app, code=code, reason=reason)
    if not app:
        f["frame_type"] = frame_type
    return f


def PING(seq):
    return dict(type="QX_PING", response=False, seq=seq)


def PONG(seq):
    return dict(type="QX_PING", response=True, seq=seq)


def PADDING(n=1):
    return dict(type="PADDING", count=n)


def DATAGRAM(data, has_len=True):
    return dict(type="DATAGRAM", data=data, has_len=has_len)


def GOAWAY(b, u, code=0):
    return dict(type="GOAWAY", max_bidi=b, max_uni=u, code=code)


def PRIO(id, seq, urgency, incremental=False):
    return dict(type="PRIORITY_UPDATE", id=id, seq=seq, urgency=urgency,
                incremental=incremental)


def PIPE_ACK(*entries):
    """Entries are (pipe, lane, position, delay), or (pipe, position, delay) for lane 0."""
    out = []
    for e in entries:
        p, lane, pos, d = e if len(e) == 4 else (e[0], 0, e[1], e[2])
        out.append(dict(pipe=p, lane=lane, position=pos, delay=d))
    return dict(type="PIPE_ACK", entries=out)


def PIPE_LANE(pipe, lane, key=LOCAL_LANE_KEY):
    """A PIPE_LANE toward the local endpoint by default; key=PEER_LANE_KEY for its own."""
    return dict(type="PIPE_LANE", lane_key=key, pipe=pipe, lane=lane)


def PIPE_PING():
    return dict(type="PIPE_PING")


def PIPE_ABANDON(pipe, code):
    return dict(type="PIPE_ABANDON", pipe=pipe, code=code)


def PIPE_STATUS(pipe, seq, status, priority=0):
    return dict(type="PIPE_STATUS", pipe=pipe, seq=seq, status=status, priority=priority)


def RAW(b):
    return dict(type="RAW", bytes=b)


def REPEAT(n, frame):
    return dict(type="REPEAT", count=n, frame=frame)


def TP(params):
    return dict(type="QX_TRANSPORT_PARAMETERS", params=params)


def tp_list(d, grease=(58, b"\xab\xcd")):
    out = []
    for k, v in d.items():
        out.append({"id": k, "value": v})
    if grease:
        out.append({"id": grease[0], "raw": grease[1]})
    return out


# --------------------------------------------------------------------------
# preambles

def pre(open_info=None, priority=None, grease=False, extra=()):
    tlvs = []
    if open_info is not None:
        tlvs.append((0x01, open_info))
    if priority is not None:
        u, inc = priority
        tlvs.append((0x02, bytes([(u << 5) | (0x10 if inc else 0)])))
    tlvs.extend(extra)
    if grease:
        tlvs.append((GREASE_TLV_TYPE, b""))
    return P.encode_preamble(tlvs)


EMPTY_PRE = pre()


# --------------------------------------------------------------------------
# peer transport parameter sets

def peer_small(**over):
    """A scripted peer with zmux parameters and the small values. It does not send
    zmux_pipe_ack and advertises less than the early-data floor (SPEC.md 6.1.2), which
    a conforming zmux endpoint would not do, so that traces of other features stay
    small and see no PIPE_ACK; peer_ack() adds zmux_pipe_ack."""
    d = dict(SMALL_TP)
    d.update(over)
    return d


def peer_plain(**over):
    d = {"initial_max_data": 8192, "initial_max_stream_data_bidi_local": 2048,
         "initial_max_stream_data_bidi_remote": 2048, "initial_max_stream_data_uni": 2048,
         "initial_max_streams_bidi": 4, "initial_max_streams_uni": 4}
    d.update(over)
    return d


def peer_ack(**over):
    """A zmux peer that also sends zmux_pipe_ack (MULTIPATH.md 3.5)."""
    d = peer_small(zmux_pipe_ack=None)
    d.update(over)
    return d


def peer_mp(**over):
    d = peer_small(zmux_multipath=_mp_tp(PEER_NONCE, PEER_LOCATOR))
    d.update(over)
    return d


def secret():
    # peer is the initiator, local the responder in multipath traces
    return C.session_secret(PEER_NONCE, LOCAL_NONCE, b"", PSK)


def join(pipe, direction="up", fate_group=0, locator=LOCAL_LOCATOR, role="initiator",
         carrier_binding=b"", bad_token=False, carrier_bound=False, priority=0, standby=False):
    f = dict(type="PIPE_JOIN", locator=locator, pipe=pipe, direction=direction,
             carrier_bound=carrier_bound, standby=standby, fate_group=fate_group,
             priority=priority, token=b"\x00" * 16)
    f["token"] = C.join_token(secret(), role, f, carrier_binding)
    if bad_token:
        t = bytearray(f["token"])
        t[0] ^= 0x01
        f["token"] = bytes(t)
    return f


def record_bytes(records):
    return b"".join(F.encode_record(r) for r in records)


def has_placeholder(obj):
    if isinstance(obj, dict):
        if "$sent" in obj:
            return True
        return any(has_placeholder(v) for v in obj.values())
    if isinstance(obj, list):
        return any(has_placeholder(v) for v in obj)
    return False


# A resolved {"$sent": ...} value is written as a 4-byte variable-length integer
# (TRACES.md 6.1), so a record's length does not depend on the local endpoint.
PLACEHOLDER_STANDIN = 1 << 14


def _stand_in(obj):
    if isinstance(obj, dict):
        if "$sent" in obj:
            return PLACEHOLDER_STANDIN
        return {k: _stand_in(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_stand_in(v) for v in obj]
    return obj


def placeholder_record_len(records):
    """Byte length of records whose placeholders the runner fills in (4-byte varints)."""
    return len(record_bytes(_stand_in(records)))


def SENT(pipe, plus=0, lane=0):
    d = {"$sent": pipe}
    if lane:
        d["lane"] = lane
    if plus:
        d["plus"] = plus
    return d


# --------------------------------------------------------------------------
# trace builder

class Trace:
    def __init__(self, id, title, refs, profile="zmux-small", local_role="responder",
                 overrides=None):
        self.id = id
        self.header = {"kind": "trace", "id": id, "title": title, "profile": profile,
                       "local_role": local_role, "overrides": overrides or {},
                       "refs": list(refs),
                       "groups": id.split(".")[:-1]}
        self.steps = []
        self.peer_sent = {}      # pipe -> bytes the peer has delivered
        self.profile = profile

    def _add(self, **kw):
        if kw.get("op") == "peer_send" and "hex" not in kw and kw.get("records") is not None \
                and has_placeholder(kw["records"]):
            key = kw.get("pipe")
            if key is None:
                first = kw["records"][0][0] if kw["records"] and kw["records"][0] else {}
                key = first.get("pipe") if first.get("type") == "PIPE_JOIN" else kw.get("carrier")
            self.peer_sent[key] = self.peer_sent.get(key, 0) + \
                placeholder_record_len(kw["records"])
        self.steps.append(kw)
        return self

    # carriers
    def up(self, pipe=None, dir="bidi", via=None, **kw):
        d = dict(op="pipe_up")
        if pipe is not None:
            d["pipe"] = pipe
        d["dir"] = dir
        if via:
            d["via"] = via
        d.update(kw)
        return self._add(**d)

    def lane_up(self, carrier, conn):
        """A new inbound stream that the peer opened on carrier connection ``conn``."""
        return self._add(op="pipe_up", carrier=carrier, dir="in", via="lane", conn=conn)

    def down(self, pipe=None, dir="bidi", how="error", carrier=None, lane=0):
        if carrier is not None:
            return self._add(op="pipe_down", carrier=carrier, dir=dir, how=how)
        return self._add(op="pipe_down", pipe=pipe, **({"lane": lane} if lane else {}),
                         dir=dir, how=how)

    def writable(self, pipe, w):
        return self._add(op="set_writable", pipe=pipe, writable=w)

    # peer
    def send(self, pipe, *records, chunks=None, invalid=False, carrier=None):
        """Deliver records on inbound pipe ``pipe``, or on carrier ``carrier`` (pipe None)."""
        recs = [list(r) for r in records]
        key = pipe if carrier is None else carrier
        d = dict(op="peer_send", **({"pipe": pipe} if carrier is None else {"carrier": carrier}),
                 records=recs)
        if invalid:
            d["invalid"] = True
        if not has_placeholder(recs):
            b = record_bytes(recs)
            d["hex"] = b
            self.peer_sent[key] = self.peer_sent.get(key, 0) + len(b)
        if chunks:
            d["chunks"] = chunks
        return self._add(**d)

    def send_messages(self, pipe, *messages, raw=None, carrier=None):
        """Deliver whole messages on a records-framed carrier (SPEC.md 3.4)."""
        d = dict(op="peer_send")
        if carrier is not None:
            d["carrier"] = carrier
        else:
            d["pipe"] = pipe
        if raw is not None:
            d["messages_hex"] = [bytes(m).hex() for m in raw]
            key = carrier if carrier is not None else pipe
            self.peer_sent[key] = self.peer_sent.get(key, 0) + sum(
                F.message_position(m) for m in raw)
            return self._add(**d)
        msgs = [list(m) for m in messages]
        d["messages"] = msgs
        enc = [F.encode_frames(m) for m in msgs]
        d["messages_hex"] = [e.hex() for e in enc]
        key = carrier if carrier is not None else pipe
        self.peer_sent[key] = self.peer_sent.get(key, 0) + sum(F.message_position(e) for e in enc)
        return self._add(**d)

    def raw(self, pipe, b, chunks=None, carrier=None):
        key = pipe if carrier is None else carrier
        d = dict(op="peer_send", **({"pipe": pipe} if carrier is None else {"carrier": carrier}),
                 hex=b)
        self.peer_sent[key] = self.peer_sent.get(key, 0) + len(b)
        if chunks:
            d["chunks"] = chunks
        return self._add(**d)

    # application
    def api(self, call, expect=None, bind=None, **args):
        d = dict(op="api", call=call, args=args)
        if bind:
            d["bind"] = bind
        if expect is not None:
            d["expect"] = expect
        return self._add(**d)

    # expectations
    def frames(self, pipe, *patterns, match="contains", one_credit_per_record=False, lane=0):
        d = dict(op="expect_frames", pipe=pipe, **({"lane": lane} if lane else {}),
                 match=match, frames=list(patterns))
        if one_credit_per_record:
            d["one_credit_per_record"] = True
        return self._add(**d)

    def no_frames(self, pipe="*", types=None, lane=0):
        d = dict(op="expect_no_frames", pipe=pipe)
        if lane:
            d["lane"] = lane
        if types:
            d["types"] = list(types)
        return self._add(**d)

    def closed(self, code, space="transport", wire="default", remote=None):
        # A locally detected error or a local close MUST send CONNECTION_CLOSE
        # (SPEC.md Section 6.5); echoing a peer's close is optional and local
        # reasons send none (or, in multipath sessions, a NO_ERROR close).
        if wire == "default":
            wire = None if (remote or space == "local") else True
        err = {"space": space, "code": code}
        if remote is not None:
            err["remote"] = remote
        if wire is None:
            return self._add(op="expect_close", error=err)
        return self._add(op="expect_close", error=err, wire=wire)

    def is_open(self):
        return self._add(op="expect_open")

    def state(self, session=None, pipe=None, dir=None, pstate=None):
        d = dict(op="expect_state")
        if session:
            d["session"] = session
        if pipe is not None:
            d.update(pipe=pipe, dir=dir, state=pstate)
        return self._add(**d)

    def event(self, name, **fields):
        return self._add(op="expect_event", event=name, fields=fields)

    def pipe_closed(self, pipe, dir="out", how="any", lane=0):
        return self._add(op="expect_pipe_closed", pipe=pipe, **({"lane": lane} if lane else {}),
                         dir=dir, how=how)

    def reject_carrier(self, carrier):
        return self._add(op="expect_reject_carrier", carrier=carrier)

    def adv(self, ms):
        return self._add(op="advance", ms=ms)

    def note(self, text):
        return self._add(op="note", text=text)

    def local_tp(self):
        """The local transport parameters: profile values plus header overrides."""
        prof = dict(PROFILES[self.profile]["tp"])
        prof.update(self.header["overrides"].get("tp", {}))
        return prof

    def expect_local_tp(self):
        return self.frames(0, dict(type="QX_TRANSPORT_PARAMETERS",
                                   params_include=F.to_json(self.local_tp()), grease=True),
                           match="first")

    # handshake helper
    def handshake(self, peer_tps, grease=(58, b"\xab\xcd"), features=None, role=None,
                  **carrier):
        self.up(0, "bidi", via="bootstrap", **carrier)
        self.expect_local_tp()
        self.send(0, [TP(tp_list(peer_tps, grease))])
        ev = {}
        if features is not None:
            ev["features"] = features
        if role is not None:
            ev["role"] = role
        self.event("established", **ev)
        return self

    def to_lines(self):
        lines = [json.dumps(F.to_json(self.header), sort_keys=False)]
        for i, s in enumerate(self.steps, 1):
            s = dict(s)
            s = {"step": i, **s}
            lines.append(json.dumps(F.to_json(s)))
        return "\n".join(lines) + "\n"
