# zmux Conformance Guidance

This document is implementation-language-neutral.

Its purpose is to define the protocol behavior that independent
implementations should validate before claiming `zmux` interoperability.

This document describes protocol behavior, not local operation names or
non-wire interface shapes. Non-wire interfaces may vary as long as the claimed
protocol behavior is preserved.

## 1. Wire interoperability

At minimum, a conforming implementation should interoperate on the base wire
contract:

- session preface parsing and emission
- `proto_ver` negotiation
- explicit `initiator` / `responder` role negotiation
- `role = auto` with non-zero `tie_breaker_nonce`
- explicit-role versus `auto` role resolution
- equal-`tie_breaker_nonce` collision handling
- the unified frame header
- stream open with the opening-eligible stream-scoped core frames
- half-close with `DATA|FIN`
- `MAX_DATA`
- `PING` / `PONG`
- `STOP_SENDING`
- `BLOCKED`
- `RESET`
- `ABORT`
- `GOAWAY`
- `CLOSE`

Base-wire conformance includes parsing, validation, and receiver-side handling of
`BLOCKED`. Proactive emission of `BLOCKED` remains an implementation policy
choice rather than a wire-compatibility requirement.

If an implementation claims `priority_update` support, it should also
interoperate on:

- negotiation of the `priority_update` capability
- not treating `priority_hints` or `stream_groups` as an independent update
  path without `priority_update`
- `PRIORITY_UPDATE` carrying `stream_priority`
- `PRIORITY_UPDATE` carrying `stream_group`
- `PRIORITY_UPDATE` with `stream_group = 0` clearing the explicit group while
  preserving omitted fields
- ignoring `open_info` inside `PRIORITY_UPDATE`
- ignoring unknown TLVs inside a `PRIORITY_UPDATE` payload
- leaving omitted fields unchanged
- ignoring a `PRIORITY_UPDATE` payload with duplicate singleton advisory TLVs
  as one dropped advisory update rather than treating it as stream-fatal
- ignoring `PRIORITY_UPDATE` when `priority_update` was not negotiated,
  before parsing its payload or checking its `stream_id`, so a malformed
  payload or `stream_id = 0` does not fail the session (only an undecodable
  `ext_type` is still `PROTOCOL`; SPEC Section 7.6)
- ignoring unnegotiated `stream_priority` or `stream_group` fields in both
  `OPEN_METADATA` and `PRIORITY_UPDATE`

If an implementation claims `open_metadata` support, it should also interoperate
on:

- negotiation of the `open_metadata` capability
- `DATA|OPEN_METADATA` carrying `stream_priority` on the first opening frame
- `DATA|OPEN_METADATA` carrying `stream_group` on the first opening frame
- `DATA|OPEN_METADATA` with `stream_group = 0` leaving the stream without an
  explicit group
- `DATA|OPEN_METADATA` carrying `open_info` on the first opening frame
- skipping unknown metadata TLVs inside `OPEN_METADATA`
- ignoring duplicate singleton metadata TLVs inside one `OPEN_METADATA` block while
  still processing the opening `DATA`

## 2. Invalid-input handling

An implementation should reject or handle correctly:

- invalid magic bytes
- unsupported `preface_ver`
- invalid explicit `role` values
- same-role conflict when both peers explicitly demand the same role
- unsupported protocol-version overlap
- non-canonical varint encodings
- truncated or otherwise invalid `varint62` encodings
- truncated TLV headers inside a valid TLV-bearing frame
- TLV value lengths that overrun the enclosing payload
- duplicate setting IDs in one preface settings block
- standard varint settings whose TLV value is malformed, non-canonical,
  truncated, or followed by trailing bytes
- `preface_padding` being accepted as ignored settings payload while still
  participating in the preface settings length and duplicate-setting rules
- `max_frame_payload < 16384`
- `max_control_payload_bytes < 4096`
- `max_extension_payload_bytes < 4096`
- `frame_length = 0` or `frame_length = 1`
- `frame_length < 1 + encoded_length(stream_id)` after the encoded `stream_id`
  has been parsed
- frame payloads larger than receiver limits
- `EXT` where `derived_payload_length < encoded_length(ext_type)`
- `MAX_DATA` with trailing bytes beyond its one canonical `max_offset`
- `BLOCKED` with trailing bytes beyond its one canonical `blocked_at`
- `RESET`, `STOP_SENDING`, `ABORT`, or `CLOSE` payloads without one canonical
  mandatory `error_code`
- `GOAWAY` payloads without canonical mandatory bidirectional watermark,
  unidirectional watermark, and `error_code` fields
- `PING` with derived payload length `< 8`
- `PONG` with derived payload length `< 8`
- `DATA`, `STOP_SENDING`, `RESET`, or `ABORT` on `stream_id = 0`
- `PING`, `PONG`, `GOAWAY`, or `CLOSE` on a non-zero `stream_id`
- `PRIORITY_UPDATE` on `stream_id = 0` when `priority_update` was negotiated
- non-zero `GOAWAY` watermarks that do not match the advertised stream class
  or the stream-ID ownership of the peer receiving the `GOAWAY`
- outbound `GOAWAY`, `CLOSE`, `RESET`, `STOP_SENDING`, and `ABORT`
  diagnostics being capped to the peer-advertised control-payload limit while
  preserving mandatory frame fields
- `DATA|OPEN_METADATA` without negotiated `open_metadata`
- `DATA|OPEN_METADATA` on an already opened stream
- `DATA|OPEN_METADATA` with a missing or malformed `metadata_len`, or with
  `metadata_len` overrunning the enclosing `DATA` payload
- forbidden non-zero flag combinations on frames other than the valid `DATA`,
  `DATA|OPEN_METADATA`, `DATA|FIN`, and `DATA|OPEN_METADATA|FIN` combinations
- duplicate singleton TLVs inside one `PRIORITY_UPDATE` payload
- a non-canonical or truncated `stream_priority` value inside a
  `PRIORITY_UPDATE` payload or an `OPEN_METADATA` block
- duplicate standardized singleton DIAG-TLVs inside one enclosing control frame
  while preserving that frame's primary semantics
- invalid UTF-8 `debug_text` inside a DIAG-TLV being ignored while preserving
  that frame's primary semantics
- a non-canonical `retry_after_millis` value inside a DIAG-TLV being ignored
  while preserving that frame's primary semantics
- `PRIORITY_UPDATE` targeting a previously unseen stream being ignored without
  creating stream state
- `PRIORITY_UPDATE` targeting a terminal stream being ignored without reviving
  stream state
- `DATA` received on a direction already closed by peer `FIN`
- `DATA` exceeding current stream `MAX_DATA` and producing stream-local
  `FLOW_CONTROL`
- `DATA` exceeding current session `MAX_DATA` and producing session-wide
  `FLOW_CONTROL`
- stream-scoped `MAX_DATA` on a previously unused stream ID causing a session
  `PROTOCOL` error
- stream-scoped `BLOCKED` on a previously unused stream ID causing a session
  `PROTOCOL` error
- `STOP_SENDING` on a previously unused stream ID causing a session
  `PROTOCOL` error
- `BLOCKED` from the non-sending side of a unidirectional stream
- `MAX_DATA` from the sending side of a unidirectional stream
- `STOP_SENDING` from the sending side of a unidirectional stream
- `RESET` from the non-sending side of a unidirectional stream
- sender-side local `ABORT` attempted first on a previously unseen peer-owned
  stream ID being treated as invalid
- illegal stream ID ownership bits
- stream ID reuse
- peer-created stream IDs skipping the next expected ID of their class (when
  the ID is not above a local `GOAWAY` watermark of that class)
- unknown core frame types
- `role = auto` with `tie_breaker_nonce = 0`
- equal `tie_breaker_nonce` when both peers use `role = auto`

Expected session error codes for the structural cases above follow SPEC
Sections 3.2, 4.3, 6.11, and 7.2:

- `FRAME_SIZE`: `frame_length = 0` or `frame_length = 1`;
  `frame_length < 1 + encoded_length(stream_id)`; frame payloads larger than
  receiver limits; missing, truncated, or non-canonical mandatory payload
  fields (the `MAX_DATA`, `BLOCKED`, `STOP_SENDING`, `RESET`, `ABORT`, `CLOSE`,
  and `GOAWAY` fields and `metadata_len`); `PING` or `PONG` payloads shorter
  than `8` bytes; container-structural TLV errors inside DIAG-TLV sequences,
  `OPEN_METADATA` blocks, and `PRIORITY_UPDATE` payloads (the latter only when
  `priority_update` was negotiated; SPEC Section 7.6), including when the same
  block also repeats a singleton TLV; and a truncated, non-canonical, or over-long
  `stream_priority` or `stream_group` value in an interpreted `OPEN_METADATA`
  block or `PRIORITY_UPDATE` payload that precedes any duplicate singleton
  (SPEC Section 7.2)
- `PROTOCOL`: non-canonical `frame_length` or `stream_id` encodings; trailing
  bytes after the one `MAX_DATA` or `BLOCKED` value; an `EXT` payload too short
  for its `ext_type`, or with a non-canonical `ext_type`; invalid preface fields
  and setting values; and the frame-scope, flag, and unknown-frame-type cases
  above

Expected establishment `CLOSE` codes follow SPEC Sections 2.4 and 2.6:
`PROTOCOL` for invalid magic, an invalid `role`, a zero `role = auto` nonce,
an invalid preface integer, a malformed settings block, or a payload limit
below its minimum; `UNSUPPORTED_VERSION` for an unsupported `preface_ver` or
no overlapping protocol version; `ROLE_CONFLICT` for the same explicit role or
equal `role = auto` nonces; `FRAME_SIZE` for `settings_len` above `4096`;
and `INTERNAL` for a local failure such as an establishment timeout.

A non-canonical, truncated, or over-long `varint62` value of a standardized
DIAG-TLV (`retry_after_millis`, `offending_stream_id`, `offending_frame_type`)
is not an error: the receiver ignores that diagnostic value and keeps the
enclosing frame's primary semantics (SPEC Section 7.1).

## 3. Extension-tolerance behavior

An implementation should demonstrate that it:

- ignores unknown capability bits
- ignores unknown setting IDs
- treats unknown advisory `scheduler_hints` values as
  `unspecified_or_balanced` without failing session establishment
- skips unknown TLVs in known namespaces
- ignores unknown `EXT` subtypes unless a stricter extension document says
  otherwise
- does not treat any stream-scoped `EXT` as implicitly opening a stream in
  core `zmux v1`

This is essential for forward evolution.

### 3.1 Protocol claims

Protocol conformance claims should be made separately for:

- `zmux-wire-v1`
- `zmux-open_metadata`
- `zmux-priority_update`

### 3.2 Protocol compatibility profile

Protocol compatibility profile:

- `zmux-v1`: the currently standardized `zmux v1` protocol feature set,
  including the base wire contract, forward extension tolerance,
  `open_metadata`, `priority_update`, and the correct negotiated handling of
  `priority_hints` and `stream_groups`

Separate protocol claims remain useful for incremental bring-up, targeted
testing, and internal release gates. Public protocol compatibility claims
should use `zmux-v1`.

### 3.3 Compatibility rule

Compatibility rule:

- `zmux-v1` implementations MUST interoperate cleanly with each other by
  negotiating and using only the capabilities both sides share on the wire
- a release that intentionally lacks one of the currently standardized
  same-version protocol features defined by this document set SHOULD NOT claim
  public `zmux-v1` compatibility
- before `session-ready`, an implementation emits only the local preface and a
  fatal establishment `CLOSE`, and emits none of: new-stream `DATA`,
  stream-scoped control, ordinary session-scoped control, or `EXT`

### 3.4 Claim checklist

The table below summarizes the readiness checklist for each protocol claim. It
does not replace the detailed scenario lists later in this document; it is a
compact gate summary for implementation planning and release review.

| Claim or profile | Minimum acceptance checklist |
| --- | --- |
| `zmux-wire-v1` | pass core wire interoperability; pass invalid-input handling; pass extension-tolerance behavior |
| `zmux-open_metadata` | satisfy `zmux-wire-v1`; negotiate `open_metadata`; accept valid `DATA\|OPEN_METADATA` on first opening `DATA`; reject unnegotiated or misplaced `OPEN_METADATA`; ignore unknown metadata TLVs; drop duplicate singleton metadata while preserving the enclosing `DATA` |
| `zmux-priority_update` | satisfy `zmux-wire-v1`; negotiate `priority_update`; process `stream_priority` and `stream_group`; ignore `open_info` inside `PRIORITY_UPDATE`; ignore unknown advisory TLVs; ignore duplicate singleton advisory updates as one dropped update |
| `zmux-v1` | satisfy `zmux-wire-v1`; interoperate on explicit-role and `role = auto` establishment; pass stream-lifecycle scenarios; pass flow-control scenarios; pass session-lifecycle scenarios; satisfy every currently active same-version optional protocol feature defined by this document set, currently `zmux-open_metadata`, `zmux-priority_update`, and the correct negotiated handling of `priority_hints` and `stream_groups` |

## 4. Stream-lifecycle scenarios

At minimum, test these stream-level cases:

- first `DATA` followed by more `DATA`
- first `RESET` on a valid unused peer-owned stream ID causing a session
  `PROTOCOL` error
- first `ABORT` on a valid unused peer-owned stream ID
- first `STOP_SENDING` on a valid unused peer-owned stream ID causing a session
  `PROTOCOL` error
- valid next-expected peer-owned stream IDs being recorded as used and
  advancing the expected cursor even when the stream is immediately refused or
  becomes terminal in the same processing step
- zero-length `DATA` opening a stream before later payload
- first `DATA|FIN` one-shot request stream
- first `DATA` with payload followed by peer `ABORT(REFUSED_STREAM)`
- unidirectional stream creation and peer rejection of wrong-direction `DATA`
- unidirectional stream rejection of wrong-direction `BLOCKED`
- unidirectional stream rejection of wrong-side `MAX_DATA`
- unidirectional stream rejection of wrong-side `STOP_SENDING`
- unidirectional stream rejection of wrong-side `RESET`
- peer opening attempts above an advertised local `GOAWAY` watermark being
  rejected with `ABORT(REFUSED_STREAM)` without consuming that stream ID or
  requiring optional `OPEN_METADATA` parsing
- after a restrictive local `GOAWAY`, peer `RESET`, `STOP_SENDING`,
  stream-scoped `BLOCKED`, stream-scoped `MAX_DATA`, or stream-scoped `EXT` on
  a refused peer-owned stream ID above the watermark (sent before the peer
  observed the refusal) being ignored without a session `PROTOCOL` error,
  further `DATA` on that ID being discarded without another
  `ABORT(REFUSED_STREAM)`, and the session staying in draining with its
  accepted streams intact
- independent enforcement of bidirectional and unidirectional incoming-stream
  limits
- `STOP_SENDING` causing the peer to stop future `DATA` on one direction while
  the opposite direction remains usable
- half-close in one direction while the reverse direction remains active
- EOF reached only after buffered data is drained following peer `FIN`
- local `DATA|FIN` preventing further local `DATA` on that direction while the
  reverse direction remains usable
- local read-side stop being represented on the wire by `STOP_SENDING`
- late `DATA` after peer `FIN`
- duplicate `RESET`
- peer `DATA|FIN` on a bidirectional stream not releasing the incoming-stream
  concurrency slot while the local send half for that stream still remains
  live
- first frame for a valid peer-owned stream ID being `RESET` causing a session
  `PROTOCOL` error
- `RESET` on a stream with unread buffered data
- `RESET` surfacing terminal error on the affected read half while the
  opposite write half may remain usable
- `ABORT` surfacing terminal errors on both read and write halves
- `STOP_SENDING` causing the sender to conclude that outbound half with either
  `RESET` or `DATA|FIN`
- `STOP_SENDING` moving the sender-side state into a no-new-writes substate
  before final conclusion
- `ABORT` preserving the numeric error code while carrying optional DIAG-TLV
  `debug_text`
- `STOP_SENDING` received after that outbound half is already terminal and
  therefore not requiring any additional concluding frame
- stream-scoped `MAX_DATA`, `BLOCKED`, and `PRIORITY_UPDATE` not overtaking the
  first opening frame for the same locally opened stream
- `DATA|FIN`, `RESET`, and `ABORT` not overtaking already committed earlier
  `DATA` on that same stream
- late non-opening control frames on a terminal stream being ignored
- late in-flight `DATA` after peer `RESET` being ignored rather than treated
  as a new stream-state violation
- late in-flight `DATA` after peer `ABORT` being ignored rather than treated
  as a new stream-state violation
- late in-flight `DATA` discarded after peer `RESET` / `ABORT` still restoring
  the released session receive budget
- `MAX_DATA` on a previously unused valid stream ID causing a session
  `PROTOCOL` error
- `BLOCKED` on a previously unused valid stream ID causing a session
  `PROTOCOL` error
- stream ID exhaustion handling without wraparound or ID reuse
- late-data classification: late `DATA` arriving after peer `FIN` producing
  `ABORT(STREAM_CLOSED)`, while late in-flight `DATA` after peer `RESET` or
  peer `ABORT` is ignored with budget release
- local read-side stop followed by bounded late peer `DATA` being discarded
  and restoring session budget without restoring stream-scoped budget
- local read-side stop, or local `ABORT`, while the peer still has its full
  outstanding stream credit in flight (for example four maximum-size `DATA`
  frames under the default `64 KiB` initial stream window) not failing the
  session: the late bytes are discarded with session budget release and no
  stream-scoped `MAX_DATA` is advertised for the stopped direction
- on a live read-stopped direction (local read-side stop, no local `ABORT`),
  one byte beyond the advertised stream credit producing `ABORT(FLOW_CONTROL)`
  rather than a session error, provided the session limit still holds; after
  a local `ABORT`, exceeding the captured late-data allowance is possible only
  for a non-compliant peer, and the implementation may then either fail the
  session with `PROTOCOL` (the peer sent beyond the stream credit it was
  granted) or keep discarding the bytes with session budget release
  (API_SEMANTICS Section 3)
- local read-side stop followed by a local `ABORT` of the same stream while
  late `DATA` is still arriving: the allowance captured at the stop keeps
  applying, so late bytes within the stream credit outstanding at the stop
  never fail the session even when they exceed the credit outstanding at the
  abort
- late `DATA` after peer `FIN` producing `ABORT(STREAM_CLOSED)` even when the
  local endpoint had already stopped reading that direction
- `OPEN_METADATA` bytes not consuming stream or session flow-control windows
  even on a zero-credit opening frame

## 5. Flow-control scenarios

At minimum, test:

- normal stream-window consumption and `MAX_DATA` advancement
- normal session-window consumption and `MAX_DATA` advancement
- sender blocking when stream `MAX_DATA` is exhausted
- sender blocking when session `MAX_DATA` is exhausted
- sender opening a stream under zero initial stream credit by first emitting a
  zero-length `DATA` opener before any stream-scoped `BLOCKED`
- large transfers split across multiple `DATA` frames
- large writes fragmented to fit the currently available stream and session
  flow-control windows instead of waiting for a larger fixed chunk size to fit
- aggregated `MAX_DATA` updates
- flow-control accounting for `DATA|OPEN_METADATA` charging only the trailing
  application-data bytes, not the metadata prefix and TLV block
- sender opening a stream under zero initial stream credit while carrying
  `OPEN_METADATA` on the opening `DATA`
- session `MAX_DATA` advanced when unread buffered data is discarded during
  reset or refusal, including the application bytes of the refused opening
  `DATA` frame itself whether the refusal comes from a local `GOAWAY`
  watermark, an incoming-stream limit, or the accept backlog
- `DATA` rejected with a stream-local `ABORT` (`STREAM_CLOSED`, `STREAM_STATE`,
  or stream `FLOW_CONTROL`) still being checked against, counted in, and
  released to the session window
- session `MAX_DATA` advanced when late `DATA` for already closed streams is
  dropped through used-ID terminal bookkeeping
- local read-side stop discarding unread data while restoring session receive
  budget without advertising fresh stream credit for that stopped direction
- late-data absorption after stop, reset, or abort being bounded per direction
  and in aggregate so ignored payloads cannot consume unbounded memory, with
  an aggregate overflow only discarding further late bytes rather than failing
  the session
- a long-lived session in which many sequential streams each absorb a small
  late tail after local read-side stop staying usable: aggregate late-data
  accounting tracks currently retained state, not a lifetime total
- overflow protection on malicious or corrupted `MAX_DATA` values
- `BLOCKED` deduplication: only the most recent limiting offset for each scope
  is retained when multiple `BLOCKED` updates are pending
- `MAX_DATA` coalescing: only the largest pending value for each scope is
  emitted when multiple `MAX_DATA` updates are pending
- stream-scoped `MAX_DATA` replenishment suppressed after local read-side stop
  on that direction while session-level replenishment continues
- session `MAX_DATA` replenishment triggered immediately when remaining
  advertised session space falls below two negotiated frame payloads

## 6. Session-lifecycle scenarios

At minimum, test:

- normal session startup with parallel preface exchange
- no frame other than the local preface, and a fatal establishment `CLOSE` on
  failure, being emitted before the peer preface is parsed: capture outbound
  bytes while withholding the peer preface and check that no `PING`, `PONG`,
  `MAX_DATA`, `BLOCKED`, `GOAWAY`, `EXT`, or stream frame appears
- immediate post-preface first `DATA` on a new stream
- no extra mux acknowledgement being required once session establishment is
  complete and stream-ID ownership is resolved
- variable-length `PING` echoed byte-for-byte by `PONG` when no recognized
  padding tag is present
- padded `PING` with a valid `ping_padding_key` tag accepting either exact
  `PONG` echo or `PONG` echo plus additional opaque suffix bytes
- locally originated `PING` payload length bounded by the smaller of local and
  peer control-payload limits
- unmatched `PONG` not completing any outstanding local `PING` and being
  ignored or counted only as no-op control traffic
- repeated `GOAWAY` with non-increasing bidirectional and unidirectional
  acceptance watermarks
- peer `GOAWAY` causing later local open attempts beyond the allowed
  watermark to fail synchronously
- peer `GOAWAY` causing never-peer-visible local streams beyond the allowed
  watermark to be reclaimed locally without waiting for explicit peer
  `ABORT(REFUSED_STREAM)`
- `CLOSE` terminating all active streams
- underlying transport closing without a prior `CLOSE`
- `PING` payload length bounded by `min(local, peer)` control-payload limits,
  not solely by the peer's advertised limit
- `GOAWAY` watermark monotonicity: a subsequent peer `GOAWAY` with a higher
  watermark than a previous one being treated as a protocol error
- session close propagating terminal errors to all remaining open streams
  and waking all blocked operations promptly
- duplicate terminal `CLOSE` frames received after session termination being
  ignored without replacing the original terminal cause

## 7. Quality behaviors to observe

These are not strict wire-level pass/fail requirements, but they should be
part of interoperability quality validation:

- stream open should not incur an extra mux round trip
- `RESET` and `CLOSE` should not be indefinitely delayed behind bulk data
- `MAX_DATA` should be advanced promptly enough to avoid unnecessary stalls
- `BLOCKED` should not be indefinitely delayed behind bulk data when the sender
  is window-limited
- urgent control handling should remain bounded through hard caps and
  coalescing rather than becoming an unbounded secondary memory sink
- small interactive streams should remain usable while large transfers are
  active
- implementations should limit individual `DATA` fragment serialization
  occupancy through bounded local fragment caps rather than relying solely on
  the negotiated `max_frame_payload`; this matters most on very slow links
- on very slow links, implementations should also avoid repetitive `BLOCKED`,
  keepalive, or similar small-control chatter when no meaningful limiting
  offset or liveness state has changed
- senders not emitting early application `DATA` or creating new streams before
  peer preface parsing completes
- local stream-ID reservation before `opening-frame-committed` preserving the
  peer-visible no-gap rule
- provisional-open cancellation consuming an earlier cancelled stream ID on the
  wire when a later same-class ID has already reached
  `opening-frame-committed`, rather than creating a skipped-ID gap
- `DATA|FIN`, `STOP_SENDING`, `RESET`, and `ABORT` remaining distinct protocol
  actions instead of being collapsed into one ambiguous close behavior
- implementations detecting and shedding abusive empty-frame or tiny-control
  floods rather than allowing unbounded CPU or queue churn
- implementations detecting and bounding repeated unmatched `PONG` traffic
- implementations continuing to read and parse underlying bytes so control
  frames can make progress even when `DATA` admission is blocked by local
  memory or flow-control policy
- implementations detecting and bounding rapid open-then-abort or
  open-then-reset churn rather than relying only on concurrent stream limits
- `debug_text` in error frames being valid UTF-8 and truncated at code-point
  boundaries when payload limits are tight
- `PONG` payloads being verbatim byte-for-byte copies of the triggering
  `PING` payload unless local PING padding policy appends an opaque suffix for
  a recognized padded `PING`
- frame bytes being serialized contiguously onto the underlying byte stream so
  frame contents from different streams cannot interleave

## 8. Shared wire examples

Implementations should share at least:

- valid preface examples
- valid preface examples carrying ignored `preface_padding`
- valid stream-open examples
- valid `MAX_DATA` examples
- valid `PING` / `PONG` examples
- valid `GOAWAY` and `CLOSE` examples
- valid `DATA|OPEN_METADATA|FIN` and zero-application-byte
  `DATA|OPEN_METADATA` examples with a valid `metadata_len` prefix
- valid `ABORT` or `CLOSE` examples carrying `debug_text`
- tolerance examples where duplicate singleton metadata invalidates only the
  metadata block while preserving the enclosing `DATA`
- invalid frame-scope examples for session-scoped frames on non-zero
  `stream_id`, stream-scoped frames on `stream_id = 0`, and
  `PRIORITY_UPDATE` on `stream_id = 0`
- invalid establishment examples such as `role = auto` with a zero
  `tie_breaker_nonce`, a truncated setting value, a repeated
  `preface_padding`, and `settings_len` above `4096`
- known-answer `ping_padding_tag` values and a padded `PING` carrying one
- valid `BLOCKED` examples
- valid `STOP_SENDING` examples
- valid `PRIORITY_UPDATE` examples when that extension is implemented
- invalid non-canonical varint examples, both in the frame header
  (`PROTOCOL`) and in a mandatory payload field (`FRAME_SIZE`)
- non-canonical standardized TLV values: a `stream_priority` value in
  `PRIORITY_UPDATE` (`FRAME_SIZE`) and a `retry_after_millis` DIAG value that
  is ignored
- invalid oversized-frame examples
- invalid frame-size and malformed-payload examples that pin the `FRAME_SIZE`
  versus `PROTOCOL` split of SPEC Section 4.3

See [WIRE_EXAMPLES.md](./WIRE_EXAMPLES.md) for a starting point.
