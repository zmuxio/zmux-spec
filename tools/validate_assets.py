#!/usr/bin/env python3
"""Validate the zmux specification assets.

Checks:
  1. registry: source, assets/registry.json and REGISTRY.md agree; every zmux
     codepoint obeys REGISTRY.md Section 2; documents quote the same values;
  2. WIRE_EXAMPLES.md: every example re-encodes to its bytes and decodes back;
     key-schedule vectors are recomputed;
  3. wire corpus: valid entries round-trip, invalid entries fail with the
     expected error;
  4. traces: schema, byte consistency of peer records, references, index;
  5. documents: relative links and section references resolve.

Exit status 0 when everything passes.
"""

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from zmuxwire import frames as F, registry as R, render, crypto as C  # noqa: E402
from zmuxwire.varint import DecodeError  # noqa: E402

ERRORS = []
CHECKS = {"registry": 0, "examples": 0, "corpus": 0, "traces": 0, "steps": 0, "docs": 0}

DOCS = ["README.md", "SPEC.md", "MULTIPATH.md", "RESUME.md", "EXTENSIONS.md", "STATE_MACHINE.md", "REGISTRY.md", "WIRE_EXAMPLES.md", "CONFORMANCE.md", "TRACES.md"]


def fail(msg):
    ERRORS.append(msg)


def read(name):
    return (ROOT / name).read_text()


# =============================================================================
# documents: sections and links

def sections(md):
    out = set()
    for line in md.splitlines():
        m = re.match(r"^#{2,4}\s+(\d+(?:\.\d+)*)\.?\s", line)
        if m:
            out.add(m.group(1))
    return out


SECTION_CACHE = {}


def ref_ok(ref):
    if "#" not in ref:
        return (ROOT / ref).exists()
    doc, sec = ref.split("#", 1)
    if not (ROOT / doc).exists():
        return False
    if doc not in SECTION_CACHE:
        SECTION_CACHE[doc] = sections(read(doc))
    return sec in SECTION_CACHE[doc]


def check_docs():
    for d in DOCS:
        if not (ROOT / d).exists():
            fail(f"docs: missing {d}")
            continue
        text = read(d)
        flat = re.sub(r"\s+", " ", text)
        for m in re.finditer(r"\]\(\./([^)#]+)(#[^)]*)?\)", text):
            target = m.group(1)
            if not (ROOT / target).exists():
                fail(f"docs: {d} links to missing {target}")
        # textual references like "SPEC.md Section 9.4" or "MULTIPATH.md Section 5.4"
        for m in re.finditer(r"\b([A-Z_]+\.md) Sections? (\d+(?:\.\d+)*)", flat):
            doc, sec = m.group(1), m.group(2)
            if doc in DOCS and not ref_ok(f"{doc}#{sec}"):
                fail(f"docs: {d} references {doc} Section {sec}, which does not exist")
        for m in re.finditer(r"\b([A-Z_]+\.md)\b", flat):
            if not (ROOT / m.group(1)).exists():
                fail(f"docs: {d} mentions missing {m.group(1)}")
        CHECKS["docs"] += 1
    # unqualified "Section N" inside a document must exist in that document
    for d in ["SPEC.md", "MULTIPATH.md", "RESUME.md", "EXTENSIONS.md", "STATE_MACHINE.md",
              "TRACES.md", "CONFORMANCE.md", "REGISTRY.md"]:
        if not (ROOT / d).exists():
            continue
        raw = read(d)
        secs = sections(raw)
        text = re.sub(r"\s+", " ", raw)
        for m in re.finditer(r"(?<![A-Za-z0-9_.-])(?<!md )Sections? (\d+(?:\.\d+)*)", text):
            start = max(0, m.start() - 40)
            ctx = text[start:m.start()]
            if re.search(r"(RFC ?\d+|QMux-02|draft-[\w-]+|reliable-stream-reset-\d+|[A-Z_]+\.md|"
                         r"multipath-21|webtrans[\w-]*|RFC\d+\]|\]|BCP 14|HTTP/2|FIPS)[^.]{0,30}$", ctx):
                continue
            if m.group(1) not in secs:
                fail(f"docs: {d} mentions Section {m.group(1)}, which it does not have")


# =============================================================================
# registry

def alloc(kind, name, suffix):
    s = f"zmux {kind} {name}" + (f"#{suffix}" if suffix else "")
    h = int.from_bytes(hashlib.sha256(s.encode()).digest()[:8], "big")
    return 0x4000 + h % (0x40000000 - 0x4000)


def check_registry():
    data = json.loads(read("assets/registry.json"))
    if data != json.loads(json.dumps(render.registry_json())):
        fail("registry: assets/registry.json is stale; run tools/rebuild_assets.py")
    md = read("REGISTRY.md")
    if render.render_registry_md(md) != md:
        fail("registry: REGISTRY.md generated tables are stale")
    spec = read("SPEC.md")

    def zmux_values(kind, items, key):
        out = []
        for it in items:
            if kind == "frame" and it["kind"] != "zmux":
                continue
            if kind == "transport_parameter" and it["scope"] not in ("zmux", "zmux-reserved"):
                continue
            if kind == "transport_error" and it["source"] != "zmux":
                continue
            out.append((it["name"], it[key]))
        return out

    seen_all = {}
    for kind, items, key in [("frame", R.FRAME_TYPES, "first"),
                             ("transport_parameter", R.TRANSPORT_PARAMETERS, "id"),
                             ("transport_error", R.TRANSPORT_ERRORS, "code")]:
        vals = zmux_values(kind, items, key)
        for name, v in vals:
            CHECKS["registry"] += 1
            suffix = R.ALLOCATION_SUFFIX.get((kind, name), 0)
            if alloc(kind, name, suffix) != v:
                fail(f"registry: {kind} {name} 0x{v:x} does not match the allocation method")
            if not (1 << 14) <= v < (1 << 30):
                fail(f"registry: {kind} {name} 0x{v:x} outside [2^14, 2^30)")
            if R.is_grease_tp(v) or R.is_grease_tlv(v):
                fail(f"registry: {kind} {name} 0x{v:x} is a GREASE value")
            if v in seen_all:
                fail(f"registry: 0x{v:x} used by {seen_all[v]} and {name}")
            seen_all[v] = name
            hexv = f"0x{v:08x}"
            if kind == "frame":
                if f"| {name} | {hexv} |" not in spec:
                    fail(f"registry: SPEC.md Section 4.2 does not list {name} = {hexv}")
                meta = R.frame_by_name(name)
                doc = meta["spec"].split("#")[0]
                if f"Type (i) = {hexv}" not in read(doc):
                    fail(f"registry: {doc} does not define {name} with Type (i) = {hexv}")
            elif kind == "transport_parameter":
                if name != "zmux_record_protection" and f"| {name} | {hexv} |" not in spec:
                    fail(f"registry: SPEC.md Section 5.4 does not list {name} = {hexv}")
                doc = R.tp_by_name(name)["spec"].split("#")[0]
                if hexv not in read(doc):
                    fail(f"registry: {doc} does not mention {name} ID {hexv}")
            else:
                if f"| {name} | {hexv} |" not in spec:
                    fail(f"registry: SPEC.md Section 10.2 does not list {name} = {hexv}")
    # ALPN draft-01 aliases (SPEC.md Section 6.2) agree with the rule and with SPEC.md
    for ex in R.ALPN_ALIAS_EXAMPLES:
        CHECKS["registry"] += 1
        if R.alpn_alias(ex["configured"]) != ex["alias"]:
            fail(f"registry: ALPN alias of {ex['configured']} is not {ex['alias']}")
        for na in ex["not_accepted"]:
            if na in (ex["configured"], ex["alias"]):
                fail(f"registry: ALPN example {ex['configured']} lists {na} as not accepted")
        if f"| `{ex['configured']}` |" not in spec:
            fail(f"registry: SPEC.md Section 6.2 does not list ALPN example {ex['configured']}")
    # frame ranges do not overlap
    ranges = sorted((f["first"], f["last"], f["name"]) for f in R.FRAME_TYPES)
    for (a1, b1, n1), (a2, b2, n2) in zip(ranges, ranges[1:]):
        if a2 <= b1:
            fail(f"registry: frame ranges {n1} and {n2} overlap")
    # gates
    tp_names = {p["name"] for p in R.TRANSPORT_PARAMETERS}
    for f in R.FRAME_TYPES:
        g = f.get("gate")
        for gn in ((g,) if isinstance(g, str) else (g or ())):
            if gn not in tp_names:
                fail(f"registry: frame {f['name']} gated on unknown parameter {gn}")
        if f["kind"] != "prohibited" and not ref_ok(f["spec"]):
            fail(f"registry: frame {f['name']} references missing {f['spec']}")
    for p in R.TRANSPORT_PARAMETERS:
        if p.get("spec") and not ref_ok(p["spec"]):
            fail(f"registry: parameter {p['name']} references missing {p['spec']}")
    for t in R.PREAMBLE_TLVS:
        if not ref_ok(t["spec"]):
            fail(f"registry: TLV {t['name']} references missing {t['spec']}")
    ext = read("EXTENSIONS.md")
    for e in R.APPLICATION_ERRORS:
        if e["code"] and f"| {e['name']} | 0x{e['code']:08x} |" not in ext:
            fail(f"registry: EXTENSIONS.md Section 7 does not list {e['name']}")
        if e["code"] and not (R.APP_ERROR_BASE <= e["code"] <= R.APP_ERROR_BASE + 0xFFF):
            fail(f"registry: application code {e['name']} outside the zmux block")
        if e["socks5_rep"] and e["code"] < 0x5A4D0110 and e["code"] != 0x5A4D0100 + e["socks5_rep"]:
            fail(f"registry: {e['name']} is not 0x5a4d0100 + REP")
    # QX values as QMux-02 defines them
    if F.encode_varint(0x3F5153300D0A0D0A) != bytes.fromhex("ff5153300d0a0d0a"):
        fail("registry: QX_TRANSPORT_PARAMETERS encoding")


# =============================================================================
# WIRE_EXAMPLES.md

def norm(frames):
    return json.loads(json.dumps(F.to_json(frames)))


def check_examples():
    import build_wire  # noqa: E402
    text = read("WIRE_EXAMPLES.md")
    if build_wire.render_examples() != text:
        fail("examples: WIRE_EXAMPLES.md is stale; run tools/rebuild_assets.py")
    pat = re.compile(r"<!-- zmux-example ([\w-]+) -->\n```json\n(.*?)\n```\n.*?```hex\n(.*?)\n```",
                     re.S)
    found = 0
    for m in pat.finditer(text):
        found += 1
        eid, spec, hexs = m.group(1), json.loads(m.group(2)), m.group(3).replace("\n", "")
        if spec["id"] != eid:
            fail(f"examples: id mismatch {eid}")
        frames = F.from_json(spec["record"])
        b = bytes.fromhex(hexs)
        if F.encode_record(frames) != b:
            fail(f"examples: {eid}: bytes differ from the JSON description")
            continue
        dec, end = F.decode_record(b, 0, max_record_size=65535)
        if dec is None or end != len(b):
            fail(f"examples: {eid}: does not decode as one record")
            continue
        if F.encode_record(dec) != b:
            fail(f"examples: {eid}: decode/re-encode mismatch")
        CHECKS["examples"] += 1
    if found < 20:
        fail(f"examples: only {found} examples found")
    # key vectors
    m = re.search(r"<!-- zmux-keys -->\n(.*?)\n\n", text, re.S)
    if not m:
        fail("examples: key vectors missing")
        return
    rows = dict(re.findall(r"^\| (.+?) \| `(.+?)` \|$", m.group(1), re.M))
    import zmuxwire.tracedsl as D
    secret = C.session_secret(D.PEER_NONCE, D.LOCAL_NONCE, b"", D.PSK)
    expect = {
        "session_secret": secret.hex(),
        "join_key_i": C.join_key(secret, "initiator").hex(),
        "join_key_r": C.join_key(secret, "responder").hex(),
    }
    for k, v in expect.items():
        if rows.get(k) != v:
            fail(f"examples: key vector {k} mismatch")
        CHECKS["examples"] += 1
    # independent HMAC/HKDF check against the RFC 5869 test case 1
    ikm = bytes.fromhex("0b" * 22)
    salt = bytes.fromhex("000102030405060708090a0b0c")
    info = bytes.fromhex("f0f1f2f3f4f5f6f7f8f9")
    prk = C.hkdf_extract(salt, ikm)
    okm = C.hkdf_expand(prk, info, 42)
    if prk.hex() != "077709362c2e32df0ddc3f0dc47bba6390b6c73bb50f9c3122ec844ad7c2b3e5" or \
            okm.hex() != ("3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf"
                          "34007208d5b887185865"):
        fail("examples: HKDF does not match RFC 5869 test case 1")
    # the out-of-band frame decodes to a join whose token verifies
    oob = rows.get("PIPE_JOIN frame, out-of-band form", "")
    if not oob:
        fail("examples: out-of-band PIPE_JOIN frame missing")
    else:
        raw = bytes.fromhex(oob)
        f, _ = F.decode_frame(raw, 0, len(raw))
        if not C.verify_join(secret, "initiator", f):
            fail("examples: out-of-band join token does not verify")


# =============================================================================
# wire corpus

def check_corpus():
    for line in (ROOT / "fixtures/wire/valid.ndjson").read_text().splitlines():
        e = json.loads(line)
        b = bytes.fromhex(e.get("hex", ""))
        gates = set(e["gates"])
        limits = e.get("limits")
        try:
            if e["kind"] == "frame":
                f, end = F.decode_frame(b, 0, len(b), gates, limits)
                if end != len(b):
                    raise DecodeError("X", "trailing bytes")
                if e.get("non_minimal"):
                    # semantic equality (CONFORMANCE.md Section 4.1)
                    b2 = F.encode_frame(f)
                    f2, _ = F.decode_frame(b2, 0, len(b2), gates, limits)
                    if len(b2) >= len(b) or F.to_json(f2) != F.to_json(f) or \
                            F.encode_frame(f2) != b2:
                        fail(f"corpus: {e['id']}: not semantically equal after re-encoding")
                    if F.to_json(f) != e["frame"] or F.encode_frame(F.from_json(e["frame"])) != b2:
                        fail(f"corpus: {e['id']}: JSON does not match the decoded frame")
                else:
                    if F.encode_frame(f) != b:
                        fail(f"corpus: {e['id']}: re-encode mismatch")
                    if F.encode_frame(F.from_json(e["frame"])) != b:
                        fail(f"corpus: {e['id']}: JSON does not encode to hex")
            elif e["kind"] == "messages":
                for m, pos in zip(e["messages"], e["positions"]):
                    mb = bytes.fromhex(m)
                    frames = F.decode_message(mb, 65535, gates)
                    if F.encode_frames(frames) != mb:
                        fail(f"corpus: {e['id']}: message round trip failed")
                    if F.message_position(mb) != pos:
                        fail(f"corpus: {e['id']}: wrong position for a message")
            else:
                frames, end = F.decode_record(b, 0, 65535, gates)
                if frames is None or end != len(b) or F.encode_record(frames) != b:
                    fail(f"corpus: {e['id']}: record round trip failed")
        except DecodeError as ex:
            fail(f"corpus: valid {e['id']} rejected: {ex}")
        CHECKS["corpus"] += 1
    for line in (ROOT / "fixtures/wire/invalid.ndjson").read_text().splitlines():
        e = json.loads(line)
        b = bytes.fromhex(e.get("hex", ""))
        gates = set(e["gates"])
        try:
            if e["kind"] == "frame":
                F.decode_frame(b, 0, len(b), gates, e.get("limits"))
            elif e["kind"] == "messages":
                for m in e["messages"]:
                    F.decode_message(bytes.fromhex(m), e.get("max_record_size", 65535), gates)
            else:
                F.decode_stream(b, max_record_size=R.LIMITS["zmux_max_record_size"],
                                enabled=gates)
            fail(f"corpus: invalid {e['id']} was accepted")
        except DecodeError as ex:
            if ex.code != e["error"]:
                fail(f"corpus: invalid {e['id']}: got {ex.code}, expected {e['error']}")
        if e["error"] not in {x["name"] for x in R.TRANSPORT_ERRORS}:
            fail(f"corpus: {e['id']} names unknown error {e['error']}")
        CHECKS["corpus"] += 1


# =============================================================================
# traces

OPS = {
    "pipe_up": ({"dir"}, {"pipe", "carrier", "via", "oob_join", "carrier_binding", "pairing",
                          "framing", "source", "conn"}),
    "pipe_down": ({"dir", "how"}, {"pipe", "carrier", "lane"}),
    "set_writable": ({"pipe", "writable"}, set()),
    "peer_send": (set(), {"pipe", "carrier", "records", "hex", "chunks", "invalid", "messages",
                          "messages_hex"}),
    "api": ({"call", "args"}, {"bind", "expect"}),
    "expect_frames": ({"pipe", "frames", "match"}, {"lane", "one_credit_per_record"}),
    "expect_no_frames": ({"pipe"}, {"lane", "types"}),
    "expect_close": ({"error"}, {"wire"}),
    "expect_open": (set(), set()),
    "expect_state": (set(), {"session", "pipe", "dir", "state"}),
    "expect_event": ({"event", "fields"}, set()),
    "expect_no_event": ({"event"}, set()),
    "expect_pipe_closed": ({"pipe", "dir", "how"}, {"lane"}),
    "expect_reject_carrier": ({"carrier"}, set()),
    "advance": ({"ms"}, set()),
    "note": ({"text"}, set()),
}
CALLS = {"open_bidi", "open_uni", "accept_bidi", "accept_uni", "write", "finish", "reset", "stop",
         "read", "release", "reject", "set_priority", "set_peer_priority", "send_datagram",
         "recv_datagram", "ping", "shutdown", "close", "add_pipe", "remove_pipe",
         "set_pipe_status", "stream_id", "set_reliable_boundary"}
EVENTS = {"established", "goaway_received", "pipe_state", "join_auth_failed", "suspended",
          "resumed", "datagram_dropped", "closed", "oob_join"}
LOCAL_REASONS = {"HANDSHAKE_TIMEOUT", "IDLE_TIMEOUT", "PIPES_LOST", "EARLY_DATA_REJECTED"}
FEATURES = {"role", "goaway", "preamble", "priority", "datagram_flows", "multipath", "pipe_ack", "resume"}
SESSION_STATES = {"ESTABLISHING", "ACTIVE", "DRAINING", "SUSPENDED", "CLOSING", "CLOSED"}
PIPE_STATES = {"JOINING", "ACTIVE", "SUSPECT", "DRAINING", "FAILED", "CLOSED", "RETIRED", "OPEN",
               "ENDED"}
FRAME_NAMES = {f["name"] for f in R.FRAME_TYPES} | {"STREAM_DATA", "RAW", "REPEAT"}


def has_placeholder(o):
    if isinstance(o, dict):
        return "$sent" in o or any(has_placeholder(v) for v in o.values())
    if isinstance(o, list):
        return any(has_placeholder(v) for v in o)
    return False


def has_raw(o):
    if isinstance(o, dict):
        return o.get("type") in ("RAW",) or any(has_raw(v) for v in o.values())
    if isinstance(o, list):
        return any(has_raw(v) for v in o)
    return False


def check_pattern(tid, n, p):
    if not isinstance(p, dict) or "type" not in p:
        fail(f"trace {tid} step {n}: pattern without type")
        return
    t = p["type"]
    if t == "*":
        return
    names = t["oneof"] if isinstance(t, dict) else [t]
    for nm in names:
        if nm not in FRAME_NAMES:
            fail(f"trace {tid} step {n}: unknown frame type {nm}")


def check_traces():
    profiles = json.loads((ROOT / "fixtures/profiles.json").read_text())
    for name, prof in profiles.items():
        params = F.from_json([{"id": k, "value": v} for k, v in prof["tp"].items()])
        try:
            body = F.encode_tps(params)
            F.decode_tps(body)
        except Exception as ex:  # noqa: BLE001
            fail(f"profile {name}: invalid transport parameters: {ex}")
    index = json.loads((ROOT / "fixtures/traces/index.json").read_text())["traces"]
    listed = {t["file"] for t in index}
    files = {str(p.relative_to(ROOT)) for p in (ROOT / "fixtures/traces").rglob("*.ndjson")}
    for missing in files - listed:
        fail(f"traces: {missing} not in index.json")
    for missing in listed - files:
        fail(f"traces: index.json lists missing {missing}")
    for entry in index:
        path = ROOT / entry["file"]
        if not path.exists():
            continue
        lines = path.read_text().splitlines()
        head = json.loads(lines[0])
        tid = head.get("id")
        CHECKS["traces"] += 1
        expected_id = ".".join(Path(entry["file"]).relative_to("fixtures/traces").with_suffix("").parts)
        if tid != expected_id or entry["id"] != tid:
            fail(f"traces: id {tid} does not match path {entry['file']}")
        for k in ("kind", "id", "title", "profile", "local_role", "refs", "groups"):
            if k not in head:
                fail(f"trace {tid}: header lacks {k}")
        if head.get("profile") not in profiles:
            fail(f"trace {tid}: unknown profile {head.get('profile')}")
        if head.get("local_role") not in ("initiator", "responder"):
            fail(f"trace {tid}: bad local_role")
        for r in head.get("refs", []):
            if not ref_ok(r):
                fail(f"trace {tid}: reference {r} does not resolve")
        binds = set()
        for i, line in enumerate(lines[1:], 1):
            s = json.loads(line)
            CHECKS["steps"] += 1
            if s.get("step") != i:
                fail(f"trace {tid}: step numbering broken at line {i + 1}")
            op = s.get("op")
            if op not in OPS:
                fail(f"trace {tid} step {i}: unknown op {op}")
                continue
            req, opt = OPS[op]
            keys = set(s) - {"step", "op"}
            if not req <= keys:
                fail(f"trace {tid} step {i}: {op} lacks {sorted(req - keys)}")
            if keys - req - opt:
                fail(f"trace {tid} step {i}: {op} has unknown fields {sorted(keys - req - opt)}")
            if op in ("pipe_up", "pipe_down", "peer_send") and ("pipe" in s) == ("carrier" in s):
                fail(f"trace {tid} step {i}: {op} needs exactly one of pipe and carrier")
            if op == "pipe_up":
                if s.get("via", "bootstrap") not in ("bootstrap", "listener", "lane"):
                    fail(f"trace {tid} step {i}: bad via")
                if s.get("via") == "lane" and not ("carrier" in s and "conn" in s
                                                   and s["dir"] == "in"):
                    fail(f"trace {tid} step {i}: a lane is an inbound carrier with conn")
            if op in ("expect_frames", "expect_no_frames", "pipe_down",
                      "expect_pipe_closed") and "lane" in s and (
                    s.get("pipe", "*") == "*" or not isinstance(s["lane"], int) or s["lane"] < 1):
                fail(f"trace {tid} step {i}: bad lane")
            if op in ("pipe_up", "pipe_down", "expect_pipe_closed") and s.get("dir") not in (
                    "in", "out", "bidi"):
                fail(f"trace {tid} step {i}: bad dir")
            if op == "pipe_down" and s.get("how") not in ("eof", "error"):
                fail(f"trace {tid} step {i}: bad how")
            if op == "peer_send":
                if "messages_hex" in s:
                    msgs = s.get("messages")
                    if msgs is not None:
                        enc = [F.encode_frames(F.from_json(m)).hex() for m in msgs]
                        if enc != s["messages_hex"]:
                            fail(f"trace {tid} step {i}: messages_hex does not match messages")
                    continue
                recs = s.get("records")
                if recs is None and "hex" not in s:
                    fail(f"trace {tid} step {i}: peer_send without bytes")
                if recs is not None:
                    frames = F.from_json(recs)
                    if has_placeholder(recs):
                        if "hex" in s:
                            fail(f"trace {tid} step {i}: hex present despite placeholders")
                    else:
                        try:
                            b = b"".join(F.encode_record(r) for r in frames)
                        except Exception as ex:  # noqa: BLE001
                            fail(f"trace {tid} step {i}: records do not encode: {ex}")
                            continue
                        if b.hex() != s.get("hex"):
                            fail(f"trace {tid} step {i}: hex does not match records")
                        if not has_raw(recs) and not s.get("invalid"):
                            try:
                                F.decode_stream(b, max_record_size=65535)
                            except DecodeError as ex:
                                fail(f"trace {tid} step {i}: records do not decode: {ex}")
                if "chunks" in s:
                    total = len(bytes.fromhex(s["hex"])) if "hex" in s else None
                    if total is not None and sum(s["chunks"]) != total:
                        fail(f"trace {tid} step {i}: chunks do not add up")
            if op == "api":
                if s["call"] not in CALLS:
                    fail(f"trace {tid} step {i}: unknown call {s['call']}")
                st = s["args"].get("stream")
                if isinstance(st, str) and st not in binds:
                    fail(f"trace {tid} step {i}: unbound stream handle {st}")
                if "bind" in s:
                    binds.add(s["bind"])
            if op == "expect_frames":
                if s["match"] not in ("exact", "contains", "unordered", "first"):
                    fail(f"trace {tid} step {i}: bad match mode")
                for p in s["frames"]:
                    check_pattern(tid, i, p)
            if op == "expect_no_frames":
                for t in s.get("types", []):
                    if t not in FRAME_NAMES:
                        fail(f"trace {tid} step {i}: unknown frame type {t}")
            if op == "expect_close":
                err = s["error"]
                sp, code = err.get("space"), err.get("code")
                if sp == "transport":
                    if code not in {e["name"] for e in R.TRANSPORT_ERRORS}:
                        fail(f"trace {tid} step {i}: unknown transport error {code}")
                elif sp == "application":
                    if not isinstance(code, int):
                        fail(f"trace {tid} step {i}: application code must be an integer")
                elif sp == "local":
                    if code not in LOCAL_REASONS:
                        fail(f"trace {tid} step {i}: unknown local reason {code}")
                else:
                    fail(f"trace {tid} step {i}: unknown error space {sp}")
            if op in ("expect_event", "expect_no_event") and s["event"] not in EVENTS:
                fail(f"trace {tid} step {i}: unknown event {s['event']}")
            if op == "expect_event" and s["event"] == "established":
                for f in s["fields"].get("features", []):
                    if f not in FEATURES:
                        fail(f"trace {tid} step {i}: unknown feature {f}")
            if op == "expect_state":
                ss = s.get("session")
                if ss and ss not in SESSION_STATES:
                    fail(f"trace {tid} step {i}: unknown session state {ss}")
                ps = s.get("state")
                if ps:
                    vals = ps["oneof"] if isinstance(ps, dict) else [ps]
                    for v in vals:
                        if v not in PIPE_STATES:
                            fail(f"trace {tid} step {i}: unknown pipe state {v}")
            if op == "advance" and (not isinstance(s["ms"], int) or s["ms"] < 0):
                fail(f"trace {tid} step {i}: bad advance")
        if len(lines) < 3:
            fail(f"trace {tid}: too short")
    if len(index) < 100:
        fail(f"traces: only {len(index)} traces")


# =============================================================================
# manifest

def check_manifest():
    import build_manifest  # noqa: E402
    want = build_manifest.manifest()
    have = json.loads(read("assets/manifest.json"))
    if want != have:
        fail("manifest: assets/manifest.json is stale; run tools/rebuild_assets.py")


def main():
    for fn in (check_docs, check_registry, check_examples, check_corpus, check_traces,
               check_manifest):
        try:
            fn()
        except Exception as ex:  # noqa: BLE001
            fail(f"{fn.__name__}: crashed: {type(ex).__name__}: {ex}")
    if ERRORS:
        for e in ERRORS:
            print("FAIL", e)
        print(f"{len(ERRORS)} problem(s)")
        sys.exit(1)
    print("validate: ok - " + ", ".join(f"{k} {v}" for k, v in CHECKS.items()))


if __name__ == "__main__":
    main()
