# zmux Conformance

This document defines what a claim of zmux conformance means. A claim names the
specification revision (the commit of this repository) it was tested
against.

---

## 1. Scope

A **zmux implementation** implements the whole core protocol (SPEC.md,
MULTIPATH.md, RESUME.md) and every negotiated feature of EXTENSIONS.md,
apart from the optional features of requirement C-10. Multipath and resume
are core: an implementation cannot claim conformance without them, even
though sessions use them only when configured (SPEC.md Section 2.4).

Conformance is about what an endpoint puts on the wire and how it reacts to
what it receives. How an implementation schedules frames across pipes,
sizes its windows and buffers, and presents its API is its own choice
within the rules of the normative documents; the test-mode sender of
TRACES.md Section 3.4 exists only so that traces can predict pipe choices.

Endpoints that implement only part of zmux do not claim conformance; the
protocol stays interoperable with them through the feature negotiation of
SPEC.md Section 5.5 and the plain-QMux invariant of SPEC.md Section 13.

---

## 2. Implementation requirements

| # | Requirement | Reference |
|---|---|---|
| C-1 | Implement every MUST of SPEC.md, MULTIPATH.md, RESUME.md and EXTENSIONS.md. | all |
| C-2 | Pass every trace in `fixtures/traces/` with a trace runner (Section 3). | TRACES.md |
| C-3 | Accept every entry of `fixtures/wire/valid.ndjson` and reject every entry of `fixtures/wire/invalid.ndjson` with the listed error (Section 5). | |
| C-4 | Enforce the no-effect budget with exactly the parameters of SPEC.md Section 9.4.2 (the protocol allows more lenient values; conforming implementations use the reference values so that traces test the boundary). | SPEC 9.4 |
| C-5 | Unless the application overrides them, use the recommended values and follow the SHOULDs that traces check: the acknowledgement rules of MULTIPATH.md Sections 6.3 and 6.5, the PIPE_PING in the join record (MULTIPATH.md Section 5.2) and in the first record of an accepted down or bidi join (MULTIPATH.md Section 5.4 step 8), the refusal backoff of MULTIPATH.md Section 5.7 step 1, the PIPE_STATUS requests of MULTIPATH.md Section 9, the credit rules of SPEC.md Section 8.3, the BLOCKED frame for a preamble that does not fit in the remaining credit (SPEC.md Section 8.4), staged unidirectional credit (SPEC.md Section 8.5 rule 4) and the linger timeout of SPEC.md Section 6.5. | |
| C-6 | Provide the test hooks of TRACES.md Section 3.3 and the test-mode sender of TRACES.md Section 3.4, unreachable from production configuration. | TRACES 3.3, 3.4 |
| C-7 | Never place a transport error code in a stream frame and never let an application choose one. | SPEC 10.1 |
| C-8 | Report session errors with the precedence of SPEC.md Section 6.5 and stream outcomes as STATE_MACHINE.md Section 2.5 lists. | |
| C-9 | Reset with code 0 when an unfinished sending part is dropped; never report a reset stream as complete. | EXTENSIONS 7.4; SPEC 7.5 |
| C-10 | Optional features: split bootstrap (SPEC.md Section 6.1.1) MAY be omitted; an implementation that offers it passes the traces whose identifiers start with `multipath.bootstrap.split_`, and one that does not skips them and states so in its claim. Streams as carriers (EXTENSIONS.md Section 10.2) MAY be omitted as well. Nothing on the wire depends on either choice. | SPEC 6.1.1, EXTENSIONS 10.2 |

---

## 3. Executable traces

A trace drives the implementation under test against a scripted peer, with a
virtual clock; TRACES.md defines the format and the runner's obligations.
`fixtures/traces/index.json` lists every trace with its title, the sections
it checks, its profile and its step count; `assets/manifest.json` holds the
current counts and checksums. Groups:

| Group | Covers |
|---|---|
| `core.handshake` | first frame, parameter rules, GREASE, identification of foreign peers, handshake timeout, early data (sending within the floor behind the PIPE_ACK fence, receiving, rejection by a peer that cannot take it) |
| `core.records` | record framing, frame-type rules, prohibited and unknown frames, incremental parsing, message framing |
| `core.streams` | opening, ordering, stream states, STOP_SENDING, resets, identifier allocation, release semantics |
| `core.flow` | stream and session credit, zero-window liveness, session accounting, stream-count credit after release, Rapid Reset |
| `core.robustness` | QX_PING rules, bounded response state, the no-effect budget and its compliant floor |
| `core.close` | close procedure, linger, error precedence, idle timeout, keep-alive |
| `core.reset_stream_at` | RESET_STREAM_AT rules |
| `core.pipe_ack` | pipe acknowledgement without multipath (MULTIPATH.md Section 3.5), and the idle timeout of a session with acknowledgement (SPEC.md Section 6.4) |
| `core.lanes` | lanes without multipath: PIPE_LANE, positions and PIPE_ACK entries per lane, head-of-line blocking on one lane, opening only after lane 0 is acknowledged, invalid lanes and entries, a lane's failure |
| `ext.*` | role negotiation, GOAWAY, preamble, PRIORITY_UPDATE, datagrams and flows |
| `compat.qmux` | behaviour towards a plain QMux peer |
| `multipath.*` | negotiation, bootstrap, joins (in band, out of band, replay, refusal, backoff), listener limits, spread reordering, acknowledgement, PIPE_PING, carrier failures and reinjection, credit after lost pipes, streams across pipes, pipe removal, pipe status and priority requests, close, idle, budget, lanes |
| `resume.*` | suspension, positions after a rejoin, fail-closed conditions, grace expiry, the attachment cap while suspended |
| `regression` | stream races and credit accounting: STOP_SENDING crossing a finish, credit not refunded on reset, a reset with code 0 |

Regenerate and check every asset with:

```sh
python3 tools/rebuild_assets.py && python3 tools/validate_assets.py
```

The tools need Python 3.12 or later and nothing beyond the standard library.
`tools/zmuxwire` is a complete reference codec (records, frames, transport
parameters, preamble, join tokens) that a runner may use to decode what the
implementation writes.

---

## 4. Tests beyond traces

The tests of this section are RECOMMENDED before a release; they are not
part of a conformance claim.

### 4.1 Decoding

The record and frame decoder, the transport-parameter decoder and the
preamble parser SHOULD be fuzzed with the wire corpus as seed. A crash, a
hang, unbounded memory or a decode whose re-encoding is not **semantically
equal** is a failure. Inputs need not be minimally encoded: RFC 9000
Section 16 allows variable-length integers in more bytes than necessary
(frame types excepted, RFC 9000 Section 12.4), and SPEC.md Section 13.2
requires accepting anything a correct QMux peer may send, so byte-for-byte
equality is not required. For every input that decodes, encoding the
decoded value with the implementation's own encoder:

1. produces no more bytes than the input, counting every variable-length
   integer that the encoder writes in a reserved fixed width (a Size or
   Length filled in afterwards, as RFC 9000 allows) at its shortest length;
2. decodes to a value equal to the first decode;
3. is stable: decoding and encoding it again yields the same bytes.

### 4.2 Compliant-floor stress test

Two endpoints with default configuration, connected over one pipe and,
separately, over two pipes:

1. run at least 3000 bidirectional streams in under 5 s, each doing open,
   write 20 KB, finish, and closing the receive side without reading the
   response (STOP_SENDING crossing the response FIN);
2. run the same with both sides cancelling half of the streams at random
   points;
3. in the two-pipe case, kill one pipe every 500 ms and re-add it.

No session may close (SPEC.md Section 9.4.3). Then a flood of chargeable
frames (for example records of PADDING only, or, on the single pipe,
MAX_DATA frames that do not raise the limit) must still close the session
with ZMUX_EXCESSIVE_LOAD.

In the two-pipe case, a peer then fragments on purpose: on as many streams
as the session window allows, it sends one byte at every other offset up
to each stream's window, in 1-byte STREAM frames alternating between the
pipes, once in records of those frames alone and once with each frame in
its own record padded to 64 KiB. No session may close, and the receiver's
memory for received stream data stays within 2.5 x the session's
flow-control consumption (SPEC.md Section 8.2) plus 4 KiB per stream plus
the read buffer each pipe or lane is being read into (SPEC.md Section 9.8).
Filling the holes afterwards delivers every stream complete.

### 4.3 Invariants

Checked after every step of fuzzing and of any randomized session test:

- delivered stream bytes are exactly the bytes written, in order, without
  duplication, for every stream that ended with FIN;
- a stream that was reset is never reported as complete;
- received data never exceeds advertised limits; session accounting equals
  the sum of max(offset, final size);
- retained (unacknowledged) bytes never exceed the peer's limits or the
  sender's bound (MULTIPATH.md Section 11 rule S4);
- pending response state stays within SPEC.md Section 9.2;
- no retained data exist in sessions without multipath, and no PIPE_ACK
  unless pipe acknowledgement is in use (MULTIPATH.md Section 3.5).

---

## 5. Invalid input

Every condition below MUST be detected and MUST produce the listed result. The
corpus entries in `fixtures/wire/invalid.ndjson` and the traces cover each
row.

Each corpus line is a JSON object with `id`, `kind`, `gates` (the features
and announcements in use, by transport-parameter name), `note`, and for
invalid entries `error` (the transport error). An entry may also give
`limits`, sized gates such as `{"max_datagram_frame_size": 12}` (the value
the receiver advertised). Valid entries are minimally encoded and re-encode
to the same bytes, except those marked `"non_minimal": true`, which must be
accepted and re-encode under the semantic equality of Section 4.1 to the
minimal encoding of their `frame`:

- `frame`: `hex` is one frame, and valid entries give it as `frame` (a
  frame object of TRACES.md Section 6);
- `record`: `hex` is a byte stream of records that are not the first record
  of a bootstrap pipe;
- `messages`: `messages` is a list of messages of a message-framed carrier
  (SPEC.md Section 3.4), each one record's Frames field; valid entries also
  give `positions`, the bytes each message counts for in pipe positions.

The receiver's own `max_record_size` is 65535 unless an entry gives
`max_record_size`. A carrier that ends inside a record is a pipe failure,
not a corpus error; traces cover it.

### 5.1 Records and frames

| Condition | Result |
|---|---|
| record Size 0 (not the first record of a bootstrap pipe, see 5.2) | FRAME_ENCODING_ERROR |
| record Size above the local `max_record_size` (detected from the Size field alone; not the first record) | FRAME_ENCODING_ERROR |
| last frame truncated at the record end; any truncated field; a length field larger than the remaining record | FRAME_ENCODING_ERROR |
| a pipe ends inside a record, even gracefully | single pipe: session ends (pipes lost); multipath: the record is discarded and the pipe fails |
| message-framed carrier: message longer than `max_record_size` | FRAME_ENCODING_ERROR (corpus `message-oversize`; trace `core.records.message_oversize`) |
| message-framed carrier: empty message | accepted as a no-op, charged as a PADDING-only record (corpus `message-empty-no-op`, valid; trace `core.records.message_empty`) |
| frame type encoded in more bytes than necessary | PROTOCOL_VIOLATION |
| unknown frame type | FRAME_ENCODING_ERROR |
| PING, ACK, CRYPTO, NEW_TOKEN, NEW_CONNECTION_ID, RETIRE_CONNECTION_ID, PATH_CHALLENGE, PATH_RESPONSE, HANDSHAKE_DONE | FRAME_ENCODING_ERROR |
| a zmux frame whose feature is not in use; RESET_STREAM_AT without having advertised it | FRAME_ENCODING_ERROR |
| DATAGRAM without having advertised `max_datagram_frame_size`, or a well-formed DATAGRAM larger than advertised (corpus `datagram-above-limit`) | PROTOCOL_VIOLATION |
| DATAGRAM whose Length runs past the record, even when it also exceeds `max_datagram_frame_size` (corpus `datagram-overrun-above-limit`: decode errors take precedence, SPEC.md Section 10.3) | FRAME_ENCODING_ERROR |
| variable-length integer fields (not frame types) encoded in more bytes than necessary | accepted (corpus `max-stream-data-non-minimal`) |
| STREAM offset plus length above 2^62 - 1 | FRAME_ENCODING_ERROR |
| MAX_STREAMS, STREAMS_BLOCKED, GOAWAY value above 2^60 (stream counts: a stream ID has 62 bits, two of them for the type) | FRAME_ENCODING_ERROR |
| RESET_STREAM_AT Reliable Size above Final Size | FRAME_ENCODING_ERROR |
| PRIORITY_UPDATE reserved bits set | FRAME_ENCODING_ERROR |
| PIPE_JOIN that does not decode (locator length 0 or above 16, direction 3, reserved flag 0x10 to 0x80 set, a missing Priority field) on a pipe that belongs to the session | FRAME_ENCODING_ERROR (a first PIPE_JOIN processed by a listener only closes the carrier, 5.5) |
| PIPE_ACK with no entry; PIPE_ACK whose entries are not in strictly increasing (Pipe ID, Lane) order (including a repeated entry) | FRAME_ENCODING_ERROR |
| PIPE_STATUS Status other than 0 or 1, or a frame without the Priority field (every Priority value is valid; corpus `pipe-status-priority-max`) | FRAME_ENCODING_ERROR (corpus `pipe-status-invalid`, `pipe-status-missing-priority`) |

### 5.2 Establishment and parameters

| Condition | Result |
|---|---|
| first bytes that cannot begin a QX_TRANSPORT_PARAMETERS record (legacy zmux format, HTTP, TLS on a plain socket; a first record whose Size is below 9 or above the local `max_record_size`) | PROTOCOL_VIOLATION as soon as the mismatching byte arrives, after the local QX_TRANSPORT_PARAMETERS |
| QX_TRANSPORT_PARAMETERS anywhere but first, or on a joined pipe | PROTOCOL_VIOLATION; TRANSPORT_PARAMETER_ERROR when its parameter block is also not valid, whether malformed or containing a duplicate, prohibited or out-of-range parameter (corpus `record-misplaced-tp-malformed`, `record-misplaced-tp-duplicate`; decode errors first, SPEC.md Section 10.3) |
| duplicate parameter; prohibited QUIC parameter; malformed value | TRANSPORT_PARAMETER_ERROR |
| `max_record_size` below 16382; `initial_max_streams_*` above 2^60 | TRANSPORT_PARAMETER_ERROR |
| `reset_stream_at`, `zmux_goaway`, `zmux_priority`, `zmux_datagram_flows`, `zmux_pipe_ack` with a non-empty value | TRANSPORT_PARAMETER_ERROR |
| `zmux_role` with an invalid role, a missing or extra nonce | TRANSPORT_PARAMETER_ERROR |
| `zmux_preamble` without `reset_stream_at`, below 1, above `max_record_size` - 32 or above the sender's stream windows | TRANSPORT_PARAMETER_ERROR |
| `zmux_multipath` malformed or with a locator above 16 bytes | TRANSPORT_PARAMETER_ERROR |
| a companion parameter (`zmux_ack_threshold`, `zmux_max_ack_delay`, `zmux_max_lanes`) without `zmux_multipath` or `zmux_pipe_ack`; companion values out of range, `zmux_max_lanes` 0 included | TRANSPORT_PARAMETER_ERROR |
| role conflict | ZMUX_ROLE_CONFLICT |

### 5.3 Streams and flow control

| Condition | Result |
|---|---|
| stream opened at or above the advertised limit (by any frame) | STREAM_LIMIT_ERROR |
| frame for an unopened local stream; frame in the wrong direction | STREAM_STATE_ERROR |
| changed error code in a repeated reset | STREAM_STATE_ERROR |
| changed final size; data beyond the final size | FINAL_SIZE_ERROR |
| stream or session data beyond the advertised limit, including through a final size | FLOW_CONTROL_ERROR |
| out-of-order or repeated STREAM data outside multipath | PROTOCOL_VIOLATION |
| BLOCKED frame above any limit ever advertised | PROTOCOL_VIOLATION |
| QX_PING request not above the previous (outside multipath); response above the largest request sent | PROTOCOL_VIOLATION |
| no-effect budget exhausted | ZMUX_EXCESSIVE_LOAD |

### 5.4 Negotiated features

| Condition | Result |
|---|---|
| preamble split across frames, above the limit, malformed TLV, repeated OPEN_INFO, PRIORITY or PIPE_CARRIER, bad PRIORITY or PIPE_CARRIER TLV | PROTOCOL_VIOLATION |
| opener's stream with final size 0, plain RESET_STREAM, or Reliable Size below the preamble, without a local STOP_SENDING | PROTOCOL_VIOLATION |
| PRIORITY_UPDATE for an unopened local stream or for the sender's own unidirectional stream | STREAM_STATE_ERROR |
| datagram flow naming a unidirectional stream, an unopened local stream or a stream beyond the limit; truncated flow prefix | PROTOCOL_VIOLATION |
| GOAWAY that raises a watermark; PRIORITY_UPDATE with an old sequence number | ignored (chargeable outside multipath sessions) |

### 5.5 Multipath and resume

| Condition | Result |
|---|---|
| unauthenticated join: malformed, unknown locator, bad token, pending-join and per-source limits, no first record within the first-record timeout | carrier closed without any response; session unaffected; a bad token is reported locally as a join authentication failure |
| authenticated join whose pipe ID has attachment state, has its replay bit set or is stale (MULTIPATH.md Section 4.3) | carrier closed, counted as a replay; session unaffected |
| authenticated join with the wrong parity | PROTOCOL_VIOLATION |
| authenticated join beyond the cap of unretired attachments, outside suspension | carrier closed, PIPE_ABANDON(ZMUX_PIPE_RESOURCE_LIMIT); session unaffected |
| PIPE_ACK, PIPE_ABANDON or PIPE_STATUS for a pipe ID of the receiver's own parity that it has not taken | PROTOCOL_VIOLATION |
| PIPE_ACK position above the bytes sent, including any position above 0 for a pipe the receiver does not send on | PROTOCOL_VIOLATION; ZMUX_RESUME_FAILED with resume in use |
| PIPE_JOIN not first on its pipe | PROTOCOL_VIOLATION |
| PIPE_ACK entries whose (Pipe ID, Lane) do not increase strictly | FRAME_ENCODING_ERROR |
| PIPE_ACK position above 0 on a lane the receiver has not opened | PROTOCOL_VIOLATION |
| new carrier streams of a multi-stream connection beyond the pending-carrier or per-source bounds (MULTIPATH.md Sections 5.5 and 5.6): on a connection that carries no OPEN inbound pipe of the endpoint; beyond one stream awaiting its first record per OPEN inbound pipe on the connection; or classified as a join or a new session | each takes a slot: refused or evicting as any unauthenticated carrier; only streams admitted for a lane stay outside the bounds, with the first-record timeout |
| a new carrier stream whose PIPE_LANE carries an unknown Lane Key, or names a pipe without a live inbound direction whose lane 0 is on the same connection | the stream closed; session unaffected |
| PIPE_LANE with Lane 0 or at least the receiver's `zmux_max_lanes`, a lane number already opened on the pipe, PIPE_LANE on lane 0 or not first on its lane | PROTOCOL_VIOLATION (FRAME_ENCODING_ERROR when lanes are not available toward the receiver or the frame does not decode) |
| a lane's first record holding a frame other than PIPE_LANE and PADDING | PROTOCOL_VIOLATION |
| an inbound lane that, after its second record, ends inside a record, is reset or fails | the pipe fails (outside multipath, the session ends, SPEC.md Section 6.6); before its second record: no event |
| the writer's own lane closed or reset before it is confirmed | refused: no failure, nothing reinjected, no further lane on that pipe |
| QX_TRANSPORT_PARAMETERS leading the joiner's inbound direction of a new attachment | discarded, not counted in the position |
| outdated PIPE_ACK, entries with Position 0, duplicate STREAM data, outdated limits, PIPE_STATUS with an old sequence number or for a pipe ID of the peer's parity without attachment state, PIPE_ABANDON for such an ID outside the replay window or with its bit set | ignored, never chargeable |

---

## 6. Interoperability with QMux implementations

This test plan is RECOMMENDED; it is not part of a conformance claim.

Plain-QMux interoperability is a hard invariant (SPEC.md Section 13). An
implementation is tested against the available QMux-02 implementations,
with zmux as client and as server, over TCP and over TLS (ALPN `qmux-02`,
and `qmux-01` or `h3qx-01` where the other side uses draft-01
identifiers). Test drivers configure `max_incoming_uni_streams` explicitly:
with the default staged credit (SPEC.md Section 8.5 rule 4) a peer can open
only 16 unidirectional streams before the zmux side accepts one. Reset tests
that rely on RESET_STREAM_AT delivering data below the Reliable Size are
skipped against implementations that treat it as a plain reset (SPEC.md
Section 13.2, "Known peer behaviour").

1. establishment: both QX_TRANSPORT_PARAMETERS frames, GREASE and zmux
   parameters ignored by the other side, no zmux feature in use;
2. bidirectional and unidirectional streams opened by either side, FIN,
   half-close, large transfers with window growth;
3. RESET_STREAM, STOP_SENDING and, where supported, RESET_STREAM_AT, with
   error codes above 2^32 where the other side allows them;
4. flow control at zero window, stream-count credit exhaustion and recovery;
5. QX_PING keep-alive and the other side's RTT pings;
6. DATAGRAM, where supported, at the largest size each side allows;
7. CONNECTION_CLOSE with transport and application codes in both
   directions, including the other side's own error codes;
8. idle timeout with both values;
9. WebSocket carriers with the message framing of SPEC.md Section 3.4, with
   both subprotocol forms (SPEC.md Section 6.2) and empty messages from the
   other side accepted as no-ops;
10. the `application_protocols` parameter on carriers without TLS, where
    the other side uses it (SPEC.md Section 13.3);
11. zmux as client wrongly declaring the other side a zmux endpoint for
    early data (SPEC.md Section 6.1.2): the other side closes at the
    PIPE_ACK fence with FRAME_ENCODING_ERROR, its application sees no
    stream, and the zmux side reports early data rejected. This is the only
    test in which the other side receives a zmux frame.

Pass criteria (test 11 aside, whose pass condition is its own): no
protocol error on either side, every byte delivered, the
zmux side's no-effect budget never below 4000 tokens under the other side's
traffic, and a capture of the zmux side shows no zmux frame type, no
PIPE_ACK and no out-of-order STREAM data (SPEC.md Section 13.2), except the
fence of test 11.
