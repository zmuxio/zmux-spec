# zmux Implementation Guide

Status: informative. Nothing here changes the protocol. Where this document
repeats a protocol rule, the normative documents win. The guide records the
architecture the four zmux libraries share, the defaults, and the performance
targets they are measured against.

---

## 1. Toolchains

| Language | Floor | Notes |
|---|---|---|
| Go | 1.27 (`go 1.27`) | pure Go; generic methods, `crypto/hkdf`, `testing/synctest` (virtual time for trace runners), `sync.WaitGroup.Go` |
| Rust | 1.95, edition 2024 (`rust-version = "1.95"`) | sans-IO core crate plus tokio and std drivers |
| Java | 25 (LTS) | pure Java; `ExtendedSSLSession.exportKeyingMaterialData` for the TLS exporter, `javax.crypto.KDF` for HKDF (JEP 510); records, sealed interfaces and pattern `switch` for frames; FFM `MemorySegment` for off-heap buffers; the blocking API is meant for virtual threads |
| Python | 3.12 | PyO3 binding of the Rust core; wheels: abi3-py312, cp314t (free-threaded), abi3t (PEP 803) for 3.15+ |

---

## 2. Core architecture

### 2.1 Sans-IO core

Every implementation is a state machine without I/O:

```text
inputs : receive(pipe_id, bytes, now)     bytes read from an inbound pipe
         pipe_event(pipe_id, event, now)  attached, writable, ended, failed
         command(op, now)                 API calls: open, write, finish, reset, stop, close, add_pipe, ...
         handle_timeout(now)
outputs: poll_transmit(pipe_id, buf, max, now) -> bytes for that outbound pipe (late binding)
         poll_event() -> stream, datagram, pipe and session events
         next_timeout() -> the earliest deadline
         wants_write(pipe_id) -> whether the driver should wait for writability
```

The single-pipe case is `pipe_id = 0` in both directions; there is no second
core for it. The trace runner of TRACES.md
drives exactly this interface.

Timers that the specification starts when something is written start at the
`now` of the `poll_transmit` that produced those bytes, not at the command
that caused them: the linger timeout starts when the CONNECTION_CLOSE of
every outbound pipe has been produced (SPEC.md Section 6.5 step 3), and the
send time of a byte for RTT samples is the start of the write call that
carries it (SCHEDULER.md Section 2). A command such as `close` only queues
the frames; a core that started the linger timeout at the command would cut
the lingering short whenever the carrier was not writable at once. A core
that cannot avoid arming the linger deadline from a stale `now` (the command's
clock reading) restarts it once, from the `now` of the `poll_transmit` that
hands the last CONNECTION_CLOSE to its carrier (SPEC.md Section 6.5); both the
Go and the Rust cores needed this fallback.

### 2.2 Linearisation

One lock protects the core; no I/O happens under it (the quinn `Mutex<Core>`
model). A cross-thread hand-off costs 115-440 ns in Go and 3-6 us between OS
threads, a lock 5-77 ns. The lock is the
only linearisation point of a session, so frames are never re-ordered after
the core produced them. Stream identifiers are allocated inside the core when
the first frame of the stream is generated (SPEC.md Section 7.2). Java
confines the core to its session's event-loop thread instead of locking it
(Section 2.5.8); that thread is then the one linearisation point, and the
rules below that speak of the core lock apply to a core call on it.

### 2.3 Drivers

| Language | Driver |
|---|---|
| Rust | one tokio task per session `select!`s over pipe readability and writability, timers and wake-ups; a std driver uses one thread per pipe direction. Futures never block. |
| Go | per pipe direction one goroutine: readers call `receive`, writers wait for writability (RawConn write callbacks on Linux so that `TCP_NOTSENT_LOWAT` works) and call `poll_transmit`. A single-pipe session has at most 2 goroutines. |
| Java | an NIO event loop registers `OP_READ` and `OP_WRITE` per pipe; few threads serve many sessions; no thread per session. The loop thread is the only one that touches a session's core; application threads reach it through the session's mailbox and the per-stream lanes (Section 2.5.8). The blocking API wraps the same operations and is intended for virtual threads (no pinning on `synchronized` since JDK 24). |
| Python | the Rust core; the asyncio driver advances it on the event-loop thread with one wake-up descriptor per loop and batched event delivery; the sync API releases the GIL while waiting, checks signals periodically, and refuses use after `fork`. |

**Reads never wait behind writes.** A pipe's carrier is read and written by
independent execution contexts (a reader and a writer goroutine, task or
thread), or by one context that writes only with non-blocking,
writability-driven write calls, bounded by Section 4 while latency-sensitive work waits,
and polls for input between them; an adapter whose writes block (API.md
Section 4.1) always gets a separate reader, since a blocking write is
bounded in bytes but not in time, and two endpoints blocked in writes to
full carriers would otherwise never read again. A write in
progress MUST NOT delay reading the same session by more than one write
call, which Section 4 bounds as soon as input or other latency-sensitive
work is waiting (a single context checks for input before each call, so
only the first input after a bulk-only phase can wait for a whole batch);
without the bound the peer's requests, PIPE_ACKs and credit would wait
behind every write batch, and on a loopback TCP carrier a large gather
write runs the receiving stack inside the call. The application fast paths, where a
language has them, are bounded the same way: an application thread that
writes through to a carrier holds the sending side for at most one write
call, and one that reads through holds the receiving side for at most one
read buffer (Section 3), before it hands the side back to the driver. The
Go driver's separate reader and writer goroutines measure a p99 of 199 us
under bulk (Section 9.2); a single tokio task that reads each pipe once and
then writes the rest of a 256 KiB batch in one call, with write-through
finishing whole batches, measured 350 to 600 us.

**Establishment round trips.** Without early data, a client's first stream
byte waits for the server's transport parameters (SPEC.md Section 5.1 rule
3): one round trip on plain TCP and WebSocket, none on TLS 1.3 when the
server sends its parameters with its Finished flight as SPEC.md Section 6.1
recommends, which rustls allows (half-RTT data) and the Go `crypto/tls` and
JDK `SSLEngine` servers do not (one round trip). With early data toward a
peer the application declares to be zmux (SPEC.md Section 6.1.2), the
client writes up to 4 KiB on each of four streams (16 KiB in total) right after its
parameters, so its first stream byte reaches the server with its first
record: 1.5 round trips after the start of a plain TCP connection instead
of 2.5, and 2.5 instead of 3.5 over TLS 1.3 with a Go or JDK server. The
rules that make this safe without remembered parameters: the floor is a
small protocol constant that every zmux endpoint advertises at least, so
nothing needs rejecting (a plain TCP carrier could not reject it); it never
travels as TLS or QUIC early data, so it is never replayed; an `auto` role
sends none; and the PIPE_ACK fence makes a wrongly declared plain QMux peer
fail before it processes any of it. On a resumed TLS 1.3 carrier with early
data, QMux-02's own 0-RTT under remembered parameters saves one more round
trip where the application accepts replay (`early_data.replayable`). An
implementation keeps the early streams of a session as ordinary streams
that are opened before establishment completes; only their credit is the
assumed set until the parameters arrive, and their content is kept until
the early data is known to stand. 

**Carrier early data and replay.** SPEC.md Section 6.1.2 lets a server take
streams from TLS or QUIC early data only when the application allows it,
reports them replayable, and asks for the TLS stack's RFC 8446 Section 8
anti-replay measures. What each implementation's servers do:

| Implementation | Server carrier | Takes carrier early data | Anti-replay from the TLS stack |
|---|---|---|---|
| Go | TLS over TCP | never (`crypto/tls` servers do not accept early data) | - |
| Go | QUIC (`zmuxquic.Serve` on a quic-go `EarlyListener`) | when the quic-go configuration allows 0-RTT and `EarlyData.Replayable` is set | none: session tickets are stateless, so early data can be replayed while a ticket is valid; only the replayable marking and `Stream.Reject` protect the application |
| Go | QUIC when the listener has an admission (`zmuxquic.Serve`, `zmuxquic.ServeConn`) | never: a connection that used 0-RTT is closed | - |
| Rust | TLS over TCP (`zmux-rustls`) | never: the adapter does not use TLS early data as carrier early data, and an admitted server forces `max_early_data_size` 0 | - |
| Rust | QUIC (`zmux-quinn` with `Options::with_zero_rtt`) | when `carrier_early_data` is set | rustls takes early data only with stateful resumption: each ticket is used once per server's session store, and its age is checked; servers sharing a name do not share that record unless they share the store |
| Rust | QUIC without 0-RTT, or when the listener has an admission | never (`max_early_data_size` 0) | - |
| Rust | HTTP/3, WebTransport | never: requests are read after the handshake completes | - |
| Java | TLS over TCP (JDK `SSLEngine`) | never (the JDK does not implement early data) | - |
| Java | QUIC (`zmux-quic`) | never (the server's TLS context is built with early data off) | - |
| Python | the Rust adapters it binds | as Rust: `serve_quic(zero_rtt=True)` is `zmux-quinn` with 0-RTT (and without an admission); its other servers never | as Rust |

Servers with an admission refuse carrier early data.

### 2.4 Wake-ups

Readers and writers are woken only when a buffer goes from empty to non-empty
or crosses a watermark, at most once per waiter per batch, and after the
core lock is released (Section 2.5.3; a condition variable bound to that lock
is the one exception).

### 2.5 Concurrency safety

Deadlocks, lost wake-ups, races and crashes are avoided by construction, not
found by testing alone; CONFORMANCE.md Section 4.7 lists the tests that
check these rules, and Section 13.1 the mistakes made against them.

#### 2.5.1 Lock levels

Locks are acquired in this order only; a lock of a lower level is never
acquired, blocking, while one of a higher level is held (only `try_lock`
is allowed in that direction):

| Level | Lock | Held across |
|---|---|---|
| L0 | per-handle operation serialisation (one reader or one writer of a stream at a time), where an API needs it | a blocking wait, which honours the operation's deadline and cancellation |
| L1 | a carrier half or the write token of a driver | the carrier I/O of that half |
| L2 | the session (core) lock: exactly one per session | nothing but core calls |
| L3 | leaf locks (buffer pools, listener counters, waker slots) | nothing; no lock is taken and no outside code runs under them |

Java has no L1 or L2 lock: a session's core and its carriers are owned by
the session's event-loop thread, which runs one session's work at a time
and never waits for another loop. L0 is a FIFO turn per stream direction,
taken by application threads and the completion context, never by the loop
thread; L3 remains for listener, pool and nesting bookkeeping.

A thread never holds the core locks of two sessions. A listener's
bookkeeping is a leaf lock, or is updated through atomics and bounded
queues, so that the listener never waits for a session's lock while
holding its own. The process's record of which session runs over which
(nested sessions, EXTENSIONS.md Section 10.2) is a leaf lock too, taken
alone (no other leaf lock, the listener's included, is held while it is
taken): the loop check of MULTIPATH.md Section 5.4 step 7 and `AddPipe`
(API.md Section 4.6) and the insertion of the new "runs over" edge happen
under it as one step, so that two joins that would close a loop together
cannot both pass. The edge is removed under the same lock when that join
is refused later (the cap of step 9, resource exhaustion), when the
attachment is retired, and when either session ends, so that a stale edge
never refuses a later nesting that forms no loop.

#### 2.5.2 What never happens under the core lock

Carrier or TLS I/O, logging, DNS, a blocking wait or a call that can block,
allocation of large or off-heap buffers, and application code, without
exception and in every language: no application-supplied function (hook,
callback, custom policy, predicate) is ever called while a zmux lock is
held, so no application code can re-enter a session's lock, whatever handles
it has captured. The two hooks whose answers the core needs are run like
this:

- **Custom scheduling policy** (API.md Section 5.2). A decision is two
  steps of the core, not a lock released in the middle of a call: when
  stream directions need a custom choice, `poll_transmit` returns "decisions
  needed" with a value-only view copied into a reusable buffer (one request
  per waiting stream direction: its kind, urgency, ready bytes and last
  pipe; per pipe its ID and plain numbers and flags, Section 8.3; no
  handle, no callback, nothing that leads back to the session); the driver
  calls `Pick` with the view outside every lock and then calls the core
  again with the answers. Pipe answers are **sticky**: each names a pipe
  for one stream direction and a byte allowance (0: until invalidated), and
  the core keeps using it for that direction's chunks without asking again
  until the allowance is used or the answer is invalidated. A "wait"
  answer is not sticky: it holds only until the core's next input (a
  record, a timer, an application write or a pipe change), after which the
  direction is in the next request again; since metric and writability
  changes do not invalidate answers, a sticky wait could otherwise stall a
  stream direction indefinitely. Requests are batched and not limited to one outstanding: a
  stream direction that needs a decision while another call is under way
  joins the next call. An answer is applied if the view's generation is
  still current; otherwise, and for an invalid answer, a panic or an
  exception, the chunk takes the `affinity` choice and the miss is counted,
  with no retry, so a busy session cannot livelock on its policy. The
  generation advances only when answers can no longer be applied as given:
  the session's pipe set changed, a pipe's state or eligibility changed, or
  a pipe stopped being able to carry data (closed, shut down, failed or
  penalized), never because a carrier's write window filled for a moment.
  Changes of metrics (SRTT, rates, bytes in flight or unsent) do not advance
  it either: a view a few microseconds old is still a valid basis, and
  answers are therefore normally applied (CONFORMANCE.md Section 4.7 item 5
  requires at least 99 % under load). With sticky answers the policy runs
  about once per binding or per allowance rather than once per 16 KiB
  chunk, so its cost stays negligible at 10 Gbit/s (about 76 000 chunks per
  second): a policy that spreads returns an allowance of a few
  milliseconds of the pipe's rate. Java, Rust and the Python binding call
  `Pick` on the driver thread or task, where a blocking zmux call fails at
  once (Section 2.5.3) and other zmux calls only queue their work, never
  drive I/O in place; the cost is one extra core call and the copy of a few
  dozen bytes per pipe per call. Go calls `Pick` on one policy goroutine per
  session, started when a custom policy is set: a writer whose stream
  direction has no current answer waits for one at most 1 ms and takes the
  `affinity` choice after that, so a policy that blocks stalls nothing but
  itself; the cost is one goroutine hand-off per call, shared by all the
  requests it carries.
- **`allow_connection_window_increase`** (API.md Section 6). The core keeps
  at most one proposal outstanding: the increase it wants (its target minus
  the current session window). While one is outstanding, further growth
  only raises the wanted target and does not replace the proposal. The
  hook runs on the session's hook context (Section 2.5.7), never on a
  driver thread, fed through a one-slot mailbox of its own, never through
  the tracer queue, so a proposal is never dropped and a slow tracer does
  not delay it. When the answer arrives the core applies it under the lock
  unless the session has closed: an approval raises the session window by up
  to the approved delta, within the caps, the memory allowance (Section 6.2)
  and SPEC.md Section 8.3 rule 4; a refusal leaves the window unchanged until
  the next growth decision; then, if the target has moved on, the core
  proposes the rest. A panic or exception in the predicate counts as a
  refusal and is counted. Until an approval the session window does not
  grow, which costs at most the hook's latency before more credit is
  offered.

No hook receives a session or stream handle, and none needs one: the policy
and the predicate decide from the values they are given, `redial` returns a
carrier, the tracer receives events, and `OnMemoryPressure` acts through the
listener and the sessions the application already holds, which it may call
freely because it runs outside every lock. The work done under the
lock per call is bounded by a
constant or the batch size: large application buffers are taken in pieces,
and no peer input may cost more than linear work in its size.

#### 2.5.3 Wake-ups and callbacks after unlock

While holding the lock, collect what must be signalled: waiters to wake,
readiness notifications, completions, events, timer changes, the driver's
need to write. Release the lock, then signal. Completions and wake-ups that
can run outside code (a `CompletableFuture` completion, a Rust `Waker`, a
Python callback, a tracer, an event listener) MUST run after the unlock;
signals that run no outside code (Go channel close or non-blocking send,
`LockSupport.unpark`) SHOULD, so that the woken thread does not queue on
the lock at once. A condition variable bound to the same lock is signalled
while holding it, as its API requires; this is the one signal given under
the lock, and it runs no outside code. Java has no session lock to release:
the loop collects the wake-ups of a round and, after the core calls of that
round have returned, unparks blocked callers and posts completions to the
session's completion context.

Completions, callbacks and hooks run with no zmux lock of any level held
(L0 to L3), the write token of a driver included. A blocking API call made
on a thread or task that drives sessions (an event-loop thread, a driver
thread or goroutine, a task of the async runtime that runs the driver),
including one made from a completion delivered there, fails at once with a
documented error instead of blocking: Java checks whether the caller is a
loop thread, the Python binding whether an event loop runs on the calling
thread, Rust whether it is called inside the runtime; Go runs no
application code on its driver goroutines (hooks get goroutines of their
own), so the case does not arise there. An application that
wants to block in callbacks has them delivered on an executor that drives
no session.

#### 2.5.4 Every waiter is woken

Each kind of wait (read, write, open, accept of each stream type, session
and stream datagrams, datagram queue space, PING, pipe events, session
established, closed, shutdown completion, listener accept, room for a
join under the own cap of unretired attachments or the end of the refusal
backoff, join confirmation, pipe handover)
is woken when its condition holds and also when its own handle is
cancelled, stopped, reset, closed or rejected, when the local endpoint shuts
down or receives GOAWAY, when the session ends, and when its deadline
changes. Concurrent waits on one handle are excluded by the API's types
(Rust `&mut self`), serialised (L0) or broadcast; they never share one
wake-up slot or one deadline timer. L0 serialises waits of one kind only:
close, reset, stop, cancellation and deadline changes never take it, so
that they can interrupt a waiter that holds it (as `SetDeadline` interrupts
a `net.Conn` read).

#### 2.5.5 Cancellation, close and timeouts

- An operation either takes effect and reports its result, or takes no
  effect: a cancellation or deadline that races with completion resolves to
  exactly one of the two under the lock (Java: by one compare-and-set on
  the operation's state), so data is never lost or
  duplicated. What a cancelled waiter had been granted (a stream, a
  datagram, credit, an open slot) passes to the next waiter.
- Close: under the lock mark the session closing and take every waiter,
  timer and pending event off its list; unlock; wake every waiter with the
  terminal error; then close or abort the carriers outside the lock. Events
  already queued, the terminal ones and the summary of Section 2.5.7
  included, are still delivered, followed by the session's end (`Closed`
  last for the tracer); the event stream then ends with the session's
  error. Close
  is idempotent and may race with any call; later calls fail with the
  session's error.
- Timers carry a generation, so a timer that fires after its owner changed
  or ended does nothing; they are driven by a thread or task that never
  blocks on carrier I/O, so that deadlines (handshake, idle, linger,
  resume) fire while a carrier read or write is stuck.
- Termination is bounded: after the session is CLOSED every carrier half is
  closed or aborted within the linger time (or a fixed bound when there is
  no linger) even while a write is blocked, and every driver thread, task
  or goroutine ends.

#### 2.5.6 Failures inside the endpoint

A panic or unchecked exception while the core lock is held, or in a driver,
closes the session with INTERNAL_ERROR and wakes every waiter; the lock is
released on every path (Java, which has no session lock: a session's
mailbox commands, round, timer and I/O handlers run inside a guard that
catches the exception, fails the session and keeps the loop running for its
other sessions, and the session's timer is re-armed, or cleared when the
session ends, on that path too; Rust: a poisoned lock ends the session instead of being
ignored, and the session task carries a guard that fails the session when
it unwinds). Peer input never gets there: decoding is bounds-checked,
arithmetic on peer values is checked or saturating, nothing recurses on
peer data, and allocation is bounded by credit or a fixed constant
(SPEC.md Section 9.8).

#### 2.5.7 Queues and hooks

Every queue is bounded and pushes back on its producer: send buffers
(`send_buffer`, Section 6.4), receive buffers (the advertised windows),
accept queues (stream credit), datagram queues (configured sizes, dropping
the oldest), pending control (coalesced state, SPEC.md Section 9.2), waiter
lists (cancelled waiters removed at once), listener queues (MULTIPATH.md
Section 5.6, RESUME.md Section 9). Events are coalesced per pipe (the
latest state wins) plus at most one terminal event per pipe, and only pipes
that are not retired hold event slots: when a pipe is retired with events
unread, they are folded into one session-level summary, `PipesRetired`
(API.md Section 4.5), the only report of retirement.
The event queue therefore holds at most max_pipes + H pipes per joiner plus
one summary, however many pipes a session goes through (rotation, redials,
a peer that joins and drops pipes in a loop), and no terminal outcome is
lost (Section 11); an overflow of non-terminal events is reported as a
count. Hooks run outside
every lock: `tracer` serially per session and in order (`Closed` last),
fed through a bounded queue so that the session never waits for it (on
overflow, events other than `Closed` are dropped and the count is reported
with the next event); `OnMemoryPressure` on its own serial context, called
when pressure starts and when it ends (API.md Section 4.4), never on an I/O
thread, and it may close sessions; `redial` at most once in flight per direction, cancelled when the
session ends, and a carrier it returns after the end is closed by the
library. A carrier's `Close` interrupts a read or write blocked on it, and
carrier `Drop`/close runs outside the core lock. A custom policy and
`allow_connection_window_increase` run outside every lock as Section 2.5.2
describes, the latter on the session's serial hook context through its own
one-slot mailbox; hook goroutines, tasks or contexts are started when the
first hook that needs one is set, so an idle session without hooks keeps the
footprint of Section 9.2; the Python binding does not offer the predicate, since the
listener's memory budget (API.md Section 4.4) covers the same need.

#### 2.5.8 Per language

- **Go.** One `sync.Mutex` per session; an `unlock()` epilogue swaps out the
  callbacks and wake-ups and runs them after `Unlock`; single waiters wake on
  a 1-buffered channel, broadcasts by closing a channel that is then
  replaced (the `pipeDeadline` pattern of `net`), never `sync.Cond` (it
  cannot be combined with a context); per-waiter deadline channels;
  `context.Context` honoured by every blocking call, including connection
  setup. `go test -race` in CI and in the release checklist,
  `testing/synctest` with in-memory carriers, goroutine-leak checks,
  `nettest.TestConn` for the `net.Conn` adapter.
- **Rust.** `std::sync::Mutex` (or `parking_lot`) never held across
  `.await`; a guard collects `Waker`s and wakes them after dropping the
  lock; `AtomicWaker` or `Notify` for single-waiter slots; cancellation
  safety documented per method, state changing only on `Poll::Ready`,
  futures deregistering their wakers on drop. `loom` models of the
  wake-up, write-token and close protocols, `shuttle` for the std
  driver (it cannot host a tokio runtime), seeded `current_thread`
  `start_paused` tokio tests (or `turmoil`) with in-memory duplex carriers
  for the tokio driver,
  `cargo fuzz` for the decoder and the core, Miri for any `unsafe`.
- **Java.** No session lock: the session's event-loop thread is the single
  owner of its core and carriers. Other threads reach it through one MPSC
  mailbox per session, handed to the loop only when it goes from idle to
  busy (a burst of commands costs one hand-off and at most one selector
  wake-up), and through a send and a receive lane per stream direction,
  each used by one FIFO turn holder at a time: the core grants a write
  lease of the stream's send-buffer share, within which the writer copies
  into lane-owned blocks on a single-producer ring without involving the
  loop, and the loop moves them into the send queue without copying; the
  loop pushes in-order received data as views, and the reader copies it
  out and reports consumption at least every window-update threshold and
  before it waits. Every call is one operation object shared by the
  blocking and the asynchronous API; completion, cancellation, timeout and
  the session's end are decided by one compare-and-set on its state, and a
  cancellation that meets a running attempt either hands the attempt's
  value to a discard hook or lets that one non-blocking attempt finish.
  Futures complete on the session's serial completion context, never on
  the loop thread; the interrupt flag is preserved; failures are handled
  as Section 2.5.6 describes; an event loop that shuts down closes its
  sessions and rejects new tasks. jcstress for the small protocols,
  Lincheck for the stream and session facade, BlockHound for blocking on
  the loop thread, Jazzer for fuzzing.
- **Python (PyO3).** No Python code runs, no Python object is created or
  released, and the interpreter is not attached while the Rust core lock is
  held (data is copied out under the lock and turned into Python objects
  after it); a Python thread detaches from the interpreter before taking a
  contended core lock and while waiting (checking signals at least every
  100 ms); asyncio calls the core on the loop thread only; `#[pyclass(frozen)]`
  types with internal locking for the free-threaded build; use after `fork`
  is refused.

### 2.6 Multipath as a layer: zero cost for single-pipe sessions

Multipath is a first-class feature negotiated at runtime (MULTIPATH.md
Section 3), not a compile-time variant: every library build contains it, and
no language has a feature flag, build tag or separate module that removes
it. Compile-time variants would double the build and test matrix, invite bugs
that only appear in some feature combinations, and complicate packaging (Go
modules, Java artifacts, the Python wheel); their only extra gain over the
runtime guarantee below is binary size. Instead the core is organised so
that a session that never negotiates multipath or resume pays nothing for
them.

**Internal interface.** The stream layer (SPEC.md, EXTENSIONS.md: streams,
flow control, stream scheduling) and the path layer (MULTIPATH.md,
SCHEDULER.md, RESUME.md: pipes, acknowledgement, retention, reinjection,
estimators, policies) meet at one narrow internal interface. It is an
internal module boundary, not a public API, and corresponds to the boundary
between QUIC's stream layer and its loss-recovery and path management:

| # | Direction | Call | Single-pipe implementation |
|---|---|---|---|
| 1 | stream to path | `on_sent(pipe, frame)`: a frame left on a pipe; `frame` is (kind, stream, byte range or current value) | nothing |
| 2 | stream to path | `retain_stream_bytes() -> bool`: keep sent stream bytes until acknowledged | false: bytes are released once written |
| 3 | stream to path | `send_budget() -> bytes`: how much written data may be outstanding | the session send buffer |
| 4 | path to stream | `acked(frame)`: the frame's information is acknowledged (SPEC.md Section 7.4) | called when the frame is written |
| 5 | path to stream | `lost(frame)`: mark the frame's scope for sending again, its range or its current value (MULTIPATH.md Section 7.4) | never called |
| 6 | scheduler to stream | `ready_by_urgency() -> [(urgency, bytes)]`: data ready within credit, per urgency level (the ECF backlog and the tier offered data) | not used |
| 7 | scheduler to stream | `oldest_wait() -> time`: when the oldest ready data started waiting (the tier age) | not used |
| 8 | scheduler to stream | `next_chunk(max, filter) -> (stream, range)`: the next chunk in priority order, optionally only streams bound to a given pipe | the stream scheduler of Section 5 |
| 9 | session | `pipes_changed`, `suspended`, `resumed`, `activity` (idle timer) | `activity` only |

The stream layer never reads path-layer state and the path layer never
touches stream state except through these calls; in particular a lost frame
is turned into "send this scope again" by `lost`, not by the path layer
setting stream flags.

**Lazy state.** All multipath state lives in side storage that the session
allocates only once multipath or resume is in use on it (both parameters
exchanged, MULTIPATH.md Section 3.3, RESUME.md Section 3): per session the
pipe table, acknowledgement and retention state, estimators, tiers,
probation and policy state; per pipe the estimator, probation and tier
fields; per stream the retained ranges, acknowledgement state and pipe
binding. A single-pipe session holds a reference that is empty, and pipe 0
and every stream carry only their single-pipe fields.

**Pipe acknowledgement.** A single-pipe session with pipe acknowledgement
(MULTIPATH.md Section 3.5) keeps a small fixed record on pipe 0, never per
stream and never data: the send and receive positions, the ack-eliciting
bytes received and the time of the first of them, the delivery-rate and RTT
estimators of SCHEDULER.md Section 2 with one sample entry per write batch
in flight (send position, send time, delivered count and time), and, only
on a carrier that reports neither unsent bytes nor a low-water mark, the
limiter state of SCHEDULER.md Section 9.1. Building a batch adds the
PIPE_ACK when one is due, and processing a PIPE_ACK pops the covered sample
entries; nothing is retained, so `acked` and `lost` of the interface above
stay as for a single pipe.

**Single-pipe fast paths.** Every hot path that the path layer could
influence first tests "multipath not in use" with one branch and returns or
takes the single-pipe route: writability and write-call notifications from
the driver (Rust `set_pipe_writable` returns at once), the commit of a frame
to a pipe, the release of written stream bytes, the receipt of a record
(no position counting beyond pipe acknowledgement's counters), timer
computation (no probe or failure deadlines; with pipe acknowledgement only
the acknowledgement delay) and statistics. No such path scans the streams or pipes
for multipath work when multipath is not in use.

**Guarantee.** A single-pipe session carries no per-pipe or per-stream
multipath bytes beyond the fixed pipe acknowledgement record above, and its
throughput, latency and allocations match a build of the same library
without multipath code within noise (the comparison build keeps pipe
acknowledgement, which is not multipath). CONFORMANCE.md
Section 4.6 makes this a measured release requirement; the comparison build
replaces the path layer by its single-pipe implementation of the interface
above and exists only in the benchmark harness, never in a release.

Per language: in Rust the side storage is an `Option<Box<..>>` per session,
per pipe and per stream (or a table keyed by stream in the session's side
storage); in Go the path layer is the package `internal/mp` behind an
interface held by the core, with a small single-pipe implementation, and
the per-pipe and per-stream state lives in tables inside that package; in
Java the path layer is a sealed interface in the core's package, held by
the session and reached by the driver only for scheduler picks; its single-pipe implementation is a small
object per session that delegates back to the session's single-pipe
handling, and the multipath implementation, with the per-pipe and
per-stream state, is instantiated only when multipath is negotiated; the Python
binding inherits the Rust core's behaviour.

### 2.7 Keeping the single-pipe core small

Two structural rules keep the single-pipe code close to what its features
need (the Rust single-pipe core measured about 2.9 times the size of a
minimal QMux implementation, most of it features the minimal one lacks):

- **Registry tables belong to conformance tooling.** The complete frame-type
  and transport-parameter tables of REGISTRY.md (names, codepoints,
  policies) are what the conformance tests and trace runner check against;
  they live with the tests or are generated for them from
  `assets/registry.json`. The library keeps only what it decodes and
  encodes: a match on the codepoints it implements.
- **One handle layer for all drivers.** Where a library has more than one
  driver or API style (Rust tokio and std, Java blocking and asynchronous),
  the public handle methods (lock the core, call it, register a waiter or
  return; in Java, submit an operation to the session's loop) are written
  once over a small waiting abstraction (a `Waker`, a condition variable,
  or the Java operation that a blocking caller parks on) that each driver
  supplies; the drivers keep only their own I/O loops. The
  cancellation semantics of each driver (dropped futures, timeouts) stay as
  Section 2.5.5 describes, and the concurrency tests of CONFORMANCE.md
  Section 4.7 run against every driver.

### 2.8 Many sessions per process

Overlays, relays and gateways hold thousands of sessions, most of them
quiet most of the time. A quiet session must cost memory proportional to
its state and CPU proportional to the protocol events it really has, never
a per-session poll or a per-session periodic wake-up. The targets are in
Section 9.2 (idle session, quiet sessions); the rules:

- **One deadline per session.** The core exposes one `next_timeout()`
  (Section 2.1); the driver keeps exactly one entry per session in a timer
  structure shared by all sessions of the runtime: the runtime's own timer
  wheel (tokio, the Go runtime timers, Netty's `HashedWheelTimer` per event
  loop) or a hierarchical wheel of the library's own. Timers are not
  re-armed on every record: the core records the time of the event (the
  last activity, the last acknowledgement) and, when the armed deadline
  fires, computes the real one and re-arms only if it moved. A session with
  pending acknowledgements, probes and an idle timer still has one entry.
- **Only real timers run.** A single-pipe session without keep-alive has no
  periodic work at all: no probe, no acknowledgement timer while nothing
  arrives, no idle-timer tick (the idle timer is one deadline). A quiet
  multipath session wakes only for the probes of MULTIPATH.md Section 7.1:
  about one exchange per pipe pair per probe interval (15 s, or
  `probe_interval`, shortened on average by the probe jitter), the answer
  carrying the other end's PIPE_PING (MULTIPATH.md Section 6.5), standby
  and less-preferred pipes included while the session is quiet.
  Keep-alive, on by default, adds one QX_PING per period (and in a
  multipath session the PIPE_ACK it elicits); it does not make the session
  busy, so it does not bring back the faster standby probing.
- **Coalesce deadlines.** The probe waits of MULTIPATH.md Section 7.1 are
  drawn at random (the first one of a bootstrap pipe from the whole probe
  interval), which keeps sessions established together from probing in
  step; a driver MAY fire deadlines that fall within a few milliseconds of
  each other together, but never later than the specification allows.
- **No idle buffers.** The read buffer of a pipe (Section 3) is taken from
  a pool shared by all sessions when the carrier becomes readable and
  returned once the buffer holds no partial record; an idle pipe then holds
  no buffer at all. Drivers that block in a read call (Go readers, std
  threads) return the pooled buffer once it has stayed empty for a while
  (for example 1 s) and then either wait for readability without a buffer
  (Go `syscall.RawConn.Read`, Section 2.3) or block on a small per-pipe
  buffer of 16 bytes (enough for a record Size and a small record's frame
  header) until the next record starts, taking a pooled buffer again for
  the rest; a busy pipe never pays the extra read. Send buffers,
  reorder structures and multipath side storage exist only while they hold
  something (Section 2.6).
- **Shared per-runtime state.** Pools, timers, clocks and the I/O reactor
  are per runtime or per event loop, never per session; per-session
  objects hold only protocol state.
- **Execution contexts.** Rust: one task per session, parked while quiet.
  Go: the reader and writer goroutines of Section 2.3 at their minimum
  stack size (a writer MAY exit while it has nothing to write and be
  started again with the next data). Java: no thread or virtual thread per quiet session; the event
  loop holds the session. Python: the Rust core and one asyncio reader per
  pipe.

---

## 3. Read path

- One read buffer per pipe, returned when idle; records are parsed in place.
  In-place parsing needs each record contiguous in the buffer, so the buffer
  MUST hold at least the largest record the endpoint accepts: its
  `max_record_size` plus 4 bytes of Size (65539 bytes for the advertised
  65535). A 64 KiB buffer cannot hold that record, and a reader limited to
  it would have to copy the record elsewhere or would stall. The default is
  128 KiB: one maximal record plus the start of the next one. When the
  unparsed tail of the buffer is a partial record that would not fit in the
  remaining space, the reader moves it to the front of the buffer before the
  next read.
- Partial records: read the Size field first and reject oversize records at
  once (SPEC.md Section 3.2); then wait for the body. The first-record check
  of SPEC.md Section 6.2 runs on each byte of a new bootstrap pipe.
- STREAM data SHOULD be copied directly into a waiting reader's buffer
  when one exists, and is otherwise appended to a pooled chunk chain; an
  API that hands out chunks (Go `WriteTo`, Rust `read_chunk`) passes them
  without a further copy. A reorder structure
  (an offset-keyed interval list) is created only when data arrives out of
  order, which happens only in multipath sessions; it is bounded by the
  stream's advertised limit.
- Memory per byte held (SPEC.md Section 9.8) does not depend on the peer's
  framing. A piece below the copy threshold (4 KiB) is copied into storage
  of the stream, appended to the previous piece when it continues it; a
  larger piece may reference the read buffer, which a referenced piece
  keeps alive whole (a record of a few bytes of STREAM data and much
  PADDING would otherwise keep a 128 KiB buffer alive for each piece). The
  bound on those buffers is per session: each pipe, or each lane of a pipe
  (MULTIPATH.md Section 2.5), reads into one current buffer, which is alive
  anyway; a buffer it has moved on from is retired, and the retired buffers
  that received data still references stay within twice the bytes that
  received data references in all buffers, current ones included, each
  buffer counted once however many streams reference it. Whenever the
  bound could have become false (a buffer retires, a pipe or lane ends,
  referenced bytes are consumed or dropped), the pieces of the least used
  retired buffers (fewest referenced bytes per byte of buffer) are copied
  out until it holds again; a running estimate (at most this many retired
  bytes, at least this many referenced) may skip the exact check while it
  shows that the bound holds. A bound per stream would count a shared
  buffer once for every stream with pieces in it: with streams interleaved
  across pipes most buffers hold pieces of several streams, and the
  per-stream rule copied out 1.1 bytes per byte received at 4 pipes and 8
  streams (some of it pieces copied again), against about 0.15 with the
  session-wide rule (Rust, Linux). Out-of-order ranges cost
  bookkeeping each; when a stream's ranges cost more than an eighth of its
  span, its out-of-order data moves to a flat store over the span, pages
  of 4 KiB allocated when first written, with a presence bitmap of one bit
  per byte, whose size no longer depends on the number of ranges, and
  moves back once the ranges are filled. Both together keep a session's
  received data within 2.125 x the sum of its streams' spans, plus a page
  per stream and the current buffer of each pipe or lane being read
  (retired buffers at most twice the referenced bytes, copied bytes once,
  an eighth for bookkeeping); a peer that sends one byte at every other
  offset of its window gets 1.125 x, since such pieces are copied. The common
  case is unchanged: spread chunks of 16 KiB arrive as a few ranges and
  are referenced without a copy. Section 13.3 (M1, M2) lists the mistakes
  that broke this bound.
- Positions (MULTIPATH.md Section 6.1) advance after the whole record has
  been applied.

---

## 4. Write path

- `poll_transmit` fills a reusable batch of up to 256 KiB; payloads under
  4 KiB are copied, larger ones are referenced (iovec / gather write).
  Coalescing small payloads into one record and one system call pays for
  its copy; copying large ones does not. Per language: Go writes TCP and
  Unix carriers with `net.Buffers` and builds TLS batches contiguously
  (flattening a gather list before `tls.Conn.Write` is a second copy);
  Rust keeps written data in reference-counted chunks (`Bytes`) so that
  large payloads can be referenced and copies reorder data once, and
  flattens a batch for carriers without vectored writes rather than
  writing slice by slice (one TLS record per slice); Java uses
  `GatheringByteChannel.write(ByteBuffer[])` and
  `SSLEngine.wrap(ByteBuffer[], ...)` over the pooled chunks; the Python
  binding takes `bytes` by reference and other buffers through the buffer
  protocol, with one copy.
- **Batch and write call.** The batch amortises core calls and the core
  lock, and a write call may hand the carrier a whole batch, except while
  latency-sensitive work is waiting, when a write call holds at most

      write_call_bound = clamp(R x B / 2, 16 KiB, 64 KiB)

  bytes, where R is the pipe's send rate (R_p in a multipath session,
  SCHEDULER.md Section 2.3; on a single pipe the send rate of the low-water
  mark in Section 9.1; the 16 KiB floor until a rate is known) and B the
  latency budget of Section 6.2 (100 us by default; low-latency mode uses
  the 16 KiB floor and applies the bound always). Latency-sensitive work is
  waiting, for the session, when at the moment the call is built any of
  these holds, all of them local state that exists on every carrier:
  1. **input:** a pipe of the session has received bytes that the core has
     not processed yet: the driver saw it readable and has not read it, a
     separate reader holds bytes not yet handed to the core, or the
     driver's last read of the pipe may have left bytes in the carrier.
     On a carrier whose short read means it is empty (a socket read
     directly) that is a read that filled all the room it offered; a
     carrier that reports everything it holds (FIONREAD on a socket; for a
     TLS layer, both the plaintext decrypted but not returned and the
     records received but not yet decrypted) answers directly. A carrier
     that may return less than it holds (a TLS layer that returns one
     record per read or reports only its plaintext, a message or chunk carrier such as
     a WebSocket or a QUIC stream) has input pending after a read that
     returned data until a further read or readiness check of it finds
     nothing ready; the driver makes that check (a non-blocking read or
     poll) before it builds the next call, or, if it cannot, counts the
     carrier as pending only until its next read round. Such a carrier is
     not treated as always pending: every call would then be bounded, and
     bulk transfer over it would lose the whole-batch writes that bound
     its CPU per byte;
  2. **queued:** the core holds frames that would go ahead of bulk data and
     are not in this call: control frames (PIPE_ACK, credit, QX_PING
     requests and responses, PIPE_PING, resets, STOP_SENDING, GOAWAY,
     CONNECTION_CLOSE) or stream data of a direction of urgency 0 to 2 or of
     a small direction (one that holds at most 64 KiB, Section 6.4);
  3. **recent:** within the last max(10 x B, RTT_min) (1 ms on a LAN) the
     core processed input that carried anything but acknowledgement and
     credit frames (PIPE_ACK, MAX_DATA, MAX_STREAM_DATA, MAX_STREAMS, the
     BLOCKED frames, PADDING), or queued frames of rule 2 other than
     PIPE_ACK and credit frames: an exchange that has just happened tends
     to happen again before a long call would end, while the
     acknowledgements and credit that a bulk sender receives all the time
     need nothing from it but processing.

  In a multipath session a blocking write is in addition always held to
  max(4 KiB, R_p x 50 ms) (MULTIPATH.md Section 7.1), and a record that
  holds a frame which cannot be split may exceed either bound by what that
  frame needs. With the bound, a request, a PIPE_ACK or a credit update
  waits for at most one short call (Section 2.3); on a loopback TCP carrier
  a 256 KiB gather write runs the receiving stack inside the call. Without
  latency-sensitive work the whole batch goes in one call, because the
  per-call cost is what bounds CPU per byte: on Linux loopback a 16 KiB
  write costs about twice the CPU per byte of a large batched one (1850
  against 919 ns per 16 KiB), and bulk
  alone is where throughput and CPU matter, while latency is what matters
  as soon as anything is waiting. The bound never limits throughput, which
  windows and the carrier's room set (a 1 Gbit/s pipe needs about 7600
  calls per second at 16 KiB, a 10 Gbit/s pipe about 19 000 at 64 KiB).
  A bulk sender receives only acknowledgements and credit, which rule 3
  ignores and rule 1 sees only while such input is pending when a call is
  built (a single context processes input before each call, Section 2.3),
  so bulk transfer writes mostly whole batches. The CPU-per-GB row of
  Section 9.2 is measured with bulk alone, acknowledgements and credit
  flowing, and checks this; the latency-under-load row sees the bound.
- **One write-progress report.** Whatever the driver's style, the core
  learns of a pipe's write progress through one report per write call:
  when the call started and ended, how many bytes the carrier took, and U,
  what the carrier still holds unsent when the call ends (its unsent-byte
  count, for a non-blocking carrier with a low-water mark up to that mark
  plus what the call added, and 0 for a blocking adapter, whose call
  returns once the bytes are handed on; MULTIPATH.md Section 7.1). A
  driver that writes with blocking calls and one that writes on
  writability send the same report, and a carrier that is not writable
  is reported as such, not as a call in progress; the core keeps one
  write-stall clock and one rate input per pipe fed from it, with no
  separate code path per driver style. A blocking driver's "call in
  progress" and a readiness driver's "not writable" are the two states of
  that one input.
- Over TLS, records are at most 16382 bytes of frames and a write call holds
  whole records, as many as the write-call bound allows (one at the 16 KiB
  floor, four at 64 KiB) while latency-sensitive work waits, otherwise as
  many as the batch holds; writing one frame per TLS write splits 16 KiB
  frames into two TLS records and costs 26-28 % throughput.
- **Compact frames.** The encoder takes every byte-saving choice that QMux
  allows, so that small messages cost what they cost in the most compact
  multiplexers: the shortest variable-length encoding for every field (the
  record Size, stream IDs, offsets, lengths, limits), not only for the frame
  type that SPEC.md Section 4.1 requires; the OFF bit clear for a frame at
  offset 0; no LEN on the last frame of a record, which then extends to the
  record's end (SPEC.md Section 3.1); the pending data of one stream sent as
  one STREAM frame (up to the 16 KiB frame size of Section 9.1), not one
  frame per write; and as many frames per record as the batch holds. A
  message alone in its record then costs 3 bytes on a new stream (Size,
  type, stream ID; without a stream preamble) up to 61 bytes of data and 4
  bytes up to 16 KiB, 4 more on a stream past 16 KiB (a 4-byte offset)
  and 8 more past 1 GiB: 16-byte messages reach
  84.2 % and 69.6 % goodput, 64-byte ones 94.1 % and 88.9 %, against 95.5 %
  for the 3-byte headers of zmux before QMux, 88.9 % for smux (8 bytes) and
  84.2 % for yamux (12 bytes); from 1 KiB on every format is above 98.8 %.
  On the wire (TLS 1.3 and TCP/IPv4 headers, one message per packet) 64-byte
  messages reach 43.8 % against 45.4 % for the 3-byte header, a difference
  of 1.6 points at most, and multipath acknowledgements add less than 1 byte
  per message at 1000 messages per second. A zmux-only compact stream frame
  would save at most 4 bytes per message; it is not worth a second encoding.
- No coalescing timer, no Nagle-style delay, no io_uring, no MSG_ZEROCOPY.
- Committed-but-unwritten bytes stay below one batch (256 KiB, 64 KiB in
  low-latency mode), so that urgent frames are not queued behind bulk data in
  user space.
- Control information is coalesced state (SPEC.md Section 9.2) emitted at the
  head of the next batch: CONNECTION_CLOSE first, then PIPE_ACK, limit
  updates, resets, reinjected data, then new data. The one exception is a
  stream's own data: outside multipath sessions, data below a stream's
  Reliable Size is written before that stream's RESET_STREAM_AT (SPEC.md
  Section 4.3), so a reset whose stream still has such data queued waits for
  it; resets of other streams are not delayed.

---

## 5. Stream scheduling

- Strict priority by urgency 0 (most urgent) to 7; within an urgency level a
  deficit round robin with a 16 KiB quantum. An incremental stream direction
  gives up its turn when its quantum is used, so the incremental ones share;
  a non-incremental one keeps its turn, past its quantum, until it has no
  data ready within its credit, so the non-incremental ones of a level are
  served one after another, in stream-identifier order (EXTENSIONS.md
  Section 5.3). SCHEDULER.md Section 5 (ECF's backlog, BLEST's n) relies on
  exactly this. Each urgency level receives at least 1/16 of the capacity so
  that no level starves.
- Selecting the next stream costs 3-4 ns at 100 000 streams; a weighted fair
  queue heap costs 486 ns, which is why
  zmux does not use one.
- The default local priority is urgency 3, incremental true (the quic-go
  default). PRIORITY TLVs and PRIORITY_UPDATE frames from the peer adjust the
  local sending part's priority (EXTENSIONS.md Section 5).
- Small-message latency under bulk load is "bytes queued ahead / link rate";
  the measures that matter are late binding, a shallow user-space queue and
  `TCP_NOTSENT_LOWAT`.

---

## 6. Flow control

### 6.1 Windows

| Item | Default |
|---|---|
| initial stream window (`initial_stream_receive_window`) | 256 KiB |
| stream window cap (`max_stream_receive_window`) | auto: 64 MiB, K x the BDP of 1 Gbit/s x 100 ms (4 x 12.5 MB, Section 6.5) rounded up to a power of two, as a floor; above it the window grows within the memory allowance (Section 6.2); a configured value is a fixed cap |
| initial session window (`initial_connection_receive_window`) | 1 MiB |
| session window cap (`max_connection_receive_window`) | auto: 96 MiB (1.5 x the stream floor) as a floor, above it within the memory allowance; a configured value is a fixed cap |
| peer unidirectional stream credit | staged: `initial_max_streams_uni` 16, MAX_STREAMS 256 on the first accept of a unidirectional stream; the configured value at once when `max_incoming_uni_streams` is set (SPEC.md Section 8.5 rule 4) |
| session receive buffer cap (SPEC.md Section 8.3 rule 4) | derived: max(session window cap, S), the cap being the configured one or the 96 MiB floor of the default, with S = granted peer bidirectional streams x bidi_remote window + granted peer unidirectional streams x uni window + open local bidirectional streams x bidi_local window = 68 MiB plus 256 KiB per open local stream by default, 128 MiB once unidirectional streams are accepted; so the cap is 96 MiB by default (128 MiB with unidirectional streams) and the receive memory bound 192 MiB (224 MiB), with the listener memory budget as the backstop |
| gateway preset (many sessions per process) | initial stream window 32 KiB, stream window cap 4 MiB, session window cap 8 MiB, so the buffer cap is 8.5 MiB (16 MiB with unidirectional streams) plus 32 KiB per open local stream |
| listener memory budget | 1 GiB by default, a fixed value; 0 means unlimited (API.md Section 4.4) |
| auto-tuning latency budget B | 100 us (Section 6.2) |
| update threshold | min(W/4, max(W/16, 256 KiB)) released and not yet sent (Section 6.2; at most the quarter of SPEC.md Section 8.3 rule 2); immediately when the peer is blocked (no credit left, or BLOCKED at the current limit) and any credit is released, or when it is nearly blocked (less than a quarter left) and min(W/4, 32 KiB) is released (rule 3) |

### 6.2 Auto-tuning

The rule of SPEC.md Section 8.3 rule 6, per scope (a stream's receiving part,
the session): each window is the arrival rate times max(K x RTT_min, B), and
never shrinks. Implementations keep auto-tuning behind one function. The
input is the minimum RTT, never a smoothed RTT: an RTT that includes
queueing (SPEC.md Section 8.3 rule 6) lets the queue a window causes raise
the RTT, and the RTT raise the window. Bounding the window by B is what
holds the request-response p99 under load at about 2 x B (Section 9.2).

```text
parameters (configurable; defaults below):
  K                    = 4
  latency_budget B     = 100 us
  rate_interval_floor  = 1 ms
  rtt_filter_span      = 300 s
  update_floor         = 256 KiB

session state:
  carrier_min_rtt      # from the driver: the carrier's own minimum RTT with
                       # microsecond resolution, None when it reports none
                       # (Darwin, Java, carriers that are not TCP)
  loop_min             # windowed minimum over rtt_filter_span of credit-loop
                       # samples, and of QX_PING samples if any are taken

scope state:
  window               # starts at the initial window
  rates[4]             # the last 4 arrival-rate samples
  rtt_seen[4]          # multipath: per rate interval, the largest
                       # pipe_min_rtt of the pipes data arrived on
  t0, off0             # start of the current rate interval (unset at first)
  pending              # FIFO of (old_limit, sent_at, blocked), at most 17
                       # entries; a full FIFO drops samples (only a sample is
                       # lost, never credit)

autotune_rtt(scope):
  single pipe: return carrier_min_rtt or loop_min          # None before a sample;
               # loop_min includes QX_PING and, with pipe acknowledgement,
               # PIPE_ACK samples (SPEC.md 8.3 rule 6)
  multipath:   return max(rtt_seen) over the current and last 3 intervals,
               or None before data arrived on a pipe with an RTT  # see below

on_data(scope, off, now, pipe):                 # off = new highest received offset
  rtt_seen[current] = max(rtt_seen[current], pipe_min_rtt(pipe))   # multipath
  # credit-loop sample: the first byte beyond a limit the peer was blocked at
  while pending not empty:
    p = pending.front
    if off < p.old_limit: break
    if off == p.old_limit: p.blocked = true; break   # the peer used exactly all of it
    if p.blocked: loop_min.update(now, now - p.sent_at)   # >= one round trip
    pending.pop_front()
  # arrival rate
  if t0 unset: t0, off0 = now, off; return
  if now - t0 >= max(rate_interval_floor, autotune_rtt(scope) or 0):
    rates.push((off - off0) / (now - t0)); t0, off0 = now, off
    rtt_seen.advance()                             # the oldest slot is reused

on_blocked_frame(scope, limit):                 # DATA_BLOCKED / STREAM_DATA_BLOCKED
  for p in pending: if p.old_limit == limit: p.blocked = true

update_due(scope):                              # SPEC 8.3 rule 2 "MAY earlier";
  return C - L >= min(window / 4, max(window / 16, update_floor))   # rule 3 unchanged

on_send_update(scope, now):                     # whenever rule 2 or 3 sends credit
  if rates not empty and not session.growth_paused:   # SPEC 8.3 rule 4: from the
                                                      # buffer cap until below half of it
    rtt  = autotune_rtt(scope)
    span = B if rtt is None else max(K * rtt, B)
    target = max(rates) * span
    if target > window:
      window = min(target, max_window(scope), memory_allowance(scope))
      if scope is a stream:          # session growth: through the predicate when set (2.5.2)
        session.window = max(session.window,
                             min(1.5 * window, session_window_cap, memory_allowance(session)))
  limit = released + window
  if limit > advertised:
    pending.push(old_limit = advertised, sent_at = now,
                 blocked = (highest_received == advertised))
    send MAX_(STREAM_)DATA(limit); advertised = limit
  # the window never shrinks
```

`memory_allowance(scope)` is the largest window the listener's memory budget
allows: for the session scope, its current window plus what the budget has
left (the budget minus the listener's buffered, retained and suspended bytes
and minus the sum over its sessions of their session window growth above
the initial window); for a stream, the same bound through the session
window; a session without a listener has its own budget (the
`memory_budget` parameter of API.md Section 6, 1 GiB by default; 0 =
unlimited). With the default (auto) caps this allowance is what lets a
window grow beyond the 64 MiB / 96 MiB floors. Because session
window growth is charged to the budget, the budget bounds the credit that
sessions keep, not only the bytes they hold.

| Parameter | Default | Why |
|---|---|---|
| latency budget B (a parameter of `max_stream_receive_window`, API.md Section 6) | 100 us | request-response p50 under bulk is about B and p99 about 2 x B (Linux loopback: 107 / 199 us), so B sets the structural term 1.9 x B of the p99 target of Section 9.2; about 80 us trades 4 points of bulk throughput for about 40 us of p99 |
| K | 4 | the rule tolerates a standing queue of (K - 1) x RTT_min below the session, and CUBIC fills a bottleneck buffer of 1-2 x BDP (with credit in quarters, K = 2 / 3 / 4: 75 / 83 / 87-100 % at 1 Gbit/s x 100 ms); K also sets the ramp (2.5 Gbit/s x 200 ms: K = 2 87-88 %, K = 4 97-98 %); K = 4 also covers the 2 x sum(R) x RTT_max that spreading needs (MULTIPATH.md Section 12) |
| rate samples | the maximum of the last 4 intervals, each max(RTT, 1 ms) long | K intervals for a K-RTT target; the maximum, because a window-limited sender idles within an interval; 1 ms is at least the coarsest common monotonic clock tick |
| RTT filter span | 300 s | longer than a bulk transfer keeps a bottleneck queue standing (Linux uses 300 s for `tcpi_min_rtt`); a 10 s span read 420 ms on a 100 ms path and grew the window to 16 x BDP |
| regular update | at min(W/4, max(W/16, 256 KiB)) released | smooth credit at large windows; 256 KiB is the write batch; at windows up to 1 MiB it is the W/4 of SPEC.md Section 8.3 rule 2 |

**RTT sources.** The carrier's minimum RTT: Linux `TCP_INFO` `tcpi_min_rtt`
(never `tcpi_rtt`, which is smoothed and includes queueing); Windows
`SIO_TCP_INFO` `MinRttUs` (`TCP_INFO_v0` or `_v1`, through `WSAIoctl`; Go
needs no cgo for it). Darwin's `TCP_CONNECTION_INFO` and `tcpi_srtt` report
only a smoothed RTT in milliseconds and are not used, for windows or for the
low-water mark of Section 9.1. Without a carrier minimum the core measures the
credit loop from the updates it sends and the data it receives, and adds
QX_PING samples (taking the minimum of both); such an endpoint SHOULD put a
QX_PING request in its first record (SPEC.md Section 8.3 rule 6), which
gives a sample one round trip after establishment. The
core's clock must resolve microseconds: Go's monotonic clock on Windows
advances with the timer interrupt (0.5-15.6 ms), which reads every loopback
credit loop as 0 and the arrival rate as 1-6 x too low, so the Go core takes
its timestamps from QueryPerformanceCounter there.

**Multipath.** autotune_rtt(scope) is the largest minimum RTT over the pipes
on which the scope's data arrived during the rate intervals that set R
(`rtt_seen`, the last 4 intervals; constant state per scope). These are the pipes the sender keeps
eligible for that data: its eligible tiers, standby pipes only while they
have taken over, and a less-preferred tier only while it takes overflow
(SCHEDULER.md Section 10). The receiver cannot see the sender's tiers, which
local settings may override, but it sees where data arrives, so idle tiers
and standby pipes that are not in use do not inflate the window, and a pipe
that starts to take data raises RTT_max from its first bytes on. pipe_min_rtt
is the attachment's carrier minimum when its carrier reports one, otherwise
min_rtt_p, the minimum over the last 300 s of its PIPE_ACK RTT samples, which
is also the scheduler's one-way delay input (SCHEDULER.md Section 2.1): each
pipe keeps one minimum filter. An
attachment without a local outbound direction has no RTT samples of its own:
it contributes the largest pipe_min_rtt of the other attachments of its fate
group (an HTTP GET downlink and its POST uplink), or, without one, the
largest pipe_min_rtt in the session; never loop_min, which, measured over
whichever pipes carried the credit and the data, can be the fastest pipe's
RTT. loop_min restarts when the carrier of a single-pipe session is
replaced (a resumed session). The minimum across pipes is never used: it would
leave the windows short of MULTIPATH.md Section 12 by the ratio of the
slowest to the fastest RTT.

The multipath estimators were checked for the same mistake: windows follow
this rule, and the other quantities that start from the 333 ms default
(T_suspect, T_fail, the first retransmit budget of 1.4 MB, the reorder budget)
are bounded, start conservative (longer timers, small budgets) and are
replaced by the first sample; none of them grows a window.

### 6.3 Release

The receiver policy of SPEC.md Section 8.3 rule 4:

- Stream credit is released when the application reads (or the stream is
  released); a stream nobody reads holds at most its own window.
- Session credit for a byte is released when the byte is received into a
  stream buffer, while the session's unread buffered bytes are below the
  buffer cap. A byte received at or above the cap is held until it is
  consumed or until the buffered bytes fall below the cap, whichever comes
  first; each byte releases session credit once. Auto-tuning stops growing
  stream windows at the cap until the buffered bytes fall below half of it.
  Credit already advertised is never taken back.
- A reset releases the session credit of the bytes it makes undeliverable
  (SPEC.md Section 8.3): on every RESET_STREAM or RESET_STREAM_AT (a first
  one, one after a FIN, one that lowers the Reliable Size), the bytes in
  [smallest Reliable Size, final size) that have neither arrived nor been
  released are released at once. Count as arrived the contiguous prefix
  [0, highest contiguous offset) plus the out-of-order ranges, and keep
  tracking those ranges when the data is read or discarded on arrival (after
  `CancelRead`, for example): the reorder structure of Section 3 alone does
  not remember consumed or discarded bytes. Record the released gap per
  stream so that a later arrival in it, or a second reset, releases
  nothing; the stream's receive state is kept until its final size is known
  and every byte below it has been released once. Section 13.2 lists the
  accounting mistakes that leaked or over-granted credit.
- Released credit is sent at min(W/4, max(W/16, 256 KiB)) (Section 6.2), and at once when the
  peer is blocked, or nearly blocked with at least min(W/4, 32 KiB)
  released (SPEC.md Section 8.3 rule 3), so no state keeps released credit
  from a blocked peer.
- The cap is max(session window cap, S), with the 96 MiB floor standing for
  the default (auto) cap, and S the sum over all stream
  kinds of the streams that the peer is granted at once times their initial
  windows, from the receiver's own parameters: 68 MiB by default (128 MiB
  once the application accepts unidirectional streams, of which only 16 are
  granted before, SPEC.md Section 8.5 rule 4) plus 256 KiB per open local
  bidirectional stream; 8.5 MiB (16 MiB) under the gateway preset plus
  32 KiB per open local stream.

**Exposure.** What a peer can make a session hold, by preset:

| | Default | Gateway preset |
|---|---|---|
| without any help from the application: unidirectional streams | 16 x 256 KiB = 4 MiB (the credit granted before the application accepts one) | 16 x 32 KiB = 512 KiB |
| without any help from the application: bidirectional streams the application accepts and reads | about the data in flight, at most one session window (1 MiB initially) | about 1 MiB |
| bidirectional streams the application never accepts (an application that never accepts peer streams should set `max_incoming_streams` to 0) | 256 x 256 KiB = 64 MiB | 256 x 32 KiB = 8 MiB |
| worst case, with the application's cooperation (it accepts streams, lets their windows grow, then stops reading) | cap + session window = 96 + 96 = 192 MiB; 128 + 96 = 224 MiB with unidirectional streams in use; plus local stream windows | 8.5 + 8 = 16.5 MiB; 24 MiB with unidirectional streams |
| across sessions | the listener memory budget, 1 GiB by default, the backstop: about 5 sessions at the worst case before new grants stop (about 13 at full rate on the 1 Gbit/s x 100 ms design point, each charged about 75 MB of session window growth; further sessions then keep their initial windows) | same budget |

With all 256 unidirectional streams granted at once, any peer could pin
64 MiB per session in unidirectional streams that an application not using
them never accepts; the staged credit limits that to 4 MiB while letting a
peer open its first 16 unidirectional streams without a round trip. Without
a default budget nothing would bound the sum over sessions.

Numbers for typical and adversarial cases (defaults unless stated):

| Case | Unread bytes | Cap | Outcome |
|---|---|---|---|
| 128 peer streams stop reading at the default 256 KiB window | 32 MiB | 96 MiB | below the cap: session credit keeps turning over on receipt; streams that are read are unaffected |
| unidirectional streams, application never accepts any | at most 4 MiB | 96 MiB | the peer has only the 16 streams of staged credit |
| 200 unidirectional streams accepted but not read, at 256 KiB | 50 MiB | 128 MiB | below the cap: no effect on other streams (granted unidirectional streams count in S) |
| all 256 peer bidirectional streams stop reading at 256 KiB except one | 63.75 MiB | 96 MiB | below the cap: the stream that is read is unaffected |
| client with 1000 pipelined requests, responses read in order | up to 250 MiB at initial windows | 314 MiB | below the cap: the response being read never waits for the others |
| gateway preset, 255 streams stop reading at 32 KiB | 7.97 MiB | 8 MiB | below the cap; no effect on other streams |
| adversarial: 2 streams auto-tune to 48 MiB, then stop reading | 96 MiB | 96 MiB | cap reached: credit only on consumption, no window growth until 48 MiB; memory stays below 96 + 96 = 192 MiB |
| adversarial, gateway preset: 2 streams auto-tune to 4 MiB, then stop reading | 8 MiB | 8 MiB | cap reached; memory stays below 8 + 8 = 16 MiB |
| two streams grown to 32 MiB and unread, a read stream holding 32 MiB, the peer at the session limit | 96 MiB, then 64 MiB once the read stream is drained | 96 MiB | at the cap the read stream's new bytes are held; as soon as reads bring the buffer below the cap the held bytes are released and rule 3 sends them at once, so the peer is never at the limit with released credit unsent |

In the adversarial cases the streams that are read slow down to what their
own reads release, and a read stream with nothing buffered waits until the
application reads or releases one of the stalled streams. That is the price
of a per-session bound; an application that keeps streams it does not read
can avoid it by capping their windows with `SetReceiveWindow(initial, max)`
(Section 6.4).

**Listener memory budget.** A listener has a budget for the sum over its
sessions of buffered receive data, retained data, the buffers of suspended
sessions and the growth of each session window above its initial size
(API.md Section 4.4; window growth stops at the memory allowance of Section
6.2): 1 GiB by default, a fixed value
rather than a fraction of the host's memory so that behaviour does not
change between machines; 0 means unlimited. The budget limits what is newly
granted: credit already advertised is never withdrawn, so the sum can exceed
the budget by the credit outstanding when it was reached. While the sum is
above the budget:

1. every session behaves as if it were at its buffer cap: session credit
   only on consumption, no window growth;
2. MAX_STREAMS keeps being raised as SPEC.md Section 8.5 requires, and new
   peer streams are rejected with ZMUX_REJECTED (RESET_STREAM and
   STOP_SENDING) without allocating receive buffers; the stack never offers
   them, discards their data (releasing its session credit) and the peer may
   retry them later or elsewhere;
3. the listener refuses new sessions (CONNECTION_REFUSED after its own
   parameters) and closes new carriers that would add a pipe to a session
   that is not suspended; a join that lets a suspended session resume is
   accepted, because resumption drains the session's retained data;
4. suspended sessions are closed with ZMUX_RESUME_TIMEOUT, those holding the
   most memory first;
5. the pressure hook is called, so the application can shed load (for
   example close idle sessions).

Normal operation resumes when the sum falls below three quarters of the
budget.

### 6.4 Per-stream windows (local policy)

`SetReceiveWindow(initial, max)` on a receiving part and `SetSendBuffer(n)`
on a sending part (API.md Section 5.1) are local policy, not protocol:

- `initial` raises the stream's advertised limit at once (MAX_STREAM_DATA in
  the next record); `max` caps auto-tuning and the stream's unread bytes, and
  therefore its reorder buffer in multipath sessions;
- lowering never withdraws credit already advertised; it only slows later
  updates;
- both are bounded by the session window and the buffer cap; values below
  16 KiB are raised to 16 KiB;
- `SetSendBuffer(n)` bounds the stream's written-but-unreleased bytes
  (unwritten to the carrier on a single pipe, unacknowledged in multipath
  sessions); writes block beyond it. The default is the whole session
  `send_buffer` (which grows to the retransmit budget in a multipath
  session), and the session buffer is shared fairly (below). A fixed
  fraction would cap one stream spread over fast pipes with different
  delays (CONFORMANCE.md S-5);
- the session `send_buffer` bounds the sum over stream directions and is
  shared fairly by water-filling: a stream direction that holds at most
  64 KiB of written-but-unreleased bytes and has no write waiting is
  **small**; its bytes come off the buffer first, and every other stream
  direction may hold at most max(64 KiB, (send_buffer - bytes of the small
  ones) / n), n being their number. The session total stays within
  `send_buffer` except that every stream direction may always hold 64 KiB.
  Two running sums (the small directions' bytes and n) keep this O(1).
  Counting the small ones in n as well would let a stream that holds one
  1 KiB message halve a bulk stream's share. A message of up to 64 KiB on any
  stream is therefore never blocked behind another stream's bulk data, and
  bulk streams progress at similar rates; the total is bounded by
  `send_buffer` plus 64 KiB per stream direction with data. With a plain
  session-wide cap, first come, first served, one bulk stream fills the
  buffer and blocks every other writer (CONFORMANCE.md F and S-2 fail);
- set before the first frame (first write or pinning) on a locally opened
  stream, both take effect in
  the first record at no extra round trip, which is why there are no
  open-time options.


### 6.5 High bandwidth-delay paths

The window caps and the send buffer maximum are memory bounds, not protocol
limits, and by default they are not fixed: 64 MiB per stream, 96 MiB per
session and a 64 MiB send buffer maximum are floors, sized for 1 Gbit/s x
100 ms, and above them a window or the retained data grows only as far as
the measured rate and RTT ask (Section 6.2, SCHEDULER.md Section 13.1) and
the memory budget allows: the listener's `MemoryBudget`, or for a session
without a listener its own `memory_budget` (both 1 GiB by default, API.md
Sections 4.4 and 6). A 10 Gbit/s x 100 ms path therefore reaches full rate
without configuration, and the budget, not a per-session constant, bounds
what all sessions hold together. With a fixed cap that limits the sender,
a path reaches about 15/16 x cap / RTT. A configured cap is a fixed bound
for deployments that want one.

| Bound | Needed for full rate | 1 Gbit/s x 100 ms (BDP 12.5 MB) | 10 Gbit/s x 100 ms (BDP 125 MB) |
|---|---|---|---|
| stream window (`max_stream_receive_window`) | K x BDP = 4 x BDP when TCP runs below the session: auto-tuning (Section 6.2) settles at R x 4 x RTT_min, which tolerates the 1-2 x BDP that CUBIC keeps queued at the bottleneck; 4 x sum(R) x RTT_max with spreading (MULTIPATH.md Section 12). Without a queue below about 16/15 x BDP suffices, since credit returns in steps of W/16 (Section 6.2; 4/3 x BDP with quarter steps) | within the 64 MiB floor (CONFORMANCE.md H-1', H-1) | about 500 MB (auto-tuning settles at 4 x BDP, H-6), above the floor, within the budget; at 200 ms (H-2) about 1 GB, the whole 1 GiB budget |
| session window (`max_connection_receive_window`) | at least the stream windows of the bulk streams | within the 96 MiB floor | 1.5 x the stream window (748.9 MB in H-6) |
| send buffer maximum (`send_buffer` parameter `max`, multipath only) | the data in flight, sum R x (SRTT + max_ack_delay), plus room for reinjection; the budget formula asks for 4 x that | two 1 Gbit/s pipes with 50 and 100 ms one way retain at most 31 MB, within the 64 MiB floor (1996 Mbit/s, H-3) | two 10 Gbit/s x 100 ms pipes retain about 250 MB, within the budget (19970 Mbit/s, H-7) |
| memory budget (`MemoryBudget`, `memory_budget`) | the sum over concurrent sessions of what they buffer and retain plus their session window growth; a session at full rate is charged about its auto-tuned session window | 1 GiB holds about 13 sessions at full rate (about 75 MB each), 5 at the 192 MiB worst case | 1 GiB holds one session at full rate (its session window grows by about 750 MB): raise it for more, or use 0 (unlimited) with an external quota |
| suspended-session bytes (RESUME.md Section 9) | the retained data and unread buffers of each suspended session | 256 MiB default holds about 10 | raise to the expected number of suspended sessions x (send buffer + window) |
| operating-system socket buffers (Linux `net.ipv4.tcp_wmem` / `tcp_rmem` maximum, `net.core.wmem_max` / `rmem_max` for explicit `SO_SNDBUF` / `SO_RCVBUF`) | each TCP carrier's own data in flight, about its share of BDP, on both ends; TCP_NOTSENT_LOWAT limits only the unsent part, not what is in flight | the common 4 MiB `tcp_wmem` maximum caps one pipe near 335 Mbit/s: raise it to at least 16 MiB | at least 128 MB per 10 Gbit/s pipe |

The memory DoS bounds stay where they were (SPEC.md Section 8.3 rule 4):
the session buffer cap is computed from the 96 MiB floor, not from the
grown window, so what a peer can make the receiver hold at the floors is
unchanged (192 MiB per session at worst), and growth above the floors needs
both the application's cooperation (windows grow only on consumption) and
the budget's grant, which bounds the sum over sessions as before. What
changes is that one session at full rate on a 10 Gbit/s long path may take
most of a 1 GiB budget: a listener's pressure hook (API.md Section 4.4)
lets the application decide, and a session without a listener simply stops
growing at its own budget.

While the window limits the sender, the arrival rate is about 15/16 x W /
RTT (credit returns in steps of W/16), so each rate interval (one RTT) lets
auto-tuning raise W to almost 4 x W: the window grows almost fourfold per
round trip until the path or the cap limits it (90 % of
2.5 Gbit/s x 200 ms after 2.5 s, CONFORMANCE.md AT-5); a larger initial window (`initial_stream_receive_window`,
`initial_connection_receive_window`) shortens that when the path is known.
Timers need no change: T_suspect, T_fail, PTO and the probation base all
scale with the measured RTT (failover with 100 ms one way:
CONFORMANCE.md H-5), and the one-second floors only matter on short paths.
The listener's `FirstRecordTimeout` (2 s) may need raising on satellite
paths where a TLS 1.2 handshake alone takes close to 2 s.

**Per-event costs at 10 Gbit/s.** Nothing in the design is per packet; the
event rates at 10 Gbit/s (1.25 GB/s) are:

- PIPE_ACK (multipath, or pipe acknowledgement): one per 64 KiB, about
  19 000 a second per direction, each about 11 bytes (0.02 % of the
  goodput, CONFORMANCE.md H-6 and Q-11s) and, on the sender, a constant
  amount of bookkeeping each (one RTT and one rate sample, a few deque
  operations), against about 160 000 full-size packets a second that the
  carrier itself handles. The threshold stays 64 KiB rather than growing with the
  rate: it is also the slack A of the limiter's in-flight cap
  (SCHEDULER.md Section 9.1), so a threshold of R x 1 ms would add a
  millisecond of queue at 10 Gbit/s, ten times the latency budget B, to
  save a cost that is already small. A receiver MAY advertise a larger
  `zmux_ack_threshold` when it knows its peer's carriers report their
  queue (MULTIPATH.md Section 3.2).
- Write calls (Section 4): one per 256 KiB batch, about 4 800 a second,
  while only bulk data flows; while latency-sensitive work waits, at most
  clamp(R x B / 2, 16 KiB, 64 KiB) = 64 KiB per call, about 19 000 a
  second, which bounds what a request waits behind to 52 us of wire time.
- Limiter pacing (SCHEDULER.md Section 9.1): at most one wake-up per timer
  granularity G (at most 1 ms), each releasing a quantum of up to gain x R
  x G (1.25 MB at G = 1 ms), never a per-packet timer.
- Reads: the 128 KiB read buffer (Section 9.1) gives about 9 500 reads a
  second; records of up to 64 KiB are parsed in place.
- Scheduling decisions: one per `spread_chunk` (16 KiB), about 76 000 a
  second in a multipath session; a larger `spread_chunk` reduces them at
  the cost of coarser spreading.

---

## 7. Retention and acknowledgement (multipath)

- Each outbound pipe keeps a queue of (position range, frame descriptors) for
  ack-eliciting frames; the descriptors reference stream send buffers, not
  copies. A PIPE_ACK pops entries in O(1) amortised (about 4 ns per frame).
- On suspicion or failure the descriptors beyond the acknowledged position
  are converted into reinjection work following MULTIPATH.md Section 7.4;
  STREAM ranges that a later acknowledgement covers are skipped.
- Acknowledgement generation tracks, per inbound pipe, the first
  unacknowledged ack-eliciting byte's time and the byte count since the last
  PIPE_ACK; one session timer covers all pipes.
- RTT per pipe follows RFC 9002 Section 5 with the sample rules of
  SCHEDULER.md Section 2 (only acknowledgements that cover ack-eliciting
  bytes, never across a suspension, never from an entry whose ACK Delay
  exceeds the peer's max_ack_delay unless the pipe has had no sample for 1
  s, never inherited by a new pipe); min_rtt is the minimum over the last
  300 s, the same filter that auto-tuning uses (Section 6.2).
- Delivery rate per pipe follows SCHEDULER.md Section 2, BBR's delivery
  rate sampling: at each send the range records the pipe's delivered byte
  count, the time of its last acknowledgement advance and the send time of
  the most recently acknowledged range; each acknowledgement advance yields
  one sample from the newest range it covers, delivered bytes divided by
  max(send interval, acknowledgement interval). A sample is
  application-limited unless, at some moment between the send and the
  acknowledgement, the pipe was not writable (or a blocking write was in
  progress) while the endpoint had data ready that it had not committed to
  any pipe, and always when its range was sent while an application-limited
  bubble was in flight: keep per pipe the position recorded at its last
  application-limited moment (a write opportunity that left the pipe
  writable), cleared once the acknowledged position passes it, and mark
  every range sent while it is set (BBR's `app_limited` mark; a new pipe
  starts with the mark at position 0, which covers the ranges sent before
  its first acknowledgement advance). Application-limited samples only
  raise the estimate. The
  estimate is the maximum over the samples of the last 10 round trips of the
  pipe within the last 10 s, and keeps its last value when no sample is
  left, so idle periods never lower it and the retransmit budget
  (SCHEDULER.md Section 13.1) never shrinks because the application paused,
  while a pipe that really slowed down produces lower bottleneck samples and
  its old maximum ages out.

---

## 8. Multipath scheduler

### 8.1 Late binding

The scheduler runs inside `poll_transmit(pipe)`, that is, only when a pipe can
take bytes. A carrier should not hide a deep buffer. Low-water marks, unsent
counts, carrier RTTs and delivery statistics are optional adapter
capabilities; each use of them below has a fallback for carriers without
them:

| Platform | Mechanism |
|---|---|
| Linux | `TCP_NOTSENT_LOWAT` of Section 9.1 (max(32 KiB, R_p x RTT_min_p / 8) per pipe in multipath sessions, max(128 KiB, send rate x RTT_min / 8) on a single pipe), writes driven by writability (epoll / Go RawConn), `TCP_INFO` for `tcpi_notsent_bytes`, `tcpi_delivery_rate`, `tcpi_min_rtt` |
| macOS | `TCP_NOTSENT_LOWAT` (0x201); `TCP_CONNECTION_INFO` reports only a smoothed RTT in milliseconds, which is used neither for windows nor for the low-water mark (RTT_min comes from the credit loop, Section 6.2) |
| Java | no `TCP_NOTSENT_LOWAT` in the JDK: approximate with a small `SO_SNDBUF` (it costs some BDP headroom) or set it through FFM |
| Windows | `SIO_TCP_INFO` (`MinRttUs`) for RTT_min (Section 6.2); no low-water mark |
| other carriers (TLS and WebSocket libraries, HTTP/2, HTTP/3 and QUIC streams, KCP, Unix sockets, in-process pipes, user-supplied adapters) | the adapter reports unsent bytes and writability when it can, and keeps its own send buffer small; otherwise the core counts only its own queue and drives writes by the adapter's write completions (Section 4). What such a carrier buffers is invisible to the arrival estimate: in a multipath session, and on a single pipe with pipe acknowledgement (MULTIPATH.md Section 3.5), the carrier queue limiter of SCHEDULER.md Section 9.1 paces what the carrier is given, from PIPE_ACK (per pipe: two monotone deques of u samples for RTT_base_p and S_p, a third of bottleneck rate samples for the rate bound R_lo, the mode, cycle phase and probe stage, a token bucket refilled at gain x R_p in quanta of the timer granularity, and the in-flight cap checked at each write opportunity), and in a multipath session urgent data is redundant (SCHEDULER.md Section 4.1). A session without multipath and without pipe acknowledgement relies on its receive windows and the first record's QX_PING. RTTs come from PIPE_ACK samples, the credit loop and QX_PING, never from the carrier |

Where the carrier is TCP, also set `TCP_USER_TIMEOUT` (Linux) or
`TCP_RXT_CONNDROPTIME` (macOS) so that a dead pipe errors in seconds rather
than after `tcp_retries2`; other carriers rely on failure detection
(MULTIPATH.md Section 7.1).

### 8.2 Estimates

`Q_p` = bytes committed to p and unsent (core queue plus carrier unsent),
`R_p` = delivery rate (windowed maximum, Section 7), `OWD_p` = min_rtt_p
minus half the smallest min_rtt of the live outbound pipes (SCHEDULER.md
Section 2); acknowledgements of all pipes return on the same pipe, so this
keeps the differences between pipes exact, and min_rtt keeps queuing, which
Q_p / R_p counts, out of the delay term.
ETA_p(L) = (Q_p + L) / R_p + OWD_p (SCHEDULER.md Section 4). The ECF test
compares a slow pipe with the fast pipe's ETA for the backlog plus the chunk,
not for the chunk alone (SCHEDULER.md Section 5); without the backlog a
single bulk stream never uses a pipe whose delay exceeds the fastest one's by
more than a few milliseconds.

### 8.3 Policies

- `affinity` (session default): bind each stream direction, within the
  most-preferred tier that has a pipe whose binding wait (Q + A) / R is at
  most `overflow_delay`, to the pipe with the smallest OWD + (Q + A + E) / R;
  E is the direction's written but uncommitted bytes and A the sum of E over
  the directions already bound to that pipe. When no tier has room, choose
  by the same cost over the pipes of every tier, which spreads bulk streams
  by capacity. Small (E < 256 KiB) and urgent (urgency 0-2) streams go to
  the smallest OWD of the chosen tier (of the most-preferred tier when none
  has room). Rebind only at idle points (nothing of that direction
  unacknowledged on the old pipe), on suspicion/failure, or up to a more
  preferred tier that has room. Bindings made before every pipe has an RTT
  sample are provisional and re-evaluated once all have one (SCHEDULER.md
  Section 7). Zero reordering. Keep A per pipe as a running sum updated
  on writes, commits and (re)bindings, so a binding costs O(pipes).
- `spread-eta` (default spreading policy): per chunk, earliest ETA with ECF
  hysteresis 0.25 (0 for urgency 0-2) and the BLEST window check; chunks of
  `spread_chunk` (16 KiB).
- Redundant urgent data (SCHEDULER.md Section 4.1), whatever the policy:
  keep per pipe a short queue of copies served at the head of its next
  write opportunity, and one session counter of copy bytes queued or sent
  whose data is still owed and not yet acknowledged on any pipe (at most
  max(`spread_chunk`, retransmit budget / 64)); decrement it on the
  acknowledgement of the data, when a reset of its stream means the data is
  no longer owed (above the Reliable Size), and when a queued
  copy is dropped (its pipe suspect, failed, draining or removed), so that
  it cannot leak. The pair is found with
  the arrival estimates the policy already computes for the tier; at a
  pipe's write opportunity an urgent chunk is offered to the pipe when it is
  one of the pair, then the copy is queued for the other. Dropping a copy
  whose data is acknowledged, or whose pipe becomes suspect, is a check at
  dequeue time. Per pipe, also remember whether the next new chunk follows
  an idle period (nothing unacknowledged, no acknowledgement progress for
  max(1 s, T_suspect)) and copy the first `spread_chunk` after it.
- `spread-round-robin` and `spread-rate-weighted`: one smooth weighted
  round-robin over the writable eligible pipes of the tier, with weight
  Weight or Weight x R_p (SCHEDULER.md Section 6); keep the current values
  per policy instance in floating point for the rate-weighted form, and
  reset a pipe's value when it leaves the eligible set.
- `session-affinity`: one active pipe per tier and direction (the smallest
  OWD); tier saturation counts only that pipe.
- Custom: called outside every lock with a value-only view (Section 2.5.2):
  it must not block, and it receives no handle; it gets a frame description
  (kind, stream, urgency, ready bytes, last pipe) per waiting stream
  direction and per-pipe values (id,
  direction, state,
  SRTT, RTT variance, delivery rate, unsent, in flight, weight, effective
  priority, standby, writable, tier saturated, in probation, eligible);
  returns, per request, a pipe index with a byte allowance (sticky) or
  "wait" (until the next input, Section 2.5.2). It normally picks an eligible pipe and may pick another
  to override tier gating. An invalid answer, a panic or an exception falls
  back to `affinity` and is counted, and so does an answer whose view is no
  longer current (generation check, Section 2.5.2).
- In every built-in policy, PIPE_ACK, credit frames and other control frames
  go to the first pipe of the control order of MULTIPATH.md Section 6.5
  (ACTIVE, then the most-preferred tier, then the lowest one-way delay); SUSPECT and DRAINING
  pipes carry them only as the additional copies of MULTIPATH.md Section 6.5,
  or when no JOINING or ACTIVE pipe exists (rule S1). DATAGRAM goes to the
  usable pipe with the lowest estimated arrival time. Reinjected information
  goes first.

### 8.4 Tiers and standby

Tier gating (SCHEDULER.md Section 10) runs before the policy, at every
scheduling decision, in O(number of pipes): sort the usable non-standby
pipes by effective priority (cache the order; it changes only with a
priority, standby or state change), walk the tiers with the offered data
X (written, within credit, uncommitted, plus pending reinjection; keep it
as a running sum) and Q_t, R_t, mark a tier saturated when Q_t + X_t > R_t x
D or when its age is D or more, pass X_(t+1) = max(0, X_t - max(0, R_t x D
- Q_t)) on, and stop at the first tier that is not saturated. The eligible
pipes are those of the tiers walked. With late binding the check happens
when a pipe asks for bytes: a pipe of a less-preferred tier asks, finds its
tier not eligible, and takes nothing; a pipe that is not writable simply
does not ask, which is why "not writable" is not saturation. For the age,
keep one FIFO of write marks (stream, end offset, time; writes within 1 ms
merged) and pop marks whose bytes are committed; per stream, the start of
its current wait for send-buffer space (kept when its next write starts
waiting within 1 ms of the previous one completing), which is the time of
the marks of bytes such a write adds; the oldest pending reinjection; and
the time a stream or the session last regained credit. Together they give
the age in O(1) amortised. Keep per tier the moment it last became eligible.
The moment the age will reach D is a deadline of `next_timeout` while a
pipe of the next tier is writable, so that the overflow starts on time even
when nothing else happens. Keep per pipe: effective send priority and its
source (local, peer request, default), standby, the receive priority and
standby requested of the peer with their PIPE_STATUS sequence number, the
peer's latest request, the probation start, backoff exponent and longest
uptime U_max (per fate group; the base is computed from the current SRTT
whenever the end is checked, SCHEDULER.md Section 11), the position whose
acknowledgement ends a revalidation (SCHEDULER.md Section 11.1), and the
bytes carried. Standby pipes take no new stream data while a
non-standby pipe in that direction is usable, are probed every `standby_probe` (1 s, like
pipes of a less-preferred tier, while the session is busy; in a quiet session at the probe
interval, plus once when traffic resumes, MULTIPATH.md Section 7.1), and when every non-standby
pipe is unusable they carry the traffic; the endpoint announces the takeover
with PIPE_STATUS and withdraws it with another PIPE_STATUS once a non-standby
pipe is usable again (MULTIPATH.md Section 9). Pipes in probation are probed every max(SRTT, 200 ms).
Expose the per-pipe bytes in each direction: on a direct-plus-relay
deployment they are the operator's view of what the relay costs.

### 8.5 Fairness

N pipes over one bottleneck take N shares; zmux cannot couple kernel TCP
congestion control. Defaults: one pipe per distinct interface or relay and
direction; pipes that share a bottleneck get one non-zero fate group. Prefer
stream-terminating proxy designs over carrying TCP inside zmux (TCP-over-TCP).
For single-interface multipath on Linux, use kernel MPTCP on a single pipe.

---

## 9. Defaults and performance targets

### 9.1 Defaults

| Item | Default | Source |
|---|---|---|
| windows | Section 6.1 | |
| `initial_max_streams_bidi` / `_uni` | 256 / 16, raised to 256 unidirectional on the first accept (the configured value at once when `max_incoming_uni_streams` is set) | SPEC 5.3.1, 8.5 |
| listener memory budget | 1 GiB (0 = unlimited) | API 4.4 |
| `max_record_size` advertised | 65535; TLS records built at 16382 | SPEC 3.2 |
| sent STREAM frames | at most 16 KiB of data each; a stream that owns the pipe gets several frames per record and many records per write batch, so the frame size does not limit its throughput | |
| write batch | 256 KiB (multiples of 16 KiB on TLS), written in one call, or in calls of at most the write-call bound while latency-sensitive work waits | Section 4 |
| copy / reference threshold | 4 KiB | |
| read buffer | 128 KiB per pipe (at least max_record_size + 4), returned when idle | Section 3 |
| TCP_NODELAY | on | |
| carrier low-water mark (TCP_NOTSENT_LOWAT on a TCP socket; a carrier that reports unsent bytes but has no mark: at most this many unsent; one that reports neither: the carrier queue limiter of SCHEDULER.md Section 9.1) | max(128 KiB, send rate x RTT_min / 8), recomputed at most every 20 ms and reset only when it changes by more than 25 %; floor 32 KiB per pipe in multipath (with R_p and the pipe's RTT_min) or low-latency mode; RTT_min as for auto-tuning (Section 6.2), never Darwin's `tcpi_srtt`; a sender with neither a carrier minimum nor a credit-loop or QX_PING sample uses the floor (it MAY send a QX_PING to get a sample); never above the carrier's send buffer | a fixed 128 KiB costs 8-12 % of a 1 Gbit/s x 100-200 ms path, because the writer must refill the carrier every millisecond |
| scheduler | urgency 0-7 + DRR, quantum 16 KiB, 1/16 minimum share | |
| per-stream send buffer (`SetSendBuffer`) | the whole `send_buffer`, within the water-filling fair share max(64 KiB, (send_buffer - small holdings) / n) | Section 6.4 |
| session send buffer (`send_buffer`) | 4 MiB; grows to the retransmit budget in multipath sessions, up to the parameter `max` (auto: 64 MiB, above it within the memory budget, which retained data is charged to); shared fairly, 64 KiB per stream direction always available | Sections 6.4, 6.5 |
| keep-alive | 15 s, capped at max_idle_timeout / 2, waits drawn from [0.75 k, k] | SPEC 6.4 |
| idle timeout | 30 s | |
| handshake timeout | 10 s | |
| linger | min(2 s, max(250 ms, 3 x SRTT)), started when CONNECTION_CLOSE has been handed to every carrier, not at the close command: a sans-IO core driven by clock-less commands starts it on the write that completes step 1 | SPEC 6.5 |
| preamble limit advertised | 4096 | EXTENSIONS 4.1 |
| datagram queues | 64 to send; 128 received per session, 32 per stream, 1 MiB of received datagrams in total | EXTENSIONS 6.5 |
| multipath acknowledgement | 64 KiB or 25 ms (10 ms low latency); copy on a second pipe every 50 ms | MULTIPATH 6 |
| pipe acknowledgement (no multipath) | `zmux_pipe_ack` always sent; in use with a zmux peer: PIPE_ACK on pipe 0 every 64 KiB or 25 ms, in records sent anyway when possible; nothing retained | MULTIPATH 3.5 |
| failure detection | T_suspect = max(250 ms, 2 x (PTO + queue)); T_fail = max(2 s, 8 x (PTO + queue)) when another pipe is ACTIVE; the last pipe of a direction fails after T_dead = max(T_suspect, dead_timeout), where dead_timeout (`DeadTimeout`, at least 1 s) defaults to T_fail with resume and to max(T_fail, half the idle timeout) without (15 s) | MULTIPATH 7.1 |
| idle-pipe probe | 15 s (scheduler parameter `probe_interval` and per-pipe `ProbeInterval` for pipe pairs; at most a 15 s constant for pipes whose attachment has no outbound direction at the peer; measured to the carrier; jitter `probe_jitter` / `ProbeJitter`, 0.25; answered on the probed pair with a PIPE_PING of its own after max(1 s, T_suspect) idle); standby pipes and pipes of a less-preferred tier every `standby_probe`, 1 s, while the session is busy (a traffic frame, QX_PING excluded, within the 15 s busy window), otherwise at the probe interval with one PIPE_PING each when traffic resumes | MULTIPATH 7.1 |
| inbound silence | an OPEN inbound pipe of an attachment without a local outbound direction fails after 15 s + n_in / 8 s + max(2 s, 2 x SRTT_max, G_p - 15 s) without a complete record (17.25 s with two pipes); the last live inbound pipe waits at least T_dead | MULTIPATH 7.3 |
| write call size | one batch; while latency-sensitive work waits (input pending, control, urgent or small-stream frames queued, or either, acknowledgements and credit aside, within max(10 x B, RTT_min)) clamp(R x B / 2, 16 KiB, 64 KiB), write-through included (always in low-latency mode); non-blocking carriers driven by writability: what the carrier takes, within that; blocking adapters in multipath sessions also max(4 KiB, R_p x 50 ms) | Section 4, MULTIPATH 7.1 |
| reorder budget | min(peer session credit, 2 x sum R x (OWD_max - OWD_min) + chunk) | SCHEDULER 13.2 |
| retransmit budget | clamp(4 x sum R x (SRTT + max_ack_delay), 1 MiB, send_buffer max) | SCHEDULER 13.1 |
| spread chunk | 16 KiB | SCHEDULER 5 |
| max pipes | 8 attachments per joiner when multipath is used (the API default 1 means multipath off), no upper limit; the peer may keep max_pipes + H unretired (and so live) attachments, H = 8; replay window max(64, 2 x (max_pipes + H)) IDs | MULTIPATH 4.3, 5.4 |
| resume grace | set by the application (for example 60 s for long-lived gateway sessions) | RESUME 3 |
| suspended sessions per listener | 1024, 256 MiB of buffered data in total | RESUME 9 |
| unauthenticated carriers | 64 per listener, 8 per source, first record within 2 s | MULTIPATH 5.6 |

### 9.2 Targets

Single pipe, Linux loopback, both ends on one host, relative to raw TCP on the
same host:

| Metric | Go | Rust (tokio) | Java | Python |
|---|---|---|---|---|
| one stream, 64 KiB writes | >= 6 GB/s and >= 40 % of TCP | >= 8 GB/s and >= 50 % of TCP | >= 4 GB/s | >= 2 GB/s |
| CPU seconds per GB, both ends, above raw TCP on the same host | <= 0.10 | <= 0.07 | <= 0.20 | <= 0.35 |
| 64 B request-response p50 | <= TCP + 4 us | <= TCP + 3 us | <= 20 us | <= 30 us |
| 64 B request-response p99 while a bulk stream runs on the same session (TCP loopback), with the bulk stream at >= 90 % of its throughput without the request-response traffic, relative to p99_idle, w and Q measured in the same run (below); CI gate in parentheses | <= p99_idle + w + 1.9 x Q (p99_idle + w + 2.4 x Q) | same | the Rust bound + 300 us (+ 375 us; a platform limitation: the JDK cannot set TCP_NOTSENT_LOWAT) | as Rust |
| steady-state allocations | 0 per frame | 0 per frame | <= 1 object per MB | - |
| tasks of an idle session (single pipe / two pipe pairs, one standby) | <= 2 / <= 4 goroutines | 1 task | no dedicated thread | as Rust |
| 10 000 quiet sessions, CPU of both ends together (single pipe / multipath as in the row above; no idle timeout, no keep-alive, default probes; the runtime's default number of workers) | no timer firing, <= 0.1 % of one core / <= 5 % | same | same | as Rust |
| one stream at 20 ms RTT, 1 Gbit/s | 80 % of the link within 2 s | same | same | same |

Footprint, one bound for every implementation (both ends together, with the
measurement method below):

| Footprint | Bound |
|---|---|
| idle session, one TCP pipe | <= 17 KiB (17 408 B) |
| idle stream (opened by one end, accepted by the other, nothing buffered) | <= 1.5 KiB (1536 B) |
| idle multipath session (two pipe pairs, one of them standby, after the join) | <= 44 KiB (45 056 B) |

Each bound is the worst measurement of a conforming implementation with a
small margin, over every platform measured. A footprint run measures one row at a
time, after the sessions of any previous phase have closed and their memory
has been released, so that freed memory of other sessions is not subtracted; an
implementation that measures more is out of bounds whatever its language. A
bound is lowered when every implementation that has measured the row fits a
lower one; it is not raised for a new implementation. Separately, each
implementation's CI records its own measured values as its regression
baseline: the measurement is nearly deterministic, so growth beyond the run-to-run spread is
a regression even below the bound, and the baseline moves down with every
improvement. The value of that baseline is the implementation's own, not a
target of this guide; the CI gate of Section 9.3 item 2 compares with it.

Carriers are not counted in the rows; on TLS or QUIC the carrier, not the
session, dominates an idle connection's memory. Resident memory depends on
the allocator (some allocators hold freed pages for a long time), while live
heap does not, which is why the rows count live heap, not RSS.

The rows of the first table are engineering targets for the architecture of
Sections 2 to 4, set from what conforming implementations measured; the
absolute gates are those of Section 9.3. The CPU row is stated above raw
TCP on the same host because the carrier's own cost is not the
implementation's; the Rust row asks for the path without copying and a
driver of about 0.02 s/GB above the core's own cost without copying.

**Relative latency target.** The latency-under-load row is the regression
test for window growth (SPEC.md Section 8.3 rule 6) and for the driver
structure of Sections 2.3 and 4. It is stated relative to three quantities
measured in the same run, on the same host, with the same runtime, worker
count and core placement as the row itself, so that it means the same on
any host and asks nothing of the host's configuration:

- p99_idle: the 64 B request-response p99 of the implementation over the
  same carrier and session configuration with no bulk stream. It counts at
  most 2 x p99_tcp, the raw TCP 64 B request-response p99 of the same
  harness, so that a regression of the implementation's own idle path does
  not loosen the row (that path is gated against raw TCP by Section 9.3
  item 2).
- w: the host's wake-up p99, from a minimal cross-thread wake ping-pong in
  the implementation's language and runtime. Two threads, or two tasks on
  different workers of the runtime the driver uses (Go: two goroutines and
  an unbuffered channel each way; Rust: two tokio tasks and a `Notify` each
  way on the multi-thread runtime of the measurement; Java: two platform
  threads with `LockSupport.park` and `unpark`; Python: the event-loop
  thread and a second thread that wakes it with `call_soon_threadsafe`),
  take turns: the side that holds the turn spins for 2 x Q, reads the
  clock and wakes the other, which reads the clock as soon as it runs; w
  is the p99 of the difference over at least 100 000 wake-ups. The spin
  keeps the waking core busy while the woken side stays blocked about as
  long as a request waits under bulk, so deep idle states, frequency
  scaling and background load show in w as they do in the row; the
  ping-pong uses no zmux code.
- Q = max(B, K x RTT_min): the window span that the rule of Section 6.2
  keeps (Q = B on loopback), from the configuration and the carrier's
  RTT_min, not from the measured window, so that a window that grows too
  far is not absorbed into the bound (CONFORMANCE.md AT-2 checks
  W / R <= 2 x Q).

The target p99_idle + w + 1.9 x Q has two parts. 1.9 x Q is the structural
queueing term of the relation below, c x W / R with W / R about Q. w is what the host
adds under bulk: there the echo task and the request-response client sit
blocked for about the queueing time between messages instead of the
microseconds of an unloaded ping-pong, so their wake-ups cost what w
measures, which p99_idle hardly shows; one w and not two, because the two
wake-ups rarely both reach their p99. On a quiet host (p99_idle about
13 us, w a few us) with the default B = 100 us the target is about 205 us
and the gate about 255 us. A run reports p99_idle, p99_tcp, w, Q, the
host's load average, its core count and the runtime's worker count next
to the p99; no other host condition applies. The row is measured with at
least as many runtime workers (threads, tokio workers, GOMAXPROCS) as the
two ends have busy tasks together (at least 6: two session drivers, the
bulk writer and reader, the echo and the request-response client), or
with the ends in separate processes, so that languages compare their
drivers and not their CPU scheduling. A deployment that needs margin
lowers the budget B. Regressions stay visible because Q comes from the
configuration, not from the measured window: a window that grows too far
(a fixed 16 MiB window, or one grown from an RTT that includes queueing)
lands far above p99_idle + w + 2.4 x Q.

**Latency under load against single-stream rate.** On one carrier, a
message sent while a bulk stream runs waits behind the bulk bytes already
in the carrier and in the receiving stack, and nothing a sender or
receiver decides afterwards can move it ahead of them. With the window
rule of Section 6.2 on a path whose K x RTT_min is below B (loopback, a
LAN) the bulk stream's window is about R x B, so:

    bulk rate  ~ min(path rate, W / L_credit)
    p99        ~ p99_unloaded + c x W / R,   c about 1.5 to 2

where L_credit is the credit loop (the reader's wake-up, the credit
update, the sender's wake-up) and W the window. The bulk stream keeps its
rate only while L_credit <= B, and every byte of window beyond that is
queue in front of the message. The relation is structural, not a rule to improve: a smaller
window idles the bulk stream whenever a wake-up is late, a larger one
queues more ahead of every message, and a window that shrank only while
latency-sensitive work is pending would act too late, because the bytes
ahead of a request are in the carrier when it arrives. The single-stream
row (bulk alone) and the latency-under-load row (bulk at >= 90 % of its
own rate) are therefore reachable together only where L_credit and the
unloaded p99 are well below B. Neither row asks for a prepared host. The
latency row is relative (above). The single-stream row needs no other
relative form than its own: its "% of TCP" clause and the CPU row are
relative to raw TCP on the same host, and its gate (Section 9.3 item 2) is
the ratio to raw TCP in the same run. Its absolute clause stays an
engineering target of the architecture, reachable by the relation where
L_credit <= B; a report gives w (measured with Q = B) next to it, so that
a shortfall on a host whose w is a sizeable part of B can be told apart
from one in the implementation, without lowering the target.
A deployment picks its point with B (larger for bulk rate, smaller for
latency); traffic that needs both carries its latency-sensitive streams on
a carrier of their own (another session, or a pipe that bulk streams do
not use), whose queue the bulk stream does not fill. There is no separate
window-span parameter: B is the span, and at rates above 1.3 GB/s the
write-call bound is at its 64 KiB clamp anyway, so a span set apart from
B changes only the latency window of Section 4 (10 x B).

**Footprint measurement.** The footprint rows are measured so that every
implementation reports comparable numbers: create N idle sessions (or N
idle streams on one session), both ends in one process, each session over
its own TCP loopback connection or over an in-memory pipe, wait until every
handshake (or every stream's opening) has completed and every transient
buffer has been returned (idle pipes give their read and write buffers back
after about a second), force a full collection where the runtime has one,
and divide the growth of live heap bytes, both ends together, by N. N is
10 000, or at least 200 after a warm-up of as many that has been released
again (so that process-wide state, such as the runtime's and the shared
buffer pool's, is not charged to the sessions). What counts is everything
allocated for the session or stream and still reachable from it: core
state, stream tables, queues, timers, pipe state, any per-session driver
objects, and the carrier's user-space state (the socket objects of a TCP
connection; an in-memory pipe and its buffers). Where an in-memory pipe
costs at least a TCP connection's user-space state, a measurement over
in-memory pipes bounds the TCP row; otherwise the row is measured over TCP loopback. What is
excluded: kernel memory (socket
buffers); buffers returned to pools shared across sessions (they are reused,
not owned); runtime and allocator caches; and task or goroutine stacks at
their minimum size, which are reported as a count instead (the tasks row of
the first table). Carriers with state of their own (TLS, QUIC) are not
footprint rows. What is
measured is live heap, never resident memory, which depends on the
allocator (above). Per language: Go, `runtime.GC()` then
`runtime.ReadMemStats` `HeapAlloc`; Rust, a counting global allocator's live
bytes as requested (layout sizes, so the result does not depend on the
platform allocator's size classes; glibc malloc on Linux underneath); Java, live-heap
bytes after `System.gc()` from the heap histogram, with the JDK 25 defaults
(compressed oops, compact object headers when enabled); Python, the binding's
native heap through the same counting as Rust plus `tracemalloc` current size
after `gc.collect()` for the Python objects. Implementations publish the
number together with the carrier, N, the allocator and the method they used.

**Quiet-session measurement.** The idle multipath row uses the method
above with N = 10 000 sessions that each joined a second pipe pair as
standby. The CPU row runs N = 10 000 sessions of each kind over in-memory
pipes, both ends in one process, with `max_idle_timeout` 0 and keep-alive
off (so that no session idles out during the measurement), lets them be
quiet for 30 s (so that the
standby probing of MULTIPATH.md Section 7.1 has fallen back to the probe
interval), then measures the process CPU time over the next 60 s. Single-pipe
sessions without keep-alive have no timer that fires in that minute, so
their row is near zero by construction; multipath sessions do what
MULTIPATH.md Section 7.1 asks, about 1 333 PIPE_PING and 2 000 PIPE_ACK
records sent per second per end with two pipe pairs each (the answer to a
probe of the standby pair also returns on that pair, MULTIPATH.md Section
6.5), and the bound allows about 3.7 us of CPU per record sent or
received. Implementations also report the
timer firings per session per minute (single pipe 0; multipath at most 8
per end, the probes of two pipes), and the same rows with the default keep-alive
and idle timeout (15 s and 30 s), which add one QX_PING request and
response per session every 11.25 to 15 s (and in multipath sessions the PIPE_ACK
they elicit) but no standby probing. Section 2.8 gives the structure that
meets these rows.

Multipath (CONFORMANCE.md Section 4.5 lists the scenarios and their exact
targets). Scenario unless stated: pipe A 100 Mbit/s with 5 ms one way, pipe
B 20 Mbit/s with 50 ms one way, reverse pipes mirrored.

**Affinity:**

- 16 streams aggregate >= 95 % of total capacity with a Jain index >= 0.95,
  also over the three tiered pipes of CONFORMANCE.md Section 4.5;
- zero reordering;
- one bulk stream is bound to pipe A once both pipes have RTT samples
  (provisional binding, SCHEDULER.md Section 7);
- failure stall <= (last acknowledgement in flight) + T_suspect + reinjected
  bytes / surviving rate + surviving OWD, and urgent messages delayed by
  little more than the surviving pipe's one-way delay;
- with tiers, flow completion no worse than 1.1 x `affinity` on the
  preferred tier alone;
- no acknowledgement frame on a single pipe without multipath unless pipe
  acknowledgement is in use, and then at most 0.1 % of the goodput in
  acknowledgement bytes (CONFORMANCE.md S-9a);
- with pipe acknowledgement, a single pipe on a carrier that reports neither
  unsent bytes nor a low-water mark meets the limiter targets of the
  multipath rows (CONFORMANCE.md Q-1s to Q-7s).

**Spread mode** (`spread-eta`):

| Metric | Target |
|---|---|
| one bulk stream throughput, plus the redundant copies of the urgent stream | >= 99 % of aggregate (>= 118.8 Mbit/s) |
| 16 bulk streams | as above, Jain index >= 0.99 (every spreading policy) |
| urgent 1 KiB message every 10 ms, p99 | <= affinity p99 + 1 ms (<= 8.7 ms) |
| urgent messages with every pipe stalling 6 times a minute for 50-300 ms, p99 / p99.9, mean over 10 seeds | <= 45 / 100 ms |
| carriers that report neither unsent bytes nor an RTT (a 4 MiB hidden buffer on the fast pipe; 1 MiB on both): urgent p99, goodput | <= 30 ms; goodput plus copies >= 97 % of 120 Mbit/s |
| one pipe of 1 Gbit/s x 100 ms whose carrier hides 64 MiB | >= 98 %, urgent p99 <= one-way delay + 30 ms |
| receiver reorder memory, one bulk stream | <= 1 MiB |
| retained data, two pipes | <= 512 KiB |
| finite flows at 60 % load, flow completion p99 for 16 KiB / 256 KiB / 4 MiB, 10 seeds | mean <= 30 ms / 190 ms / 2.05 s, maximum <= 45 ms / 300 ms / 3.65 s |
| 1 MiB session window | never below the fastest pipe alone (>= 99 Mbit/s) |
| 1 Gbit/s + 1 Gbit/s, 50 ms one-way spread, 32 MiB window | >= 99 % of aggregate |
| failover of the pipe carrying data, over 12 failure phases | stall <= (last acknowledgement in flight) + T_suspect + reinjected bytes / surviving rate + surviving OWD, and <= 400 ms; urgent p99 <= 80 ms |
| duplicate bytes after a failure, beyond the redundant copies | <= unacknowledged bytes of the failed pipe |
| first message after the pipe it goes to failed while idle | <= the other pipe's one-way delay + 15 ms |
| pipe B slows from 20 to 0.2 Mbit/s, over 12 phases | never below the fastest pipe alone |
| graceful pipe removal | 0 reinjected bytes, no stall |
| adding a pipe | first data on it within one RTT of the join plus its probation (4 x SRTT for the first join of a path without history, otherwise at least 1 s); within one RTT when no other pipe is usable or `Probation` is 0 |
| a pipe of equal rate joins while the first carries a bulk stream | >= 97 % of the aggregate |
| two pipes over loopback | throughput >= 90 % of one-pipe affinity; CPU per GB <= 1.15 x |
| split carriers, 2 uplinks + 2 downlinks | per-direction aggregate >= 95 % of capacity |

The estimates converge to the true values within known biases: the
windowed maximum picks the high tail of noisy rate samples, and the 300 s
min_rtt filter keeps a pipe's queue-free minimum through a whole bulk
transfer, so that the standing carrier queue of a busy pipe does not enter
min_rtt. After a rise of the path delay the filter keeps the old minimum for
up to 300 s; the queue term of the arrival estimate and the rate estimate
carry the change.

**Tiered pipes** (SCHEDULER.md Sections 6 and 10; the T scenarios of
CONFORMANCE.md Section 4.5: A 50 Mbit/s / 30 ms and B 30 Mbit/s / 40 ms at
priority 2, C 100 Mbit/s / 20 ms at priority 5, overflow delay D 1 s):

| Metric | Target |
|---|---|
| 40 Mbit/s offered: data on the less preferred tier | <= 1 % |
| 40 Mbit/s offered, `spread-round-robin`, weights 5 and 3: A / B shares | within 1 point of 62.5 % / 37.5 % |
| 150 Mbit/s offered: preferred tier used | >= 95 % of its 80 Mbit/s, >= 97 % of the offered rate delivered |
| finite flows at 15 / 29 / 45 % of the preferred tier, 10 seeds: data on the less preferred tier, mean / maximum | <= 0.5 / 1.5 %, 1.25 / 3.5 %, 3.5 / 6.5 % |
| 16 MiB bursts | the less preferred tier takes no more than the burst minus R_1 x D; completion <= 1.45 s |
| failure of A, then of A and B, 12 phases | stall <= 400 ms |
| A and B recover | less preferred tier <= 1 % from 2 s after the recovery |
| A flaps every 2 s (every 1 s; irregularly) for 50 s | no flapping of the traffic; A used again within about twice its longest up period after the flapping ends |
| reverse priorities differ from forward ones | acknowledgement and credit frames follow the reverse tier |

**High bandwidth-delay paths** (the H scenarios of CONFORMANCE.md Section
4.5): one stream reaches the path rate on long, fast paths with the default
window caps (H-1, H-6) or the larger caps of H-2, two 1 Gbit/s pipes with 50 and 100 ms one way aggregate the
path rate with the default send buffer maximum of 64 MiB, and failure handling scales with the RTT.

`spread-eta` puts most of a light load on the pipe with the lowest arrival
time and `spread-rate-weighted` splits it by measured rate; only
`spread-round-robin` divides it by configured capacity. The overflow delay
D is a cost-against-latency setting: a larger D keeps more data off the less
preferred tier at the price of completion time. Probation costs a pipe that
joins a path without history only 4 x SRTT.

### 9.3 Benchmarks and gates

Each implementation runs these benchmarks:

1. micro-benchmarks on every commit: frame codec (ns per frame and
   allocations), stream selection, flow-control accounting, retention and
   acknowledgement release. A median regression above 15 % fails; any extra
   allocation fails;
2. loopback end-to-end nightly on a pinned Linux runner, five runs, median,
   always against raw TCP on the same run: throughput ratio -10 % fails
   (-5 % warns), p50 +15 % and p99 +25 % fail, memory during bulk
   transfer +10 % fails; the footprint rows of Section 9.2 fail above their
   bound and, against the implementation's own recorded baseline, on
   growth beyond the run-to-run spread;
3. emulated WAN nightly (`tc netem`, 20 and 100 ms RTT at 1 Gbit/s): the
   auto-tuning scenarios H-1' and AT-1 to AT-6 of CONFORMANCE.md Section 4.8;
   the latency-under-load gate is the relative one of Section 9.2,
   p99_idle + w + 2.4 x Q (target p99_idle + w + 1.9 x Q) for Go and Rust,
   with p99_idle, w and Q measured in the same run on the runner;
4. multipath nightly: the implementation's own multipath harness runs the
   scenarios of CONFORMANCE.md Section 4.5, plus real two-pipe runs, with
   the targets of Section 9.2; the affected rows run on every change to
   scheduler or estimator code, and every row with the seed counts of
   CONFORMANCE.md Section 4.5 before a release;
5. cross-implementation matrix weekly.

Gates run on Linux only; macOS results are recorded.

---

## 10. Testing

CONFORMANCE.md lists what every implementation runs: traces, corpus, fuzzing,
differential tests between the Go and Rust cores, the compliant-floor stress
test and the multipath scheduler tests. Trace runners use virtual time (Go
`testing/synctest`, a manual clock elsewhere).

---

## 11. Observability

- Statistics (API.md Section 7) are returned synchronously, with the
  consistency API.md Section 7.2 states and no more: counts may be relaxed
  atomics read without a lock, and the state fields come from one point of
  the core state (Java: published by the loop at the end of each round and
  read without a hand-off).
- Events: pipe added, joined, status changed, suspect, recovered, failed,
  closed; session suspended and resumed; GOAWAY received. Event queues are
  bounded and coalesce per pipe; terminal events of retired pipes are folded
  into a session-level summary, never dropped (Section 2.5.7).
- qlog: implementations SHOULD export the QUIC qlog events for streams and
  frames (`transport:frame_sent`, `transport:frame_received`,
  `transport:parameters_set`, `recovery:metrics_updated`) plus zmux events
  `zmux:pipe_state_updated`, `zmux:pipe_ack`, `zmux:reinjection`,
  `zmux:session_suspended` and `zmux:session_resumed`. The schema is reserved
  and will be fixed in a later revision (REGISTRY.md Section 7).

---

## 12. Security notes

- Compare join tokens in constant time.
- Hold unauthenticated carriers only within the limits of MULTIPATH.md
  Section 5.6, and never touch session state before the token verifies.
- Exporters: Go `tls.ConnectionState.ExportKeyingMaterial`; Rust rustls
  `export_keying_material`; Java `ExtendedSSLSession.exportKeyingMaterialData`;
  Python's `ssl` module has none, so Python applications either use the
  Rust-side TLS adapter or supply a PSK, for example one derived per
  session from their own authenticated exchange.
- Treat PSKs as secrets: zero them after the session secret
  is derived when the language allows it.
- Bound the listener's pairing table for split bootstraps (SPEC.md Section
  6.1.1).

---

## 13. Pitfalls

Each entry names a common implementation mistake, what it looks like, the structure that avoids it and the section the rule
comes from. Where a rule is already stated elsewhere, the entry only points
to it; a MUST or SHOULD here restates what that section already requires.
The labels (W1, C1, ...) are what CONFORMANCE.md Section 4.9 uses to list
the regression test or review item for each entry. Numbers are given only where they are the evidence.

### 13.1 Concurrency and wake-ups

- **W1. A waiter registered after its check.** A wait that checks its
  condition, releases the core lock (to read through, to copy outside the
  lock, to call a carrier) and only then registers itself misses every
  change in between; when the change is the session's end, nothing ever
  wakes it. Symptom: a read that hangs after the session closed, about once
  in 10 to 50 runs of a carrier-failure test under load. Structure: a waiter
  MUST check its condition and register in one critical section; a path
  that released the lock after a check checks again under the lock before
  it registers (Section 2.5.4). The protocol is small enough for a model
  checker of the memory model, which finds this race at once.
- **W2. A wake-up source missing.** Every kind of wait needs the list of
  all inputs that can make its condition true, and each of them evaluates
  the condition. The send-capacity wait (API.md Section 5.1) also holds
  when the part's unsent bytes drain while some capacity is left, which no
  credit frame announces: a wait woken only by MAX_DATA and MAX_STREAM_DATA
  sleeps until the peer happens to send credit. The opposite mistake, waking
  a waiter with a `min` on every credit change, costs a wake-up per credit
  update instead of one per chunk. Structure: evaluate the condition at
  every input that changes one of its terms (credit, unsent bytes, stream
  and session state) and wake only when it holds (Section 2.5.4).
- **W3. One wake-up slot for two waiters.** Readiness of a pipe routed to
  one slot that the readers of two streams share loses wake-ups: one
  reader's hand-back clears the other's registration. Symptom: stalls of
  about 1 s, until an unrelated timer ran the session. Structure: one slot
  per waiter (Section 2.5.4); a fast path that finds data for another
  stream wakes that stream's reader.
- **W4. A wait for an answer that cannot come.** QX_PING is not reinjected
  (MULTIPATH.md Section 7.4), so an application ping whose request or
  response was lost with a failed pipe waits forever. Structure: a request
  stops being outstanding when its pipe fails or after max(1 s, 3 x SRTT)
  (SPEC.md Section 4.5), and a ping still waiting then asks again; every
  wait for a peer's response has pipe failure among its causes.
- **W5. The driver waiting for a side that the application holds.** With
  read-through and write-through (Section 2.3) an application thread holds a
  carrier side for up to one read buffer or one write call. A driver
  context that blocks on acquiring that side services nothing else
  meanwhile: in one implementation the reply to a request waited 110 us at
  p99 behind it and 24 us once the driver only tried to acquire the side.
  Structure: the driver never blocks on a side held by a fast path; it
  tries, and the holder hands back what arrived while it held the side when
  it releases it (Section 2.3); a holder that panics or throws returns the
  side (Section 2.5.6).
- **W6. Application code under the lock because it does not block.** A
  limiter's `try_ready` and `charge` do not wait, but a limiter shared by
  several sessions takes its own lock or calls a session; called under the
  session lock it deadlocks (AB-BA) against a thread that holds the
  limiter's lock and calls that session. The same holds for meters,
  predicates and tracers. Structure: no application-supplied function runs
  under a zmux lock (Section 2.5.2): `try_ready` before taking the lock,
  `charge` after releasing it, received datagrams policed on the driver
  outside the lock (API.md Appendix A.1).
- **W7. Several critical sections per wake-up.** A driver wake-up that
  locks the core separately for housekeeping, carrier probes, each read
  result, the write-call limit, each batch, each custom decision and the
  next deadline makes 6 to 8 lock round trips; with two pipes and four
  streams those round trips were 17 % of the core-lock contention.
  Structure: per wake-up, prepare the I/O without the lock (read
  into buffers, query the carriers), then one critical section that feeds
  every input, runs the timers and housekeeping, takes every batch, the
  custom-policy requests and the next deadline, then write, flush and wake
  after the unlock; only answers and write failures need a second section,
  which can join the next round. The loop runs again only when the round
  produced new input or output. An application read takes as many buffered
  pieces under the lock as its buffer holds, not a small fixed number.
- **W8. Busy re-polling.** When `wants_write(p)` is true but
  `poll_transmit(p)` produces nothing, the driver spins: a pipe held back
  by the carrier queue limiter (SCHEDULER.md Section 9.1) reported write
  interest because redundant or opportunistic copies were queued for it,
  and the writer threads of a blocking driver woke each other in a loop.
  Structure: `wants_write(p)` is true only when `poll_transmit(p, now)`
  would produce bytes now, and whatever is due now (frames that must go,
  data the scheduler and the limiter let through, a FIN-only STREAM frame
  of a stream whose credit is used up, SPEC.md Section 8.2) makes it true; what is
  not due yet is covered by a deadline of `next_timeout` instead (a held
  pipe's release time, a PIPE_ACK's acknowledgement delay, a pacing
  allowance). The converse does not hold and need not: a `poll_transmit`
  that runs for another reason may add frames that are pending but not due
  on their own, such as a PIPE_ACK or a credit update below its threshold
  that rides along with data (MULTIPATH.md Section 6.3, SPEC.md Section
  8.3), so the core can produce bytes for a pipe that does not report
  write interest. A held pipe asks only for control frames that are due
  (which are never held back); a queued copy that can no longer be sent is
  dropped when it is checked (Section 8.3); a write opportunity that
  produced nothing is not repeated without a new input or deadline.

### 13.2 Flow control and credit

- **C1. Accounting changed on one exit only.** Received bytes leave a
  stream's storage on many paths: reads, discard after `CancelRead`,
  resets, STOP_SENDING, release, and the trimming of bytes that arrived
  before a stream's preamble and overlap it once the preamble is parsed
  (EXTENSIONS.md Section 4.4). An implementation that dropped the
  overlapping bytes without taking them off the session's buffered count
  kept them counted for ever: the count drifts up with every such stream
  until the session sits at its buffer cap and releases session credit only
  on consumption (Section 6.3). Structure: one function per scope changes
  the buffered and released counts, every path that drops stored bytes goes
  through it, and debug builds check that the session's buffered bytes
  equal the sum over its streams.
- **C2. Credit for bytes that will never arrive.** Bytes lost with a failed
  pipe below a reset's final size are not reinjected (MULTIPATH.md Section
  7.4) and never arrive; without the reset release of SPEC.md Section 8.3
  their session credit is never returned and two healthy pipes can stay
  blocked at the session limit for good. The same accounting covers a
  reset that races the preamble and the reset of unread data: each byte
  releases session credit once. Structure: Section 6.3 (arrival tracked
  also after the data is read or discarded, the released gap recorded).
- **C3. Credit released early or twice.** Bytes beyond a gap release
  session credit when they arrive and the gap releases nothing until its
  own bytes arrive or a reset releases it; duplicates (redundant copies,
  reinjected ranges, MULTIPATH.md Section 7.6) release nothing and are not
  counted again in `bytes_received` (API.md Section 7.2). Releasing for a
  gap at once, or for each copy, grants the peer more than the receiver
  holds room for.
- **C4. A running sum with an exit missing.** The O(1) designs of this
  guide keep running sums: the copy bytes owed of Section 8.3, the small
  holdings and their count of Section 6.4, the offered data X of Section
  8.4, the per-pipe A of `affinity`. A sum that one exit forgets to
  decrement does not crash; a policy silently stops (no more redundant
  copies, a wrong fair share). Structure: each sum has one owner and an
  enumerated list of exits, and debug builds recompute it from scratch at
  intervals and assert equality.

### 13.3 Memory

- **M1. Read buffers kept alive by references.** Where stream data
  references the read buffer (Section 3), a buffer can return to the pool
  only when no piece refers to it. Three mistakes: (a) a buffer the reader
  retired still counted as in use (it kept its length), so reclaiming it
  never succeeded: 80 to 140 MB live during a bulk transfer instead of
  2.2 MB; (b) the twice-the-bytes rule of Section 3 applied only on arrival,
  so that after reads consume most of the referenced bytes the small rests
  keep whole buffers alive; (c) the rule measured with the length of the
  referenced data instead of the size of the buffer it keeps alive; (d) the
  rule applied per stream, which counts a buffer shared by several streams
  once for each and copies out data that the session bound does not
  require (with 8 streams over 4 pipes, 1.1 bytes per byte received, some
  of them pieces copied again).
  Structure: the session-wide rule of Section 3 compares the allocated
  size of the retired buffers kept alive with the bytes referenced in all
  buffers, each buffer counted once, and is checked whenever it could have
  become false, consumption included; the reader gives up its own
  reference before it retires a buffer; retired buffers beyond a small
  number go back to the shared pool (Section 2.8). A test watches live
  heap during a bulk transfer.
- **M2. Memory per range.** One bookkeeping node per out-of-order range
  costs more than the data under deliberate fragmentation: 925 KB for
  16 KB of consumed flow control in one measurement. Fragmentation is not
  chargeable (SPEC.md Section 9.4.1), so the representation of Section 3
  (copied small pieces, the flat store beyond an eighth of the span) is the
  only bound (SPEC.md Section 9.8).
- **M3. Allocation churn on hot paths.** A container that frees its
  storage whenever it becomes empty and allocates again on the next use
  allocates per chunk: the per-tag sets of a scheduling ledger did so each
  time a stream direction left its queue, about 4800 allocations per 128
  MiB. The target is no allocation per frame (Section 9.2). Structure: keep
  emptied containers (drop them only beyond a size bound), pool chunks and
  batch buffers, and test the bulk paths, multipath with acknowledgements
  included, with an allocation counter.
- **M4. Growth policies in idle paths.** Growth that suits bulk transfer
  wastes memory and CPU in quiet sessions: a table that doubled its room
  held space for four attachments while using two, 3.1 KB per session pair
  at 10 000 multipath sessions; a transmit buffer that, after shrinking
  while idle, reserves a whole batch (256 KiB) for each small probe record
  pays a large allocation per probe. Structure: tables of a quiet session
  grow by exactly what an insertion needs; a buffer for a small record is
  sized to it, and batch-sized buffers come from the shared pool only when
  a batch is built (Section 2.8).
- **M5. Self-reported memory counted twice.** An implementation that sums
  its memory field by field counted nested objects twice (10.9 KB reported
  for an idle session that held 7.1 KB). Such a figure feeds the memory
  budget (Section 6.3, API.md Section 4.4) and the footprint rows of
  Section 9.2. Structure: every object reports its own heap bytes once (the
  allocated size, not the length in use), a shared object is counted by one
  owner, and a test compares the report with the allocator's live bytes.
- **M6. Resources that one exit path keeps.** Leaks found so far: carriers
  of retired or failed attachments never closed; a pending PIPE_ABANDON
  flag that survived the pipe's failure, so that the attachment never
  retired; structures of the application fast paths holding strong
  references to carriers after the session ended; cancelled waiters left in
  waiter lists. Structure: Sections 2.5.5 and 2.5.7 (every exit path
  releases, waiter lists drop cancelled waiters at once, carriers are
  released with the session); fast-path structures refer to the session's
  carriers without keeping them alive.

### 13.4 Timers

- **T1. Clock overflow.** A duration configured as very large (the
  language's maximum, used to mean "never") added to the current time
  overflows the clock type and wraps or aborts. Structure: every deadline
  is computed with a checked addition, and one beyond the clock's range is
  "never": absent from `next_timeout`, never compared as a time. Duration
  arithmetic saturates. This extends the rule of Section 2.5.6 for peer
  values to configuration.
- **T2. A deadline that `next_timeout` forgets.** A state that has a
  deadline but is missing from the earliest-deadline computation waits for
  an unrelated input: a pipe stayed in probation (SCHEDULER.md Section 11)
  until traffic arrived. Structure: every deadline source updates the
  session's deadline; where the earliest deadline is cached (Section 2.8),
  debug builds compare the cached value with a full recomputation at every
  call.
- **T3. Lateness thrown away.** Drivers wake late: a timer armed G ahead
  fires on a tick of the runtime's clock, later still on a busy host. A
  pacing token bucket capped at one quantum (SCHEDULER.md Section 9.1)
  discards what accrued during the lateness; the pipe then sends below its
  pacing rate, its rate samples fall below R_p, and R_p decays round by
  round while the RTT stays at the base: 100 Mbit/s x 100 ms fell to 33
  Mbit/s after 12 s with timers 0.5 ms late, and 10 Gbit/s x 100 ms from 9.8
  to 1.2 Gbit/s. Structure: the allowance accrues with the time that
  actually elapsed, and the bucket holds up to twice the quantum of
  SCHEDULER.md Section 9.1, so what accrued while a wake-up was late is
  sent, not discarded; a released pipe sends whole chunks, not the few
  bytes refilled since its last batch. Above the 2 x `spread_chunk` floor
  the quantum is gain x R_p x G, so the cap is 2 x G of accrual at every
  rate, and a wake-up up to G late loses nothing at 100 Mbit/s or at 10
  Gbit/s alike; a driver whose wake-ups are later than that is coarser than
  its G, and a larger cap would only hand the carrier a larger burst.
- **T4. Periodic passes over every session.** A listener that visits each
  of its sessions every second (housekeeping, memory allowance) makes quiet
  CPU proportional to the number of sessions; it is one of the two
  measured excesses over the quiet-session row of Section 9.2. Structure:
  listener accounting changes when a session's charge changes (atomics or
  bounded queues, Section 2.5.1), and only real timers run (Section 2.8).

### 13.5 Estimators and scheduler

- **E1. Stale send times in merged sample entries.** A bounded table of
  sample entries (one per write batch in flight, Section 2.6) merges
  batches when it is full. An endpoint that only receives sends records of
  PIPE_ACK alone for minutes; when its first ack-eliciting batch was merged
  into an entry that began then, it inherited that old send time and gave
  an RTT sample of minutes. Structure: an ack-eliciting batch never takes
  the send time or delivery state of non-eliciting batches before it; the
  entry keeps its start position (so that the positions it covers still
  give rate samples) but its send time and delivered state restart; RTT
  samples use the send time of the last acknowledged byte (SCHEDULER.md
  Section 2.1).
- **E2. Samples from another epoch.** A sample measures the state in which
  its bytes were sent. Mistakes: samples of bytes sent before the pipe
  became SUSPECT, while the sender had no live inbound pipe or before a
  suspension (SCHEDULER.md Section 2.1); samples of the bytes in flight
  when a base refresh began, which carry the queue it drains (Section 9.1);
  estimates carried over to a new pipe or a new carrier (SCHEDULER.md
  Section 2.1; `loop_min` restarts with a new carrier, Section 6.2);
  QX_PING samples mixed into a paced pipe's base once PIPE_ACK gives
  samples (only the first record's request may give its first sample).
  Structure: each sample entry records the epoch it was sent in (pipe
  incarnation, suspicion, suspension, probe start) and the acknowledgement
  discards it when the epoch has changed.
- **E3. "In flight" counted with bytes that elicit nothing.** Records of
  PIPE_ACK, PADDING or other frames that elicit no acknowledgement are
  acknowledged only together with later ack-eliciting bytes, if ever.
  Counted in flight, they filled the in-flight cap of the carrier queue
  limiter of an endpoint that only received (its R_p still the initial
  1 MB/s): after a few seconds of receiving at 1 Gbit/s its pipe was held
  for good and data written then never left. The same applies to every
  "nothing in flight" test (SCHEDULER.md Sections 2.3 and 9.1). Structure:
  bytes in flight end at the end of the last ack-eliciting byte sent, as
  RFC 9002 Section 2 counts them (SCHEDULER.md Section 9.1).
- **E4. Per-chunk work that grows with the queue.** A spreading loop that
  after every frame recomputes the tier gate and the decision, scans for
  lost data, rescans the stream queues from the top and removes entries by
  search costs O(queue) per chunk; one implementation measured 0.064 s/GB
  of core time with `spread-eta` on one pipe against 0.022 without
  multipath. At
  10 Gbit/s (about 76 000 chunks per second, Section 6.5) work per chunk and
  per PIPE_ACK has to be O(1) amortised and free of allocation. Structure:
  continue a scan where it stopped, remove by position, reuse the gate and
  the decision until an input that changes them (a pipe state, a penalty,
  a tier saturation; the reuse must give the same choices as recomputing),
  skip empty classes with counters, and log one entry per contiguous run of
  a stream in a batch rather than one per frame.

### 13.6 Write path

- **P1. A decision recomputed from merged inputs.** Splitting the driver
  round (W7) must not change decisions. One implementation recomputed the
  write-call bound of a new batch's first call (Section 4) from the
  "latency-sensitive work is waiting" inputs of two rounds combined, so that
  most bulk calls were bounded: write calls fell to half their size and bulk
  throughput to 90 %. Structure: the bound is decided when the batch is
  taken, from the state at that moment, and travels with the batch; later
  calls of the batch re-evaluate it only from input that arrived since.
- **P2. A frame without room.** The room left in a record counts the
  header of every frame, a FIN-only STREAM frame of zero length included;
  an implementation that let such a frame through when the batch was full
  emitted it outside any record. Structure: a frame that does not fit,
  header included, waits for the next record.
- **P3. Copies of bytes already released.** Redundant and opportunistic
  copies and reinjection read the stream's held bytes. After a reset, bytes
  above the Reliable Size are released; an implementation that copied
  those ranges anyway sent zeros. Structure: a copy covers only bytes still
  held, stops at the Reliable Size, and an owed FIN after a reset is
  dropped (MULTIPATH.md Section 7.4).
- **P4. Input pending read as "a read returned data".** Rule 1 of the
  write-call bound (Section 4) asks whether bytes wait unprocessed, not
  whether the last read round found any. A bulk sender receives
  acknowledgements and credit in every round, so the second reading made
  almost every call of one implementation a bounded one: 64 KiB calls,
  9 400 to 15 500 write calls per GB over 1 to 4 pipes. Asking whether the
  last read filled its room cut the calls to a half to a third (4 600 to
  4 900 per GB, about 210 KiB on average) and raised multipath bulk throughput by 10 to 22 %
  at 9 to 25 % less CPU per GB, with the request-response p99 under bulk
  unchanged. Structure: the reader records whether its last read may have
  left bytes behind (rule 1), and only that feeds the bound.
- **P5. FIN on a partial copy.** A copy that covers only part of what the
  stream direction has left, such as the first chunk after an idle period,
  carries no FIN; only a copy that reaches the final size may (SPEC.md
  Section 7.5, SCHEDULER.md Section 4.1).

### 13.7 Multipath as a layer

- **L1. Multipath work on single-pipe paths.** Section 2.6 states the
  structure. Mistakes found against it: an accessor that allocated the
  side storage when it was only read; single-pipe notifications from the
  driver (writability, unsent bytes) that reached multipath code; limiter
  state allocated for pipes it does not govern (it exists only while a
  pipe is paced); per-pipe tables sized for many pipes. Structure: reads
  of absent side storage return defaults without allocating, storage is
  allocated on the first write after negotiation, and the measurements of
  CONFORMANCE.md Section 4.6 check the result.
- **L2. State from before multipath in multipath detectors.** Write batches
  sent before multipath was in use are never acknowledged by PIPE_ACK;
  counted by the acknowledgement-progress clock of failure detection
  (MULTIPATH.md Section 7.1), they move the moment a pipe is judged suspect.
  Structure: the detectors watch only what was sent while their inputs
  existed.

### 13.8 Lanes

The lanes of MULTIPATH.md Section 2.5 add a few carrier streams to a pipe
and nothing to the estimators; the mistakes below follow from the carrier.

- **N1. Unsent bytes counted by record.** A lane's unsent bytes are what
  its carrier stream has not yet sent, byte by byte as the stack reports
  them. Counting a record as unsent until its last byte has left, as a
  TCP-like model may, holds the bytes of the records that the connection
  is sending in turns across lanes as unsent: the pipe looks full, the
  writer stops and the connection runs dry (12.6 % less throughput for 16
  bulk streams at 10 Gbit/s x 100 ms). Structure: Q_p
  is the sum of the lanes' unsent counts as the adapter reports them
  (SCHEDULER.md Section 9.2); without a count, the limiter governs the
  pipe as a whole.
- **N2. Delivery rate from lane positions.** A sample taken at each lane's
  advance divides bytes sent before the sample interval by an interval
  that begins at another lane's last acknowledgement: when head-of-line
  blocking ends, the released lane's bytes all count at once, and R_p
  overstates the path (126 to 128 Mbit/s for 100 Mbit/s). Structure: RTT and rate samples come
  only from advances of the cumulative acknowledgement point
  (MULTIPATH.md Section 2.5), with the send-order counts of the pipe.
- **N3. Ack stall by lane.** A stall clock per lane, or the longest of
  them, marks a pipe suspect when a lost retransmission holds one lane for
  two or three round trips at 10 Gbit/s while the others advance. Structure: one clock per pipe at the
  cumulative point; a lane that is really dead stops it anyway.
- **N4. QUIC connection window smaller than the lanes'.** The bytes a
  blocked lane has received out of order occupy the connection's receive
  window; when that window is not larger than the stream windows of the
  lanes that can be blocked at once, every lane waits on connection-level
  flow control and lanes degrade to one stream. Structure: the adapter
  sets the connection window to at least the sum of the stream windows of
  the lanes that can be blocked at once (a few, not all L: a lane is
  blocked for about a round trip per loss), charges it to the memory budget
  (API.md Section 4.4), and opens no more lanes than that window covers
  (API.md Section 4.1).
- **N5. Stream credit for lanes.** Each lane is a carrier stream. A QUIC
  adapter that grants the peer a small stream count (QUIC MAX_STREAMS)
  makes lane opening wait; that is not an error, but the pipe then has
  fewer lanes than streams sending. Structure: grant the peer as many
  streams as the lanes the endpoint is willing to carry (within its memory
  budget, and on an outer zmux session within the share API.md Section
  4.6 leaves the application), and count a lane whose stream waits for
  credit as the pipe's one lane not yet confirmed, its handshake timeout
  starting only when the stream opens (MULTIPATH.md Section 5.8).
- **N6. One lane starving the others.** A reader that drains one lane
  while it has data leaves lane 0's urgent messages and control frames,
  and the other lanes' records, waiting. Structure: read the lanes of a
  pipe in turns, at most one batch from each (Section 3); a receiver
  without priorities between them still serves lane 0 every turn.
- **N7. Lanes routed by the connection alone.** A connection can carry
  the streams of several sessions and, through a relay or a proxy, of
  several parties; routing a lane by its connection, or by a carrier stream
  identifier that intermediaries renumber, attaches it to someone else's
  pipe. And a connection's addresses change with connection migration.
  Structure: route by the Lane Key, compared in constant time, then check
  that the pipe's lane 0 is on the same connection, whose identity is the
  stack's connection object (MULTIPATH.md Section 5.8).
- **N8. A record buffer per lane.** Up to L lanes per inbound pipe each
  hold at most one incomplete record. Allocating a `max_record_size`
  buffer for every lane up front costs 16 MiB per pipe at the default L.
  Structure: an incomplete record references the read buffer as Section 3
  describes and is copied out only when the buffer would otherwise be
  kept alive (SPEC.md Section 9.8).
- **N9. A lane moved too early without multipath.** In a session without
  multipath, a stream direction that moves to another lane before its
  frames on the old one are acknowledged can have a later offset arrive
  first, which the peer must treat as a PROTOCOL_VIOLATION (SPEC.md
  Section 7.3). Structure: the binding of SCHEDULER.md Section 9.2 lasts
  until a PIPE_ACK covers everything of the stream direction on its lane
  (a hand-off to the carrier, which SPEC.md Section 7.4 calls acknowledged
  outside multipath, is not enough), which is the condition of MULTIPATH.md
  Section 5.8; control frames stay on lane 0, and an orderly
  CONNECTION_CLOSE waits until the other lanes are covered (SPEC.md Section
  6.5).
- **N10. Data on a lane that may be refused.** A lane opened before the
  peer has attached the pipe, or through an intermediary that splits the
  connection, is refused; data already written on it is lost (outside
  multipath nothing is retained) or, counted as a failure of the pipe,
  fails a healthy pipe. Structure: open lanes only once lane 0 has an
  acknowledged position, put only PIPE_LANE on a lane until it is
  confirmed, and treat a lane that ends before that as refused, not as a
  failure (MULTIPATH.md Section 5.8).
- **N11. Lanes where streams share one byte stream.** Lanes on HTTP/2, or
  on streams of a zmux session over one TCP pipe, recover nothing:
  everything below waits for the same lost segment. Structure: the
  adapter does not open lanes on such connections by default (API.md
  Section 4.1).
- **N12. Lane state outliving the session.** A session's lane state (its
  Lane Key entry, lane requests not yet opened, lanes not yet taken over by
  a reader or writer, claims on carrier connections) is created when a pipe
  that offers lanes is attached or the first lane is asked for; a late
  attach, or a lane stream that arrives after the session has ended,
  created it again in one implementation, where nothing would release it.
  Structure: once the session is CLOSING or CLOSED (STATE_MACHINE.md
  Section 4.1) it takes no new lanes and no new pipes: attaching a pipe
  fails (its carrier is closed) and a lane stream is aborted as MULTIPATH.md
  Section 5.8 rule 1 says, and neither creates state; on CLOSED the session
  releases all lane state and removes its Lane Key entry under the same
  lock that marks it closed.
- **N13. Lanes served in a fixed order.** When the limiter of SCHEDULER.md
  Section 9.1, or the room of the carrier connection, bounds a pipe, a
  round lets one or two batches through. One implementation offered them
  to the writers of its lanes in a fixed order: the first lanes took them
  every round, and a stream direction bound to a later lane waited until
  those had nothing to send. Over a real QUIC connection (100 Mbit/s x 50
  ms, flows at 60 % load, BBR) the 16 KiB completion p99 with lanes was 696
  instead of 147 ms without them, the first bytes of a flow arriving 0.3 to
  0.8 s late. Structure: lane 0 first, then the data lanes in turns, each
  round starting with the lane after the one that last took a batch
  (SCHEDULER.md Section 9.2 rule 4).
- **N14. Held bytes in the limiter's flight.** Counting every byte behind
  the cumulative acknowledgement point, those already acknowledged on
  their own lanes included, fills the in-flight cap of SCHEDULER.md
  Section 9.1 with bytes that have arrived whenever some lane waits for a
  retransmission: on a 10 Gbit/s x 100 ms path with 0.1 % loss start-up
  ended at about a tenth of the rate and the pipe stayed there. Leaving all of them out instead
  lets an overestimated R_p queue at low loss. Structure: the pipe keeps
  its held byte count (acknowledged on their lane, beyond the cumulative
  point; it rises at lane acknowledgements and falls when the cumulative
  point passes them) and the minimum of that count over the last
  RTT_base_p: a monotone deque of time and value, appended at every change,
  whose front is dropped only while the entry after it is at or before now
  - RTT_base_p (the value in force at the window's start counts; dropping
  an entry because its own time is older makes the minimum too lenient
  unless the count is sampled at every limiter decision); the cap, the drain exit and the gain
  cycle subtract the whole count during start-up and the minimum
  afterwards. A pipe without lanes has no held bytes.
- **N15. A carrier adapter that loses the connection's identity.** An
  adapter that wraps a carrier's pipe I/O must keep the identity of the
  carrier connection it wraps, or inbound lanes are never matched to it and
  never accepted (SCHEDULER.md Section 9.2).
