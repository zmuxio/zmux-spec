# zmux Executable Traces

Status: normative for conformance claims (CONFORMANCE.md Section 3). This
document defines the trace format in `fixtures/traces/` and what a trace
runner must do. Every implementation ships a trace runner and passes every
trace.

---

## 1. Model

A trace drives one zmux endpoint, the **local endpoint** (the implementation
under test), against a scripted **peer**. The runner:

- creates the local endpoint with the trace's profile (Section 3) and test
  hooks (Section 3.3);
- attaches in-memory carriers to it, one per pipe direction and one per lane
  (Section 5.1.1);
- feeds the bytes the peer "sends" into the local endpoint's inbound pipes;
- performs local application calls;
- collects the bytes the local endpoint writes to each outbound pipe, decodes
  them into records and frames, and compares them with expectations;
- controls a virtual clock that starts at 0 ms. Time moves only at `advance`
  steps, and the runner fires every local timer that is due.

The runner does not interpret the peer's behaviour: the peer's bytes are
fully scripted. A sans-IO core (IMPLEMENTATION.md Section 2) makes the runner
a thin layer: `receive(pipe, bytes)`, API calls, `poll_transmit(pipe)`,
`handle_timeout(now)`; with lanes, `receive` and `poll_transmit` name the
lane too, and a new inbound stream is handed over with its carrier connection
(Section 5.1.1).

---

## 2. Files

| Path | Content |
|---|---|
| `fixtures/profiles.json` | local endpoint profiles |
| `fixtures/traces/<group>/<name>.ndjson` | one trace per file |
| `fixtures/traces/index.json` | every trace id with its file, groups and references |
| `fixtures/wire/valid.ndjson`, `fixtures/wire/invalid.ndjson` | the wire corpus (CONFORMANCE.md Section 5) |

Every trace file is NDJSON: one JSON object per line. The first line is the
header (Section 4); every following line is a step (Section 5). Byte strings
are lowercase hex.

---

## 3. Profiles and test hooks

### 3.1 Profiles

`fixtures/profiles.json` maps profile names to:

```json
{
  "tp": { "<parameter name>": <value> },
  "config": { "<config name>": <value> },
  "test": { "<hook>": <value> }
}
```

- **tp**: the exact transport parameters the local endpoint sends, besides
  the GREASE parameter. Values follow the formats of REGISTRY.md Section 4:
  integers, `null` for empty values, objects for `zmux_role`,
  `zmux_multipath` and `zmux_max_lanes` (`{"max_lanes": n, "lane_key": hex}`). Some values are below what the API lets an application
  configure (for example `zmux_preamble`, `zmux_ack_threshold` and
  `max_datagram_frame_size`, and the windows, stream limit and preamble limit
  below the early-data floor of SPEC.md Section 6.1.2), so that traces stay
  small; the runner sets them through the test hooks of Section 3.3. The
  scripted peers of many traces advertise less than the floor as well, or
  omit parameters every zmux endpoint sends (`zmux_pipe_ack`), which a
  receiver does not check unless it sent early data.
- **config**: local settings that are not transport parameters, with the
  names of API.md Section 6 (`handshake_timeout`, `keep_alive_period`
  (every profile sets it to 0, off, except in the traces that test it),
  `max_pipes`, `scheduler`, `resume_timeout`, `enable_datagrams`,
  `max_incoming_uni_streams`), the scheduler parameters `suspect_floor`,
  `dead_timeout`, `probation` and `overflow_delay` in milliseconds (API.md
  Sections 4.2 and 6, the session-wide defaults that also apply to pipe 0;
  absent or `null` means the default of MULTIPATH.md and SCHEDULER.md, and the multipath
  profiles set `probation` to 0, disabling it, except in the traces that
  test it), the scheduler parameter `redundancy` (`urgent` or `off`, API.md
  Section 6; the multipath profiles set it to `off`, so that a trace sees
  each chunk once, except in the traces that test SCHEDULER.md Section 4.1),
  `standby_probe` in milliseconds (MULTIPATH.md Section 7.1; the multipath
  profiles set it to 0, the 15 s probe interval, except in the traces that
  test it), `probe_jitter` (MULTIPATH.md Section 7.1; every profile sets it to 0, so
  that every probe wait is the probe interval and every keep-alive wait the
  keep-alive period, SPEC.md Section 6.4, and traces can name the moment of
  each probe and keep-alive), plus
  `role` (`initiator`,
  `responder` or `auto`: the role argument of session creation, API.md
  Section 3), `early_data` (object with `zmux_peer` and `replayable`, booleans, API.md Section 6; absent means both false; the in-memory carriers have no early data of their own, so traces exercise only the zmux floor) and
  `pipe_auth` as an object with `psk` (hex).
  `max_incoming_uni_streams` set to a number is the explicit
  configuration (the profile's `initial_max_streams_uni` equals it); `null`
  means unset, that is staged unidirectional credit (SPEC.md Section 8.5
  rule 4): the profile then sends `initial_max_streams_uni` 16 and the
  endpoint raises it to 256 on the first `accept_uni`.
- **test**: test hooks (Section 3.3).

A trace header may override individual entries. `zmux-default` is the base
the other profiles are derived from; it is not used by a trace directly.

### 3.2 Peer parameters

The peer's transport parameters are not part of the profile: they appear as
an ordinary QX_TRANSPORT_PARAMETERS frame in a `peer_send` step.

### 3.3 Test hooks

Implementations MUST provide these hooks for the trace runner; they MUST NOT
be reachable from production configuration:

| Hook | Effect |
|---|---|
| (transport parameters) | the local endpoint sends exactly the `tp` values of the profile, including values that are internal constants in production (API.md Section 6) |
| `grease_tp` `{id, value}` | the GREASE transport parameter to send (SPEC.md 5.2 rule 6) |
| `grease_tlv` `{type, value}` | the GREASE TLV to put last in the first preamble (EXTENSIONS.md 4.3); in test mode no other preamble carries a GREASE TLV, and the other TLVs of a preamble are written in increasing type order |
| `session_nonce` | the 16-byte `zmux_multipath` Session Nonce |
| `lane_key` | the 16-byte Lane Key of `zmux_max_lanes` (MULTIPATH.md 3.6) instead of a random one |
| `role_nonce` | the 64-bit `zmux_role` nonce |
| `ping_seq_start` | the first QX_PING request sequence number; later requests add 1 |
| `rtt_ms` | a fixed initial smoothed RTT for every pipe instead of 333 ms (rttvar is half of it, as RFC 9002 Section 6.2.2 derives it), used where a trace needs short timers; later samples update the estimate as usual |
| `pipe_rtt_ms` | `{pipe: ms}`: the initial smoothed RTT of particular pipes, overriding `rtt_ms`, for traces that depend on which pipe has the lowest one-way delay |
| `pipe_cap` | the number of unretired attachments of the peer that the local endpoint keeps (instead of max_pipes + H, MULTIPATH.md 5.4 step 9); an authenticated join beyond it is refused with PIPE_ABANDON(ZMUX_PIPE_RESOURCE_LIMIT) (MULTIPATH.md 5.4 step 7), which makes the resource-exhaustion path deterministic |

Three SHOULD/MAY choices are fixed in test mode, without a hook:

- the runner's carriers count as reporting a carrier minimum RTT, so the
  local endpoint puts no QX_PING request in its first record (SPEC.md 8.3
  rule 6);
- a carrier attached with `conn` (Section 5.1.1) belongs to a
  connection whose streams are delivered independently and whose stream
  credit never runs out, so the local endpoint opens lanes on it exactly
  when SCHEDULER.md Section 9.2 calls for one, within the peer's
  `zmux_max_lanes` and MULTIPATH.md Section 5.8; a carrier without `conn`
  offers no lanes;
- a local endpoint that accepts carriers waits for the first record's
  prefix before it sends its transport parameters (the MAY of SPEC.md 13.2
  rule 7), so it never writes a leading QX_TRANSPORT_PARAMETERS record on a
  join carrier; traces of the joiner's side send one where they test the
  discard rule (MULTIPATH.md 5.7 step 3).

Everything else the local endpoint sends is determined by the specification
and the trace, except where an expectation uses a wildcard.

---

## 4. Header

```json
{"kind": "trace", "id": "core.flow.stream_credit_quarter", "title": "...",
 "profile": "zmux-small", "local_role": "responder",
 "overrides": {"tp": {}, "config": {}, "test": {}},
 "refs": ["SPEC.md#8.3"], "groups": ["core", "flow"]}
```

- **id**: unique; equals the file path below `fixtures/traces/` with `/`
  replaced by `.` and without the extension.
- **local_role**: `initiator` or `responder`: the local endpoint's transport
  role for pipe 0 (who dialled the bootstrap carrier). With `zmux_role` in
  use the negotiated role may differ; the trace then asserts it.
- **refs**: the normative sections the trace checks.

---

## 5. Steps

Every step has an `op`. Pipe numbers are wire pipe IDs. Directions are from
the local endpoint's point of view: `in` (the local endpoint reads), `out`
(it writes), `bidi` (both).

### 5.1 Carrier steps

| op | Fields | Meaning |
|---|---|---|
| `pipe_up` | `pipe` or `carrier`, `dir`, optional `via` (`bootstrap`, `listener`, `lane`), optional `conn` (Section 5.1.1), optional `oob_join` (hex), optional `carrier_binding` (hex), optional `pairing` (hex), optional `framing` (`bytes`, the default, or `records` for a message-framed carrier, SPEC.md 3.4), optional `source` (an opaque name of the remote address, for the listener's per-source limits, MULTIPATH.md 5.6) | attach an in-memory carrier. `listener` delivers it through the local endpoint's listener (classification, MULTIPATH.md 5.4); such a carrier is named by `carrier` until the local endpoint accepts its join, after which steps may also use the pipe ID from the join. `oob_join` is a PIPE_JOIN frame delivered out of band with it; `carrier_binding` is the value its exporter would return; `pairing` is the split-bootstrap pairing token (SPEC.md 6.1.1) delivered with it, after which the paired carriers form pipe 0 |
| `pipe_down` | `pipe` (with optional `lane`, Section 5.1.1) or `carrier`, `dir`, `how` (`eof` or `error`) | the carrier reports an end of stream or an error on that direction (with `pipe`, on that lane, by default lane 0); on an outbound lane, the peer closed or reset the stream |
| `set_writable` | `pipe`, `writable` (bool) | stop or resume taking bytes from the local endpoint's outbound pipe (every lane of it), as a full or drained kernel buffer would |

#### 5.1.1 Lanes

A carrier attached with `conn` (a name of a multi-stream carrier connection)
is a stream of that connection and lane 0 of its pipe there (MULTIPATH.md
Section 2.5); carriers with the same `conn` share one connection. Lanes are
named as follows:

- **Inbound lanes**: a `pipe_up` step with `via: "lane"`, `carrier`, `conn`
  and `dir: "in"` hands the local endpoint a new stream that the peer opened
  on connection `conn`, as an adapter hands over a new stream of a
  connection that carries a pipe (MULTIPATH.md Sections 5.5 and 5.8). Steps name it by
  `carrier` throughout (`peer_send`, `pipe_down`, `expect_reject_carrier`);
  the local endpoint's PIPE_ACK counts its bytes as those of the lane its
  PIPE_LANE names.
- **Outbound lanes**: when the local endpoint opens a lane, the runner gives
  it a new outbound stream of the lane-0 connection. Steps name it by `pipe`
  and `lane`, the Lane of the PIPE_LANE frame that the local endpoint writes
  first on it; `lane` is 0 when absent.

### 5.2 Peer steps

| op | Fields | Meaning |
|---|---|---|
| `peer_send` | `pipe` or `carrier`, `records` (list of frame lists) and/or `hex`, or on a `records`-framed carrier `messages` (list of frame lists) and/or `messages_hex` (list of hex, one message each), optional `chunks` (list of byte counts) | deliver bytes on the local endpoint's inbound pipe. When both `records` and `hex` are present, `hex` is the encoding of `records` (the validator checks this) and the runner sends `hex`. `chunks` splits delivery into reads of the given sizes. When `records` contain a placeholder (Section 6.1), `hex` is absent and the runner encodes the records itself after substitution. `invalid: true` marks bytes that are deliberately invalid beyond frame syntax (for example a malformed transport-parameter block); the validator then skips its decoding check. On a `records`-framed carrier each message is delivered whole, and the local endpoint's output is decoded one message per record |

Frames are JSON objects with a `type` and the fields of Section 6.

### 5.3 Application steps

```json
{"op": "api", "call": "<name>", "args": {...}, "bind": "<handle>", "expect": {...}}
```

`bind` names the stream handle returned by the call; later steps refer to it
in `args.stream`. `expect` is the required result (Section 5.5).

| call | args | result on success |
|---|---|---|
| `open_bidi`, `open_uni` | `open_info` (hex), `priority` `{urgency, incremental}` | `{"ok": true}`; `priority` only sets the PRIORITY TLV of the preamble (a request to the peer), not local scheduling |
| `accept_bidi`, `accept_uni` | | `{"stream": id, "open_info": hex, "priority": {...}, "pipe_carrier": bool, "app_tlvs": [{"type": n, "value": hex}, ...]}` or `{"pending": true}`; `app_tlvs` lists the application TLVs in the order received (EXTENSIONS.md 4.3, 4.4) |
| `write` | `stream`, `data` (hex) or `fill` (`{"len": n, "byte": b}`: n copies of byte b, for large writes), `fin` (bool) | `{"accepted": n}`: the bytes taken into the send buffer. Writes never block in traces: the send buffer (API.md Section 6, default 4 MiB) is larger than the data of any trace, so `n` is the data length unless the stream already failed |
| `stream_id` | `stream` | `{"stream": id}`; reading the identifier pins it (SPEC.md 7.2) |
| `set_reliable_boundary` | `stream` | `{"ok": true}`; the data written so far must be delivered even if the stream is reset later (RESET_STREAM_AT Reliable Size, API.md 5.1) |
| `finish` | `stream` | `{"ok": true}` |
| `reset` | `stream`, `code` | `{"ok": true}` |
| `stop` | `stream`, `code` | `{"ok": true}` |
| `read` | `stream`, `max` | `{"data": hex, "fin": bool}` or `{"pending": true}` |
| `release` | `stream` | `{"ok": true}`; drops every handle without finishing: an unfinished sending part is reset and an unread receiving part is stopped, both with code 0 (EXTENSIONS.md 7.4) |
| `reject` | `stream` | `{"ok": true}`; declines an accepted stream before reading (EXTENSIONS.md 7.2) |
| `set_priority` | `stream`, `urgency`, `incremental` | local scheduling only |
| `set_peer_priority` | `stream`, `urgency`, `incremental` | `{"ok": true}`; the API of the same name (API.md 5.1): the PRIORITY TLV before a local stream's first frame (first write or pinning), PRIORITY_UPDATE otherwise |
| `send_datagram` | `data`, optional `stream`, `context` | `{"ok": true}`; without `stream` a session datagram (Flow 0), with `stream` the stream's datagram method (Flow = stream ID + 1, API.md 5.6) |
| `recv_datagram` | optional `stream` | `{"data": hex}` from the session queue, or `{"data": hex, "context": n}` from the stream's queue; `{"pending": true}` when the queue is empty |
| `ping` | | `{"ok": true}`; sends a QX_PING request, or, while a request is outstanding, joins it and sends nothing (SPEC.md 4.5) |
| `shutdown` | `code` | graceful close: the two-phase GOAWAY when it is in use (EXTENSIONS.md 3.3), then CONNECTION_CLOSE once no stream remains (SPEC.md 6.7) |
| `close` | `code`, `reason` (hex), `app` (always `true`) | `{"ok": true}`; the application closes the session with an application error code (SPEC.md Section 10.1: an application never chooses a transport code, so there is no API, and no trace step, for a transport close; `app` stays for clarity and MUST be `true`) |
| `add_pipe` | `pipe`, `dir`, `fate_group`, `oob` (bool), `carrier_bound` (bool), optional `send_priority`, `receive_priority`, `send_standby`, `receive_standby`, `weight`, `probation` (ms) as in `set_pipe_status` and API.md 4.2 `PipeOptions`; `receive_priority` and `receive_standby` go into the PIPE_JOIN as the initial request for the peer's outbound direction (MULTIPATH.md 5.1, 9) | `{"ok": true}`, or `{"pending": true}` when the endpoint may not join yet: it has max_pipes + H unretired attachments of its own (MULTIPATH.md 5.4 step 9) or is in the refusal backoff (MULTIPATH.md 5.7 step 1); it then joins with ID `pipe` once allowed. The runner attaches the carrier at the call either way |
| `remove_pipe` | `pipe`, `graceful` (bool) | `{"ok": true}` |
| `set_pipe_status` | `pipe`, and any of `send_priority`, `receive_priority` (integers 0 to 2^62 - 1, lower preferred), `send_standby`, `receive_standby` (bool), `weight` (integer); `send_priority` or `send_standby` set to `null` clears the local setting | `{"ok": true}`; the setters of API.md 4.2: `send_priority`, `send_standby` and `weight` apply to the local outbound direction (no frame; `null` = `ClearSendPriority` / `ClearSendStandby`, back to the peer's request, else the default); `receive_priority` and `receive_standby` change the request for the peer's outbound direction, which the endpoint sends at once as one PIPE_STATUS with the next sequence number, the current receive standby flag and receive priority, or, while the attachment is JOINING, once it is ACTIVE with the latest values (MULTIPATH.md 5.7 step 3, 9; SCHEDULER.md 10) |
| `set_redundant` | `stream`, `redundant` (bool) | `{"ok": true}`; `SetRedundant` of API.md 5.1 for the stream's sending part (SCHEDULER.md 4.1) |
| `revalidate_pipes` | optional `pipe` (absent: every live outbound pipe) | `{"ok": true}`; `RevalidatePipes` or `Revalidate` of API.md 4.2: a PIPE_PING at once, and the pipe awaits revalidation until it is acknowledged (SCHEDULER.md 11.1) |
| `set_scheduler` | `policy` (`affinity`, `session-affinity`, `spread-eta`, `spread-round-robin`, `spread-rate-weighted`), optional `stream` (bind name: that stream's directions; absent: the session) | `{"ok": true}`; takes effect at the next chunk (SCHEDULER.md Section 12) |

### 5.4 Expectation steps

| op | Fields | Passes when |
|---|---|---|
| `expect_frames` | `pipe` (ID or `"*"`), optional `lane`, `frames` (patterns), `match` (`exact`, `contains`, `unordered` or `first`), optional `one_credit_per_record` (bool) | see Section 5.6; with `one_credit_per_record`, also no record written since the previous expectation contains two MAX_DATA frames, or two MAX_STREAM_DATA frames for one stream, or two MAX_STREAMS frames of one type (SPEC.md 8.3 rule 5) |
| `expect_no_frames` | `pipe` (ID or `"*"`), optional `lane`, optional `types` | after flushing, no frame (or no frame of the listed types) was written since the last expectation on that pipe (that lane of it; a lane not opened has written nothing) |
| `expect_close` | `error` `{space, code, remote}`, optional `wire` (bool) | the session is closed (or closing) with that error; `wire: true` requires a CONNECTION_CLOSE with that code on at least one outbound pipe, `wire: false` requires that no CONNECTION_CLOSE was written; without `wire` either is accepted. Traces use `wire: true` for errors the local endpoint detects or causes (it MUST send CONNECTION_CLOSE, SPEC.md 6.5) and omit it where echoing a peer's close is optional |
| `expect_open` | | the session is not closed or closing |
| `expect_state` | `session` and/or `pipe`+`dir`+`state` | the session or pipe state (STATE_MACHINE.md names) matches |
| `expect_event` | `event`, `fields` | the local endpoint reported the event (Section 5.7) since the previous `expect_event` with the same name |
| `expect_pipe_closed` | `pipe`, optional `lane`, `dir`, `how` (`graceful`, `abort` or `any`) | the local endpoint closed that carrier direction, every lane of it, or with `lane` that lane only (Section 5.1.1) |
| `expect_reject_carrier` | `carrier` | the local endpoint closed the carrier without writing anything to it; for an inbound lane, it aborted reading it (MULTIPATH.md 5.8) |
| `expect_no_event` | `event` | the local endpoint has not reported the event since the previous expectation of that event |
| `advance` | `ms` | moves the virtual clock and fires due timers |
| `note` | `text` | nothing; documentation |

Before every expectation step, the runner **flushes**: it calls the local
endpoint's transmit function for every writable outbound pipe until no more
bytes come out, at the current virtual time.

### 5.5 Results and errors

`expect` objects use these error shapes:

```json
{"error": {"kind": "stream_reset", "code": 7, "remote": true}}
{"error": {"kind": "stream_stopped", "code": 7, "remote": true}}
{"error": {"kind": "stream_rejected"}}
{"error": {"kind": "closed_locally"}}
{"error": {"kind": "going_away"}}
{"error": {"kind": "too_large"}}
{"error": {"kind": "unsupported"}}
{"error": {"kind": "session", "space": "transport", "code": "FLOW_CONTROL_ERROR", "remote": false}}
```

The kinds follow STATE_MACHINE.md Section 2.5. A result object that contains
only some fields matches any result with those fields.

### 5.6 Frame matching

The runner decodes the bytes written to a pipe into frames. For each pipe,
and each lane of a pipe with lanes (Section 5.1.1), it keeps the frames
written since the previous expectation on that pipe and lane; for `"*"` it
merges all outbound pipes and lanes. PADDING frames are skipped unless a
pattern names PADDING.

- `exact`: the frames match the patterns one to one, in order.
- `contains`: the patterns match a subsequence of the frames, in order.
- `unordered`: each pattern is checked on its own, ignoring order: it must
  match at least one of the frames, or exactly n frames when it carries
  `"count": n` (or a number of frames in a range with `"count": {"min": a,
  "max": b}`). Other frames are allowed.
- `first`: the first frames ever written on the pipe match the patterns one
  to one, in order; later frames are ignored.

After the comparison the frames are consumed, whether they matched a
pattern or not.

A pattern is a frame object (Section 6) in which:

- a missing field matches any value;
- `"*"` matches any value;
- `{"min": a, "max": b}` (either bound optional) matches an integer range;
- for byte fields, a hex string matches exactly, `{"len": n}` matches the
  length and `{"prefix": hex}` matches a prefix;
- for QX_TRANSPORT_PARAMETERS, `params_include` lists parameters that must be
  present with the given values (`"*"` for any value), `params_exclude` lists
  names that must be absent, and `grease: true` requires at least one GREASE
  parameter.

The pseudo-frame `{"type": "STREAM_DATA", "id": n, "off": o, "data": hex,
"fin": bool}` matches the STREAM frames for stream n among the frames being
matched: assembled by offset, they must cover exactly the range starting at
`off` (default 0) with the bytes `data` (or, with `{"len": n}`, exactly n
bytes of any content), contiguously and without other bytes; in `contains` mode it stands at the position of the last STREAM frame
it covers, so a pattern after it requires that frame to follow all of that
data; `fin`, when present, states whether a FIN was seen at the end of the
range. It is used where an implementation is free to split stream data into
frames. A pattern `type` may also be `{"oneof": [names]}`, or `"*"` for any
frame. In `expect_state`, a pipe `state` may likewise be `{"oneof": [names]}`.

### 5.7 Events

| Event | Fields |
|---|---|
| `established` | `role`, `features` (the set of features in use, from `role`, `goaway`, `preamble`, `priority`, `datagram_flows`, `multipath`, `pipe_ack`, `resume`; lanes, available per direction, are observed through PIPE_LANE and PIPE_ACK instead) |
| `oob_join` | `pipe`, `frame` (hex): the PIPE_JOIN the local endpoint asks its adapter to deliver out of band |
| `goaway_received` | `max_bidi`, `max_uni`, `code`; reported only when a watermark is lowered |
| `pipe_state` | `pipe`, `dir`, `state` |
| `join_auth_failed` | `reason`: `mac`, or `mac_carrier_bound` when the token was carrier-bound (MULTIPATH.md 5.4 step 5; API.md 4.3 `JoinAuthFailed`) |
| `suspended`, `resumed` | |
| `datagram_dropped` | `count` |
| `closed` | `error` |

---

## 6. Frame objects

| type | fields |
|---|---|
| PADDING | `count` |
| STREAM | `id`, `off`, `data`, `fin`, optional `has_off`, `has_len` (encoding flags, default: OFF iff off > 0, LEN set) |
| RESET_STREAM | `id`, `code`, `final_size` |
| RESET_STREAM_AT | `id`, `code`, `final_size`, `reliable_size` |
| STOP_SENDING | `id`, `code` |
| MAX_DATA | `max` |
| MAX_STREAM_DATA | `id`, `max` |
| MAX_STREAMS | `bidi`, `max` |
| DATA_BLOCKED | `limit` |
| STREAM_DATA_BLOCKED | `id`, `limit` |
| STREAMS_BLOCKED | `bidi`, `limit` |
| CONNECTION_CLOSE | `app`, `code`, `frame_type` (transport only), `reason` |
| DATAGRAM | `data`, `has_len` |
| QX_TRANSPORT_PARAMETERS | `params`: list of `{"id": name or number, "value": ...}` or `{"id": number, "raw": hex}` |
| QX_PING | `response`, `seq` |
| GOAWAY | `max_bidi`, `max_uni`, `code` |
| PRIORITY_UPDATE | `id`, `seq`, `urgency`, `incremental` |
| PIPE_JOIN | `locator`, `pipe`, `direction`, `carrier_bound`, `standby` (bool), `fate_group`, `priority`, `token` |
| PIPE_ACK | `entries`: list of `{pipe, lane, position, delay}` |
| PIPE_LANE | `lane_key` (16 bytes, hex), `pipe`, `lane` |
| PIPE_PING | |
| PIPE_ABANDON | `pipe`, `code` |
| PIPE_STATUS | `pipe`, `seq`, `status` (`available` or `standby`), `priority` (0 to 2^62 - 1) |
| RAW | `bytes`: inserted verbatim (only in `peer_send`, for malformed input) |
| REPEAT | `count`, `frame`: `count` copies of `frame` (only in `peer_send`) |

Error codes in frames are integers; in `expect_close` they are names from
REGISTRY.md Section 6, or one of these local reasons with `"space": "local"`:

- `HANDSHAKE_TIMEOUT`;
- `IDLE_TIMEOUT`, with `"remote": true` when the peer ended the session by
  CONNECTION_CLOSE with ZMUX_IDLE_TIMEOUT, or, in a session without
  acknowledgement, by a graceful shutdown without CONNECTION_CLOSE (SPEC.md
  6.4);
- `EARLY_DATA_REJECTED`: the local endpoint sent early data and the peer's
  parameters show that the peer cannot take it (SPEC.md 6.1.2); the
  endpoint closes with CONNECTION_CLOSE(NO_ERROR);
- `PIPES_LOST`: the session ended because pipes were lost: a carrier failure
  outside multipath (with pipe acknowledgement, a graceful end of the peer's
  pipe without CONNECTION_CLOSE included), or the loss of every pipe in a direction without resume
  (MULTIPATH.md 8.5), in which case the endpoint may also have sent
  CONNECTION_CLOSE(NO_VIABLE_PATH) on a remaining pipe.

These map to the API termination kinds of API.md Section 5.3.

### 6.1 Placeholders

The local endpoint's exact byte counts depend on choices it is free to make
(record boundaries, frame splitting). Where the peer must acknowledge them, an
integer field of a `peer_send` frame may be the object
`{"$sent": pipe, "lane": l, "plus": n}`: the number of bytes the local
endpoint has written on lane l (default 0) of its outbound direction of that
pipe so far, plus n (default 0).
It is used only in PIPE_ACK positions. The runner writes the resolved value
as a 4-byte variable-length integer (values below 2^30; RFC 9000 Section
16 allows the longer encoding outside the Frame Type field, SPEC.md 4.1), so
the record's length, and with it every position the peer reaches on that
pipe afterwards, is fixed by the trace and counted like any other record
(MULTIPATH.md 6.1).

---

## 7. Runner obligations

1. Apply the profile, overrides and test hooks before the first step.
2. Execute steps in order. A failed expectation fails the trace; the runner
   reports the step number.
3. Never advance time except at `advance` steps.
4. Flush before each expectation (Section 5.4).
5. Treat a session error raised by the local endpoint as an event, not a
   runner failure; only expectations decide.
6. After the last step, fail the trace if the local endpoint raised an error
   or closed the session and no `expect_close` step covered it.

---

## 8. Writing traces

Traces are generated by `tools/build_traces.py`, which computes every byte
(including join tokens) with `tools/zmuxwire`.
`tools/validate_assets.py` checks the schema of every trace, that every
`hex` matches its `records`, that every frame decodes, that ids match paths,
and that `index.json` lists every trace.
