# zmux API Semantics

This document defines the repository-level default cross-language stream and
session contract.

Its purpose is to give independent language bindings one common high-level
semantic model while keeping exact API spellings flexible. It therefore
distinguishes operation families from concrete names, and it distinguishes an
ordinary stable surface from optional native or fuller-control surfaces.

## 1. Stream model

A bidirectional `zmux` stream is expected to be exposed as a connection-style
byte stream with:

- ordered `Read`
- ordered `Write`
- write-half close
- read-side stop
- send-half abort
- full-stream abort

Unidirectional streams should be exposed distinctly when the binding or adapter
surface can represent that distinction.

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
- `peer-visible`: for repository-default local-open bookkeeping, the opening
  frame has entered the local transport-submission path, or the local
  implementation has later observed an accepted peer-originated stream-scoped
  frame or other protocol reaction that references that exact stream ID; this
  is strong enough to stop local `GOAWAY`-style reclaim for that stream, but it
  still does not prove remote delivery
- `session-ready`: both prefaces have been parsed successfully and local
  stream-ID ownership is resolved
- `stream-local signalling remains valid`: a stream-scoped local response can
  still be sent without itself creating, reviving, or re-opening the stream and
  without depending on a previously unseen peer-owned stream ID becoming
  signallable
- `provisional-expired`: a provisional local stream handle has exceeded a
  local policy time limit without reaching `opening-frame-committed`;
  repository-default behavior is to fail that stream with a retryable local
  error and release any provisional reservation without consuming a stream ID

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

Diagnostic maps keyed by peer-selected or caller-selected application error
codes SHOULD have a bounded distinct-key footprint. Implementations SHOULD keep
counting already-retained keys and SHOULD expose or account for overflow
observations beyond the retained-key bound.

If an implementation deliberately exposes a different accept order, it should
document that explicitly because it becomes observable application behavior.

Bindings that need lower-level observability MAY expose richer accept metadata
or a separate lifecycle-event surface indicating whether a stream experienced
hidden control-path state before it became application-visible. Repository-
default ordinary `AcceptStream()` remains intentionally simpler and does not
require that extra event surface.

### 2.1 Lifecycle event surface

Repository-default implementations MAY expose an optional lifecycle event
surface for observability. When provided, the recommended event types are:

- `stream_opened`: a local stream has become `peer-visible`; repository-default
  implementations typically fire this once the opener has entered the local
  transport-submission path or once later peer-originated evidence for that
  exact stream ID has been observed
- `stream_accepted`: a peer-opened stream has been dequeued from the accept
  queue and returned to the application
- `session_closed`: the session has left the open or draining state

Event payload fields SHOULD include:

- stream ID (when applicable)
- whether the stream is locally or peer-opened
- whether the stream is bidirectional or unidirectional
- timestamp
- whether the stream is application-visible at the time of the event

Event handlers SHOULD be invoked synchronously without holding internal session
locks so that handler logic cannot deadlock the session. Ordinary handler
failures that are recoverable by the binding SHOULD be contained rather than
propagated as session errors. Fatal runtime failures or process-fatal signals
are outside the event best-effort contract.

Repository-default event surfaces are opt-in. Ordinary session and stream
operations MUST NOT depend on an event handler being registered.

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

For a local read-side stop (`CloseRead()`):

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
- after a write-oriented call returns, the caller SHOULD be free to reuse or
  mutate the supplied payload buffers immediately; implementations that queue
  internally therefore SHOULD detach any borrowed payload bytes before return
- it does not prove the peer application has accepted the stream
- repository-default ordinary `Write` with zero application bytes and no final
  intent SHOULD be a local no-op; it should not by itself force an opening
  `DATA` frame, make a stream peer-visible, or observe write-side terminal
  state
- a zero-length `WriteFinal(...)` / `WritevFinal(...)`, when those helpers are
  exposed, SHOULD behave like `CloseWrite()` and therefore still finish the
  local send half with `DATA|FIN`
- after local `CloseWrite` or repository-default `Close`, further material
  `Write` calls should fail immediately with a write-side-closed error
- after local `CloseWrite` has committed graceful send completion, later local
  use of the primary send-reset or send-cancel entry SHOULD also fail locally
  and SHOULD NOT retroactively replace an already-queued graceful `DATA|FIN`
  tail
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
- blocking is the preferred default when the binding's I/O model naturally
  supports it
- if the binding does not expose natural blocking stream APIs,
  implementations should return an explicit retryable backpressure error
  rather than continue growing memory indefinitely
- ordinary `Write` should not continue consuming memory indefinitely after
  watermarks are exceeded
- implementations should bound pending application-invisible peer-opened stream
  state, including:
  - pending unaccepted peer-opened streams
  - total bytes buffered before application handoff
  - control-opened-only stream tombstones or equivalent bookkeeping

Repository-default accept-queue notification model:

- accept queues use coalescing notification rather than per-stream signalling;
  a single notification wakes a blocked `AcceptStream` caller, which then
  drains all currently queued streams before blocking again
- this coalescing pattern bounds notification overhead when many streams arrive
  in a short burst
- the notification primitive SHOULD be bounded (for example, with capacity 1)
  so that rapid peer stream arrivals collapse into a single wakeup event

Repository-default capacities:

- `per_stream_data_hwm = max(256 KiB, 16 * negotiated max_frame_payload)`
- per-stream data low watermark: 50% of the per-stream high watermark
- `session_data_hwm = max(4 MiB, 4 * per_stream_data_hwm)`
- session data low watermark: 50% of the session high watermark
- urgent-lane hard cap:
  `max(64 KiB, 8 * negotiated max_control_payload_bytes)`
- coalescible urgent control such as pending `MAX_DATA` / `BLOCKED` may be
  emitted in urgent-lane-sized chunks; overflow remains dirty in the pending
  registry and must not be dropped merely because one writer batch is full
- if non-deferrable urgent control, or the handoff of coalesced pending control
  into a writer-owned batch, cannot be retained within the broader session
  memory cap, repository-default implementations should fail the session with
  `INTERNAL` rather than silently committing local terminal state without a
  corresponding control signal; if the resulting `CLOSE` itself cannot be
  retained under an extreme cap, the implementation may finish the local failed
  session without sending that final `CLOSE`
- repository-default late-data allowance after local `CloseRead`:
  `max(1 KiB, min(2 * negotiated max_frame_payload, initial_stream_window / 8))`,
  where `initial_stream_window` means the negotiated initial stream-scoped
  receive limit for that stream kind; the same repository-default per-stream
  cap is a reasonable default for ignored late tail after peer `RESET` or
  peer `ABORT` while stream-local late-tail accounting still exists
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
- repository-default hidden open-then-`ABORT` churn window:
  `1s`
- repository-default hidden open-then-`ABORT` churn threshold:
  `128`
- repository-default ignored terminal-control no-op handling:
  repeated `RESET`, `STOP_SENDING`, or `ABORT` frames that no longer change
  effective stream state count against the no-op control budget; an effective
  terminal control that does change stream state clears accumulated no-op
  control budget
- repository-default visible terminal churn handling:
  count only not-yet-accepted peer-owned application-visible streams that reach
  fully terminal state; do not count streams after the application has accepted
  them, local-opened streams, or bidirectional streams that have only a
  one-sided `RESET`
- repository-default provisional-open soft cap per stream class:
  `max(16, max_pending_unaccepted_streams / 4)` when such a stream-count limit
  exists locally
- repository-default provisional-open hard cap per stream class:
  `max(32, max_pending_unaccepted_streams / 2)` when such a stream-count limit
  exists locally
- repository-default provisional-open max age:
  `5s` base when local timers are available, widened when observed local
  `PING` RTT requires at least `6 * observed_ping_rtt + 250ms`, and capped at
  `20s`

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
- repository-default stream-style `CloseRead()` ->
  emit `STOP_SENDING(CANCELLED)`, discard unread inbound data under the
  repository-default policy, and thereafter fail local `Read` calls on that
  direction
- fuller read-stop control, if exposed -> emit `STOP_SENDING(code)` with
  optional diagnostics
- one primary send-reset or send-cancel entry -> emit `RESET(code)`; fuller
  send-reset control MAY additionally carry optional diagnostics when that
  surface is exposed
- the primary whole-stream abort entry in a convenience surface -> emit
  `ABORT(code)` and carry optional diagnostic text when that surface carries
  one
- `Close()` -> repository-default full-stream close helper that ends ordinary
  local use of the stream and commits graceful local shutdown through
  `CloseWrite()` plus `CloseRead()` under local policy

If read-side stop commits the local receive half before its opener dependency
or `STOP_SENDING` signal reaches the writer, the local read side remains
stopped. A later read-stop call SHOULD retry the still-pending outbound signal
until it is queued or the session becomes terminal.

### 6.2 Operation families

Repository-default API design distinguishes semantic operation families from
any one concrete method naming scheme.

Core stream operation families are:

- full local close helper
- graceful send-half completion
- read-side stop
- send-side reset
- whole-stream abort
- stream ID, open-time metadata, and advisory metadata observation when
  exposed

A binding MAY present those families through:

- a stream-style convenience profile using conventional byte-stream names
- a full-control protocol surface exposing caller-selected codes and optional
  diagnostics
- both, as long as the semantic mapping stays consistent

### 6.3 Stream-style convenience profile

The repository-default stream-style convenience profile intentionally follows
ordinary stream and connection-style surfaces. It SHOULD expose one primary
ordinary spelling for:

- full local stream close
- graceful send-half completion
- read-side stop
- send-side reset or cancel with code
- byte-stream I/O
- stream ID and metadata observation when exposed

Representative spellings include:

- `Close()` for full local stream close
- `CloseWrite()` for graceful send-half completion
- `CloseRead()` for reader-side stop
- one primary send-reset or send-cancel entry carrying a code
- `Read()` / `Write()` for byte-stream I/O
- `StreamID()` for the numeric wire ID when exposed
- `OpenInfo()` for opener-supplied opaque open-time bytes when exposed
- `Metadata()` for the current advisory metadata snapshot when exposed
- `UpdateMetadata(update)` for post-open advisory metadata changes when exposed

For new bindings, exposing at least this stream-style convenience surface is
RECOMMENDED when those operations fit the binding's ordinary API style. Bindings
SHOULD also expose one primary explicit whole-stream abort entry in this
surface when caller-visible whole-stream abort is supported. Acceptable
shapes include:

- a structured-error entry such as `CloseWithError(err)`
- an explicit code-and-reason entry such as
  `CloseWithErrorCode(code, reason)`

If a binding keeps more than one convenience spelling for compatibility, it
SHOULD document one as the primary entry and the others as wrappers over the
same semantic action rather than as distinct lifecycle operations.

Repository-default stream-style profile intentionally treats `CloseRead()` as
the one convenience read-side stop operation. Choosing a different
`STOP_SENDING` code does not create a second lifecycle action in the way that
the primary send-reset entry does for the local send half; it only changes the
code value attached to the same read-stop action.

### 6.4 Full-control protocol surface

A binding that needs fuller protocol control SHOULD allow caller-selected code
and optional diagnostics on whichever low-level operations correspond to:

- read-side stop -> `STOP_SENDING(code)`
- send-side reset -> `RESET(code)`
- whole-stream abort -> `ABORT(code)`

This surface MAY be exposed through:

- dedicated lower-level control operations
- option-bearing variants of convenience verbs
- structured control objects passed to one primary control entry per
  operation family

Exact spellings for that fuller control surface are intentionally not
standardized by this document. Representative shapes include:

- an explicit read-stop operation carrying `code` and optional diagnostics
- a code-bearing variant of the read-side stop action within the same verb
  family
- an explicit send-reset operation carrying `code` and optional diagnostics
- a primary whole-stream abort entry that accepts either a structured error
  value or explicit `(code, reason)` parameters

This surface is intentionally capability-oriented. It remains conformant
whether a binding chooses protocol-native names, stream-style names with
explicit control parameters, or both, as long as the semantic mapping is
documented clearly.

### 6.5 Repository-default `Close()` helper

Repository-default `Close()` is not a half-close synonym. It is a full local
stream-close helper:

- if the local send half is still open, it commits graceful send completion
  through `CloseWrite()`
- if the local read half is still open, it commits reader-side stop through
  `CloseRead()` using the repository-default `CANCELLED` stop code
- after `Close()` becomes visible locally, further local `Read` and `Write`
  calls should fail promptly
- bindings MAY additionally wait for bounded local drain or peer
  acknowledgement when that blocking contract fits the binding's I/O
  surface, but they MUST document that choice explicitly
- for unidirectional streams, `Close()` should silently ignore any locally
  absent direction rather than surfacing an error solely because that half does
  not exist; it therefore behaves like `CloseWrite()` on a local send-only
  stream and like `CloseRead()` on a local receive-only stream

Repository-default implementations MAY additionally expose a separate blocking
helper such as `Wait(...)` / `WaitClosed(...)` to observe final stream
termination after `Close()` has committed its local shutdown steps.

If a binding chooses to make plain `Close()` blocking, it SHOULD still first
apply the repository-default `CloseWrite()` + `CloseRead()` mapping above and
only then wait for terminal completion. Plain `Close()` SHOULD NOT be
implemented by translating it into abortive `ABORT` semantics.

### 6.6 Semantic conformance and profile coexistence

Bindings satisfy repository-default API semantics by documenting and
implementing the operation families above. Exact API spellings are not
mandated by the claim. A conforming binding SHOULD document whether it
exposes:

- only the stream-style convenience profile
- only a full-control protocol surface
- both

A stream-style convenience profile and a full-control protocol surface MAY
coexist.
When both are exposed:

- `CloseRead()` is the repository-default convenience shorthand for read-side
  stop with code `CANCELLED`
- `CloseWrite()` remains graceful completion
- the primary send-reset entry in the convenience surface is the
  repository-default shorthand for a send-side `RESET(code)` when no extra
  diagnostics are supplied
- the primary explicit whole-stream abort entry in the convenience surface is
  a shorthand for `ABORT(code)` with optional reason text when that surface
  carries one
- richer control entry points MAY additionally expose caller-selected codes or
  diagnostics without changing the underlying lifecycle families

Bindings SHOULD still keep one primary ordinary spelling per operation family
inside each exposed surface, rather than presenting multiple co-equal names
for the same action in that same layer.

### 6.7 Optional native and state-observation surface

Bindings MAY additionally expose non-blocking native or fuller-control state
queries such as:

- whether a stream was opened locally or by the peer
- whether a stream is bidirectional or unidirectional
- whether the local read or write half has already left the open state

When such queries are exposed, repository-default semantics are:

- `ReadClosed()` and `WriteClosed()` report committed local lifecycle state, not
  whether all already-buffered bytes have been drained
- `ReadClosed()` therefore MAY become `true` as soon as local read-stop or peer
  `FIN` / `RESET` / `ABORT` becomes visible in shared stream state, even if one
  later `Read()` can still drain already-buffered pre-terminal bytes according
  to the read rules in Section 3
- `WriteClosed()` likewise becomes `true` when the local send half has left the
  open state, not only after queued bytes have necessarily been transport-
  submitted

## 7. Error mapping

Default minimum error surface:

- protocol/session errors
- stream reset errors with numeric code
- write-after-close errors
- local cancellation errors
- remote application-defined error codes

Repository-default bindings SHOULD expose, preserve, or make queryable a more
structured local error shape when the binding's error model allows it.
Recommended fields are:

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
more idiomatic for that binding.

Repository-default diagnostic-text mapping is:

- on a stream-style convenience surface, the primary explicit whole-stream
  abort entry sends `ABORT(code)`
- fuller control surfaces MAY likewise carry optional DIAG-TLV `debug_text`
  on `STOP_SENDING`, `RESET`, `ABORT`, `GOAWAY`, or `CLOSE` when those
  controls are exposed directly
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
  DIAG-TLV `debug_text` on the relevant terminating control frame

When the session receives `CLOSE` or the underlying transport fails, blocked
`Read`, `Write`, and accept operations should be unblocked promptly and fail
with a session-termination error rather than hang indefinitely.

Core `zmux` error codes occupy `0-255`. Values above that range should be
surfaced unchanged rather than collapsed into generic transport errors.

### 7.1 Session error visibility

When surfacing session-level errors to application callers, implementations
SHOULD translate internal close errors into application-appropriate error
values. Internal implementation details such as specific worker shutdown
sequences, transport buffer states, or internal lock contention SHOULD NOT
leak through the public error surface.

Repository-default session error translation:

- `NO_ERROR` or nil close cause: return nil or a language-appropriate success
  indication
- `ApplicationError` with a core or non-core code: surface the structured
  error with code and optional reason text preserved
- transport-level EOF or connection reset: surface as a session-closed or
  transport-failure error
- fatal protocol errors detected while parsing or validating inbound frames on
  an established reader loop: surface as remote read-side session termination
  errors, even if the lower-level codec helper itself is context-neutral
- internal implementation errors: surface as `INTERNAL` errors without
  exposing implementation detail

## 8. Open semantics

Default caller expectations:

- opening a stream does not wait for peer acknowledgement
- the first local `Write` on a new stream may race with a later peer refusal
- peer refusal should surface as a later stream error via `ABORT`
- repository-default bindings expose `OpenStream(...)` or an equivalent
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
- repository-default bindings SHOULD therefore prefer opaque provisional
  handles over exposing numeric wire IDs before the stream is active, because
  early numeric exposure constrains same-class open serialization and
  complicates cancellation behavior

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
- if `OPEN_METADATA` bytes alone consume the entire opening-frame payload
  budget, repository-default bindings MUST keep the opener within that payload
  limit and emit any trailing application bytes in later `DATA` frames
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

When a binding exposes open-time or advisory metadata methods, repository-
default names are:

- `OpenInfo()` for the opener's opaque open-time bytes when known locally
- `Metadata()` for the currently known advisory metadata snapshot
- `UpdateMetadata(update)` for post-open advisory metadata updates; in
  `zmux v1`, only standardized advisory fields such as priority and group have
  update semantics on the wire

Repository-default `UpdateMetadata(update)` behavior is:

- before `opening-frame-committed`, supported advisory fields SHOULD merge into
  the pending opening metadata state when the first opening `DATA` can still
  carry them
- once the opener carrying those merged fields has entered the local
  transport-submission path, the local metadata snapshot SHOULD remain
  consistent with that potentially in-flight opener rather than rolling back to
  stale local-only values after a later session close or transport failure
- after `opening-frame-committed`, supported advisory fields SHOULD use the
  standardized post-open carriage path still available for that stream, such as
  `PRIORITY_UPDATE`
- after local validation succeeds, post-open advisory updates remain
  best-effort until handed to the writer; if retaining the queued advisory frame
  would exceed the session memory hard cap, repository-default bindings SHOULD
  drop that pending advisory update and release its retained state rather than
  mutating only local shadow state or exceeding the cap
- `open_info` remains open-time metadata only in `zmux v1`; it is not part of
  post-open update semantics
- if the binding cannot carry requested peer-visible metadata on any
  standardized path still available, `UpdateMetadata(update)` SHOULD fail
  explicitly rather than silently mutating only local shadow state

Bindings MAY also expose convenience surfaces that collapse common first-batch
patterns into one call, for example:

- `WriteFinal(...)` / `WritevFinal(...)` for one-shot `DATA|FIN`
- `OpenAndSend(...)` / `OpenAndSendWithOptions(...)` for bidirectional open
  plus immediate first payload submission
- `OpenUniAndSend(...)` / `OpenUniAndSendWithOptions(...)` for unidirectional
  open plus immediate first payload submission using `WriteFinal(...)`
  semantics for that first payload

Ordinary `OpenStream()` / `OpenUniStream()` usage remains metadata-free by
default. Carrying open-time metadata is an opt-in sender choice.

### 8.1 Repository-default session surface

When a binding exposes a repository-default session surface, it SHOULD cover
these capability families:

- accepting bidirectional and unidirectional peer-opened streams
- opening bidirectional and unidirectional local streams
- open-time options when open metadata or initial advisory hints are supported
- ordinary graceful session shutdown
- terminal session close carrying an error or application-defined close cause
- waiting for final session termination
- non-blocking local session inspection when exposed
- optional one-shot open-and-send conveniences when they fit the binding's API
  surface

Repository-default session design likewise distinguishes:

- a stream-style session convenience profile for ordinary open, accept, close,
  and wait usage
- an optional native or fuller session-control surface when the binding wants
  more direct control over graceful drain, terminal diagnostic-bearing
  shutdown, or richer local observation

If a binding exposes an ordinary session surface, it SHOULD provide one
primary spelling for:

- accepting bidirectional and unidirectional peer-opened streams
- opening bidirectional and unidirectional local streams
- open-time options when supported
- optional one-shot open-and-send helpers when they fit the binding's API
  surface
- graceful session shutdown
- terminal session close carrying an error or application-defined close cause
- waiting for final session termination
- non-blocking local session inspection when exposed

Representative spellings include:

- `AcceptStream(...)` / `AcceptUniStream(...)`
- `OpenStream(...)` / `OpenUniStream(...)`
- `OpenStreamWithOptions(...)` / `OpenUniStreamWithOptions(...)`
- `OpenAndSend(...)` / `OpenAndSendWithOptions(...)`
- `OpenUniAndSend(...)` / `OpenUniAndSendWithOptions(...)`
- `Close()` for ordinary session shutdown
- one primary terminal session error-close helper
- `Wait(...)` / `AwaitTermination(...)` to observe final session termination
- `Closed()` / `IsClosed()`, `State()`, and `Stats()` for non-blocking local
  session inspection when exposed

Exact session API spellings are not part of the API-semantics claim. Bindings
SHOULD still keep one primary spelling per capability family inside each API
layer rather than standardizing multiple verb families for the same action.

A native or fuller session-control surface MAY additionally expose direct
controls or observation corresponding to:

- bounded graceful drain initiation
- terminal session close with caller-selected code and optional diagnostics
- local observation of close cause
- protocol ping round-trip measurement
- direct `GOAWAY` initiation with caller-selected watermarks and optional
  diagnostics
- peer `GOAWAY` / `CLOSE` cause queries
- local and peer preface observation
- negotiated-parameter observation

Exact spellings for those fuller session controls are intentionally not
standardized by this document.

Repository-default session-lifecycle behavior is:

- `Close()` is the ordinary graceful shutdown helper: it SHOULD stop admitting
  new local opens, perform the bounded `GOAWAY`-based drain sequence when that
  path is in use, and then commit terminal session close
- repository-default graceful drain SHOULD NOT remain blocked solely because a
  stream still retains unread inbound bytes after the local side has no
  remaining send-side work; in particular, peer-opened streams with no
  outstanding local send work, and fully terminal streams retained only for
  unread buffered data, need not delay `Close()`
- the primary terminal session error-close helper is stronger than `Close()`
  and SHOULD commit terminal session shutdown without waiting for graceful
  drain
- once terminal session state becomes visible locally, blocked accept, open,
  read, and write operations SHOULD be woken promptly against that committed
  state; final `Wait(...)` / `Closed()` completion may follow after close-path
  cleanup finishes
- `Wait(...)` observes final session termination, not merely shutdown
  initiation; bindings SHOULD provide a way to observe the non-graceful
  terminal cause, either as the wait result or through a documented
  accessor/helper for the terminal cause
- once a terminal session cause has been committed, later transport close
  noise, writer shutdown failures, interrupt cleanup, or repeated local close
  attempts MUST NOT replace that cause; a graceful terminal close likewise
  MUST NOT acquire a non-graceful cause from late cleanup noise
- `Closed()` becomes true only after final terminal completion
- `State()` and `Stats()` are local observation helpers; they MUST NOT be
  treated as completion acknowledgements

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
- `AcceptStream(...)` / `AcceptUniStream(...)`, if exposed, should unblock on
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
contract above. Adapters MAY additionally expose a fuller control layer when
they want direct caller access to lower-level `zmux` controls.

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
- whole-stream abort
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

### 10.4 Convenience mapping and fuller control layer

The following names are one recommended stream-style mapping, not the only
conformant API surface:

- `OpenStream` -> local bidirectional stream open
- `OpenUniStream` -> local unidirectional stream open
- `OpenStreamWithOptions` -> local bidirectional stream open with open-time
  metadata or initial advisory options
- `OpenUniStreamWithOptions` -> local unidirectional stream open with
  open-time metadata or initial advisory options
- `AcceptStream` -> accept next application-visible peer-opened bidirectional
  stream
- `AcceptUniStream` -> accept next application-visible peer-opened
  unidirectional stream
- `OpenAndSend` / `OpenAndSendWithOptions` -> bidirectional open plus
  immediate first payload submission
- `OpenUniAndSend` / `OpenUniAndSendWithOptions` -> unidirectional open plus
  immediate first payload submission with `WriteFinal(...)` semantics for that
  first payload
- `StreamID()` -> locally known numeric stream ID when exposed
- `OpenInfo()` -> opener-supplied opaque open-time bytes when known locally
- `Metadata()` -> current advisory metadata snapshot when exposed
- `UpdateMetadata(update)` -> post-open advisory metadata update request
- `Read` -> `Read`
- `Write` -> `Write`
- `WriteFinal(...)` / `WritevFinal(...)` -> one-shot `DATA|FIN`
- `Close()` -> repository-default full-stream close helper
- `CloseWrite()` -> `DATA|FIN`
- `CloseRead()` -> `STOP_SENDING(CANCELLED)` as the repository-default
  receiver-side close control for one stream direction
- one primary send-reset or send-cancel entry -> `RESET(code)`
- one primary explicit whole-stream abort helper, if exposed -> `ABORT(code)`
  with optional DIAG-TLV `debug_text`
- lower-level read-stop control, if exposed -> `STOP_SENDING(code)` with
  optional diagnostics
- lower-level send-reset control, if exposed -> `RESET(code)` with optional
  diagnostics
- lower-level whole-stream abort control, if exposed -> `ABORT(code)` with
  optional diagnostics
- a code-bearing convenience variant for read-side stop MAY be used when the
  adapter chooses to fold fuller read-stop control into the same verb family

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
- adapter surfaces SHOULD expose one primary send-reset or send-cancel entry
  for send-side abortive cancellation
- adapter surfaces SHOULD expose one primary explicit whole-stream abort
  helper that can carry code and optional reason text
- adapter surfaces MAY additionally expose a fuller control layer for caller-
  selected codes and diagnostics on `STOP_SENDING`, `RESET`, and `ABORT`
- adapter surfaces SHOULD keep one primary spelling per operation family
- application-visible incoming streams should follow the same rules described
  in sections 2 and 8 of this document
- stream adapters should hide control-opened-only streams from the ordinary
  accept queue unless they intentionally expose lower-level internals
- if an adapter carries open-time metadata through an adapter-local opener
  prelude or equivalent sideband, fresh read-side stop should submit that
  opener before sending read-side cancellation so the peer can still make the
  stream application-visible
- fresh write-side reset / abort opener visibility is not portable for
  transports where reset can discard earlier unacknowledged stream data; such
  adapters may document it as best-effort rather than a conformance guarantee
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
- do not collapse them into one generic reset error unless the adapter surface
  cannot preserve distinct numeric codes

### 10.6 Deadline and cancellation mapping

For stream adapters:

- cancellable open operations should map to `OpenStream(...)`-style local
  cancellation
- a provisional local open cancelled before first-frame commit should not
  consume a peer-observable `stream_id`
- bindings SHOULD delay numeric `StreamID()` exposure until first-frame
  commit; earlier numeric ID exposure is outside the repository-default
  profile
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

Repository-default usage guidance should steer applications toward directional
operations instead of whole-stream aborts by default. The examples below use
the stream-style convenience surface, but bindings with a fuller control layer
may additionally let callers choose explicit codes and diagnostics when that is
useful.

Recommended operation choices:

- request body finished, response still expected -> `CloseWrite()`
- local side no longer wants to read, but the opposite direction may still
  matter -> `CloseRead()`
- local send half failed, but the opposite direction may still be useful ->
  the primary send-reset or send-cancel entry
- the local application is done with the stream as a whole -> `Close()`
- the whole stream is no longer meaningful and the peer should receive an
  application-defined terminal error -> the primary explicit whole-stream
  abort entry

Bindings SHOULD document that:

- successful `Write` means bytes entered the local send path, not that the
  peer application accepted them
- `Close()` ends ordinary local use of the stream as a whole under the
  repository-default full-close helper semantics
- `CloseWrite()` finishes only the local send half
- `CloseRead()` stops local interest in further inbound bytes for that
  direction and emits `STOP_SENDING(CANCELLED)` by default
- the primary send-reset or send-cancel entry aborts only the local send half
- the primary explicit whole-stream abort entry is stronger than `Close()` and
  should surface numeric code plus optional reason text when it is exposed
