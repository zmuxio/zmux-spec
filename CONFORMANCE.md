# zmux Conformance

Status: normative for any claim of zmux conformance. A claim names the
specification revision (the commit of this repository) it was tested
against.

---

## 1. Scope

There is one conformance class: a **zmux implementation** implements the
whole core protocol (SPEC.md, MULTIPATH.md, RESUME.md) and every negotiated
feature of EXTENSIONS.md, apart from the optional features of requirement
C-11. Multipath and resume are core: an implementation cannot claim
conformance without them, even though sessions use them only when
configured (SPEC.md Section 2.4).

Third-party endpoints that implement only part of zmux do not claim
conformance; the protocol stays interoperable with them through the feature
negotiation of SPEC.md Section 5.5 and the plain-QMux invariant of SPEC.md
Section 13.

---

## 2. Implementation requirements

| # | Requirement | Reference |
|---|---|---|
| C-1 | Implement every MUST of SPEC.md, MULTIPATH.md, RESUME.md and EXTENSIONS.md. | all |
| C-2 | Pass every trace in `fixtures/traces/` with a trace runner shipped in the implementation's repository (Section 3). | TRACES.md |
| C-3 | Accept every entry of `fixtures/wire/valid.ndjson` and reject every entry of `fixtures/wire/invalid.ndjson` with the listed error (Section 4). | |
| C-4 | Run the traces with the no-effect budget at the parameters of SPEC.md Section 9.4.2, so that they test the boundary. Production values may be more lenient. | SPEC 9.4 |
| C-5 | Run the traces with the sender behaviour they assume: the PIPE_PING of MULTIPATH.md Sections 5.2 and 5.4 step 8, the PIPE_STATUS requests of MULTIPATH.md Section 9, the credit rules of SPEC.md Sections 8.3 to 8.5 and the linger timeout of SPEC.md Section 6.5. Production defaults are the implementation's own. | |
| C-6 | Run the traces with frames bound to a pipe only when that pipe is being written (MULTIPATH.md Section 11 rule S10). | |
| C-7 | Provide the test hooks of TRACES.md Section 3.3, unreachable from production configuration. | TRACES 3.3 |
| C-8 | Never place a transport error code in a stream frame and never let an application choose one. | SPEC 10.1 |
| C-9 | Report session errors with the precedence of SPEC.md Section 6.5 and stream outcomes as STATE_MACHINE.md Section 2.5 lists. | |
| C-10 | Reset with code 0 when an unfinished sending part is dropped; never report a reset stream as complete. | EXTENSIONS 7.4; SPEC 7.5 |
| C-11 | Optional features: split bootstrap (SPEC.md Section 6.1.1) MAY be omitted. An implementation that offers it passes the traces whose identifiers start with `multipath.bootstrap.split_`; one that does not skips them and states so in its conformance claim. Streams as carriers (EXTENSIONS.md Section 10.2) MAY be omitted as well; an implementation that offers them follows EXTENSIONS.md Section 10.2. Nothing on the wire depends on either choice. | SPEC 6.1.1, EXTENSIONS 10.2 |

---

## 3. Executable traces

`fixtures/traces/index.json` lists every trace with its references and the
defect-class tags it covers. The format and the runner obligations are in
TRACES.md. Groups:

| Group | Covers |
|---|---|
| `core.handshake` | first frame, parameter rules, GREASE, identification of foreign peers, handshake timeout, early data (sending within the floor behind the PIPE_ACK fence, receiving, rejection by a peer that cannot take it) |
| `core.records` | record framing, frame-type rules, prohibited and unknown frames, incremental parsing |
| `core.streams` | opening, ordering, stream states, STOP_SENDING, resets, identifier allocation, release semantics |
| `core.flow` | stream and session credit, zero-window liveness, session accounting, stream-count credit after release, Rapid Reset |
| `core.robustness` | QX_PING rules, bounded response state, the no-effect budget and its compliant floor |
| `core.close` | close procedure, linger, error precedence, idle timeout, keep-alive |
| `core.reset_stream_at` | RESET_STREAM_AT rules |
| `core.pipe_ack` | pipe acknowledgement without multipath (MULTIPATH.md Section 3.5), and the idle timeout of a session with acknowledgement (SPEC.md Section 6.4) |
| `core.lanes` | lanes (MULTIPATH.md Sections 2.5, 3.6 and 5.8) in a session without multipath: PIPE_LANE, positions and PIPE_ACK entries per lane, head-of-line blocking on one lane, opening only after lane 0 is acknowledged, invalid lanes and entries, a lane's failure |
| `ext.*` | role negotiation, GOAWAY, preamble, PRIORITY_UPDATE, datagrams and flows |
| `compat.qmux` | behaviour towards a plain QMux peer |
| `multipath.*` | negotiation, joins (first batch, direction, replay, refusal backoff, key shares and Groups), session epochs (the four phases, copies and late frames, collisions, key phases and Locators), spread reordering, acknowledgement, failover and reinjection, pipe removal, pipe status, close, idle, budget, lanes (acknowledgement per lane, graceful end of every lane, a lane's end failing its pipe) |
| `resume.*` | suspension, positions after a rejoin, fail-closed conditions, grace expiry, the attachment cap while suspended |
| `findings` | regressions of defect classes (Section 6) not covered elsewhere |

The current counts are in `assets/manifest.json`.

---

## 4. Invalid input

Every condition below MUST be detected and MUST produce the listed result. The
corpus entries in `fixtures/wire/invalid.ndjson` and the traces cover each
row.

Each corpus line is a JSON object with `id`, `kind`, `gates` (the features
and announcements in use, by transport-parameter name), `note`, and for
invalid entries `error` (the transport error). An entry may also give
`limits`, sized gates such as `{"max_datagram_frame_size": 12}` (the value
the receiver advertised). Valid entries are minimally encoded and re-encode
to the same bytes, except those marked `"non_minimal": true`, which must be
accepted and re-encode, under the semantic equality defined below, to the
minimal encoding of their `frame`:

- `frame`: `hex` is one frame;
- `record`: `hex` is a byte stream of records that are not the first record
  of a bootstrap pipe;
- `messages`: `messages` is a list of messages of a message-framed carrier
  (SPEC.md Section 3.4), each one record's Frames field; valid entries also
  give `positions`, the bytes each message counts for in pipe positions.

The receiver's own `max_record_size` is 65535 unless an entry gives
`max_record_size`. A carrier that ends inside a record is a pipe failure,
not a corpus error; traces cover it.

**Semantic equality.** Inputs need not be minimally encoded: RFC 9000
Section 16 allows variable-length integers in more bytes than necessary
(frame types excepted, RFC 9000 Section 12.4), and SPEC.md Section 13.2
requires accepting anything a correct QMux peer may send, so byte-for-byte
equality is not required. For every input that decodes, encoding the
decoded value with the implementation's own encoder:

1. produces no more bytes than the input, counting every variable-length
   integer that the encoder writes in a reserved fixed width (a Size or
   Length filled in afterwards, as RFC 9000 allows) at its shortest length;
   an encoder that writes shortest forms throughout is compared byte for
   byte;
2. decodes to a value equal to the first decode;
3. is stable: decoding and encoding it again yields the same bytes.

### 4.1 Records and frames

| Condition | Result |
|---|---|
| record Size 0 (not the first record of a bootstrap pipe, see 5.2) | FRAME_ENCODING_ERROR |
| record Size above the local `max_record_size` (detected from the Size field alone; not the first record) | FRAME_ENCODING_ERROR |
| last frame truncated at the record end; any truncated field; a length field larger than the remaining record | FRAME_ENCODING_ERROR |
| a pipe ends inside a record, even gracefully | single pipe: session ends (pipes lost); multipath: the record is discarded and the pipe fails |
| message-framed carrier: message longer than `max_record_size` | FRAME_ENCODING_ERROR (corpus `message-oversize`; trace `core.records.message_oversize`) |
| message-framed carrier: empty message | accepted as a no-op, not chargeable, like a PADDING-only record (corpus `message-empty-no-op`, valid; traces `core.records.message_empty` and `core.records.message_empty_not_charged`) |
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
| PIPE_ACK with no entry; PIPE_ACK whose entries are not in strictly increasing Pipe ID order (including a repeated pipe) | FRAME_ENCODING_ERROR |
| PIPE_STATUS Status other than 0 or 1, or a frame without the Priority field (every Priority value is valid; corpus `pipe-status-priority-max`) | FRAME_ENCODING_ERROR (corpus `pipe-status-invalid`, `pipe-status-missing-priority`) |

### 4.2 Establishment and parameters

| Condition | Result |
|---|---|
| first bytes that cannot begin a QX_TRANSPORT_PARAMETERS record (HTTP, TLS on a plain socket; a first record whose Size is below 9 or above the local `max_record_size`) | PROTOCOL_VIOLATION as soon as the mismatching byte arrives, after the local QX_TRANSPORT_PARAMETERS |
| QX_TRANSPORT_PARAMETERS anywhere but first, or on a joined pipe | PROTOCOL_VIOLATION; TRANSPORT_PARAMETER_ERROR when its parameter block is also not valid, whether malformed or containing a duplicate, prohibited or out-of-range parameter (corpus `record-misplaced-tp-malformed`, `record-misplaced-tp-duplicate`; decode errors first, SPEC.md Section 10.3) |
| duplicate parameter; prohibited QUIC parameter; malformed value | TRANSPORT_PARAMETER_ERROR |
| `max_record_size` below 16382; `initial_max_streams_*` above 2^60 | TRANSPORT_PARAMETER_ERROR |
| `reset_stream_at`, `zmux_goaway`, `zmux_priority`, `zmux_datagram_flows` with a non-empty value | TRANSPORT_PARAMETER_ERROR |
| `zmux_role` with an invalid role, a missing or extra nonce | TRANSPORT_PARAMETER_ERROR |
| `zmux_preamble` without `reset_stream_at`, below 1, above `max_record_size` - 32 or above the sender's stream windows | TRANSPORT_PARAMETER_ERROR |
| `zmux_multipath` malformed, or with a value whose length disagrees with Locator Length | TRANSPORT_PARAMETER_ERROR |
| a companion parameter (`zmux_ack_threshold`, `zmux_max_ack_delay`, `zmux_max_lanes`) without `zmux_multipath` or `zmux_pipe_ack`; companion values out of range, `zmux_max_lanes` 0 included | TRANSPORT_PARAMETER_ERROR |
| role conflict | ZMUX_ROLE_CONFLICT |

### 4.3 Streams and flow control

| Condition | Result |
|---|---|
| stream opened at or above the advertised limit (by any frame) | STREAM_LIMIT_ERROR |
| frame for an unopened local stream; frame in the wrong direction | STREAM_STATE_ERROR |
| changed error code in a repeated reset | STREAM_STATE_ERROR |
| changed final size; data beyond the final size | FINAL_SIZE_ERROR |
| stream or session data beyond the advertised limit, including through a final size | FLOW_CONTROL_ERROR |
| out-of-order or repeated STREAM data outside multipath | PROTOCOL_VIOLATION |
| BLOCKED frame above any limit ever advertised | PROTOCOL_VIOLATION |
| QX_PING request not above the previous; response above the largest request sent | PROTOCOL_VIOLATION |
| no-effect budget exhausted | ZMUX_EXCESSIVE_LOAD |

### 4.4 Negotiated features

| Condition | Result |
|---|---|
| preamble split across frames, above the limit, malformed TLV, repeated OPEN_INFO or PRIORITY, bad PRIORITY TLV | PROTOCOL_VIOLATION |
| opener's stream with final size 0, plain RESET_STREAM, or Reliable Size below the preamble, without a local STOP_SENDING | PROTOCOL_VIOLATION |
| PRIORITY_UPDATE for an unopened local stream or for the sender's own unidirectional stream | STREAM_STATE_ERROR |
| datagram flow naming a unidirectional stream, an unopened local stream or a stream beyond the limit; truncated flow prefix | PROTOCOL_VIOLATION |
| GOAWAY that raises a watermark; PRIORITY_UPDATE with an old sequence number | ignored (chargeable outside multipath sessions) |

### 4.5 Multipath and resume

| Condition | Result |
|---|---|
| unauthenticated join: malformed Hello, unknown Locator, Suite outside the set, failed AEAD, a key share missing, of a Group not usable or of the wrong length, pending-join and per-source limits, no Hello within the first-record timeout | carrier closed or stalled by the reject policy, without any response; session unaffected; a failed AEAD is reported as `JoinAuthFailed` |
| new carrier while the listener's bound on carriers before a session is full (MULTIPATH.md Section 5.6) | the oldest stalled carrier closed to make room; with none stalled, the new carrier closed without any response; sessions unaffected |
| carrier to be stalled while stalled carriers hold half of that bound | closed at once instead, without any response |
| authenticated join whose key share is refused (`kex`, secure-carrier/SPEC.md Section 11.6) | carrier closed, counted, the pipe ID's bit stays set; session unaffected |
| anything other than a verified Reply on a JOINING attachment, a CONNECTION_CLOSE or an oversized record included | the attachment fails; the session is unaffected and nothing read reaches it |
| authenticated join whose pipe ID has attachment state, has its replay bit set or is stale (MULTIPATH.md Section 4.3) | carrier closed, counted in `join_replays`; session unaffected |
| authenticated join with the wrong parity | PROTOCOL_VIOLATION |
| authenticated join beyond the cap of unretired attachments, outside suspension | Reply with decision 1, carrier closed, no PIPE_ABANDON; if the cap is reached only at Confirmed, carrier closed and PIPE_ABANDON(ZMUX_PIPE_RESOURCE_LIMIT); session unaffected |
| Hello replayed before the original, without a confirmation | no attachment, no eviction, no cap check, no effect on suspension |
| Secret Flags both non-zero and different | TRANSPORT_PARAMETER_ERROR |
| SESSION_EPOCH on a Trusted pipe, with an undefined Phase or a Share of the wrong length; an Epoch of at least d+3; an Epoch of d+2 other than an offer received while d is still kept; an answer of d+1 at Stable(d) (MULTIPATH.md Section 10.7) | PROTOCOL_VIOLATION |
| SESSION_EPOCH of an Epoch at most d, or a copy of a phase already processed or sent | ignored |
| Hello whose key phase matches no accepted epoch | `auth`: reject policy, reported as `JoinAuthFailed` |
| lane naming a Sealed pipe | stream aborted; nothing else |
| PIPE_ACK, PIPE_ABANDON or PIPE_STATUS for a pipe ID of the receiver's own parity that it has not taken | PROTOCOL_VIOLATION |
| PIPE_ACK position above the bytes sent, including any position above 0 for a pipe the receiver does not send on | PROTOCOL_VIOLATION; ZMUX_RESUME_FAILED with resume in use |
| PIPE_ACK entries whose (Pipe ID, Lane) do not increase strictly | FRAME_ENCODING_ERROR |
| PIPE_ACK position above 0 on a lane the receiver has not opened | PROTOCOL_VIOLATION |
| new carrier streams of a multi-stream connection beyond the pending-carrier or per-source bounds (MULTIPATH.md Sections 5.5 and 5.6): on a connection that carries no OPEN inbound pipe of the endpoint (none yet, only carriers before attachment, or only pipes that have failed or ended); beyond one stream awaiting its first record per OPEN inbound pipe on the connection; or classified as a join or a new session | each takes a slot: refused or evicting as any unauthenticated carrier; only streams admitted for a lane stay outside the bounds, with the first-record timeout |
| a new carrier stream whose PIPE_LANE carries an unknown Lane Key, or names a pipe without a live inbound direction whose lane 0 is on the same connection | the stream closed; session unaffected |
| PIPE_LANE with Lane 0 or at least the receiver's `zmux_max_lanes`, a lane number already opened on the pipe, PIPE_LANE on lane 0 or not first on its lane | PROTOCOL_VIOLATION (FRAME_ENCODING_ERROR when lanes are not available toward the receiver or the frame does not decode) |
| a lane's first record holding a frame other than PIPE_LANE and PADDING | PROTOCOL_VIOLATION |
| an inbound lane that, after its second record, ends inside a record, is reset or fails | the pipe fails (outside multipath, the session ends, SPEC.md Section 6.6); before its second record: no event |
| the writer's own lane closed or reset before it is confirmed | refused: no failure, nothing reinjected, no further lane on that pipe |
| QX_TRANSPORT_PARAMETERS leading the joiner's inbound direction of a new attachment | discarded, not counted in the position |
| outdated PIPE_ACK, entries with Position 0, duplicate STREAM data, outdated limits, PIPE_STATUS with an old sequence number or for a pipe ID of the peer's parity without attachment state, PIPE_ABANDON for such an ID outside the replay window or with its bit set | ignored, never chargeable |

---

## 5. Interoperability with QMux implementations

Plain-QMux interoperability is a hard invariant (SPEC.md Section 13).

### 5.1 Targets

| Implementation | Status | Use |
|---|---|---|
| Rust `qmux` crate 0.6.2 (moq-dev/web-transport), draft-02 | primary target | full matrix of Section 5.2 |
| shiguredo/webtransport-py (sans-IO, Python + C++ dwnx core) | secondary target (it has no DATAGRAM, reset or stop) | streams, flow control, close |
| okdaichi/qmux-go v0.3.0 | optional | streams, datagrams, QX_PING; no RESET_STREAM_AT |
| ngtcp2/dwnx (C) | optional once released | streams |
| Apache Traffic Server (HTTP/3 over QMux, ALPN `h3qx-01`) | optional; needs an HTTP/3 layer on zmux and an ATS build with a QMux-enabled quiche fork | HTTP/3 requests |

There is no Java QMux implementation; the Java library is covered through
its interoperability with the Go and Rust libraries.

### 5.2 Test matrix

For each target, with zmux as client and as server, over TCP and over TLS
(ALPN `qmux-02`, and `qmux-01`/`h3qx-01` where the target uses draft-01
identifiers). Interoperability drivers configure `max_incoming_uni_streams`
explicitly: with the default staged credit (SPEC.md Section 8.5 rule 4) a
peer can open only 16 unidirectional streams before the zmux side accepts
one, which tests written for QUIC defaults may exceed. Reset tests that rely on
RESET_STREAM_AT delivering data below the Reliable Size are skipped against
targets that treat it as a plain reset (SPEC.md Section 13.2, "Known peer
behaviour"):

1. establishment: both QX_TRANSPORT_PARAMETERS frames, GREASE and zmux
   parameters ignored by the target, features reported as "none" on the zmux
   side;
2. bidirectional and unidirectional streams opened by either side, FIN,
   half-close, 1 GiB transfers with window growth;
3. RESET_STREAM, STOP_SENDING and, where supported, RESET_STREAM_AT, with
   error codes above 2^32 where the target allows them;
4. flow control at zero window, stream-count credit exhaustion and recovery;
5. QX_PING keep-alive and the target's RTT pings;
6. DATAGRAM, where supported, at the largest size each side allows;
7. CONNECTION_CLOSE with transport and application codes in both directions,
   and the target's own error codes (the `qmux` crate uses transport code
   1002 for its violations);
8. idle timeout with both values;
9. WebSocket carriers with the `Records` framing used by the `qmux` crate and
   qmux-go, with both subprotocol forms (`qmux-02.<application>` and an
   application identifier used directly, SPEC.md Section 6.2), and empty
   messages from the target accepted as no-ops (SPEC.md Section 3.4);
10. the `qmux` crate's `application_protocols` parameter, with zmux's
    optional hook enabled and disabled;
11. zmux as client with `early_data` wrongly set toward the target (SPEC.md
    Section 6.1.2): the target closes at the PIPE_ACK fence with
    FRAME_ENCODING_ERROR, its application sees no stream, and the zmux side
    reports `EarlyDataRejected`. This is the only test in which the target
    receives a zmux frame.

Pass criteria: no protocol error on either side and every byte delivered (test 11
aside, whose pass condition is its own), the
zmux side's no-effect budget (SPEC.md Section 9.4) never drops below
4000 tokens under the target's traffic (the target sends no chargeable frames
beyond its pings and empty messages), and a packet capture of the zmux side shows no zmux frame
type, no PIPE_ACK and no out-of-order STREAM data (SPEC.md Section 13.2),
except the fence of test 11.

---

## 6. Defect-class coverage

Every defect class below is either tested here or impossible by design.
Trace ids are under `fixtures/traces/`; tag names are those used in trace
headers. A tag is `D-` followed by a class letter and a number: F flow
control and budgets, S streams, C close and termination. Classes of local
behaviour (API, carrier adapters, implementation, release process) are not
part of conformance.

| Item | Tag | Disposition |
|---|---|---|
| Legitimate traffic trips flood heuristics | `D-F01` | traces `core.robustness.budget_churn_never_trips`, `core.streams.stop_sending_after_fin`; compliant-floor stress testing; closed chargeable list (SPEC 9.4) |
| Provisional streams aged per class | `D-S01` | impossible: there are no provisional streams; identifiers are allocated at the first frame (SPEC 7.2) |
| Retained state without a charge unit | `D-F02` | impossible: no hidden or provisional state; memory is bounded by flow control (SPEC 9.8) |
| Open information lost after accept | `D-S02` | trace `core.streams.open_info_after_terminal`; SPEC 7.6, EXTENSIONS 4.4 |
| Peer CLOSE code lost under load | `D-C01` | trace `core.close.peer_code_survives_carrier_reset`; SPEC 6.5 error precedence |
| Closer never writes CLOSE | `D-C02` | trace `core.close.half_close_then_linger`; SPEC 6.5 step 1 |
| Zero-window deadlock | `D-F03` | trace `core.flow.zero_window_liveness`; SPEC 8.3 |
| Rapid Reset | `D-F04` | traces `core.flow.reset_before_accept_keeps_credit`, `core.flow.rapid_reset_bounded` |
| Data overtakes stream-open metadata | `D-S03` | impossible by design: the preamble occupies offset 0 (EXTENSIONS 4.2); trace `core.streams.local_open_write` |
| STOP_SENDING race escalates to session close | `D-S04` | trace `findings.stop_race_is_stream_error`; SPEC 9.3 |
| Opener rollback over-refunds session credit | `D-F05` | trace `findings.reset_does_not_refund_session_credit`; SPEC 8.2 |
| Stream errors reported as session errors | `D-S05` | traces `core.streams.stop_sending_elicits_reset`, `findings.peer_reset_code_zero`; STATE_MACHINE 2.5 |
| Stream identifier skipped | `D-S06` | trace `core.streams.no_identifier_gap`; SPEC 7.2 |
| Blocked read misses session close | `D-C03` | trace `core.close.pending_read_fails_with_session_error`; API test with a blocked reader |
| Data beyond the final size handled wrongly | `D-S07` | trace `core.streams.data_beyond_final_size` (FINAL_SIZE_ERROR); duplicates within the final size are ignored in multipath |
| Forged increasing BLOCKED escapes budgets | `D-F06` | trace `core.flow.blocked_above_advertised`; SPEC 4.3 |
| Writes fail the session instead of backpressure | `D-F07` | trace `core.flow.backpressure_not_error` |
| FIN overtakes data after STOP_SENDING | `D-S08` | trace `core.streams.no_data_after_stop_sending` |
| Writer blocks past session termination | `D-C04` | traces `core.close.peer_code_survives_carrier_reset`, `core.close.pending_read_fails_with_session_error` |
| Wrong code after peer STOP_SENDING | `D-S09` | trace `core.streams.stop_sending_elicits_reset` |
