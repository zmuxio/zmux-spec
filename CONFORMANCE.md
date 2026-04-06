# zmux Conformance Guidance

This document is not a language-specific test suite.

Its purpose is to define the behavioral surface that independent
implementations should validate before claiming `zmux` interoperability.

## 1. Core wire interoperability

At minimum, a conforming core implementation should interoperate on:

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

Core conformance includes parsing, validation, and receiver-side handling of
`BLOCKED`. Proactive emission of `BLOCKED` remains an implementation policy
choice rather than a wire-compatibility requirement.

If an implementation claims `priority_update` support, it should also
interoperate on:

- negotiation of the `priority_update` capability
- not treating `priority_hints` or `stream_groups` as an independent update
  path without `priority_update`
- `PRIORITY_UPDATE` carrying `stream_priority`
- `PRIORITY_UPDATE` carrying `stream_group`
- ignoring `open_info` inside `PRIORITY_UPDATE`
- ignoring unknown TLVs inside a `PRIORITY_UPDATE` payload
- leaving omitted fields unchanged
- ignoring a `PRIORITY_UPDATE` payload with duplicate singleton advisory TLVs
  as one dropped advisory update rather than aborting the stream by default

If an implementation claims `open_metadata` support, it should also interoperate
on:

- negotiation of the `open_metadata` capability
- `DATA|OPEN_METADATA` carrying `stream_priority` on the first opening frame
- `DATA|OPEN_METADATA` carrying `stream_group` on the first opening frame
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
- `PING` with derived payload length `< 8`
- `PONG` with derived payload length `< 8`
- `ABORT` on `stream_id = 0`
- `DATA|OPEN_METADATA` without negotiated `open_metadata`
- `DATA|OPEN_METADATA` on an already opened stream
- forbidden non-zero flag combinations on frames other than the valid `DATA`,
  `DATA|OPEN_METADATA`, `DATA|FIN`, and `DATA|OPEN_METADATA|FIN` combinations
- duplicate singleton TLVs inside one `PRIORITY_UPDATE` payload
- duplicate standardized singleton DIAG-TLVs inside one enclosing control frame
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
- peer-created stream IDs skipping the next expected ID of their class
- unknown core frame types
- `role = auto` with `tie_breaker_nonce = 0`
- equal `tie_breaker_nonce` when both peers use `role = auto`

## 3. Extension-tolerance behavior

An implementation should demonstrate that it:

- ignores unknown capability bits
- ignores unknown setting IDs
- skips unknown TLVs in known namespaces
- ignores unknown `EXT` subtypes unless a stricter extension document says
  otherwise
- does not treat any stream-scoped `EXT` as implicitly opening a stream in
  core `zmux v1`

This is essential for forward evolution.

### 3.1 Repository claims

Conformance claims should be made separately for:

- `zmux-wire-v1`
- `zmux-api-semantics-profile-v1`
- `zmux-stream-adapter-profile-v1`
- `zmux-open_metadata`
- `zmux-priority_update`

### 3.2 Implementation profiles

Repository-level implementation profiles are:

- `zmux-core-v1`: mandatory single-link core wire interoperability plus forward
  extension tolerance, including explicit-role and `role = auto`
  establishment
- `zmux-full-v1`: `zmux-core-v1` plus every currently active standardized
  same-version optional surface in this repository, including `open_metadata`,
  `priority_update`, and the correct negotiated handling of `priority_hints`
  and `stream_groups`
- `zmux-reference-profile-v1`: `zmux-full-v1` plus the repository-default API,
  sender, memory, liveness, and scheduling guidance

### 3.3 Profile compatibility rule

Profile compatibility rule:

- `zmux-full-v1` implementations MUST interoperate cleanly with
  `zmux-core-v1` peers by negotiating and using only the capabilities both
  sides share
- `zmux-reference-profile-v1` does not change wire requirements; it narrows
  local behavior toward the repository-default guidance

### 3.4 Reference-profile claim gate

Reference-profile claim gate:

- repository-default `CloseRead()` emits `STOP_SENDING(CANCELLED)` unless a
  binding intentionally exposes an explicit caller-supplied-code variant
- repository-default `Close()` acts as a full local close helper
- repository-default `Close()` on a unidirectional stream silently ignores the
  locally absent direction rather than failing solely because that half does
  not exist
- before `session-ready`, repository-default sender behavior emits only the
  local preface and a fatal establishment `CLOSE`, and emits none of:
  new-stream `DATA`, stream-scoped control, ordinary session-scoped control,
  or `EXT`
- repository-default sender and receiver memory rules enforce the documented
  hidden-state, provisional-open, and late-tail bounds
- repository-default liveness rules keep at most one outstanding protocol
  `PING` and do not treat weak local signals as strong progress

### 3.5 Claim checklist

The table below summarizes the repository-default readiness checklist for each
claim or implementation-profile level. It does not replace the detailed
scenario lists later in this document; it is a compact gate summary for
implementation planning and release review.

| Claim or profile | Minimum acceptance checklist |
| --- | --- |
| `zmux-wire-v1` | pass core wire interoperability; pass invalid-input handling; pass extension-tolerance behavior |
| `zmux-open_metadata` | satisfy `zmux-wire-v1`; negotiate `open_metadata`; accept valid `DATA|OPEN_METADATA` on first opening `DATA`; reject unnegotiated or misplaced `OPEN_METADATA`; ignore unknown metadata TLVs; drop duplicate singleton metadata while preserving the enclosing `DATA` |
| `zmux-priority_update` | satisfy `zmux-wire-v1`; negotiate `priority_update`; process `stream_priority` and `stream_group`; ignore `open_info` inside `PRIORITY_UPDATE`; ignore unknown advisory TLVs; ignore duplicate singleton advisory updates as one dropped update |
| `zmux-api-semantics-profile-v1` | document and implement the repository-default stream lifecycle surface from [API_SEMANTICS.md](./API_SEMANTICS.md), including `Close()`, `CloseRead()`, `CloseWrite()`, `Reset()`, structured error surfacing, open/cancel behavior, and accept visibility rules |
| `zmux-stream-adapter-profile-v1` | satisfy the stream-adapter subset from [API_SEMANTICS.md](./API_SEMANTICS.md), including bidirectional/unidirectional open and accept mapping, adapter method mapping, and documented limits/non-goals |
| `zmux-core-v1` | satisfy `zmux-wire-v1`; interoperate on explicit-role and `role = auto` establishment; pass core stream-lifecycle scenarios; pass core flow-control scenarios; pass core session-lifecycle scenarios |
| `zmux-full-v1` | satisfy `zmux-core-v1`; satisfy every currently active same-version optional surface in this repository, currently `zmux-open_metadata`, `zmux-priority_update`, and the correct negotiated handling of `priority_hints` and `stream_groups`; interoperate cleanly with `zmux-core-v1` peers by using only shared negotiated capabilities |
| `zmux-reference-profile-v1` | satisfy `zmux-full-v1`; satisfy the reference-profile claim gate above; meet the quality behaviors to observe closely enough to preserve the documented repository-default sender, memory, liveness, API, and scheduling behavior |

## 4. Core stream-lifecycle scenarios

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
- independent enforcement of bidirectional and unidirectional incoming-stream
  limits
- `STOP_SENDING` causing the peer to stop future `DATA` on one direction while
  the opposite direction remains usable
- half-close in one direction while the reverse direction remains active
- EOF surfaced only after buffered data is drained following peer `FIN`
- local `Close` acting as a full local close helper that ends ordinary use of
  both halves under the repository-default API profile
- `CloseWrite` preventing further local writes while reads remain usable
- repository-default `CloseRead()` emitting `STOP_SENDING(CANCELLED)` unless
  an explicit caller-selected-code variant is used
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
- repository-default whole-stream close-with-error helpers mapping optional
  reason text to DIAG-TLV `debug_text` while preserving the numeric `ABORT`
  code
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

## 5. Core flow-control scenarios

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
  reset or refusal
- session `MAX_DATA` advanced when late `DATA` for already closed streams is
  dropped via compact tombstone or equivalent used-ID bookkeeping
- local read-side stop discarding unread data while restoring session receive
  budget but suppressing further stream-scoped replenishment by default
- repository-default late-data policy enforcing both per-direction and
  aggregate session caps for stopped directions
- overflow protection on malicious or corrupted `MAX_DATA` values

## 6. Core session-lifecycle scenarios

At minimum, test:

- normal session startup with parallel preface exchange
- immediate post-preface first `DATA` on a new stream
- no extra mux acknowledgement being required once session establishment is
  complete and stream-ID ownership is resolved
- variable-length `PING` echoed byte-for-byte by `PONG`
- locally originated `PING` payload length bounded by the smaller of local and
  peer control-payload limits
- repeated `GOAWAY` with non-increasing bidirectional and unidirectional
  acceptance watermarks
- peer `GOAWAY` causing later local open attempts beyond the allowed
  watermark to fail synchronously
- peer `GOAWAY` causing never-peer-visible local streams beyond the allowed
  watermark to be reclaimed locally without waiting for explicit peer
  `ABORT(REFUSED_STREAM)`
- `CLOSE` terminating all active streams
- underlying transport closing without a prior `CLOSE`

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
- repository-default sender profiles not emitting early application `DATA`
  or creating new streams before peer preface parsing completes
- repository-default bindings not exposing numeric `StreamID()` values before
  `opening-frame-committed`, except on explicitly documented compatibility
  surfaces
- repository-default handling of provisional-open cancellation consuming an
  earlier cancelled stream ID on the wire when a later same-class ID has
  already reached `opening-frame-committed`, rather than creating a skipped-ID
  gap
- repository-default API and stream-adapter profiles using mainstream
  stream-style names such as `Close()`, `CloseRead()`, `CloseWrite()`, and
  `Reset()`, with `Close()` documented as a full local close helper rather
  than an undocumented send-half-only shorthand, and explicit
  close-with-error helpers carrying code plus optional reason text when
  exposed
- repository-default bulk protection preserving a bounded minimum class share
  when bulk and interactive work are both continuously active
- repository-default implementations detecting and shedding abusive empty-frame
  or tiny-control floods rather than allowing unbounded CPU or queue churn
- repository-default implementations continuing to read and parse underlying
  bytes so control frames can make progress even when `DATA` admission is
  blocked by local memory or flow-control policy
- repository-default implementations detecting and bounding rapid open-then-
  abort or open-then-reset churn rather than relying only on concurrent stream
  limits

## 8. Shared wire examples

Implementations should share at least:

- valid preface examples
- valid stream-open examples
- valid `MAX_DATA` examples
- valid `PING` / `PONG` examples
- valid `GOAWAY` and `CLOSE` examples
- valid `DATA|OPEN_METADATA|FIN` and zero-length `DATA|OPEN_METADATA` examples
- valid `ABORT` or `CLOSE` examples carrying `debug_text`
- tolerance examples where duplicate singleton metadata invalidates only the
  metadata block while preserving the enclosing `DATA`
- invalid establishment examples such as `role = auto` with a zero
  `tie_breaker_nonce`
- valid `BLOCKED` examples
- valid `STOP_SENDING` examples
- valid `PRIORITY_UPDATE` examples when that extension is implemented
- invalid non-canonical varint examples
- invalid oversized-frame examples

See [WIRE_EXAMPLES.md](./WIRE_EXAMPLES.md) for a starting point.
