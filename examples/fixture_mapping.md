# Fixture Field Mapping

This document is non-normative.

It explains how the generated fixture assets are intended to map onto common
parser, state-machine, and session-behavior assertions in implementations
across programming languages.

## 1. Wire fixtures

Files:

- `fixtures/wire_valid.ndjson`
- `fixtures/wire_invalid.ndjson`

Recommended assertion mapping:

- `hex`  
  Feed into the frame or preface decoder as raw bytes.
- `expect.frame_length`  
  Assert the parsed frame length.
- `expect.frame_type`  
  Assert the decoded core frame type name.
- `expect.flags`  
  Assert the decoded flag set after applying the code-byte split.
- `expect.stream_id`  
  Assert the parsed stream ID.
- `expect.payload_hex`  
  Assert the remaining payload bytes after frame-header parsing.
- `expect.decoded.*`  
  Assert frame-type-specific semantic decoding such as `max_offset`,
  `blocked_at`, `error_code`, `ext_type`, `debug_text`, the `goaway` fields, or
  a `ping_padding_tag` known-answer vector. Keys a harness does not recognize
  can be skipped.
- `expect.settings`  
  For prefaces, the decoded settings that differ from their defaults. An empty
  object means every setting keeps its default.
- `expect_error`  
  Assert the decoder or session layer reports the named protocol error.
- `notes`  
  Human-readable context only. A preface carrying `preface_padding` does not
  round-trip through decode and re-encode, because receivers drop the padding;
  assert `settings_len` against the raw preface rather than against re-encoded
  settings.

Each `frame_valid` hex string is exactly one complete frame. A `frame_invalid`
hex string never extends past its frame, except that it may carry the full
announced `stream_id` encoding when `frame_length` is too small for it, and it
may stop after the header when the receiver rejects the frame from its header
alone.

## 2. State fixtures

File:

- `fixtures/state_cases.ndjson`

Recommended assertion mapping:

- `initial_state`  
  Seed the local state-machine model. Depending on the case, this may be a
  simple stream-level marker such as `idle`, or a structured half-state object
  such as `{ "send_half": "send_open", "recv_half": "recv_open" }`.
- `stream_kind`  
  Select the correct stream-kind rules, especially for unidirectional cases.
- `ownership`  
  Decide whether the incoming or outgoing side owns the stream ID.
- `steps[].event`  
  Feed the named logical event into the state machine or session-behavior
  layer.
- `steps[].expect_state`  
  Assert the resulting conceptual state. For stream lifecycle cases, prefer
  asserting the send-half and receive-half states separately when a structured
  object is provided.
- `steps[].expect_result`  
  Assert a semantic outcome such as protocol violation, local invalid action,
  or permitted refusal. `protocol_violation` means a session `PROTOCOL` error;
  `abort_stream_state` means the stream is answered with `ABORT(STREAM_STATE)`
  and the session stays open.

State cases fall into two groups:

- **Portable cases** are the members of the `portable_state` case set. Each
  one carries `stream_kind`, `ownership`, and `initial_state`, and every step
  uses only the events and results listed in Section 2.1. Any implementation
  can run them from the fixture data alone: reach `initial_state` through
  ordinary frames and API calls (for example a peer opening `DATA` followed by
  a peer `RESET` for `{ "send_half": "send_open", "recv_half": "recv_reset" }`)
  or through an equivalent harness hook, drive each event, and assert its
  `expect_result` and `expect_state`.
- **Reference-implementation regression cases** are every other state case.
  Most have only an `id`, an optional `scope`, and steps whose event and result
  names (for example `local_closeSession_internal_bye_observe_control_notify`,
  names built from `closed_ch`, `*_locked`, `take_pending_*`, or a bare
  `accepted` result) are labels of the reference implementation's white-box
  tests, not a standardized vocabulary. Some do name `stream_kind`,
  `ownership`, and `initial_state` but still depend on state the case does not
  state, such as pending receive credit, already-buffered unread data, a
  seeded frame-size limit, or a replenishment pacing threshold. For example,
  `stream_flow_control_noop_controls_do_not_force_flush` and
  `stream_blocked_force_flushes_pending_credit_below_pacing_threshold` send the
  same `BLOCKED` on the same half-states and expect opposite outcomes, because
  the reference harness seeds different window accounting for each. Cases
  whose events say `threshold_plus_one`, or that expect a close at a memory
  cap, encode the reference local abuse policy; SPEC Sections 11 and 13 leave
  those thresholds and responses to local policy. Other implementations may use
  regression cases as a checklist of behaviors to cover with their own tests,
  or skip them.

`tools/validate_assets.py` checks that `portable_state` holds exactly the
cases that meet these rules, that the tables below list exactly the vocabulary
the case-set builder accepts, and that no two portable cases give different
expectations for the same preconditions and event sequence.

### 2.1 Portable vocabulary

In these tables, `S` is the case's stream: a stream of the case's
`stream_kind` whose ID is owned by the side that `ownership` names. For a
peer-owned `idle` stream, `S` is the next expected peer-owned ID of its class.

| Event | Meaning |
| --- | --- |
| `peer_first_DATA` | the peer opens `S` with `DATA` carrying a non-empty payload |
| `peer_first_ABORT` | the peer's first frame on `S` is `ABORT` |
| `peer_first_RESET` | the peer's first frame on `S` is `RESET` |
| `peer_first_MAX_DATA` | the peer's first frame on `S` is stream-scoped `MAX_DATA` |
| `peer_first_BLOCKED` | the peer's first frame on `S` is stream-scoped `BLOCKED` |
| `peer_first_opening_frame_stream_id_gap` | while `S` is still unused, the peer opens a later ID of the same class with `DATA` |
| `peer_DATA` | the peer sends `DATA` with a non-empty payload on `S` |
| `peer_DATA_FIN` | the peer sends `DATA\|FIN` on `S` |
| `peer_BLOCKED` | the peer sends stream-scoped `BLOCKED` on `S` |
| `peer_STOP_SENDING` | the peer sends `STOP_SENDING` on `S` |
| `peer_late_DATA` | the peer sends `DATA` with a non-empty payload on `S` after the receive half of `S` became terminal |
| `peer_late_RESET` | the peer sends `RESET` on `S` after `S` became terminal |
| `peer_late_STOP_SENDING` | the peer sends `STOP_SENDING` on `S` after `S` became terminal |
| `peer_same_MAX_DATA` | the peer sends stream-scoped `MAX_DATA` on `S` whose value does not raise the local send limit |
| `peer_same_BLOCKED` | the peer sends stream-scoped `BLOCKED` on `S`; portable cases use it only on a fully terminal stream, where its value does not matter |
| `local_DATA_FIN` | the local application finishes the send direction of `S`, so `DATA\|FIN` is sent |
| `local_STOP_SENDING` | the local application stops reading `S` (local read-side stop) |
| `local_MAX_DATA` | the local application attempts a receive-side operation on `S` that would grant stream credit, such as a read |

| Result | Meaning |
| --- | --- |
| `protocol_violation` | the session fails with `CLOSE(PROTOCOL)` |
| `abort_stream_state` | the local endpoint answers with `ABORT(STREAM_STATE)` on `S` and the session stays open |
| `local_invalid` | the local operation fails with a local stream-side error (not readable or not writable); nothing is sent |
| `sender_must_finish_with_reset_or_fin` | the local send half of `S` reaches `send_reset` or `send_fin`, and `RESET` or `DATA\|FIN` is sent on `S` |
| `restore_session_budget_only_after_terminal_data` | the payload is discarded; its bytes count against the session receive window and are released back to it (SPEC Section 8), the stream window of `S` does not change, nothing is sent on `S`, and the session stays open |
| `no_control_flush` | the frame is ignored: nothing is sent in response and the session stays open; portable only when both halves of `S` are terminal or absent |

## 3. Invalid-policy fixtures

File:

- `fixtures/invalid_cases.ndjson`

Recommended assertion mapping:

- `layer` / `category`  
  Route the case to parser, flow-control, state, preface, or extension tests.
- `input_shape`  
  Build the local preconditions for the invalid condition.
- `expected_result.scope`  
  Assert whether the result is stream-local (`stream`), session-wide
  (`session`), or session-establishment-local (`session_establishment`, used by
  every preface-layer error case).
- `expected_result.error`  
  Assert the named error code if the outcome is an error.
- `expected_result.action`  
  Assert non-error outcomes such as ignore or forbid-send.

## 4. Case-set fixtures

File:

- `fixtures/case_sets.json`

Recommended use:

- select subsets for targeted test jobs
- split CI into jobs such as `codec_valid` / `codec_invalid`, `frame_invalid`,
  `preface`, `stream_lifecycle`, `session_lifecycle`, `flow_control`,
  `unidirectional`, `open_metadata`, `priority_update`, `local_policy`, or
  `portable_state`
- keep per-language harnesses aligned on the same logical case groups

`codec_valid` and `codec_invalid` hold exactly the byte-level
`fixtures/wire_valid.ndjson` and `fixtures/wire_invalid.ndjson` cases.
`frame_invalid` holds the frame-layer invalid-policy cases. `stream_lifecycle`
holds every state case whose scope is not `session`, plus the state-layer
invalid cases; `session_lifecycle` holds the session-scoped state cases and the
session-layer invalid cases. `local_policy` holds open-semantics,
API-semantics, and security policy cases. `portable_state` holds the state
cases that Section 2 calls portable. Every fixture ID belongs to at least one
set, so a CI split built only from case sets still runs every case.

## 5. Golden-case view

File:

- `assets/golden_cases.json`

Recommended use:

- consume when one harness wants a single source of truth
- consume the sharded `fixtures/*.ndjson` files when test organization by layer
  is more convenient
