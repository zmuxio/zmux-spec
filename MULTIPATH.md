# zmux Multipath

Status: normative, part of the zmux core protocol (SPEC.md Section 11).
Every conforming implementation implements this document. A session uses it
when both endpoints advertise `zmux_multipath` (Section 3); the parameter
exists so that zmux can talk to plain QMux peers and so that sessions that do
not need multipath pay nothing for it (IMPLEMENTATION.md Section 2.6). A
zmux session without multipath uses only the acknowledgement of Section 6
on its single pipe pair, for queue control (Section 3.5).

This document defines the protocol: what goes on the wire and what each
endpoint does in response. How a sender estimates its pipes and chooses
among them is local sender policy, invisible on the wire, and is specified
separately in SCHEDULER.md.

---

## 1. Overview

A multipath session runs over a changing set of **pipes**. Each pipe is a
unidirectional, reliable, ordered byte stream provided by a pluggable carrier
adapter: a TCP or TLS connection (a pipe pair), a WebSocket, an HTTP request
body (uplink only), an HTTP response body (downlink only), a bidirectional
QUIC stream (a pipe pair), or any other reliable byte carrier. Pipes can be added and removed at any time.

Seen from the session, the set of pipes behaves like a datagram carrier that
can reorder records across pipes and loses records only when a pipe breaks.
The session therefore applies QUIC's stream semantics unchanged and replaces
QUIC's packet-level machinery with much simpler pipe-level machinery:

| QUIC mechanism | zmux multipath |
|---|---|
| packet numbers | implicit byte positions per pipe, or per lane of a pipe (no field on the wire) |
| ACK frames with ranges | PIPE_ACK: one cumulative position per inbound pipe (per lane, Section 2.5), sent on any reverse pipe |
| loss detection timers, PTO | none; loss happens only when a pipe fails, detected from acknowledgement progress |
| retransmission of information (RFC 9000 Section 13.3) | unchanged, applied to the unacknowledged part of a failed or suspect pipe |
| congestion control | none; each carrier does its own |
| path validation, connection IDs | authenticated PIPE_JOIN |
| connection migration | adding a pipe and removing another |

Every stream's data may be scattered frame by frame over all pipes in each
direction. Per-frame spreading is a first-class mode, not an exception: any
stream may be spread at any time, the `spread-eta` policy is tuned for it
(SCHEDULER.md Section 5), and receivers MUST handle any interleaving (Section 13).

---

## 2. Model

### 2.1 Pipes, pipe pairs and directions

- A **pipe ID** names one attachment of a carrier to the session. The
  attachment provides one pipe (an uplink or a downlink) or a pipe pair (a
  bidirectional carrier). A pipe ID is used for exactly one attachment in the
  life of a session.
- The endpoint that creates the attachment is its **joiner**; the other
  endpoint is its **acceptor**. The joiner of pipe ID 0 (the bootstrap pipes)
  is the initiator.
- Directions are named from the joiner: **up** is joiner to acceptor, **down**
  is acceptor to joiner, **bidi** is both.
- On the wire a single pipe is identified by its pipe ID together with the
  direction implied by the frame: PIPE_ACK lists pipes that are inbound to
  the frame's sender; PIPE_STATUS refers to the pipe that is
  inbound to its sender; PIPE_ABANDON refers to the whole attachment.

### 2.2 Fate groups

Each attachment carries a **fate group** chosen by its joiner. Fate group 0
means "independent". Attachments of the same joiner with the same non-zero
fate group are expected to fail together, for example an HTTP POST uplink and
an HTTP GET downlink multiplexed on one HTTP/2 connection, or two
connections through the same network interface. A pipe pair is always one
fate unit. Fate groups inform failure detection (Section 7.1), probation
(SCHEDULER.md Section 11) and fairness (Section 14); they never change wire
semantics.

Fate group numbers are scoped to the joiner: attachments joined by different
endpoints never share a fate group, whatever their numbers. Where both
endpoints join pipes (for example a relayed pipe joined by one endpoint and
direct pipes joined by whichever endpoint completes a connection first), a
replacement shares the fate of the pipe it replaces only if the same
endpoint joins it; an application that joins replacements from either side
gives each endpoint's joins their own groups, and the history that probation
keeps per fate group (SCHEDULER.md Section 11) then follows each joiner's
groups separately. Several pipes that are streams of one carrier connection
(for example QUIC streams of one QUIC connection, or HTTP/2 streams of one
TCP connection) fail together: those that one endpoint joins SHOULD be in
one non-zero fate group of that endpoint. To spread one pipe over several
streams of its connection, an endpoint uses lanes (Section 2.5), not several
pipes: pipes on one connection share its congestion controller, so each
one's estimates follow the others' load.

### 2.3 Positions

The **position** of a pipe (of each lane of a pipe that has lanes, Section
2.5) is a byte count from the first byte of the pipe's (the lane's)
record stream (the first byte after any out-of-band join information, and
after a leading QX_TRANSPORT_PARAMETERS record that the joiner discards,
Section 5.7 step 3). The
sender's position is the number of bytes passed to the carrier. The
receiver's position is the number of bytes in records that it has completely
received and processed (Section 6.1). On message-framed carriers each message
counts as the bytes of its byte-stream form (SPEC.md Section 3.4).

### 2.4 Carrier adapters

The core sees a pipe as a byte source or a byte sink plus close. The minimal
adapter interface (API.md Section 4) is:

- inbound pipe: `read(buffer) -> n | end | error`, `close()`;
- outbound pipe: `write(bytes) -> n | error`, `shutdown()` (graceful end of
  writing), `abort()`;
- optional: exporter access (Section 10.1), unsent-byte and RTT
  information for the scheduler (SCHEDULER.md Section 4), out-of-band join
  delivery (Section 5.3), lanes (Section 2.5).

Any carrier that delivers bytes reliably and in order can be attached,
including a stream of another zmux session (a nested session,
EXTENSIONS.md Section 10). If an
outbound pipe breaks, its unacknowledged information is re-sent on the other
pipes without the application noticing (Section 7).

**Carrier liveness and zmux's timers.** Many carriers detect a dead path
themselves (a QUIC or KCP connection's idle timeout or keep-alive, TCP
keep-alive or user timeout, a WebSocket ping). The two layers relate as
follows:

- A carrier that decides its connection is dead MUST be reported by the
  adapter at once, as a read or write error or an unexpected end; the
  endpoint then fails the pipe at once (Section 7.1), without waiting for
  T_suspect_p or T_fail_p. A carrier-level verdict is never held back
  until zmux's own detection agrees.
- An adapter or deployment MUST NOT configure a carrier idle timeout
  shorter than the pipe's probe interval (Section 7.1) plus a round trip:
  on an idle pipe the PIPE_PING frames of the probe interval and their
  answers are the only traffic, so a shorter carrier idle timeout closes
  healthy idle pipes. The same holds for an intermediary on the carrier's
  path that ends connections it sees idle for some limit: a deployment
  sets the probe interval of the pipes through it below that limit by at
  least a round trip. The probe interval is measured to the carrier
  (Section 7.1): a probe reaches the carrier within it however long the
  endpoint holds records before handing them on, so this rule needs no
  margin for such holding.
  With probes turned off on a pipe pair (a probe interval of 0 at both of
  its ends) zmux sends nothing on it while the session is quiet, and the
  carrier's idle timeout alone decides how long it lives. A carrier's keep-alive may run
  more often than the probe interval; zmux does not see it and needs none.
- While other pipes are ACTIVE, zmux's own detection (T_suspect_p,
  T_fail_p) governs a pipe whose carrier is silent but has not reported an
  error: a carrier with a longer idle timeout than T_fail_p does not delay
  failover. Only the last pipe of a direction is bounded by the longer
  T_dead_p, where a carrier timeout below T_dead_p may end it first.
- A carrier's own loss-recovery state (consecutive probe timeouts,
  retransmission backoff) is not an input to zmux's detection (Section
  7.1).

### 2.5 Lanes

A pipe whose carrier is one stream of a **multi-stream carrier connection**
(a QUIC connection, and HTTP/3 or WebTransport streams on one; an SCTP
association; a zmux session whose streams carry the pipes of a nested
session, EXTENSIONS.md Section 10; an HTTP/2 connection) can be carried over
several streams of that connection at once. Each of them is a **lane** of
the pipe:

- Lane 0 is the carrier stream on which the pipe started: the one that
  carried its PIPE_JOIN or, for an out-of-band join, its first record, and
  for the bootstrap pipe the bootstrap carrier. Lanes 1, 2, ... are streams
  of the same connection that the pipe's writer opens later, each starting
  with a PIPE_LANE frame (Section 5.8). A lane belongs to one pipe, that is
  to one direction of one attachment; the two directions of a pipe pair
  have lanes of their own.
- Each lane is a record stream of its own: it has its own position (Section
  2.3), counted from its first byte, and its own entries in PIPE_ACK
  (Section 6.2), and no record crosses from one lane to another. There is no
  order between the lanes of a pipe, as there is none between pipes (SPEC.md
  Section 4.1 rule 6); a session without multipath keeps the order that it
  needs by the placement rule of Section 5.8.
- Everything else belongs to the pipe: its attachment, pipe ID and state
  (STATE_MACHINE.md Section 3), its PIPE_STATUS, its estimates, its failure
  detection and its place in the scheduler (one pipe of one tier,
  SCHEDULER.md Section 9.2). A lane is not an attachment: it has no
  PIPE_JOIN and no token, takes no place in max_pipes + H (Section 5.4 step
  9) and never touches the replay window (Section 4.3). It is accepted only
  with the receiver's lane key and only on the carrier connection of its
  pipe's lane 0 (Section 5.8); Section 17 discusses why that suffices.
- The confirmed lanes of a pipe live and fail together. A lane is
  **confirmed** once a PIPE_ACK acknowledges a position greater than 0 on
  it (Section 5.8); before that it carries nothing but its PIPE_LANE, and a
  lane that ends before it is confirmed is **refused**, which costs the
  pipe nothing. The pipe ends gracefully when every lane has ended
  gracefully at a record boundary (Section 7.3); an error, a reset or an
  unexpected end of any confirmed lane is a failure of the pipe (Section
  7.1). A confirmed lane is never closed on its own: an idle lane sends
  nothing and costs only its reading state.
- **Send order and the cumulative acknowledgement point.** The sender
  numbers the bytes it hands to the lanes of a pipe in the order it hands
  them over, across lanes, except the first record of a lane (its
  PIPE_LANE), whose acknowledgement only confirms the lane; this **send
  order** is local and never on the wire, and a refused lane therefore
  leaves nothing in it. The pipe's **cumulative acknowledgement point** is the send-order
  number of its oldest byte that is not yet acknowledged, or of the next
  byte when all are acknowledged; without lanes it is the acknowledged
  position. Wherever this document and SCHEDULER.md refer to a pipe as a
  whole by its acknowledged position (its RTT and delivery-rate samples and
  bytes in flight, Section 6.6 and SCHEDULER.md Sections 2 and 9.1; the ack
  stall and the return from SUSPECT, Section 7.1; the last progress of
  Section 5.4 step 9), a pipe with lanes uses its cumulative acknowledgement
  point, and the number of bytes sent is counted in send order; only the
  carrier queue limiter leaves out of its bytes in flight the bytes that
  their own lanes acknowledged and that a hole on another lane has held
  back for a whole round trip, and all of them during start-up
  (SCHEDULER.md Section 9.1). Lane
  positions serve to acknowledge frames (SPEC.md Section 7.4): a frame is
  acknowledged, and is "beyond the acknowledged position" for reinjection
  (Sections 7.2, 7.4 and 8.2), by the position of the lane that carried it.
  The cumulative point moves exactly as the position of the same pipe
  without lanes would, so the estimators and the failure detection need no
  rule of their own; a lane that stops while others advance holds it back,
  and the ack stall finds a dead lane as it finds a dead pipe. Measuring by
  lane would mislead the sender:
  delivery-rate samples taken from each lane's advance overstate R_p (a
  lane released from head-of-line blocking acknowledges at once bytes sent
  before the sample interval began), RTT samples by lane mix blocked and unblocked
  lanes and raise rttvar_p and T_suspect_p, and an ack stall judged by lane
  marks a pipe suspect for the two or three round trips that a lost
  retransmission holds one lane at 10 Gbit/s.
- **Carriers.** Lanes are an optional capability of a carrier adapter (API.md
  Section 4.1): it opens a new stream on the connection of a pipe's lane 0
  and hands over each new inbound stream together with the identity of its
  connection. A pipe pair on a QUIC connection (and on HTTP/3 or
  WebTransport over one), pipe 0 included, is always one bidirectional
  stream, whose two halves are lane 0 of the two directions; it is never
  split into a unidirectional stream per direction, and a pipe joined in
  one direction only (Section 5.1) is a bidirectional stream too, its
  reverse half unused. A further lane is a unidirectional stream on a QUIC
  connection and on WebTransport; on carriers whose further streams are
  bidirectional (HTTP/3 request streams, SCTP, HTTP/2, the streams of an
  outer zmux session, which carry PIPE_CARRIER, EXTENSIONS.md Section 10)
  it is a bidirectional stream whose reverse half is unused: the reader
  ends that half at once, and its end is no event of the pipe. A lane needs a
  stream that its writer can open toward its reader: an HTTP/3 server
  cannot open a request stream, so a server's lanes need WebTransport or an
  adapter convention by which the client opens them for it. They pay off where the streams of a connection are
  delivered independently, so that a loss on one does not hold back the
  others (QUIC, SCTP, the streams of a zmux session whose own pipes are
  delivered independently): a pipe on one stream then makes all its data
  wait for any of its lost packets, which mostly delays urgent messages and
  short streams behind bulk data on the same pipe. On
  a connection whose streams share one ordered byte stream (HTTP/2 over TCP,
  a zmux session over a single TCP pipe) lanes are allowed but recover
  nothing, and an adapter SHOULD NOT open them there by default. A carrier
  with one stream per connection (TCP, TLS, WebSocket, KCP) has no lanes; a
  further pipe on another connection needs a PIPE_JOIN. A connection that an
  intermediary splits (an HTTP/3 proxy that terminates QUIC) does not reach
  the peer as one connection, so lanes opened on it are refused (Section
  5.8) and the pipe stays on lane 0. Lanes through a relay that forwards
  each stream on as a stream of its own connection to the peer (EXTENSIONS.md
  Section 10.5) reach the right pipe, because the key and the pipe ID
  travel with them, but recover nothing unless the relay's own carriers
  deliver streams independently.

---

## 3. Negotiation

### 3.1 The `zmux_multipath` transport parameter

ID 0x30dc2978. Value:

```text
zmux_multipath Value {
  Secret Flags (8),
  Session Nonce (128),
  Locator Length (8),
  Session Locator (0..128),
}
```

- **Secret Flags**: the secret sources the endpoint can provide (Section
  10.1): 0x01 E (exporter of the bootstrap carrier's innermost end-to-end
  handshake), 0x04 P (pre-shared
  key), 0x08 I (insecure join allowed). Other bits, 0x02 included, are
  reserved; senders set them to 0 and receivers ignore them.
- **Session Nonce**: 16 bytes chosen uniformly at random for this session.
- **Session Locator**: 0 to 16 bytes that the peer puts into PIPE_JOIN to
  reach this session through this endpoint's listener. Its content is chosen
  by the endpoint (for example a random identifier, optionally with an
  instance hint for load-balancer stickiness). Length 0 means that this
  endpoint does not accept joins. A Locator Length above 16, or a value whose
  length disagrees with Locator Length, is a TRANSPORT_PARAMETER_ERROR.

### 3.2 Companion parameters

| Parameter | ID | Value | Absent means | Range |
|---|---|---|---|---|
| zmux_ack_threshold | 0x2762c7e5 | bytes: the sender of the parameter acknowledges no later than this many ack-eliciting bytes on any pipe | 65536 | at least 1024 |
| zmux_max_ack_delay | 0x17fa1ed6 | milliseconds: the sender of the parameter acknowledges no later than this after receiving an ack-eliciting byte | 25 | below 2^14 (as QUIC's max_ack_delay, RFC 9000 Section 18.2) |

These parameters MUST NOT be sent without `zmux_multipath` or `zmux_pipe_ack`
(Section 3.5; a TRANSPORT_PARAMETER_ERROR, SPEC.md Section 5.5). A value out
of range is a TRANSPORT_PARAMETER_ERROR. The ranges are safety bounds only: an
acknowledgement threshold below 1024 bytes would let a peer demand a PIPE_ACK
for almost every record, and an acknowledgement delay of 2^14 ms (16 s) or
more would push failure detection beyond any useful time. QUIC's
`max_ack_delay` (0x0b) remains prohibited.

### 3.3 Enablement

Multipath is in use when both endpoints sent `zmux_multipath` and the secret
selection of Section 10.1 yields a source. If both sent the parameter but no
source is selected, multipath is not in use; both endpoints know this from the
two parameter values, and the API reports "join security unavailable"
(API.md Section 5.4). Each endpoint decides from configuration whether to send
`zmux_multipath`; zmux implementations send it when the application enables
more than one pipe or enables resume (API.md Section 6).

### 3.4 What changes in a multipath session

Only in multipath sessions:

1. STREAM data may arrive out of order and duplicated (SPEC.md Section 7.3).
2. A pipe failure is not a session failure (SPEC.md Section 6.6); the loss of
   the last pipe in a direction is handled by Section 8.5.
3. The idle timer and the keep-alive timer restart only on application
   activity received or acknowledged, not on every record, and the idle
   timer's expiry sends CONNECTION_CLOSE(ZMUX_IDLE_TIMEOUT) (SPEC.md Section
   6.4). This item also applies with pipe acknowledgement (Section 3.5).
4. Information is acknowledged by PIPE_ACK, and endpoints retain
   unacknowledged information (SPEC.md Section 7.4, SCHEDULER.md Section 13.1).
5. CONNECTION_CLOSE is sent on every outbound pipe (SPEC.md Section 6.5).

### 3.5 Pipe acknowledgement without multipath

A sender whose carrier neither reports its unsent bytes nor has a low-water
mark cannot see the queue it builds in that carrier; only acknowledgements
show it (SCHEDULER.md Section 9.1). zmux endpoints therefore acknowledge
the single pipe pair of a session without multipath as well.

**Parameter.** `zmux_pipe_ack`, ID 0x34b47621, an empty value (a non-empty
value is a TRANSPORT_PARAMETER_ERROR). zmux endpoints send it in every
session (SPEC.md Section 5.5). **Pipe acknowledgement** is in use when both
endpoints sent it and multipath is not in use; a multipath session
acknowledges its pipes by Section 6 anyway. A plain QMux peer ignores the
parameter (QMux-02 Section 5.1), so pipe acknowledgement is never in use
with it and it never sees a PIPE_ACK (SPEC.md Section 13.2). An endpoint
MUST NOT send PIPE_ACK in a session where neither feature is in use; a
receiver treats such a frame as unknown (SPEC.md Section 4.1 rule 3). The
only PIPE_ACK sent before the endpoint knows is the fence of early data
(SPEC.md Section 6.1.2), whose single entry has Position 0 and is ignored
where PIPE_ACK is in use, and which a peer without it rejects before
processing anything after it.

**Rules.** While pipe acknowledgement is in use, each endpoint acknowledges
its inbound pipe 0 (the bootstrap pipe, Section 4.2) as in Sections 6.1 to
6.3, with these differences:

1. Positions (Section 2.3) count from the first byte of the pipe, the
   QX_TRANSPORT_PARAMETERS record included.
2. PIPE_ACK lists only pipe 0, at most one entry per lane of it; any other
   Pipe ID is a PROTOCOL_VIOLATION (a repeated entry is a
   FRAME_ENCODING_ERROR, Section 6.2), and a Position beyond the bytes the
   receiver has sent on that lane of pipe 0 is a PROTOCOL_VIOLATION as in
   Section 6.2. Without lanes every PIPE_ACK is a complete snapshot of its
   one entry; with lanes a snapshot may omit the lanes whose position has
   not changed, with a complete one at least once per second, as Section
   6.2 says.
3. An endpoint acknowledges by rules 1 and 2 of Section 6.3 only: when its
   own `zmux_ack_threshold` (65536 bytes unless sent) of ack-eliciting
   bytes have arrived since its last PIPE_ACK, and no later than its own
   `zmux_max_ack_delay` (25 ms unless sent) after the first of them. There
   is no PIPE_PING and no other multipath frame. It SHOULD put the PIPE_ACK
   into a record it writes anyway (credit updates, stream data), and writes
   a record of its own only when a deadline would otherwise pass. PIPE_ACK
   is not ack-eliciting, so acknowledgements never answer each other.
4. Nothing is retained (SPEC.md Section 9.6): a PIPE_ACK acknowledges
   nothing that could be sent again, and the sender keeps per range in
   flight only the sample state of SCHEDULER.md Section 2 (send time and
   delivery counters), which the next PIPE_ACK releases. Items 1, 2, 4 and
   5 of Section 3.4 do not apply: data stays in order and a pipe failure
   ends the session. Item 3 applies: the idle and keep-alive timers count
   application activity received or acknowledged (a record that carries
   only PIPE_ACK or credit frames restarts neither), the idle timeout sends
   CONNECTION_CLOSE(ZMUX_IDLE_TIMEOUT), and a graceful end of the peer's
   pipe without CONNECTION_CLOSE is a pipe failure, not an idle close
   (SPEC.md Section 6.4).

The acknowledgement feeds only the sender's estimates (SCHEDULER.md Section
2) and, on a carrier without a low-water mark or unsent count, the carrier
queue limiter (SCHEDULER.md Section 9.1), and the liveness that the idle
timer measures (SPEC.md Section 6.4); it plays no other part in failure
detection: no ack stall fails the pipe. Its cost is one PIPE_ACK of about 10 bytes per 64 KiB received
or per 25 ms, mostly inside records that are sent anyway (CONFORMANCE.md S-9a bounds
it at 0.1 % of a bulk transfer).

### 3.6 The `zmux_max_lanes` transport parameter

ID 0x27856ef0. Value:

```text
zmux_max_lanes Value {
  Max Lanes (i),
  Lane Key (128),
}
```

- **Max Lanes** L: the number of lanes, lane 0 included, that the sender of
  the parameter accepts on each of its inbound pipes (lanes 0 to L - 1).
  0 is a TRANSPORT_PARAMETER_ERROR.
- **Lane Key**: 16 bytes chosen uniformly at random for this session by the
  sender of the parameter. Every PIPE_LANE toward it carries the key, which
  names the session among all those of the receiver and which nobody
  outside the session knows (Section 5.8).

A value of any other length is a TRANSPORT_PARAMETER_ERROR. Absent means
L = 1: no lanes. Like the parameters of Section 3.2 it MUST NOT be sent
without `zmux_multipath` or `zmux_pipe_ack` (a TRANSPORT_PARAMETER_ERROR,
SPEC.md Section 5.5); sent with either while neither feature ends up in use,
it is valid and lanes are not available.

**Lanes are available** toward an endpoint when it sent L > 1 and multipath
or pipe acknowledgement is in use. The feature is one-directional: each
endpoint opens lanes on its outbound pipes as the peer's L allows, whatever
it announced itself, and only where its carrier adapter offers them (Section
2.5). A plain QMux peer never sends the parameter, so its pipe is lane 0 and
it never sees a PIPE_LANE or a non-zero Lane (SPEC.md Section 13.2).

zmux endpoints send the `max_lanes` setting (API.md Section 6), by default
258: the default stream concurrency of 256, plus lane 0 and the copy lane of
SCHEDULER.md Sections 4.1 and 9.2. It bounds what the receiver keeps per
pipe (one incomplete record per lane, within the session's budget for
incomplete records, SPEC.md Section 9.8); it is not a target: a writer
opens only the lanes it uses (SCHEDULER.md Section 9.2), and lanes beyond the
number of stream directions sending at once change nothing. Any value is allowed; 1 turns lanes off.

---

## 4. Pipe identifiers

### 4.1 Allocation

A pipe ID is a variable-length integer. Its least significant bit is the
joiner's role: 0 for the initiator, 1 for the responder. Pipe ID 0 is the
bootstrap pipe pair. Each joiner allocates its pipe IDs in increasing order
(initiator 2, 4, 6, ...; responder 1, 3, 5, ...).

Each joiner takes the next pipe ID of its parity for every join attempt,
whether or not the carrier connects, and never uses a pipe ID for a second
attempt or a second attachment. A joiner needs nothing from its peer to
join: pipe IDs are not credit, and the acceptor bounds what joins can cost it
with the cap of Section 5.4 step 9 and the replay window of Section 4.3. A
joiner that has used the largest pipe ID of its parity cannot join again in
that session (at a million joins per second that takes tens of thousands of
years).

### 4.2 Bootstrap pipes

The bootstrap pipe pair has pipe ID 0, joiner the initiator, fate group 0.
Its up direction is the initiator's outbound bootstrap pipe. When the
bootstrap uses two unidirectional carriers paired with a pairing token
(SPEC.md Section 6.1.1, an optional feature), they are still pipe 0 up and
pipe 0 down and still one attachment: when either carrier fails, the
endpoint fails the attachment and closes the other carrier too (Section
7.3). A healthy direction is replaced by joining a new pipe.

### 4.3 Replay window

For the pipe IDs of the peer's parity an endpoint keeps h, the highest pipe
ID of a PIPE_JOIN it has verified (Section 5.4 step 5), and a bitmap that
covers the W pipe IDs of that parity below h and the W above it. For an
initiator peer h starts at 0, the bootstrap pair; for a responder peer, which
has no attachment yet, the bitmap covers its first W pipe IDs until its
first verified join. W is a local safety bound of at least max_pipes + H (H,
Section 5.4 step 9), counted in pipe IDs of that parity; the default is
max(64, 2 x (max_pipes + H)).

- A bit is set when its ID is attached, and when the ID is recorded as used
  without being attached: a refused join (Section 5.4 step 7), or
  PIPE_ABANDON for an ID not seen joined (Section 8.1).
- Only a verified PIPE_JOIN raises h. When h rises, bits that fall below the
  bitmap are dropped, together with any PIPE_ABANDON pending for an ID that
  has no attachment state.
- The bitmap decides only for IDs without attachment state. An attachment
  keeps its state until it is retired, however far below h its ID has
  fallen.

A pipe ID of the peer's parity is **stale** when it is more than W IDs of
that parity below h. An ID of the peer's parity that has no attachment state
and whose bit is set, or that is stale, is treated as retired.

A frame that references a pipe ID of the receiver's own parity that the
receiver has not yet taken (Section 4.1) is a PROTOCOL_VIOLATION: the peer
cannot know of such an attachment. Frames that reference an ID of the peer's
parity without attachment state follow Sections 6.2, 8.1 and 9. Because
only verified joins move the window, frames injected on an unprotected
carrier cannot push it past the joiner's future pipe IDs; they can at most
set bits within it, which the joiner skips over by taking new IDs.

---

## 5. Joining a pipe

### 5.1 The PIPE_JOIN frame

```text
PIPE_JOIN Frame {
  Type (i) = 0x1a24de36,
  Locator Length (8),
  Session Locator (8..128),
  Pipe ID (i),
  Flags (8),
  Fate Group (i),
  Priority (i),
  Join Token (128),
}
```

- **Locator Length / Session Locator**: the acceptor's locator from its
  `zmux_multipath` parameter, 1 to 16 bytes.
- **Pipe ID**: the new attachment's pipe ID (Section 4.1).
- **Flags**: bits 0-1 (mask 0x03) direction: 0 up, 1 down, 2 bidi, 3 invalid.
  Bit 2 (0x04) carrier-bound: the token covers the exporter of the
  innermost end-to-end handshake that carries the new carrier, such as its
  TLS connection or the QUIC connection whose stream it is (Section 10.5). Bit 3 (0x08) standby. Bits 4-7 (mask 0xf0) are
  reserved and MUST be 0.
- **Fate Group**: Section 2.2.
- **Priority** and the standby flag: the joiner's initial request for the
  acceptor's outbound direction of the new attachment, with the meaning of a
  PIPE_STATUS request (Section 9) at sequence number 0. For direction up the
  acceptor has no outbound direction on the attachment: the joiner sends
  Priority 0 without standby, and the acceptor ignores both.
- **Join Token**: 16 bytes, Section 10.5.

The whole frame, from Type to Join Token, MUST NOT exceed 64 bytes; with the
fields above it is at most 62 bytes and at least 26. The frame is not
retransmitted.

### 5.2 In-band joining

For an attachment whose joiner can write on it (direction up or bidi), the
joiner MAY send PIPE_JOIN as the first frame of the first record of its
outbound pipe. Other frames MAY follow in the same record and in later
records immediately, without waiting for any answer: a join costs the
acceptor no round trip, and control frames, and stream data while no other
pipe is usable (rule S1 of Section 11.1), go out with it. While other pipes
carry the traffic, new stream data waits for the confirmation (Section 5.7,
one round trip) and the probation of SCHEDULER.md Section 11; the session
does not wait, only the new pipe's share.

The join record starts with PIPE_JOIN. It MUST then carry a complete
PIPE_ACK snapshot (Section 6.2) when the joiner has at least one entry, as
the first record on every new outbound pipe does (Section 5.7 step 2), and
SHOULD then carry a PIPE_PING. The snapshot tells the acceptor at once what
the joiner has received, which after a suspension is where retransmission
starts (RESUME.md Section 5); it may continue at the start of the next
record when it does not fit in the first. The PIPE_PING makes the acceptor
acknowledge at once
(Section 6.3) instead of after its acknowledgement delay, which confirms the
join and gives the pipe its first RTT sample up to max_ack_delay earlier; it
counts against the limit of Section 6.4. An endpoint that delays records
before handing them to the carrier (Section 7.1, H_p) delays the first
record of a pipe whole, the join record and the first record of an
accepted join (Section 5.4 step 8) alike: it keeps the snapshot and the
PIPE_PING in it, the snapshot current when the record is sent, and frames
that fall due in the meantime join it.

A first PIPE_JOIN is processed by the listener (Section 5.4), never as a
session frame, and its problems only ever close the carrier. On a pipe that
already belongs to the session, a PIPE_JOIN frame is a PROTOCOL_VIOLATION if
it decodes, and a FRAME_ENCODING_ERROR if it does not (decoding errors take
precedence, SPEC.md Section 4.1).

### 5.3 Out-of-band joining

For an attachment whose joiner cannot write first (direction down, for
example an HTTP GET response), and optionally for any other attachment, the
joiner gives the PIPE_JOIN frame (Type through Join Token) to its carrier
adapter, which delivers it to the acceptor's adapter outside the pipe. The
pipe's record stream then starts directly with ordinary records.

The adapters of both ends agree on how the frame travels and how it is
encoded; it carries exactly the bytes from Type through Join Token, and it is
never placed where intermediaries commonly log it, such as a URL.

The adapter of the acceptor passes the out-of-band frame to the listener
together with the carrier; the listener processes it as in Section 5.4.

### 5.4 Acceptor processing

An acceptor (normally a listener that serves many sessions, API.md Section
4.4) processes a new carrier as follows. Steps 1 to 6 MUST allocate no session
state and MUST perform no session operation; they may hold the carrier and at
most one record (in-band) or 64 bytes (out-of-band) of it.

1. Apply the pending-join limits of Section 5.6 (except to the streams
   admitted for a lane, Section 5.5). On excess, close the carrier.
2. Classify the first bytes (Section 5.5) or take the out-of-band frame.
   Parse PIPE_JOIN. On any parse failure or invalid field, close the carrier.
3. Look up the Session Locator. If no session matches, close the carrier.
   An acceptor configured to answer unknown joins (API.md Section 4.4; off
   by default) first writes one fixed record on the carrier when the
   carrier was admitted (API.md Section 4.7), the join passed step 2 and
   the locator begins with the acceptor's instance identifier (every
   locator, without one): a record holding only CONNECTION_CLOSE 0x1c with
   Error Code ZMUX_RESUME_FAILED, Frame Type 0 and an empty Reason Phrase,
   that is the bytes `07 1c 8f 05 f7 4e 00 00` on a byte-stream carrier
   and the 7-byte message `1c 8f 05 f7 4e 00 00` with message framing,
   after the QX_TRANSPORT_PARAMETERS of a shared listener if it sent them
   (SPEC.md Section 13.2 rule 7), and then ends the carrier gracefully.
   A carrier without a sending half (a lane, an HTTP split uplink request,
   whose response is not a carrier) gets no answer and is closed.
   Every other failure of steps 1 to 6 sends nothing. The answer tells a
   party that already passed the admission only that the locator it
   presents names no session of this instance: such a party gets the
   listener's own transport parameters by starting a session anyway, an
   acceptor that answers MUST put at least 8 bytes chosen uniformly at
   random after its instance identifier in every locator, so that no
   locator can be guessed, and a bad token or a replay
   stays silent, so the answer is no oracle for tokens or secrets; it is
   8 bytes, no more than the join, and bounded by the source slots. It is
   off by default because it is wrong where a locator with this
   instance's identifier can reach another instance that holds the
   session.
4. If the session is not yet established (the joiner's transport parameters
   are still in flight), hold the join until establishment completes or the
   handshake timeout expires.
5. Verify the Join Token in constant time (Section 10.5), always with the
   join key of the peer's role (the session knows which role the peer has),
   never with a role inferred from the Pipe ID's parity. On failure, close
   the carrier and report a **join authentication failure** to the
   application, a distinct event and statistic (API.md Section 5.4) whose
   reason says whether the token was carrier-bound (ZMUX_PIPE_AUTH_FAILED is
   its local code). Nothing is sent to the peer: the joiner learns only that
   the carrier closed. A stream of such failures for a session whose secret
   comes from an exporter usually means that an intermediary terminates
   the handshake it was taken from (Section 10.7).
6. If the Pipe ID has the joiner's parity and has attachment state, its bit
   is set, or it is stale (Section 4.3), close the carrier: the join is a
   replay or arrived too late. Count it in the statistic `join_replays`. A
   Pipe ID of the other parity goes on to step 7.
7. From here on the join is authenticated, so violations are the peer's:
   - wrong parity for the joiner: close the session with
     PROTOCOL_VIOLATION;
   - a direction the carrier cannot provide (for example "up" on a
     carrier the adapter reports as write-only for the acceptor): close the
     carrier and send PIPE_ABANDON(Pipe ID, ZMUX_PIPE_FAILED);
   - a carrier that is a stream of a session running, directly or through
     other sessions of the process, over a pipe of this session, as far as
     the acceptor can see (EXTENSIONS.md Section 10.2, API.md Section 4.6;
     a local condition, not a violation): close the carrier and send
     PIPE_ABANDON(Pipe ID, ZMUX_PIPE_RESOURCE_LIMIT), so that the joiner,
     which may not see the loop, backs off (Section 5.7) instead of
     rejoining at once. The check and the recording that the session now
     runs over the carrier are one atomic step of the process
     (IMPLEMENTATION.md Section 2.5.1); an implementation that can still
     find a loop after step 8 (one formed concurrently through another
     session) removes the attachment abruptly with the same
     PIPE_ABANDON(Pipe ID, ZMUX_PIPE_RESOURCE_LIMIT), never with
     ZMUX_PIPE_FAILED, which the joiner would answer by rejoining at once;
   - resource exhaustion, including the cap of step 9: close the carrier and
     send PIPE_ABANDON(Pipe ID, ZMUX_PIPE_RESOURCE_LIMIT).

   In each non-fatal case the acceptor sets the Pipe ID's bit (Section 4.3)
   and raises h if the ID is above it; the joiner retires the attachment
   when that PIPE_ABANDON arrives, or unilaterally (Section 8.4).
8. Attach the pipe(s): set the Pipe ID's bit, raise h if the ID is above it,
   create pipe state, take the joiner's initial request from the PIPE_JOIN
   (Section 9), and process the rest of the record (in-band) and then the
   pipe's records. The acceptor MAY write on its outbound direction of the
   attachment at once, and for a down or bidi attachment MUST write at least
   one record on it within its `zmux_max_ack_delay`, so that the joiner
   learns that the join succeeded (Section 5.7 step 4). That first record
   MUST carry a complete PIPE_ACK snapshot (Section 6.2) when the acceptor
   has at least one entry, and a PIPE_PING, subject to the PIPE_PING limit
   of Section 6.4 (a pipe whose PIPE_PING is delayed by the limit gets it as
   soon as the limit allows): the snapshot tells the joiner what the
   acceptor has received, and the acknowledgement of the PIPE_PING gives the
   new outbound direction its first RTT and rate samples, which a record of
   PIPE_ACK alone, not being ack-eliciting, never would (SCHEDULER.md
   Section 2). An acceptor with a non-default request for the joiner's
   outbound direction puts its PIPE_STATUS in the same record (Section 9).
   There is no separate "joined" frame: the first acknowledgement or record
   is the confirmation. A listener that sent its own QX_TRANSPORT_PARAMETERS
   on the carrier before it classified it (SPEC.md Section 13.2 rule 7)
   counts its outbound position from the end of that record (Section 5.7
   step 3).
9. Cap on unretired attachments: an acceptor keeps at most max_pipes + H
   attachments joined by the peer that are not retired (live, or failed or
   abandoned but not yet retired, Section 8.4). H, the **attachment
   headroom**, is 8 by default (API.md Section 6, the `headroom` parameter
   of `max_pipes`): it is room for failed attachments whose retirement has
   not completed, which matters most while rejoining after a suspension
   (RESUME.md Section 4.3). The bootstrap pair counts as an attachment joined
   by the initiator (Section 2.1). The count is taken after the join or
   failure being processed has been applied. Once the cap is reached:
   - outside suspension, the acceptor refuses an authenticated join beyond
     the cap as resource exhaustion (step 7); that is not a session error. A
     refused join is never attached and does not count against the cap: it
     leaves only its bit and its pending PIPE_ABANDON, both inside the
     replay window of Section 4.3;
   - while the acceptor is SUSPENDED (RESUME.md Section 4), the PIPE_ABANDON
     exchanges that retirement needs cannot complete, so instead of refusing
     the join it **displaces** one attachment of the peer and then accepts
     the join. It retires locally the oldest unretired attachment of the
     peer that has failed and whose own PIPE_ABANDON has not been
     acknowledged, dropping its state including that pending PIPE_ABANDON.
     If none has failed, all of them are live, and if the join provides a
     direction the acceptor lacks, none of them provides that direction (the
     acceptor would not be suspended otherwise): it then fails the one whose
     **last progress** (the later of the last advance of its outbound
     acknowledged position, the last record received on its inbound
     direction and its attachment) is oldest, as Section 7.2 says but
     without sending PIPE_ABANDON, and retires it locally at once. A join
     that provides no direction the acceptor lacks is refused as outside
     suspension. Pipe IDs are never reused and frames for a retired
     attachment are ignored (Section 8.4), so this is safe; the count never
     exceeds the cap, and with a cap of at least 2 (9 with the defaults) the
     displaced live attachment is never the acceptor's last pipe in the
     direction it still has.

   A joiner applies the same cap to the attachments it joined itself: it does
   not join while it has max_pipes + H of them unretired, except that a
   joiner that is SUSPENDED first displaces one of them in the same way
   before a join that provides a direction it lacks: the oldest failed one
   whose own PIPE_ABANDON has not been acknowledged, otherwise the live one
   with the oldest last progress. Together with unilateral retirement (Section 8.4) this
   bounds the attachment state a peer can make an endpoint keep and the size
   of PIPE_ACK snapshots (Section 6.2), and it never stops a suspended
   session from resuming, however many rejoin attempts were cut off. A peer
   may therefore have up to max_pipes + H attachments live at once;
   max_pipes is what each endpoint aims for with its own joins.

### 5.5 Listener classification

A listener that accepts both new sessions and joins classifies a new carrier
by the first frame of its first record, applying the incremental check of
SPEC.md Section 6.2 with two alternatives:

- `ff 51 53 30 0d 0a 0d 0a` after a Size from 9 to the listener's own
  `max_record_size`: a new session;
- `9a 24 de 36` (PIPE_JOIN) after a Size from 26 to the listener's own
  `max_record_size`: a join;
- `ba 05 a1 c9` (PIPE_LANE) after a Size from 22 to the listener's own
  `max_record_size`: a lane (Section 5.8).

Any other prefix is rejected as soon as it is seen. Carriers that deliver an
out-of-band join skip classification. An adapter that hands a new stream of
a connection already carrying pipes of the endpoint directly to the
endpoint's sessions, without a listener, classifies the same way.

**Lanes and the carrier limits.** The streams that a lane needs are not
held to the carrier limits of Section 5.6 (the listener, session and source
bounds and the eviction), and no other stream escapes them. A new stream
that the adapter delivers with the identity of a multi-stream carrier
connection (API.md Section 4.1) is admitted outside those bounds only when,
at the moment it arrives, the connection carries lane 0 of at least one
**OPEN inbound pipe** of one of the endpoint's sessions (STATE_MACHINE.md
Section 3.2: the bootstrap pipe of an established session, or a pipe
attached by a verified join, step 8 of Section 5.4, or as joiner by its
first record, Section 5.7 step 4), and fewer
streams of the connection admitted this way are still awaiting their first
record than it carries such pipes (a writer has at most one lane that is
not yet confirmed per pipe, Section 5.8). Every other stream takes a slot
like any carrier. An admitted stream is classified as above: a lane is decided by
its first record (Section 5.8) within the first-record timeout, outside
the bounds throughout; a stream that turns out to be a join or a new
session takes a slot at that moment, as an unauthenticated carrier (refused
or evicting as Section 5.6 says), and goes on through Section 5.4 with both
timeouts. The rule is a security property: a peer does not need to
authenticate to establish a session, so an established session on the
connection must not open it to unbounded pending carriers; with the rule a
peer holds at most one pending stream per OPEN inbound pipe outside the bounds,
and the pipes themselves are bounded by Section 5.4 step 9 and by the
listener's memory budget (API.md Section 4.4), which every session is
charged to.

### 5.6 Pending-join and rate limits

Defaults (local policy; the values are RECOMMENDED):

- at most 64 carriers per listener, at most 4 per session and at most 8 per
  source, in steps 1 to 7 of Section 5.4 at any time (the streams a lane
  needs are outside these bounds, Section 5.5). A source is a client
  address, aggregated to its /64 prefix for IPv6 so that an attacker cannot
  rotate addresses within a prefix; the per-source bound keeps one client
  from occupying the listener's slots. Behind an intermediary that the
  listener is configured to trust (a reverse proxy, load balancer or CDN),
  the client address is the one that intermediary reports for the carrier
  (for example with the PROXY protocol, or in a forwarding header field of
  an HTTP carrier); such a report from any other peer is ignored, so that a
  client cannot choose its own source. When the 64 listener slots are full,
  a new carrier evicts the oldest unauthenticated carrier (as SYN cookies
  do) rather than being refused, so that slow attackers cannot lock out new
  sessions, joins and resumptions. Many users behind one carrier-grade NAT
  share a source; deployments that serve them raise the per-source bound;
- a carrier must deliver its first record, or come with its out-of-band
  join, within the **first-record timeout** (default 2 s) after the carrier
  is established; otherwise it is closed. A carrier that has not completed
  step 7 within the handshake timeout (default 10 s) is closed;
- failed join verifications are rate limited per source (for example per
  client address or per front-end connection), by listener policy. A listener
  MUST NOT stop verifying the joins of a session because of failures
  attributed to that session: anyone who knows a locator could otherwise
  block every legitimate join and resume of the session. Verification is one
  HMAC and stays affordable;
- authenticated joins are bounded by the cap of Section 5.4 step 9; refused
  ones leave state only inside the replay window of Section 4.3.

### 5.7 Joiner behaviour

1. The joiner takes the next pipe ID of its parity (Section 4.1) unless it
   already has max_pipes + H unretired attachments (Section 5.4 step 9).
   After the peer refused a join, or abandoned one of the joiner's
   attachments, with ZMUX_PIPE_RESOURCE_LIMIT, it SHOULD NOT join again
   until another attachment that the acceptor had attached (not the
   refused or abandoned one) has been retired, or a backoff has elapsed: 1
   s after the first such refusal or abandonment, doubled after each
   consecutive one (refusals and abandonments count together), at most 60
   s, and reset by a successful join that the peer does not then abandon
   with ZMUX_PIPE_RESOURCE_LIMIT. An attachment the joiner itself abandons
   for its own limits does not start a backoff.
2. It computes the token and sends the join (in-band) or gives it to the
   adapter (out-of-band). The attachment enters JOINING; so does its inbound
   direction, if it has one. The first record the joiner writes on the new
   outbound direction, if the attachment has one, MUST carry a complete
   PIPE_ACK snapshot (Section 6.2) when the joiner has at least one entry:
   after the PIPE_JOIN for an in-band join (Section 5.2), at the start of
   the record for an out-of-band one.
3. While JOINING, its outbound direction is live but not usable (SCHEDULER.md
   Section 10): the scheduler MAY place control frames on it, and new stream
   data only as rule S1 of Section 11.1 allows. A JOINING pipe therefore
   never holds back failover to a standby pipe. Its information counts as
   unacknowledged like any other. Its inbound direction is not yet listed in
   PIPE_ACK. The joiner sends no PIPE_STATUS for the attachment while it is
   JOINING: a request it changes meanwhile is sent, with its latest value,
   once the attachment is ACTIVE (step 4). The acceptor attaches before it
   confirms the join, so such a frame always finds the attachment, while a
   PIPE_STATUS that overtook the join would be ignored (Section 9). If the
   attachment has an inbound direction (down or bidi; the joiner does not
   read the carrier of an up attachment) and the first record that arrives
   on it begins with
   QX_TRANSPORT_PARAMETERS (a shared listener that sent its parameters
   before it classified the carrier, SPEC.md Section 13.2 rule 7), the joiner
   discards that record without processing it: it confirms nothing, is not
   counted in the pipe's position (Section 2.3) and is not acknowledged. A
   QX_TRANSPORT_PARAMETERS frame anywhere else on the pipe is a
   PROTOCOL_VIOLATION (SPEC.md Section 5.1).
4. The attachment becomes ACTIVE when a PIPE_ACK acknowledges a position
   greater than 0 on its up direction, or a complete record arrives on its
   down direction; its inbound direction becomes OPEN with that first record.
5. If the carrier fails while JOINING, or the attachment is not ACTIVE within
   the handshake timeout, it fails (Section 7.2) and its information is
   re-sent elsewhere.
6. If the first record on the inbound direction of a JOINING attachment
   (after a leading QX_TRANSPORT_PARAMETERS record, step 3) begins with
   CONNECTION_CLOSE 0x1c carrying ZMUX_RESUME_FAILED, the acceptor has no
   session for the locator (Section 5.4 step 3). A joiner MUST recognise
   it. If the session is SUSPENDED (RESUME.md Section 4), the joiner
   closes the session at once with ZMUX_RESUME_FAILED, locally, as
   RESUME.md Section 7 does when no pipe is available; otherwise only the
   attachment fails as in step 5, the session is unaffected, and the
   joiner backs off further joins as after a refusal with
   ZMUX_PIPE_RESOURCE_LIMIT (step 1). Any other CONNECTION_CLOSE on a
   JOINING attachment fails only the attachment. Like every record on an
   unconfirmed carrier, the answer is as trustworthy as the carrier: a
   party that can write on it could equally drop every join, so the
   answer only brings forward an end it could already cause.

### 5.8 Opening a lane

```text
PIPE_LANE Frame {
  Type (i) = 0x3a05a1c9,
  Lane Key (128),
  Pipe ID (i),
  Lane (i),
}
```

- **Lane Key**: the receiver's Lane Key from its `zmux_max_lanes` parameter
  (Section 3.6).
- **Pipe ID**: the attachment whose direction from the writer of the
  PIPE_LANE to its reader the lane carries.
- **Lane**: the lane's number.

**Writer.** An endpoint MAY open a lane on an outbound pipe while lanes are
available toward the peer (Section 3.6) and its adapter offers them on the
pipe's carrier, once a PIPE_ACK has acknowledged a position greater than 0
on the pipe's lane 0, so that the peer has attached the pipe before the lane
arrives. It opens a new stream on the connection of lane 0 and writes on it
one record that holds the PIPE_LANE, with the next lane number of the pipe
(lanes are numbered in increasing order from 1 and each number is used
once), and possibly PADDING, and nothing else until the lane is confirmed.
That record is a record of the pipe like any other: whatever the
endpoint applies to the pipe's records before handing them to the
carrier, size and timing included, applies to it, and when the endpoint
delays the pipe's records (Section 7.1, H_p), a delayed record holds back
every lane of the pipe, this one included.
PIPE_LANE is ack-eliciting, so the reader acknowledges the record within its
acknowledgement delay (Section 6.3); the lane is **confirmed** when a
PIPE_ACK acknowledges a position greater than 0 on it, and from then on it
takes frames as below. A pipe has at most one lane that is not yet
confirmed. A lane whose stream ends, is reset or fails before it is
confirmed, or that is not confirmed within the handshake timeout (the
writer then resets it), is **refused**: that is not a failure of the pipe,
nothing on it needs reinjecting (its PIPE_LANE record is outside the send
order, Section 2.5), a later acknowledgement for it is ignored, and the
writer opens no further lane on that pipe. The reverse half of a
bidirectional stream used as a lane only ever ends gracefully and is
ignored by the writer. A
confirmed lane stays open until its pipe ends (Section 8). A writer opens
lanes as it needs them (SCHEDULER.md Section 9.2). Each takes one stream of
the connection: the adapter opens it when the connection's stream credit
allows, and until then the request waits, which is not an error; it counts
as the pipe's one lane not yet confirmed, its handshake timeout runs only
from the moment its stream is opened, and it is dropped when the pipe ends.
Where the application shares that stream credit (for example a stream
carrier on a zmux session, API.md Section 4.6), the adapter does not wait:
it opens the stream only when credit is available that no opener of the
application is waiting for (API.md Section 5.1) and within the share it
leaves the application (API.md Section 4.6), and otherwise opens no lane
now, which is neither a refusal nor an error; the writer asks again on its
next occasion.

**Frames by lane.** In a multipath session any frame may go on any confirmed
lane; the scheduler decides (SCHEDULER.md Section 9.2). In a session without
multipath (pipe acknowledgement, Section 3.5), whose stream data must arrive
in order (SPEC.md Section 7.3), the frames of a stream direction's sending
part (STREAM, RESET_STREAM, RESET_STREAM_AT, STREAM_DATA_BLOCKED) all go on
one lane, which the writer changes only once a PIPE_ACK covers every frame of
that sending part on the old lane (Section 6.2; "acknowledged" in the sense
of SPEC.md Section 7.4, a hand-off to the carrier, is not enough); every
other frame goes on lane 0, and a CONNECTION_CLOSE waits as SPEC.md Section
6.5 step 1 says. Each stream's frames, and the session's other frames, then
keep their order; frames of different streams can be reordered, which the
rules for opening and accepting streams already allow (SPEC.md Section 7.2
rules 2 and 5), and limits only grow, so a credit frame that overtakes data
is never a violation.

**Reader.** A new inbound stream whose first record begins with PIPE_LANE is
handed over with the identity of its carrier connection (Section 5.5). The
reader looks the Lane Key up among its sessions, as a listener looks up a
Session Locator, comparing it in constant time:

1. If no session has that key, or the session has no attachment Pipe ID with
   a live inbound direction whose lane 0 is on the same connection as the
   new stream, the reader aborts the new stream (it stops reading it: QUIC
   STOP_SENDING, an HTTP/2 or HTTP/3 stream reset) and does nothing else. A
   session in CLOSING or CLOSED (STATE_MACHINE.md Section 4.1) counts as
   having no such attachment: it takes no new lanes, and the stream creates
   no state for it. A
   stream that names another endpoint's session, or a pipe of another
   connection, is never attributed to a session (a lane copied onto another
   connection, or forwarded by an intermediary that splits connections, is
   refused this way), and a writer whose pipe the reader has already given
   up learns of it otherwise (Section 7.3).
2. Otherwise the stream belongs to that pipe. When lanes are not available
   toward the reader, PIPE_LANE is a frame of a feature not in use, a
   FRAME_ENCODING_ERROR (SPEC.md Section 4.1 rule 3). Only the peer knows
   the key, so the session is closed with PROTOCOL_VIOLATION when Lane is 0
   or at least the reader's L, when that Lane has already been opened on
   the pipe, or when the lane's first record holds a frame other than the
   PIPE_LANE and PADDING.
3. Otherwise the stream is lane Lane of the pipe from its first byte: its
   records are processed as the pipe's and its position counts from 0, the
   PIPE_LANE record included. A lane that ends, in any way, before any
   record after its first has arrived is no event of the pipe (its writer
   may see it as refused); after that, its end is the pipe's (Section 7.3).

A PIPE_LANE frame anywhere else, on a pipe's lane 0 or after the first frame
of a lane, is a PROTOCOL_VIOLATION if it decodes and lanes are available
toward the receiver; where lanes are not available toward the receiver,
PIPE_LANE is a frame of a feature not in use and a FRAME_ENCODING_ERROR
(SPEC.md Section 4.1 rule 3), and decoding errors take precedence (SPEC.md
Section 4.1). A reader reads all lanes of a pipe, none starving another
(IMPLEMENTATION.md Section 13.8). PIPE_LANE is never retransmitted or
reinjected: it belongs to its lane, which does not move. It is never
chargeable, since a second one for the same pipe and number is a violation,
and it creates no response state beyond the acknowledgement of its record.

---

## 6. Acknowledgement

### 6.1 Counting positions

A receiver counts a record into a pipe's position (its lane's, Section 2.5)
only after it has
completely received the record and applied every frame in it to session
state, so that the information survives the loss of the pipe. Acknowledgement
is a zmux-level count. It never relies on the carrier's own
acknowledgements: bytes that the carrier acknowledged but the zmux receiver
did not process (for example bytes in the kernel buffer of a socket that
died) are not acknowledged and are re-sent. A record that is incomplete when
its pipe ends is never counted (Section 7.3).

### 6.2 The PIPE_ACK frame

```text
PIPE_ACK Frame {
  Type (i) = 0x0b2b3eeb,
  Entry Count (i),
  PIPE_ACK Entry (..) ...,
}

PIPE_ACK Entry {
  Pipe ID (i),
  Lane (i),
  Position (i),
  ACK Delay (i),
}
```

- **Entry Count**: at least 1. Zero is a FRAME_ENCODING_ERROR.
- Entries MUST appear in strictly increasing order of (Pipe ID, Lane). A
  receiver MUST close the session with FRAME_ENCODING_ERROR when an entry's
  (Pipe ID, Lane) is not greater than the previous entry's; this also
  rejects an entry listed twice, with one comparison per entry and no
  allocation.
- **Pipe ID**: a pipe inbound to the frame's sender.
- **Lane**: a lane of that pipe (Section 2.5); 0 for a pipe without lanes.
- **Position**: the sender's receive position on that lane (Section 6.1).
- **ACK Delay**: microseconds between the moment the sender processed the
  record that brought the position to this value and the moment it sent the
  frame.

The PIPE_ACK frames that an endpoint sends together, in one batch of
records, form a **snapshot**. A **complete** snapshot lists every inbound pipe
of the sender that is not retired and whose position is greater than 0,
including pipes that have ended but whose attachment is not yet retired, and
for a pipe with lanes every such lane. A
snapshot MAY omit the pipes and lanes whose position has not changed since the last
complete snapshot the endpoint sent, so that its size follows the number of
pipes and lanes with new data rather than max_pipes; the endpoint MUST send a complete
snapshot at least once per second while it has such pipes. A snapshot that
omits pipes still lists every change since that complete snapshot, so a
lost frame is repaired by the next snapshot either way. A
snapshot is normally one frame; one that does not fit in a record is split
across several PIPE_ACK frames, each listing a strictly increasing subset of
the entries. Positions are cumulative, so a partial list is harmless: a
receiver applies each entry on its own. A pipe on which nothing
has arrived yet is not listed: for example a downlink that a joiner handed to
its adapter (Section 5.3) and that the acceptor has not attached yet. Its
loss never matters, because the next snapshot carries the same information.
PIPE_ACK is not ack-eliciting and is never retransmitted.

**Size.** Because a snapshot may be split, PIPE_ACK sets no limit on the
number of pipes. Only attached pipes have positions: refused joins and IDs
abandoned before they were seen leave only a bit in the replay window
(Section 4.3). An endpoint has at most max_pipes + H unretired attachments
joined by the peer and at most max_pipes + H that it joined itself (Section
5.4 step 9, the bootstrap pair included on its initiator's side), each with
at most one inbound pipe, so a snapshot has at most 2 x (max_pipes + H)
entries without lanes, and at most the sum over those inbound pipes of their
lanes, each at most the endpoint's own L (Section 3.6), with them; an entry
takes at most 32 bytes (four 8-byte variable-length integers).

A receiver of PIPE_ACK processes each entry:

- a Pipe ID of the receiver's own parity that it has not taken (Section
  4.1): PROTOCOL_VIOLATION;
- a retired Pipe ID (for an ID of the peer's parity without attachment
  state, Section 4.3), or a Position of 0: the entry is ignored;
- a Position greater than the number of bytes the receiver has sent on that
  lane of that pipe, which is 0 for a pipe on which it has no outbound
  direction and for a lane it has not opened: PROTOCOL_VIOLATION (the peer
  claims data that was never sent), or ZMUX_RESUME_FAILED in a session with
  resume in use (RESUME.md Section 7);
- a Position not greater than the largest already acknowledged: the entry is
  ignored (outdated, normal when PIPE_ACKs travel on different pipes);
- otherwise the lane's position advances: every frame on that lane wholly
  below it is acknowledged (SPEC.md Section 7.4), and when the pipe's
  cumulative acknowledgement point advances with it (Section 2.5) an RTT
  sample is taken (Section 6.6).

### 6.3 When to acknowledge

Each endpoint keeps, per inbound pipe, the ack-eliciting bytes received since
the last PIPE_ACK that reported that pipe, summed over its lanes, so that
lanes do not make acknowledgements more frequent. A record is ack-eliciting when it
contains at least one ack-eliciting frame (SPEC.md Section 1.4). An endpoint
MUST send a PIPE_ACK:

1. when any inbound pipe has at least its own `zmux_ack_threshold` such bytes;
2. no later than its own `zmux_max_ack_delay` after the first such byte;
3. immediately after processing a record that contains PIPE_PING;
4. promptly after an inbound pipe ends, so that its final position (that of
   each of its lanes) is known;
5. as a complete snapshot (Section 6.2) when the PIPE_ACK answers, under
   rule 3, a PIPE_PING that arrived in the first record of a newly attached
   inbound pipe. Together with the snapshots in the first records of new
   outbound pipes (Sections 5.2 and 5.4 step 8) this gives both endpoints
   each other's positions within one round trip of any join, which is what a
   resumption needs (RESUME.md Section 5).

"Immediately" means with the next records the endpoint writes, without
waiting for any other condition. An endpoint MAY acknowledge more often. It
SHOULD put a PIPE_ACK in the same batch as pending MAX_DATA or
MAX_STREAM_DATA updates.

### 6.4 PIPE_PING

```text
PIPE_PING Frame {
  Type (i) = 0x269b43e6,
}
```

PIPE_PING has no fields. It is ack-eliciting and elicits an immediate PIPE_ACK
(Section 6.3 rule 3); the response is the next snapshot, so any number of
PIPE_PINGs produce bounded response state (SPEC.md Section 9.2). It is used
to probe a pipe that has no unacknowledged data (Section 7.1), to test a
suspect pipe, and to revalidate a pipe at the application's request
(SCHEDULER.md Section 11.1). It is never retransmitted.

Because each PIPE_PING makes the peer send, PIPE_PING is chargeable (SPEC.md
Section 9.4, NE-2). An endpoint sends at most 8 PIPE_PING frames in total in
any interval of one second, which leaves room for the QX_PING requests
counted with it by SPEC.md Section 9.4.3 rule 1; when more pipes need a PIPE_PING
(probes, the first record of an accepted join, return-pipe liveness), it
serves those that have waited longest first, each as soon as the limit
allows.

### 6.5 Choice of pipe for PIPE_ACK and credit frames

Rule S1 of Section 11.1 says which outbound pipes may carry control frames.
Among them, PIPE_ACK and credit frames go first to a pipe that is known to
work: an endpoint **prefers ACTIVE pipes**, and a JOINING, SUSPECT or
DRAINING pipe carries such a frame only as an additional copy, or when no
ACTIVE outbound pipe exists (a DRAINING pipe marked suspect comes last,
Section 8.2). Among the ACTIVE pipes it prefers the **most-preferred tier**
(SCHEDULER.md Section 10: non-standby before standby, then the lowest
effective priority), and within that tier the lowest estimated one-way delay
(SCHEDULER.md Section 2.2). This order is the **control order** below; a pipe of a less-preferred
tier carries the primary copies of acknowledgements and credit only when no
pipe of a more preferred tier is ACTIVE.

- **Primary pipe.** An endpoint SHOULD send PIPE_ACK on the first pipe of the
  control order, so that the peer's RTT samples for different pipes share the
  same return path and are comparable. The exception is the answer to a
  PIPE_PING in a quiet session (below).
- **Redundancy.** When it has at least two live outbound pipes, an endpoint
  SHOULD also send its latest snapshot on a second outbound pipe (the next
  pipe of the control order) no later than 50 ms after it sent a changed
  snapshot on the primary pipe, and immediately when the primary pipe
  becomes suspect. This keeps acknowledgements flowing when the
  primary return pipe dies.
- **Credit frames.** MAX_DATA, MAX_STREAM_DATA and MAX_STREAMS SHOULD be
  sent on the first pipe of the control order. When the window is
  the bottleneck, that is, when the endpoint has received a BLOCKED frame
  for the scope or sends credit under the liveness rules of SPEC.md Section
  8.3 rule 3, it SHOULD also put the latest value of that limit into the
  record that carries its next redundant snapshot (above), on the second
  pipe. When the pipe that carried the latest value of a limit becomes
  suspect, the endpoint SHOULD send that value again on an ACTIVE pipe. A
  credit update on a slow or dead return pipe stalls the sender for that
  pipe's delay, or until T_fail, on every update; the copy bounds that stall
  by the redundancy interval.
- **Return-pipe liveness.** An outbound pipe that carries only PIPE_ACK
  frames has no ack-eliciting bytes, so it has no ack stall of its own.
  While the session is busy (Section 7.1), when an endpoint sends a
  PIPE_ACK on its primary pipe or as the redundant copy, on a pipe whose
  last ack-eliciting byte was sent, or which became live, more than R_p =
  max(1 s, that pipe's T_suspect) (Section 7.1) ago, it SHOULD add a
  PIPE_PING to the same record. A busy session thereby finds the death of
  the path its acknowledgements return on within R_p plus that pipe's
  detection (Section 7.1, liveness bound), while the redundancy rule above
  keeps acknowledgements flowing in the meantime. In a quiet session these
  pipes are idle pipes like any other and are probed as Section 7.1 says.
- **PIPE_PING answers on its own pipe pair.** The PIPE_ACK that answers a
  PIPE_PING under rule 3 of Section 6.3 SHOULD be sent on the outbound
  direction of the pipe pair (the same attachment) on which the PIPE_PING
  arrived, when that direction exists and is neither suspect nor failed. A
  probe then measures its own path in both directions and never depends on
  another one: answered only on the primary pipe, the probe of a backup
  pipe stays unanswered as soon as the primary return path fails, so the
  backup becomes suspect exactly when it is needed, and a failover takes
  seconds. In a quiet session a PIPE_ACK that acknowledges nothing but
  PIPE_PING frames goes only there: neither on the primary pipe nor as a
  redundant copy, unless the pipe pair of one of those PIPE_PINGs has no
  such direction (an uplink, or a suspect or failed direction), in which
  case it goes on the primary pipe and the redundancy rule applies. In a
  busy session, and whenever the PIPE_ACK acknowledges other ack-eliciting
  bytes too, it goes on the primary pipe as well, with its redundant copy.
  When the pipe that carries the answer has had no ack-eliciting byte for
  R_p, the endpoint SHOULD add a PIPE_PING to the answer, which starts a
  new probe wait for that pipe (Section 7.1): one exchange then shows both
  directions of the pipe pair to work, and the two ends of a quiet pipe
  pair together probe it about once per probe interval instead of each on
  its own schedule. A quiet session then puts records on each pipe only
  for that pipe's own exchanges (CONFORMANCE.md LV-1). The copy is a
  PIPE_ACK of about 20 bytes per probe and is never chargeable.

These copies carry the same value on different pipes. They are an explicit
exception to the rule that limits are sent only when they increase (SPEC.md
Section 4.3) and are never chargeable (SPEC.md Section 9.4.1). Conforming zmux
implementations follow these SHOULDs (CONFORMANCE.md Section 2, C-5), so
traces can check them.

### 6.6 Estimates

For each outbound pipe p the sender keeps an RTT estimate (smoothed_rtt_p,
rttvar_p and min_rtt_p, sampled from PIPE_ACK entries as RFC 9002 Section 5
does), a one-way delay estimate OWD_p and a delivery-rate estimate R_p.
Failure detection (Section 7.1), the retransmit budget and the scheduler use
them; they are local, and the peer sees only their effect on timing.
SCHEDULER.md Section 2 specifies them, including which PIPE_ACK entries
yield samples (for a pipe with lanes, those that advance its cumulative
acknowledgement point, Section 2.5): never one that would measure an outage (bytes sent before the
pipe became suspect or the session suspended, or while the sender had no
live inbound pipe) and normally not one whose
ACK Delay exceeds the peer's `zmux_max_ack_delay`. A new pipe never inherits
another pipe's estimates: until its first RTT sample its smoothed_rtt is
333 ms and its rttvar 166.5 ms (RFC 9002 Section 6.2.2), and until its first
rate sample R_p is 1 MB/s.

---

## 7. Failure detection and reinjection

### 7.1 Suspect and failed pipes

The sender judges each outbound pipe p from acknowledgement progress and from
the progress of its carrier's writes; the receiver judges each inbound pipe
from the records that arrive on it (Section 7.3).

    PTO_p       = smoothed_rtt_p + max(4 x rttvar_p, 1 ms) + peer zmux_max_ack_delay   (RFC 9002 Section 6.2.1)
    queue_p     = min(1 s, unsent_ahead_p / R_p)
    T_suspect_p = max(suspect_floor_p, 2 x (PTO_p + queue_p))
    T_fail_p    = max(2 s, 8 x (PTO_p + queue_p))
    T_dead_p    = max(T_suspect_p, dead_timeout_p)

- `unsent_ahead_p` is a snapshot: Q_p (SCHEDULER.md Section 4: the bytes of
  write calls in progress on any lane of p plus the carrier's unsent bytes
  where the adapter reports them) at the moment the batch holding the
  oldest unacknowledged ack-eliciting byte was handed to the carrier,
  excluding that batch. Without an unsent count it is the in-progress part
  alone, not 0; T_suspect_p and T_fail_p include queue_p on every carrier. It
  does not grow afterwards, so a pipe that stops moving is still detected.
  The R_p of queue_p is taken in the same snapshot, so a rate estimate
  that falls afterwards does not raise queue_p either.
- `suspect_floor_p` defaults to 250 ms and is a per-pipe option (API.md
  Section 4.2, `SuspectFloor`) that may be lowered to 50 ms at most. The
  default buys stability: an ordinary pause of the peer or of the network (a
  garbage-collection pause, a Wi-Fi roam of 100 to 200 ms) does not flap a
  pipe, because 250 ms is well above max_ack_delay plus normal jitter. It
  costs failover time: with a 50 ms floor, 2 x (PTO_p + queue_p) takes over. Deployments that value fast failover over
  robustness to pauses lower it. A carrier's own loss-recovery state, such
  as the consecutive probe timeouts of a QUIC connection, is not a
  separate input: it measures the same silence as the ack stall, and
  acting on it amounts to a lower floor, which costs goodput and urgent
  tails under short stalls. An endpoint therefore does not act on such a
  hint; a deployment that values fast failover lowers `SuspectFloor`.
- `dead_timeout_p` is the stall limit of the last pipe of a direction
  (below), the per-pipe option `DeadTimeout` (API.md Section 4.2; at least
  1 s). When the application has not set it, it is T_fail_p with resume in
  use, and otherwise max(T_fail_p, half the effective idle timeout), where
  the idle timeout counts as 30 s when there is none (15 s with the
  defaults). T_dead_p is never below T_suspect_p, so a pipe always becomes
  suspect before it fails. `dead_timeout_p` does not change the limit of a
  pipe that has an ACTIVE alternative.

The pipe has an **ack stall** while it has unacknowledged ack-eliciting bytes
(stream data, control frames or an unanswered PIPE_PING), measured from

    t_ref = max(time of the last advance of p's acknowledged position
                    (or the time p became live, if it never advanced),
                send time of the oldest unacknowledged ack-eliciting byte on p)

where a pipe with lanes uses its cumulative acknowledgement point (Section
2.5): a lane that stops holds it back, so t_ref finds a dead lane as it finds
a dead pipe, while the head-of-line blocking of one lane by a lost packet,
which other lanes do not share, is measured like any slower acknowledgement.

It has a **write stall** while bytes are waiting to be written to it and its
carrier makes no progress; for a pipe with lanes, while bytes wait on some
lane and none of its lanes makes progress (a lane held back by its carrier's
stream flow control while the others move is the receiver's head-of-line
blocking, which the ack stall measures). For a pipe with lanes, each lane on
which bytes wait is measured as below, as a carrier of its own (its write
calls, its unsent count or writability); the pipe's write stall is the
smallest of these, and never more than the time since any lane of the pipe
last made progress (a write call returned with bytes taken, or its unsent
count decreased). Bytes are waiting for p when frames are committed
to p or stream data within its flow-control limits is bound to p (the
`affinity` policy, SCHEDULER.md Section 7), or when the endpoint has stream
data to send within its flow-control limits, or reinjection, and p is the
only live outbound pipe that may carry it: the only one that is neither suspect nor under the penalty of Section
7.5.1, standby pipes and pipes of lower tiers included, since they take the
data when the others cannot (SCHEDULER.md Section 10). A pipe that is
suspect or penalized is not waited for on account of that data, even when
its carrier stops being writable, and neither is any pipe while two or more
may carry it: their ack stalls judge them. A write call hands the adapter at most one write
batch, and while latency-sensitive work is waiting (input not yet
processed, control, urgent or small-stream frames queued, or either,
acknowledgements and credit aside, within the last max(10 x B, RTT_min);
IMPLEMENTATION.md Section 4) at most the
write-call bound clamp(R_p x B / 2, 16 KiB, 64 KiB) with B the latency
budget, so that reads and urgent frames wait for at most one short call. A driver that writes with blocking calls (an adapter whose
write returns only once the bytes are handed to the layer below, API.md
Section 4.1) additionally hands at most max(4 KiB, R_p x 50 ms) bytes per
call, so that adapters need no partial writes and no call is expected to
take much longer than 50 ms; on a message-framed carrier a write call is one
message, and its record is built within the same bounds. A driver that
writes to a non-blocking carrier when it is writable (for example a TCP
socket with TCP_NOTSENT_LOWAT, or any adapter that signals writability)
hands it, in each gather write, the frames committed to the pipe up to what
the carrier takes (the space below its low-water mark plus one chunk,
SCHEDULER.md Section 9; where the carrier has no low-water mark, the room a
small send buffer leaves, IMPLEMENTATION.md Section 8),
within the write-call bound; how much is committed follows the carrier's
room, not a fixed batch. Such a write never blocks, so its duration is not
judged. A record that holds a frame which cannot be split (the first STREAM
frame of a stream with its preamble, a DATAGRAM) may exceed the bounds by
what that frame needs. The write stall is measured as:

- while a write call is in progress: the time since the call started minus
  its expected duration, its actual size divided by R_p, when that is
  positive;
- while no write call is in progress but bytes are waiting and the carrier
  is not writable: for a carrier that reports its unsent bytes (an optional
  adapter capability, for example `tcpi_notsent_bytes` or SIOCOUTQNSD on a
  TCP socket), the time since that count last decreased; otherwise the time
  since the later of the end of the last write call and the moment the
  bytes started waiting, minus U / R_p, where U is what the carrier held
  unsent when that call ended (for a non-blocking carrier with a low-water
  mark, up to that mark plus what the call added; 0 for a blocking adapter,
  whose call returns once the bytes are handed on).

A pipe that the carrier queue limiter of SCHEDULER.md Section 9.1 holds
back has no write stall: the limiter is not the carrier, and what it holds
back waits in the core, while the bytes already sent have an ack stall if
they are not acknowledged. The limiter applies only to stream data,
reinjection and copies; control frames are not held back by it, and the
return of a suspect pipe to ACTIVE depends on the carrier accepting bytes,
not on the limiter. A slow carrier that is still accepting bytes at its rate therefore never
looks stalled: a non-blocking carrier that must drain its low-water mark
before it is writable again (half a second for 16 KiB at 256 kbit/s) is
not judged during that time. For example a 256 kbit/s
pipe (R_p = 32 kB/s) gets 4 KiB write calls, each expected to take 128 ms; a
call counts as stalled only after 128 ms + T_suspect_p (378 ms with the
defaults), so the healthy pipe is never suspected; an unbounded 80 KiB write
call would block for 2.56 s, longer than T_fail. Then:

- p becomes **suspect** when an ack stall or a write stall has lasted longer
  than T_suspect_p. This applies to JOINING pipes too. A DRAINING pipe is
  only marked suspect and fails only as Section 8.2 says (drain deadline,
  carrier error, abrupt removal).
- p becomes **failed**:
  - when its carrier reports an error or an unexpected end, on any of its
    confirmed lanes (a lane that ends before it is confirmed is refused,
    Section 5.8), or the endpoint removes it abruptly;
  - while p has never been confirmed (Section 5.7), only when its join does
    not complete within the handshake timeout; T_fail_p and T_dead_p do not
    apply to it;
  - when a stall has lasted longer than T_fail_p while the endpoint has
    another outbound pipe in the ACTIVE state;
  - when a stall has lasted longer than T_dead_p while it has none, that is,
    when p is the **last pipe** of its direction (SCHEDULER.md Section 10
    uses "usable" in a narrower sense).

A change of state can make other limits due at once: with `DeadTimeout`
below T_suspect_p a pipe's suspicion and failure fall due together, and
the failure of one pipe turns another into the last pipe of its
direction, whose limit becomes T_dead_p. An endpoint that processes a
deadline therefore evaluates every limit again after each change of
state it makes, until no further change results, before it waits for the
next deadline.

A carrier that backs off its retransmissions can stay silent for up to one
backoff interval after its path returns (a TCP-like carrier with a 200 ms
initial timeout retransmits after a 1.5 s outage only 3 s after it began). zmux cannot shorten that and does not need to: the pipe fails
after T_fail_p, and the pipe the application joins in its place is a new
carrier, which does not inherit the old one's backoff. With such carriers
modelled, the failover and failback targets of CONFORMANCE.md Section 4.5
still hold; only the urgent tail during short stalls of every pipe (S-3s)
grows, by what the carriers' backoff adds to each stall.

How long the last pipe may stall depends on what failing it costs, which is
what the default of `dead_timeout_p` expresses:

- **With resume in use**, a false positive costs only a rejoin, so the last
  pipe fails on T_fail_p like any other. The stall is measured from
  ack-eliciting bytes without acknowledgement progress, which includes an
  unanswered PIPE_PING; on an idle pipe the probes of the probe interval
  provide that measurement. When it fails, the session suspends (Section
  8.5).
- **Without resume**, failing the last pipe ends the session, so by default
  it fails from a stall only after half the idle timeout. Until then it
  stays SUSPECT and keeps carrying control frames until acknowledgements
  resume, its carrier fails, or T_dead_p expires.

Acknowledgements MUST NOT be withheld because the pipe that would carry them
is suspect (Section 11.1 rule S1), so ack suppression is never what makes a
pipe fail.

A pipe without unacknowledged ack-eliciting bytes cannot have an ack stall.
To detect the failure of an idle pipe, and to give the receiver the records
its inbound liveness check relies on (Section 7.3), the sender sends a
PIPE_PING on each outbound pipe on which no ack-eliciting byte has been
handed to the carrier for the pipe's current **probe wait** w, measured
from the moment the pipe became ACTIVE if it never carried one. Only idle
pipes are probed: a pipe that carries ack-eliciting bytes at least that
often (stream data, credit frames, keep-alive, the PIPE_PING frames of
Section 6.5) gets no probe, since those bytes already give the ack stall
something to measure.

The **probe interval** k of a pipe is the per-pipe option `ProbeInterval`
(API.md Section 4.2; 0 or at least 1 s), by default the scheduler parameter
`probe_interval` (API.md Section 6; default 15 s; 0 turns these probes off
on the pipe pairs that use the default, and only these probes: the
PIPE_PING frames of Section 6.5 and the standby probes below stay). For an
outbound pipe of an attachment on which the peer has no outbound direction
(an uplink or a downlink of a split carrier) k is at most 15 s, a protocol
constant that the peer's inbound silence rule (Section 7.3) relies on: a
longer setting counts as 15 s there, and 0 does not turn these probes off.
For the outbound direction of a pipe pair, which only the sender judges,
any value of at least 1 s is allowed (one above 0 and below 1 s is invalid
at every layer: a configuration error in the session configuration and in
pipe and pool options, a rejected decision of a pool policy, API.md
Appendix A): a longer interval only delays the detection of a
pipe pair that died while idle.

Each probe wait is drawn when it starts, that is when the pipe becomes
ACTIVE and whenever a PIPE_PING is sent on it, uniformly from
[k_eff x (1 - j), k_eff], where j is the **probe jitter** (the per-pipe
option `ProbeJitter`, by default the scheduler parameter `probe_jitter`;
default 0.25; 0 <= j < 1) and

    k_eff = min(k, max(1 s, k - H_p))

with H_p the longest time the endpoint may hold one record of p that
carries no urgent frame between building it and handing it to the
carrier, as bounded when the wait starts: the smaller of the bounds the
application set on one delay and on what remains of its total delay, the
one that is set if only one is; 0 when the endpoint does not delay
records or the application set neither bound, and the application then
keeps every delay short enough for the bounds below. The outer minimum
matters only for an interval below 1 s (such as a short `standby_probe`):
a probe never waits longer than k. The first wait of
a pipe on which no PIPE_PING has been sent yet (a bootstrap pipe: a pipe
that joins sends one in its first record, Sections 5.2 and 5.4 step 8) is
drawn from [0, k_eff] instead when j > 0, so that sessions and pipes
established together, after a restart or a network change, do not probe in
step. An ack-eliciting byte sent on the pipe restarts the measurement but
keeps w. A probe therefore reaches the carrier at most k after the
previous ack-eliciting byte of its pipe did, whatever j and H_p, so every
bound that relies on the probe interval, the inbound silence limit of
Section 7.3 and the carrier idle rule of Section 2.4 included, holds as
written. With j = 0 every wait is k_eff, the first one included; tests use
it to get a fixed schedule. The draws come from a cryptographically secure
generator, or from a generator seeded from one; only test hooks inject a
seed, and implementations need not draw the same values.

The probe and its answer, the PIPE_ACK that Section 6.5 sends on the
probed pipe pair, put bytes into both directions of an idle pipe pair's
carrier at least once per probe interval, and a probe on a pipe whose
attachment has no outbound direction at the peer puts bytes into that
carrier's one direction. Probes keep pipes and their carriers in use, not
the session: they never restart the idle timer or the keep-alive timer
(SPEC.md Section 6.4). The converse does not hold: a QX_PING that
keep-alive sends is an ack-eliciting byte like any other and restarts the
probe wait of the pipe that carries it, which it has just shown to work;
it does not replace the probes of the other pipes. A session without
multipath sends no probes (Section 3.5), and only keep-alive (on by
default, SPEC.md Section 6.4) or the carrier's own keep-alive (Section
2.4) then puts bytes on an idle carrier. An
endpoint MAY probe more often. It SHOULD send a PIPE_PING on a standby
pipe, and on a pipe of a less-preferred tier than the most-preferred tier
that has a usable pipe (SCHEDULER.md Section 10), on which no
ack-eliciting byte has been sent for `standby_probe` (a scheduler
parameter, API.md Section 6; default 1 s; 0 turns these probes off, and
such pipes are then probed only at the probe interval),
measured in the same way but with a fixed period (the probe jitter applies
to the probe interval only), while the session is **busy**: a **traffic
frame** (STREAM, RESET_STREAM, RESET_STREAM_AT, STOP_SENDING or DATAGRAM:
the activity frames of SPEC.md Section 6.4 without QX_PING, so that
keep-alive does not count) has been sent or received on any pipe within the
**busy window** of 15 s, a constant independent of `probe_interval`, or the
session was established within it. A session that is not busy is
**quiet**, and its standby and less-preferred pipes are probed at the probe
interval like any idle pipe; when it next sends or receives a traffic
frame, it sends a PIPE_PING at once (within the limit
of Section 6.4) on each standby or less-preferred pipe on which no
ack-eliciting byte has been sent for `standby_probe`. A backup matters only
when there is traffic to fail over, and that PIPE_PING tells within one
round trip whether a backup that died while the session was quiet still
works, as the faster probe would have; until then only the probe interval
runs. For a process holding 10 000 quiet sessions with one standby pipe
pair each, this is about 1 600 PIPE_PING frames per second (plus their
acknowledgements), and with `probe_interval` at 60 s about 400.
Such a pipe carries nothing while the preferred tier works, so without the
faster probe a backup whose path died silently is found only when the
preferred tier fails too, after the failover has already chosen it, and the session
stalls for seconds (CONFORMANCE.md DF-1, DF-2). The faster probe costs, per backup
pipe, one PIPE_PING (5 bytes with its record) and the PIPE_ACKs that
answer it (about 20 bytes each, on the primary pipe and on the probed pair)
per second, and when the answering side adds a liveness probe of its own
(Section 6.5) that probe and its answers too: well under 100 bytes per
second. The probe in flight on a backup when the preferred tier fails
adds up to one backup round trip to a failover, because the backup's stall
clock already runs. These probes count against the PIPE_PING limit of
Section 6.4, which takes precedence. New data after an idle period is not held
back for a probe: the data itself elicits the acknowledgement, the ack stall
is measured from its send time, and the first chunk is also sent on another
pipe (SCHEDULER.md Section 4.1). Probing first would cost a round trip
whenever the pipe is alive and gain nothing when it is not (CONFORMANCE.md
I-1).

**Liveness bound.** These rules give every live outbound pipe of a
multipath session an ack-eliciting byte, handed to its carrier, in every
interval of bounded length, so that no pipe dies without detection. With
R_p = max(1 s, T_suspect_p), n_out the number of live outbound pipes
(the PIPE_PING limit of Section 6.4 delays a PIPE_PING by up to n_out / 8
s) and the failure at time 0, an outbound pipe p is suspect, and fails
T_fail_p later (T_dead_p for the last pipe of its direction), by:

| Pipe | Bound on the suspicion |
|---|---|
| carrying unacknowledged ack-eliciting bytes (stream data, control frames, a PIPE_PING) | T_suspect_p, t_ref being no later than the failure |
| carrying only the primary or redundant PIPE_ACK of a busy session | R_p + max_ack_delay + H_p + n_out / 8 s + T_suspect_p (Section 6.5) |
| idle, the session quiet; or idle on an attachment without an outbound direction at the peer | k + n_out / 8 s + T_suspect_p (k at most 15 s for the latter) |
| standby or less-preferred, the session busy | `standby_probe` + n_out / 8 s + T_suspect_p |
| the one pipe pair of a session without multipath, keep-alive on (the default) | not judged as a pipe: the session closes by its idle timer (SPEC.md Section 6.4) at most I after the failure (without acknowledgement: after the last record other than keep-alive that the carrier accepted), I the effective idle timeout; never without an idle timeout |

An inbound pipe of an attachment without a local outbound direction is
judged by its receiver within L_in (Section 7.3). A pipe pair whose probes
are turned off at one end is probed by the other end, whose answer
(Section 6.5) also judges the first end's outbound direction while the
pair works; when the pair dies, that end learns it from the attachment's
failure at the other end (Section 7.3: PIPE_ABANDON(ZMUX_PIPE_FAILED) on
another pipe) or by the ack stall of its own next ack-eliciting byte on
it. The one exception is a pipe pair whose probes are turned off at both
ends: while the session is quiet nothing is sent on it, and its death is
found only when it is next used. In a session without multipath (the last
row) nothing marks the pipe suspect: the idle timer, restarted only by
activity received or acknowledged, or in a session without acknowledgement
by records received and records the carrier accepted, expires at most I
after the last such event, and the endpoint reports an idle timeout. With
acknowledgement that is at most I after the failure. Without it, a record
other than keep-alive that the dead carrier still accepts restarts the
timer as well (QMux-02 Section 7.1), so the bound runs from the last such
record; keep-alive records alone never extend it. Keep-alive, every k <= I / 2 at the
latest, makes this the bound of a live idle session too, so the session
then ends only when its peer or carrier is gone. With keep-alive off the
same bound holds, but a live idle session ends at I as well. CONFORMANCE.md Section 4.5 (LV rows)
tests each multipath row.

When an attachment fails abruptly, every other attachment of the same joiner
in the same non-zero fate group is affected at once: an outbound pipe of it
that has unacknowledged ack-eliciting bytes becomes suspect, and one that has
none is probed with a PIPE_PING, which gives detection something to measure.
Their own detection then decides whether they fail. An attachment
abandoned with ZMUX_PIPE_RESOURCE_LIMIT, by either endpoint and whatever
code the answer carries (Sections 5.4 step 7, 8.1 and 8.3), did not fail
with its path: it affects no other attachment either, and counts for
nothing in the probation history (SCHEDULER.md Section 11). An attachment whose
graceful removal has started (PIPE_ABANDON(NO_ERROR) sent or received for
it, Section 8.2), whatever its directions, affects no other attachment when
it fails or one of its pipes is marked suspect: its carrier
often ends because of a limit of its own, such as the lifetime limit that
made the endpoint rotate it (API.md Appendix A), and the replacement that
shares its fate group would otherwise stall although its path works.

While an endpoint has no live inbound pipe (a partially suspended endpoint,
RESUME.md Section 4.2), no acknowledgement can reach it. It then moves no
outbound pipe to SUSPECT or FAILED for lack of acknowledgement progress;
carrier errors and write stalls still count. Detection resumes, with t_ref
reset to the current time, when an inbound pipe exists again.

A suspect pipe returns to ACTIVE when its acknowledged position advances and
its carrier accepts bytes again; a pipe that was DRAINING stays DRAINING
(Section 8.2).

### 7.2 Effects of suspicion and failure

When an outbound pipe becomes suspect, the sender:

1. stops scheduling new stream data on it. Control frames continue to use it
   as rule S1 of Section 11.1 allows;
2. reinjects (Section 7.4) the information of every ack-eliciting frame sent
   on it beyond its acknowledged position. Frames that were committed to the
   pipe but not yet handed to its adapter never left: they are taken back and
   scheduled again, and stream directions bound to the pipe are rebound
   (SCHEDULER.md Section 7), so streams spread over a stalled pipe move to
   the others.
   Bytes of a write call already handed to the adapter count as sent and are
   never taken back, because the carrier may still deliver them;
3. sends PIPE_PING on it, subject to the PIPE_PING limit of Section 6.4, and
   keeps processing acknowledgements for it.

When an attachment fails, the endpoint additionally:

4. closes the carrier (abortively if needed);
5. sends PIPE_ABANDON(Pipe ID, ZMUX_PIPE_FAILED) on another outbound pipe
   (Section 8), unless it already sent PIPE_ABANDON for the attachment;
6. keeps the attachment's state until it is retired (Section 8.4).

Suspicion is what bounds the stall caused by a dead pipe. Measured from the
failure, the stall is about

    (t_ref - failure time) + T_suspect_p + reinjected bytes / R_q + OWD_q

where q is the surviving pipe that carries the reinjection, and the first term
is the time until the last acknowledgement that left the peer before the
failure has arrived (at most a return trip plus the 50 ms redundancy
interval of Section 6.5). CONFORMANCE.md Section 4.5 gives the targets.

A live pipe loses nothing at zmux's level: its carrier delivers or fails.
What a failed pipe had sent and the peer had not acknowledged when it
failed is its `bytes_lost` (API.md Section 7.2), counted once, at the
failure, over all its lanes; the carrier's own retransmissions are
reported separately (`bytes_retransmitted`), where the adapter reports
them.

### 7.3 Inbound pipe end and inbound silence

The end of an inbound pipe is **expected** when it is a graceful end at a
record boundary, of every lane for a pipe with lanes (a lane that ended
before its second record does not count), after the receiver has received PIPE_ABANDON for the
attachment or a CONNECTION_CLOSE, or after it has itself sent
PIPE_ABANDON(NO_ERROR) for the attachment (a graceful removal it started:
the peer may shut its side down as soon as it has that frame, and its own
PIPE_ABANDON may still be on its way over another pipe, Section 8.2). When an inbound pipe ends in any other way
(carrier error, reset, an end in the middle of a record, or an end of stream
that is not expected, on any of its lanes except one that ends before its
second record, Section 5.8), or falls silent as described below, the receiver:

1. discards any incomplete record, on every lane, and does not count it;
2. treats the attachment as failed: if the attachment is a pipe pair, its own
   outbound direction fails too (Section 7.2);
3. sends PIPE_ABANDON(Pipe ID, ZMUX_PIPE_FAILED) unless it already sent one,
   and reports the final position in its next PIPE_ACK (Section 6.3 rule 4).

**Inbound silence.** A carrier can stall without ending. For a pipe pair the
endpoint's own outbound direction detects that (Section 7.1), and the pair
fails as a whole. An inbound pipe of an attachment on which the endpoint has
**no outbound direction** (an uplink the peer joined, a downlink of a split
carrier the endpoint joined itself) is otherwise never judged at this end,
and only to such pipes does the following rule apply. Because the sender
probes every idle outbound pipe at least once per probe interval (Section
7.1), such an OPEN inbound pipe p always receives a complete record (on any of its lanes) within

    L_in = probe interval + n_in / 8 s + max(2 s, 2 x srtt_max, G_p - probe interval)

where, evaluated at the current time:

- n_in is the number of the receiver's live inbound pipes (the PIPE_PING
  limit of Section 6.4 may delay the sender's probes by that much);
- srtt_max is the largest smoothed_rtt of the receiver's live outbound pipes
  (333 ms when it has none);
- G_p is the longest interval between two consecutive complete records
  observed on p so far (0 before the second record), so that a downlink
  whose intermediary buffers and releases records in bursts gets the slack
  it has shown.

With the defaults and two inbound pipes, L_in is 17.25 s. An OPEN inbound
pipe to which the rule applies fails, as an unexpected end above, when no
complete record has arrived on it for L_in, measured from its last complete
record or from the moment it became OPEN, except that when losing it would
leave the receiver without a live inbound pipe, the limit is the larger of
L_in and the stall limit T_dead that Section 7.1 gives the last pipe,
computed with the largest PTO of the receiver's live outbound pipes, or with the initial
values when it has none: a silent last inbound pipe is given the same
patience as a stalled last outbound pipe. A JOINING inbound pipe is bounded
by the handshake timeout instead (Section 5.7). With resume in use, the
receiver then lacks that direction and rejoins it (RESUME.md Section 4.3). A
pipe that merely carries no application data is never silent this long,
because probes and their acknowledgements keep arriving.

### 7.4 Reinjection

Reinjection re-sends information, not records, exactly as RFC 9000 Section
13.3 does after packet loss. For each ack-eliciting frame that was sent on a
suspect or failed pipe beyond the acknowledged position:

| Frame | What is sent again |
|---|---|
| STREAM | the unacknowledged byte ranges and the FIN, re-chunked as convenient, unless the sending part has been reset; after RESET_STREAM_AT, only ranges below the smallest Reliable Size sent. A FIN is re-sent even when the frame carried no data. The frame at offset 0 must still carry the whole preamble (EXTENSIONS.md Section 4.2). |
| RESET_STREAM | the same frame, unchanged, until acknowledged or the sending part is terminal |
| RESET_STREAM_AT | the frame with the current, smallest Reliable Size (reliable-stream-reset-11 Section 5), until acknowledged or the sending part is terminal |
| STOP_SENDING | the same frame until the receiving part is terminal |
| MAX_DATA, MAX_STREAMS | the current limit |
| MAX_STREAM_DATA | the current limit, only while the receiving part is in "Recv" |
| DATA_BLOCKED, STREAM_DATA_BLOCKED, STREAMS_BLOCKED | the current blocking limit, only while still blocked |
| GOAWAY | the current watermarks |
| PRIORITY_UPDATE | the latest update for the stream, if it is not yet acknowledged |
| PIPE_STATUS | the latest status for the pipe |
| PIPE_ABANDON | the same frame, until it is acknowledged (retirement waits for that, Section 8.4) |
| PIPE_ACK, PIPE_PING, PADDING, QX_PING, DATAGRAM, PIPE_JOIN, QX_TRANSPORT_PARAMETERS, CONNECTION_CLOSE | nothing |

Reinjected information SHOULD be sent before new data (RFC 9000 Section
13.3). A sender SHOULD NOT re-send information that a PIPE_ACK has
acknowledged in the meantime. Information sent on a pipe is reinjected from
that pipe at most once: a pipe that is suspect and later fails does not
reinject it again, and for frames that carry a current value (limits,
watermarks, status) only the latest value per scope is re-sent. A copy that
is itself lost with the pipe that carried it is reinjected from that pipe in
turn. If the suspect pipe recovers, copies already re-sent are harmless
duplicates; queued reinjections that are now acknowledged are dropped.

If no other usable outbound pipe exists, reinjected information waits for a
new pipe (and the session may become SUSPENDED, RESUME.md Section 4); when the
suspect pipe recovers first, the queued reinjections it acknowledges are
dropped.

### 7.5 No retransmission timers

A sender MUST NOT re-send information from a pipe that is not suspect,
failed or abandoned, and MUST NOT use any per-frame or per-record
retransmission timer. Each pipe's carrier already recovers losses; a second
timer-driven retransmission layer causes the meltdown of a reliable layer
over another (TCP over TCP). A sender MUST NOT send the same information on
several pipes proactively, with three exceptions:

1. the copies of PIPE_ACK and credit frames of Section 6.5;
2. **opportunistic reinjection** (Section 7.5.1);
3. **redundant urgent data** (SCHEDULER.md Section 4.1): STREAM frames of
   urgent stream directions on the two pipes of their tier with the
   earliest arrival estimate, and the first `spread_chunk` written on a
   pipe after an idle period on one more pipe of its tier, at most one copy
   per chunk and at most max(spread_chunk, retransmit_budget / 64) of
   copies whose data is not yet acknowledged.

These copies are not reinjection in the sense of Section 7.4, are never
driven by a timer, and SPEC.md Sections 4.3 and 9.4.3 allow them
explicitly; the receiver handles them as duplicates (Section 7.6).

#### 7.5.1 Opportunistic reinjection

A pipe can slow down without stalling: acknowledgements still advance, so it
never becomes suspect, but data spread onto it arrives much later than data
on the other pipes, and the receiver's flow-control window fills with data
waiting for that hole. MPTCP implementations handle this case with
opportunistic retransmission and penalization (Raiciu et al., "How Hard Can
It Be? Designing and Implementing a Deployable Multipath TCP", NSDI 2012);
zmux does the same, driven by flow control rather than by a timer. Let f be
the eligible pipe (SCHEDULER.md Section 10) with the smallest estimated
arrival time (SCHEDULER.md Section 4); opportunistic copies, like reinjection, never go to a
less-preferred tier while a more-preferred one is eligible.

- **Trigger.** The sender cannot send new data of a stream because of the
  peer's stream flow control, or cannot send new stream data at all because
  of the peer's session flow control. The **blocking data** is the lowest
  unacknowledged data of that stream, or, for the session, the unacknowledged
  stream data that was sent first. If the blocking data was sent on a pipe p
  other than f, and more than

      max(2 x smoothed_rtt_f + peer zmux_max_ack_delay, min(PTO_p, 2 x E))
      E = (unsent_ahead + L) / R_p + min_rtt_p + peer zmux_max_ack_delay

  ago, the sender MAY send it again once on f, one `spread_chunk` at a time
  while the condition holds: each time the next unacknowledged part of the
  blocking data that has not been re-sent yet and meets the same conditions.
  E is the acknowledgement time that was expected for that data when it was
  sent: unsent_ahead and L are the bytes queued ahead of it and its own size
  on p, and R_p and min_rtt_p are the values at that moment, so a pipe that
  slows down cannot raise its own threshold. PTO_p alone adapts to the
  slowed pipe once its RTT samples reflect the slowdown, and
  2 x E alone fires late on a pipe whose rate estimate is still its initial
  value; the threshold is late by either measure.
- Each byte is re-sent opportunistically at most once. The copies are
  ordinary STREAM frames; the receiver handles them as duplicates, which are
  never chargeable (Section 7.6).
- **Penalty.** The sender then penalizes p, once per episode: it discards
  p's delivery-rate samples (SCHEDULER.md Section 2.3) and sets R_p to half its previous
  value, which new bottleneck samples then replace, and until all bytes it
  had sent on p are acknowledged it schedules no new stream data on p, and p
  is not a candidate for f in SCHEDULER.md Section 5.

### 7.6 Duplicates at the receiver

Duplicate STREAM data, repeated control frames and outdated limits are normal
in multipath sessions. They are not errors and are handled by the idempotence
rules (SPEC.md Section 4.1 rule 5). They are never chargeable: SPEC.md Section
9.4.1 exempts them in multipath sessions, because their number depends on how
much was in flight on a failed pipe.

Reinjection and duplicate suppression make each byte of a stream reach
the receiving endpoint once and in order whatever happens to the pipes;
that guarantee ends at the receiving zmux endpoint (SPEC.md Section 7.4,
"Delivery boundary"), not at its application.

---

## 8. Removing pipes

### 8.1 The PIPE_ABANDON frame

```text
PIPE_ABANDON Frame {
  Type (i) = 0x23b35b74,
  Pipe ID (i),
  Error Code (i),
}
```

Error Code is a transport error code: NO_ERROR for a graceful removal,
ZMUX_PIPE_FAILED for a failure or abrupt removal, ZMUX_PIPE_RESOURCE_LIMIT
when the endpoint cannot keep the attachment. Unknown codes mean
ZMUX_PIPE_FAILED. PIPE_ABANDON refers to the whole attachment. It MAY be sent
on any outbound pipe; it SHOULD be sent on a pipe other than the abandoned
one, except that a graceful removal of one's own outbound pipe MAY put it as
the last frame on that pipe.

PIPE_ABANDON for a pipe ID of the peer's parity without attachment state
(the join may have been lost, held by an intermediary or refused): if the ID
lies within the replay window of Section 4.3 and its bit is not set, the
receiver sets the bit, so that a later join with that ID is a replay, and
answers. In every other case (bit already set, stale, or above the window)
the frame is ignored, and the peer retires the attachment unilaterally
(Section 8.4). PIPE_ABANDON for an ID of the receiver's own parity that it
has not taken (Section 4.1) is a PROTOCOL_VIOLATION: the peer cannot know of
such an attachment.

### 8.2 Graceful removal

Rotating a carrier (the helper of API.md Appendix A) is a join followed, once the new
pipe is usable, by this graceful removal of the old one, the new pipe
normally in the old one's fate group; nothing on the wire marks it. An endpoint that removes attachment P gracefully:

1. stops scheduling new stream data and DATAGRAM frames on its outbound
   direction of P, which enters DRAINING. A DRAINING pipe never becomes
   ACTIVE or SUSPECT again, so a removal is never undone. Control frames may
   still use it as Section 11.1 rule S1 allows, as a last resort, until the
   **PIPE_ABANDON exchange** for P is complete, that is until the endpoint has
   both sent its PIPE_ABANDON for P (handed it to a carrier, on any pipe)
   and received the peer's; after that it carries only PIPE_ACK and
   CONNECTION_CLOSE. A DRAINING pipe that has an ack stall or
   write stall of T_suspect_P (Section 7.1) is marked suspect: it stays
   DRAINING, moves after every other live pipe in the choice of Section 6.5,
   and the information of the ack-eliciting frames sent on it beyond its
   acknowledged position is reinjected at once (Section 7.4), stream data
   included. A link that dies right after a planned removal, for example in
   a Wi-Fi handover announced by the operating system, then costs the same
   stall as an unplanned failure (315 to 360 ms with the defaults in
   CONFORMANCE.md S-8, 308 ms with `affinity`) rather than
   the drain deadline of at least 2 s; if the pipe was only slow, the copies
   are harmless duplicates;
2. sends PIPE_ABANDON(P, NO_ERROR);
3. keeps processing acknowledgements. The **drain** of P completes at the
   first moment at which the PIPE_ABANDON exchange is complete and every byte
   of stream data it sent on P has been acknowledged; the endpoint checks
   this whenever either can change, that is when it sends its PIPE_ABANDON,
   when it receives the peer's and when an acknowledgement arrives. A pipe
   with no unacknowledged stream data therefore completes its drain as soon
   as the second PIPE_ABANDON of the exchange is sent or received. It then
   shuts down its write side of P gracefully (every lane at a record
   boundary) and P is CLOSED. Waiting for the peer's PIPE_ABANDON
   guarantees that the peer knows the end is coming before it sees it,
   because PIPE_ABANDON may travel on another pipe than the end of stream
   (Section 7.3). Information of other ack-eliciting frames sent on P that is
   still unacknowledged at that moment is reinjected (Section 7.4): it is
   control information of a few bytes. If the drain has not completed when
   T_fail_P has elapsed since P entered DRAINING, P fails instead (Section
   7.2, without a second PIPE_ABANDON), and its unacknowledged information
   that was not already reinjected when it was marked suspect, stream data
   included, is reinjected;
4. keeps reading its inbound direction of P until the peer shuts it down or,
   counted from the moment the PIPE_ABANDON exchange is complete, the linger
   timeout of SPEC.md Section 6.5 expires. A linger expiry after this
   exchange ends the inbound direction gracefully, not as a failure.

An endpoint that receives PIPE_ABANDON(P, NO_ERROR) performs steps 1, 3 and 4
for its own directions of P and sends its own PIPE_ABANDON(P, NO_ERROR) if it
has not done so. A graceful removal that completes its drain without a
stall reinjects no stream data and causes no stall; only a draining pipe that
stalls for T_suspect_P (a pipe that died or slowed while draining) does.

### 8.3 Abrupt removal

An endpoint that removes P abruptly treats P as failed (Section 7.2):
reinjection, carrier closed, PIPE_ABANDON(P, ZMUX_PIPE_FAILED), or
PIPE_ABANDON(P, ZMUX_PIPE_RESOURCE_LIMIT) when it removes P for a local
resource limit or a loop (Section 5.4 step 7) rather than because P's
path failed. An endpoint
that receives PIPE_ABANDON(P, error other than NO_ERROR) treats its own
directions of P the same way and answers with PIPE_ABANDON if it has not sent
one.

### 8.4 Retirement

An attachment is retired at an endpoint when that endpoint has sent
PIPE_ABANDON for it and that frame has been acknowledged, has received the
peer's PIPE_ABANDON for it, and each of its directions at that endpoint has
ended (outbound CLOSED or FAILED, inbound ENDED). Retirement takes effect at
the first moment all three hold, whichever event completes them (an
acknowledgement, the peer's PIPE_ABANDON, the end of a direction, including
the endpoint's own closing of the carrier when the attachment fails or the
linger timeout expires); from then on both directions are RETIRED and the
state is freed. Waiting for the acknowledgement keeps the endpoint's own PIPE_ABANDON
in the reinjection state until the peer has it, so that both sides retire the
attachment even when a PIPE_ABANDON is lost with a failing pipe. An endpoint
answers a peer's PIPE_ABANDON once: a copy that arrives again (reinjected by
the peer, or sent on several pipes) gets no second answer, because the
endpoint's own PIPE_ABANDON is already retained and re-sent until it is
acknowledged. Answering every copy would let two endpoints keep answering
each other's copies indefinitely.

**Unilateral retirement.** A peer that never answers must not make the
endpoint keep the attachment forever. When the endpoint's own PIPE_ABANDON
for an attachment has been acknowledged and the peer's PIPE_ABANDON has not
arrived within

    max(2 s, 3 x PTO)

after that acknowledgement, where PTO is the largest PTO_p (Section 7.1) of
the endpoint's ACTIVE outbound pipes, or of its live outbound pipes when none
is ACTIVE, or the PTO of the initial values (Section 6.6) when it has no live
outbound pipe, the endpoint closes whatever carrier of the attachment is
still open and retires the attachment anyway. A conforming
peer answers within one round trip, so this only fires for a peer that does
not, or whose answer was lost together with every pipe that carried it.
While the endpoint is SUSPENDED its PIPE_ABANDON cannot be acknowledged;
the cap of Section 5.4 step 9 then displaces an attachment (a failed one, or
else a live one that cannot serve the missing direction) when a rejoin needs
the room.

After retirement:

- its pipe ID is never used again; PIPE_ACK entries, PIPE_ABANDON and other
  frames for it are ignored. A late PIPE_ABANDON from the peer is still
  acknowledged at the pipe level like any record, so the peer retires the
  attachment in turn;
- its retained information has already been reinjected or acknowledged;
- for an attachment the peer joined, the ID's bit stays set in the replay
  window (Section 4.3) until it slides out, after which the ID is stale.

### 8.5 Losing the last pipe in a direction

An outbound pipe is **live** while it is JOINING, ACTIVE, SUSPECT or
DRAINING; an inbound pipe is live while it is JOINING or OPEN, that is until
it ends (STATE_MACHINE.md Section 3.2). When an endpoint has no live
outbound pipe, or no live inbound pipe:

- if resume is in use, the session enters SUSPENDED (RESUME.md Section 4);
- otherwise the session ends: if a live outbound pipe remains, the endpoint
  sends CONNECTION_CLOSE(NO_VIABLE_PATH) on it and follows SPEC.md Section
  6.5; if none remains, it closes its carriers without sending anything. In
  both cases it reports "pipes lost" to its application (SPEC.md Section 6.5
  rule 4).

Removing the last pipe in a direction, gracefully or not, therefore suspends
or closes the session. API.md Section 5.3 describes how implementations warn
applications before they do so.

---

## 9. Pipe status and priority

```text
PIPE_STATUS Frame {
  Type (i) = 0x227b2811,
  Pipe ID (i),
  Sequence Number (i),
  Status (i),
  Priority (i),
}
```

- **Status**: 0 available, 1 standby. Other values are a
  FRAME_ENCODING_ERROR.
- **Priority**: any value of the variable-length integer range (0 to
  2^62 - 1); lower values are preferred, as with route metrics and RFC 9218
  urgency. The default is 0, the most-preferred tier: pipes nobody configured
  share the best tier, and costly or slow pipes are configured with higher
  values. No value is invalid.
- PIPE_STATUS states the sender's preference about the pipe that is inbound to
  the sender, that is, how the receiver should use its outbound direction of
  that attachment (draft-ietf-quic-multipath-21 Section 3.3 semantics, with a
  priority added). Each direction of an attachment therefore has its own
  status and priority: an endpoint sets those of its outbound direction
  locally and requests those of the peer's outbound direction with
  PIPE_STATUS. A received PIPE_STATUS never switches the receiver's own
  sending on or off, and never changes what it expects to receive: it
  only enters the effective status and priority of the receiver's
  outbound direction below. (Some QUIC multipath implementations treat a
  received PATH_STATUS as a switch for their own sending on that path;
  that is not the meaning here.)
- Sequence numbers are per pipe ID and per sender, strictly increasing, and
  start at 1. Sequence number 0 is the initial request: for the acceptor's
  outbound direction the one in the PIPE_JOIN (Section 5.1), otherwise
  available with priority 0. A
  receiver ignores a PIPE_STATUS whose Sequence Number is not greater than the
  last one it received for the pipe. Each frame carries both fields, so the
  latest frame states the complete request, and requests may change at any
  time and as often as the application wants: frames that cross, are
  reordered across pipes or are reinjected (Section 7.4 re-sends only the
  latest) never undo a newer request, because only a higher sequence number
  is applied.
- Pipe IDs that are not attached, with the rules of PIPE_ABANDON (Section
  8.1):
  - an ID of the receiver's own parity that the receiver has not taken
    (Section 4.1) is a PROTOCOL_VIOLATION: the peer cannot know of such an
    attachment;
  - an ID of the peer's parity without attachment state, an ID whose
    attachment is retired, or an attachment on which the receiver has no
    outbound direction: the frame is ignored. A receiver never stores a
    request for an attachment it does not have; a joiner states its initial
    request in PIPE_JOIN and sends PIPE_STATUS only once the attachment is
    ACTIVE (Section 5.7 step 3).

**Effective status and priority of an outbound direction.** For each of the
two fields separately, an explicit local setting wins (API.md Section 4.2,
`SendPriority` and `SendStandby`), then the latest PIPE_STATUS request of the
peer for that attachment (the one in PIPE_JOIN until a PIPE_STATUS with a
sequence number of at least 1 arrives), then the default (available,
priority 0). An
endpoint SHOULD honour the peer's request when it has no local setting: the
request typically expresses something the sender cannot see, such as the
cost of the path to the peer. Both the local setting and the request can
change at any time; the scheduler uses the effective values in force at each
decision (SCHEDULER.md Section 10).

**Joining with a priority.** A joiner states the initial status and
priority of the acceptor's outbound direction of a new attachment in the
PIPE_JOIN itself (Section 5.1), in band or out of band, so the request is
covered by the join token and in force from the first byte the acceptor
sends; no request can arrive late and misroute data. An acceptor with a
non-default request for the joiner's outbound direction puts its PIPE_STATUS
in its first record on the attachment (with the PIPE_PING of Section 5.4
step 8), or sends it on another outbound pipe when the attachment gives it
none. Until it arrives the joiner's outbound direction is at the default,
and a new attachment is in probation (SCHEDULER.md Section 11) anyway.

**Standby.** A standby pipe carries new stream data only on failover, when no
non-standby pipe in its direction is usable (SCHEDULER.md Section 10); it is never used
for overflow, whatever its priority. It is probed with PIPE_PING at the probe
interval like any pipe.

A takeover is temporary, and so is the request that announces it. An
endpoint that requests standby for the peer's outbound direction of an
attachment, and starts using its own standby outbound pipe of that
attachment because no non-standby outbound pipe is usable, SHOULD send a
PIPE_STATUS for the attachment with Status available and the Priority it
requests (its configured value, not 0), so that the peer's outbound
direction follows (draft-ietf-quic-multipath-21 Section 3.3). Once a
non-standby outbound pipe of that direction is usable again, which for a
returning or new pipe means after its probation, an endpoint that sent such a
takeover request MUST send a PIPE_STATUS restoring its configured request
(Status standby, the same Priority). Without the restoring frame the peer
would keep using its direction of the attachment as an ordinary pipe
indefinitely.

---

## 10. Join security

### 10.1 Secret source selection

From the two endpoints' Secret Flags, both endpoints compute the same
selection:

1. exporter = used if both set E;
2. psk = used if both set P;
3. if neither is used: insecure if both set I; otherwise **no source**, and
   multipath is not in use (Section 3.3).

When both endpoints set E and P, both enter the session secret.

An endpoint sets E only when the application has explicitly asserted that
the innermost end-to-end handshake of pipe 0's bootstrap carriers
terminates at the peer and offers an RFC 8446 Section 7.5 style exporter
(API.md Section 4.3, `PipeAuth.exporter(...)`). That handshake is either
TLS 1.3 or QUIC terminated by the endpoint's own process (a TLS
connection, or a QUIC connection whose stream is the carrier, RFC 9001
Section 4), or the handshake of a carrier layer that runs its own
authenticated key exchange end to end with the peer and exports keying
material in the same way. When such handshakes are nested, the innermost
one that terminates at both endpoints is used; a handshake below it may be
terminated by an intermediary. An implementation never sets E on its own, because it
cannot tell whether a CDN or a TLS-intercepting proxy sits in the path, and
then the two ends export different values. It sets P only when the
application supplied a PSK; I only when the application explicitly allowed
insecure joins (API.md Section 6). An endpoint MUST NOT set I together with
P: on a plaintext
bootstrap an attacker could strip the stronger flag from the parameters and
select insecure mode. Stripping a flag then only disables multipath, which
an on-path attacker of a plaintext session can do anyway. By default no
plaintext session can join pipes.

### 10.2 Inputs

- **Exporter value**: Exporter("EXPORTER-zmux-session", "", 32) of the
  innermost end-to-end handshake (Section 10.1) of the carrier of pipe 0
  up, with the interface of [RFC8446] Section 7.5; both endpoints export
  from that same carrier, which is the initiator's outbound and the
  responder's inbound carrier. A carrier that is a stream of a connection
  (a QUIC stream) exports from that connection's TLS handshake. An
  endpoint reads the value when it processes the peer's transport
  parameters: they arrive through that carrier, so its handshake has
  completed by then even when early data preceded it. A failed read closes
  the session with INTERNAL_ERROR. For a split bootstrap (SPEC.md Section 6.1.1) the exporter
  value is the 64-byte concatenation of the exporters of the uplink carrier
  and the downlink carrier, in that order, so that the session secret binds
  both carriers of the pair.
- **PSK**: at least 16 bytes supplied by the application, identical at both
  endpoints and unpredictable to third parties. It may be static (for
  example provisioned with a client's credentials) or specific to the
  session (derived from the application's own authenticated exchange on the
  bootstrap carrier, which also serves TLS stacks that expose no exporter,
  such as Python's `ssl` module, and carriers whose security comes from
  another layer). A per-session value confines the effect of its disclosure
  to that session. An application with both kinds derives one PSK from them,
  for example with HKDF.

### 10.3 Session secret

    salt           = "zmux session" || initiator Session Nonce || responder Session Nonce
    IKM            = exporter value (or empty) || PSK (or empty)
    session_secret = HKDF-Extract(salt, IKM)                      [RFC5869], SHA-256

The exporter value has a fixed length for a given bootstrap form (32 bytes,
or 64 for a split bootstrap), so the concatenation is unambiguous.

In insecure mode IKM is empty, so anyone who observed the bootstrap
parameters can compute the secret. That mode offers MPTCP-level protection
(RFC 7430) and is only for deployments that are protected by other means.

### 10.4 Keys

    join_key_i   = HKDF-Expand(session_secret, "zmux join i", 32)
    join_key_r   = HKDF-Expand(session_secret, "zmux join r", 32)

`join_key_i` belongs to the initiator and `join_key_r` to the responder. All labels
are ASCII without a terminating zero.

### 10.5 Join token

    carrier_binding = Exporter("EXPORTER-zmux-pipe", "", 32) of the new carrier      if the carrier-bound flag is set
                    = empty                                                             otherwise
    token = first 16 bytes of HMAC-SHA256(join_key of the joiner's role,
                                          PIPE_JOIN bytes from Type through Priority || carrier_binding)

The MAC input is the frame exactly as sent, so varint encodings are covered
as transmitted. The acceptor computes the token with the join key of the
peer's role, never with a role inferred from the Pipe ID (Section 5.4 step
5), and compares in constant time. A joiner sets the carrier-bound flag only
when the new carrier is carried by an end-to-end handshake in the sense of
Section 10.1 that terminates at the peer and both ends can export from it:
a TLS connection, a stream of a QUIC connection (RFC 9001 Section 4),
whose exporter is the connection's, or a carrier layer with its own such
handshake; then a token that is copied to another connection does not
verify. The binding is the exporter of the new carrier's innermost such
handshake, read by the joiner when it sends the join and by the acceptor
when the join arrives. A carrier without such a handshake (plain TCP, KCP,
a TLS or QUIC connection that an intermediary terminates) is never
carrier-bound.

Several pipes may be streams of one QUIC connection: they then share one
exporter and one carrier_binding value. This holds only where the
connection's own record layer protects every byte of every stream end to
end between the two endpoints, as QUIC's does. A stream of a multiplexer
running over the connection (a stream of another zmux session, EXTENSIONS.md
Section 10.2, an HTTP/2 stream through a proxy) is never carrier-bound,
and an endpoint never sets E from the exporter of a connection below such
a stream: the multiplexer's other carriers, or an intermediary that
terminates them, can open streams and inject bytes the binding would
wrongly vouch for. The binding proves that the
join arrived over a connection that terminates at the peer and that
whoever sent it holds that connection's keys; it does not distinguish the
streams of the connection, and need not:
- uniqueness and replay protection come from the Pipe ID, which the token
  covers and which is accepted once (Section 5.4 step 6), not from the
  binding; a token replayed on another stream of the same connection is a
  replay of a used Pipe ID and is closed at step 6;
- only the two endpoints of the connection can open streams on it and
  write to them, so a token moved between its streams can only be moved
  by the peer itself;
- the binding of pipe joins uses the label "EXPORTER-zmux-pipe", distinct
  from the session secret's "EXPORTER-zmux-session", so that the session
  secret and the binding stay independent even when pipe 0 is a stream of
  the same connection;
- such pipes fail together with their connection, and those that one
  endpoint joins SHOULD be in one non-zero fate group (Section 2.2);
- the exporter is the one of the completed handshake, never an early
  exporter: a carrier-bound join is not sent as carrier early data (TLS or
  QUIC early data, 0-RTT, or a carrier layer's first message), whose
  exporter differs or does not yet exist and whose bytes an attacker can
  replay.

### 10.6 Properties

- **One-time tokens.** The token covers the pipe ID, and each pipe ID is
  accepted at most once (Section 5.4 step 6, with the bounded replay window
  of Section 4.3), so every token is usable once. Tokens rotate implicitly:
  each new attachment uses a new pipe ID and therefore a new token. A
  captured token cannot join a different pipe ID, direction, fate group,
  priority request or session.
- **No state before verification.** Section 5.4 steps 1 to 6.
- **Replay.** Replayed joins, including requests that CDNs or HTTP/2 GOAWAY
  replay, are closed at step 6 without effect.
- **Reflection.** Tokens are keyed per joiner role.
- **Rate limiting.** Per source, never per session (Section 5.6).
- **No distinguishable failure.** The joiner never learns why a join failed.
- **Locators.** A locator identifies a session to anyone who sees a join in
  clear text. Where carriers are TLS, locators are protected by the carrier;
  over CDN-terminated HTTP the CDN sees them.
- **Inside the carrier.** Join authentication proves that a pipe belongs to
  the session. It does not protect the pipe's content, which is the carrier's
  task (SPEC.md Section 14.2).
- **Diagnosable misconfiguration.** The acceptor reports every failed
  verification as a join authentication failure (Section 5.4 step 5), so a
  secret mismatch is visible at the acceptor even though the joiner learns
  nothing.

### 10.7 Deployment guidance

- **End-to-end TLS** (the endpoints terminate TLS themselves, for example
  direct TCP, a TLS passthrough load balancer, or QUIC between the
  endpoints): `PipeAuth.exporter(...)` (E), and optionally carrier-bound tokens
  for joins over further direct TLS connections or QUIC streams.
- **Mixed carriers** (for example a relayed pipe through an intermediary
  plus direct QUIC or KCP pipes between the endpoints): the secret source
  is chosen once, from pipe 0 (Section 10.1), so a session whose pipe 0 is
  relayed uses a PSK; joins over direct QUIC streams may still be
  carrier-bound, joins over KCP or relayed carriers are not.
- **CDNs, WebSocket front ends and other TLS-terminating intermediaries**:
  never E. Use a PSK (P): either one per client, provisioned with its
  credentials, or one derived per session by the application from a secret
  its own authenticated handshake established (for example a token returned
  by a login request over the same TLS chain). The secret is mixed with the fresh session nonces (Section 10.3), so a
  static PSK still gives a fresh secret per session.
- **Plaintext carriers** (private networks): P, never I outside tests.
- **Not authentication inputs.** Positions, counters and pipe order are never
  MAC inputs; only explicit fields are (Terrapin lesson).

---

## 11. Scheduling

### 11.1 Requirements on every scheduler

The scheduler decides, whenever an outbound pipe can accept bytes, which
frames to put on it. The receiver cannot observe the policy, so policies are
local; these rules are normative for every multipath sender:

- S1. What each outbound pipe state may carry:

  | State | Frames |
  |---|---|
  | ACTIVE | every frame |
  | JOINING | control frames; while no outbound pipe is usable (SCHEDULER.md Section 10, standby pipes included), stream data as an ACTIVE pipe would carry it, within the carrier's room and the retransmit budget, reinjected information first (rule S3) and paced as RESUME.md Section 6 says; no DATAGRAM frames. A JOINING pipe is not usable until its join is confirmed (Section 5.7 step 4) |
  | SUSPECT | every frame except STREAM frames with new stream data (bytes or a FIN not sent before), reinjected information and DATAGRAM frames |
  | DRAINING | as SUSPECT until the PIPE_ABANDON exchange for the attachment is complete (Section 8.2); afterwards only PIPE_ACK and CONNECTION_CLOSE |
  | FAILED, CLOSED | nothing |

  SUSPECT and DRAINING pipes are a last resort: an endpoint puts a frame on
  one only when it has no JOINING or ACTIVE outbound pipe, or when the frame
  concerns that pipe (its PIPE_PING, the PIPE_ABANDON of its own
  attachment), or as the additional copies of PIPE_ACK and credit frames
  that Section 6.5 describes. A frame sent there is not reinjection. Which
  JOINING or ACTIVE pipes may take new stream data is further restricted by
  tier gating (S2).
- S2. Tier gating (SCHEDULER.md Section 10) comes first: new stream data,
  reinjected information, opportunistic copies and redundant copies
  (SCHEDULER.md Section 4.1) go only to the eligible pipes (apart from the
  JOINING pipes of S1 while no pipe is usable), and the policy chooses among
  them.
- S3. Reinjected information SHOULD be scheduled before new data, except
  that new stream data does not wait while reinjection after a rejoin is
  paced (RESUME.md Section 6).
- S4. Flow control, the retransmit budget (SCHEDULER.md Section 13.1) and the
  preamble rule (EXTENSIONS.md Section 4.2) apply to every policy; a
  FIN-only STREAM frame is held back neither by flow control nor by the
  retransmit budget (SPEC.md Section 8.2).
- S5. Retransmission only as Section 7 allows.
- S6. PIPE_ACK placement as Section 6.5.
- S7. DATAGRAM frames SHOULD go to the eligible pipe with the lowest
  estimated arrival time (SCHEDULER.md Section 4), except that a stream's
  datagrams follow its unacknowledged opening frame (EXTENSIONS.md Section
  6.4), even onto a less-preferred tier for about a round trip, and are never
  reinjected.
- S8. Within the frames chosen for a pipe, stream priorities apply
  (IMPLEMENTATION.md Section 5).
- S9. Credit frames go to the first pipe of the control order (Section 6.5),
  with the copies that Section 6.5 describes.
- S10. **Late binding.** Frames are bound to a pipe only when that pipe can
  accept bytes, not earlier. The core MUST NOT hold more than one
  `spread_chunk` plus one record of frames committed to a pipe but not yet
  accepted by its carrier; SCHEDULER.md Section 9 bounds what the carrier
  itself may hold. A redundant copy queued for a pipe (SCHEDULER.md Section
  4.1) is not bound until that pipe accepts bytes and can be dropped until
  then.
- S11. **Lanes.** A pipe with lanes (Section 2.5) is one pipe for every rule
  above; the lane a frame goes on is the scheduler's choice (SCHEDULER.md
  Section 9.2). Late binding (S10) holds per lane: what the core has
  committed to a lane counts toward the pipe's bound.

### 11.2 The zmux scheduler

zmux implementations schedule with the estimators, policies, tier gating,
probation and budgets of SCHEDULER.md, which is normative for them
(CONFORMANCE.md C-5 and C-8) without being part of the protocol: an endpoint
that schedules differently within the rules of Section 11.1 interoperates.
This document uses its terms: a pipe is **usable** when it may take new
stream data at all, **eligible** when tier gating lets it take the next
chunk, and **in probation** for a while after it joins or returns
(SCHEDULER.md Sections 10 and 11); the policies are listed in SCHEDULER.md
Section 3.

---

## 12. Receive windows

To keep every pipe busy when one stream is spread over all of them, the
stream and session receive windows need to reach

    W >= 2 x sum(R_p) x RTT_max + sum(R_p) x J,     J = max(4 x max(rttvar_p), 10 ms)

where RTT_max is the largest RTT of the pipes that carry the data. Receivers
reach this by auto-tuning (IMPLEMENTATION.md Section 6.2), which sizes a
window as the arrival rate times max(4 x RTT_max, B), with RTT_max the
largest minimum RTT of the pipes the scope's data arrived on (SPEC.md Section
8.3 rule 6): 4 x sum(R_p) x RTT_max covers the 2 x term, and the jitter term
whenever J <= 2 x RTT_max (RTT_max >= 5 ms at the 10 ms floor of J); on
shorter paths the window follows the measured loop instead. The stream
window cap and session window cap apply (by default 64 MiB and 96 MiB
floors that auto-tuning exceeds within the memory budget, SPEC.md Section
8.3 rule 4), bounded by the session buffer cap. The caps are memory bounds,
not protocol limits: paths whose 4 x sum(R_p) x RTT_max exceeds the
budget's share (10 Gbit/s at 200 ms needs about 1 GB) need a larger memory
budget at the receiver and at the sender, whose send buffer maximum grows
within its budget the same way (IMPLEMENTATION.md Section 6.5). When the window is smaller, the BLEST check (SCHEDULER.md
Section 5) keeps slow pipes out instead of stalling the fast ones. What the
sender retains is bounded by SCHEDULER.md Section 13.

---

## 13. Receiver requirements for spread streams

1. Accept any interleaving of a stream's frames across pipes and within a
   pipe (SPEC.md Section 7.3).
2. Reassemble by offset; buffer out-of-order data up to the advertised
   limits; never discard data within them.
3. Deliver data to the application in offset order, as soon as it is
   contiguous.
4. Handle frames that open streams in any order (SPEC.md Section 7.2), the
   preamble arriving after later data of the same stream (EXTENSIONS.md
   Section 4.4), and RESET_STREAM before data.
5. Account session credit by max(offset, final size) (SPEC.md Section 8.2).

---

## 14. Fairness

Every TCP pipe runs its own congestion control. Pipes that share a bottleneck
together take several shares of it; zmux cannot couple the congestion control
of kernel TCP connections (RFC 6356 needs coupling inside the transport). By
default an application SHOULD open at most one pipe per distinct network
interface or relay and per direction, and SHOULD give pipes that share a
bottleneck the same non-zero fate group so that schedulers do not count them
as independent capacity. When only single-interface multipath is wanted on
Linux, kernel MPTCP on a single pipe is the better tool. IMPLEMENTATION.md
Section 8.5 lists the trade-offs.

---

## 15. Interactions

- **GOAWAY**: the PING fence is best effort in multipath sessions
  (EXTENSIONS.md Section 3.3), and a fence request lost with a pipe is
  replaced (SPEC.md Section 4.5); the "not processed" guarantee does not
  depend on it.
- **Closing**: CONNECTION_CLOSE goes on every live outbound pipe; the first
  copy received closes the session (SPEC.md Section 6.5).
- **Datagrams**: never reinjected; lost with their pipe (EXTENSIONS.md
  Section 6.5).
- **Preamble**: always in the frame at offset 0, also after reinjection.
- **Resume**: RESUME.md.

---

## 16. Defaults

| Item | Default | Reference |
|---|---|---|
| zmux_ack_threshold | 65536 bytes | 3.2 |
| pipe acknowledgement without multipath | `zmux_pipe_ack` sent in every session; PIPE_ACK on pipe 0 by rules 1 and 2 of 6.3, nothing retained | 3.5 |
| zmux_max_ack_delay | 25 ms (10 ms for low-latency profiles) | 3.2 |
| acknowledgement redundancy interval | 50 ms on a second pipe | 6.5 |
| redundant urgent data | STREAM frames of urgency 0 to 2 on two pipes of their tier, and the first chunk after max(1 s, T_suspect) without acknowledgement progress on one more; at most max(spread_chunk, retransmit budget / 64) of copies unacknowledged | 7.5; SCHEDULER.md 4.1 |
| T_suspect | max(suspect floor, 2 x (PTO_p + queue_p)); suspect floor 250 ms, per-pipe option `SuspectFloor` down to 50 ms | 7.1 |
| T_fail (another pipe ACTIVE) | max(2 s, 8 x (PTO_p + queue_p)) | 7.1 |
| last-pipe stall limit T_dead | max(T_suspect, dead_timeout); dead_timeout is the per-pipe option `DeadTimeout` (at least 1 s), by default T_fail with resume and max(T_fail, half the effective idle timeout) without (15 s by default) | 7.1 |
| idle-pipe probe interval | per-pipe option `ProbeInterval` (0 or at least 1 s; a value in between is a configuration error), by default the scheduler parameter `probe_interval` (15 s; 0 = no idle probes on pipe pairs); at most 15 s, a protocol constant, for attachments without an outbound direction at the peer; measured to the carrier: k_eff = min(k, max(1 s, k - H_p)); each wait drawn uniformly from [k_eff x (1 - j), k_eff] with the probe jitter j (`ProbeJitter`, by default the scheduler parameter `probe_jitter`, 0.25), the first wait of a bootstrap pipe from [0, k_eff] when j > 0; probing more often is allowed | 7.1 |
| backup-pipe probe interval | `standby_probe`, 1 s (scheduler parameter; 0 = off, leaving the probe interval): standby pipes and pipes of a less-preferred tier, while the session is busy (a traffic frame, QX_PING excluded, within the busy window of 15 s); in a quiet session the probe interval, plus one PIPE_PING on each such pipe when traffic resumes | 7.1 |
| inbound silence limit (attachments without a local outbound direction) | probe interval + n_in / 8 s + max(2 s, 2 x srtt_max, G_p - probe interval); 17.25 s with two inbound pipes; for the last live inbound pipe at least the last-pipe stall limit | 7.3 |
| write call size | one batch; clamp(R_p x B / 2, 16 KiB, 64 KiB) while latency-sensitive work waits, B the latency budget (IMPLEMENTATION.md Section 4); blocking adapters also max(4 KiB, R_p x 50 ms); non-blocking carriers: what the carrier takes, within the bound | 7.1 |
| initial SRTT and delivery rate for a pipe | 333 ms (rttvar 166.5 ms) and 1 MB/s until its own first samples | 6.6 |
| pipe priority | 0 (any value 0 to 2^62 - 1, lower preferred), status available, per direction | 9 |
| stream / session window caps | auto: 64 MiB / 96 MiB floors (4 x the BDP of 1 Gbit/s x 100 ms), beyond them within the memory budget (1 GiB); a configured value is a fixed cap (IMPLEMENTATION.md Section 6.5) | 12 |
| estimators, policies, overflow delay, probation, budgets | SCHEDULER.md Section 14 | SCHEDULER.md |
| max_pipes (attachments each endpoint aims for with its own joins) | 8 when multipath is used (API.md Section 6 defaults to 1, multipath off); no upper limit | 5.4 |
| attachment headroom H | 8 (the `headroom` parameter of `max_pipes`) | 5.4 |
| unretired attachments of the peer | at most max_pipes + H kept, the bootstrap pair included, and so at most max_pipes + H live; beyond that joins are refused with ZMUX_PIPE_RESOURCE_LIMIT, or, while suspended, one attachment is displaced: the oldest failed one, else, for a join that provides a lacking direction, the live one with the oldest last progress | 5.4 |
| replay window W | max(64, 2 x (max_pipes + H)) pipe IDs of the peer's parity below and above the highest verified join; configurable, at least max_pipes + H | 4.3 |
| join refusal backoff | 1 s after the peer's ZMUX_PIPE_RESOURCE_LIMIT refusal or abandonment, doubled per consecutive one, at most 60 s, or until an own attachment is retired | 5.7 |
| unilateral retirement | max(2 s, 3 x PTO) after the own PIPE_ABANDON is acknowledged | 8.4 |
| pending joins | 64 per listener, 4 per session | 5.6 |
| failed joins | rate limited per source (listener policy) | 5.6 |
| unauthenticated carriers | 8 per source address; first record within 2 s | 5.6 |
| PIPE_PING | at most 8 per session in any one-second interval | 6.4 |
| lanes accepted per inbound pipe (`zmux_max_lanes`) | 258, lane 0 included (API.md Section 6 `max_lanes`; 1 = no lanes); any value allowed | 3.6 |
| lanes opened | on demand, once lane 0 of the pipe is acknowledged, up to the peer's L and the carrier's stream credit; only PIPE_LANE until confirmed; a refused lane ends lane opening on that pipe; confirmed lanes never closed before the pipe | 5.8; SCHEDULER.md 9.2 |
| incomplete records with lanes | at most one per lane, within the carrier connection's receive window | SPEC.md 9.8 |
| PIPE_PING answer | on the probed pipe pair only, in a quiet session when it acknowledges nothing else; with a PIPE_PING of its own after R_p = max(1 s, T_suspect) without ack-eliciting bytes | 6.5 |
| return-pipe liveness PING | busy sessions: on the primary and redundant acknowledgement pipes after R_p without ack-eliciting bytes | 6.5 |

---

## 17. Security considerations

- Join authentication: Section 10.
- Unauthenticated carriers can hold listener resources only within Section
  5.6 limits and only until the handshake timeout.
- A peer can make the endpoint keep at most max_pipes + H unretired
  attachments (the cap of Section 5.4 step 9 and unilateral retirement,
  Section 8.4), which bounds the size of PIPE_ACK snapshots (Section 6.2),
  and, for pipe IDs without attachment state, at most the 2W bits of the
  replay window with their pending PIPE_ABANDON (Section 4.3). Frames about
  IDs of the receiver's own parity that it has not taken are fatal, and
  PIPE_STATUS for IDs without attachment state is ignored (Section 9), so no
  frame makes the receiver store state per unknown ID. Only verified joins
  move the replay window, so frames injected on an unprotected carrier
  cannot push it past the joiner's future pipe IDs.
- A peer that keeps joining beyond the cap is refused each time; the
  refused joins cost state only inside the replay window, and their rate is
  bounded by the listener's per-source limits (Section 5.6) like any new
  connection.
- An authenticated peer can make the endpoint retain at most its own flow
  control limits of data and its retransmit budget (SCHEDULER.md Section
  13.1).
- A lane is accepted only with the receiver's Lane Key, which travels only
  in the session's own records (its transport parameters and its lanes),
  and only on the connection of its pipe's lane 0 (Section 5.8). A
  connection may carry the streams of several parties (a relay or a proxy
  that forwards the streams of many clients over one connection), so the
  connection alone would let one party's stream join another's pipe; the
  key confines a lane to the session that issued it, and the connection
  check to the pipe's own path. Whoever knows the key reads the session's
  records already, so the key needs no MAC and no replay protection: a lane
  replayed onto another connection names no pipe there and is refused. A
  peer can make the endpoint keep at most L - 1 lanes per inbound pipe,
  their incomplete records within the session's budget (SPEC.md Section
  9.8).
- Spreading exposes per-pipe traffic volumes to observers of each carrier.
- A CDN or other intermediary that terminates the carrier's TLS sees the
  pipe's content and locators; end-to-end protection then needs the reserved
  record protection feature (REGISTRY.md Section 7) or application-level
  encryption.
