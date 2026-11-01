# zmux API Guidance

Status: informative, cross-language. This document summarises the API of
the zmux libraries and states how each API element maps to the wire. Where
it disagrees with the normative documents on protocol behaviour, the
normative documents win.

---

## 1. Principles

1. **Single-pipe usage looks like the closest QMux or QUIC library.** Go
   follows qmux-go and quic-go (`Dial`, `OpenStreamSync`, `Close` = FIN);
   Rust follows the `qmux` crate and quinn (`Session::connect`, `open_bi`,
   `finish`/`reset`/`stop`); Java uses the same stream verbs
   (`finish()`, `reset(code)`, `stop(code)`) on JDK
   channel interfaces; Python follows the moq binding style (`open_bi`,
   `accept_bi`, `async with`).
2. **Multipath is an additive layer.** It appears only in `AddPipe`,
   `ReservePipe`, the pipe events of the session event stream, the `Listener`, and four configuration
   knobs. Without them, behaviour and wire format are plain QMux. `Pipes`
   and the pipe statistics work on every session alike, a single-pipe
   session reporting its one pipe (Section 4.5).
3. **References are references, not templates.** Conventions that cause
   truncation, hidden blocking, lost error information or racy closing are
   not copied. Examples: no
   implicit FIN on drop, non-blocking `close`, non-blocking datagram send.
4. The carrier is a **Pipe** everywhere, never a "Path".

Toolchain floors: Go 1.27, Rust 1.95 (edition 2024), Java 25, Python 3.12
(IMPLEMENTATION.md Section 1).

---

## 2. Objects

| Object | Go | Rust | Java | Python |
|---|---|---|---|---|
| session | `*zmux.Conn` | `Session` | `ZmuxSession` | `Session` |
| bidirectional stream | `*Stream` | `(SendStream, RecvStream)` | `ZmuxStream` | `BiStream(send, recv)` |
| sending / receiving part | `*SendStream` / `*ReceiveStream` | `SendStream` / `RecvStream` | `ZmuxSendStream` / `ZmuxReceiveStream` | `SendStream` / `RecvStream` |
| pipe input | `PipeIO` | `PipeIo` | `PipeIo` | `PipeIo` |
| attached pipe | `*Pipe` | `Pipe` | `ZmuxPipe` | `Pipe` |
| pipe credentials | `PipeAuth` | `PipeAuth` | `PipeAuth` | `PipeAuth` |
| server registry | `*Listener` | `Listener` | `ZmuxListener` | `Listener` |
| scheduler | `Scheduler` | `Scheduler` | `Scheduler` | `sched.Scheduler` |

---

## 3. Establishment and identification

| Operation | Go | Rust | Java | Python |
|---|---|---|---|---|
| client on one carrier | `Dial(ctx, conn, cfg)` | `Session::connect(pipe, cfg)` | `Zmux.client(pipe, cfg)` | `await za.connect(host, port, ...)` |
| server without registry | `Server(ctx, conn, cfg)` | `Session::accept(pipe, cfg)` | `Zmux.server(pipe, cfg)` | `za.serve(handler, ...)` |
| any carrier, split carriers, symmetric role | `Start(ctx, p, role, cfg)` | `Session::peer(pipe, cfg)` | `Zmux.peer(pipe, cfg)` | `await za.start(reader, writer, role=...)` |

- Establishment returns after the peer's transport parameters have been
  processed (SPEC.md Section 6.1), except when early data is possible
  (`early_data`, Section 6),
  where it returns as soon as the local parameters are written: streams
  opened before the peer's parameters arrive send early data within the
  floor of SPEC.md Section 6.1.2 and the rest when the parameters arrive,
  and a failed establishment fails them with the session error
  (`EarlyDataRejected` when the peer turned out unable to take early data,
  which guarantees that it processed none of it).
- `RoleAuto` / `peer` sends `zmux_role` with a random nonce (EXTENSIONS.md
  Section 2); `Dial`/`Server` use transport roles.
- **ALPN** is the application's choice and is configured on the TLS layer.
  zmux's defaults follow QMux draft-02: offer `qmux-02`, accept `qmux-01` and
  the `-01` forms of configured identifiers (SPEC.md Section 6.2).
- `protocols` is an optional hook for the `qmux` crate's
  `application_protocols` transport parameter on carriers without TLS; it is
  for interoperability only and is not part of zmux (SPEC.md Section 13.3).
- A split bootstrap (uplink and downlink on different carriers; optional,
  SPEC.md Section 6.1.1) passes both halves in one `PipeIO`/`PipeIo` and sets
  its `Pairing` token, which the carrier conveys to the listener. A library
  that does not offer split bootstrap does not have these fields.
- `Server()` / `Session::accept` without a `Listener` cannot accept joins;
  configuring `max_pipes > 1` on them is an error, not a silent downgrade.

---

## 4. Pipes

### 4.1 Adapter interface

A pipe input is at most one receiving half and at most one sending half, at
least one of them, each closed by zmux exactly once:

| Language | Receiving half | Sending half |
|---|---|---|
| Go | `io.ReadCloser` | `io.WriteCloser` |
| Rust | `AsyncRead` (std driver: `Read`) | `AsyncWrite` (std driver: `Write`) |
| Java | `PipeReader` (CompletionHandler style) | `PipeWriter` |
| Python | asyncio `StreamReader`-compatible | asyncio `StreamWriter`-compatible |

- Framing: `ByteStream` (default; records are concatenated) or `Records` for
  message carriers such as WebSocket (one message = one record's Frames field,
  without Size; SPEC.md Section 3.4), byte-compatible with the `qmux` crate
  and qmux-go.
- A write returns only after the bytes are handed to the layer below
  (buffered carriers flush inside the write); a write in progress means "not
  writable", the only backpressure signal the scheduler uses. Adapters do
  not need partial writes: the driver bounds each write call to one batch,
  and to the write-call bound clamp(R x B / 2, 16 KiB, 64 KiB) of
  IMPLEMENTATION.md Section 4 (R the pipe's send rate, B the latency budget)
  while latency-sensitive work is waiting, and in a
  multipath session a blocking write additionally to max(4 KiB, R_p x
  50 ms) bytes (on a message-framed carrier, one message whose record is
  built within those bounds), except that a record holding a frame that
  cannot be split may exceed them by what that frame needs; bytes count as
  sent when the call starts. Drivers that write to non-blocking sockets
  driven by writability (TCP and TLS carriers on an event loop, Go TCP
  carriers through `syscall.RawConn`) write what the socket takes, within
  the write-call bound, in each gather write (MULTIPATH.md Section 7.1).
  A slow carrier that
  blocks in a write is therefore judged only by the time beyond that write's
  expected duration, its actual size divided by R_p (MULTIPATH.md Section
  7.1). For rate estimation, a blocking write in progress counts as "not
  writable" only while the endpoint has more data ready that it has not
  committed to any pipe (SCHEDULER.md Section 2).
- Closing the sending half is a graceful end (TCP FIN, TLS close_notify, end
  of an HTTP body, WebSocket Close); read or write errors and unexpected ends
  are pipe failures (MULTIPATH.md Section 7).
- Optional abort (MULTIPATH.md Section 2.4): Go `interface{ Abort(error) }` on
  the sending half, Rust `PipeIo::with_abort(f)`, Java `PipeWriter.abort(Throwable)`,
  Python `writer.transport.abort()`. zmux calls it instead of a graceful close
  when it fails a pipe, so that the peer sees a failure and not the graceful
  end that, toward a plain QMux peer, means "idle close" (SPEC.md Section
  6.4).
  Without it, zmux closes the half gracefully.
- Optional capabilities, each with a fallback when absent: the count of
  bytes the carrier holds unsent (`tcpi_notsent_bytes`, SIOCOUTQNSD, or the
  adapter's own buffer: bytes a write call took that the carrier has not
  yet transmitted), queried on the sending half (of each lane) by the driver
  outside the session lock: Go `interface{ Unsent() (uint64, bool) }` on
  the sending half, Rust `PipeIo::with_unsent(f)` (`f: Fn() -> Option<u64>`),
  Java `PipeWriter.unsent()` (`OptionalLong`, empty by default), Python
  `writer.transport.get_write_buffer_size()` (asyncio's own query, where
  the transport has it, sampled at each write call); the built-in TCP adapters implement it with the
  socket query where the platform has one (Rust `with_tcp_socket` supplies
  it together with the RTT and the low-water mark). An adapter whose query
  answers "absent" reports no count. A low-water mark (TCP_NOTSENT_LOWAT), and the
  carrier's minimum RTT. An adapter declares which it offers; one that
  offers neither of the first two is paced by the carrier queue limiter
  (SCHEDULER.md Section 9.1) in multipath sessions and, with pipe
  acknowledgement (MULTIPATH.md Section 3.5), on a single pipe; without the
  third the RTT comes from the credit loop, QX_PING and PIPE_ACK samples
  (SPEC.md Section 8.3 rule 6). An adapter for a stream of a QUIC, HTTP/2
  or HTTP/3 connection SHOULD report the bytes the stream holds unsent
  where its stack exposes them (the stream's send buffer not yet sent):
  a carrier that reports them keeps urgent tails shorter than one paced by
  the limiter, most of all when its rate changes. The count is the existing capability; no other
  "send-queue depth" exists or is needed. A QUIC stack that exposes no
  count leaves its pipe to the limiter; its adapter does not bound the
  stack's send window below the limiter's in-flight cap (SCHEDULER.md
  Section 9.1) and offers lanes all the same (SCHEDULER.md Section 9.2:
  over such a carrier lanes keep their gains for urgent messages and short
  flows under loss, and only their acknowledgements show the limiter
  which bytes a lost packet holds back, SCHEDULER.md Section 9.1).
- Optional write overhead: the fixed number of bytes that the layers below
  zmux add to each write call on the sending half (0 by default, at most
  65535; any other value is the configuration error when the pipe is
  added). An inbound transform (Section 4.4) may set it on the value it
  returns. zmux passes it unchanged to the shaper factory together with the
  pipe's kind (Go `PipeIO.WriteOverhead` and `ShapedPipe.WriteOverhead`,
  Rust `PipeIo::with_write_overhead`, Java `PipeIo.withWriteOverhead`,
  Python `write_overhead=`) and does not otherwise use it.
- Optional retransmission count: the bytes the carrier itself has
  retransmitted (`tcpi_bytes_retrans`, `tcpi_txretransmitbytes`,
  Windows `SIO_TCP_INFO` `BytesRetrans`, a 32-bit count in every
  `TCP_INFO` version that the adapter widens across wraps, or a QUIC stack's lost bytes of the
  connection), queried on the sending half where and when the unsent count
  is (Go `interface{ Retransmitted() (uint64, bool) }`, Rust
  `PipeIo::with_retransmitted(f)`, Java `PipeWriter.retransmitted()`
  (`OptionalLong`), Python inside the binding's adapters). For pipes that
  share one carrier connection it is that connection's count. The
  built-in TCP adapters implement it where the platform has the query; it
  feeds only `bytes_retransmitted` and `loss_rate` (Section 7.2).
- Optional metadata: TLS exporter (of the TLS 1.3 handshake that carries
  the pipe: its TLS connection, or the QUIC connection whose stream it is;
  MULTIPATH.md Section 10.5), addresses, socket statistics (`TCP_INFO`),
  label.
- Optional lanes (MULTIPATH.md Sections 2.5 and 5.8), for a pipe input that
  is one stream of a multi-stream carrier connection. The adapter reports
  the identity of that connection (an opaque value that is equal for two
  streams exactly when they belong to the same connection: the stack's
  connection object, never its addresses) and can open a new stream on it,
  which zmux uses as the sending half of a lane; every stream the peer opens
  on such a connection is handed to the library with the same identity,
  like any new carrier (below), and the library recognises a lane by its
  PIPE_LANE record:

  | Language | Carrier connection | Open a lane |
  |---|---|---|
  | Go | `interface{ CarrierConn() any }` on the pipe input; the value is comparable (a pointer) | `interface{ OpenLane(ctx context.Context) (io.WriteCloser, error) }` |
  | Rust | `PipeIo::with_carrier_conn(id: CarrierConnId)`, `CarrierConnId` an opaque `Clone + Eq + Hash` value | `PipeIo::with_lane_opener(f)`, `f` returning a future of an `AsyncWrite` (std driver: a `Write`) |
  | Java | `PipeIo.withCarrierConn(Object conn)` (compared by identity) | `PipeIo.withLaneOpener(LaneOpener)`, `LaneOpener.open()` returning a `CompletionStage<PipeWriter>` |
  | Python | `carrier_conn=` keyword of the pipe input (compared by identity) | `open_lane=` an async callable returning a `StreamWriter` |

  An adapter offers lanes only where the connection delivers its streams
  independently (QUIC and the HTTP/3 and WebTransport streams on it, SCTP;
  streams of another zmux session when that session's own pipes are
  delivered independently, Section 4.6); HTTP/2 adapters report the
  connection but do not open lanes by default (MULTIPATH.md Section 2.5),
  and single-stream carriers (TCP, TLS, WebSocket, KCP) offer neither. Over
  QUIC (and HTTP/3 or WebTransport on it) a pipe pair, pipe 0 included, is
  always one bidirectional stream, whose two halves are lane 0 of the two
  directions, and a pipe joined in one direction is a bidirectional stream
  too; a further lane is a unidirectional stream on QUIC and WebTransport,
  and a bidirectional stream with its reverse half unused on carriers whose
  further streams are bidirectional (MULTIPATH.md Section 2.5). Streams that the peer
  opens on a lane-capable connection go to `Listener.HandlePipe` (Section
  4.4), or, on an endpoint without a listener, to `HandleLane` (Go
  `zmux.HandleLane(io)`, Rust `zmux::handle_lane(io)`, Java
  `Zmux.handleLane(io)`, Python `await zmux.handle_lane(reader, writer,
  carrier_conn=conn)`); both route by the Lane Key over the sessions of the
  process, check the connection, and close a stream that names no pipe
  (MULTIPATH.md Section 5.8). The built-in QUIC, HTTP/3 and WebTransport
  adapters run that accept loop themselves. Each lane's unsent bytes and
  writability are reported as for any pipe (the capabilities above), per
  lane; a lane without the unsent count is covered by the limiter as its
  pipe is. An adapter bounds the lanes it opens by the connection's stream
  credit and its memory budget, and sizes the connection-level receive
  window for the lanes that can be blocked at once, charged to the memory
  budget (IMPLEMENTATION.md Section 13.8 N4, N5); `max_lanes` (Section 6)
  bounds what it accepts.
- Carrier early data. An adapter whose carrier can send TLS 1.3 early data
  or QUIC 0-RTT (`early_data` with `replayable`, Section 6) reports the
  carrier's verdict to the session: accepted, or rejected. On rejection
  the session first calls the adapter's restart hook (Go
  `interface{ RestartEarly(ctx) (io.ReadWriteCloser, error) }`, Rust
  `PipeIo::with_early_restart(f)`, Python `early_restart=`; Java has
  none, since none of its carriers sends early data: its QUIC adapter runs
  without 0-RTT and the JDK TLS engine offers no early data), which returns the pipe's byte stream as it
  stands after the handshake: on QUIC the bootstrap stream is opened again,
  because the 0-RTT stream is gone; on TLS it is the same stream when the
  TLS library discards rejected early data rather than resending it
  itself. Only then does the session restart the pipe (SPEC.md Section
  6.1.2, Outcome): QX_TRANSPORT_PARAMETERS first, and the early streams'
  data, FIN bits and resets after the peer's parameters. Every send the
  application made before that point, before or after the rejection, is
  provisional: a write, close or reset completes from the application's
  view, but reaches the peer only as re-issued, and is final once the
  carrier accepted the early data or the re-issue followed the peer's
  parameters. A hook that fails fails the session with that error. A TLS library that resends
  rejected early data itself, or cannot report the verdict, gives no
  restart hook, and the adapter then does not offer carrier early data;
  the floor (`zmux_peer`) does not need the hook.
- Built-in adapters: TCP and Unix sockets, TLS (exporter wired), WebSocket
  (following QMux's WebSocket mapping), HTTP carriers, QUIC streams (lanes
  wired), in-memory pipes, and streams of another zmux
  session (Section 4.6).

### 4.2 Adding and removing pipes

| Operation | Go | Wire |
|---|---|---|
| add a pipe (in-band join) | `c.AddPipe(ctx, p, opts)` | PIPE_JOIN first on the new pipe, carrying `ReceivePriority` and `ReceiveStandby` as the initial request for the peer's outbound direction (MULTIPATH.md Section 5.1), then a complete PIPE_ACK snapshot and a PIPE_PING; stream data follows once the join is confirmed and the pipe leaves probation, or at once when no other pipe is usable (MULTIPATH.md Section 11.1 rule S1). `opts` (`PipeOptions`) holds `SendPriority`, `ReceivePriority`, `SendStandby`, `ReceiveStandby`, `Weight` and `Probation` (below), label, fate group (a pipe that replaces a failed one on the same path SHOULD reuse its fate group, which carries the probation history, SCHEDULER.md Section 11; fate groups are scoped to their joiner, so the replacement is joined by the endpoint that joined the old pipe, MULTIPATH.md Section 2.2), `SuspectFloor` (the floor of T_suspect, default 250 ms, at least 50 ms), `DeadTimeout` (dead_timeout, at least 1 s: how long the last pipe of a direction may stall before it fails, effective max(T_suspect, `DeadTimeout`); unset, T_fail with resume and max(T_fail, half the idle timeout) without), `ProbeInterval` (the probe interval k of the pipe while it is idle, measured to the carrier: unset, the scheduler parameter `probe_interval`; it may turn the idle probes of the pipe off (Section 6), as for the scheduler parameter, except on an attachment without an outbound direction at the peer, where they cannot be turned off and at most 15 s takes effect; otherwise at least 1 s, any other value being the configuration error) and `ProbeJitter` (the probe jitter j, at least 0 and below 1: each probe waits a uniform draw from [k_eff x (1 - j), k_eff], the first wait of a bootstrap pipe from [0, k_eff] when j > 0; unset, the scheduler parameter `probe_jitter`) of MULTIPATH.md Section 7.1. Session-wide defaults for all four, which also apply to pipe 0, are parameters of the `scheduler` setting (Section 6), like `spread_chunk` |
| reserve for an out-of-band join | `r := c.ReservePipe(dir, opts)`; `r.Header()`; `r.Attach(ctx, p)`; `r.Cancel()` | PIPE_JOIN conveyed out of band by the carrier |
| list | `c.Pipes()` | |
| priority, standby, weight, probation | `p.SetSendPriority(v)`, `p.ClearSendPriority()`, `p.SetReceivePriority(v)`, `p.SetSendStandby(b)`, `p.ClearSendStandby()`, `p.SetReceiveStandby(b)`, `p.SetWeight(w)`, `p.SetProbation(d)` | the `Send` settings apply to the local outbound direction (no frame), and `Clear` returns a field to the peer's request, else the default; the `Receive` settings are the request for the peer's outbound direction: given at `AddPipe` or `ReservePipe` they travel in the PIPE_JOIN, and changed later they are sent as PIPE_STATUS, at once when the attachment is ACTIVE and when it becomes ACTIVE otherwise (MULTIPATH.md Sections 5.7 and 9) |
| revalidation after a network change | `c.RevalidatePipes()`, `p.Revalidate()` | a PIPE_PING at once on each named live outbound pipe, within the PIPE_PING limit, longest-waiting first (MULTIPATH.md Section 6.4); nothing else (below) |
| graceful removal | `p.Close()` | PIPE_ABANDON(NO_ERROR) and drain (MULTIPATH.md Section 8.2) |
| abrupt removal | `p.Abort(cause)` | failure, reinjection, PIPE_ABANDON(ZMUX_PIPE_FAILED) |
| events | `c.NextEvent(ctx)`: pipe events are kinds of the session event stream (below) | |

Rust, Java and Python use the same names in their conventions (`add_pipe`,
`reserve_pipe`, `pipes`, `next_event`, `set_send_priority`,
`clear_send_priority`, `set_receive_standby`, `revalidate_pipes`,
`revalidate`, ...; `addPipe`, `reservePipe`, `pipes`, `nextEvent`,
`setSendPriority`, `clearSendPriority`, `setReceiveStandby`,
`revalidatePipes`, `revalidate`, ...).

Python keeps every option of a pipe in one immutable `PipeOptions` (a
frozen dataclass with the keyword fields `send_priority`,
`receive_priority`, `send_standby`, `receive_standby`, `weight`,
`probation`, `label`, `fate_group`, `suspect_floor`, `dead_timeout`,
`probe_interval` and `probe_jitter`; durations in seconds as `float`;
`None` is unset, that is the default of the tables of this section), and
`add_pipe(pipe, *, options=None)`, `reserve_pipe(direction, *,
options=None)` and `PipePool(..., options=None)` take only that. A live pipe
changes the six fields that the methods above change with
`pipe.update(**fields)`, the Python form of all of them: a keyword left
out keeps its value; `send_priority=None` and `send_standby=None` clear
the field (the `Clear` methods); `receive_priority`, `receive_standby`,
`weight` and `probation` take a value only, and `None` for them raises
`TypeError`; a field fixed when the pipe is added (`label`, `fate_group`,
`suspect_floor`, `dead_timeout`, `probe_interval`, `probe_jitter`) or an
unknown name raises `TypeError`; an invalid value raises `ValueError` and
changes nothing. One call applies all its fields together, so a call that
changes both `Receive` fields sends one PIPE_STATUS. `pipe.options`
returns the options in force, with the fields set by `update`; the
effective values, including those that follow the peer's request, are in
the pipe statistics (Section 7.2). There is no separate policy object.

**Events.** Pipe events have no stream of their own: they are kinds of
the one event stream of the session, which also carries the session
events (Section 4.5) and those of carrier selection (Section 4.8), in the
order they happened. Go `c.NextEvent(ctx) (Event, error)` (the kind in
`Event.Kind`), Rust `session.next_event().await -> Option<SessionEvent>`
(`None` once the session ended), Java `session.nextEvent()` and
`nextEvent(timeout)` returning a sealed `SessionEvent`, Python `await
session.next_event()` (`None` once the session ended) and `async for
event in session.events()`, the events being subclasses of
`zmux.Event`. Each event is delivered to one reader. The calls work on
every session, multipath or not.

**Revalidation.** An application that learns of a network change (an
interface gone or readdressed, a switch between Wi-Fi and cellular, a
resumed laptop) calls `RevalidatePipes()` for every outbound pipe, or
`Revalidate()` on the pipes it suspects. Each named live outbound pipe gets
a PIPE_PING at once (or as soon as the PIPE_PING limit allows) and is
**awaiting revalidation** from the call until its acknowledged position
reaches that PIPE_PING. Meanwhile it takes no new stream data,
reinjection, opportunistic or redundant copies as long as another usable pipe of its
direction is in neither probation nor revalidation (SCHEDULER.md Section 11.1); it keeps
carrying control frames and what was already committed to it. Revalidation
is not a suspicion: the pipe does not become suspect because of it, nothing
is reinjected, it enters no probation and it adds nothing to the probation
history. A PIPE_PING that stays unanswered makes the pipe suspect after
T_suspect through the ordinary ack stall (MULTIPATH.md Section 7.1), as any
unacknowledged byte would. A pipe that answers costs new data one round
trip at most, while a pipe that vanished with its interface stops taking
new data at once instead of after T_suspect, or for an idle pipe the next
15 s probe. Without a call nothing changes. The calls are idempotent: a
pipe already awaiting revalidation is not sent a second PIPE_PING.

**Tiers and weights** (MULTIPATH.md Section 9, SCHEDULER.md Sections 6 and 10). Every pipe
has a priority per direction, an unsigned integer with no upper limit (Go
`uint64`, Rust `u64`, Java `long` read as unsigned, Python `int`), lower
preferred, default 0; and a weight, also unbounded, default 1:

| `PipeOptions` field | Meaning | Default |
|---|---|---|
| `SendPriority` | tier of the local outbound direction. Set explicitly, it wins over the peer's request | unset: the peer's latest PIPE_STATUS request, else 0 |
| `ReceivePriority` | tier requested for the peer's outbound direction of this attachment: in the PIPE_JOIN of an attachment this endpoint joins, otherwise, and for later changes, in PIPE_STATUS | 0 (no PIPE_STATUS sent) |
| `SendStandby` | the local outbound direction carries data only on failover. Set explicitly, it wins over the peer's request | unset: the peer's latest PIPE_STATUS request, else available |
| `ReceiveStandby` | standby requested for the peer's outbound direction of this attachment, carried like `ReceivePriority` | false (no PIPE_STATUS sent) |
| `Weight` | the outbound direction's share for `spread-round-robin`, and the factor applied to its measured rate by `spread-rate-weighted`; local, never sent | 1 |
| `Probation` | base of the probation of a pipe that joins or returns from suspicion; 0 disables probation | 4 x SRTT for the first join of a path without history, otherwise max(1 s, 4 x SRTT), doubled for a pipe that keeps failing, but never beyond max(base, twice the longest up period it has shown) (SCHEDULER.md Section 11) |

All six can be changed at any time with the methods above, and the two
`Send` fields can be cleared again; changes apply from the next scheduling
decision, and a changed request is sent to the peer at once. New stream
data goes to the most-preferred tier; a less-preferred tier takes data only
while the preferred tier is saturated (overflow) or has no usable pipe
(failover), and traffic returns to the preferred tier as soon as it has room
again. Saturation is a property of the tier, not of a pipe: the tier is
saturated when its backlog would take longer than `overflow_delay` (default
1 s) to send at the tier's measured rate, or when data has waited for it
that long; a pipe that is merely not writable does not make it overflow.
The less-preferred tier then takes what the preferred one cannot send
within `overflow_delay`, chunk by chunk. This is a cost-versus-latency
setting: with the default, a burst of up to about rate x 1 s (10 MB on
80 Mbit/s of preferred pipes) never touches the less-preferred tier, and
larger bursts and sustained overload spill their excess. A smaller
`overflow_delay` shortens completion times at the price of more data on the
less-preferred tier; standby keeps a pipe out of overflow entirely
(SCHEDULER.md Section 10). Standby pipes take data only on failover. zmux has no notion of what a pipe runs over: whether a relay is
a fallback, an equal member of the tier or the preferred path is only a
choice of priorities (SCHEDULER.md Section 10 shows four such
configurations). Stream urgency does not bypass tiers; an application that
wants urgent streams on the lowest-delay pipe whatever its tier installs a
custom policy (Section 5.2). The session-wide `overflow_delay` (default
1 s) and the default `Probation` are parameters of the `scheduler` setting
(Section 6).

### 4.3 Pipe credentials (`pipe_auth`)

`PipeAuth` selects the secret flags of `zmux_multipath` (MULTIPATH.md Section
10.1): `PipeAuth.TLS(...)` / `tls(...)` asserts that the bootstrap TLS 1.3
handshake (a TLS connection, or the QUIC connection whose stream is pipe 0)
terminates at the peer and uses its exporter (Go, Rust with rustls, Java 25
`ExtendedSSLSession`); `PSK(key)` supplies a pre-shared key, static or
derived per session by the application (MULTIPATH.md Section 10.2), alone or
together with the exporter; `AllowInsecure` is for plaintext deployments
protected by other means. There is no default: the
library never uses the exporter on its own, because a CDN or a
TLS-intercepting proxy makes the two ends export different values, and
without `pipe_auth` multipath is reported as "join security unavailable".
MULTIPATH.md Section 10.7 gives the recommended setup for CDN and WebSocket
deployments (a PSK).

| Language | Credentials |
|---|---|
| Go | `PipeAuth` value in `Config.PipeAuth` |
| Rust | `PipeAuth::none()`, `PipeAuth::tls()`, `PipeAuth::psk(key)`, `PipeAuth::insecure()`, `.with_psk(key)`; `Config::pipe_auth` |
| Java | `PipeAuth` in `ZmuxConfig` |
| Python | `zmux.PipeAuth`, immutable: `PipeAuth.tls()`, `PipeAuth.psk(key: bytes)`, `PipeAuth.insecure()`, `auth.with_psk(key)` returning a new value; `Config(pipe_auth=...)`, `None` the default (no credentials). It replaces the keywords `psk` and `tls_auth` |

A token that fails verification at the acceptor is reported as the event
`JoinAuthFailed{reason}` (Go `Event` of kind `EventJoinAuthFailed`, Rust
`SessionEvent::JoinAuthFailed`, Java `SessionEvent.JoinAuthFailed`, Python
`zmux.JoinAuthFailed`), with reason
`mac` or `mac_carrier_bound`, and counted in the `join_auth_failures`
statistic, so that a secret mismatch is diagnosable.

### 4.4 Listener

`Listener.HandlePipe(io, hint)` classifies a new carrier by its first record
(SPEC.md Section 6.2, MULTIPATH.md Section 5.5): QX_TRANSPORT_PARAMETERS
starts a session that `Accept` returns; PIPE_JOIN (or an out-of-band
`JoinHint` the carrier conveyed) attaches to an existing session after
verification. `HandlePipe` blocks for the lifetime of the pipe so that HTTP
handlers stay alive. The listener also pairs split bootstraps by
their `Pairing` token, holds suspended sessions (`MaxSuspended`, default 1024),
and can encode an instance identifier into session locators for load-balancer
stickiness. Pairing split bootstraps is part of the optional split-bootstrap
feature (SPEC.md Section 6.1.1). Unknown, unauthenticated or replayed joins are closed with no
distinguishable reason, with one opt-in exception: with
`AnswerUnknownJoins` set (Go `ListenerConfig.AnswerUnknownJoins`, Rust
`ListenerConfig::answer_unknown_joins`, Java
`ListenerConfig.answerUnknownJoins`, Python `answer_unknown_joins=`;
off by default), an admitted carrier whose well-formed join names a
locator that begins with the listener's instance identifier (every
locator, without one) and matches no session is answered with the fixed
close of MULTIPATH.md Section 5.4 step 3 before it is closed, so that its
joiner ends a resumption at once instead of at the end of its grace
period. It is for deployments in which a locator with this listener's
instance identifier reaches no other instance; it is still counted as
`rejected`.

Listener options (not session knobs): `MaxSuspended`; the per-source and
first-record limits of MULTIPATH.md Section 5.6 (`MaxPendingPerSource`,
default 8, `FirstRecordTimeout`, default 2 s); and a global memory budget:
`MemoryBudget` (bytes; 1 GiB by default, a fixed value that does not depend
on the host's memory; it may be set to unlimited, Section 6) with the hook `OnMemoryPressure(used,
budget)` (Go field, Rust `on_memory_pressure`, Java
`memoryPressureListener`, Python `on_memory_pressure`), called outside every
lock on its own serial context when pressure starts (the sum reaches the
budget) and when it ends (the sum falls below three quarters of it, below),
and free to close sessions (IMPLEMENTATION.md Section 2.5.7). The
budget covers the
sum over the listener's sessions of buffered receive data, retained data,
the buffers of suspended sessions and the growth of each session window
above its initial size (auto-tuned windows never shrink, so a session that
once ran at full rate keeps that credit). Window growth stops when it would
exceed the budget (the memory allowance of IMPLEMENTATION.md Section 6.2). With the default (auto) window caps and send
buffer maximum this allowance is what lets windows grow beyond 64 / 96 MiB
and retained data beyond 64 MiB (SPEC.md Section 8.3 rule 4, SCHEDULER.md
Section 13.1). A session without a listener has a budget of its own, the
`memory_budget` parameter of knob 7 (Section 6; 1 GiB by default, or
unlimited), with the same accounting; at it, that session simply stops
growing (there is no pressure hook for it).
The budget limits what is newly granted, not what was already granted:
credit that has been advertised is never withdrawn, so the sum can exceed
the budget by the credit outstanding when it was reached.
While the sum is above the budget:

- every session behaves as if at its buffer cap (SPEC.md Section 8.3 rule
  4): session credit only on consumption, no window growth;
- stream credit keeps flowing as SPEC.md Section 8.5 requires, but new peer
  streams are rejected with ZMUX_REJECTED (RESET_STREAM and STOP_SENDING,
  EXTENSIONS.md Section 7.2) without allocating receive buffers and without
  being offered to the application; their data is discarded, which releases
  its session credit, and the peer may retry them;
- the listener refuses new sessions with CONNECTION_REFUSED after its own
  parameters, and closes carriers that would add a pipe to a session that is
  not suspended; joins that let a suspended session resume are still
  accepted, because resumption drains retained data;
- suspended sessions are closed with ZMUX_RESUME_TIMEOUT, those holding the
  most memory first;
- the hook is called, so the application can shed load.

Normal operation resumes below three quarters of the budget
(IMPLEMENTATION.md Section 6.3).

**Inbound transform.** A listener may be configured with one inbound
transform: a function that takes a carrier's `PipeIO` and returns the
`PipeIO` the listener continues with. It is called once for every carrier,
each one-directional half of a split bootstrap included, after admission
(Section 4.7) and before any session processing, the classification of the
first record included. While it runs, the carrier counts against
`MaxPendingPerSource` and `FirstRecordTimeout` bounds it. It cannot change
the carrier's kind: the returned value has the same directions, the same
framing (bytes or messages) and the same carrier declarations. An error
closes the carrier and counts as a setup failure (Section 7). A wrapping
layer, here or wherever a `PipeIO` is wrapped, MUST map each write on the
carrier to exactly one write on the layer below and MUST NOT coalesce
writes. A language may provide a helper that presents a `PipeIO` as its
connection or stream type (a `net.Conn` in Go), so that existing
stream-based code can implement a transform.

**Order.** Every entry point of a listener (its own accept loops, the HTTP
handlers in every form, WebSocket, WebTransport, QUIC, and a direct
`HandlePipe`) takes a new carrier through the same steps:

1. a **source slot**: the carrier counts against its source's
   `MaxPendingPerSource` (Section 4.7) from the moment the entry point takes
   it, before any handshake; a carrier whose source has no slot left is
   closed at once (`refused`, Section 7.2). The slot is held through the
   handshake, the admission and the steps below, until the carrier enters
   a session, is closed or is handed to a fallback; a stalled carrier keeps
   it until the stall ends;
2. the outer handshake (TLS, the HTTP upgrade or WebSocket handshake, the
   QUIC handshake), if any, bounded by the handshake timeout;
3. the admission (Section 4.7), when one is configured;
4. the inbound transform, when one is configured;
5. classification: a join or resumption and its verification, or a new
   session (with the pairing of a split bootstrap);
6. the exchange of transport parameters of a new session.

The listener's own pending-carrier slots (64 by default, MULTIPATH.md
Section 5.6) are taken at step 5, as before. `FirstRecordTimeout` runs from
the end of step 3 (step 2 without an admission). Nothing is written on the
carrier before step 3 has accepted it.

**Per entry point.** The steps apply to each kind of entry as follows:

- HTTP request carriers: a request whose source has no slot left, or that
  arrives once the listener is closed, and that the listener has not taken
  over and on which nothing has been written, goes to the host's fallback
  handler, as every request the listener does not take. Every other entry
  closes such a carrier at once.
- Exchange carriers: the admission decides once per new carrier, on the
  request that creates it, after the request's authentication has been
  verified. Later requests of the same carrier are not decided and take no
  source slot.
- Split bootstrap: each half takes a source slot of its own, passes the
  admission and holds its slot until the pair is complete. A source thus
  needs two slots for one split bootstrap, and `MaxPendingPerSource` is at
  least 2.
- QUIC: the connection is decided on its first bidirectional stream.
  Further streams of an accepted connection are not decided again, but each
  takes a source slot of its own. A handshake that fails inside the QUIC
  stack before the connection is accepted may not be counted (Section 7.2).
- WebSocket and WebTransport: decided on the upgrade request, before the
  101 or 200 response.

| Language | Entry of an adapter with its own accept loop | Inbound transform | Stopping |
|---|---|---|---|
| Go | `Listener.Arrive(source, peer netip.AddrPort) (*Arrival, error)`, then `Arrival.HandlePipe` (or `Admit`), `Release`, `HandshakeFailed`; `Listener.AdmitUpgrade` for an HTTP upgrade; `JoinHint.Source` | `ListenerConfig.WrapPipe` | `Shutdown(ctx, code, msg)`, `Close()` |
| Rust | `Listener::enter(peer, source) -> Option<Entry>`, then `Entry::admit`, `Entry::stall` and `handle_pipe` with `PipeHint::from_entry` (`PipeHint::from_admitted` for further streams of a QUIC connection); `PipeHint::source`, `PipeHint::peer` | none | `shutdown(code, reason).await`, `close()` |
| Java | `ZmuxListener.reserve(peer, source)` returning a `Slot` (`handle`, `release`, `handshakeFailed`), or `ZmuxListener.handle(io, hint, fallback)`, which takes the slot itself and returns a `Handled`; a host-resolved HTTP source in the channel attribute `HttpEndpoint.SOURCE` | none | `shutdown(Duration)` returning a `CompletionStage<Void>`, `close()` |
| Python | inside the binding (`serve_*`, `handle_pipe`) | none | `await shutdown(code, reason)`, `close()` |

**Closing.** As with an HTTP server, there are two ways to stop a
listener. `Shutdown(ctx, code, msg)` (Rust `shutdown(code, reason)`,
Java `shutdown(Duration)`, Python `await shutdown(code, reason)`) is graceful: new sessions are refused at once
(`Accept` reports the closed listener; a carrier classified as a new
session, or a half of a split bootstrap, is closed and counted as
`refused`), its sessions are shut down (Section 5.7), and until they have
all ended, or `ctx` ends, the listener keeps accepting the joins and
resumptions of its running and suspended sessions, through the steps above
as before; then it closes as below. `Close` (Rust `close`, Java `close`,
Python `close`) is immediate: the accept loops stop and close their
sockets, every carrier is refused, joins and resumptions included, pending
admissions, handshakes and pairing waits are cancelled, and the memory
watcher stops; sessions already started are left to run, without new
pipes from the listener.

**Clock.** Every timer of a listener and of its admission (handshake,
peek and first-record timeouts, the admission's deadline, stall durations,
pairing waits) runs on the clock of the session configuration it is made
with: in Go `Config.Clock` (Section 6); the other languages use their
runtime's monotonic clock.

### 4.5 Pipe quality

`c.PipeStats()` returns, for every pipe, and `p.Stats()` for one, a snapshot
per pipe and per direction: the `PipeStats` of
Section 7.2, which is the only definition of its fields.

Events (kinds of the session event stream, Section 4.2): the state events
`PipeAdded`, `PipeJoined`, `PipeSuspect`, `PipeRecovered`, `PipeFailed` and
`PipeClosed`; `PipeQualityChanged`, `CapacityBelow` and `CapacityRestored`
when the application asked for them (below); `PipePriorityChanged` (priority, standby or weight, with the
source: local setting or peer request); `TierSaturation` (a tier of a
direction entered or left saturation, with the direction and the priority
value), at most one per tier per second, the latest state winning; `JoinAuthFailed`
(Section 4.3); `PipesRetired` (below); and the session events `SessionSuspended`, `SessionResumed`
and `GoAwayReceived`.

**Retirement.** Retirement (MULTIPATH.md Section 8.4) is reported by one
event only, `PipesRetired{retired, failed, closed, dropped, last}`. Each
pipe retired since this event was last read adds 1 to `retired`; when its
terminal state had not been read at retirement, it also adds 1 to
`failed` if either direction FAILED and to `closed` otherwise (a terminal
state already read counts in neither). Its unread non-terminal events
leave the queue and are not counted. `dropped` counts only the events the
queue's bound dropped, added to the summary not yet read or, when none is
queued, to the next one. `last` holds the IDs of the last retired pipes,
at most four, oldest first. The summary enters the event stream at the
first retirement after the previous one was read and accumulates until it
is read, so `retired` is always at least 1 and the queue stays bounded
however many pipes a session goes through (IMPLEMENTATION.md Section
2.5.7); the bound never drops the summary or a terminal event. No per-pipe retirement event exists, and `PipeClosed` reports only a
direction that ended (outbound CLOSED, inbound ENDED). Go `Event` of kind
`EventPipesRetired` (fields `Retired`, `Failed`, `Closed`, `Dropped`,
`Last []uint64`), Rust `SessionEvent::PipesRetired`, Java
`SessionEvent.PipesRetired(retired, failed, closed, dropped, last)`, Python
`zmux.PipesRetired`. An application that needs every retired ID compares
`Pipes()` with the handles it holds. From retirement on (and once a session
that ended has released its pipes), a handle's reads report absence and
its operations fail with the "pipe gone" error: Go `p.Stats() (PipeStats,
bool)` false and `ErrPipeGone`; Rust `stats()` `None` and
`PipeError::UnknownPipe`; Java `stats()` and `state()` an empty `Optional` and
`PipeGoneException`; Python `stats()` and `state` `None` and `PipeError`.
A read never raises because a pipe was retired. Applications that
watch costs read `bytes_sent` per pipe, for example how much a relay
carries.

**Quality events (optional).** Applications that manage tiers by delay or
rate (for example promoting a relayed pipe above a direct one while the
relay is faster, or redialling when the session's capacity drops) need not
poll `Stats()`:

| Operation | Go | Rust | Java | Python |
|---|---|---|---|---|
| quality band of one pipe, or the default of the session's pipes | `p.SetQualityBand(b)`, `s.SetQualityBand(b)` | `set_quality_band(b)` | `setQualityBand(b)` | `Pipe.set_quality_band(b)`, `Session.set_quality_band(b)`, `b` a `QualityBand(rtt=, rate_ratio=, min_interval=)` |
| capacity floor of the session's outbound direction | `s.SetCapacityFloor(f)` | `set_capacity_floor(f)` | `setCapacityFloor(f)` | `Session.set_capacity_floor(f)`, `f` a `CapacityFloor(rate=, rate_ratio=)` |

- `QualityBand{RTT, RateRatio, MinInterval}`: the event
  `PipeQualityChanged{pipe, smoothed_rtt, delivery_rate}` is emitted for an
  outbound pipe when its `smoothed_rtt` leaves the band of +-`RTT` around
  the last reported value, or its `delivery_rate` (R_p) leaves the band
  [last / `RateRatio`, last x `RateRatio`]; the band then recentres on the
  reported values (the first estimates measured after the band is set
  are the first report, without an event). At most one event per pipe
  per `MinInterval` (default 1 s; 0 sets no minimum), the latest values
  winning; a zero `RTT` or `RateRatio` turns that half off. Inbound directions have no
  sender estimates; the peer reports its own.
- `CapacityFloor{Rate, RateRatio}`: the event `CapacityBelow{rate}` is
  emitted once when the sum of `delivery_rate` over the usable, non-standby
  outbound pipes (SCHEDULER.md Section 10; a pipe counts 0 until its rate
  has been measured, the initial value is not a measurement) falls below
  `Rate`, or at once when it is below when the floor is set, and `CapacityRestored{rate}` once when it rises to
  `Rate` x `RateRatio` (default 1.25) or more again, as LACP's minimum
  links: zmux reports, the application decides (it never closes the
  session on its own).
- Both are off until set, and cost nothing then: only a pipe or session
  with a band or floor keeps its last reported values (the idle footprint
  bounds of IMPLEMENTATION.md Section 9.2 are unchanged). They are
  evaluated where the estimates change, in O(1), and queued with the other
  pipe events (bounded, coalesced per pipe); they never influence
  scheduling. A session without multipath reports pipe 0 in the same way
  wherever its estimates exist (the `rtt_source` of `PipeStats`, Section 7.2; the rate needs
  pipe acknowledgement).

A pipe whose join is not confirmed within `handshake_timeout` fails with a
"rejected" pipe error; the joiner never learns why (MULTIPATH.md Section
5.4).

**Every session, the same view.** `Pipes()` and `p.Stats()` work on every
session with the same fields, so that monitoring and route selection need
no special case. A session without multipath reports one pipe, ID 0, in
both directions: `state` is `active` until the session ends (a single pipe
is never suspect, its failure ends the session), the tier fields hold their
defaults and the reinjection, duplicate and opportunistic counters are 0.
`rtt_source` names where `smoothed_rtt` and `rttvar` come from, the
first available of:

| `rtt_source` | Samples | Available |
|---|---|---|
| `ack` | PIPE_ACK (SCHEDULER.md Section 2.1) | multipath sessions, and single-pipe sessions between zmux endpoints (pipe acknowledgement, MULTIPATH.md Section 3.5) |
| `kernel` | the carrier's own estimate (`TCP_INFO`, `SIO_TCP_INFO`) | adapters that report it |
| `ping` | QX_PING request to response, together with the credit-loop samples of SPEC.md Section 8.3 rule 6 | every session; a plain QMux peer answers QX_PING (QMux-02 Section 4.3) |

`min_rtt` is the minimum of SPEC.md Section 8.3 rule 6 (the carrier's
minimum when it reports one, otherwise the 300 s minimum of all samples).
With a plain QMux peer the samples are the QX_PING of the first record
(SPEC.md Section 8.3 rule 6), keep-alive requests, `Ping()` (Section 5.4)
and credit loops: the library sends no request of its own beyond these, so
an application that wants a fresh value calls `Ping()`. Before the first
sample the estimates are absent, not the 333 ms default. `delivery_rate`,
`bytes_acked` and `bytes_in_flight` need acknowledgements: with pipe
acknowledgement they are measured as in a multipath session, with a plain
QMux peer they are absent. The queue and traffic fields are exact
everywhere. The session-level RTT fields of Section 7 of a single-pipe
session are those of pipe 0.

### 4.6 Streams as pipes (nested sessions)

A bidirectional stream of one session can be the carrier of a pipe pair of
another session (EXTENSIONS.md Section 10, which is normative for the
mapping):

| Operation | Go | Rust | Java | Python |
|---|---|---|---|---|
| open a carrier stream on an outer session | `c.OpenPipeStream(ctx, tlvs...)` returns a `PipeIO` | `session.open_pipe_stream(&preamble).await` (its application TLVs) returns a `PipeIo` | `session.openPipeStream(tlvs)` returns a `PipeIo` | `await session.open_pipe_stream(tlvs=None)` returns a `PipeIo` |
| wrap an accepted stream that carries PIPE_CARRIER, or any bidirectional stream the application knows to be a carrier | `zmux.StreamPipe(s)` | `PipeIo::from_stream(send, recv)` | `stream.asPipe()` | `zmux.stream_pipe(send, recv)` |

The result is used like any other pipe input: given to `Start` (Section 3)
to bootstrap a nested session, to `AddPipe` (Section 4.2) of a nested
session to join it, or, on the accepting side, to the nested session's
`Listener.HandlePipe` (Section 4.4), which tells a bootstrap from a join by
the first record as for any carrier. `tlvs` are application TLVs for the
opening preamble (Section 5.1), for example the destination and hop limit
a relay uses to route the stream (EXTENSIONS.md Section 4.7); the library
adds PIPE_CARRIER. Opening sends nothing until the nested session writes
its first record, which then travels in the stream's opening frame with
the preamble: a join over a relayed stream costs no extra round trip.
Loops are refused where a stream-backed pipe meets the session it is for,
because only there are both known (`OpenPipeStream` does not name the
nested session, and a stream may be opened before the nested session
exists): `AddPipe` fails, and the listener refuses the join (MULTIPATH.md
Section 5.4 step 7), when the library can see that the carrier stream's
session runs, directly or through other sessions of the process, over a
pipe of the session the pipe would join (EXTENSIONS.md Section 10.2; the
listener answers with ZMUX_PIPE_RESOURCE_LIMIT so that a joiner that
cannot see the loop backs off; the check and the recording of the new
nesting are one atomic step, IMPLEMENTATION.md Section 2.5.1, and a loop
found only after the pipe was attached is answered the same way). A
bootstrap (`Start`) cannot form a loop: the new session has no other pipe.
Loops the library cannot see are bounded by EXTENSIONS.md Sections 4.7 and
10.3. When the outer
session does not use the preamble (a plain QMux peer), `OpenPipeStream`
opens a stream without any TLV, both directions of the pipe start at
offset 0, an abort uses RESET_STREAM, and the receiving application
recognises the carrier stream by its own means and wraps it with
`StreamPipe`; `tlvs` must then be empty.

The adapter declares no unsent count and no low-water mark, so the nested
session paces the pipe with its carrier queue limiter (SCHEDULER.md Section
9.1). It reports the outer session as the carrier connection and opens
lanes as further carrier streams of the same outer session, by default only
when the outer session itself has lanes or more than one pipe in use, since
over a single TCP pipe the outer streams share one ordered byte stream and
lanes would recover nothing (MULTIPATH.md Section 2.5), and only with
stream-count credit that no opener of the application is waiting for,
while the lanes that all nested pipes of the process hold on that outer
session together (each pipe's lane 0 not counted) stay at most half of the
outer peer's `initial_max_streams_bidi`, rounded down, so that lanes, which live as long as their pipe, never take
the application's streams; it never waits for credit itself (MULTIPATH.md
Section 5.8); it
passes the nested session's records to the outer stream as owned buffers
in both directions, without copying (Section 5.1, chunk calls). Closing the
nested session, removing the pipe or a pipe failure ends the outer stream as
EXTENSIONS.md Section 10.2 specifies, and the loss of the outer stream or
its session is a pipe failure in the nested session, handled like any other
(MULTIPATH.md Section 7).

### 4.7 Carrier admission

A listener may be configured with one **admission**: a decision taken on
each new carrier, after the outer handshake and before the inbound
transform (Section 4.4). It is part of the listener's configuration and of
nothing else: every entry point of a listener that has one runs it, joins
and resumptions included, and no entry point bypasses it.

- **Inputs.** The carrier's metadata, read-only (its source and peer
  addresses, below; the completed TLS or QUIC handshake state; for an HTTP
  carrier, the request head), and a **prefix**: at most N bytes read from
  the carrier (N configured, 0 for none), up to N, the end of the carrier or
  the peek timeout.
- **Verdicts.** Accept(k): the first k bytes of the prefix are dropped and
  the rest of the carrier continues with the listener; Fallback: the
  carrier, its prefix replayed, goes to the fallback target configured for
  its kind (a connection handler, an HTTP handler, a QUIC connection
  handler); Stall: nothing is sent, input is discarded, and the carrier is
  closed after a duration that depends only on its source; Close. A k
  outside the prefix, an unknown verdict, an error, a cancelled or late
  decision are Close; nothing on the carrier tells them apart.
- **Asynchronous decision.** The decision function may be asynchronous: Go
  `Decide(ctx context.Context, info *CarrierInfo, prefix []byte) Decision`
  (the deadline is ctx's); Rust `trait Decide`, whose `decide(&self, info:
  &CarrierInfo, prefix: &[u8])` returns a future of a `Verdict` (a plain
  closure is one; cancelled by dropping the future); Java
  `Admission.Decider`, `CompletionStage<Decision> decide(CarrierInfo,
  ByteBuffer, Deadline)` (cancelled with `cancel(false)`); Python `async def
  decide(info, prefix)` returning a `Verdict` (cancelled with
  `CancelledError`; a plain function is accepted). Its deadline is what remains of the peek timeout, which runs
  from the end of the outer handshake; it is cancelled when the listener
  stops taking carriers (Section 4.4: `Close`, or the end of `Shutdown`). It is called at most once per
  carrier, concurrently for different carriers, outside every lock of zmux.
- **Host value.** An Accept decision may carry one value of the host's,
  opaque to zmux, which never inspects, compares, copies or closes it; on
  any other verdict it is ignored. The listener passes it with the carrier:
  the inbound transform sees it (Go `JoinHint.Admitted`, filled by the
  listener), a session holds the value of the carrier that created it (for
  a split bootstrap, the uplink half) for its lifetime, resumption
  included, and a pipe holds the value of the carrier that carried its
  join (the bootstrap pipe: the session's; lanes do not change it). Without
  an admission, without a value, and on the client side it is absent. zmux
  does not decide whether a join comes from the same party as its session:
  the host compares the values, in the inbound transform or after the pipe
  event, and closes what it does not accept. Go `Decision.Value any`,
  `Conn.Admitted() any`, `Pipe.Admitted() any`; Rust `Verdict::accept(k)
  .with_value(v)` (`Arc<dyn Any + Send + Sync>`), `Session::admitted()`,
  `Pipe::admitted()`; Java `Decision.accept(skip, value)`,
  `Session.admitted()` and `Pipe.admitted()` (`Optional<Object>`); Python
  `Verdict.accept(skip, value=None)`, `session.admitted`, `pipe.admitted`.
- **Stall limit.** A stalled carrier keeps its source slot. A source holds
  at most `MaxPendingPerSource` - 1 stalled carriers, so that it always has
  a slot for a new attempt; a Stall beyond that is Close
  (`stall_overflow`, Section 7.2).
- **Source.** A carrier has two addresses: its **peer**, the address it
  arrived from, and its **source**, the client address after resolution:
  behind an intermediary the listener is configured to trust, the one that
  intermediary reports (PROXY protocol or a forwarding header field the
  deployment names; port 0 when it reports none), or the address a host
  resolved itself and passed as the request's source address; otherwise the
  peer. One function of the trusted-intermediary configuration resolves it,
  and every adapter calls it. The `Source` of a `JoinHint` is the same value
  (unset: the peer). Aggregation (an IPv6 address to its /64) is internal to
  the listener, the same for every per-source bound and the stall duration.
  Go `CarrierInfo.Source`, `CarrierInfo.Peer` and `JoinHint.Source` are
  `netip.AddrPort`; Rust `SocketAddr`, Java `InetSocketAddress`, Python a
  `(host, port)` tuple.
- **Client credentials.** A dialer may present a credential on the carriers
  it opens: bytes written before the first record, or a request header field
  for carriers decided on their request head. It is written as 1-RTT data,
  never as carrier early data.

| Language | Admission | Configured on |
|---|---|---|
| Go | `Admission{Peek, PeekTimeout, Decide, StallMin, StallMax}`, `Decision{Verdict, Skip, Value}`, `CarrierInfo{Source, Peer netip.AddrPort, TLS, Request, Head}` | `ListenerConfig.Admission`; fallback targets `ListenerConfig.Fallback` (byte streams), `HTTPOptions.Next`, the `fallback` argument of `zmuxquic.Serve` |
| Rust | `Admission::new(peek, f)` (a synchronous decision) or `Admission::with_decider(peek, impl Decide)`, with `with_peek_timeout`, `with_stall_interval`; `trait Decide`, `Verdict`; `CarrierInfo` (`source`, `peer`: `SocketAddr`) | `ListenerConfig::admission`; fallback targets `ListenerConfig::fallback` (byte streams), the `fallback` of the HTTP, HTTP/3 and WebTransport adapters' options, the `QuicFallback` of `zmux_quinn::serve` |
| Java | `Admission(peek, peekTimeout, stallMin, stallMax, decider)`, `Admission.of(peek, decider)`, `Admission.Decider` (`Decider.immediate` for a synchronous one), `Decision`, `Verdict`, `CarrierInfo(source, peer: InetSocketAddress, ...)` with `RequestHead` | `ListenerConfig.admission`; fallback targets `ListenerConfig.fallback` (byte streams), the host handler behind `HttpEndpoint`, `QuicServer.Fallback` |
| Python | `Admission(decide, *, peek, peek_timeout, stall)`, `Admission.prefix_auth(auth, ...)`, `Verdict`; `CarrierInfo.source`, `.peer`: `(host, port)` | `Listener(admission=...)`; no fallback target (Fallback stalls a stream carrier, an HTTP request gets 404) |

### 4.8 HTTP carrier selection

HTTP-type carriers may share one server endpoint. A client may name the
carrier kind it uses, or let the dialer choose one automatically; a session
whose carrier was chosen automatically reports the kind chosen. A carrier
kind describes only this endpoint's own hop, so HTTP/3 and WebTransport are
not limited to direct connections. A pipe pool (Appendix A) may use the
automatic choice as its dialer; dialing a session automatically is
establishing it on the most robust candidate and running such a pool of
one member, which moves the session to preferred kinds by make before
break. The library's carrier cache, which remembers per server the kinds
that worked, can be exported and imported in a JSON form shared by all
languages:

    {"version": 1, "entries": [{"key": "...", "kind": "websocket",
      "good_until": "2026-11-01T12:00:00Z", "fail_until": "",
      "backoff_ms": 60000, "url": ""}]}

`version` is 1 (another version fails the import); `kind` is a carrier
kind name, an unknown one skips its entry; the times are RFC 3339 in UTC,
empty for none; `backoff_ms` is a non-negative integer; unknown members
are ignored; a wrong type fails the whole import; expired entries (both
times and the fail time plus the maximum backoff past) are dropped; an
entry replaces one of the same key and kind only if it is newer.

---

## 5. Sessions and streams

### 5.1 Stream operations

| Operation | Go | Rust | Java | Python | Wire |
|---|---|---|---|---|---|
| open (waits for credit) | `OpenStreamSync(ctx)` | `open_bi()`, `open_bi_with(&preamble)` | `openStream()`, `openStream(preamble)` | `await open_bi(*, info=None, tlvs=None, pipe_carrier=False, priority=None, peer_priority=None)` | nothing until the first frame: the first write, or pinning (SPEC.md Section 7.2) |
| open without waiting | `OpenStream()` | `try_open_bi()`, `try_open_bi_with(&preamble)` | `tryOpenStream(preamble)` | `try_open_bi(*, info=None, tlvs=None, pipe_carrier=False, priority=None, peer_priority=None)`, raising `OpenError` without credit | |
| accept | `AcceptStream(ctx)` | `accept_bi()` | `acceptStream()` | `await accept_bi()` | with the preamble: offered once its preamble has arrived, without waiting for lower-numbered streams (identifier order from a conforming peer outside multipath and lanes); without it: in identifier order on opening (SPEC.md Section 7.2 rule 5) |
| write | `Write` / `TryWriteAll` | `write` / `write_chunk` | `write` / `writeAsync` / `tryWrite` | `await write()` / `write_nowait()` | STREAM |
| explicit finish | `Close()` | `finish()` | `finish()` | `finish()`, or leaving `async with` normally | STREAM with FIN |
| reset | `CancelWrite(code)` | `reset(code)` | `reset(code)` | `reset(code)` | RESET_STREAM, or RESET_STREAM_AT covering the preamble |
| reliable boundary | `SetReliableBoundary()` | `set_reliable_boundary()` | `markReliableBoundary()` | `SendStream.set_reliable_boundary()` | RESET_STREAM_AT Reliable Size |
| stop reading | `CancelRead(code)` | `stop(code)` | `stop(code)` | `stop(code)` | STOP_SENDING |
| drop or close without finish | GC cleanup: `CancelWrite(0)`, `CancelRead(0)` (`Close` is FIN) | `Drop`: reset(0), stop(0) | `close()`: RESET(0), STOP_SENDING(0) | exception in `async with`: reset(0); GC: reset(0) | code 0 (EXTENSIONS.md Section 7.4) |
| decline before reading | `Reject()` | `reject()` | `reject()` | `reject()` | RESET_STREAM and STOP_SENDING with ZMUX_REJECTED (EXTENSIONS.md Section 7.2) |
| open info | `SetPreamble(b)` before the first frame; `Preamble()` | `Preamble::with_info(b)` given to `open_bi_with`; `RecvStream::preamble().info()` | `Preamble(openInfo, tlvs, pipeCarrier)` record given to `openStream`; `preamble().openInfo()` | `info=` of `open_bi`/`open_uni`; `RecvStream.preamble.info` | OPEN_INFO TLV (EXTENSIONS.md Section 4) |
| application TLVs | `AddPreambleTLV(t, v)` (repeatable) before the first frame; `PreambleTLVs()` | `Preamble::with_tlv(t, v)` (repeatable); `preamble().tlvs()` | the record's `tlvs` (`Preamble.Tlv`); `preamble().tlvs()` | `tlvs=` (`Tlv` or `(type, value)` pairs); `RecvStream.preamble.tlvs` | application TLVs of the preamble, in order (EXTENSIONS.md Section 4.3); a type outside 0x1000 to 0x3fff, or a GREASE value, is refused with an error, and without the preamble in use (a plain QMux peer) the call returns `ErrNotNegotiated` |
| carrier stream | `SetPipeCarrier()` before the first frame; `PipeCarrier()` | `Preamble::with_pipe_carrier()`; `preamble().is_pipe_carrier()` | the record's `pipeCarrier`; `preamble().pipeCarrier()` | `pipe_carrier=True`; `RecvStream.preamble.pipe_carrier` | the PIPE_CARRIER TLV (Section 4.6): set by `OpenPipeStream`, and by a relay on the stream it opens towards the next hop; reports whether the opener sent it |
| chunks without copying | `ReadChunk(ctx, max) (Chunk, error)` / `WriteChunk(ctx, Chunk)` | `read_chunk(max)` / `write_chunk(Bytes)` | `readChunk(max)` / `writeChunk(Chunk)` | `await RecvStream.read_chunk(max)` returning a `memoryview` of the received buffer / `await SendStream.write_chunk(buffer)` taking any buffer-protocol object, which the caller does not modify afterwards | none: a received buffer passes to the caller and on to a sending part, of any session, without a copy; where a language pools buffers the chunk carries its release (Go `Chunk.Release`): `WriteChunk` takes ownership in every outcome, success, error or cancellation, and releases the buffer once it is no longer needed, and a chunk that was read and is not written is released by its holder |
| send capacity | `SendCapacity()`, `WaitSendCapacity(ctx, min)` | `send_capacity()`, `poll_send_capacity(cx, min)` | `sendCapacity()`, `awaitSendCapacity(min)` | `SendStream.send_capacity()`, `await SendStream.wait_send_capacity(min)` | none: the bytes the sending part could transmit now, the smaller of its remaining stream credit and the session's remaining credit, minus what it already holds unsent (0 at least); the wait (`min` >= 1) returns the capacity once it is at least `min`, or above 0 while the part holds nothing unsent (a peer window below `min` would otherwise never satisfy it), or with the stream's error. The waiter is woken only when its condition holds, not on every change of credit, so a caller that wants a chunk's worth costs one wake-up per chunk; the condition is re-evaluated on every event that can change it: credit (MAX_DATA, MAX_STREAM_DATA), the part's unsent bytes being transmitted (which raises the capacity without any frame from the peer, and makes "holds nothing unsent" true), and the stream's state (reset, STOP_SENDING, session close) |
| local priority | `SetPriority(u, inc)` | `set_priority(Priority)` | `setPriority(Priority)` | `SendStream.set_priority(Priority(urgency, incremental))` (a `(u, inc)` tuple is accepted) | none (local scheduling) |
| request the peer's priority (receiving side) | `SetPeerPriority(u, inc)` | `set_peer_priority(Priority)` | `setPeerPriority(Priority)` | `RecvStream.set_peer_priority(Priority)`, or `peer_priority=` of `open_bi` | PRIORITY TLV of a local stream's preamble before its first frame, PRIORITY_UPDATE otherwise (EXTENSIONS.md Section 5) |
| replayable | `Replayable()` | `is_replayable()` | `isReplayable()` | `RecvStream.is_replayable` (property) | true while the stream's opening frame arrived as carrier early data and the carrier handshake has not completed (SPEC.md Section 6.1.2, Replay); the application treats such a stream's request as one an attacker may have repeated |
| stream identifier | `StreamID()` | `id()` | `id()` | `id` (property) | before the first frame: pins the identifier and sends the opening frame (the preamble) as soon as flow control and the retransmit budget allow, before any later stream's first frame (SPEC.md Section 7.2) |
| stream datagrams (bidirectional streams, datagram flows in use) | `SendDatagram(context, p)`, `ReceiveDatagram(ctx)` on `Stream` | `send_datagram(context, Bytes)` on `SendStream`, `read_datagram()` on `RecvStream` | `sendDatagram(context, ByteBuffer)`, `receiveDatagram()` on `ZmuxStream` | `send_datagram(payload, context=0)` on `SendStream`, `await read_datagram()` on `RecvStream` | DATAGRAM with Flow = stream ID + 1 (EXTENSIONS.md Section 6.3) |
| per-stream windows | `SetReceiveWindow(initial, max)`, `SetSendBuffer(n)` | `set_receive_window`, `set_send_buffer` | `setReceiveWindow`, `setSendBuffer` | `RecvStream.set_receive_window(initial, max)`, `SendStream.set_send_buffer(n)` | MAX_STREAM_DATA (IMPLEMENTATION.md Section 6.4) |
| per-stream scheduler | `SetScheduler(s)` | `set_scheduler` | `setScheduler` | `SendStream.set_scheduler(s)` | none |
| redundancy of the sending part (multipath) | `SetRedundant(on)`, `ClearRedundant()` following the session | `set_redundant(Option<bool>)`, `None` following the session | `setRedundant(Boolean)`, `null` following the session | `SendStream.set_redundant(on: bool \| None)`, `None` following the session | copies of its STREAM frames on a second pipe of the tier, within the session's copy bound (SCHEDULER.md Section 4.1); unset, it follows the session's `redundancy` parameter: on for urgency 0 to 2 |
| `net.Conn` view | `StreamConn(s, l, r)`: `Close` = FIN + CancelRead(0) | `AsyncRead`/`AsyncWrite` | `inputStream()`/`outputStream()` | | |

- Only an explicit finish ends a stream cleanly; the receiver never reports a
  reset stream as complete (SPEC.md Section 7.5). Go keeps `Close` = FIN
  (the quic-go convention that proxy code relies on) and documents
  `CancelWrite` for error paths.
- The **first frame** of a locally opened stream is emitted by its first
  write or by pinning, whichever comes first. Settings made before it
  (preamble, priority, windows, scheduler) are merged into the first record:
  there are no open-time options and no extra round trip. After it,
  `SetPreamble` fails with an error, `SetPeerPriority` sends PRIORITY_UPDATE,
  and the window settings take effect with the next MAX_STREAM_DATA.
- **Preamble at open.** Rust, Java and Python give the preamble (open
  info, application TLVs, PIPE_CARRIER) to the open call, so it is fixed
  before the first frame by construction; Go sets it on the stream before
  the first frame. The wire result is the same.
- **Preamble size.** Every setting that goes into the preamble (open info,
  application TLVs, priority, PIPE_CARRIER) is checked when it is made: when
  the preamble with it, its Length field and the GREASE TLV zmux adds
  included, would exceed the limit, the setting fails with an error and has
  no effect (Go `ErrPreambleTooLarge` from `SetPreamble`/`AddPreambleTLV`;
  Rust, Java and Python fail the open call: Rust `PreambleTooLarge`, Java
  `PreambleTooLargeException`, an `IllegalArgumentException`, Python
  `PreambleTooLargeError`, a `ValueError`). The limit is the peer's Maximum
  Preamble Size once its parameters have arrived; before that, the
  remembered value when the session uses remembered parameters, otherwise
  the floor of 1024 (SPEC.md Section 6.1.2); and never more than the
  implementation's own limit (4096). The check is repeated when the stream's
  identifier is assigned, with the limit known then: a preamble that no
  longer fits fails that stream alone, locally, with the same error, and
  nothing of it is sent; the session is unaffected.
- **Before establishment.** A session can be used before the peer's
  transport parameters have arrived, but then neither the peer's stream
  credit nor, with role `auto`, the stream ID parity is known. `open` then
  succeeds provisionally: the stream is queued, nothing is sent, and its
  credit is checked when its first frame would be emitted after
  establishment (it waits for MAX_STREAMS if the peer's limit is already
  used, `OpenStream` included). The non-blocking ID accessor (`StreamID()`,
  `id()`, `id()`, `id`) fails with a "not established" error
  before establishment; an implementation MAY offer a variant that waits for
  establishment (an async accessor, or a context-taking one in Go). Once the
  session is established, the rule below that opening reserves credit
  applies, and `StreamID()` always returns at once. Datagrams sent before
  establishment are queued (within the send queue of Section 5.6) and go out
  once the peer's parameters allow them; those the parameters turn out not
  to allow (no DATAGRAM support, too large, a stream datagram without
  datagram flows) are then dropped and counted.
- **Waiting openers are served in order.** Openers that wait for stream
  credit (`OpenStreamSync` and its equivalents) SHOULD be granted credit in
  FIFO order, the order in which they started waiting, so that a burst of
  opens cannot starve an earlier one; pinning waits in the same order
  (SPEC.md Section 7.2).
- **After GOAWAY** from the peer, no new stream can be opened: open calls,
  and streams opened earlier that have neither emitted their first frame
  nor been pinned, fail with the retryable "going away" error; they hold no
  identifier and sent nothing. Streams already emitted or pinned continue if
  they are below the peer's watermark and fail with the retryable "rejected"
  error otherwise (EXTENSIONS.md Section 3.5).
- **Peer unidirectional streams need the application's consent.** With
  `max_incoming_uni_streams` unset (the default), the peer cannot open any
  unidirectional stream until the application calls `AcceptUniStream`
  (`accept_uni`, `acceptReceiveStream`, `accept_uni()`) for the first time: only
  then is credit granted (SPEC.md Section 8.5 rule 4). Applications and
  interoperability drivers whose peers open unidirectional streams on their
  own, such as HTTP/3 or MoQ-style protocols, set
  `max_incoming_uni_streams` explicitly.
- **Cancelling before the first frame** is invisible to the peer: a stream
  that never emitted a frame consumes no identifier and nothing is sent
  (SPEC.md Section 7.2 rule 1). Applications used to QUIC libraries in which
  every opened stream has an identifier the peer will see reset (as in
  quinn) call the ID accessor first when the peer must observe the reset:
  that pins the identifier and sends the opening frame, so a later cancel
  sends RESET_STREAM.
- Asking for the stream ID before the first frame pins it and sends the
  stream's opening frame as soon as flow control and the retransmit budget
  allow (SPEC.md Section 7.2 rule 1), so a stream
  whose ID was read for logging never holds back the first frames of later
  streams. Once the session is established, opening a stream reserves
  stream-count credit (`OpenStreamSync` waits for it, `OpenStream` fails
  without it), so `StreamID()` always returns at once with an identifier
  below the peer's limit. Settings that go into the
  preamble (open info, peer priority) must therefore be made before the
  identifier is read.
- `Reject()` is for accepted streams whose data the application has not
  read; on a bidirectional stream it ends both directions (Rust and Python:
  on `RecvStream`, which also resets the paired `SendStream` if it has not
  been written to).
- `SetPeerPriority` asks the peer to schedule its sending part of the stream;
  it is available on the receiving side of every stream the peer sends on.

### 5.2 Scheduling policies

Session default from `scheduler` (default `affinity`); changed at any time
for the session with `c.SetScheduler(s)` and per stream direction with
`SetScheduler` on the stream. Built-ins: `Affinity()` (per-stream-direction
pinning), `SessionAffinity()`, `SpreadETA()` (per chunk, earliest arrival;
the default when spreading; parameter `spread_chunk`, default 16 KiB),
`SpreadRoundRobin()` (per chunk, smooth weighted round-robin by the pipes'
`Weight`), `SpreadRateWeighted()` (the same round-robin by `Weight` x
measured rate, SCHEDULER.md Section 6); custom policies implement
`Pick(requests, pipes) -> answers` over a value-only view (pipe IDs,
metrics, sizes and flags; no handle that leads back to the session), run
outside every lock in every language and must not block (MULTIPATH.md
Section 11, IMPLEMENTATION.md Section 2.5.2). The Python binding takes
the built-in policies only, by name: a policy written in Python would run
on the driver's decision path under the interpreter lock, which the
must-not-block rule excludes. One call carries every stream
direction that is waiting for a decision, and each answer names a pipe and
how many bytes it covers, 0 meaning "until it is invalidated", or `Wait`.
`Wait` is never sticky: it holds only until the next input to the session
(a record received, a timer, an application write or a pipe change), after
which the stream direction is asked again. A pipe answer sticks to its
stream direction, so a policy that binds streams
is asked once per binding and a policy that spreads decides a batch of
chunks per call. An answer is applied with a generation check and dropped
when the pipe set, a pipe's state or eligibility, or the pipe's ability to
carry data changes, or when its bytes are used; a stale answer is replaced
by the `affinity` choice for that chunk and counted, never retried. A change takes effect at the
next chunk: frames already committed to a pipe are not moved, and the new
policy starts from its initial state (SCHEDULER.md Section 12). Tier
gating (SCHEDULER.md Section 10) applies before every built-in policy. A
custom policy receives, for each pipe, its effective send priority, standby
flag, probation state, whether it is writable and whether its tier is
saturated, with the pipes
of the eligible tiers marked; it normally picks among them, and may pick
another pipe to override the gating (for example urgent streams on the
lowest-delay pipe whatever its tier).

**Control and synchronisation traffic.** Streams that carry control
messages, signalling or state synchronisation between the endpoints of an
overlay, small and latency-sensitive, should be opened with a high urgency
(0 to 2; `SetPriority` for the local sending part, `SetPeerPriority` so that
the peer's replies on the same stream are urgent too, which costs nothing
when set before the first frame). The defaults then give them, with no
further setting:

- on every session, strict precedence over urgency 3 and lower (the
  default urgency is 3), within the 1/16 floor that keeps lower levels
  from starving (IMPLEMENTATION.md Section 5);
- in a multipath session, a redundant copy of each chunk on the second
  pipe of the eligible tier (`redundancy` = `urgent`, SCHEDULER.md Section
  4.1), so that a message is not delayed by a pipe that fails or stalls
  under it, within the session's copy bound;
- for sparse messages after a quiet period, the first chunk copied too and
  no probe round trip first (MULTIPATH.md Section 7.1).

Urgency does not bypass tiers (Section 4.2). Bulk transfers that are part
of synchronisation, such as snapshots, stay at the default urgency or
lower: urgent bulk would only be capped by the copy bound while holding
back every other stream.

### 5.3 Errors

Four dimensions: application or transport code; local or remote; stream or
session scope; retryable or not ("rejected": ZMUX_REJECTED, or at or above a
GOAWAY watermark; "going away": opened after a GOAWAY, or not yet emitted nor
pinned when it arrived; `IsRetryable` / `is_retryable` / `isRetryable` /
`.retryable`). Codes are
62-bit everywhere.

| Session termination kind | Cause | Wire |
|---|---|---|
| `ApplicationClosed{code, reason, remote}` | close by either application | CONNECTION_CLOSE 0x1d |
| `TransportClosed{code, frame_type, reason, details, remote}` | protocol error; `details` are the `key=value` pairs of the reason's diagnostic line (SPEC.md Section 4.3), empty when there are none, and `reason` is the text before it | CONNECTION_CLOSE 0x1c |
| `IdleTimeout{remote}` | idle timer; `remote` when the peer sent CONNECTION_CLOSE(ZMUX_IDLE_TIMEOUT), or, in a session without acknowledgement (a plain QMux peer), shut down its pipe without CONNECTION_CLOSE (SPEC.md Section 6.4) | CONNECTION_CLOSE(ZMUX_IDLE_TIMEOUT) in every session with acknowledgement (every session between zmux endpoints); none with a plain QMux peer |
| `HandshakeTimeout` | no peer parameters in time | none |
| `EarlyDataRejected` | the endpoint sent early data, and the peer's parameters show that it cannot take it (not a zmux endpoint, no PIPE_ACK in use, or no bidirectional streams accepted): the peer processed none of it (SPEC.md Section 6.1.2); retryable on a new session without `early_data` | CONNECTION_CLOSE(NO_ERROR) |
| `ResumeTimeout{last pipe error}` | grace period expired | ZMUX_RESUME_TIMEOUT if a pipe remains |
| `PipesLost{last pipe error}` | every pipe in a direction lost without resume, or the single carrier failed (in a session with acknowledgement, a graceful end of the peer's pipe without CONNECTION_CLOSE included) | NO_VIABLE_PATH if a pipe remains |

Stream errors: `Reset{code, reliable_size}` on reads, `Stopped{code}` on
writes, `Closed` on a direction closed locally, `SessionLost{session error}`
after termination, `Rejected` (retryable). The outcomes and their precedence
are normative in STATE_MACHINE.md Section 2.5 and SPEC.md Section 6.5.

Removing the last pipe in a direction suspends the session (resume in
use) or ends it with `PipesLost` (MULTIPATH.md Section 8.5).
Implementations SHOULD log a warning when an application removes the last
pipe in a direction while the session has open streams.

### 5.4 Negotiation results

`ConnectionState` (Go) and its equivalents report: role, QMux version
(draft-02), negotiated application protocol, TLS state, datagram and
RESET_STREAM_AT support per side, whether multipath, pipe acknowledgement
(MULTIPATH.md Section 3.5) and resume are in use,
"join security unavailable" when both sides offered multipath but no secret
source matched (MULTIPATH.md Section 3.3), suspension, and the received
GOAWAY watermarks, and the largest datagram payload the peer currently
accepts, for session datagrams and for a given stream (EXTENSIONS.md
Section 6.6), which packet-carrying applications use as their MTU. The
multipath calls (`AddPipe`, `ReservePipe`, `RevalidatePipes`,
the pipe setters) on a session without multipath return
`ErrNotNegotiated` and leave the session untouched; `Pipes`, the
statistics and the event stream work on every session (Sections 4.2 and 7.2).

`Ping(ctx)` (Rust `ping()`, Java `ping()`, Python `await ping()`) sends a
QX_PING request, or joins the one outstanding (SPEC.md Section 4.5), and
returns the round-trip time it measured; it works with every peer,
plain QMux peers included, and is the way to refresh the RTT of a session
whose peer gives no acknowledgements (Section 4.5).

### 5.5 Resume

`resume_timeout > 0` enables resume. While suspended, writes buffer up to the
limits and then block; reads wait; `SessionSuspended` and `SessionResumed`
events are delivered. The `redial` hook (`func(ctx, need Direction)
(PipeIO, error)`) lets the library re-establish pipes automatically.
The hook and the pools of Appendix A divide the work: a session that keeps
several pipes (multipath) keeps them up with pools, one per path, each
redialling its own members, and needs no hook; the hook serves a session
that runs on one pipe at a time, and replaces that pipe.
The grace period is the time redialling has: it should exceed what the
application's redial needs in the outages it means to survive, that is
the detection of the lost pipes (MULTIPATH.md Section 7.1), the redial's
own delays between attempts, and a join handshake. The redial hook and
the helpers of Appendix A dial as soon as the session suspends or lacks a
direction, delayed only by their own wait since their previous dial
attempt.

### 5.6 Datagrams

Session datagrams: `SendDatagram` never blocks: when the queue (64) is full,
the oldest datagram is dropped and counted; `SendDatagramWait` waits for queue
space. `ReceiveDatagram` returns datagrams from a 128-entry queue. With
datagram flows in use these carry Flow 0. Payloads that do not fit return
`DatagramTooLarge{max}`; zmux never fragments. The largest payload is
reported by the session (Section 5.4) and accommodates IP packets of 1500
and 9000 bytes with every peer (EXTENSIONS.md Section 6.6). The queue
defaults suit messages; an application that forwards packets sizes the
queues for its rate: 64 packets of 1500 bytes are 77 us at 10 Gbit/s, so
a packet forwarder sets `send_queue` to about rate x 2 ms / packet size
(1700 at 10 Gbit/s) and `receive_bytes` to about rate x 2 ms (2.5 MB).

**Datagram channels.** Where a pipe's carrier has a datagram channel of its
own (QUIC, HTTP/3, WebTransport) and both endpoints negotiated it
(EXTENSIONS.md Section 6.8), a datagram that fits the channel is sent on it
rather than in a record, so that a lost carrier packet loses that datagram
instead of delaying every later one. Nothing changes for the application:
the same calls, sizes and queues. The QUIC, HTTP/3 and WebTransport
adapters enable their carrier's datagram support whenever the session
enables datagrams. The statistics report how many of the datagrams sent
and received went on channels (`datagrams_sent_channel`,
`datagrams_received_channel`, included in the totals).

**Datagram limiter.** A session MAY be given a limiter for its datagrams
in each direction (the hook `datagram_limiter {send, receive}`, using the
limiter interface of Appendix A): datagram calls are not stream I/O, so no
wrapper around the stream interfaces can meter them. Datagram limiting
polices and never shapes, since datagrams never wait: a datagram passes
when the limiter, asked without waiting (`try_ready`) for its payload size,
allows all of it, and is then charged that size; otherwise it is dropped
and counted (`datagrams_dropped_limit`), on send before it is queued
(`SendDatagramWait` waits only for queue space, as without a limiter) and
on receive before it is queued for the application. With no limiter (the
default) nothing is called; with one, each datagram costs two calls, no
allocation and no lock of the session.

Stream datagrams follow the per-stream model of RFC 9297 and of quic-go's
HTTP/3 stream datagrams: with datagram flows in use, a bidirectional stream
has its own datagram methods (table of Section 5.1), with a context ID
(EXTENSIONS.md Section 6.3):

| Language | Send | Receive |
|---|---|---|
| Go | `Stream.SendDatagram(context uint64, payload []byte) error` | `Stream.ReceiveDatagram(ctx) (context uint64, payload []byte, err error)` |
| Rust | `SendStream::send_datagram(context, Bytes)` | `RecvStream::read_datagram().await -> (u64, Bytes)` |
| Java | `ZmuxStream.sendDatagram(long context, ByteBuffer)` | `ZmuxStream.receiveDatagram()` |
| Python | `SendStream.send_datagram(payload, context=0)` | `await RecvStream.read_datagram() -> (context, bytes)` |

Datagrams of a stream go only to that stream's queue (32 datagrams; when it
is full the oldest is dropped and counted), never to the session queue.
Sending shares the session's send queue and never blocks. Datagrams for an
unknown or closed stream are dropped and counted. A stream datagram call on a
unidirectional stream, or without datagram flows in use, returns
`ErrNotNegotiated`.

### 5.7 Closing

`CloseWithError` / `close` is immediate and never blocks. `Shutdown(ctx,
code, msg)` is graceful: two-phase GOAWAY when it is in use, wait for open
streams, flush, CONNECTION_CLOSE, half-close, read to end of stream; on
cancellation it falls back to `close`. Towards a plain QMux peer it skips
GOAWAY.

---

## 6. Configuration

Sixteen knobs with the same names and meaning in every language (snake_case
here; CamelCase in Go, builder camelCase in Java, keyword arguments in
Python). Every duration, here and in the options of Sections 4 and 5,
accepts any value of the language's duration type at or above its stated
minimum (any non-negative value where none is stated; a value below a
stated minimum, or any other value outside a stated range, is an error
when it is set, reported with the language's existing argument error: a Go
`error`, Rust `PipeError::InvalidOptions` for pipe options and the
configuration error of the builder otherwise, Java
`IllegalArgumentException`, Python `ValueError`): there is no maximum, and a
deadline that lies beyond what the clock can represent never fires
(implementations add durations to instants with saturating or checked
arithmetic, IMPLEMENTATION.md Section 13), which is neither an error nor
silently capped.

Every option, here and in Sections 4 and 5 and Appendix A, is either unset
or set. Unset, it takes the library default stated for it; set, it takes
the value as given, 0 included; a value outside its stated range is the
error above and is never corrected. Some options also have a setting
that is not a value of their range, stated with the option: a timeout may
be set to no timeout, keep-alive and the probes of the scheduler and of a
pipe may be turned off, a memory budget may be unlimited. Each language
tells unset apart from these settings and from the effect of 0 in its own
idiom: Rust uses `Option`, Java `Optional` or not calling the builder
method, Python `None`; Go takes the zero value as unset and, following its
standard library, expresses no timeout and turning keep-alive or probes
off with a negative value, and the effect of 0 (allowing none, no jitter)
and other mechanisms turned off with a named switch. Each implementation's
documentation states which value or switch selects each setting. Where
this document says what 0 of an option does, or that an option is turned
off, it means that setting chosen explicitly; no value of an option is a
sentinel for unset, and a value no stated setting covers, a negative one
included, is the configuration error.

The timers and periods (`handshake_timeout`, `max_idle_timeout`,
`keep_alive_period`, `standby_probe`, `probe_interval`, and the per-pipe
and pool probe intervals that override them) are written in each
language's idiom for an optional duration; a set duration below the
option's lower bound, 0 included where 0 is below it, is the
configuration error, and no duration stands for no timeout or off:

| Language | Default | Set | No timeout, off | Per-pipe or pool override: keep the session's / off |
|---|---|---|---|---|
| Rust | held by `Config::default()` | `Some(duration)` | `None` | `None` / an explicit off variant |
| Java | not calling the setter | a `with` method | a `disable` method | not calling it / a `disable` method |
| Python | the default in the signature | seconds or `timedelta` | `None` | `None` / a separate switch |
| Go | the zero value | a positive duration | a negative duration (the standard library's `net.Dialer.KeepAlive` convention) | zero / negative |

| # | Name | Default | Wire effect |
|---|---|---|---|
| 1 | `handshake_timeout` | 10 s; may be set to no timeout | establishment and join confirmation deadline |
| 2 | `max_idle_timeout` | 30 s; may be set to no timeout, which announces none; at least 1 ms (the parameter is in milliseconds; a smaller value is the configuration error) | `max_idle_timeout` |
| 3 | `keep_alive_period` | 15 s; may be turned off; at least 1 s when on (0 and any shorter value are the configuration error; a period of 0 has no meaning). The period in effect is capped at half the effective idle timeout, and each wait is drawn from [k x (1 - j), k] with j the scheduler parameter `probe_jitter` (SPEC.md Section 6.4). On, an idle session stays open while the peer answers and closes within the idle timeout once its carrier dies; off, the idle timeout ends any session without application activity | QX_PING when idle |
| 4 | `initial_stream_receive_window` | 256 KiB; at least 4 KiB, the early-data floor of `initial_max_stream_data_bidi_remote` (SPEC.md Section 6.1.2; a smaller value is an error when it is set) | `initial_max_stream_data_*` |
| 5 | `max_stream_receive_window` | auto: at least 64 MiB (K x the BDP of 1 Gbit/s x 100 ms, IMPLEMENTATION.md Section 6.5), beyond that as far as auto-tuning asks and the memory budget allows (SPEC.md Section 8.3 rule 4); a value sets a fixed cap; parameter `latency_budget` (100 us), the B of auto-tuning (IMPLEMENTATION.md Section 6.2) | auto-tuning cap |
| 6 | `initial_connection_receive_window` | 1 MiB; at least 16 KiB (the early-data floor, SPEC.md Section 6.1.2; a smaller value is an error when it is set) | `initial_max_data` |
| 7 | `max_connection_receive_window` | auto: at least 96 MiB (1.5 x the stream floor), beyond that within the memory budget; a value sets a fixed cap; parameter `memory_budget` (1 GiB; may be set to unlimited), the budget of a session without a listener, which a listener's `MemoryBudget` replaces | auto-tuning cap, inbound memory bound (192 MiB per session at worst at the floors, 224 MiB with unidirectional streams, SPEC.md Section 8.3 rule 4, plus growth the budget grants) |
| 8 | `max_incoming_streams` | 256; 0, or at least 4 (the early-data floor, SPEC.md Section 6.1.2; another value is an error when it is set) | `initial_max_streams_bidi`; an application that never accepts peer streams sets 0, so that a peer cannot fill 256 stream windows it never reads (SPEC.md Section 8.3 rule 4); a peer's early data then fails with `EarlyDataRejected` at the peer, before anything is processed |
| 9 | `max_incoming_uni_streams` | unset: staged credit. `initial_max_streams_uni` is 16 and MAX_STREAMS (unidirectional) 256 is sent when the application first calls accept on a unidirectional stream (SPEC.md Section 8.5 rule 4) | set (0 included): `initial_max_streams_uni` = the value, at once. Set it when the peer must open more than 16 unidirectional streams before the application accepts one, or to forbid them (0) |
| 10 | `enable_datagrams` | false; parameters `send_queue` (64 datagrams), `receive_queue` (128), `stream_queue` (32 per stream), `receive_bytes` (1 MiB), all unbounded and settable (EXTENSIONS.md Section 6.5) | `max_datagram_frame_size` = 65535, `zmux_datagram_flows` |
| 11 | `send_buffer` | 4 MiB; parameter `max` (auto: at least 64 MiB, beyond that within the memory budget, which retained data is charged to; a value sets a fixed maximum; a `max` below `send_buffer` counts as `send_buffer`) | none (written-but-unreleased bytes per session; grows to the retransmit budget in multipath sessions, up to `max`, which also caps that budget, SCHEDULER.md Section 13.1; shared fairly between stream directions, each of which can always hold 64 KiB, IMPLEMENTATION.md Section 6.4) |
| 12 | `max_pipes` | 1 (multipath off); 8 is the recommended value when multipath is used (MULTIPATH.md Section 16); no upper limit. It is the number of attachments the endpoint aims for with its own joins, counted over the attachments this endpoint joined itself (for the initiator the bootstrap pair included) that have neither failed nor been retired (JOINING, ACTIVE, SUSPECT and DRAINING ones count; failed ones awaiting retirement count only against max_pipes + H; the peer's joins never count): every "free attachment" or "room" condition of this document (Appendix A rotation and redial, A.3 `rejoin`) uses this count; the peer may have up to max_pipes + H attachments live at once (failed ones awaiting retirement included), where H is the parameter `headroom` (default 8, unbounded; MULTIPATH.md Section 5.4 step 9). Parameter `replay_window` (W, default max(64, 2 x (max_pipes + H)), at least max_pipes + H; MULTIPATH.md Section 4.3). Parameter `max_lanes` (L, default 258, any value at least 1; 1 = no lanes toward this endpoint), in every session, single-pipe ones included: the lanes accepted on each inbound pipe (MULTIPATH.md Sections 2.5 and 3.6); outbound lanes follow the peer's L and the adapter (Section 4.1) | > 1: `zmux_multipath`; `max_lanes`: `zmux_max_lanes` (always sent) |
| 13 | `resume_timeout` | 0 (off) | > 0: `zmux_multipath` and `zmux_resume` |
| 14 | `scheduler` | `affinity` (`spread-eta` takes `spread_chunk`); parameters `suspect_floor` (250 ms), `dead_timeout` and `probation` set the per-pipe defaults of Section 4.2, `overflow_delay` (1 s) the overflow delay D of tier gating (SCHEDULER.md Section 10), `standby_probe` (1 s; at least 1 s, any other value being the configuration error; turned off, standby pipes are probed at `probe_interval`) the probe interval of standby pipes and of pipes of a less-preferred tier while the session is busy, `probe_interval` (15 s) the probe interval of idle pipe pairs (MULTIPATH.md Section 7.1; at least 1 s, any other value being the configuration error; a longer one only delays the detection of a pipe pair that dies while idle; turned off, these idle probes stop: the PIPE_PING of an answer, the standby probes and the probes of attachments without an outbound direction at the peer stay, and a pipe pair with idle probes off at both ends is not judged while the session is quiet), `probe_jitter` (0.25; at least 0 and below 1; 0 gives the fixed schedule tests use) the probe jitter of MULTIPATH.md Section 7.1, which also spreads the keep-alive waits (SPEC.md Section 6.4), both the defaults of the per-pipe `ProbeInterval` and `ProbeJitter` of Section 4.2, and `redundancy` (`urgent`: stream directions of urgency 0 to 2 and the first chunk after an idle period are sent redundantly; `off`) the redundant urgent data of SCHEDULER.md Section 4.1 | none |
| 15 | `pipe_auth` | none (must be set to use multipath) | `zmux_multipath` secret flags |
| 16 | `early_data` | off; parameters `zmux_peer` (false) and `replayable` (false) | `zmux_peer` declares the peer to be a zmux endpoint: stream data written before the peer's parameters arrive goes out at once under the floor of SPEC.md Section 6.1.2, behind the PIPE_ACK fence (ignored with role `auto`); an implementation MAY set it per peer from an earlier session's parameters with the application's consent, and then clears it for that peer on `EarlyDataRejected`. `replayable` allows carrier early data (TLS 1.3 early data or QUIC 0-RTT on a resumed carrier, under the remembered parameters of SPEC.md Section 6.1.2): on a client, early streams may be sent in it and so may be replayed; on a server, it accepts carrier early data and marks the streams opened in it `replayable` until the handshake completes (Section 5.1) |

**Python.** `zmux.Config` is a frozen dataclass of keyword fields; every
field defaults to `None`, which keeps the default of the table above.
Durations are seconds (`float`, or `datetime.timedelta`); a knob with
parameters is the knob's field plus one field per parameter, as in Rust:

| # | Python fields |
|---|---|
| 1 | `handshake_timeout` |
| 2 | `max_idle_timeout` |
| 3 | `keep_alive_period` |
| 4 | `initial_stream_receive_window` |
| 5 | `max_stream_receive_window` (`None` = auto), `latency_budget` |
| 6 | `initial_connection_receive_window` |
| 7 | `max_connection_receive_window` (`None` = auto), `memory_budget` |
| 8 | `max_incoming_streams` |
| 9 | `max_incoming_uni_streams` (`None` = staged credit) |
| 10 | `enable_datagrams`, `datagram_send_queue`, `datagram_receive_queue`, `datagram_stream_queue`, `datagram_receive_bytes` |
| 11 | `send_buffer`, `send_buffer_max` (`None` = auto) |
| 12 | `max_pipes`, `pipe_headroom`, `replay_window`, `max_lanes` |
| 13 | `resume_timeout` |
| 14 | `scheduler` (a `Scheduler`, except in Python, or one of `"affinity"`, `"session-affinity"`, `"spread-eta"`, `"spread-round-robin"`, `"spread-rate-weighted"`), `spread_chunk`, `suspect_floor`, `dead_timeout`, `probation`, `overflow_delay`, `standby_probe`, `probe_interval`, `probe_jitter`, `redundancy` (`"urgent"` or `"off"`) |
| 15 | `pipe_auth` (`PipeAuth`, Section 4.3) |
| 16 | `early_data` (the parameter `zmux_peer`), `carrier_early_data` (the parameter `replayable`) |

The names are those of the knobs and their parameters; there are no
shorter aliases (`idle_timeout`, `keep_alive`, `datagrams`) and no
separate `psk` or `tls_auth` fields, since `pipe_auth` is the one place for
join credentials. The hooks that the binding offers (`protocols`, the
shaping fields) are further fields of the same class.

The shaping limits are mechanical bounds, each unset by default, which
means no limit. A set byte, count or time limit is finite and non-negative,
0 allowing none; a set padding ratio limit lies in [0, 1]; any other value
(negative, NaN, infinite, a ratio above 1) is the configuration error,
raised where the value is first checked (at construction where the
language allows) and never corrected. Under a set ratio limit r the padding
allowed so far is max(r x non-padding bytes sent, 16382), within the
padding byte limit, so that a session's first records are not cut by the
ratio; without a ratio limit only the padding byte limit applies. A
padding-only record that the current allowance does not cover is deferred
and sent at its full length once it is covered; only a hard limit, such as
the padding byte limit, shortens one, and only such a cut counts as
clamped.

A shaper factory receives each pipe's write overhead (Section 4.1). An
executor of shaping configuration files takes the largest and the
padded-to record lengths a file plans as lengths of one write at the
layer below and subtracts the write overhead from them, once per write
call: on the first record of a write that holds several, and on every
record of a message-framed carrier, where each message is one write. A
result below the core's minimum is raised to it and counted as clamped.
Block sizes to pad to a multiple of, and the lengths of padding-only
records, are not changed.

A library that loads shaping configuration files (a JSON format shared by
all languages, with one JSON Schema) reads them strictly: unknown,
duplicate and inapplicable members are errors, member names and enumerated
values are case-sensitive, `null` is the same as an absent member,
integer members take the integer form only, and a number outside binary64,
NaN or an infinity is an error; any error fails the whole file. In a
session limits file `max_padding_bytes` and `max_injected` are integers in
[0, 2^53], `max_padding_ratio` lies in [0, 1], and `max_pause_total` and
`max_delay` are seconds in [0, 1e9]; an absent member sets no limit and 0
sets the limit 0 as above (a ratio limit of 0 leaves the 16382-byte
allowance). A library maps the file onto its own limits in its own idiom.
Lengths and counts drawn from a file's values are rounded half away from
zero.

**Exported defaults.** Each library exports these defaults as public
constants, named in its own conventions, and uses only these constants
for them: the keep-alive period (15 s), the idle timeout (30 s), the record
size limit before the peer's parameters (16382, SPEC.md Section 3.2), the
probe jitter (0.25) and the cap of the redial backoff of `MaintainPipes`
(60 s, Appendix A).

Not knobs: hooks `protocols`, `tracer`, `redial`, `datagram_limiter` (Section 5.6),
`allow_connection_window_increase` (a predicate of the proposed session
window increase alone, called outside every lock on the session's hook
context, its answer applied if the proposal is still current; not in the
Python binding), all of them run as
IMPLEMENTATION.md Section 2.5.7 says; listener options (`MaxSuspended`,
`MaxPendingPerSource`, `FirstRecordTimeout`, `MemoryBudget` with
`OnMemoryPressure`, backlog, instance identifier; Section 4.4). Implementation-optional
extensions, which other languages need not offer and which change no wire
behaviour: Go `Config.Clock` (a `timeshim.Clock`, the session's time
source; unset, the system clock). The session
receive buffer cap is derived, not configured (SPEC.md Section 8.3 rule 4). Internal constants: `max_record_size` 65535,
acknowledgement and failure-detection coefficients, the no-effect budget,
the preamble limit (4096). These are defaults or safety bounds of the
implementation, not protocol limits: the protocol bounds a value only where
its encoding does, or where the bound protects an endpoint from its peer. zmux endpoints always
advertise `reset_stream_at`, `zmux_goaway`, `zmux_preamble`,
`zmux_priority`, `zmux_pipe_ack` and `zmux_max_lanes` (SPEC.md Section 5.5).

---

## 7. Statistics

Two kinds of counts are kept apart, because they answer different
questions: **application bytes**, what the application handed to zmux and
got from it, which is exact and independent of how zmux carried it; and
**transport bytes**, what zmux put on its carriers to do so, which depends
on pipes, failures, redundancy and acknowledgements and is the operator's
view of cost.

### 7.1 Application bytes

| Counter | Stream (per sending or receiving part) | Session (total over all its streams, released ones included, and its datagrams) |
|---|---|---|
| `bytes_written` | payload bytes the application handed to the sending part and the call accepted (returned as written, or taken by a non-blocking or chunk write) | sum over streams |
| `bytes_read` | payload bytes the receiving part returned to the application (reads and chunk reads) | sum over streams |
| `datagram_bytes_sent` | payload bytes of the stream's datagrams handed to a carrier | the same over session and stream datagrams |
| `datagram_bytes_received` | payload bytes of the stream's datagrams returned to the application | the same over session and stream datagrams |

- **Exclusions.** None of these counts the preamble, any framing (record
  Size, frame headers, the datagram flow prefix), acknowledgements or other
  control frames, padding, data sent again (retransmission after a
  resumption, reinjection, opportunistic and redundant copies), duplicates
  received, or anything a carrier adds. Bytes accepted by a write and then
  lost to a reset stay counted as written; bytes received and then
  discarded unread (after `CancelRead`, a reset, or release) are not
  counted as read; a datagram dropped from a queue or by a limiter is in
  neither datagram counter.
- **Exactness.** A stream that ends with a finish (FIN) that its peer reads
  has `bytes_written` at the sender equal to `bytes_read` at the receiver.
  A relay that splices one stream into another (Appendix A) reads exactly
  what it writes, so the bytes an application counts at each hop agree
  except for what a reset discarded.
- **Monotonic and cheap.** Each counter is an unsigned 64-bit count that
  zmux never decreases or resets. It is updated with one relaxed atomic
  add where the event happens, by the thread that makes the call for
  written and read bytes and by the driver that hands a datagram to a
  carrier for `datagram_bytes_sent`, either inside a critical section that
  the operation holds anyway (where the add costs nothing more) or after
  it; an update never takes a lock of its own. Session totals MAY be
  sharded per CPU. Any thread can read it at any time without a lock and
  without disturbing the session: a read returns a value between the
  counter's values at the start and at the end of the read, one reader's
  successive reads never decrease, and two counters read one after the
  other are not a consistent snapshot. A stream's counters stay readable
  as long as the application holds the handle and are final once the part
  is terminal; a session's stay readable after it closed.
- **No rates.** zmux keeps no rate of these counters and no reference
  sample: a reader that wants one divides the increase between two of its
  own reads by the time between them, so that readers never disturb each
  other.

Rate limiting, byte budgets and quotas are not part of zmux. The stream
interfaces are the standard ones of each language (`net.Conn`, `AsyncRead`
/ `AsyncWrite`, `InputStream` / `OutputStream`, asyncio streams), so a
general limiter wraps them, and zmux flow control turns a reader slowed by
such a wrapper into backpressure on the peer's sender without any buffer
growing: the receive window is not extended until the data is read. The
splice helper takes a limiter directly (Appendix A), and datagrams, which
no wrapper can reach, take one through the session (Section 5.6).

### 7.2 Transport bytes and other statistics

Four snapshots, with the same fields in every language. The tables below
are their complete field sets: a language has every field and no other
(a further field is added here first), so that one concept has one field
and one name. Field names are given in snake_case, as in Rust and Python;
Go uses CamelCase with Go initialisms (`SmoothedRTT`, `RTTVar`, `PipeID`),
Java the camelCase record components (`smoothedRtt`, `pipeId`,
`datagramsDroppedRecv`).

| Snapshot | Go | Rust | Java | Python |
|---|---|---|---|---|
| session | `c.ConnectionStats() ConnectionStats` | `session.stats() -> SessionStats` | `session.stats()` returning `SessionStats` | `session.stats() -> SessionStats` |
| pipe | `p.Stats() (PipeStats, error)`, `c.PipeStats() []PipeStats` | `pipe.stats() -> Option<PipeStats>`, `session.pipe_stats()` | `pipe.stats()`, `session.pipeStats()` | `pipe.stats() -> PipeStats \| None`, `session.pipe_stats()` |
| stream | `(*Stream).Stats()`, `(*SendStream).Stats()`, `(*ReceiveStream).Stats()` returning `StreamStats` | `SendStream::stats()`, `RecvStream::stats()` | `ZmuxStream.stats()` returning `StreamStats` | `SendStream.stats()`, `RecvStream.stats()` returning `StreamStats` |
| listener | `l.Stats() ListenerStats` | `listener.stats() -> ListenerStats` | `ZmuxListener.stats()` returning `ListenerStats` | `listener.stats() -> ListenerStats` |

**Common rules.**

- Snapshots are returned synchronously, and each field holds a value it
  had during the call. A count MAY be a relaxed atomic read without a lock
  (the Section 7.1 counters and the listener's counts always are). The
  fields of one snapshot are not a consistent cut, with one exception: the
  state fields (direction states, flags, priorities and their sources,
  probation, the lanes present, `pipe_id`) are read from the core state at
  one point. Relations between counts (a subset, a sum, `bytes_written` at
  one end equal to `bytes_read` at the other) hold for their final
  values, not in every snapshot.
- Unless a field says otherwise, a count is an unsigned 64-bit integer
  (Java `long` read as unsigned, Python `int`) that only increases, from 0
  when the object is created, and is updated when the event it counts
  happens; a *gauge* is a current value and goes up and down; an
  *estimate* is a sender estimator of SCHEDULER.md Section 2 and changes
  as samples arrive. Bytes are bytes, rates bytes per second (unsigned
  integers), durations the language's duration type (Python `float`
  seconds).
- A field that can be *absent* (before the first sample, without
  acknowledgements, without the direction, unbound) is Rust `Option`,
  Python `None`, Java `null` (objects and durations) or `OptionalLong`
  (counts, positions, IDs, rates and points in time). Go uses the field's
  zero value where zero is never a reading of it or another field tells
  the absence: an RTT or a rate (never 0), a point in time (the zero
  `time.Time`), a string or kind (empty), a direction's state (its `None`
  value, below; every field of that direction is then 0), the fields that
  need acknowledgements (absent exactly when `ConnectionState` shows pipe
  acknowledgement not in use), and `queued_bytes` and `queue_delay` (0
  where the adapter reports no unsent count); `PeerRequest` is a pointer,
  nil before one. The one field whose zero is a reading and whose absence
  no other field tells, `pipe_id` (pipe 0 is a pipe), has a companion in
  Go: `PipeID uint64` with `PipeBound bool`.
- A direction's state is one of the states of STATE_MACHINE.md Sections
  3.1 and 3.2, its only definition, except `retired` (a retired attachment
  has no snapshot): outbound `joining`, `active`, `suspect`, `draining`,
  `failed`, `closed`; inbound `joining`, `open`, `ended`. They are two
  types, used by every value of the API that reports a direction's state,
  statistics and events alike: Go `OutboundState` (`OutboundJoining` …
  `OutboundClosed`, zero value `OutboundNone`) and `InboundState`
  (`InboundJoining`, `InboundOpen`, `InboundEnded`, zero value
  `InboundNone`); Rust `OutboundState` and `InboundState`; Java
  `PipeState.Outbound` and `PipeState.Inbound`; Python `OutboundState` and
  `InboundState`, string-valued enums.
- A point in time is on the monotonic clock: Rust `Instant`, Go
  `time.Time` with its monotonic reading, Java a `System.nanoTime()` value,
  Python a `time.monotonic()` value.
- Session and pipe `bytes_sent` / `bytes_received` count every byte of the
  records passed to or received from the carriers, Size fields, frames of
  every kind, copies and acknowledgements included; they exclude what the
  carrier itself adds (TLS, WebSocket, TCP and IP overhead).

**`SessionStats`** (Go `ConnectionStats`):

| Field | Meaning |
|---|---|
| `carrier` | the carrier kind a carrier mode chose (Section 4.8), the latest after a switch; absent for a session not dialed in a mode |
| `carrier_candidates` | the tries of the mode's candidates in order, joins of preferred kinds included, each with its kind, outcome (usable, timed out, refused, failed) and whether it was joined; the latest 64 |
| `bytes_sent`, `bytes_received` | bytes passed to and received from the carriers (above) |
| `records_sent`, `records_received` | records passed to and received from the carriers |
| `bytes_written`, `bytes_read`, `datagram_bytes_sent`, `datagram_bytes_received` | the application bytes of Section 7.1 (session totals); the same counters as Section 7.1, not a second count |
| `streams_opened_local`, `streams_opened_remote` | streams opened by this endpoint and by the peer: a local stream counts when its first frame is emitted or it is pinned, a peer stream when it is first seen |
| `streams_active` | gauge: streams the session holds |
| `stream_credit_waits` | opens that had to wait for stream-count credit |
| `datagrams_sent`, `datagrams_received` | session and stream datagrams handed to a carrier, and received and queued for the application |
| `datagrams_sent_channel`, `datagrams_received_channel` | of these, the ones that went on or arrived on a carrier datagram channel (Section 5.6) |
| `datagrams_dropped_send` | datagrams dropped from the send queue (full, or not allowed by the peer's parameters) |
| `datagrams_dropped_recv` | received datagrams dropped: a full queue, an unknown or closed stream, the datagram byte limit |
| `datagrams_dropped_limit` | datagrams dropped by a datagram limiter, sent or received (Section 5.6) |
| `no_effect_budget` | gauge: the current tokens of the no-effect budget (SPEC.md Section 9.4), from 0 to its capacity: the charge that would take it below 0 closes the session, and the gauge then reads 0 |
| `smoothed_rtt`, `min_rtt`, `latest_rtt`, `mean_deviation` | absent before the first sample: pipe 0's outside multipath (its acknowledgements, the carrier's estimate, or QX_PING and credit-loop samples), the primary pipe's in a multipath session; `min_rtt` as SPEC.md Section 8.3 rule 6; `mean_deviation` absent where the source measures no variation |
| `bytes_reinjected` | stream bytes sent again after a pipe failed or became suspect, or opportunistically |
| `bytes_opportunistic` | of `bytes_reinjected`, those sent opportunistically (MULTIPATH.md Section 7.5.1) |
| `penalties` | pipes penalized for slowing down (MULTIPATH.md Section 7.5.1) |
| `reinjections` | reinjection episodes: a pipe's data reinjected |
| `bytes_redundant` | stream bytes sent as redundant copies (SCHEDULER.md Section 4.1) |
| `bytes_duplicate_received` | stream bytes received that had been received already (multipath) |
| `send_buffered`, `recv_buffered` | gauges: written stream bytes not yet sent; received stream bytes not yet read |
| `retained` | gauge: sent stream bytes not yet acknowledged (multipath), each counted once, what the retransmit budget bounds |
| `pipes_active` | gauge: outbound pipes that are ACTIVE |
| `pipes_added` | attachments added, joined either way, the bootstrap pair included |
| `pipe_failures` | attachments that failed |
| `join_auth_failures` | joins whose token did not verify (MULTIPATH.md Section 5.4 step 5; Section 4.3) |
| `join_replays` | joins closed as replays or too late (MULTIPATH.md Section 5.4 step 6) |
| `join_refusals` | own joins refused with ZMUX_PIPE_RESOURCE_LIMIT |
| `suspensions` | times the session entered SUSPENDED |
| `suspended_time` | duration, only increasing: time spent suspended, the current suspension included |
| `scheduler_answers` | custom scheduler answers that named a pipe and were applied (Section 5.2); `Wait` answers are not counted |
| `scheduler_fallbacks` | custom scheduler answers replaced by the `affinity` choice: stale, invalid, or the policy failed |

**`PipeStats`**, one per attachment; absent once the session no longer
keeps the attachment's state (Go: the error):

| Field | Meaning |
|---|---|
| `pipe` | the pipe ID |
| `local` | this endpoint joined the attachment |
| `fate_group` | its fate group |
| `label` | the label the application gave it, if any |
| `carrier` | its carrier's kind, if the adapter declares one |
| `inbound`, `outbound` | the state of each direction (above); absent for a direction the attachment does not have |
| `attached_for` | duration since the attachment was made |
| `suspect_count` | times the outbound pipe became suspect |
| `failure_count` | 0 or 1: whether the attachment failed |
| `failure` | why it failed, once it did |
| `send_priority`, `send_priority_source` | the effective priority of the outbound direction (lower preferred) and where it comes from (`local`, `peer`, `default`) |
| `standby`, `standby_source` | the effective standby flag of the outbound direction and where it comes from |
| `receive_priority`, `receive_standby` | the priority and standby requested of the peer for its outbound direction (Section 4.2) |
| `request_seq` | the sequence number of the last PIPE_STATUS request sent |
| `peer_request` | the peer's latest request for the outbound direction (priority and standby), absent before one |
| `weight` | the outbound direction's weight |
| `probation_until`, `in_probation` | the end of the outbound pipe's probation (absent outside probation, or for a base so large that it never ends) and whether it is in probation now |
| `revalidating` | awaiting revalidation (Section 4.2) |
| `eligible` | eligible under tier gating now |
| `writable` | the outbound carrier takes bytes now |
| `saturated` | the outbound pipe's tier is saturated (SCHEDULER.md Section 10) |
| `rtt_source` | where `smoothed_rtt` and `rttvar` come from (`ack`, `kernel`, `ping`); absent before the first sample |
| `smoothed_rtt`, `min_rtt`, `rttvar` | *estimates* of the outbound pipe; absent before the first sample, never the 333 ms initial value; `min_rtt` is the carrier's minimum where it reports one, else the 300 s minimum of the samples |
| `one_way_delay` | *estimate*; needs acknowledgements |
| `delivery_rate` | R_p, *estimate*; needs acknowledgements (absent with a plain QMux peer) |
| `queued_bytes` | gauge: bytes handed to the outbound carrier that it still holds unsent, the adapter's unsent count (Section 4.1) summed over the lanes; absent where the adapter reports none. It is the carrier's part of Q_p only (SCHEDULER.md Section 4: Q_p = bytes of write calls in progress + `queued_bytes`, 0 when absent); the bytes of write calls in progress are not reported separately |
| `queue_delay` | *estimate*: `queued_bytes` / R_p; absent when either is |
| `bytes_in_flight` | gauge: bytes sent and not acknowledged; needs acknowledgements |
| `bytes_sent`, `bytes_received` | the outbound and inbound positions: bytes written to and completely received from the carrier, over all lanes |
| `bytes_acked` | the acknowledged outbound position; needs acknowledgements |
| `records_sent`, `records_received` | records written and completely received |
| `bytes_reinjected` | stream bytes sent again elsewhere because this pipe failed or became suspect |
| `bytes_opportunistic` | opportunistic copies sent on the outbound pipe (MULTIPATH.md Section 7.5.1) |
| `bytes_redundant` | redundant copies sent on the outbound pipe (SCHEDULER.md Section 4.1) |
| `bytes_duplicate_received` | duplicate stream bytes received on the inbound pipe |
| `pings_sent`, `pings_answered` | PIPE_PING frames sent on the outbound pipe, and of those the ones whose record a PIPE_ACK, on any pipe, has acknowledged (the acknowledged position of the record's lane reached the end of the record); each once |
| `since_last_ack` | duration since the outbound acknowledged position last advanced; absent without an outbound direction or acknowledgements |
| `since_last_receive` | duration since the inbound pipe last completed a record; absent without an inbound direction |
| `since_last_traffic` | duration since a record carrying a traffic frame (STREAM, RESET_STREAM, RESET_STREAM_AT, STOP_SENDING or DATAGRAM) was sent on the pipe or completely received on it, or since the pipe became live; probes, acknowledgements, other control frames and padding do not count; absent while neither direction is live |
| `bytes_lost` | outbound bytes the pipe had sent and that were not acknowledged when it failed (`bytes_sent` - `bytes_acked` at the failure, over all lanes); 0 before a failure; needs acknowledgements. zmux loses no record on a live pipe (MULTIPATH.md Section 7.2) |
| `records_lost` | *estimate*: `bytes_lost` x `records_sent` / `bytes_sent`, rounded up |
| `bytes_retransmitted` | bytes the carrier itself retransmitted, from the adapter's retransmission count (Section 4.1); for pipes sharing one carrier connection, that connection's count; absent where the adapter reports none |
| `loss_rate` | *estimate*: exponentially weighted average (gain 1/8) of the increase of `bytes_retransmitted` over the increase of `bytes_sent`, sampled at most once per second on the driver's existing write and acknowledgement paths; absent without a retransmission count and before the first sample |
| `lanes_out`, `lanes_in` | gauges: the lanes of the outbound and of the inbound direction, lane 0 included (MULTIPATH.md Section 2.5) |
| `lanes_refused` | own lanes of the outbound direction refused (MULTIPATH.md Section 5.8) |
| `lanes` | per lane, for a pipe with lanes (empty without: lane 0 is then the pipe and its positions are `bytes_sent`, `bytes_acked`, `bytes_received`): `lane` (its number), `position_sent` (bytes written on the outbound lane, its first record included), `position_acked` (the position the peer acknowledged on it), `position_received` (the position of the inbound lane); each absent for a direction the lane does not have. Go `[]LaneStats`, Rust `Vec<LaneStats>`, Java `List<LaneStats>`, Python `tuple[LaneStats, ...]` |

The pipe's addresses are not statistics: they are carrier metadata
(Section 4.1).

**`StreamStats`**, in payload bytes, the preamble excluded. Both parts of
a bidirectional stream return the same snapshot of the stream; the fields
of a direction the stream does not have stay 0. Once the session released
the stream, the snapshot keeps its final values, so that the Section 7.1
counters stay readable as long as the handle is held.

| Field | Meaning |
|---|---|
| `bytes_written` | Section 7.1 |
| `bytes_sent` | payload bytes put into STREAM frames, each copy (reinjection, redundancy, opportunistic, retransmission) again |
| `bytes_acknowledged` | distinct payload offsets acknowledged: passed to the carrier in a session without multipath (SPEC.md Section 7.4), covered by PIPE_ACK in a multipath session |
| `bytes_received` | distinct payload bytes received, read or not; none count before a peer stream's preamble, and once the application stopped reading, every byte below the highest offset received counts |
| `bytes_read` | Section 7.1 |
| `datagrams_sent`, `datagrams_received` | the stream's datagrams handed to a carrier, and received and queued for the application (Section 5.6) |
| `datagrams_dropped_recv` | the stream's datagrams dropped from its full queue |
| `datagram_bytes_sent`, `datagram_bytes_received` | Section 7.1 |
| `send_buffered` | gauge: written bytes not yet released (unsent; unacknowledged in multipath) |
| `recv_buffered` | gauge: received bytes not yet read |
| `recv_window` | gauge: the current, auto-tuned receive window |
| `pipe_id` | the pipe the sending direction is bound to under `affinity`; absent when unbound (Go: `PipeBound` false) |

**`ListenerStats`**, counts since the listener was made, each incremented
with one relaxed atomic add when an outcome is decided. The unit is the
**pipe input** handed to the listener (Section 4.1): a carrier, a lane, or
the two carriers of a split pipe once paired, which are one input; a half
that is never paired is an input of its own. Every input whose outcome is
known is counted exactly once, in one of the first five fields, so that

    sessions + joins + rejected + refused + timed_out
        = pipe inputs handed to the listener whose outcome is known

and `setup_failures` counts connections that never became a pipe input:

| Field | Counts |
|---|---|
| `sessions` | sessions started: bootstraps offered to `Accept`, a split bootstrap once |
| `joins` | joins accepted into existing sessions, and lanes accepted into their pipes |
| `rejected` | inputs closed without a distinguishable reason: unknown, unauthenticated or replayed joins, lanes that name no pipe, or not zmux at all (MULTIPATH.md Sections 5.4 and 5.8); admission verdicts are not counted here but in `admission` |
| `refused` | inputs refused at a limit: of the listener (the memory budget, Section 4.4), of the session (a join closed with ZMUX_PIPE_RESOURCE_LIMIT), or new sessions after the listener was closed; and carriers closed at once for want of a source slot (`MaxPendingPerSource`, Section 4.4), which become no input and are counted nowhere else |
| `timed_out` | inputs without a first record, or join, within `FirstRecordTimeout`, and split halves whose other half did not arrive in time |
| `setup_failures` | connections never made a pipe input: a failed TLS handshake or its timeout (with an admission configured, counted in `admission.handshake_failed` instead); the handshake that a join sent in early data waits for, when it fails, times out or its wait is cancelled (the listener closed, a context cancelled), the join then counted nowhere else; an HTTP request answered 425 (Too Early: it arrived in early data and needs the handshake); a failed HTTP or WebSocket upgrade; or the setup limit. Only failures the adapter observes count: a QUIC handshake that fails inside the stack before the connection is accepted may not be counted, here or in `admission.handshake_failed` (the stack's own statistics report it) |
| `admission` | an `AdmissionStats` (below); all zero without an admission |

**`AdmissionStats`**, counts of the carriers that reached the admission's
listener with a source slot, each counted once when its outcome is decided,
so that with an admission configured

    accepted + fallback + stalled + closed + stall_overflow
        + peek_timed_out + handshake_failed
        = carriers holding a source slot whose outcome is known

The `accepted` carriers then become pipe inputs, counted in the fields
above.

| Field | Counts |
|---|---|
| `accepted` | carriers decided Accept |
| `fallback` | carriers decided Fallback (also when, without a fallback target, they are stalled or answered not found) |
| `stalled` | carriers decided Stall whose stall began (counted at the decision) |
| `closed` | carriers decided Close, and decisions that failed, were cancelled or returned a k outside the prefix or an unknown verdict |
| `stall_overflow` | carriers decided Stall and closed at once because their source held its maximum of stalled carriers (Section 4.7) |
| `peek_timed_out` | carriers whose decision had not returned when the peek timeout ended |
| `handshake_failed` | carriers whose outer handshake failed or timed out before the admission; a QUIC handshake that fails inside the stack before the connection is accepted may not be counted |

Go `ListenerStats.Admission`, Rust `ListenerStats::admission`, Java
`ListenerStats.admission()`, Python `ListenerStats.admission`, each an
`AdmissionStats`. Only carriers that are decided are counted: the later
requests of an exchange carrier and the further streams of a QUIC
connection are not (Section 4.4); a further QUIC stream is a pipe input of
its own.

Adapters that run their own accept loops report their setup failures to
the listener: Go `Listener.CountSetupFailure()`, Rust
`Listener::count_setup_failure()`, Java `ZmuxListener.countSetupFailure()`;
the Python binding's adapters do it inside.

---

## 8. QUIC adapters

The same API runs over real QUIC (quic-go, quinn, netty-quic, aioquic
adapters):

- portable: streams, datagrams, close, RTT and byte statistics;
- best effort (accepted, may be hints): priorities, per-stream windows,
  reliable boundaries;
- zmux only (return "not supported"): pipes, resume, GOAWAY-based shutdown.

The QUIC carrier adapters (QUIC streams as pipes, Section 4.1) accept
connections in two ways:

- a listener's accept loop: `zmuxquic.Serve(ctx, ln, l, fallback)`;
- one established connection: `zmuxquic.ServeConn(ctx, qc, l, fallback)`
  (Rust `zmux_quinn::serve_connection`, Java `QuicServer.serve(QuicConnection)`,
  Python `await serve_quic_connection(conn, listener)`) hands a QUIC
  connection whose handshake has completed, from any source, to a listener
  exactly as `Serve` hands each connection it accepts: a source slot (the
  source is the connection's peer address), the admission on its first
  bidirectional stream (a connection that used 0-RTT is closed under an
  admission), the inbound transform and classification, and its later
  streams and lanes routed the same way; `Serve` runs it for every
  connection. It returns when the connection ends (no error) or ctx ends
  (ctx's error; the connection and its sessions continue, as with `Serve`),
  and closes the connection and reports the closed listener once the
  listener is closed.

The preamble is a zmux protocol feature; over QUIC adapters the libraries
write the same TLV block at the start of the stream so that applications
see identical open info.

---

## Appendix A. Helper layer (non-normative)

Libraries MAY offer these helpers. They are not part of the core: they
live in a separate package above the public API, drive only public
operations (Sections 4 and 5) and the `redial` hook, send nothing but what
those operations send, keep no state inside the session and carry no
conformance requirement beyond the tests of CONFORMANCE.md Section 4.7
for the libraries that offer them (CONFORMANCE.md Section 1). Anything
they do, an application can do with the public API alone.

- **Rotation.** `PipePool.Rotate` (`MaxBytes`, `MaxAge`, `Jitter`; unset
  by default), for the pipes that `MaintainPipes` keeps up (below): only the
  pool's `Dial` knows which path a pipe runs over, so rotation is a pool
  feature and not a per-pipe option. When a pipe of the pool has carried
  `MaxBytes` in either direction, or is `MaxAge` old (each reduced by a
  random fraction of up to `Jitter`, so that pipes opened together do not
  rotate together), the library dials a replacement through the pool's
  `Dial` with the same options and fate group (unless a carrier pool puts it
  on another underlying connection, whose fate group it then takes), joins
  it, and once the replacement is usable (out of probation) closes the old
  pipe gracefully (MULTIPATH.md Section 8.2): make before break, so nothing
  stalls and nothing is reinjected. If the dial fails, the old pipe stays
  and the library retries with backoff; it never closes the old pipe before
  its replacement is usable. Rotation applies only to a usable pipe: a
  failed pipe is redialled instead, and while the session lacks a direction
  (or is suspended) nothing rotates, so a rotation never delays the redial
  of a lost direction. The replacement needs a free attachment (`max_pipes`,
  MULTIPATH.md Section 5.4 step 9), so `max_pipes` must exceed the
  number of pipes kept up by the number that may rotate at once; without
  one the rotation waits. For a carrier with a hard lifetime limit the
  library starts the rotation early enough for the replacement's expected
  probation, base x 2^k with the current k of the fate group (SCHEDULER.md
  Section 11), plus a few round trips; `MaxAge` set below that limit by
  the default probation and a margin suffices while the path is stable.
  The end of an attachment being removed gracefully, in either direction,
  does not disturb the replacement in its fate group (MULTIPATH.md Section
  7.1). Uses: carriers with lifetime limits (CDN request durations, HTTP/2
  connection age or request limits, NAT mappings), and moving a long
  session's traffic over fresh carrier connections.
- **Carrier pools.** Adapters that carry several pipes over one underlying
  connection (HTTP/2 and HTTP/3 request streams) keep a pool of connections
  with a limit on the pipes per connection at once (`MaxPipesPerConnection`),
  over its lifetime (`MaxPipesPerConnectionLifetime`) and on its age
  (`MaxConnectionAge`); a new pipe goes to a connection within these limits,
  chosen by remaining room, else to a new connection. Pipes on one
  connection get one non-zero fate group automatically: they fail together
  and share a bottleneck (MULTIPATH.md Sections 2.2 and 14), so they are
  never counted as independent paths.
- **Keeping pipes up.** `c.MaintainPipes(ctx, PipePool{Dial, Count,
  Options, Rotate, Backoff})` keeps `Count` usable pipes dialled by `Dial`
  (one pool per path; several pools may run on one session):
  it redials failed pipes with exponential backoff (from 1 s, at most 60 s,
  with jitter), rotates them by `Rotate`, and stops when the context ends or
  the session closes. The backoff is measured from the start of the
  previous dial attempt, whether it succeeded or failed, and from the start
  of the pool when it has not dialled yet. A pool acts on the failure of a
  pipe, the loss of a direction and the suspension of the session when it
  happens (the pipe and session events of Section 4.5): it decides again at
  once, not at a later periodic check, so a pool whose
  pipes all fail dials at once when its previous attempt is older than the
  current backoff. It keeps dialling while the session is suspended (its
  joins rejoin the session, RESUME.md Section 4.3), and the pipes it dials
  get `Options`, `ProbeInterval` and `ProbeJitter` included. In a pool
  decision an unset `ProbeInterval` or `ProbeJitter` keeps what the pool's
  `Options`, and failing them the session, set; a set value applies to the
  pipes dialled under the decision, so turning idle probes off in the
  decision (`ProbeInterval` 0; in Go a named switch, since a negative
  probe interval is invalid in a pool decision) turns them off for
  that member (Section 4.2). A pool whose
  decisions come from an application policy rejects a decision that is
  invalid (for example a probe interval above 0 and below 1 s or a
  negative one, a negative dial delay, or a jitter outside [0, 1)), whatever implements the policy; no layer raises
  such a value to 1 s or otherwise corrects it: the pool treats it as
  one that keeps its own behaviour, reports the error through the pool's
  existing error or event channel of the language, and keeps maintaining
  its pipes; it neither stops nor drops the error silently. A pool
  member may be a group of pipes: with `Join` in place of `Dial` (`func(ctx,
  c, opts) ([]*Pipe, error)`, for example `HTTPSplitJoin`) the pool joins
  all pipes of a member itself; the member is usable while all of its
  pipes are, counts once against `Count`, and is redialled, rotated (make
  before break) and retired as a whole. Rust `PipePool::join`, Java
  `PipePool.join(Join)` (`Join` a functional interface over the pool's
  `Attempt`), Python `PipePool(join=...)`. A pool
  member may instead come from a mode (Section 4.8: Go `PipePool.Mode`
  with `PipePool.Policy`), which chooses its carrier kind for each dial
  and upgrades the pool's members to a preferred kind by make before
  break. It dials outside every lock and never blocks the core
  (IMPLEMENTATION.md Section 2.5).

Rust, Java and Python use the same names in their conventions
(`maintain_pipes`, `maintainPipes`; a `rotate` field of the pool). zmux keeps the
scheduling (tiers, weights, policies, quality estimates) in the session; the
helpers only decide when carriers are created and retired. In particular a
pool never picks pipes for traffic at random: which pipe carries a chunk is
the scheduler's decision (SCHEDULER.md).

### A.1 Limiter interface

One small interface lets any external limiter, budget or meter act on
bytes that zmux forwards itself (the splice helper below, and datagrams,
Section 5.6) without zmux knowing what it implements:

| Language | Interface |
|---|---|
| Go | `type Limiter interface { Ready(ctx context.Context, min, max int) (int, error); TryReady(max int) int; Charge(n int) }` |
| Rust | `trait Limiter: Send + Sync { fn poll_ready(&self, cx: &mut Context<'_>, min: usize, max: usize) -> Poll<usize>; fn try_ready(&self, max: usize) -> usize; fn charge(&self, n: usize); }` |
| Java | `interface Limiter { CompletionStage<Integer> ready(int min, int max); int tryReady(int max); void charge(int n); }` |
| Python | `class Limiter(Protocol): async def ready(self, min: int, max: int, /) -> int; def try_ready(self, max: int, /) -> int; def charge(self, n: int, /) -> None` (`zmux.Limiter`; positional-only, so an implementation names its parameters freely) |

- `ready(min, max)` (1 <= `min` <= `max`) waits until the limiter allows at
  least `min` bytes, or as many as it can ever allow at once if that is
  less (its burst), and returns how many it allows now, at most `max`.
  `min` keeps a caller at the edge of a budget from being fed one byte at
  a time. `try_ready(max)` returns the allowance at once, 0 when it would
  have to wait.
- Neither call reserves anything: the caller then forwards at most the
  allowance and calls `charge(n)` with what it actually forwarded. A
  `ready` that is cancelled (context ended, future dropped, task
  cancelled) therefore leaves no state behind. Callers sharing one limiter
  may each be allowed the same bytes before any of them charges, so a
  shared limiter can overshoot by up to one allowance per concurrent
  caller; the following `charge` calls account for it, and a limiter that
  must not overshoot hands out allowances it tracks itself.
- zmux calls the interface outside every lock of the session, never from
  a callback of the core, and never holds a buffer of its own while it
  waits in `ready`: waiting costs no memory and no copy. This holds for
  `try_ready` and `charge` too, although they do not wait: a limiter is
  application code, which may take a lock of its own (one shared by a
  group of sessions) or call into a session, and either one under the
  session's lock can deadlock against a thread that holds the limiter's
  lock and calls the session (IMPLEMENTATION.md Section 2.5.2). The
  datagram paths need no lock for it: a sender asks `try_ready` before it
  takes the lock and charges after it, once the datagram is queued; a
  received datagram leaves the core with the batch of the read that
  carried it and passes the limiter on the driver, outside the lock,
  before it enters the application's queue; one held for a stream not yet
  opened (EXTENSIONS.md Section 6.5) leaves the core, and passes the
  limiter, when the stream opens and it moves to that stream's queue.
  Received datagrams held while they wait for the limiter stay within the
  session's datagram byte limit (EXTENSIONS.md Section 6.5): the oldest
  are dropped, never buffered beyond it.
- A limiter shared by many streams or sessions (a group or hierarchy) is
  the limiter's own business; zmux only calls it.

### A.2 Splice (relay helper)

`zmux.Splice(ctx, a, b, opts)` (Rust `splice(a, b, opts).await`, Java
`Zmux.splice(a, b, opts)` returning a `CompletionStage`, Python `await
zmux.splice(a, b, chunk=, a_to_b=, b_to_a=)`) forwards two bidirectional streams, usually of
two different sessions, into each other: what arrives on `a` is sent on
`b` and what arrives on `b` is sent on `a`, until both directions have
ended. It is how a relay connects an inbound stream to an outbound one,
whether the streams carry application data or a nested session's pipe
(EXTENSIONS.md Section 10). The application opens the outbound stream and
sets its preamble first: it copies what the next hop needs, PIPE_CARRIER
(`SetPipeCarrier`, Section 5.1) when the inbound stream carries it, and
the application TLVs, adjusted as its conventions say (for example a
decremented hop limit, EXTENSIONS.md Section 4.7). The helper sends nothing
but what the stream operations it calls send.

Each direction runs this loop, from a receiving part `r` to a sending part
`w`:

1. wait for `w`'s send capacity (Section 5.1) with `min` = m = min(`opts.chunk`,
   the bytes readable on `r`, 16 KiB), so that bytes are taken from `r`
   only when `w` can transmit them within its peer's credit, never in
   slivers, and a peer window smaller than m cannot stall the splice;
2. with a limiter for the direction, `ready(min(m, capacity), min(capacity,
   opts.chunk))`;
3. take at most that many bytes from `r` as a chunk (Section 5.1), without
   copying, and hand the chunk to `w`;
4. `charge(n)` the limiter with the bytes forwarded, and count them.

Every wait of the loop (capacity, data on `r`, the limiter) also ends at
once on a stop, a session loss or a cancellation of the table below, so
an error is never held behind a missing capacity; a FIN, and a reset with
bytes below its Reliable Size still to forward, keep waiting for `w`'s
credit (EXTENSIONS.md Section 10.5) until those bytes are forwarded or
another event of the table ends the direction.

**Bounds.** Per direction the helper holds at most one chunk (at most
`opts.chunk`, default 64 KiB, and at most the capacity of step 1) between
step 3 and the moment `w` takes it; it never buffers anything else. The
backlog stays in `r`'s receive buffer, where the stream's own window
bounds it and its credit stops the upstream sender: backpressure crosses
the relay hop by hop through flow control. A relay's memory per spliced
direction is at most `r`'s receive window, plus what `w` holds unsent,
which step 1 keeps within `w`'s peer credit, plus one chunk. There is no
timeout and no byte limit in the helper; the streams' and sessions' own
failure detection ends a splice whose other side is gone.

**Ends and errors**, per direction:

| Event on the upstream side (`r`), or on `w` | Action |
|---|---|
| FIN read on `r` | finish `w` once the data before it is forwarded |
| `r` reset with code c and Reliable Size s | the bytes below s are delivered on `r` before the reset is reported (SPEC.md Section 7.5). If the helper has forwarded them all (always the case for ZMUX_CARRIER_FAILED, whose Reliable Size covers only the preamble), it marks a reliable boundary on `w` after them (unless s covers only the preamble) and resets `w` with c at once. Otherwise it forwards the rest as `w`'s credit allows (EXTENSIONS.md Section 10.5: a relay forwards data only within the next hop's credit), marks a reliable boundary on `w` after them (Section 5.1; the Reliable Size of `w`'s reset is then in `w`'s offsets, its preamble included), and resets `w` with c. When `w`'s session does not use RESET_STREAM_AT (a plain QMux peer that did not announce it), it resets `w` with RESET_STREAM once the forwarded bytes have been passed to the carrier; the peer may then discard bytes it has not yet read |
| `w` stopped (STOP_SENDING) with code c | stop `r` with c at once |
| `r`'s or `w`'s session closed or lost | reset and stop the other side's stream with ZMUX_CARRIER_FAILED at once |
| `ctx` cancelled, or the helper dropped | reset and stop both streams with code 0 (EXTENSIONS.md Section 7.4) at once |

Codes travel unchanged, so ZMUX_REJECTED and the proxy codes of
EXTENSIONS.md Section 7 reach the original opener with their meaning. The
two directions end independently, as half-closes do. When the streams have
datagram flows (EXTENSIONS.md Section 6.3), the helper also forwards each
stream's datagrams to the other stream, with their context IDs, without
waiting and without a queue of its own: a datagram passes the direction's
limiter as Section 5.6 describes (`try_ready` for its whole payload, then
`charge`), then the other session's own datagram limiter and send queue,
and one that any of them does not take is dropped and counted
(EXTENSIONS.md Section 6.7).

**Result.** Per direction the stream bytes and the datagram bytes
forwarded (together exactly what was charged to the direction's limiter),
the datagrams dropped, and how the direction ended: one of the rows of
the table above, finished, reset (with its code), stopped (with its code),
the upstream or the downstream session lost, or cancelled. Each language
says it once, in its own form (the table below): Rust a variant with the
code as its payload, Java a sealed type, Go a kind with a code, Python an
enum with the code beside it in `Forwarded.code`. Where cancelling is the
language's own cancellation of the call (Rust dropping the future, Java
cancelling the stage, Python cancelling the task), there is no result and
no cancelled end.

| Language | Signature |
|---|---|
| Go | `Splice(ctx, a, b *Stream, opts *SpliceOptions) (SpliceResult, error)`; `SpliceResult{AtoB, BtoA}` of `SpliceDirection{Bytes, DatagramBytes, DatagramsDropped, End}`, `End` a `SpliceEnd{Kind, Code}` with `Kind` one of `SpliceFinished`, `SpliceReset`, `SpliceStopped`, `SpliceUpstreamLost`, `SpliceDownstreamLost`, `SpliceCancelled`, and `Code` set for reset and stopped |
| Rust | `splice(a: (SendStream, RecvStream), b: (SendStream, RecvStream), opts: SpliceOptions { chunk, a_to_b, b_to_a }) -> Result<Spliced, SpliceError>`; `Spliced { a_to_b, b_to_a }` of `Forwarded { bytes, datagram_bytes, datagrams_dropped, end: SpliceEnd }`, `SpliceEnd::{Finished, Reset(code), Stopped(code), UpstreamLost, DownstreamLost}` |
| Java | `Zmux.splice(ZmuxStream a, ZmuxStream b, SpliceOptions opts)` returning `CompletionStage<SpliceResult>`; `SpliceResult(aToB, bToA)` of `Direction(streamBytes, datagramBytes, datagramsDropped, end)`, `end` a sealed `SpliceResult.End`: `Finished`, `Reset(long code)`, `Stopped(long code)`, `UpstreamLost`, `DownstreamLost`; cancelling the stage is the cancellation of the table |
| Python | `async def splice(a: tuple[SendStream, RecvStream], b: tuple[SendStream, RecvStream], *, chunk: int = 65536, a_to_b: Limiter \| None = None, b_to_a: Limiter \| None = None) -> Spliced`; `Spliced(a_to_b, b_to_a)` of `Forwarded(bytes, datagram_bytes, datagrams_dropped, end)`, `end` a `SpliceEnd` (`finished`, `reset`, `stopped`, `upstream_lost`, `downstream_lost`) with `code` for `reset` and `stopped`; splicing a stream into itself raises `ValueError`; cancelling the task is the cancellation of the table |

**Safety.** The helper's work runs outside every lock; it starts no task
that outlives the call; on every exit path, return, error, cancellation
or drop, both streams are ended as the table says and released, every
chunk in hand is released, and no limiter call is left pending. Splicing
a stream into itself, or into the other part of its own bidirectional
stream, is refused.

### A.4 Test helpers (Go)

Package `zmuxtest` holds helpers for tests of applications on zmux: a TCP
relay that ends connections idle or older than a limit and drops the
bytes of chosen directions without ending the connection, and an HTTP
reverse proxy that behaves as a default reverse proxy, a front that
buffers whole bodies, or a front that passes WebSocket only. It uses only
the standard library. Its API is experimental: it is outside the stability
promise of the other packages and may change in any revision; it is not
for production use. Other languages may offer their own.

### A.3 Duplicate sessions

`zmux.ResolveDuplicate(s1, s2, opts)` (Rust `resolve_duplicate`, Java
`Zmux.resolveDuplicate`, Python `resolve_duplicate`) applies the rule of
EXTENSIONS.md Section 11 to two established sessions that the application
has identified as connecting the same two endpoints. `opts` carries the
application identities of the two endpoints, when it has them. It returns
the survivor, and starts draining the other session: GOAWAY when it is in
use, no new streams, close with NO_ERROR once its streams have ended. With
`opts.rejoin` set and multipath in use on the survivor, it also calls the
application's dial function for the route of the losing session, if this
endpoint dialed it, and joins the new carrier to the survivor with
`AddPipe`. `rejoin` is a hint: the join is started in the background and
may not happen (this endpoint did not dial, no dial function, the
survivor's `max_pipes`, counted as Section 6 says, or the peer's cap and
the refusal backoff, MULTIPATH.md Sections 5.4 step 9 and 5.7); the result reports whether it was started, and a failed or skipped
join never undoes the resolution. The library knows which endpoint dialed each session it dialed
or accepted. It returns an error, and changes nothing, when the two
sessions have the same dialer or the rule has no input that decides.

| Language | Signature |
|---|---|
| Go | `ResolveDuplicate(s1, s2 *Conn, opts *DuplicateOptions) (DuplicateResult, error)` |
| Java | `Zmux.resolveDuplicate(a, b, DuplicateOptions)` returning `DuplicateResult` |
| Rust | `resolve_duplicate(s1: &Session, s2: &Session, opts: &DuplicateOptions { local_identity, peer_identity, rejoin }) -> Result<Resolved { survivor, loser, rule, rejoin_started }, DuplicateError>` |
| Python | `def resolve_duplicate(s1: Session, s2: Session, *, local_identity: bytes \| None = None, peer_identity: bytes \| None = None, rejoin: Callable[[], Awaitable[PipeIo]] \| None = None) -> Resolved`; `Resolved(survivor, loser, rule, rejoin_started)`; an undecidable pair or one with the same dialer raises `ValueError`. Synchronous like Rust: it only decides and starts the drain and the join |
