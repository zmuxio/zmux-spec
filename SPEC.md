# zmux Protocol Specification

This document is the normative core protocol definition for **`zmux v1`**.

Use it together with [ARCHITECTURE.md](./ARCHITECTURE.md),
[REGISTRY.md](./REGISTRY.md), and [CONFORMANCE.md](./CONFORMANCE.md). In this
repository, public `zmux-full-v1` compatibility means this core specification plus
the currently standardized same-version surfaces and their negotiated handling.

## 1. Scope and transport model

`zmux` is a stream multiplexer for reliable, ordered, full-duplex byte
streams.

The underlying transport is expected to provide:

- reliable delivery
- in-order delivery
- bidirectional byte-stream I/O

The underlying transport is not tied to any one transport family. Any bearer
that provides these properties is suitable, including TCP connections, TLS
streams, Unix domain sockets, or in-process pipes.

`zmux` standardizes one session over one reliable, ordered, full-duplex byte
stream. It does not define multi-connection session composition inside the
protocol.

`zmux` does not preserve application write boundaries. Each logical stream is a
byte stream, not a message stream. The protocol makes no provision for
out-of-order delivery, packet-loss recovery, or message framing beyond its own
frame model. Those properties are delegated entirely to the underlying
transport.

### 1.1 Stream abstraction

Each bidirectional `zmux` stream is a full-duplex byte-stream connection
abstraction with independent inbound and outbound halves.

At the protocol-semantics level, a conforming stream abstraction supports:

- reading inbound bytes in order
- writing outbound bytes in order
- graceful local write-half close
- local read-side stop without requiring full stream abort
- independent completion or termination of both halves
- full stream abort

In `zmux v1`, those actions map to wire behavior as follows:

- write application data: `DATA`
- graceful write-half close: `DATA|FIN`
- local read-side stop: `STOP_SENDING`
- local send-half abort: `RESET`
- full stream abort: `ABORT`

For a bidirectional stream, the two halves are independent:

- receiving peer `FIN` ends the inbound byte stream after buffered data is read
- sending local `FIN` ends only the local outbound byte stream
- `STOP_SENDING` is the dedicated receiver-side close control for one inbound
  direction; it requests that the peer end its outbound byte stream toward the
  sender
- `RESET` aborts only the sender's own outbound byte stream
- `ABORT` aborts both directions immediately

There is no dedicated graceful full-close frame. A local endpoint may finish
its send half with `DATA|FIN`, may stop its read half with `STOP_SENDING`, and
may continue processing until the peer direction reaches graceful or abortive
termination. `STOP_SENDING` is a reader-side stop or cancel control; it is not
a synonym for peer-graceful EOF.

For a unidirectional stream, one half is absent by construction:

- the opener may write
- the peer may read
- the peer MUST NOT send `DATA` on that stream
- the peer may still close that receive half early by sending `STOP_SENDING`

Core `zmux` does not define:

- authentication
- encryption
- non-multiplexing higher-layer semantics
- datagram semantics
- transport-specific recovery behavior

Those belong in other protocol layers or in extension or higher-layer
documents.

## 2. Session establishment

Immediately after the underlying transport is established, both peers MUST send
their session preface.

The two peers MAY send the preface in parallel. Implementations MUST NOT block
sending the local preface while waiting to receive the peer preface first.
Repository-default implementations SHOULD write the local preface concurrently
with reading the peer preface so that both directions make forward progress
without deadlocking on a full transport buffer.

### 2.1 Preface wire format

```text
4 bytes    magic         = "ZMUX"
1 byte     preface_ver
1 byte     role
varint62   tie_breaker_nonce
varint62   min_proto
varint62   max_proto
varint62   capabilities
varint62   settings_len
bytes      settings_tlv
```

### 2.2 Preface field meanings

- `magic`: fixed ASCII bytes `ZMUX`
- `preface_ver`: session preface encoding version
- `role`: stream-ID allocation role mode
- `tie_breaker_nonce`: tie-breaker used only for `role = auto`
- `min_proto`: minimum supported protocol version
- `max_proto`: maximum supported protocol version
- `capabilities`: bitmask of optional supported behaviors
- `settings_len`: byte length of `settings_tlv`
- `settings_tlv`: TLV-encoded initial session parameters

### 2.3 Role values

- `0`: initiator
- `1`: responder
- `2`: auto

The `role` field is only used to assign stream-ID ownership role. It does not
define business-level client/server semantics.

When `role = initiator` or `role = responder`, `tie_breaker_nonce` MUST be
`0` and is ignored by the receiver.

If an endpoint using an explicit `initiator` or `responder` role sends a
non-zero `tie_breaker_nonce` anyway, the receiver SHOULD ignore that nonce and
MUST NOT fail the session solely for that reason.

When `role = auto`, `tie_breaker_nonce` MUST be a non-zero random value within
the `varint62` range.

Repository-default implementations SHOULD generate that nonce from a strong
random source and SHOULD NOT reuse a nonce from a failed establishment
attempt.

In transports that have a natural connection initiator and acceptor, endpoints
SHOULD still use explicit `initiator` / `responder` roles when they want the
most predictable early-send behavior.

`role = auto` is a core establishment mode in `zmux v1`. Repository-default
libraries support it even if some higher-level deployment helpers still
default to explicit `initiator` / `responder` roles on obviously asymmetric
client/server paths.

Repository-default user-facing APIs SHOULD usually hide this choice from
ordinary callers:

- when the underlying transport or API surface already has a natural dialer
  and acceptor, the implementation SHOULD map those sides directly onto
  explicit `initiator` / `responder` roles
- when the API starts a session on top of an already established generic
  duplex byte stream without a natural dialer/acceptor distinction, the
  repository-default behavior SHOULD be to use `role = auto` unless the caller
  explicitly overrides that choice
- callers SHOULD need to choose a role explicitly only on advanced raw-stream,
  symmetric-peer, or manually orchestrated establishment paths
- `role = auto` is therefore a core wire capability, but not something
  ordinary client/server users should need to reason about routinely

### 2.4 Negotiation rules

After both prefaces are received, the negotiated protocol state is:

- `negotiated_proto = min(local.max_proto, peer.max_proto)`
- `negotiated_capabilities = local.capabilities & peer.capabilities`

The session MUST fail if:

- `magic` is invalid
- `preface_ver` is unsupported
- either peer advertises an invalid `role` value
- either peer advertises `role = auto` with `tie_breaker_nonce = 0`
- `negotiated_proto < max(local.min_proto, peer.min_proto)`
- both peers use `role = auto` with the same `tie_breaker_nonce`
- either peer advertises `max_frame_payload < 16384`
- either peer advertises `max_control_payload_bytes < 4096`
- either peer advertises `max_extension_payload_bytes < 4096`

Role resolution rules:

- if one peer advertises `initiator` and the other `responder`, those explicit
  roles are used
- if one peer advertises an explicit role and the other advertises `auto`, the
  explicit role wins and the `auto` peer takes the opposite role
- if both peers advertise the same explicit role, the session fails with
  `ROLE_CONFLICT`
- if both peers advertise `auto`, the peer with the larger
  `tie_breaker_nonce` becomes `initiator` and the smaller becomes `responder`

Role-resolution outcome summary:

| Local role | Peer role | Outcome |
| --- | --- | --- |
| `initiator` | `responder` | use explicit roles as advertised |
| `responder` | `initiator` | use explicit roles as advertised |
| `initiator` | `auto` | local stays `initiator`; peer becomes `responder` |
| `responder` | `auto` | local stays `responder`; peer becomes `initiator` |
| `auto` | `initiator` | peer stays `initiator`; local becomes `responder` |
| `auto` | `responder` | peer stays `responder`; local becomes `initiator` |
| `initiator` | `initiator` | fail with `ROLE_CONFLICT` |
| `responder` | `responder` | fail with `ROLE_CONFLICT` |
| `auto` | `auto` with distinct nonces | larger nonce becomes `initiator`; smaller becomes `responder` |
| `auto` | `auto` with equal nonces | fail with `ROLE_CONFLICT` |

Endpoints MUST NOT silently fall back to `auto` when both peers explicitly
request the same role. That case is a configuration conflict, not an automatic
role-resolution path.

The receiver MUST fully parse the peer preface before interpreting subsequent
bytes as regular `zmux` frames.

An endpoint using `role = auto` MUST NOT create any new outbound streams until
role resolution is complete, because its stream-ID ownership is not yet known.

If both peers advertise `role = auto` and the nonces are equal, the current
session-establishment attempt fails with `ROLE_CONFLICT`.

Higher-layer connection orchestration MAY retry with a fresh random nonce after
such a collision.

Repository-default collision handling is:

1. fail the current establishment attempt with `ROLE_CONFLICT`
2. do not send ordinary stream traffic on that failed attempt
3. release the failed session attempt completely
4. if retry is desired, start a fresh session-establishment attempt with a new
   non-zero random nonce
5. apply a short randomized backoff when repeated symmetric collisions are
   possible

If a deployment already has an external deterministic ordering signal, such as
an agreed dialer/acceptor relationship or another authenticated higher-layer
endpoint ordering, implementations SHOULD prefer mapping that signal to
explicit roles instead of relying on nonce comparison.

`zmux v1` does not define an in-band retry exchange for this case. Retry is a
new session-establishment attempt on a fresh underlying byte stream or fresh
transport connection. The failed byte stream is already structurally unusable
for ordinary `zmux` traffic once establishment has failed. Such retry policy
belongs to higher-layer connection orchestration rather than the core mux
contract.

### 2.5 Receive-limit model

The preface settings are unilateral receive-side parameters.

Each endpoint advertises the limits and hints it wants the peer to obey for
traffic flowing **toward the advertising endpoint**.

Examples:

- local `initial_max_stream_data_bidi_locally_opened` is the initial receive
  window toward the local endpoint for bidirectional streams opened by the
  local endpoint
- local `initial_max_stream_data_bidi_peer_opened` is the initial receive
  window toward the local endpoint for bidirectional streams opened by the
  peer
- local `initial_max_stream_data_uni` is the initial receive window toward the
  local endpoint for peer-initiated unidirectional streams
- local `initial_max_data` is the initial session-wide receive window toward
  the local endpoint
- local `max_frame_payload` is the largest payload the local endpoint is
  willing to receive in a single frame
- local `max_control_payload_bytes` is the largest control-plane payload the
  local endpoint is willing to accept in one `PING`, `PONG`, `BLOCKED`,
  `RESET`, `STOP_SENDING`, `GOAWAY`, `CLOSE`, or `ABORT`
- local `max_incoming_streams_bidi` is the number of peer-initiated
  bidirectional streams that may be active concurrently toward the local
  endpoint
- local `max_incoming_streams_uni` is the number of peer-initiated
  unidirectional streams that may be active concurrently toward the local
  endpoint

No inbound receive window exists for a locally initiated unidirectional stream,
because the peer cannot send `DATA` on that stream.

There is no merged value for these settings. Each direction uses the values
advertised by the receiver for that direction.

For `max_incoming_streams_bidi` and `max_incoming_streams_uni`, an incoming
stream counts as active once the receiver has accepted a valid next-expected
opening frame for it. It remains active until it reaches a fully terminal
local state. For bidirectional streams, a terminal receive half alone does not
release the slot while the local send half still remains live. A stream that
has been fully aborted may release its active slot as soon as local abort
processing completes. If a stream becomes fully terminal within the same
processing step, it does not continue occupying an active slot after that step
completes.

### 2.6 Preface size bounds

`settings_len` in the session preface MUST NOT exceed `4096` bytes in
`zmux v1`.

A larger value is a negotiation error and the session MUST fail.

### 2.7 Early post-preface traffic

Once the preface is sent, a peer MAY start sending regular `zmux` frames
without waiting for an explicit handshake acknowledgement.

No additional handshake ACK frame is required by `zmux v1`.

`zmux v1` still requires one sender-local readiness condition before ordinary
stream traffic begins: an endpoint MUST finish parsing the peer preface before
it creates new local streams or sends application `DATA`.

For endpoints using `role = auto`, that readiness condition also includes role
resolution.

After peer preface parsing completes, and after role resolution completes when
needed, the endpoint MAY immediately create streams and send regular frames.
No extra mux round trip and no per-stream open acknowledgement are required.

Repository-default implementations therefore treat session establishment as:

- send local preface immediately
- parse peer preface immediately
- transition to session-ready as soon as peer preface parsing succeeds
- only then allow new stream creation and application `DATA`

Repository-default session-ready summary:

| Local establishment condition | Required before ordinary stream traffic? |
| --- | --- |
| local preface sent | yes |
| peer preface fully parsed | yes |
| role resolved when either side uses `role = auto` | yes |
| negotiated protocol version and capabilities accepted | yes |
| ordinary stream traffic (`DATA`, `DATA|FIN`, stream-scoped control, `EXT`) | allowed only after every required readiness condition above is satisfied |

Repository-default establishing-state outbound policy is:

| Outbound item before `session-ready` | Repository-default policy |
| --- | --- |
| local session preface | allowed |
| fatal session `CLOSE` during establishment failure | allowed |
| new-stream `DATA` / `DATA|FIN` | not allowed |
| stream-scoped control (`STOP_SENDING`, `RESET`, `ABORT`, stream `MAX_DATA`, stream `BLOCKED`) | not allowed |
| ordinary session-scoped control (`PING`, `PONG`, session `MAX_DATA`, session `BLOCKED`, `GOAWAY`) | not allowed |
| `EXT` | not allowed |

This keeps the core protocol free of preface-race resource ambiguity while
preserving ackless stream opening once the session is ready.

## 3. Numeric rules

### 3.1 Stream IDs

- `stream_id = 0` is reserved for session-scoped frames
- for `stream_id > 0`, the low two bits define stream kind:
  - `00`: initiator-opened bidirectional stream
  - `01`: responder-opened bidirectional stream
  - `10`: initiator-opened unidirectional stream
  - `11`: responder-opened unidirectional stream

Each endpoint therefore maintains two local counters:

- initiator:
  - next bidirectional stream ID starts at `4`
  - next unidirectional stream ID starts at `2`
- responder:
  - next bidirectional stream ID starts at `1`
  - next unidirectional stream ID starts at `3`

Each newly created outbound stream uses the current value from the matching
counter and then increments that counter by `4`.

Stream IDs MUST increase monotonically and MUST NOT be reused within a session.

Stream IDs MUST NOT wrap. Implementations MUST NOT scan for lower unused IDs
and MUST NOT recycle IDs from closed streams within the same session.

Receiving a new stream ID whose low two bits identify the wrong opener for the
peer that created it is a protocol error.

For each peer-owned stream class, receivers also maintain the next expected new
stream ID:

- one next expected peer bidirectional ID
- one next expected peer unidirectional ID

When the first opening-eligible frame for a previously unused peer-owned stream
is observed, that stream ID MUST equal the next expected ID for its class.
Once that first use is accepted as a syntactically and directionally valid use
of the next expected ID for its class, the receiver MUST:

- record that stream ID as used
- advance the corresponding expected-ID cursor by `4`

This cursor advance still applies even if the stream is then immediately
refused, aborted, or otherwise reaches a terminal state in the same processing
step.

A peer-owned new stream ID that skips over lower still-unused IDs of the same
class is a session `PROTOCOL` error.

The pair `(session, stream_id)` uniquely identifies one logical stream.

For local sender-side APIs, a newly requested outbound stream may exist
provisionally before its first opening-eligible frame is committed to wire
order.

A local stream ID is considered **consumed** only when the first
opening-eligible frame for that stream has been committed into that stream
class's sender-side serialization order.

Before that commit point, local cancellation MAY discard the provisional open
without consuming the stream ID, provided that no later local stream ID of the
same class has already been committed ahead of it.

If an earlier provisional stream is cancelled after a later stream ID of the
same class has already been committed, the implementation MUST still consume
the cancelled earlier ID on the wire so that no peer-observable gap is
created. Repository-default behavior is to emit `ABORT(CANCELLED)` for that
cancelled stream ID.

Implementations MUST NOT create peer-observable gaps in local stream-ID usage,
and they MUST NOT recycle or silently reuse a previously committed stream ID.

If a local API exposes a numeric `stream_id` before that commit point, the
implementation MUST either treat that ID as already reserved and eventually
surface it on the wire, or avoid exposing numeric IDs until commit.

If the local side cannot allocate another outbound stream ID within the allowed
range, it MUST stop creating new streams on that session. It SHOULD begin
graceful session replacement with `GOAWAY` rather than attempting wraparound or
ID reuse.

### 3.2 Integer encoding

Except for `magic`, `preface_ver`, `role`, and the 1-byte `code` field in frame
headers, all integers use length-prefixed 62-bit variable integers
(`varint62`).

All core protocol integers defined by `zmux v1` MUST be representable within
`0 .. 2^62 - 1`.

Encoding lengths are selected by the high two bits of the first byte:

- `00`: 1 byte total, 6 bits of value
- `01`: 2 bytes total, 14 bits of value
- `10`: 4 bytes total, 30 bits of value
- `11`: 8 bytes total, 62 bits of value

The remaining bits are decoded as a big-endian (network byte order) unsigned
integer.

All integer encodings in `zmux v1` MUST use the shortest valid representation.
Non-canonical encodings are protocol errors.

Receivers MUST bound varint parsing accordingly. Any integer field that:

- uses any length other than `1`, `2`, `4`, or `8` bytes
- decodes to a value larger than `2^62 - 1`
- ends prematurely before the announced length is complete

is a session `PROTOCOL` error.

## 4. Frame model

### 4.1 Unified frame header

All frames use the same header layout:

```text
varint62   frame_length
1 byte     code
varint62   stream_id
bytes      payload
```

`frame_length` is the total number of bytes that follow it in the frame:

- the 1-byte `code`
- the encoded `stream_id`
- the payload bytes

`frame_length` therefore does not include the bytes used to encode
`frame_length` itself.

### 4.2 Code layout

The `code` byte is split as:

```text
bits 0-4   frame_type
bits 5-7   frame_flags
```

### 4.3 Frame size rules

- `frame_length` MUST be at least `2`, because every frame must contain the
  1-byte `code` field and at least the 1-byte shortest canonical encoding of
  `stream_id`
- after decoding `stream_id`, the parser MUST verify
  `frame_length >= 1 + encoded_length(stream_id)` before deriving any payload
  length by subtraction
- after decoding `code` and `stream_id`, the payload length is derived as:
  `frame_length - 1 - encoded_length(stream_id)`
- `frame_length` MUST be large enough to contain the encoded `code` and
  `stream_id`
- inbound `DATA` payload length MUST NOT exceed the receiver's configured
  `max_frame_payload`
- inbound `MAX_DATA`, `PING`, `PONG`, `BLOCKED`, `RESET`, `STOP_SENDING`,
  `GOAWAY`, `CLOSE`, and `ABORT`
  payload lengths MUST NOT exceed the receiver's configured
  `max_control_payload_bytes`
- inbound `EXT` payload length MUST NOT exceed the receiver's
  `max_extension_payload_bytes`

For core frames with a strictly defined payload layout and no variable-length
tail, excess payload bytes beyond the defined fields are invalid. In
particular:

- `MAX_DATA` payloads MUST contain exactly one canonical `varint62 max_offset`
- `BLOCKED` payloads MUST contain exactly one canonical `varint62 blocked_at`

Trailing garbage in those fixed-layout payloads is a session `PROTOCOL` error.

Conversely, a payload that is too short to contain a frame's mandatory defined
fields is invalid. Unless a frame-specific rule states otherwise, repository-
default handling for such structurally truncated payloads is a session
`FRAME_SIZE` error.

A violation of these rules is a session error and SHOULD be signaled with
`CLOSE(FRAME_SIZE)` before the underlying transport is closed.

### 4.4 Flags and valid combinations

The three flag bits are interpreted by frame type, not as one global flag set.

`zmux v1` defines these meanings:

- for `DATA`:
  - bit `0x20` = `OPEN_METADATA`
  - bit `0x40` = `FIN`
  - bit `0x80` is reserved
- for all other core frame types:
  - all flag bits MUST be zero

Recommended valid combinations are therefore:

- `DATA|OPEN_METADATA`
- `DATA|FIN`
- `DATA|OPEN_METADATA|FIN`

Receiving any non-zero flag combination not explicitly allowed for the
corresponding frame type is a session `PROTOCOL` error.

### 4.5 Core invariants

The following rules apply throughout the protocol:

- only `DATA` payload bytes consume stream and session receive windows
- control frames do not preserve application message boundaries
- senders MUST obey the peer's advertised receive limits
- receivers MAY aggregate control work and emit fewer control frames
- senders MAY fragment large application writes into multiple `DATA` frames
- receivers MUST present a continuous byte stream to the application
- implementations SHOULD avoid indefinitely starving `MAX_DATA`, `BLOCKED`,
  `STOP_SENDING`, `RESET`, `ABORT`, `GOAWAY`, or `CLOSE` behind bulk `DATA`
- within one stream, once `DATA` bytes have entered that stream's local
  serialization order, later `DATA|FIN`, `RESET`, or `ABORT` for that same
  stream MUST NOT be emitted ahead of those already committed bytes
- implementations MUST ensure that each frame is written completely to the
  underlying transport; partial frame writes that leave the transport in an
  inconsistent state are not recoverable within `zmux v1`

## 5. Settings and defaults

If a setting is omitted from the preface, the default value from
[REGISTRY.md](./REGISTRY.md) applies.

Unless stated otherwise, all standard setting values are encoded as a single
`varint62` in the setting TLV value field.

### 5.1 General setting rules

Senders MUST NOT repeat the same setting ID within one settings block.

Receivers MUST treat duplicate setting IDs as a session `PROTOCOL` error.

Unknown setting IDs in the preface MUST be ignored unless a stricter
negotiated document defines otherwise.

### 5.2 Initial receive-window grants

The following settings define the starting send allowance toward the receiver:

- `initial_max_stream_data_bidi_locally_opened`
- `initial_max_stream_data_bidi_peer_opened`
- `initial_max_stream_data_uni`
- `initial_max_data`

These are initial receive-window grants, not fixed protocol maxima. Receivers
MAY later advance those limits with `MAX_DATA` frames.

### 5.3 Hard receive constraints

The following settings are receive-side hard interoperability limits:

- `max_incoming_streams_bidi`
- `max_incoming_streams_uni`
- `max_frame_payload`
- `max_control_payload_bytes`
- `max_extension_payload_bytes`

In `zmux v1`, the following minimum compatibility constraints also apply:

- `max_frame_payload` MUST be at least `16384`
- `max_control_payload_bytes` MUST be at least `4096`
- `max_extension_payload_bytes` MUST be at least `4096`

These minima do not force implementations to keep large runtime buffers for
all control paths. They only define the smallest interoperable receive limits
for the corresponding negotiated core or extension behaviors.

### 5.4 Advisory hints

The following settings are hints only:

- `idle_timeout_millis`
- `keepalive_hint_millis`
- `scheduler_hints`

Advisory hints MAY be ignored.

In particular, `keepalive_hint_millis` is only a hint. Implementations MAY
ignore it when the underlying transport already provides satisfactory liveness,
close detection, or native keepalive behavior, or when local policy prefers to
avoid protocol-originated keepalive traffic.

`scheduler_hints` defines a session-wide baseline scheduling intent. Standard
values are listed in [REGISTRY.md](./REGISTRY.md). Unknown hint values MAY be
ignored.

The standard intent of the recognized values is:

- `unspecified_or_balanced`: implementation default general-purpose policy
- `latency`: prefer lower queuing delay and shorter batching windows
- `balanced_fair`: prefer even treatment across active streams
- `bulk_throughput`: permit more batching and throughput-oriented scheduling
- `group_fair`: increase the influence of `stream_group` when group hints are
  available

### 5.5 Parameter immutability

`zmux v1` defines no post-preface mechanism that changes core session limits.

The values negotiated through the session preface therefore remain fixed for
the lifetime of that logical session.

## 6. Core frame semantics

### 6.1 DATA

`DATA` carries application payload for a stream.

Requirements:

- `stream_id != 0`
- if `OPEN_METADATA` is not present, `payload` is uninterpreted application
  data
- if `OPEN_METADATA` is present, payload format is:

```text
varint62 metadata_len
bytes     metadata_tlvs    // exactly metadata_len bytes of STREAM-METADATA-TLVs
bytes     application_data
```

Allowed flags:

- `OPEN_METADATA`
- `FIN`

Semantics:

- first `DATA` on a previously unseen valid stream ID implicitly creates that
  stream
- first `DATA|OPEN_METADATA` on a previously unseen valid stream ID opens the
  stream, applies any valid initial open-time metadata carried in the
  flag-gated metadata block, and then delivers the remaining payload bytes as
  application data
- `DATA|FIN`: closes the sender's write direction after the payload
- first `DATA|FIN` on a previously unseen valid stream ID opens the stream,
  sends payload, and half-closes the local write direction in one frame
- first `DATA|OPEN_METADATA|FIN` on a previously unseen valid stream ID opens
  the stream, applies any valid initial open-time metadata, sends the
  remaining payload bytes, and half-closes the local write direction in one
  frame

Additional rules:

- zero-length `DATA` is valid and may open a stream without payload
- zero-length `DATA|FIN` is valid and half-closes the sender write side
- zero-length `DATA|OPEN_METADATA` is valid and may open a stream with
  initial open-time metadata but no application bytes
- zero-length `DATA|OPEN_METADATA|FIN` is valid and may open and half-close a
  stream while carrying only initial open-time metadata
- excessive zero-length `DATA` that does not materially advance stream or
  session state MAY be treated as abusive traffic; see Sections 11 and 13
- `OPEN_METADATA` is valid only on the first `DATA` / `DATA|FIN` that opens a
  stream
- `OPEN_METADATA` MUST NOT appear on later `DATA` frames for an already
  opened stream
- the `OPEN_METADATA` flag MUST be used only when the `open_metadata`
  capability was negotiated
- `metadata_tlvs` uses the STREAM-METADATA-TLV namespace
- unknown metadata TLVs in `OPEN_METADATA` MUST be skipped
- senders MUST NOT include `stream_priority` inside `OPEN_METADATA` unless
  both `open_metadata` and `priority_hints` were negotiated
- senders MUST NOT include `stream_group` inside `OPEN_METADATA` unless both
  `open_metadata` and `stream_groups` were negotiated
- `open_info` inside `OPEN_METADATA` is valid only when `open_metadata` was
  negotiated
- duplicate singleton metadata TLVs inside one `OPEN_METADATA` block make
  that opening metadata block invalid
- if duplicate singleton metadata TLVs appear in one `OPEN_METADATA` block,
  the receiver MUST ignore the entire metadata block for that frame but MUST
  still process the `DATA` payload and stream-lifecycle effects of the frame
- if `metadata_len` overruns the enclosing `DATA` payload, the frame is
  malformed and the session MUST fail with `CLOSE(FRAME_SIZE)`

The first `DATA` on a new locally owned stream ID is the default immediate
stream-opening path in `zmux`.

This means a sender may send application data before it knows whether the peer
will ultimately keep or reject the new stream. If the peer rejects the stream,
the sender learns that through a later `ABORT`, not through a mandatory
stream-open acknowledgement.

### 6.2 MAX_DATA

`MAX_DATA` advertises an absolute receive limit.

Requirements:

- `stream_id = 0` means a session-wide absolute data limit
- `stream_id != 0` means a stream-local absolute data limit
- `payload = varint62 max_offset`

Allowed flags:

- none

Additional rules:

- `max_offset` counts only `DATA` payload bytes
- all flow-control offsets and byte counters are defined only within the
  protocol range `0 .. 2^62 - 1`
- stream-scoped `MAX_DATA` MUST target an already opened stream
- receiving `MAX_DATA` that would exceed the protocol-defined flow-control
  range is a session `FLOW_CONTROL` error
- a `MAX_DATA` value smaller than one already accepted for the same scope MUST
  be ignored
- the same `MAX_DATA` value MAY be repeated

`zmux v1` uses absolute byte offsets, not incremental credit deltas.

### 6.3 STOP_SENDING

`STOP_SENDING` is the receiver-side close control for one inbound stream
direction. It requests that the peer stop transmitting further `DATA` on that
direction while leaving the opposite direction unaffected.

Requirements:

- `stream_id != 0`
- payload format:

```text
varint62 error_code
tlv...    optional DIAG-TLV metadata
```

Allowed flags:

- none

Additional rules:

- `STOP_SENDING` does not open a previously unused stream
- upon receiving `STOP_SENDING`, an endpoint MUST stop sending new `DATA` on
  that stream once the stop becomes committed in local stream state
- sending `STOP_SENDING` is how a receiver closes that local read half without
  requiring whole-stream abort
- `STOP_SENDING` does not itself terminate the sender's own read side
- a stream MAY still observe peer data that was already in flight before the
  request took effect
- once an endpoint has processed `STOP_SENDING` for one of its outbound
  directions, it MUST eventually conclude that outbound direction by emitting
  either:
  - `RESET(error_code)`, if it is aborting that send half; or
  - `DATA|FIN`, if it has no more bytes to send and is ending that send half
    gracefully
- `ABORT` remains permitted as a stronger whole-stream action after
  `STOP_SENDING` if the implementation decides the entire stream should now be
  terminated rather than only that outbound half
- after processing `STOP_SENDING`, bytes already handed to the underlying
  transport MAY still arrive at the peer as late in-flight data
- after processing `STOP_SENDING`, bytes encoded only in the local session
  queue for that outbound direction SHOULD be dropped rather than flushed as
  ordinary new `DATA`
- after processing `STOP_SENDING`, application data not yet framed MUST NOT be
  converted into new `DATA`
- after `STOP_SENDING` has taken effect for an outbound direction, the sender
  MUST NOT resume ordinary application writes on that direction

### 6.4 PING

`PING` measures RTT and optionally acts as an idle-period liveness probe.

Requirements:

- `stream_id = 0`
- payload format:

```text
8 bytes   token
bytes     opaque_echo_bytes
```

In core `zmux v1`, the derived payload length for `PING` MUST be at least `8`.

Allowed flags:

- none

Semantics:

- `PING`: request

Additional rules:

- when originating `PING`, the sender MUST choose a payload length no larger
  than the smaller of:
  - the peer-advertised `max_control_payload_bytes`; and
  - the sender's own local `max_control_payload_bytes`
- implementations SHOULD make a defensive copy of the received `PING` payload
  before constructing the `PONG` reply to avoid aliasing the inbound frame
  buffer

### 6.5 PONG

`PONG` responds to a previously received `PING`.

Requirements:

- `stream_id = 0`
- payload format:

```text
8 bytes   token
bytes     opaque_echo_bytes
```

In core `zmux v1`, the derived payload length for `PONG` MUST be at least `8`.

Allowed flags:

- none

Semantics:

- `PONG`: response, MUST echo the exact payload bytes from the triggering
  `PING`

Additional rules:

- a receiver of `PING` SHOULD reply promptly with `PONG`
- the `PONG` payload MUST be a byte-for-byte verbatim copy of the triggering
  `PING` payload, including both the 8-byte token and any trailing opaque echo
  bytes
- implementations MAY disable periodic pings entirely
- local implementations MAY decide whether to originate `PING` at all based on
  underlying transport capabilities and deployment policy

### 6.6 BLOCKED

`BLOCKED` reports that a sender has data ready but cannot currently transmit it
because the applicable `MAX_DATA` limit has been reached.

Requirements:

- `stream_id = 0` means session-level blocked state
- `stream_id != 0` means stream-level blocked state
- payload format:

```text
varint62 blocked_at
```

Allowed flags:

- none

Additional rules:

- `blocked_at` is the sender's currently effective session or stream send limit
- `BLOCKED` is advisory only
- receivers MAY ignore `BLOCKED`
- stream-scoped `BLOCKED` MUST target an already opened stream
- senders SHOULD avoid emitting unbounded duplicate `BLOCKED` frames without a
  relevant state change such as new queued data or a changed limiting offset
- when multiple `BLOCKED` updates are pending for the same scope, only the
  most recent limiting offset needs to be retained; earlier intermediate values
  MAY be coalesced or superseded
- receivers that honor it MAY use it as a signal to advance or grow future
  `MAX_DATA` more promptly when local memory policy permits

### 6.7 RESET

`RESET` aborts the sender's own outbound direction for a stream.

Requirements:

- `stream_id != 0`
- payload format:

```text
varint62 error_code
tlv...    optional DIAG-TLV metadata
```

Allowed flags:

- none

Additional rules:

- `RESET` MUST target an already opened stream
- after sending `RESET`, the sender MUST NOT send further `DATA` or `DATA|FIN`
  on that same outbound direction
- a receiver that observes peer `RESET` MUST treat the corresponding inbound
  direction as terminated with error rather than graceful EOF
- if unread buffered inbound `DATA` for that direction is discarded, the
  receiver MUST apply the session-window release rule from Section 8 for those
  discarded bytes
- `RESET` does not by itself terminate the opposite direction
- duplicate or late `RESET` for an already reset or fully terminal stream MUST
  be ignored

### 6.8 ABORT

`ABORT` aborts both directions of a stream immediately.

Requirements:

- `stream_id != 0`
- payload format:

```text
varint62 error_code
tlv...    optional DIAG-TLV metadata
```

Allowed flags:

- none

Additional rules:

- after `ABORT`, no additional stream-scoped traffic for that stream is valid
  except late duplicates that the receiver ignores
- a receiver that observes peer `ABORT` MUST treat both directions as terminal
  and SHOULD surface subsequent reads and writes as terminal stream errors
- if unread buffered inbound `DATA` is discarded, the receiver MUST apply the
  session-window release rule from Section 8 for those discarded bytes
- duplicate or late `ABORT` for an already fully terminal stream MUST be
  ignored

### 6.9 GOAWAY

`GOAWAY` announces that no further new incoming streams will be accepted, while
existing streams may continue.

Requirements:

- `stream_id = 0`
- payload format:

```text
varint62 last_accepted_bidi_stream_id
varint62 last_accepted_uni_stream_id
varint62 error_code
tlv...    optional DIAG-TLV metadata
```

Allowed flags:

- none

Additional rules:

- `last_accepted_bidi_stream_id` MUST be `0` or identify a bidirectional
  stream ID that could have been created by the peer that receives the
  `GOAWAY`
- `last_accepted_uni_stream_id` MUST be `0` or identify a unidirectional
  stream ID that could have been created by the peer that receives the
  `GOAWAY`
- after receiving `GOAWAY`, the peer MUST NOT create any new bidirectional
  stream with an ID greater than `last_accepted_bidi_stream_id`
- after receiving `GOAWAY`, the peer MUST NOT create any new unidirectional
  stream with an ID greater than `last_accepted_uni_stream_id`
- attempts to open such streams SHOULD be rejected with `ABORT(REFUSED_STREAM)`
  and MAY escalate to session close if abuse continues
- a sender MAY send more than one `GOAWAY`; if it does, the advertised
  `last_accepted_bidi_stream_id` values MUST be non-increasing and the
  advertised `last_accepted_uni_stream_id` values MUST be non-increasing
- a graceful draining implementation that expects peer-created streams to
  still be in flight SHOULD first send a permissive `GOAWAY` that stops future
  open intent without prematurely rejecting likely already in-flight peer-
  created streams
- after a short drain interval, it SHOULD send a final non-increasing more
  restrictive `GOAWAY` or proceed directly to `CLOSE`

### 6.10 CLOSE

`CLOSE` terminates the entire `zmux` session.

Requirements:

- `stream_id = 0`
- payload format:

```text
varint62 error_code
tlv...    optional DIAG-TLV metadata
```

Allowed flags:

- none

After receiving `CLOSE`, the peer MUST promptly terminate the `zmux` session
and fail all remaining open streams.

### 6.11 EXT

`EXT` is the reserved extension entry point.

Requirements:

- `stream_id` MAY be `0` or a stream ID
- payload format:

```text
varint62 ext_type
bytes     ext_payload
```

Allowed flags:

- none

Unknown `EXT` subtypes MUST be ignored unless an extension specification for
that subtype requires stricter handling.

`EXT` does not implicitly open a stream in core `zmux v1`.

Extension documents defined for use with `zmux v1` MUST NOT assign implicit
stream-opening behavior to `EXT`.

If extension metadata needs to refer to a newly created stream, the sender
MUST first open that stream with an opening-eligible core frame and only then
send any stream-scoped `EXT`.

The length of `ext_payload` is implicitly:

`derived_payload_length - encoded_length(ext_type)`.

If `derived_payload_length` is smaller than `encoded_length(ext_type)`, that
frame is physically impossible and MUST be treated as a session `PROTOCOL`
error before attempting to derive `ext_payload`.

## 7. TLV and optional metadata

### 7.1 TLV encoding rules

All TLV fields use:

```text
varint62 type
varint62 length
bytes     value
```

Rules:

- TLV type spaces are scoped by the containing frame or field
- unknown TLV types in a known namespace MUST be skipped
- TLV is for metadata, advisory values, and optional extensions
- TLV MUST NOT redefine mandatory core protocol semantics for a version
- each namespace MUST define which TLV types, if any, are singleton
- if a namespace-defined singleton TLV appears more than once, handling is
  defined by that namespace
- if a namespace does not define a TLV type as singleton, repeated occurrences
  are allowed and are interpreted according to that namespace's rules
- when a core frame payload ends with an optional TLV sequence, such as the
  optional `DIAG-TLV` metadata on `STOP_SENDING`, `RESET`, `ABORT`, `GOAWAY`,
  or `CLOSE`, that TLV sequence implicitly occupies the entire remaining frame
  payload after all mandatory preceding fields have been parsed

When `debug_text` is included as a DIAG-TLV, the value MUST be valid UTF-8.
If local payload-size limits do not permit carrying the full diagnostic text,
implementations MUST truncate only at valid UTF-8 code-point boundaries. If no
valid UTF-8 prefix fits within the remaining payload budget, `debug_text`
SHOULD be omitted entirely rather than sent with invalid encoding.

Examples of separate namespaces:

- session preface `settings_tlv` uses the SETTINGS-ID namespace
- `PRIORITY_UPDATE` payload uses the STREAM-METADATA-TLV namespace
- `RESET`, `ABORT`, `STOP_SENDING`, `GOAWAY`, and `CLOSE` metadata use the
  DIAG-TLV
  namespace

### 7.2 TLV error classes

`zmux v1` distinguishes three error classes around TLV-bearing containers:

1. frame-envelope errors
2. container-structural TLV errors
3. semantic TLV errors

Frame-envelope errors are errors in the outer frame structure, such as:

- invalid outer `frame_length`
- malformed outer `varint62`
- payload overrun beyond the frame envelope

Frame-envelope errors are session `PROTOCOL` errors.

Container-structural TLV errors are errors while parsing a TLV sequence inside
an otherwise valid frame, such as:

- truncated TLV type
- truncated TLV length
- TLV value length that overruns the containing payload

Unless a container definition explicitly says otherwise, container-structural
TLV errors are treated the same way as a malformed instance of that enclosing
frame.

Semantic TLV errors occur after the TLV sequence is structurally valid, such as:

- duplicate singleton TLVs
- unknown advisory enum values
- container-specific combinations that are structurally valid but not allowed

Semantic TLV errors are handled according to the namespace or enclosing frame
definition. Unknown TLV types remain skippable unless a stricter rule is
defined by that namespace.

### 7.3 Standard priority semantics

If the `priority_hints` capability was negotiated, a stream MAY carry an
initial `stream_priority` in `OPEN_METADATA` on its first opening `DATA`, and
it MAY be updated later through the `PRIORITY_UPDATE` extension if that
capability was also negotiated.

Until such an update is received, a stream uses the default priority value
`0`.

`stream_priority` is an advisory unsigned integer with these semantics:

- `0` means default priority
- larger values mean stronger scheduling preference
- the value space is intentionally unbounded within the core integer range

The repository-default interpretation is:

- higher priority biases the implementation toward lower latency
- lower priority biases the implementation toward higher batching efficiency and
  bulk throughput

If the receiver also has a non-zero `scheduler_hints` setting, that setting
acts as the session-wide baseline policy and `stream_priority` refines
treatment within that baseline.

`zmux v1` does not mandate a specific scheduler or a fixed number of priority
levels. Implementations MAY internally map many wire values into fewer local
buckets.

If `priority_hints` was not negotiated, receivers MUST ignore
`stream_priority`.

Advertising `priority_hints` without also negotiating either `open_metadata` or
`priority_update` is valid in `zmux v1`, but it does not create a standardized
peer-visible carriage path. Such a session therefore MUST NOT be described as
providing peer-visible standardized priority interoperability. New deployments
SHOULD avoid negotiating `priority_hints` alone unless they intentionally use
that semantic bit only for local metadata surfaces.

### 7.4 Standard stream-group semantics

If the `stream_groups` capability was negotiated, a stream MAY carry an
initial `stream_group` in `OPEN_METADATA` on its first opening `DATA`, and it MAY
be updated later through the `PRIORITY_UPDATE` extension if that capability
was also negotiated.

Until such an update is received, a stream has no explicit group assignment.

`stream_group` is an advisory opaque grouping identifier with these semantics:

- it may influence local scheduling, fairness, budgeting, or placement policy
- it does not define standardized non-mux meaning
- peers MUST NOT assume that matching group values imply matching higher-layer
  semantics across different environments

If `stream_groups` was not negotiated, receivers MUST ignore `stream_group`.

In `zmux v1`, `priority_hints` and `stream_groups` define the semantics of
their respective advisory metadata TLVs. They do not by themselves create an
independent carriage path. The standardized peer-visible carriage paths for
those values are:

- `OPEN_METADATA` on the first opening `DATA`
- `PRIORITY_UPDATE` after the stream is already open

Advertising `stream_groups` without also negotiating either `open_metadata` or
`priority_update` is valid in `zmux v1`, but it does not create a standardized
peer-visible carriage path. Such a session therefore MUST NOT be described as
providing peer-visible standardized stream-group interoperability. New
deployments SHOULD avoid negotiating `stream_groups` alone unless they
intentionally use that semantic bit only for local metadata surfaces.

### 7.5 `OPEN_METADATA` on first `DATA`

If the `open_metadata` capability was negotiated, the sender MAY place initial
STREAM-METADATA-TLV metadata on the first `DATA` / `DATA|FIN` that opens a
local stream by setting the `OPEN_METADATA` flag.

Requirements:

- `frame_type = DATA`
- the frame is the first opening `DATA` / `DATA|FIN` for that stream
- `OPEN_METADATA` flag is set
- the flag-gated metadata block is a STREAM-METADATA-TLV sequence

Behavior:

- omitted recognized metadata TLVs leave the corresponding peer-visible
  metadata value at its default or absence state
- recognized metadata TLVs establish the initial peer-visible metadata values
  for subsequent handling of that stream
- unknown metadata TLVs MUST be skipped
- duplicate singleton metadata TLVs invalidate only the metadata block for
  that frame;
  they do not invalidate the enclosing `DATA`

The currently standardized open-time metadata fields are:

- `stream_priority`
- `stream_group`
- `open_info`

In `zmux v1`, all currently standardized STREAM-METADATA-TLV fields are
singleton within one enclosing metadata or update block.

`open_info` is opaque peer-visible open-time metadata with no core mux
semantics. It is intended for higher-layer dispatch or stream bootstrap logic.
Ordinary streams usually do not carry it. If an application wants to attach
multiple logical fields at open time, it should encode them into this one byte
string using its own higher-layer format; `zmux` does not define internal
structure for `open_info`.

`OPEN_METADATA` does not create a new stream-opening round trip. It only
allows the first opening `DATA` frame to carry optional open-time metadata
alongside the first application bytes.

Ordinary streams usually omit `OPEN_METADATA` entirely and open with plain
`DATA` / `DATA|FIN`.

### 7.6 `PRIORITY_UPDATE` extension

If the `priority_update` capability was negotiated, peers MAY send an `EXT`
frame with subtype `PRIORITY_UPDATE` to revise advisory scheduling hints for an
already opened stream.

Requirements:

- `frame_type = EXT`
- `stream_id != 0`
- `ext_type = PRIORITY_UPDATE`
- `ext_payload` is a STREAM-METADATA-TLV sequence
- only `stream_priority` and `stream_group` have standardized meanings in this
  payload in `zmux v1`

Behavior:

- the target stream MUST already exist
- the target stream MUST NOT be in a terminal state
- senders MUST NOT include `stream_priority` unless both `priority_update` and
  `priority_hints` were negotiated
- senders MUST NOT include `stream_group` unless both `priority_update` and
  `stream_groups` were negotiated
- `open_info` has no standardized update semantics in `PRIORITY_UPDATE` and
  MUST be ignored there
- senders MUST preserve the stream-opening dependency from Section 9.1 and
  MUST NOT emit `PRIORITY_UPDATE` ahead of the frame that first opens that same
  locally owned stream
- `PRIORITY_UPDATE` is a post-open advisory update and does not guarantee any
  effect on the first ordering-sensitive outbound frame for that stream
- omitted fields leave the previously known advisory value unchanged
- known fields update the receiver's local scheduling hints for future handling
  of that stream
- unknown TLVs in the payload MUST be skipped
- duplicate `stream_priority` TLVs in one payload make that advisory update
  payload invalid
- duplicate `stream_group` TLVs in one payload make that advisory update
  payload invalid

If duplicate singleton TLVs appear in one `PRIORITY_UPDATE` payload, the
receiver MUST ignore the entire update payload for that stream. Receivers
SHOULD log or count such dropped advisory updates for diagnostics. Base
`zmux v1` behavior does not abort the stream solely because one
`PRIORITY_UPDATE` payload repeated a singleton advisory TLV.

If the target stream is previously unseen or already terminal, the receiver
MUST ignore the update and MUST NOT create new stream state for it. Receivers
MAY record diagnostics for such dropped updates.

This is intentionally asymmetric with unseen stream-scoped `MAX_DATA` and
`BLOCKED`. `PRIORITY_UPDATE` is advisory-only metadata and does not affect
stream lifecycle or flow-control credit, so an unseen target is safely
ignorable. By contrast, unseen stream-scoped `MAX_DATA` and `BLOCKED` would
change credit-bearing or lifecycle-adjacent state and therefore remain stream-
state violations.

If `priority_update` was not negotiated, senders MUST NOT depend on this
extension. Receivers MUST ignore such frames when the capability was not
negotiated.

`PRIORITY_UPDATE` does not alter stream identity, flow control, or stream
lifecycle semantics. It only updates advisory scheduling metadata.

## 8. Flow control model

`zmux` uses dual absolute receive windows:

1. session `max_data`
2. stream `max_data`

For flow-control purposes, every sender tracks:

- bytes sent on the stream
- total `DATA` bytes sent across the session

Before sending `DATA` of length `n`, the sender MUST have:

- `stream_bytes_sent + n <= peer_stream_max_data`
- `session_bytes_sent + n <= peer_session_max_data`

Sending `DATA` advances both byte counters.

If received `DATA` would make the session-wide received byte count exceed the
currently effective session `MAX_DATA` limit, the receiver MUST fail the
session with `CLOSE(FLOW_CONTROL)`.

If received `DATA` would make only the stream-local received byte count exceed
that stream's currently effective stream-scoped `MAX_DATA` limit while the
session-wide limit would still remain satisfied, the receiver MUST fail that
stream with `ABORT(FLOW_CONTROL)`.

Only application-data bytes carried by `DATA` participate in flow-control
accounting. The optional `OPEN_METADATA` prefix inside a first opening
`DATA` / `DATA|FIN` frame (`metadata_len` and the metadata TLV block) does not
consume stream or session receive windows.

Receivers therefore deduct only the trailing application-data portion of the
validated `DATA` payload from stream and session receive windows. `OPEN_METADATA`
bytes remain bounded by frame-size limits and local memory policy, but they are
not counted against `MAX_DATA`.

`PING`, `PONG`, `BLOCKED`, `RESET`, `STOP_SENDING`, `GOAWAY`, `CLOSE`,
`ABORT`, and `EXT` are not flow-controlled by `DATA` windows, though they
remain bounded by frame size and local resource policies.

Endpoints MAY additionally enforce local receive-side byte-rate or frame-rate
budgets for control and extension traffic. Exceeding those local budgets is not
itself a wire-level flow-control violation, but it MAY trigger local defensive
dropping, coalescing, or session termination according to Section 11.

The purpose of `zmux` flow control is intentionally narrow:

- prevent the receiver from overcommitting memory
- preserve fairness across multiple streams

It is not intended to replace transport congestion control, packet loss
recovery, or packet pacing provided by the underlying reliable transport.

Receivers advance those limits with `MAX_DATA`.

Because `MAX_DATA` carries absolute offsets, it is idempotent:

- the same value MAY be repeated
- larger values supersede smaller earlier values
- intermediate updates do not need reliable preservation

Receivers MAY also advance limits beyond bytes just consumed if local policy
permits. This allows implementations to increase the effective receive budget
over time to fit local throughput and concurrency targets.

Because `MAX_DATA` values are absolute and idempotent, implementations MAY
coalesce multiple pending `MAX_DATA` updates for the same scope into a single
frame carrying only the largest current value. Similarly, implementations MAY
sort pending stream-scoped `MAX_DATA` updates by stream ID for deterministic
batch ordering without changing their semantic effect.

Once a higher absolute limit has been advertised, it is not revocable within
`zmux v1`. Receivers that want to tighten memory usage or fairness MUST do so
by slowing or stopping future limit growth, not by attempting to reduce an
already advertised limit.

Same-version extensions and local policies MUST NOT reinterpret `MAX_DATA` as a
decrement or revocation signal within `zmux v1`.

`zmux` flow control is for receive budgeting and multiplexing fairness. It is
not a transport congestion-control algorithm and MUST NOT assume packet-level
feedback from the underlying transport.

Implementations MAY aggregate limit updates and send fewer larger `MAX_DATA`
frames.

Receivers SHOULD advance limits promptly enough to avoid avoidable sender
stalls.

When a sender is blocked by the current limit and has additional data ready, it
MAY emit `BLOCKED` as a hint to the receiver.

If a receiver discards unread `DATA` payload bytes without delivering them to
the application, those bytes still count as released receive budget.

This includes cases such as:

- refusing a newly opened stream after some `DATA` has already arrived
- resetting a stream with unread buffered data
- dropping buffered stream data during local stream teardown

In those cases, the receiver MUST promptly restore the released **session**
receive budget unless the session is already terminating. If the currently
advertised absolute session `MAX_DATA` is already sufficient to preserve the
local target outstanding budget, no new session-scoped frame is required.
Otherwise the receiver MUST advertise a larger session `MAX_DATA` value.

If the affected stream remains open for further peer transmission, the receiver
SHOULD likewise restore the released stream-local receive budget. If the
currently advertised absolute stream-scoped `MAX_DATA` is already sufficient to
preserve the local target outstanding budget for that stream, no new
stream-scoped frame is required. Otherwise the receiver SHOULD advertise a
larger stream-scoped `MAX_DATA` value.

If the local endpoint has already entered reader-side stop for that stream
direction, such as after local `STOP_SENDING` / `CloseRead`, the receiver MUST
still restore the released session receive budget, but it SHOULD NOT advertise
additional stream-scoped `MAX_DATA` for that stream unless it is following an
explicitly documented bounded late-data policy for already in-flight tail
bytes.

That bounded late-data exception applies only to already in-flight tail bytes
or other bytes that were already unavoidable. It MUST NOT be used to re-enable
ordinary standing stream-window growth after local read-side stop.

No further stream-scoped update is required once the affected stream is
terminal or can no longer receive peer `DATA`.

## 9. Stream lifecycle and state rules

Each stream conceptually moves through:

- idle
- open
- half-closed-local
- half-closed-remote
- receive-stopped
- closed
- reset

The wire protocol does not require these names on the API, but implementations
MUST preserve the following behavior.

### 9.1 Opening

A stream MAY be opened by:

- sending the first opening-eligible stream-scoped core frame on a new locally
  owned stream ID
- receiving the first opening-eligible stream-scoped core frame on a
  previously unseen valid peer-owned stream ID

The opening-eligible stream-scoped core frames in `zmux v1` are:

- `DATA`
- `ABORT`

If `ABORT` is the first opening-eligible frame on a
stream, the stream opens with zero application bytes transferred so far.

Such control-opened stream state does not by itself imply application
visibility. Whether a stream becomes visible to ordinary accept/open APIs is an
API-contract question defined by local bindings and
[API_SEMANTICS.md](./API_SEMANTICS.md).

No other core frame type opens a stream in `zmux v1`.

Previously unseen valid peer-owned stream first-frame handling is therefore:

| First frame | Effect in `zmux v1` |
| --- | --- |
| `DATA` | open stream |
| `DATA|FIN` | open stream and half-close sender write side |
| `ABORT` | open hidden terminal bookkeeping by default |
| `RESET` | session `PROTOCOL` error |
| `STOP_SENDING` | session `PROTOCOL` error |
| stream-scoped `MAX_DATA` | session `PROTOCOL` error |
| stream-scoped `BLOCKED` | session `PROTOCOL` error |
| stream-scoped `EXT` | ignored; does not open the stream |

Senders MUST preserve this opening dependency even when local writer queues
give control traffic higher priority. A sender MUST NOT emit a stream-scoped
`MAX_DATA`, stream-scoped `BLOCKED`, or any stream-scoped `EXT` before it has
emitted an opening-eligible frame for that same locally owned stream.

Opening eligibility is still constrained by stream kind and direction:

- `DATA` is a sender-side frame and may only be sent by the side that is
  allowed to transmit `DATA` on that stream
- `ABORT` may be sent by either side to terminate the whole stream once the
  stream already exists
- on a previously idle stream, a locally originated first `ABORT` is valid only
  on a new locally owned stream ID; a local endpoint MUST NOT use `ABORT` as
  the first frame on a previously unseen peer-owned stream ID

The local endpoint MAY open either:

- a bidirectional stream using its next local bidirectional stream ID
- a unidirectional stream using its next local unidirectional stream ID

The sender does not need to wait for an ACK before sending further `DATA`.

This is intentional. Requiring a per-stream acknowledgement before application
data would add an avoidable round trip to every new stream and would hurt the
short-flow latency that multiplexers are often meant to improve.

The tradeoff is that a peer may reject a newly opened stream after some data
has already been transmitted. Implementations and applications must tolerate
that failure mode.

If the local side cannot allocate a new outbound stream ID within the allowed
numeric range, it SHOULD stop opening new streams and SHOULD begin graceful
session shutdown with `GOAWAY`.

For a unidirectional stream:

- only the opener may send `DATA`
- only the opener may send `BLOCKED`
- only the non-opener may send stream-scoped `MAX_DATA`
- only the non-opener may send `STOP_SENDING`
- only the opener may send `RESET`
- either side may send `ABORT`

The peer MUST NOT send sender-side frames on the non-sending half of a
unidirectional stream, and the opener MUST NOT send receiver-side
flow-control or stop-sending frames on that same stream.

### 9.2 Half-close

To close the local write direction, a peer sends:

- `DATA|FIN` with payload
- `DATA|FIN` with empty payload

The receiver MUST return EOF only after all buffered data has been delivered.
The reverse direction remains writable unless it is also closed or reset.

After sending `FIN` on one direction, an endpoint MUST NOT send further `DATA`
on that same direction. A peer that receives additional `DATA` after having
already observed `FIN` for that direction MUST treat it as a stream-state
violation.

For a bidirectional stream, stream processing becomes fully quiescent only
after both directions have separately reached graceful or abortive terminal
conditions. `STOP_SENDING` participates as a reader-side stop or cancel
control; it does not itself mean peer-graceful EOF.

A connection-style API may therefore expose separate local operations for
write-half completion, read-side stop, send-half abort, and full-stream abort,
rather than collapsing them into one close primitive.

### 9.3 Stop-sending

Any side MAY send `STOP_SENDING` to request that the peer stop sending further
`DATA` on the stream.

After receiving `STOP_SENDING`, the peer MUST stop sending new `DATA` on that
stream once the stop becomes committed in local stream state.

`STOP_SENDING` is the receiver-side close control for one stream direction. It
affects only the peer's sending direction and the sender's local read half. It
does not close the local sending direction.

An endpoint MUST tolerate a bounded amount of peer `DATA` that was already in
flight before the peer processed `STOP_SENDING`. Such late-arriving bytes MAY
be delivered to the application or discarded according to local API policy.

A local API that exposes read-half close semantics should map that operation to
`STOP_SENDING` plus local discard of unread inbound data.

If unread inbound `DATA` is discarded as part of local read-side stop, the
receiver MUST apply the released-window rules from Section 8.

If the outbound direction is still open when `STOP_SENDING` takes effect, the
sender MUST complete that outbound direction with either:

- `RESET(error_code)`, if it is aborting the send half; or
- `DATA|FIN`, if it chooses graceful send completion and has no further data

The sender MUST NOT keep that outbound direction indefinitely half-open after
`STOP_SENDING` has taken effect.

`ABORT` remains permitted as a stronger action after `STOP_SENDING`. In that
case the whole stream is terminated instead of only concluding the outbound
half.

Repository-default sender conclusion summary after `STOP_SENDING`:

| Local outbound state when stop becomes committed | Allowed repository-default outcome |
| --- | --- |
| no further application bytes have become unavoidable | prefer `RESET(CANCELLED)` |
| only a negligible already-committed tail remains and graceful completion is immediate | allow `DATA|FIN` |
| bounded graceful-drain attempt expires before conclusion | switch to `RESET(CANCELLED)` |
| local whole-stream cancellation or stronger terminal action occurs | `ABORT(CANCELLED)` or another caller-selected `ABORT` code |
| outbound half was already terminal before `STOP_SENDING` became visible | no additional concluding frame required |

### 9.4 Reset

Any side MAY send `RESET` to abort only its own outbound direction.

If a receiver causes unread buffered `DATA` for the affected inbound direction
to be discarded, it MUST apply the session-window release rule from Section 8
for those discarded bytes.

After peer `RESET`, the corresponding inbound direction is terminal with error.
The opposite direction remains usable unless later terminated by `FIN`,
`STOP_SENDING`-driven shutdown, or `ABORT`.

After local `RESET`, the local outbound direction is terminal with error.
Further local writes on that direction are invalid, but local reads MAY remain
usable.

Late non-opening control frames for an already reset half MUST be ignored.
Late in-flight `DATA` or `DATA|FIN` for that already reset inbound direction
MUST also be ignored rather than treated as a new stream-state violation.
Discarded late payload bytes for such ignored late `DATA` MUST still follow the
same discard-and-budget-release path used for other local discards so that the
session receive window does not bleed permanently. Implementations SHOULD also
enforce both a per-stream late-tail cap and an aggregate session-wide cap for
late data absorbed after `RESET`.

### 9.5 Abort

Any side MAY send `ABORT` to terminate the whole stream immediately.

If a receiver causes unread buffered `DATA` for that stream to be discarded, it
MUST apply the session-window release rule from Section 8 for those discarded
bytes.

If a peer-owned valid stream ID is previously unseen and the first frame
received for it is `ABORT`, the receiver MUST treat that stream as entering a
terminal abort state and MUST record the stream ID as used.

After `ABORT`, both directions of the stream are terminal. A connection-style
API SHOULD surface subsequent reads and writes as terminal stream errors rather
than a graceful EOF.

`ABORT` remains a stronger action even if one half had already reached graceful
completion. Sending `ABORT` after local or peer `FIN` does not revoke or
reinterpret bytes already delivered gracefully on that finished half; it only
terminates whatever stream state or opposite direction was still live.

After a stream becomes fully terminal, late non-opening control frames for
that stream MUST be ignored. Late in-flight `DATA` or `DATA|FIN` that arrive
after the abort became visible MUST also be ignored rather than escalating to a
fresh stream-state violation.

Discarded late payload bytes for such ignored late `DATA` MUST still follow the
same discard-and-budget-release path used for other local discards so that the
session receive window does not bleed permanently. Implementations SHOULD also
enforce both a per-stream late-tail cap and an aggregate session-wide cap for
late data absorbed after `ABORT`.

### 9.6 Stream-state violations

Recommended handling:

For this section, **stream-local signalling remains valid** only when sending a
stream-scoped response would not itself create, reopen, or revive stream state,
would not advance a next-expected cursor, and does not depend on a previously
unseen peer-owned stream ID becoming locally signallable. Previously unseen
peer-owned stream IDs therefore do not permit stream-local rejection by sending
first-frame `ABORT` from the local side.

- if a new peer-initiated bidirectional stream exceeds
  `max_incoming_streams_bidi`, reject it with `ABORT(REFUSED_STREAM)`
- if a new peer-initiated unidirectional stream exceeds
  `max_incoming_streams_uni`, reject it with `ABORT(REFUSED_STREAM)`
- if a peer-owned new stream ID skips the next expected ID for its class, treat
  it as a session `PROTOCOL` error
- if peer `MAX_DATA`, `BLOCKED`, `STOP_SENDING`, or `RESET` arrives on a
  previously unseen valid stream ID, treat it as a session `PROTOCOL` error
- if peer `DATA` arrives on a locally send-only unidirectional stream, reject
  it with `ABORT(STREAM_STATE)` while stream-local signalling remains valid
- if peer `BLOCKED` arrives on a locally send-only unidirectional stream,
  reject it with `ABORT(STREAM_STATE)` while stream-local signalling remains valid
- if peer `RESET` arrives on a locally send-only unidirectional stream, reject
  it with `ABORT(STREAM_STATE)` while stream-local signalling remains valid
- if peer `MAX_DATA` or `STOP_SENDING` arrives on a locally receive-only
  unidirectional stream, reject it with `ABORT(STREAM_STATE)` while stream-local signalling remains valid
- if peer `DATA` exceeds only the stream-scoped `MAX_DATA` limit for that
  stream while the session-wide limit remains satisfied, reject it with
  `ABORT(FLOW_CONTROL)`
- if `DATA` arrives after the receiving side has observed peer `FIN`, reject it
  with `ABORT(STREAM_CLOSED)`
- if a peer reuses a previously used stream ID for a new stream open attempt,
  treat it as a session `PROTOCOL` error

Implementations SHOULD prefer stream-local failure over session-wide failure
when the violation is confined to one stream and does not imply parser or
state-machine desynchronization.

Violation-handling summary:

| Condition | Repository-default handling scope |
| --- | --- |
| previously unseen peer-owned stream receives non-opening core stream frame | session `PROTOCOL` |
| peer-owned stream ID skips the next expected ID of its class | session `PROTOCOL` |
| wrong-direction stream-scoped frame on an already opened stream where stream-local signalling remains valid | `ABORT(STREAM_STATE)` |
| `DATA` exceeds only stream-local `MAX_DATA` | `ABORT(FLOW_CONTROL)` |
| `DATA` exceeds session `MAX_DATA` | session `CLOSE(FLOW_CONTROL)` |
| `DATA` arrives after peer `FIN` on that direction | `ABORT(STREAM_CLOSED)` |
| late non-opening control on already terminal stream | ignore |
| late in-flight `DATA` after peer `RESET` or `ABORT` | ignore payload, still follow discard-and-budget-release path |

## 10. Session lifecycle

### 10.1 Graceful shutdown

Recommended sequence:

1. stop admitting new local streams
2. if local shutdown policy still permits accepting likely already in-flight
   peer opens, send an initial permissive `GOAWAY`
3. allow a short drain interval
4. send a final non-increasing more restrictive `GOAWAY` or proceed directly
   to `CLOSE`
5. drain existing streams
6. send `CLOSE` or close the underlying transport

Repository-default graceful-shutdown summary:

| Stage | Repository-default intent |
| --- | --- |
| stop local admission | prevent new locally opened streams immediately |
| initial permissive `GOAWAY` | stop future peer open intent without prematurely rejecting likely already in-flight peer opens |
| short drain interval | give already in-flight peer opens and finishing traffic time to arrive |
| final `GOAWAY` or direct `CLOSE` | narrow the accepted watermark or escalate directly to terminal shutdown |
| stream drain | let streams with remaining local close-relevant work finish under local policy; unread inbound-only tails need not prolong drain once local send-side work is done |
| final close | send `CLOSE` or terminate the underlying transport when graceful drain is complete |

A receiver that observes a subsequent `GOAWAY` with a higher
`last_accepted_bidi_stream_id` or `last_accepted_uni_stream_id` than a
previously received `GOAWAY` MUST treat this as a session `PROTOCOL` error.
`GOAWAY` watermarks are strictly non-increasing within a session.

### 10.2 Fatal shutdown

On unrecoverable protocol or internal errors:

1. send `CLOSE`
2. close the underlying transport

If the underlying transport closes without a prior `CLOSE`, the `zmux` session
is considered terminated immediately.

Duplicate session-scoped terminal frames received after session termination
MUST be ignored.

## 11. Error handling and unknown elements

Core error signaling uses numeric error codes only.

Core error codes occupy the range `0-255`.

Any value `>= 256` is a non-core passthrough error code.

Core `zmux v1` does not assign built-in meanings to non-core values. It only
transports them unchanged inside `STOP_SENDING`, `RESET`, `ABORT`, `GOAWAY`,
and `CLOSE`.

Companion documents, extension documents, or higher-layer protocols MAY assign
their own meanings to non-core values by separate agreement. Core `zmux`
neither interprets nor remaps them.

Human-readable diagnostics, if sent at all, SHOULD use optional DIAG-TLVs and
SHOULD be conservative by default.

The standardized DIAG-TLV fields in `zmux v1` are advisory metadata only:

- `debug_text` is human-readable diagnostic text
- `retry_after_millis` is a machine-readable advisory retry hint only; it does
  not by itself create a mandatory backoff rule
- `offending_stream_id` and `offending_frame_type` are machine-readable context
  only; they do not alter parsing, blame, or stream-state decisions

All standardized DIAG-TLV fields are singleton within one enclosing frame. If a
single frame repeats any standardized singleton DIAG-TLV, the receiver SHOULD
ignore the duplicated DIAG block for that frame while preserving the enclosing
frame's primary semantics.

Recommended error domains:

- session parse and negotiation failures: `CLOSE(...)`
- frame-size and flow-control failures: `CLOSE(...)`
- per-stream admission or lifecycle failures: `ABORT(...)`

Implementations MAY also treat sustained abuse as a session-failure
condition. This includes excessive empty `DATA`, repeated `PING`, or redundant
advisory/control traffic that does not materially advance stream or session
state. In such cases, implementations MAY terminate the session with
`CLOSE(PROTOCOL)` or `CLOSE(INTERNAL)` according to local policy.

Version-handling rules:

- unknown capability bits MUST be ignored
- unknown setting IDs MUST be ignored
- unknown TLV types in a known namespace MUST be skipped
- unknown `EXT` subtypes MUST be ignored unless a stricter extension document
  says otherwise
- unknown core frame types MUST be treated as protocol errors

## 12. Implementation freedom

The protocol does not require:

- fixed-period heartbeats
- one underlying write per control event
- fixed fragment sizes
- separate writes for control and data frames
- one specific local scheduler

Implementations therefore remain free to:

- batch writes
- aggregate `MAX_DATA` updates
- send pings only when idle
- add keepalive jitter
- map advisory scheduling hints to local scheduler behavior

## 13. Security considerations

Implementations should treat resource exhaustion and traffic-shape abuse as
first-class concerns.

At minimum, repository-default implementations SHOULD bound:

- control-opened-only hidden stream state
- not-yet-accepted inbound stream bytes
- provisional local-open state
- urgent control-lane memory
- stopped-direction late tail absorption
- repeated advisory updates that do not change effective local policy

Implementations SHOULD also defend against abusive traffic patterns including:

- unbounded zero-length `DATA` churn
- high-rate `PING` traffic
- repeated advisory control traffic that does not advance effective state
- rapid open-then-abort or open-then-reset churn intended to bypass concurrent
  stream limits

Concurrent stream limits alone are not sufficient protection against rapid
open-and-abort churn. Implementations SHOULD bound stream creation churn rate
and MAY terminate abusive peers with `CLOSE(PROTOCOL)` or `CLOSE(INTERNAL)`
when local shedding or throttling is insufficient.

When such abuse is detected, local defensive action MAY include:

- dropping redundant local work
- refusing hidden or provisional state
- stream-local `ABORT(...)` when that remains valid
- session `CLOSE(PROTOCOL)` or `CLOSE(INTERNAL)` when narrower shedding is
  insufficient

Human-readable diagnostics are optional. If `debug_text` or other diagnostic
TLVs are sent, implementations SHOULD treat them as operational diagnostics
rather than trusted machine-readable protocol inputs, and SHOULD avoid leaking
more deployment detail than local policy intends.

`PING` and `PONG` are control-path tools only. They do not prove application
progress on a specific stream and should not be treated as a universal request
timeout or business-level liveness signal.
