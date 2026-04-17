# zmux Stream State Machine

This document is a normative state-transition reference for
[SPEC.md](./SPEC.md).

Its purpose is to restate the core stream lifecycle as explicit half-direction
state transitions so independent implementations do not invent incompatible
local interpretations.

It should be read as the stream-lifecycle companion to the complete current
`zmux-v1` surface, including the opening and terminal rules used by the active
same-version features in this repository.

## 1. Model

Each `zmux` stream has two independent byte-stream halves:

- the **local send half**: bytes the local endpoint transmits toward the peer
- the **local receive half**: bytes the local endpoint receives from the peer

For a bidirectional stream, both halves exist.

For a unidirectional stream:

- on a locally opened unidirectional stream, the local send half exists and
  the local receive half is absent
- on a peer-opened unidirectional stream, the local receive half exists and
  the local send half is absent

`STOP_SENDING` acts on the peer's send half and therefore on the local receive
half.

`RESET` acts on the sender's own send half and therefore on the peer's receive
half.

`ABORT` terminates both halves at once.

## 2. Opening rules

- A stream leaves `idle` when the first opening-eligible stream-scoped core
  frame is sent or received on a valid unused stream ID.
- In `zmux v1`, the opening-eligible stream-scoped core frames are `DATA` and
  `ABORT`.
- `RESET` does not open a previously unseen stream in `zmux v1`.
- `EXT` never opens a stream in `zmux v1`.
- A first inbound `ABORT` on a valid peer-owned stream ID is allowed when that
  frame is legal for the stream kind and direction.
- A local first `ABORT` on an idle stream is valid only on a new locally owned
  stream ID. A local endpoint cannot use `ABORT` as the first frame on a
  previously unseen peer-owned stream ID.

Previously unseen valid peer-owned stream first-frame outcomes:

| First frame | Result |
| --- | --- |
| `DATA` / `DATA|FIN` | stream opens |
| `ABORT` | hidden terminal bookkeeping by default |
| `RESET` | invalid |
| `STOP_SENDING` | invalid |
| stream-scoped `MAX_DATA` | invalid |
| stream-scoped `BLOCKED` | invalid |
| stream-scoped `EXT` | ignored; does not open the stream |

Opening eligibility is still constrained by stream kind and direction:

- `DATA` is a sender-side frame and may only be sent by the side that may send
  `DATA` on that stream
- `RESET` may be sent only by a side that has a local send half on that stream
- `ABORT` may be sent by either side

When a stream leaves `idle`, its halves are initialized according to stream
kind and opener before the triggering frame is applied as an event:

- locally opened bidirectional: local send half = `send_open`, local receive
  half = `recv_open`
- locally opened unidirectional: local send half = `send_open`, local receive
  half = `absent`
- peer-opened bidirectional: local send half = `send_open`, local receive half
  = `recv_open`
- peer-opened unidirectional: local send half = `absent`, local receive half =
  `recv_open`

## 3. Half states

`idle` is a stream-level pre-creation state rather than a half-state. The
half-state tables below apply only after one opening-eligible frame has caused
the stream to exist.

### 3.1 Local send-half states

- `absent`: this stream has no local send half
- `send_open`: local outbound bytes may still be sent
- `send_stop_seen`: peer `STOP_SENDING` has become locally visible; new
  application writes are no longer allowed, though already committed tail bytes
  may still drain before final conclusion
- `send_fin`: local outbound direction has ended gracefully with `DATA|FIN`
- `send_reset`: local outbound direction has ended abortively with `RESET`
- `send_aborted`: the whole stream has been aborted

### 3.2 Local receive-half states

- `absent`: this stream has no local receive half
- `recv_open`: peer outbound bytes may still arrive
- `recv_fin`: peer outbound direction has ended gracefully
- `recv_stop_sent`: local endpoint has sent `STOP_SENDING`; a bounded amount of
  in-flight peer data may still arrive before terminal completion
- `recv_reset`: peer outbound direction has ended abortively with `RESET`
- `recv_aborted`: the whole stream has been aborted

Repository-default local `Read` error resolution checks local API state before
protocol half-state. If the local application has issued a read-side stop
(such as `CloseRead`), subsequent `Read` calls SHOULD fail with a local
read-stopped error even if the protocol receive half has since transitioned to
`recv_fin` or `recv_reset` through peer action. This preserves the local
cancellation precedence: once the application has expressed disinterest in
further reads, the specific peer-side terminal outcome is secondary.

## 4. Local action transitions

### 4.1 Local send half

| Current state | Event | Result |
| --- | --- | --- |
| `absent` | local `DATA`, `DATA|FIN`, `RESET` | invalid |
| `absent` | local `ABORT` | `send_aborted` only when opening a new locally owned stream; otherwise invalid |
| `send_open` | local `DATA` | `send_open` |
| `send_open` | local `DATA|FIN` | `send_fin` |
| `send_open` | local `RESET` | `send_reset` |
| `send_open` | local `ABORT` | `send_aborted` |
| `send_stop_seen` | local `DATA` | local API error, no state change |
| `send_stop_seen` | local `DATA|FIN` | `send_fin` |
| `send_stop_seen` | local `RESET` | `send_reset` |
| `send_stop_seen` | local `ABORT` | `send_aborted` |
| `send_fin` / `send_reset` | local `ABORT` | `send_aborted` |
| `send_fin` / `send_reset` / `send_aborted` | local `DATA`, `DATA|FIN`, `RESET` | local API error, no state change |
| `send_aborted` | local repeated `ABORT` | unchanged |

On a locally opened stream, the first outbound `DATA`, `DATA|FIN`, or `ABORT`
creates the stream if it was previously idle.

### 4.2 Local receive half

| Current state | Event | Result |
| --- | --- | --- |
| `absent` | local `STOP_SENDING` | invalid |
| `absent` | local `ABORT` | `recv_aborted` |
| `recv_open` | local `STOP_SENDING` | `recv_stop_sent` |
| `recv_open` | local `ABORT` | `recv_aborted` |
| `recv_fin` / `recv_reset` / `recv_stop_sent` | local `ABORT` | `recv_aborted` |
| `recv_fin` / `recv_reset` / `recv_aborted` | local `STOP_SENDING` | local API error or no-op |
| `recv_stop_sent` | local repeated `STOP_SENDING` | unchanged |
| `recv_aborted` | local repeated `ABORT` | unchanged |

On a peer-opened stream, local `ABORT` is valid only after the stream already
exists. A local endpoint cannot create a previously unseen peer-owned stream by
sending first-frame `ABORT`. `STOP_SENDING` also does not open a previously
unseen stream.

## 5. Peer action transitions

### 5.1 Local receive half

| Current state | Event | Result |
| --- | --- | --- |
| `absent` | peer `DATA` / `DATA|FIN` / `RESET` | invalid |
| `absent` | peer `ABORT` | `recv_aborted` |
| `recv_open` | peer `DATA` | `recv_open` |
| `recv_open` | peer `DATA|FIN` | `recv_fin` after buffered data is drained |
| `recv_open` | peer `RESET` | `recv_reset` |
| `recv_open` | peer `ABORT` | `recv_aborted` |
| `recv_stop_sent` | peer late in-flight `DATA` | `recv_stop_sent` |
| `recv_stop_sent` | peer `DATA|FIN` after in-flight drain | `recv_fin` |

The `recv_stop_sent` to `recv_fin` transition via peer `DATA|FIN` represents
the case where the peer gracefully concludes its send half in response to (or
concurrent with) the local `STOP_SENDING`. This transition is valid because
`recv_stop_sent` is not yet a terminal state — it indicates the local endpoint
has requested the peer to stop, not that the peer has necessarily complied.
The peer may still choose graceful completion over abortive reset.

| `recv_stop_sent` | peer `RESET` | `recv_reset` |
| `recv_stop_sent` | peer `ABORT` | `recv_aborted` |
| `recv_fin` | peer `DATA` / `DATA|FIN` | invalid |
| `recv_reset` / `recv_aborted` | peer late in-flight `DATA` / `DATA|FIN` | unchanged |
| `recv_fin` / `recv_reset` | peer repeated `RESET` | unchanged |
| `recv_aborted` | peer repeated `ABORT` | unchanged |

### 5.2 Local send half

| Current state | Event | Result |
| --- | --- | --- |
| `absent` | peer `STOP_SENDING` | invalid |
| `absent` | peer `ABORT` | `send_aborted` |
| `send_open` | peer `STOP_SENDING` | `send_stop_seen` |
| `send_stop_seen` | peer repeated `STOP_SENDING` | unchanged |
| `send_open` | peer `ABORT` | `send_aborted` |
| `send_stop_seen` | peer `ABORT` | `send_aborted` |
| `send_fin` / `send_reset` | peer `STOP_SENDING` | unchanged |
| `send_fin` / `send_reset` | peer `ABORT` | `send_aborted` |
| `send_aborted` | peer `STOP_SENDING` / `ABORT` | unchanged |

`send_stop_seen` is not yet terminal, but it is semantically different from
`send_open`: the local application can no longer enqueue ordinary new writes.
Only already committed tail bytes may still drain before the sender concludes
that outbound half with `DATA|FIN`, `RESET`, or the stronger whole-stream
`ABORT`.

## 6. Graceful and abortive completion

A stream is **gracefully complete** only when every existing half has reached a
graceful terminal state:

- local send half: `send_fin` or absent
- local receive half: `recv_fin` or absent

A stream is **abortively complete** when either side has processed `ABORT`.

A stream is **partially abortive** when one outbound half has reached
`send_reset` or one inbound half has reached `recv_reset` but the opposite
direction is still alive.

There is no dedicated graceful full-close frame. Graceful full close is the
combination of graceful send completion plus eventual graceful peer completion.
Local reader-side stop is separate:

- `DATA|FIN` gracefully ends the local send half
- `STOP_SENDING` is a local read-side stop or cancel signal, not a synonym for
  peer-graceful EOF

Abortive full close is `ABORT`.

### 6.1 Terminal resolution and close visibility

When multiple overlapping close conditions exist, implementations MUST surface
them to local API callers in a consistent way.

Repository-default terminal resolution priority is, from strongest to weakest:

1. `send_aborted` or `recv_aborted` — whole-stream abort error
2. `send_reset` or `recv_reset` — direction-specific reset error
3. `send_fin` — graceful write-side completion
4. `recv_fin` — graceful read-side EOF

`send_stop_seen` and `recv_stop_sent` are not terminal states. They are
intermediate local-visibility conditions:

- `send_stop_seen` closes ordinary new writes before later `send_fin`,
  `send_reset`, or `send_aborted` becomes visible
- `recv_stop_sent` closes ordinary further reads before later `recv_fin`,
  `recv_reset`, or `recv_aborted` becomes visible

Once a terminal condition becomes visible on that direction, it takes
precedence over the earlier stop-only condition. When both an abort and a
reset are present, the abort error takes precedence. When both send-side and
receive-side terminal conditions exist at the same severity level, the
send-side abort or reset takes precedence over the receive-side equivalent for
combined error queries.

## 7. Unidirectional constraints

For a unidirectional stream:

- only the opener may send `DATA`
- only the opener may send `BLOCKED`
- only the non-opener may send stream-scoped `MAX_DATA`
- only the non-opener may send `STOP_SENDING`
- only the opener may send `RESET`
- either side may send `ABORT`

Therefore:

- peer `DATA` is always invalid on a locally send-only unidirectional stream
- peer `BLOCKED` is always invalid on a locally send-only unidirectional
  stream
- local `MAX_DATA` is always invalid on a locally send-only unidirectional
  stream
- local `STOP_SENDING` is always invalid on a locally send-only
  unidirectional stream

## 8. Terminal handling

After a stream is fully terminal:

- late non-opening control frames are ignored
- late `DATA` after peer `FIN` is invalid
- late in-flight `DATA` after peer `RESET` or `ABORT` is ignored
- local `Write` and `Read` should fail promptly with terminal errors rather
  than hang

After a local or peer `RESET`, only the affected half is terminal.

After `ABORT`, both halves are terminal immediately.

Terminal late-frame handling summary:

| Stream condition | Late frame class | Result |
| --- | --- | --- |
| after peer `FIN` on one direction | late `DATA` / `DATA|FIN` on that same direction | invalid |
| after peer `RESET` on one direction | late in-flight `DATA` / `DATA|FIN` for that direction | ignore and apply discard-and-budget-release |
| after peer `ABORT` or local/peer full terminal stream state | late in-flight `DATA` / `DATA|FIN` | ignore and apply discard-and-budget-release |
| fully terminal stream | late non-opening control | ignore |

### 8.1 Compact terminal state

Once a stream is fully terminal and no local queued work or buffered data
remains, implementations MAY compact the stream into a minimal tombstone
record. A tombstone retains only:

- the stream ID used marker (to prevent reuse)
- the terminal kind (graceful, reset, or aborted)
- the late-data handling policy for the receive direction

Repository-default late-data policies for tombstones are:

| Stream condition at compaction | Late `DATA` action |
| --- | --- |
| stream had no local receive half (send-only unidirectional) | ignore silently |
| receive half was `recv_fin` (graceful close) | reject with `ABORT(STREAM_CLOSED)` |
| receive half was `recv_reset` or `recv_aborted` | ignore and apply discard-and-budget-release |

Tombstones MUST NOT be reaped in a way that permits stream ID reuse or loss of
the used-ID marker semantics for that session. Implementations MAY use
range-compressed markers, bitmaps, or the `next_expected_stream_id` cursor
instead of per-stream tombstone objects when the resulting late-frame handling
and no-reuse semantics remain correct.

## 9. Invalid events

The following are always invalid:

- reusing a previously used stream ID for a new open
- a peer-owned new stream ID that skips lower still-unused IDs of the same
  class
- `RESET` on a previously unused stream ID
- `STOP_SENDING` on a previously unused stream ID
- `MAX_DATA` on a previously unused stream ID
- `BLOCKED` on a previously unused stream ID
- local first `ABORT` on a previously unseen peer-owned stream ID
- treating a stream-scoped `EXT` as opening a previously unseen stream ID
- peer `DATA` on a locally send-only unidirectional stream
- forbidden non-zero frame flags

## 10. Session lifecycle

Repository-default session lifecycle states are:

- `establishing`: prefaces not yet fully parsed or stream-ID ownership not yet
  resolved
- `ready`: session established and ordinary streams may be opened or accepted
- `draining`: local or peer `GOAWAY` has stopped new opens in at least one
  direction, while existing streams may still complete
- `closing`: `CLOSE` has been sent or received, or a fatal local session error
  has committed closing behavior
- `closed`: orderly session shutdown is complete
- `failed`: the underlying transport failed or session shutdown was not
  orderly

Repository-default transition guidance:

- `establishing -> ready`: both prefaces parsed successfully and role
  resolution complete
- `establishing -> failed`: invalid preface, version conflict, role conflict,
  or transport failure before session readiness
- `ready -> draining`: local or peer `GOAWAY` narrows future stream admission
- `ready -> closing`: local `CLOSE`, peer `CLOSE`, or another fatal
  session-wide error
- `draining -> closing`: local shutdown escalates to `CLOSE`, peer sends
  `CLOSE`, or a fatal session-wide error occurs
- `closing -> closed`: orderly shutdown work completes and the underlying
  transport is closed or quiesced as intended
- `ready` or `draining` -> `failed`: underlying transport fails without an
  orderly `CLOSE` sequence

Once the session is in `closing`, `closed`, or `failed`, implementations
should fail new open attempts and wake blocked operations promptly with
session-termination errors rather than leaving them queued indefinitely.

Required handling remains defined by [SPEC.md](./SPEC.md).
