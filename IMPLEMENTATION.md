# zmux Implementation Guidance

This document defines the repository-level default implementation guidance.

Its purpose is to describe implementation strategies that fit the `zmux` wire
protocol well and avoid the common failure modes seen in real multiplexers:

- latency spikes
- control-frame starvation
- memory blowups
- unnecessary timing rigidity
- confused lifecycle handling

This guidance is intentionally scoped to **one `zmux` session over one
underlying reliable, ordered, full-duplex byte stream**.

This document mixes two kinds of guidance:

- interoperability-sensitive local behavior that should stay aligned across
  conforming implementations
- repository-default reference profile choices for sender scheduling, queue
  sizing, and other local policy details

Where this document says **repository-default**, alternative local tuning is
allowed as long as wire behavior and documented API semantics remain
compatible.

## 1. Implementation goals

At the mux layer, `zmux` does not require an extra per-stream acknowledgement
round trip:

- both peers send the session preface immediately
- a sender may transmit regular frames as soon as its own preface is sent
- stream open does not require a per-stream acknowledgement
- first `DATA` on a new stream is the fast path for first-byte latency

That means most remaining performance work is implementation policy, not wire
format.

Implementations should:

- avoid waiting for acknowledgements the protocol does not require
- keep write coalescing delay small and bounded
- avoid forcing control frames to wait behind long bulk-only batches
- send the session preface and the first `DATA` in close succession or the
  same underlying batch when local buffering and API timing allow it
- keep `role = auto` nonce-collision retry in higher-layer connection
  orchestration rather than making it part of the core mux sender path
- generate `role = auto` tie-breaker nonces from a strong random source, do
  not reuse a nonce from a failed attempt, and treat an equal-nonce collision
  as a failed establishment attempt that must be retried only through a fresh
  session attempt

Repository-default sender behavior follows the stricter core `zmux v1`
session-ready rule:

- do not create new streams or send application `DATA` before peer preface
  parsing completes
- for `role = auto`, also wait until role resolution completes
- once the session is ready, send the first opening `DATA` immediately when
  local policy allows rather than adding any extra mux acknowledgement step

Repository-default establishing-state outbound policy is:

- before `session-ready`, send only the local preface and a fatal session
  `CLOSE` if establishment must fail
- do not send new-stream `DATA`
- do not send stream-scoped control
- do not send ordinary session-scoped control or `EXT`

They should also avoid redoing work that the underlying transport or wrapper
already does well.

When available, reuse:

- transport-native close detection or cancellation
- transport-native keepalive or liveness signaling
- transport-exposed RTT or path estimates
- transport-exposed writable-send allowance

`zmux` should add multiplexing, backpressure, and lifecycle control. It should
not grow a second retransmission, reordering, or congestion-control stack on
top of a transport that already provides those properties.

Control-plane parsing should also stay small and predictable.

Because `PING`, `PONG`, `BLOCKED`, `RESET`, `STOP_SENDING`, `GOAWAY`, `CLOSE`,
`ABORT`, and `EXT` are all bounded by negotiated small payload limits,
implementations should parse them with fixed-size scratch buffers or small
pooled buffers instead of unbounded dynamic allocation.

### 1.1 Repository-default concurrency model

Repository-default implementations should keep one explicit linearization model
for shared session and stream state:

- one session writer path linearizes outbound frame order
- receive-side parsing may be pipelined or parallelized, but commits to shared
  stream or session state should still pass through one clear serialization
  point per session
- accept-queue insertion should be ordered by the local visibility sequence
  rather than by opportunistic parser-thread race
- waiter wakeups should happen only after the state transition and any related
  budget or reservation changes are visible to other local operations

Repository-default scalability guidance:

- complexity-sensitive sender and scheduler operations SHOULD scale with the
  number of active streams or active groups, not the total number of streams
  known to the session
- quiescent streams SHOULD remain passive state objects; repository-default
  implementations SHOULD avoid per-stream tasks, mailboxes, or fine-grained
  timers as the ordinary representation
- transitions between quiescent and active scheduling state SHOULD happen on
  queue-empty to queue-non-empty edges, not on every small buffer mutation
- timer-driven local policy such as keepalive, provisional-open expiry, or
  tombstone aging SHOULD use shared coarse timers or timer-wheel style
  facilities when the host platform provides them

That model is intentionally stricter than the minimum wire contract because it
reduces cross-language behavioral drift in cancellation, reclaim, and
concurrent open paths.

## 2. Sending path

### 2.1 Recommended writer model

The repository-default design is a **single dispatcher plus single writer
loop**:

1. a session-global dispatcher accepts stream write descriptors and control
   work, applies local fairness policy, and preserves opening dependencies
2. one egress queue and one writer task serialize the chosen work onto the
   underlying byte stream
3. writes are batched through a buffered writer or equivalent transport buffer
4. flushes happen when:
   - the batch becomes large
   - a control-priority event arrives
   - a bounded coalescing timer expires

This design keeps throughput high and syscall overhead lower than one write per
frame.

Repository-default guidance:

- shared stream and session state should have one clear linearization point
- before any `DATA` is placed onto the egress queue, the implementation SHOULD
  reserve both session-global and stream-local send credit
- the writer loop should serialize only already-reserved work; it should not
  independently decide whether the session still has send credit
- implementations SHOULD prefer state-local optimizations such as active lists,
  dirty bits, and shared timers over extra wire-visible work used only for
  local bookkeeping

### 2.1.1 Reserved send-credit release rules

Repository-default send-credit rollback rules are:

- reserved `DATA` bytes still in a withdrawable local state MUST release their
  session and stream send-credit reservations if they are discarded due to
  peer `STOP_SENDING`, local `Reset`, local `CloseWithError(...)`, peer
  `GOAWAY` reclaim, or session shutdown
- the same rollback rule applies to reserved `DATA` discarded while a stream
  is still provisional or while the stream has reached
  `opening-frame-committed`, as long as those bytes are still withdrawable by
  the implementation
- once reserved bytes have entered a non-withdrawable committed state, later
  terminal events MUST NOT roll back the send credit already assigned to those
  bytes
- becoming `transport-submitted` is a common way for bytes to become
  non-withdrawable, but it is not the only possible boundary; the real
  criterion is whether the implementation can still retract those bytes
  without violating its own local serialization guarantees
- reservation release should happen in the same linearized critical section as
  the local state change that made the queued bytes impossible to send
- wake blocked writers only after the state change and any resulting
  reservation release are both visible

### 2.1.2 Terminal and reclaim linearization summary

Repository-default implementations should apply terminal or reclaim work in a
stable order. The exact data structures may differ, but the externally visible
behavior should match this sequence:

| Event | Repository-default local sequence |
| --- | --- |
| peer `RESET` becomes visible | commit receive-side terminal state -> discard unread inbound bytes for that direction -> restore released session receive budget -> coalesce or schedule resulting `MAX_DATA` work -> wake blocked readers and any local waiters that depend on that half |
| peer `ABORT` becomes visible | commit full-stream terminal state -> discard unread inbound bytes -> restore released session receive budget -> cancel or detach queued local work that is no longer sendable and release any still-withdrawable reserved send credit -> wake blocked readers and writers |
| local `CloseRead()` | commit local read-stopped state -> discard unread inbound bytes -> restore released session receive budget -> enqueue `STOP_SENDING(CANCELLED)` if stream-local signalling remains valid -> wake blocked local readers |
| local `Reset(code)` | commit local send terminal state -> discard unsent application bytes for that outbound half -> release any still-withdrawable reserved send credit -> enqueue `RESET(code)` if stream-local signalling remains valid -> wake blocked local writers |
| local `CloseWithError(...)` | commit full local terminal state -> discard unread inbound bytes and unsent outbound bytes -> restore receive budget and release reserved send credit -> enqueue `ABORT(code)` with optional diagnostics if stream-local signalling remains valid -> wake blocked readers, writers, and accept/open waiters tied to that stream |
| peer `GOAWAY` reclaim of a never-peer-visible local stream | commit local failure for that stream object -> discard queued outbound data -> release reserved send credit -> fail local open/write waiters -> do not wait for explicit peer `ABORT(REFUSED_STREAM)` |
| session `CLOSE` or underlying transport failure | commit session terminal state first -> mark all remaining streams failed according to session-close semantics -> discard queued outbound and unread inbound buffers -> restore released receive budget locally and release reserved send credit -> wake all blocked session, stream, open, and accept waiters |

These sequences are intended as implementation-ordering guidance, not as extra
wire-visible sub-protocols.

Repository-default ordering for local stop/reset/abort convergence is:

1. commit the stop or terminal state in shared local stream state
2. discard unread buffered bytes for the affected direction or directions
3. reclaim session or stream receive budget made available by that discard
4. coalesce or schedule any resulting `MAX_DATA` work
5. then wake blocked readers, writers, and acceptors

Repository-default `STOP_SENDING` convergence profile is:

- once `STOP_SENDING` is committed in shared stream state, all subsequent new
  application `Write` calls for that outbound direction fail immediately
- application data not yet framed MUST be discarded and MUST NOT be converted
  into new `DATA`
- bytes already encoded into local session buffers but not yet handed to the
  writer loop MAY be discarded
- if the implementation chooses graceful completion with `DATA|FIN`, it should
  allow only a short bounded local drain window for already unavoidable tail
  bytes
- the sender MUST NOT wait indefinitely for new stream `MAX_DATA` during that
  drain window; if the currently available stream window is insufficient to
  flush the unavoidable tail, repository-default behavior is to stop the drain
  attempt immediately and emit `RESET(CANCELLED)`
- if that bounded drain window expires before graceful completion is emitted,
  repository-default policy switches to `RESET(CANCELLED)` for that outbound
  direction
- repository-default policy prefers `RESET` after `STOP_SENDING` unless the
  remaining unavoidable tail is already small and local graceful completion is
  immediate
- on very slow links, implementations should interpret "small unavoidable
  tail" narrowly and favor switching to `RESET(CANCELLED)` rather than
  spending long serialization time on delayed graceful drain

Repository-default zero-window open handling is:

- if the negotiated initial stream-scoped send allowance for a newly opened
  local stream is `0`, the implementation MUST NOT emit stream-scoped
  `BLOCKED` before that stream has been opened on the wire
- it MUST NOT simply leave the stream permanently provisional while waiting
  for stream-local credit that the peer cannot know to advertise yet
- repository-default behavior is to commit a zero-length opening `DATA` frame
  first. When `OPEN_METADATA` is present, its metadata prefix may still be
  carried on that opening frame because `OPEN_METADATA` bytes are not counted
  against stream or session `MAX_DATA`
- after that zero-length opener has been committed, the implementation MAY
  emit stream-scoped `BLOCKED` if it implements proactive advisory blockage
  signalling; otherwise it should block the caller or return a retryable local
  backpressure result until peer credit arrives
- implementations SHOULD avoid repeated stream-scoped `BLOCKED` chatter while
  the limiting offset remains unchanged; one meaningful advisory update is
  enough until available credit or the limiting offset changes
- implementations should avoid following a pure opener with a burst of tiny
  additional control frames unless those frames carry meaningfully new
  information; this matters most on very slow links

For short-lived request streams, the first batch should contain:

- the session preface if the session is newly established
- the first `DATA`
- optionally `DATA|FIN` when the request body is complete

### 2.2 Batching strategy

For repository-conforming default implementations, the default is
**adaptive short-window batching**.

Its goals are:

- avoid one underlying write per small frame
- let small bursts merge into a larger batch
- keep control-path latency low
- avoid turning interactive traffic into large delayed bursts

Recommended flush triggers:

- the current batch reaches a configured byte threshold
- the current batch reaches a configured frame-count threshold
- a short coalescing timer expires
- an urgent control frame arrives
- the writer observes the queue becoming temporarily empty

Recommended batching behavior:

- when the queue is sparse, flush quickly
- when the queue is hot, allow short additional coalescing to form larger
  writes
- when control frames arrive, either flush the current batch immediately or
  splice the control frame into the next immediate flush
- allow small `MAX_DATA`, `PONG`, and similar frames to piggyback on nearby
  data rather than forcing their own write when the added delay is tiny

Batching should not:

- hold interactive traffic for long fixed delays
- wait for "ideal" packet sizes before flushing
- allow bulk data to indefinitely postpone `RESET`, `ABORT`, `GOAWAY`, or
  `CLOSE`
- assume the underlying transport exposes packet boundaries

`zmux` batching is byte-stream write coalescing. It should improve shape and
syscall efficiency, not create a second message transport.

Large application writes should also be fragmented into multiple `DATA` frames
rather than emitted as one very large monolithic send path operation.

Repository-default batch parameters:

- maximum frames per batch: `32`
- batch cost metric: `sum(len(payload) + 1)` per frame, where the `+1`
  accounts for the minimum frame-header overhead beyond the payload
- when collecting an ordinary-lane batch, advisory-lane work is interleaved:
  advisory reads are attempted both before and after each ordinary read to
  give advisory updates slightly higher same-batch priority without starving
  ordinary data
- if the advisory lane is empty or unavailable, advisory read attempts
  silently fall back to the ordinary data lane

Recommended behavior:

- cap each emitted `DATA` frame at or below the negotiated `max_frame_payload`
- cap each emitted `DATA` fragment to fit the currently available stream and
  session flow-control windows rather than waiting for a fixed fragment size
  or the entire write request to fit
- choose fragment sizes small enough that urgent control work can be
  interleaved between fragments
- let `RESET`, `ABORT`, `STOP_SENDING`, `MAX_DATA`, `BLOCKED`, `GOAWAY`,
  `CLOSE`, and latency-relevant `PING` bypass queued bulk fragments when
  possible
- implementations SHOULD maintain a local `tx_fragment_cap` and emit each
  `DATA` fragment at:
  `min(max_frame_payload, available_stream_credit, available_session_credit,
  tx_fragment_cap)`
- when a local send-rate estimate is available, derive that cap from a target
  serialization-time budget rather than from a fixed large byte count
- implementations with a local send-rate estimate can derive that cap from
  `estimated_send_rate * target_fragment_time`
- indicative target fragment serialization budgets are usually around
  `50-200 ms` for interactive-biased traffic and around `200-500 ms` for more
  conservative very-slow-link operation
- on ordinary links, `tx_fragment_cap` may simply collapse to the negotiated
  `max_frame_payload`; its main purpose is to keep serialization occupancy
  bounded when smaller fragments are needed for timely control interleaving
- when serialization-time budgeting is used, smaller caps are usually more
  effective than more elaborate scheduler logic at keeping urgent control work
  observable on very slow links
- on very slow links, queue and backlog limits should usually be smaller than
  the ordinary repository-default values so that local cancellation, `RESET`,
  and `STOP_SENDING` become visible faster in practice

### 2.3 Control-frame priority

Not all frames should compete equally in the writer queue.

Recommended priority order:

1. `CLOSE`
2. `GOAWAY`
3. `ABORT`
4. `RESET`
5. `STOP_SENDING`
6. `MAX_DATA`
7. `BLOCKED`
8. `PONG`
9. `PING`
10. `DATA`

Repository-default sender scheduling uses a multi-lane writer queue:

- urgent control lane: `CLOSE`, `GOAWAY`, `ABORT`, `RESET`, `STOP_SENDING`,
  `MAX_DATA`, `BLOCKED`, `PONG`, `PING`
- ordinary data lane: `DATA`, stream-scoped `EXT`, non-urgent session-scoped
  `EXT`
- advisory lane: latest-only per-stream `PRIORITY_UPDATE` frames

The advisory lane is logically part of the ordinary data lane for scheduling
purposes but uses a separate input path so that advisory updates can be
coalesced and merged into data-lane batches without competing for urgent
control priority.

Urgent control frames within a collected batch SHOULD be ordered by the
priority ranking listed above (most urgent first). When multiple frames target
different streams, stream-scoped urgent frames within the same priority rank
SHOULD be emitted in ascending stream-ID order for deterministic batch
composition. Repository-default implementations need not rescan urgent work
beyond the current batch window just because a later enqueued frame has a
higher control rank.

Bulk `DATA` should not indefinitely starve control work.

In repository-default implementations, urgent control frames should also be allowed to
bypass ordinary data-queue watermarks and normal batching delays. Bulk-data
backpressure should not trap `CLOSE`, `GOAWAY`, `ABORT`, `RESET`,
`STOP_SENDING`, `MAX_DATA`, `BLOCKED`, or latency-relevant `PING` traffic
behind a full data queue, except for fixed hard memory caps that protect the
process as a whole.

That bypass MUST still preserve per-stream opening dependencies. For a locally
owned stream that has not yet emitted its first opening-eligible frame, a
stream-scoped `MAX_DATA`, stream-scoped `BLOCKED`, or non-opening stream-
scoped `EXT` for that same stream must remain behind the frame that first
opens the stream at the peer.

That bypass also MUST NOT reorder already committed bytes within one stream.
`DATA|FIN`, `RESET`, and `ABORT` may bypass other streams' bulk data, but they
must remain behind any earlier `DATA` already committed to that same stream's
local serialization order.

Recommended send-path backpressure shape:

- one session queued-byte high watermark and low watermark
- one per-stream queued-byte high watermark and low watermark
- urgent control work bypassing ordinary data-queue high-watermark blocking
  where process-wide hard memory caps still allow it
- ordinary writes blocking or failing on deadline or cancellation once the
  relevant high watermark is exceeded

Repository-default capacities:

- `per_stream_data_hwm = max(256 KiB, 16 * negotiated max_frame_payload)`
- `session_data_hwm = max(4 MiB, 4 * per_stream_data_hwm)`
- low watermarks = 50% of the corresponding high watermark
- `urgent_lane_cap = max(64 KiB, 8 * negotiated max_control_payload_bytes)`

Urgent control handling should still be bounded.

Repository-default guidance:

- keep a hard cap for urgent control memory as well as ordinary data memory
- coalesce or deduplicate queued control work where only the newest value
  matters:
  - keep only the newest session-scoped `MAX_DATA`
  - keep only the newest stream-scoped `MAX_DATA` per scope
  - keep only one pending `BLOCKED` per scope and limiting offset
  - keep only the most restrictive pending `GOAWAY`
  - keep at most one locally originated outstanding `PING` per session
  - cap pending per-stream control items and use dirty-bit or
    lazy-materialization schemes when very many streams are active
- repeated terminal signals that do not add new information MAY be dropped once
  equivalent local convergence work is already queued
- queue membership for coalescible control work SHOULD be edge-triggered: a
  scope should become runnable when it first becomes dirty, not be re-enqueued
  once per intermediate superseded value

### 2.4 Stream scheduling

The protocol does not standardize a scheduler, but repository-default
implementations expose one stable default policy.

Supported scheduler policy labels include:

- `unspecified_or_balanced`: the implementation's normal general-purpose policy
- `latency`: bias toward streams with small queued payloads or urgent control
  work
- `balanced_fair`: round-robin, deficit-based, or similarly fair treatment
  across active streams
- `bulk_throughput`: bias toward batching efficiency and throughput for large
  transfers
- `group_fair`: increase the influence of `stream_group` on fairness when
  group hints are available

Repository-default baseline policy is `unspecified_or_balanced` or
`balanced_fair`.

Repository-default queue shape:

- one urgent control lane
- one data lane

Data-lane frames:

- `DATA`
- stream-scoped `EXT`, classified with the same interactive or bulk class as
  the target stream and still subject to opening-dependency rules
- non-urgent session-scoped `EXT`

Repository-default implementations should also keep one per-stream
advisory-control mini-lane for the latest pending `PRIORITY_UPDATE`:

- it belongs logically to the target stream's data-lane class
- it may overtake future not-yet-committed `DATA` for that same stream
- it MUST NOT overtake that stream's first opening frame
- it MUST NOT overtake bytes already committed to that stream's local
  serialization order
- only the newest pending advisory update for that stream needs to be retained

Repository-default data-lane classes:

- interactive
- bulk

Classification inputs:

- session `scheduler_hints`
- `stream_priority`
- per-stream queued-byte depth
- whether the stream has pending short writes or large fragmented writes

Repository-default class hysteresis:

- enter `bulk` when queued bytes exceed two interactive quanta
- return to `interactive` only when queued bytes fall below one interactive
  quantum
- recompute class on empty-to-active transition or batch boundary rather than
  on every byte

Within each class, use retained fair scheduling across active streams.
Weighted deficit round robin (DRR), WFQ, WF2Q+, or another equivalent
virtual-time selector are all acceptable. Quiescent streams SHOULD remain
outside the active competition set until they again have queueable work.

Default values:

- interactive quantum = one negotiated `max_frame_payload`
- bulk quantum = four negotiated `max_frame_payload`
- interactive quantum <= bulk quantum
- active streams retain deficit, virtual-time, or equivalent fairness state
  between rounds

When the session baseline is `group_fair`, the repository-default profile uses
two-level fair scheduling:

- level 1: active groups compete by DRR or an equivalent retained virtual-time
  selector
- level 2: within the selected group, active streams compete by DRR or an
  equivalent retained virtual-time selector
- `stream_group = 0` is treated semantically as "this stream is its own
  default group"
- when all active streams have `stream_group = 0`, implementations MAY
  fast-path to plain per-stream DRR or equivalent per-stream WFQ if resulting
  fairness is equivalent

When a stream changes class, clamp carried deficit into the range
`[0, 2 * new_quantum]`.

Repository-default fairness guards:

- burst limit: 8 consecutive interactive selections
- when both interactive and bulk work exist, reserve at least one bulk service
  opportunity within every four class-level scheduling opportunities
- small-burst bonus: one early quantum for streams with queued bytes less than
  or equal to one interactive quantum
- aging: a stream bypassed for two full scheduler rounds should gain one
  temporary extra quantum on its next opportunity

The small-burst bonus should be granted only once per empty-to-active burst and
re-armed only after the stream has remained idle for at least one flush
interval or has crossed two flush boundaries without queued data.

When `priority_hints` are available, the default interpretation should be:

- higher `stream_priority` shifts a stream toward the low-latency end of the
  scheduler
- lower `stream_priority` shifts a stream toward the throughput-oriented end
  without removing basic fairness
- `stream_priority = 0` follows the session's default scheduler policy

Repository-default bucket guidance for cross-language tuning is:

- `0`: default priority
- `1..3`: mild latency bias
- `4..15`: strong latency bias
- `>= 16`: saturated high-priority range; finer distinctions above this point
  are implementation-defined

If `group_fair` scheduling is implemented, repository-default defenses should
also bound group cardinality and churn:

- cap the number of simultaneously active explicit non-zero groups
- map overflow groups into one fallback local bucket rather than creating
  unbounded scheduler state
- coalesce or rate-limit repeated `stream_group` changes so a peer cannot force
  pathological rebucketing churn

Repository-default group cardinality bounds:

- maximum simultaneously active explicit non-zero groups per batch:
  implementation-defined, but SHOULD be bounded to a small constant (for
  example, `8` or `16`)
- overflow groups beyond the cap are mapped into one fallback local bucket
  rather than creating unbounded scheduler state
- the fallback bucket participates in the same fair scheduling as explicit
  groups
- inactive or empty groups MAY remain outside the scheduler entirely until
  they again have eligible work
- group reassignment through `PRIORITY_UPDATE` takes effect on the next
  scheduling decision; it does not reset stream deficit or alter bytes already
  committed to local serialization order

Session-level `scheduler_hints` define the baseline policy for the whole
session; per-stream `stream_priority` then refines treatment within that
baseline.

Repository-default APIs MAY feed open-time hints into this same scheduling
path before the first batch is committed:

- `initial_priority` may choose the initial latency-vs-throughput bucket for a
  newly opened local stream
- `initial_group` may choose the initial local fairness group for that stream
- `open_info` may attach opaque peer-visible open-time metadata when the
  opening frame uses `OPEN_METADATA`

When the `open_metadata` capability is negotiated and the stream opens with
`DATA` / `DATA|FIN`, repository-default implementations SHOULD carry supported
initial values on the first opening `DATA` frame through `OPEN_METADATA`.

When `open_metadata` is unavailable or the stream does not open with `DATA`,
`initial_priority` and `initial_group` still remain valid local
sender-policy inputs only, unless later `PRIORITY_UPDATE` frames carry
standardized advisory metadata on the wire. Repository-default implementations
MUST reject caller-supplied `open_info` if it cannot be carried on the
opening frame rather than silently dropping it.

If the `priority_update` extension is implemented, later updates should affect
future local scheduling decisions for that stream without changing stream
identity, flow-control accounting, or lifecycle state.

Repository-default scheduler decision order is:

1. serve urgent control lane first, subject to urgent-lane hard caps and
   coalescing rules
2. choose the next data-lane class using class-level fairness guards
3. within that class, choose the next eligible group or stream using DRR or an
   equivalent retained virtual-time selector
4. apply any one-shot small-burst bonus or aging adjustment for the chosen
   stream
5. emit up to one class quantum of frame fragments for that stream while still
   honoring opening dependencies and advisory-control mini-lane ordering rules
6. update class counters, stream deficit, and burst/aging state

`priority_update` changes future local classification and scheduling decisions.
It does not reset stream deficit or alter bytes already committed to local
serialization order.

A suggested implementation of this repository-default profile is a bounded
active-set two-level retained-state fair selector:

- level 1: active groups compete within the current baseline policy using
  retained deficit or virtual-time state
- level 2: active streams compete within the selected group using the same
  kind of retained fair selector
- same-stream FIFO is preserved
- retained scheduler state is kept only for active or recently active groups
  and streams
- when all active streams effectively compete as their own default groups, an
  equivalent flat per-stream fast path is acceptable

This is an implementation technique, not a wire-visible requirement.
Implementations using it MUST still preserve the admission, ordering,
coalescing, and control-latency guarantees described above, and SHOULD NOT
depend on periodic scheduler-driven wire activity merely to make the selector
advance.

Priority and control-latency policy only apply while bytes are still under the
mux sender's control. Once large amounts of `DATA` have already been submitted
to the underlying transport or wrapper, later high-priority work may no longer
preempt them in practice. Repository-default implementations SHOULD therefore
keep underlying not-yet-transmitted backlog bounded and avoid feeding
unbounded bulk data directly into transport-managed buffers. When the platform
offers knobs to limit outstanding transport-submitted bytes or to flush in
smaller batches, implementations SHOULD use them so that mux-level scheduling
remains meaningful.

## 3. Flow control and memory

The protocol uses receiver-advertised absolute offsets.

The implementation should treat receive windows as:

- a correctness guard
- a memory budget
- a fairness input

It should not treat them as a second congestion-control algorithm.

Implementations SHOULD also maintain independent local budgets for non-`DATA`
traffic:

- a control-plane buffered-byte or byte-rate budget
- a control-plane frame-rate budget
- an extension-plane buffered-byte, byte-rate, or frame-rate budget when `EXT`
  support is enabled

Exceeding those local budgets should not be reported as `DATA`-window flow
control. Repository-default handling is to coalesce, defer, shed, or disconnect
abusive peers according to local defensive policy.

### 3.1 Session and stream receive budgets

Recommended model:

- session `MAX_DATA` is the main global memory guardrail
- stream `MAX_DATA` exists primarily for per-stream fairness and to prevent one
  stream from consuming all available receive budget
- when buffered unread data is discarded, the released **session** budget
  should be restored promptly
- after a local read-side stop on one direction, stream-local replenishment
  for that stopped direction should remain suppressed by default

Repository-default admission and memory hierarchy is:

1. session hard memory cap
2. urgent control lane cap
3. application-visible but not yet accepted inbound stream budget
4. hidden control-opened-only state budget
5. provisional local-open budget
6. per-stream buffered-data budget

Repository-default admission and shedding summary:

| Layer | Purpose | Default pressure response |
| --- | --- | --- |
| session hard memory cap | final process-level guardrail | broader session-level failure only when narrower shedding is insufficient |
| urgent control lane cap | bound control-memory growth | coalesce, deduplicate, or shed lower-value control work before exceeding hard cap |
| visible-but-not-yet-accepted inbound budget | protect accept backlog memory | refuse or reap newest lower-value not-yet-accepted visible stream state first |
| hidden control-opened-only budget | bound application-invisible bookkeeping | shed newest hidden control-opened state first |
| provisional local-open budget | bound pre-commit local opens | fail newest still-uncommitted provisional open first |
| per-stream buffered-data budget | keep one stream from consuming all memory | block, fail, or shed according to local per-stream backpressure policy |

Repository-default shedding order is:

1. newest hidden control-opened-only state
2. newest low-value visible-but-not-yet-accepted inbound stream state
3. newest still-uncommitted provisional local open
4. broader session-level failure only when narrower shedding is insufficient

Repository-default receivers MUST continue reading from the underlying byte
stream even when application-facing buffers are full or current `MAX_DATA`
prevents further `DATA` admission. Flow control should block or discard `DATA`
delivery, not stall underlying transport reads. Control frames such as
`MAX_DATA`, `ABORT`, `RESET`, `GOAWAY`, and `CLOSE` must still be able to make
forward progress through parsing and local handling.

### 3.2 Default replenishment style

Repository-default replenishment is **batched and memory-first**:

- do not send a new `MAX_DATA` for every byte consumed
- replenish in larger chunks
- keep `MAX_DATA` steady when memory pressure is high
- only grow the standing window when backlog is low and the implementation is
  intentionally tuning for high-BDP throughput

Repository-default replenishment triggers are:

- consider session-window replenishment when remaining advertised session space
  falls below 25% of the current standing target
- replenish session window immediately when remaining advertised session space
  falls below two negotiated frame payloads
- consider stream-window replenishment for an active stream when remaining
  advertised stream space falls below 25% of that stream's standing target
- do not keep separately replenishing long-idle streams only to preserve large
  standing windows they are not using

Repository-default replenishment threshold calculation:

- quarter threshold: for a target value `v`, the threshold is `v / 4` when
  `v > 4`, otherwise `1`
- session-level emergency threshold: `2 * negotiated max_frame_payload`; when
  remaining advertised session space falls below this value, replenishment is
  immediate regardless of the quarter-threshold check

Replenishment is suppressed for a stream when:

- the stream has entered local read-stopped state (`STOP_SENDING` sent or
  `CloseRead` called)
- the stream's receive half is terminal (`recv_fin` or `recv_reset`)
- the stream is still provisional (no wire-visible ID yet)

Session-level replenishment is never suppressed while the session remains
open, even when individual stream replenishment is suppressed for terminal or
stopped streams.

Repository-default standing targets are:

- `session_window_target = max(initial_max_data, 4 * session_data_hwm)`
- `stream_window_target = max(initial_stream_window, 2 * per_stream_data_hwm)`

These targets are local defaults, not wire-visible promises. Local memory
pressure or unread backlog suppresses growth and may leave the implementation
at pure released-credit replenishment.

Repository-default high-water marks and standing targets are accounting
budgets, not implied eager allocations. Implementations SHOULD charge actual
buffered bytes against these limits and SHOULD NOT preallocate per-stream
memory merely because a stream could legally grow to its configured target.

Deployments on very slow links should generally choose materially smaller
initial windows, smaller queue watermarks, and smaller standing targets than
the ordinary repository-default values, while still keeping replenishment
batched rather than becoming chatty with per-small-release `MAX_DATA`.

If backlog drains and memory pressure falls, larger grants may resume. If
backlog grows or buffered unread data accumulates, growth should slow or stop.

### 3.3 Hidden and not-yet-delivered state

Implementations should bound:

- bytes buffered for not-yet-accepted peer-opened streams
- application-invisible control-opened-only streams
- late tail data accepted after local `CloseRead`
- late tail data accepted or discarded after remote `RESET` or `ABORT`

Repository-default policy:

- hidden unread bytes should use the same discard-and-budget-release path as
  other local discards
- hidden control-opened bookkeeping should remain bounded even when it is
  mostly terminal
- stopped directions should have both a per-direction late tail cap and a
  session-wide aggregate late tail cap
- late tail after remote `RESET` or `ABORT` should also have both a per-stream
  cap and a session-wide aggregate cap

In active single-link `zmux v1`, hidden control-opened-only live state should
normally remain absent. Repository-default implementations should expect hidden
terminal bookkeeping to arise mainly from `ABORT`-first observations on unseen
peer-owned streams, not from `RESET` or ordinary `STOP_SENDING`.

Repository-default tombstone compaction policy:

- once a stream is fully terminal and has no remaining local queued work or
  buffered receive data, convert it to a compact tombstone
- tombstones retain only the stream ID used marker, terminal kind, and a
  late-data handling action policy
- late-data action policies depend on the stream's terminal state:
  - send-only unidirectional streams (no local receive half): ignore late data
    silently
  - streams closed gracefully on the receive side (`recv_fin`): late `DATA`
    triggers `ABORT(STREAM_CLOSED)`
  - streams terminated abortively (`recv_reset`, `recv_aborted`): late `DATA`
    is ignored with discard-and-budget-release
- payload bytes from late `DATA` dropped via tombstone handling MUST still be
  restored to the session receive budget
- tombstones MUST NOT be reaped in a way that permits stream ID reuse
- implementations MAY use the `next_expected_stream_id` cursor plus the active
  stream map to infer dead-stream status for peer-opened streams without
  requiring one heap object per tombstone

### 3.4 Terminal retention and tombstones

Repository-default implementations should distinguish:

- live stream state
- terminal stream state still retaining local queues or API wakeups
- compact tombstone state

A tombstone should retain only the minimum data needed for correct late-frame
handling and local diagnostics:

- stream ID used marker
- terminal kind
- last terminal error code when one exists
- any small local bookkeeping still needed to reject late duplicates cleanly

Repository-default handling is:

- once a stream is fully terminal and no local queued work remains, convert it
  to a compact tombstone rather than retaining full live state
- when dropping late `DATA` for a closed stream via compact tombstone or other
  used-ID bookkeeping, still parse the frame envelope and account for the
  discarded payload bytes against the same discard-and-budget-release path
  used for other local discards
- payload bytes from such dropped late `DATA` MUST still be restored to the
  session receive budget; otherwise the session window will bleed permanently
  over time
- apply the same discard-and-budget-release rule to late in-flight `DATA`
  ignored after remote `RESET` or `ABORT`; those terminal-control late tails
  are still bounded discard paths, not free unmetered garbage paths
- reap tombstones under session shutdown, tombstone budget pressure, or local
  tombstone-age limits
- never reap tombstones in a way that would permit stream ID reuse or loss of
  the "already used" marker semantics for that session

Compact tombstone retention need not imply one heap object per closed stream.
For peer-opened streams, strict no-gap opening plus ordered transport often
lets implementations infer dead-stream status directly from the
`next_expected_stream_id` cursor together with the active-stream map. For
other cases, range-compressed markers, compact bitmaps, or other low-overhead
used-ID bookkeeping are acceptable as long as late-frame handling and no-reuse
semantics remain correct. Repository-default behavior does not require one
heap tombstone object per fully closed stream.

## 4. Ping and liveness

`PING`/`PONG` are protocol tools, not mandatory fixed-period heartbeats.

Repository-default guidance:

- keepalive is optional
- if keepalive is enabled, use idle-only `PING`
- add jitter so independently configured sessions do not repeatedly align on
  the same keepalive deadlines
- avoid fixed per-stream keepalive timers or synchronized periodic sweeps when
  shared timer facilities can provide the same semantics

Repository-default keepalive jitter formula:

- jitter window = `keepalive_interval / 8`
- each keepalive deadline adds a random value uniformly distributed in
  `[0, jitter_window]`
- this prevents thundering-herd synchronization between sessions sharing the
  same interval configuration

Repository-default keepalive deadline reset triggers:

- any successfully parsed inbound frame resets the keepalive deadline
- any successful outbound transport write resets the keepalive deadline
- an active sender therefore never fires idle keepalive probes while it is
  still producing outbound traffic

Repository-default keepalive timeout behavior:

- repository-default runtime bindings that expose a configurable keepalive
  timeout and treat an unset or zero local value as "use the default timeout"
  derive that timeout adaptively from local policy rather than disabling
  enforcement outright
- repository-default adaptive timeout base is `max(2 * keepalive_interval,
  5s)`, capped at `60s`, and widened further when needed to at least
  `4 * observed_ping_rtt + 50ms`
- when the effective keepalive timeout is positive and an outstanding `PING`
  has been waiting longer than that effective timeout, the session SHOULD be
  closed with a keepalive timeout error
- implementations that expose an explicit "disable keepalive timeout"
  capability MAY leave an outstanding `PING` pending indefinitely and simply
  wait one full `keepalive_interval` before re-evaluating

- piggyback small control work when the added delay is tiny
- keep at most one locally originated outstanding protocol `PING` per session
- on very slow links, protocol keepalive should normally stay disabled unless
  the deployment explicitly requires it

Progress and liveness should be interpreted conservatively.

Repository-default liveness semantics are split as:

- `transport_progress`: underlying bearer accepted or drained local bytes
- `mux_control_progress`: control path is alive, as shown by parsed inbound
  frames or `PONG`
- `stream_progress`: one stream direction is making observable byte or
  lifecycle progress
- `application_progress`: the local application is still usefully consuming or
  producing work

`PING`/`PONG` can demonstrate `mux_control_progress`. They do not by themselves
prove useful application progress on any specific stream.

Repository-default guidance:

- strong progress signals:
  - inbound frame parsing
  - `PONG` return
  - transport-exposed writable allowance recovering
- weak local signals:
  - bytes handed to the transport or wrapper
  - outbound write completion
  - queue drain
- weak signals alone MUST NOT be treated as definitive proof that the remote
  path is healthy
- if the wrapper does not document bounded-buffer or durable-accept semantics,
  bytes merely handed to that wrapper should not count as meaningful network
  progress
- weak local signals alone MUST NOT reset a liveness timer or transition a
  stalled session back to healthy

## 5. Diagnostics and local error surfacing

Repository-default local diagnostics include:

- send-queue depth
- flush frequency
- batch size
- blocked write duration
- stream open latency
- `RESET` and `ABORT` reason counters
- receive backlog
- memory-pressure state

These are not wire-visible. They are implementation diagnostics.

## 6. Single-link performance priorities

Once multi-connection coordination is out of scope, the highest-value
implementation work is:

- keeping the dispatcher and writer path short
- reducing descriptor and queue-node allocations
- reusing frame buffers and scratch buffers
- minimizing syscall frequency for small bursts
- keeping high-priority control work on a lightweight fast path
- keeping counters and observability cheap enough for always-on production use

## 7. What to avoid

Repository-default implementations should avoid:

- unbounded urgent-lane growth
- per-byte `MAX_DATA` chatter
- one large application write monopolizing the writer loop
- silently buffering without backpressure
- treating local buffer acceptance as proof of remote delivery
- allowing abusive empty-frame or tiny-control floods to consume unbounded CPU
  or queue work, including:
  - unbounded consecutive zero-length `DATA` frames that do not advance stream
    state materially
  - high-rate `PING` traffic beyond the repository-default one-outstanding
    limit
  - repeated advisory control frames that do not change effective scheduling or
    flow-control state
  - rapid open-then-abort or open-then-reset churn intended to bypass
    concurrent stream limits or exhaust allocators

Repository-default hidden churn detection:

- implementations SHOULD maintain a rolling window counter for
  control-opened-only streams that reach terminal state before becoming
  application-visible
- repository-default threshold: if more than `128` hidden terminal stream
  events (such as `ABORT`-first on previously unseen peer-owned stream IDs)
  occur within any `1-second` rolling window, the session SHOULD be terminated
  with `CLOSE(PROTOCOL)`
- the rolling window resets when uninitialized or when the current window has
  expired
- this defense is intentionally narrow: it targets only hidden streams that
  the application never sees, not legitimate rapid creation and teardown of
  application-visible streams

- if such abusive traffic is detected, disconnecting the peer with
  `CLOSE(PROTOCOL)` or `CLOSE(INTERNAL)` should be part of local defensive
  policy
- exposing application-visible behavior that contradicts
  [API_SEMANTICS.md](./API_SEMANTICS.md) without documenting it

## 8. GOAWAY local reclaim

After peer `GOAWAY` reduces the accepted locally opened stream range for a
stream kind, repository-default implementations should immediately reclaim any
local stream object of that kind whose `stream_id` exceeds the allowed
watermark and is still not peer-visible.

Repository-default handling is:

1. mark that local stream as refused
2. release its provisional-open reservation and queued local resources
3. surface the result locally as equivalent to `ABORT(REFUSED_STREAM)`
4. do not wait for an explicit peer `ABORT` for those never-peer-visible local
   objects

Repository-default graceful drain sequence is:

1. stop admitting new local streams
2. send an initial `GOAWAY`
3. allow a short bounded drain interval when local shutdown policy still
   accepts already in-flight peer opens
4. if needed, send a second more restrictive `GOAWAY` before final shutdown
5. reclaim never-peer-visible local streams above the final accepted
   watermark
6. drain streams that still have remaining local close-relevant work; unread
   inbound-only tails need not delay final close once local send-side work is
   done
7. send `CLOSE` or close the underlying transport

## 9. Implementer checklist

This section is non-normative.

Its purpose is to help implementers build `zmux` in a stable order, starting
from the smallest interoperable core and only then layering optional features.

### 9.1 Recommended implementation order

Phase 1. Session and codec core

Implement:

- preface parsing and emission
- `proto_ver` negotiation
- role resolution
- canonical `varint62`
- unified frame header parsing
- frame-size checks against negotiated limits

Exit criteria:

- valid prefaces decode correctly
- invalid prefaces fail cleanly
- valid frames round-trip through decode and re-encode
- invalid frame headers fail with the expected session error

Primary inputs:

- `fixtures/wire_valid.ndjson`
- `fixtures/wire_invalid.ndjson`
- `fixtures/case_sets.json` -> `codec_valid`, `codec_invalid`, `preface`

Phase 2. Core stream lifecycle

Implement:

- bidirectional stream opening
- unidirectional stream opening
- `DATA`
- `DATA|FIN`
- `RESET`
- `ABORT`
- `STOP_SENDING`
- terminal-stream ignore rules
- stream-ID ownership and no-reuse rules

Exit criteria:

- first opening-eligible frame creates the stream
- `DATA|FIN` yields correct half-close behavior
- `RESET` aborts only the sender's outbound half
- `ABORT` becomes terminal immediately
- `STOP_SENDING` leads to `RESET` or `DATA|FIN` completion on that outbound
  half
- wrong-direction unidirectional behavior is rejected consistently

Primary inputs:

- `fixtures/state_cases.ndjson`
- `fixtures/invalid_cases.ndjson`
- `fixtures/case_sets.json` -> `stream_lifecycle`, `unidirectional`

Phase 3. Flow control

Implement:

- stream-scoped `MAX_DATA`
- session-scoped `MAX_DATA`
- sender-side blocking
- receiver-side over-limit detection
- released-window updates after unread-data discard
- `BLOCKED` frame parsing and receiver-side handling

Exit criteria:

- sender never exceeds current peer `MAX_DATA`
- receiver closes the session on over-limit `DATA`
- released session budget is reflected after discard
- stream-scoped replenishment is suppressed by default after local read-side
  stop on that direction
- `BLOCKED` frame parsing and receiver-side handling are implemented
- proactive `BLOCKED` emission, if implemented, is advisory only

Primary inputs:

- `fixtures/invalid_cases.ndjson`
- `fixtures/case_sets.json` -> `flow_control`

Phase 4. Session lifecycle and liveness

Implement:

- `PING`
- `PONG`
- `GOAWAY`
- `CLOSE`
- session teardown on underlying transport close

Exit criteria:

- `PING` / `PONG` payload echo is exact
- at most one locally originated outstanding protocol `PING` exists per
  session
- repeated `GOAWAY` is monotonic
- `CLOSE` terminates the session and remaining streams

Primary inputs:

- `fixtures/wire_valid.ndjson`
- `fixtures/state_cases.ndjson`
- `fixtures/invalid_cases.ndjson`
- `fixtures/case_sets.json` -> `session_lifecycle`

Phase 5. API contract

Expose a stable host-language surface for:

- bidirectional and unidirectional open operations
- bidirectional and unidirectional accept operations
- ordered `Read` / `Write`
- one full local close helper
- one graceful send-half completion operation
- one read-side stop operation
- one send-side reset operation
- stream ID exposure when the binding chooses to expose numeric IDs
- conn-style deadlines when the host language naturally supports them
- stream-open and accept operations
- error-code surfacing
- explicit whole-stream close-with-error helper with numeric code and optional
  reason text when the binding deliberately chooses to expose it
- fuller protocol-control operations with caller-selected codes and optional
  diagnostics when the binding chooses to expose them

Exit criteria:

- EOF appears only after peer `FIN` and buffer drain
- post-peer-`RESET` reads fail on the affected inbound half while writes may
  remain usable
- post-`ABORT` reads and writes fail as terminal errors
- successful local `Write` is documented as local-send-path success only

Primary references:

- [API_SEMANTICS.md](./API_SEMANTICS.md)
- [STATE_MACHINE.md](./STATE_MACHINE.md)

Phase 6. Optional same-version extensions

Implement only when claiming:

- `open_metadata`
- `priority_update`
- negotiated `priority_hints` / `stream_groups` semantics together with at
  least one standardized carriage path (`open_metadata` or `priority_update`)

Exit criteria:

- unknown or unnegotiated optional behavior is ignored where required
- duplicate singleton TLVs are handled consistently

Primary inputs:

- `fixtures/case_sets.json` -> `open_metadata`
- `fixtures/case_sets.json` -> `priority_update`

### 9.2 Suggested readiness gates

Core single-link gate

Do not claim core interoperability until all of these are true:

- parser/codec cases pass
- state-machine cases pass
- flow-control invalid cases pass
- `GOAWAY` monotonic behavior is correct
- explicit-role and `role = auto` establishment behavior are correct
- EOF and reset API behavior match the repository-default contract

Optional-extension gate

Do not claim `open_metadata` interoperability until:

- the capability bit is negotiated correctly
- `DATA|OPEN_METADATA` is accepted only on the first opening `DATA`
- duplicate singleton hint TLVs are handled according to the repository-
  default policy while still processing the opening `DATA`

Do not claim `priority_update` interoperability until:

- the capability bit is negotiated correctly
- unnegotiated `PRIORITY_UPDATE` is ignored
- duplicate singleton TLVs are handled according to the repository-default
  policy

### 9.3 Suggested implementation order summary

1. parser and codec tests
2. stream lifecycle
3. flow control
4. session lifecycle
5. API contract
6. `open_metadata` when claimed
7. `priority_update` when claimed
