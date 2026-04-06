# zmux API Semantics

This document defines the repository-level default cross-language stream
contract.

Its purpose is to give independent language bindings one common high-level
stream contract so that `zmux` behaves consistently across implementations.

## 1. Stream model

A bidirectional `zmux` stream is expected to be exposed as a connection-style
byte stream with:

- ordered `Read`
- ordered `Write`
- write-half close
- read-side stop
- send-half abort
- full-stream abort

Unidirectional streams should be exposed distinctly when the host language or
adapter surface can represent that distinction.

### 1.1 Local object layers

Repository-default API and implementation discussions distinguish three local
layers:

- wire-known stream state: protocol lifecycle and accounting state created
  once a stream ID is used on the wire
- application-visible stream object: the object exposed through ordinary open
  or accept APIs
- provisional local stream handle: a local pre-commit handle that may exist
  before any wire-visible stream ID has been consumed

These layers are intentionally not identical:

- a control-opened-only stream may be wire-known without becoming
  application-visible
- a provisional local open may exist locally before any peer-visible stream ID
  exists on the wire
- an application-visible stream object may appear only after earlier
  control-path state has already changed its writeability or terminal status

Repository-default terminology:

- `provisional`: a local stream handle exists, but no wire-visible stream ID
  has been consumed yet
- `opening-frame-committed`: the first opening-eligible frame has entered the
  local serialization order for that stream class; after this point the local
  `stream_id` is consumed
- `transport-submitted`: bytes have been handed to the underlying transport or
  wrapper; this does not by itself prove remote awareness or delivery
- `peer-visible`: the local implementation has protocol evidence that the peer
  is aware of the stream's existence, such as any accepted peer-originated
  stream-scoped frame for that stream or another protocol reaction that
  references that exact stream ID
- `session-ready`: both prefaces have been parsed successfully and local
  stream-ID ownership is resolved
- `stream-local signalling remains valid`: a stream-scoped local response can
  still be sent without itself creating, reviving, or re-opening the stream and
  without depending on a previously unseen peer-owned stream ID becoming
  signallable

## 2. Application-visible incoming streams

Default cross-language contract:

- a peer-opened stream becomes application-visible only when the first inbound
  `DATA` or `DATA|FIN` for that stream is accepted
- a first inbound `ABORT` on a previously unseen valid stream ID may create
  internal terminal bookkeeping without making that stream
  application-visible
- a first inbound `RESET` on a previously unseen valid stream ID is not hidden
  stream state in `zmux v1`; it is a session `PROTOCOL` error
- a previously unseen stream opened only by control-path state should not be
  queued to `AcceptStream()` by default

Accept-queue order:

- maintain separate bidirectional and unidirectional accept queues when the API
  surface distinguishes them
- within each queue, order application-visible peer-opened streams by the
  moment they first become application-visible
- implementations should assign each newly visible peer-opened stream a local
  monotonic visibility sequence number and enqueue by that sequence rather than
  retroactively reordering by stream ID
- control-opened-only streams should not occupy ordinary accept-queue slots by
  default
- a stream may become application-visible only after earlier control-path state
  has already changed its writeability or terminal status; an accepted stream
  therefore need not imply both halves are still fully open

Repository-default invisible-state bounds:

- implementations should cap the amount of hidden control-opened bookkeeping
  retained outside the ordinary accept queue
- implementations should cap the total bytes retained for not-yet-accepted
  peer-opened streams
- if hidden control-opened bookkeeping remains application-invisible beyond a
  local policy TTL, the implementation should reap or compress it according to
  documented policy

Repository-default invisible-state budgeting should distinguish ordinary
application-visible accept headroom from hidden control-opened-only headroom.
Implementations SHOULD maintain:

- a soft headroom for hidden control-opened bookkeeping
- a hard cap beyond which hidden control-opened state is shed promptly
- a local maximum age for application-invisible control-opened-only state

Repository-default policy when those bounds are exceeded is:

- refuse the newest still-application-invisible peer-opened stream first
- use `ABORT(REFUSED_STREAM)` for that rejected or reaped stream when a
  stream-local response remains valid
- escalate to session-scoped failure only when broader local memory pressure
  makes stream-local shedding insufficient
- reclaim hidden unread bytes through the same discard-and-budget-release path
  used for other local discards

In active single-link `zmux v1`, repository-default control-opened-only state
arises mainly from `ABORT`-first observations on unseen peer-owned streams.
Because `ABORT` is terminal and `RESET` / `STOP_SENDING` do not open a
previously unseen stream in the repository-default core, such control-opened
state is expected to remain hidden terminal bookkeeping rather than later
becoming ordinary application-visible stream data state.

Repository-default diagnostics SHOULD expose counters for:

- hidden streams refused
- hidden streams reaped by TTL or policy
- hidden unread bytes discarded

If an implementation deliberately exposes a different accept order, it should
document that explicitly because it becomes observable application behavior.

Bindings that need lower-level observability MAY expose richer accept metadata
or a separate lifecycle-event surface indicating whether a stream experienced
hidden control-path state before it became application-visible. Repository-
default ordinary `AcceptStream()` remains intentionally simpler and does not
require that extra event surface.

When a stream becomes application-visible through a first `DATA` /
`DATA|FIN` carrying `OPEN_METADATA`, bindings MAY additionally expose the
decoded open-time metadata alongside the accepted stream, for example through:

- a richer accept result object
- an `OpenInfo()` / `Metadata()` query on the stream object
- a separate accept-metadata event surface

Repository-default ordinary `AcceptStream()` and `AcceptUniStream()` remain
usable without any metadata object in the common case where the opener did not
attach open-time metadata.

## 3. Read behavior

Default read behavior:

- `Read` returns bytes in wire order
- remote `DATA|FIN` does not cause immediate EOF until already buffered bytes
  are drained
- after buffered bytes are drained, `Read` returns EOF
- remote `RESET` should surface as a terminal read-half error rather than EOF
- once remote `RESET` becomes visible to the API, unread buffered inbound bytes
  for that direction should be discarded and subsequent `Read` calls should
  return the terminal reset error rather than continue draining pre-reset data
- remote `RESET` does not by itself require local writes to fail immediately
- remote `ABORT` should surface as a terminal whole-stream error rather than
  EOF
- once remote `ABORT` becomes visible to the API, unread buffered inbound
  bytes should be discarded and both subsequent `Read` and `Write` calls
  should fail

Repository-default visibility point:

- parser or ingress processing first commits the terminal half or full-stream
  state into shared local stream state
- blocked readers and writers are then woken against that committed state
- one blocked `Read` call should either:
  - complete with already buffered pre-terminal bytes if the terminal state had
    not yet become visible to that call; or
  - fail with the terminal reset or abort error after that state becomes
    visible
- one `Read` call should not mix newly visible post-terminal error semantics
  with additional pre-terminal bytes beyond the buffer snapshot it had already
  been allowed to consume

For a local read-side stop (`CloseRead`, `CancelRead`, or equivalent):

- `CloseRead` is the repository-default reader-side stop primitive in `zmux`
- unread inbound bytes may be discarded immediately
- subsequent `Read` calls should fail with a local read-stopped or cancelled
  error rather than graceful EOF
- the peer may still produce a bounded amount of late `DATA` that was already
  in flight before it processed `STOP_SENDING`
- repository-default flow-control policy restores released session budget but
  suppresses further stream-local replenishment for that stopped direction
  unless a bounded late-data policy is explicitly documented
- repository-default late-data policy SHOULD bound both:
  - one per-stopped-direction tail allowance
  - one aggregate session-wide allowance across all stopped directions
- if the aggregate late-data allowance is exceeded, repository-default policy
  SHOULD discard additional late tail data and MAY escalate to stream-local or
  session-local failure when local memory pressure makes continued absorption
  unsafe

## 4. Write behavior

Default write behavior:

- a successful local `Write` only means the bytes entered the local `zmux`
  send path
- it does not prove the peer application has accepted the stream
- after local `CloseWrite` or repository-default `Close`, further `Write`
  calls should fail immediately with a write-side-closed error
- after local `Reset`, further `Write` calls on that direction should fail
  immediately
- after peer `STOP_SENDING`, implementations should fail future local writes as
  soon as that stop becomes locally visible with a write-cancelled or
  equivalent terminal error
- writers already blocked on local send-queue space for that direction should
  be unblocked promptly with the same local cancellation/terminal error
- after local or remote `ABORT`, further `Write` calls should fail immediately

## 5. Send-queue backpressure

Implementations should define explicit queued-byte limits instead of allowing
unbounded send-path buffering.

Recommended default shape:

- one session-level queued-byte high watermark and low watermark
- one per-stream queued-byte high watermark and low watermark
- urgent control frames may bypass ordinary data-queue high-watermark checks
- ordinary `Write` should block, fail on deadline, or fail on cancellation once
  the relevant high watermark is reached
- blocking is the preferred default when the host language naturally supports
  it
- if the host language does not expose natural blocking stream APIs,
  implementations should return an explicit retryable backpressure error
  rather than continue growing memory indefinitely
- ordinary `Write` should not continue consuming memory indefinitely after
  watermarks are exceeded
- implementations should bound pending application-invisible peer-opened stream
  state, including:
  - pending unaccepted peer-opened streams
  - total bytes buffered before application handoff
  - control-opened-only stream tombstones or equivalent bookkeeping

Repository-default capacities:

- `per_stream_data_hwm = max(256 KiB, 16 * negotiated max_frame_payload)`
- per-stream data low watermark: 50% of the per-stream high watermark
- `session_data_hwm = max(4 MiB, 4 * per_stream_data_hwm)`
- session data low watermark: 50% of the session high watermark
- urgent-lane hard cap:
  `max(64 KiB, 8 * negotiated max_control_payload_bytes)`
- repository-default late-data allowance after local `CloseRead`:
  `min(2 * negotiated max_frame_payload, initial_stream_window / 8)`, where
  `initial_stream_window` means the negotiated initial stream-scoped receive
  limit for that stream kind
- repository-default aggregate late-data allowance across all stopped
  directions: `max(64 KiB, 4 * negotiated max_frame_payload)`
- repository-default hidden control-opened soft headroom:
  `max(16, max_pending_unaccepted_streams / 4)` when such a stream-count limit
  exists locally
- repository-default hidden control-opened hard cap:
  `max(32, max_pending_unaccepted_streams / 2)` when such a stream-count limit
  exists locally
- repository-default hidden control-opened max age:
  `1s` when local timers are available
- repository-default provisional-open soft cap per stream class:
  `max(16, max_pending_unaccepted_streams / 4)` when such a stream-count limit
  exists locally
- repository-default provisional-open hard cap per stream class:
  `max(32, max_pending_unaccepted_streams / 2)` when such a stream-count limit
  exists locally
- repository-default provisional-open max age:
  `250ms` when local timers are available

If the host platform exposes deadline or cancellation primitives, send-side
blocking should honor them consistently.

Repository-default provisional-open overflow policy is:

- fail the newest still-uncommitted provisional open first
- return a retryable local error rather than silently stretching the queue
- release any provisional reservation immediately
- do not consume a stream ID when the provisional open never reached
  `opening-frame-committed`

## 6. Close operations

### 6.1 Default wire mapping

Default close mapping:

- `CloseWrite()` -> emit `DATA|FIN`
- `CloseRead()` -> emit `STOP_SENDING(CANCELLED)`, discard unread inbound data
  under the repository-default policy, and thereafter fail local `Read` calls
  on that direction
- an explicit caller-supplied-code read-stop variant, if exposed ->
  emit `STOP_SENDING(code)`
- `Reset(code)` -> emit `RESET(code)`
- `CloseWithError(err)` or `CloseWithErrorCode(code, reason)` -> emit
  `ABORT(code)` and carry optional diagnostic text when the local error
  surface provides one
- `Close()` -> repository-default full-stream close helper that ends ordinary
  local use of the stream and commits graceful local shutdown through
  `CloseWrite()` plus `CloseRead()` under local policy

### 6.2 Repository-default primary names

Repository-default naming intentionally follows mainstream stream and
connection-style surfaces:

- use `Close()` for ordinary full stream close
- use `CloseWrite()` for graceful send-side completion
- use `CloseRead()` for reader-side stop
- use `Reset()` for send-side abortive termination
- avoid inventing new primary verbs when established stream-style names are
  already sufficient

Repository-default cross-language surfaces should prefer these primary method
names:

- `Close()` for full local stream close
- `CloseWrite()` for graceful send-half completion
- `CloseRead()` for reader-side stop
- `Reset()` aborts only the local send half
- `Read()` / `Write()` for byte-stream I/O
- `StreamID()` / `ID()` for the numeric wire ID when exposed

For new bindings, exposing `Close()`, `CloseWrite()`, `CloseRead()`, and
`Reset()` is RECOMMENDED. Bindings SHOULD also expose an explicit abortive
whole-stream close helper that can carry both a numeric application error code
and optional diagnostic reason text, for example through `CloseWithError(err)`
with a structured application error value or through
`CloseWithErrorCode(code, reason)`.

### 6.3 Repository-default `Close()` helper

Repository-default `Close()` is not a half-close synonym. It is a full local
stream-close helper:

- if the local send half is still open, it commits graceful send completion
  through `CloseWrite()`
- if the local read half is still open, it commits reader-side stop through
  `CloseRead()` using the repository-default `CANCELLED` stop code
- after `Close()` becomes visible locally, further local `Read` and `Write`
  calls should fail promptly
- bindings MAY additionally wait for bounded local drain or peer
  acknowledgement when that blocking contract fits the host-language I/O
  surface, but they MUST document that choice explicitly
- for unidirectional streams, `Close()` should silently ignore any locally
  absent direction rather than surfacing an error solely because that half does
  not exist; it therefore behaves like `CloseWrite()` on a local send-only
  stream and like `CloseRead()` on a local receive-only stream

### 6.4 Compatibility aliases

If a binding exposes an explicit caller-supplied-code read-stop variant,
`CancelRead(code)` SHOULD alias that variant, preferably spelled
`CloseReadWithCode(code)`.

Otherwise, `CancelRead()` SHOULD alias repository-default `CloseRead()`.

If a surface also exposes `CancelWrite()` or `ResetWrite()`, repository-
default behavior is to treat them as documented compatibility aliases for
`Reset()`.

If a surface also exposes `Abort()` or `AbortWithError()`, repository-default
behavior is to treat them as documented compatibility aliases for
`CloseWithError(...)`.

Repository-default naming preference for new bindings is:

- prefer `CloseRead()` over `CancelRead()`
- prefer `Reset()` over `CancelWrite()` or `ResetWrite()`
- prefer `CloseWithError(...)` over `Abort()` or `AbortWithError(...)`
- prefer `StreamID()` or `ID()` over ad-hoc numeric-ID getter names
- treat `CancelRead()`, `CancelWrite()`, `ResetWrite()`, and
  `Abort()` / `AbortWithError()` as compatibility aliases only when a binding
  deliberately keeps them
- prefer one explicit whole-stream close-with-error helper that carries code
  and optional reason text rather than multiple competing abort spellings

## 7. Error mapping

Default minimum error surface:

- protocol/session errors
- stream reset errors with numeric code
- write-after-close errors
- local cancellation errors
- remote application-defined error codes

Repository-default bindings SHOULD expose, preserve, or make queryable a more
structured local error shape when the host language allows it. Recommended
fields are:

- `scope`: `session` or `stream`
- `operation`: `open`, `accept`, `read`, `write`, or `close`
- `source`: `local`, `remote`, or `transport`
- `wire_code`: numeric wire error code when one exists
- `reason_text`: optional diagnostic reason text when available locally or on
  the wire
- `direction`: `read`, `write`, or `both`
- `termination_kind`: `graceful`, `reset`, `abort`, or `session_termination`

Bindings that expose explicit abortive close helpers SHOULD support carrying
both a numeric code and optional reason text. A structured application error
value with `Code` and optional `Reason` is the repository-default model. A
binding MAY instead expose separate `(code, reason)` parameters if that is
more idiomatic for the host language.

Repository-default reason-text mapping is:

- `CloseWithErrorCode(code, reason)` sends `ABORT(code)`
- if `reason` is non-empty, it is encoded as DIAG-TLV `debug_text`
- `reason` MUST be valid UTF-8
- if local control-payload limits do not permit carrying the full reason text,
  the implementation MUST preserve the numeric code first and then apply this
  repository-default rule:
  - keep any non-`debug_text` diagnostic fields already selected
  - truncate `debug_text` only at valid UTF-8 code-point boundaries to the
    remaining available payload space
  - if no valid UTF-8 prefix fits, omit `debug_text`
- surfaced remote `reason_text`, when exposed, comes from the peer's
  DIAG-TLV `debug_text`

When the session receives `CLOSE` or the underlying transport fails, blocked
`Read`, `Write`, and accept operations should be unblocked promptly and fail
with a session-termination error rather than hang indefinitely.

Core `zmux` error codes occupy `0-255`. Values above that range should be
surfaced unchanged rather than collapsed into generic transport errors.

## 8. Open semantics

Default caller expectations:

- opening a stream does not wait for peer acknowledgement
- the first local `Write` on a new stream may race with a later peer refusal
- peer refusal should surface as a later stream error via `ABORT`
- repository-default bindings expose `OpenStream(ctx)` or an equivalent
  cancellable open operation
- if open or first-write cancellation happens after the stream has reached
  `opening-frame-committed` or after an inbound frame for that stream has been
  accepted locally, the implementation should send `ABORT(CANCELLED)` while
  outbound stream signalling remains valid

Repository-default sender APIs should treat local open as a two-stage action:

- a provisional open may exist locally before any numeric `stream_id` is
  committed to wire order
- a stream ID becomes consumed only when the first opening-eligible frame is
  committed into the class-specific opening sequence
- cancellation before that commit point should fail locally without consuming a
  wire-visible stream ID or creating a peer-observable gap
- repository-default bindings SHOULD NOT expose numeric wire `stream_id` values
  before `opening-frame-committed`
- if a compatibility surface nevertheless exposes a numeric `StreamID()` /
  `ID()`-style result before that point, it MUST treat that ID as reserved and
  later surface it on the wire rather than silently reusing it
- such early exposure SHOULD be documented as an advanced or compatibility
  surface because it can reduce concurrent open throughput: later locally
  opened streams of the same class must remain behind the reserved earlier ID
  in wire serialization order to avoid protocol-fatal gaps
- repository-default bindings SHOULD therefore prefer opaque provisional
  handles over exposing numeric wire IDs before the stream is active

Repository-default sender APIs MAY additionally expose open-time metadata
fields, such as:

- `initial_priority`
- `initial_group`
- `open_info`

Those open-time metadata inputs behave as follows:

- `initial_priority` and `initial_group` may influence first-batch scheduling
  and local queue classification before any later advisory update can exist
- when `open_metadata` is negotiated and the first opening frame is `DATA` /
  `DATA|FIN`, repository-default implementations SHOULD carry supported
  open-time metadata on that first frame through `OPEN_METADATA`
- when `open_metadata` is not negotiated, or when the stream does not open
  with `DATA`, `initial_priority` and `initial_group` still remain valid local
  sender-policy inputs unless later `PRIORITY_UPDATE` frames carry the
  corresponding standardized advisory values
- `open_info` is intended as peer-visible open-time metadata, not a local-only
  hint; if a caller supplies `open_info` but `open_metadata` is unavailable,
  repository-default bindings SHOULD fail or reject that option rather than
  silently dropping it
- if `open_info` is supplied and `open_metadata` is negotiated, repository-
  default bindings SHOULD still fail or reject the open request rather than
  silently degrading to plain `DATA` when the metadata block cannot be carried
  on the opening frame because of frame-payload or local memory limits
- lack of stream or session `MAX_DATA` alone is not a reason to reject
  `open_info`, because `OPEN_METADATA` bytes do not consume those receive
  windows
- `open_info` is one opaque byte string at the `zmux` layer; applications that
  want to attach multiple logical values should encode them into that byte
  string and decode them above `zmux`
- `open_info` is suitable for small open-time attributes or headers-like
  metadata; larger metadata blocks should usually travel in ordinary stream
  payload instead
- repository-default bindings MAY reject over-large `open_info` according to
  negotiated frame-payload and local memory limits rather than buffering it
- the local binding MUST NOT claim peer-visible open-time metadata unless the
  relevant wire capability and opening `DATA` carriage were actually used

This matches `zmux`'s no-per-stream-ack opening model and should be documented
clearly in user-facing APIs.

Bindings MAY also expose convenience surfaces that collapse common first-batch
patterns into one call, for example:

- `WriteFinal(...)` / `WritevFinal(...)` for one-shot `DATA|FIN`
- `OpenAndSend(...)` / `OpenUniAndSend(...)` with open-time options and
  immediate first payload submission

Ordinary `OpenStream()` / `OpenUniStream()` usage remains metadata-free by
default. Carrying open-time metadata is an opt-in sender choice.

## 9. Cancellation and deadlines

Implementations should map local cancellation or timeout onto stream failure
consistently.

Default cancellation and deadline behavior:

- local cancellation before any bytes are sent -> fail locally without opening
  a stream
- if local cancellation targets an earlier provisional open after a later
  stream ID of the same class has already reached `opening-frame-committed`,
  repository-default behavior is to consume the cancelled earlier ID on the
  wire with `ABORT(CANCELLED)` rather than creating a skipped-ID gap
- local cancellation after the stream has reached `opening-frame-committed` or
  after an inbound frame for that stream has been accepted locally:
  - send `RESET(CANCELLED)` when cancelling only the local send half
  - send `ABORT(CANCELLED)` when aborting the whole stream
- read-side cancellation with response still needed -> use `CloseRead()` /
  `STOP_SENDING(CANCELLED)` rather than full `ABORT`
- `SetDeadline`, `SetReadDeadline`, and `SetWriteDeadline`, if exposed, should
  affect only local blocking behavior; they do not change wire semantics by
  themselves
- `AcceptStream(ctx)` / `AcceptUniStream(ctx)`, if exposed, should unblock on
  session termination, deadline expiry, or cancellation rather than hang
- after peer `GOAWAY` prevents further locally opened streams of a given kind,
  subsequent local open operations of that kind should fail synchronously
  rather than creating user-visible stream objects that cannot be opened
- after peer `GOAWAY` narrows the accepted stream-ID watermark for a given
  kind, any locally created stream of that kind whose `stream_id` exceeds the
  permitted watermark and has not yet become `peer-visible` should be failed
  locally immediately and released without waiting for an explicit peer
  `ABORT(REFUSED_STREAM)`

Repository-default cancellation matrix:

| Scenario | Stream ID consumed? | Peer-visible? | Default action |
| --- | --- | --- | --- |
| provisional open cancelled before `opening-frame-committed` | no | no | fail locally only |
| earlier provisional open cancelled after a later same-class stream reached `opening-frame-committed` | yes | not required | emit `ABORT(CANCELLED)` to consume the cancelled earlier ID |
| cancel only the local send half on an existing stream | yes | any | emit `RESET(CANCELLED)` |
| read-side cancellation while the opposite direction may still matter | yes | any | `CloseRead()` / `STOP_SENDING(CANCELLED)` |
| whole-stream cancellation after open | yes | any | emit `ABORT(CANCELLED)` |

## 10. Stream adapter profile

This section defines the repository-default stream-adapter profile.

It is a narrower stream-oriented surface layered on the cross-language
contract above.

### 10.1 Supported adapter subset

The following concepts map well between a stream-oriented adapter surface and
`zmux`:

- opening bidirectional streams
- opening unidirectional streams
- accepting bidirectional streams
- accepting unidirectional streams
- ordered `Read`
- ordered `Write`
- write-half close
- read-side cancellation
- write-side cancellation / reset
- numeric stream identifiers
- numeric application-defined stream errors

### 10.2 Explicit non-goals

This adapter profile does **not** attempt to model:

- datagrams
- cryptographic handshake state
- packet acknowledgements
- packet-loss recovery state
- congestion-window state
- packet- or path-level identifiers
- transport migration

Those are outside the scope of `zmux` stream multiplexing.

### 10.3 Stream-type correspondence

`zmux v1` already distinguishes bidirectional and unidirectional streams in the
low two bits of `stream_id`.

Recommended adapter mapping:

- local bidirectional open -> local next bidirectional `stream_id`
- local unidirectional open -> local next unidirectional `stream_id`
- peer bidirectional accept -> peer-owned bidirectional `stream_id`
- peer unidirectional accept -> peer-owned unidirectional `stream_id`

### 10.4 Method mapping

Default method-level correspondence:

- `OpenStream` -> local bidirectional stream open
- `OpenUniStream` -> local unidirectional stream open
- `AcceptStream` -> accept next application-visible peer-opened bidirectional
  stream
- `AcceptUniStream` -> accept next application-visible peer-opened
  unidirectional stream
- `StreamID()` / `ID()` -> locally known numeric stream ID when exposed
- `Read` -> `Read`
- `Write` -> `Write`
- `Close()` -> repository-default full-stream close helper
- `CloseWrite()` -> `DATA|FIN`
- `CloseRead()` -> `STOP_SENDING(CANCELLED)` as the repository-default
  receiver-side close control for one stream direction; an explicit
  caller-supplied-code variant may expose a caller-selected code
- `Reset(code)` -> `RESET(code)`
- explicit native whole-stream close-with-error helper, if exposed ->
  `CloseWithError(err)` or `CloseWithErrorCode(code, reason)` ->
  `ABORT(code)` with optional DIAG-TLV `debug_text`

Default adapter behavior:

- plain `Close()` in a stream adapter should end ordinary local use of the
  stream rather than acting as an undocumented half-close-only helper
- repository-default `Close()` SHOULD commit `CloseWrite()` and `CloseRead()`
  when those halves are still locally open
- new adapter bindings SHOULD prefer `OpenStream()` / `OpenUniStream()` as the
  primary outbound-open names
- adapter surfaces SHOULD expose `CloseWrite()` separately for graceful
  send-half completion
- adapter surfaces SHOULD expose `CloseRead()` separately for reader-side stop
- adapter surfaces SHOULD expose `Reset(code)` for send-side abortive
  cancellation
- adapter surfaces SHOULD expose one explicit whole-stream close-with-error
  helper that can carry code and optional reason text
- if an adapter also exposes `CancelRead()`, repository-default behavior is to
  treat it as a documented alias for the adapter's caller-supplied-code
  read-stop variant or `CloseRead()`, depending on whether the adapter accepts
  a caller-supplied code
- if an adapter also exposes `CancelWrite(code)` or `ResetWrite(code)`,
  repository-default behavior is to treat it as a compatibility alias for
  `Reset(code)`
- if an adapter also exposes `Abort()` or `AbortWithError()`, repository-
  default behavior is to treat them as compatibility aliases for an explicit
  whole-stream close-with-error helper
- if an adapter also exposes `OpenBidi`, `OpenUni`, `OpenStreamSync`, or
  `OpenUniStreamSync`, those should be documented as compatibility or
  convenience aliases rather than the primary naming surface
- application-visible incoming streams should follow the same rules described
  in sections 2 and 8 of this document
- stream adapters should hide control-opened-only streams from the ordinary
  accept queue unless they intentionally expose lower-level internals
- adapter open calls MAY accept open-time scheduling hints, such as initial
  priority, group, or opaque `open_info` values; those inputs become
  peer-visible only when `OPEN_METADATA` is negotiated and actually used on
  the first opening `DATA`
- if `open_info` is supplied but `OPEN_METADATA` cannot be used on the opening
  frame, repository-default adapters MUST reject that request rather than
  silently discarding the metadata
- if only `initial_priority` or `initial_group` is supplied and
  `OPEN_METADATA` is unavailable, those inputs may still remain local sender
  policy unless later advisory updates are sent on the wire

### 10.5 Error-code mapping

`zmux` core error codes occupy `0-255`.

Values `>= 256` are transported unchanged and are suitable for
adapter-level application-defined stream errors.

Default adapter behavior:

- preserve remote numeric stream error codes when surfacing them locally
- do not collapse them into one generic reset error unless the host language
  forces that shape

### 10.6 Deadline and cancellation mapping

For stream adapters:

- cancellable open operations should map to `OpenStream(ctx)`-style local
  cancellation
- a provisional local open cancelled before first-frame commit should not
  consume a peer-observable `stream_id`
- bindings that expose numeric stream IDs early should either delay exposure
  until first-frame commit or treat the exposed ID as reserved and later
  surfaced on the wire
- local cancellation after the stream has reached `opening-frame-committed` or
  after an inbound frame for that stream has been accepted locally should
  attempt
  `RESET(CANCELLED)` when cancelling only the local send half, or
  `ABORT(CANCELLED)` when the adapter intentionally aborts the whole stream
- read deadlines should affect local blocking `Read`
- write deadlines should affect local blocking `Write`

Deadline expiry is local API behavior. It does not create new `zmux` wire
semantics by itself.

### 10.7 Adapter limits

Even with a good adapter, some behavioral differences remain:

- `zmux` has no native datagram path
- `zmux` does not expose packet-level loss or RTT state
- `zmux` over one underlying byte stream still inherits any head-of-line
  blocking already present in that transport
- `zmux v1` includes an explicit whole-stream `ABORT` primitive in addition to
  half-direction controls

Adapters should document these limits clearly rather than pretending transport
equivalence beyond the stream surface they actually implement.

## 11. Usage guidance

Repository-default API guidance should steer applications toward directional
operations instead of whole-stream aborts by default, while using mainstream
stream method names for those directional semantics.

Recommended operation choices:

- request body finished, response still expected -> `CloseWrite()`
- local side no longer wants to read, but the opposite direction may still
  matter -> `CloseRead()`
- local send half failed, but the opposite direction may still be useful ->
  `Reset(code)`
- the local application is done with the stream as a whole -> `Close()`
- the whole stream is no longer meaningful and the peer should receive an
  application-defined terminal error -> `CloseWithError(...)`

Bindings SHOULD document that:

- successful `Write` means bytes entered the local send path, not that the
  peer application accepted them
- `Close()` ends ordinary local use of the stream as a whole under the
  repository-default full-close helper semantics
- `CloseWrite()` finishes only the local send half
- `CloseRead()` stops local interest in further inbound bytes for that
  direction and emits `STOP_SENDING(CANCELLED)` by default
- a binding MAY additionally expose an explicit caller-supplied-code read-stop
  variant when it wants caller-chosen `STOP_SENDING` codes
- `Reset()` aborts only the local send half
- compatibility aliases such as `CancelRead()`, `CancelWrite()`, or
  `ResetWrite()` should preserve those same semantics when exposed
- explicit whole-stream close-with-error helpers are stronger than `Close()`
  and should surface numeric code plus optional reason text when they are
  exposed
