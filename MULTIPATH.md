# zmux Multipath

Status: normative, part of the zmux core protocol (SPEC.md Section 11).
Every conforming implementation implements this document. A session uses it
when both endpoints advertise `zmux_multipath` (Section 3), so that zmux can
talk to plain QMux peers and sessions that do not need multipath pay nothing
for it. A zmux session without multipath uses only the acknowledgement of
Section 6 on its single pipe pair (Section 3.5).

This document defines what goes on the wire and what each endpoint does in
response. How a sender estimates its pipes and chooses among them is local
policy, invisible on the wire, within the rules of Section 11.

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
QUIC's packet-level machinery with pipe-level machinery.

Every stream's data may be scattered frame by frame over all pipes in each
direction. Per-frame spreading is a first-class mode, not an exception: any
stream may be spread at any time, and receivers MUST handle any interleaving
(Section 12).

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
an HTTP GET downlink multiplexed on one HTTP/2 connection. A pipe pair is
always one fate unit. Fate groups inform failure detection (Section 7.1);
they never change wire semantics.

Fate group numbers are scoped to the joiner: attachments joined by different
endpoints never share a fate group, whatever their numbers. To spread one pipe over several
streams of its connection, an endpoint uses lanes (Section 2.5), not several
pipes.

### 2.3 Positions

The **position** of a pipe (of each lane of a pipe that has lanes, Section
2.5) is a byte count from the first byte of the pipe's (the lane's)
record stream (the first byte after the Join handshake, and after a
QX_TRANSPORT_PARAMETERS record that leads it, Section 5.7 step 3). The
sender's position is the number of bytes passed to the carrier. The
receiver's position is the number of bytes in records that it has completely
received and processed (Section 6.1). On message-framed carriers each message
counts as the bytes of its byte-stream form (SPEC.md Section 3.4).

### 2.4 Carrier adapters

The core sees a pipe as a byte source or a byte sink plus close. Any carrier
that delivers bytes reliably and in order can be attached, including a
stream of another zmux session (a nested session, EXTENSIONS.md Section 10).
If an outbound pipe breaks, its unacknowledged information is re-sent on the
other pipes without the application noticing (Section 7).

A carrier that decides its connection is dead (an idle timeout, a failed
keep-alive, an error) fails the pipe at once (Section 7.1).

### 2.5 Lanes

A pipe whose carrier is one stream of a **multi-stream carrier connection**
(a QUIC connection, and HTTP/3 or WebTransport streams on one; an SCTP
association; a zmux session whose streams carry the pipes of a nested
session, EXTENSIONS.md Section 10; an HTTP/2 connection) can be carried over
several streams of that connection at once. Each of them is a **lane** of
the pipe:

- Lane 0 is the carrier stream on which the pipe started: the one that
  carried its Hello, and for the bootstrap pipe the bootstrap carrier. Lanes 1, 2, ... are streams
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
  detection and its place in the scheduler. A lane is not an attachment: it has no
  Join, takes no place in max_pipes + H (Section 5.4 step
  9) and never touches the replay window (Section 4.3). It is accepted only
  with the receiver's lane key and only on the carrier connection of its
  pipe's lane 0 (Section 5.8); Section 15 discusses why that suffices.
- The confirmed lanes of a pipe live and fail together. A lane is
  **confirmed** once a PIPE_ACK acknowledges a position greater than 0 on
  it (Section 5.8); before that it carries nothing but its PIPE_LANE, and a
  lane that ends before it is confirmed is **refused**, which costs the
  pipe nothing. The pipe ends gracefully when every lane has ended
  gracefully at a record boundary (Section 7.3); an error, a reset or an
  unexpected end of any confirmed lane is a failure of the pipe (Section
  7.1). A confirmed lane is never closed on its own: an idle lane sends
  nothing and costs only its reading state.
- **Send order.** The sender numbers the bytes it hands to the lanes of a
  pipe in the order it hands them over, across lanes, except the first
  record of a lane (its PIPE_LANE), whose acknowledgement only confirms the
  lane; this **send order** is local and never on the wire. The pipe's
  **cumulative acknowledgement point** is the send-order number of its
  oldest byte that is not yet acknowledged, or of the next byte when all are
  acknowledged; without lanes it is the acknowledged position. Wherever this
  document refers to a pipe as a whole by its acknowledged position (RTT
  samples, Section 6.6; the ack stall and the return from SUSPECT, Section
  7.1; the last progress of Section 5.4 step 9), a pipe with lanes uses its
  cumulative acknowledgement point. A frame is acknowledged, and is "beyond
  the acknowledged position" for reinjection (Sections 7.2, 7.4 and 8.2), by
  the position of the lane that carried it (SPEC.md Section 7.4).
- **Carriers.** Lanes are an optional capability of a carrier adapter. A
  pipe pair on a QUIC connection (and on HTTP/3 or WebTransport over one),
  pipe 0 included, is always one bidirectional stream, whose two halves are
  lane 0 of the two directions; it is never split into a unidirectional
  stream per direction, and a pipe joined in one direction only (Section
  5.1) is a bidirectional stream too, its reverse half unused. A further
  lane is a unidirectional stream on a QUIC connection and on WebTransport;
  on carriers whose further streams are bidirectional (HTTP/3 request
  streams, SCTP, HTTP/2, the streams of an outer zmux session, which carry
  PIPE_CARRIER, EXTENSIONS.md Section 10) it is a bidirectional stream whose
  reverse half is unused: the reader ends that half at once, and its end is
  no event of the pipe. A carrier with one stream per connection (TCP, TLS,
  WebSocket) has no lanes; a further pipe on another connection needs a
  Join. Lanes opened on a connection that an intermediary splits do not
  reach the peer as one connection and are refused (Section 5.8).

---

## 3. Negotiation

### 3.1 The `zmux_multipath` transport parameter

ID 0x30dc2978. Value:

```text
zmux_multipath Value {
  Secret Flags (8),
  Session Nonce (128),
  Locator Length (8),
  Session Locator (0..2040),
}
```

- **Secret Flags**: the secret sources the endpoint can provide (Section
  10.1): 0x01 E (exporter of the bootstrap carrier's innermost end-to-end
  handshake), 0x04 P (pre-shared
  key). Other bits are reserved; senders set them to 0 and receivers ignore
  them.
- **Session Nonce**: 16 bytes chosen uniformly at random for this session.
- **Session Locator**: 0 to 255 bytes that the peer puts into a Hello to
  reach this session through this endpoint's listener. Its content is chosen
  by the endpoint (for example a random identifier, optionally with an
  instance hint for load-balancer stickiness). Length 0 means that this
  endpoint does not accept joins. A value whose length disagrees with Locator
  Length is a TRANSPORT_PARAMETER_ERROR. The one-byte length is the only bound:
  a receiver accepts any length, and the endpoint that issues locators chooses
  how long they are. This is the Locator of epoch 0; an epoch exchange may replace
  it (Section 10.7).

### 3.2 Companion parameters

| Parameter | ID | Value | Absent means | Range |
|---|---|---|---|---|
| zmux_ack_threshold | 0x2762c7e5 | bytes: the sender of the parameter acknowledges no later than this many ack-eliciting bytes on any pipe | 65536 | at least 1024 |
| zmux_max_ack_delay | 0x17fa1ed6 | milliseconds: the sender of the parameter acknowledges no later than this after receiving an ack-eliciting byte | 25 | below 2^14 (as QUIC's max_ack_delay, RFC 9000 Section 18.2) |

These parameters MUST NOT be sent without `zmux_multipath` or `zmux_pipe_ack`
(Section 3.5; a TRANSPORT_PARAMETER_ERROR, SPEC.md Section 5.5). A value out
of range is a TRANSPORT_PARAMETER_ERROR.  QUIC's
`max_ack_delay` (0x0b) remains prohibited.

### 3.3 Enablement

Multipath is in use when both endpoints sent `zmux_multipath` and the secret
source of Section 10.1 exists. If both sent the parameter but there is no
source, multipath is not in use; both endpoints know this from the two
parameter values. Each endpoint decides from configuration whether to send
`zmux_multipath`.

A session in which multipath is in use is **join pending** while an endpoint
has no join keys: until the application supplies a deferred PSK (Section 10.2),
and on an Unmanaged first pipe until the application has declared it end to end
(Section 10.1). While join pending the endpoint starts no join and holds the
peer's (Section 5.4 step 4). It ends once its conditions hold, and never begins
again.

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
   unacknowledged information (SPEC.md Section 7.4).
5. CONNECTION_CLOSE is sent on every outbound pipe (SPEC.md Section 6.5).

### 3.5 Pipe acknowledgement without multipath

zmux endpoints acknowledge the single pipe pair of a session without
multipath as well.

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
   `zmux_max_ack_delay` (25 ms unless sent) after the first of them, or at
   once when the first of them arrives more than that delay after its last
   PIPE_ACK (or before its first). There
   is no PIPE_PING and no other multipath frame. It SHOULD put the PIPE_ACK
   into a record it writes anyway (credit updates, stream data), and writes
   a record of its own only when a deadline would otherwise pass. PIPE_ACK
   is not ack-eliciting, so acknowledgements never answer each other.
4. Nothing is retained (SPEC.md Section 9.6): a PIPE_ACK acknowledges
   nothing that could be sent again. Items 1, 2, 4 and 5 of Section 3.4 do
   not apply: data stays in order and a pipe failure ends the session. Item
   3 applies: the idle and keep-alive timers count application activity
   received or acknowledged (a record that carries only PIPE_ACK or credit
   frames restarts neither), the idle timeout sends
   CONNECTION_CLOSE(ZMUX_IDLE_TIMEOUT), and a graceful end of the peer's
   pipe without CONNECTION_CLOSE is a pipe failure, not an idle close
   (SPEC.md Section 6.4).

The acknowledgement feeds only the sender's estimates and the liveness that
the idle timer measures (SPEC.md Section 6.4); it plays no other part in
failure detection: no ack stall fails the pipe.

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

Any value of Max Lanes is allowed; 1 turns lanes off.

### 3.7 The `zmux_session_crypto` transport parameter

ID 0x026f24b5. Value:

```text
Value          = SuiteSet ‖ GroupSet
SuiteSet       = Count(varint) ‖ Suite(varint) ...    ascending, shortest
GroupSet       = Count(varint) ‖ Group(varint) ...    ascending, shortest
session_crypto = Value(session initiator) ‖ Value(session responder)
```

`Suite` is a Join Suite and `Group` a key exchange group of secure-carrier/SPEC.md
Sections 11.1 and 11.6. A SuiteSet holds Suite 1 and a GroupSet holds Group 2; neither
holds zero or a duplicate. An endpoint MUST send the parameter whenever it sends
`zmux_multipath`, and MUST NOT send it otherwise. A received value that breaks these
rules, its absence beside `zmux_multipath`, or its presence without it, is a
TRANSPORT_PARAMETER_ERROR. A local configuration whose value would break them is
`invalid_config` (secure-carrier/SPEC.md Section 9), and the endpoint sends nothing. An endpoint declares only what it implements; it MAY leave
Group 1 out. The usable Suites and the usable Groups are the intersections of the two
sets of each; Group 2 is always usable.

Each endpoint builds `session_crypto` from its own value as locally configured and the
peer's as received, in the order of the session's initiator and responder after any role
resolution, and MUST NOT read its own value back from the wire. `session_crypto` enters
every join key and epoch secret (Sections 10.4 and 10.7), so a change on the path makes
every join fail.

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
that session .

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
ID of a Hello it has verified (Section 5.4 step 5), and a bitmap that
covers the W pipe IDs of that parity below h and the W above it. For an
initiator peer h starts at 0, the bootstrap pair; for a responder peer, which
has no attachment yet, the bitmap covers its first W pipe IDs until its
first verified join. W is a local safety bound of at least max_pipes + H (H,
Section 5.4 step 9), counted in pipe IDs of that parity; the default is
max(64, 2 x (max_pipes + H)).

- A bit is set when the ID is recorded as used
  without being attached: a verified Hello (Section 5.4 step 7), or
  PIPE_ABANDON for an ID not seen joined (Section 8.1).
- Only a verified Hello raises h. When h rises, bits that fall below the
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
parity without attachment state follow Sections 6.2, 8.1 and 9.

---

## 5. Joining a pipe

### 5.1 Join

A pipe is added with the Join handshake of secure-carrier/SPEC.md Section 11,
which fixes its messages, keys, parse order and error classes. It is the only way
to add an attachment. It runs on the new carrier, before any zmux record and
for every session, and its key comes from the session secret (Section 10.4).

The joiner fills the fields of the Hello as follows:

- **Suite**: one of the session's Suites (Section 3.7), by local preference.
- **Mode**: the record mode of this carrier (Section 10.5).
- **Locator**: the acceptor's locator from its `zmux_multipath` parameter.
- **join pipe** payload item:
  - **PipeID**: the new attachment's pipe ID (Section 4.1).
  - **Direction**: 0 up, 1 down, 2 bidi.
  - **FateGroup**: Section 2.2.
  - **Priority** and **Standby**: the joiner's initial request for the
    acceptor's outbound direction of the new attachment, with the meaning of a
    PIPE_STATUS request (Section 9) at sequence number 0. For direction up the
    acceptor has no outbound direction on the attachment: the joiner sends
    Priority 0 without standby, and the acceptor ignores both.

The Hello, the Reply and the confirmation are outside the pipe's record stream:
they count in no position (Section 2.3), are never acknowledged and never
retransmitted. Positions count from the first record after them. In Sealed mode a
position counts the record's plaintext form, without the protection's overhead.

### 5.2 First batch and first record

**First batch.** The Hello's *frames* item MAY carry a first batch, within the rules
of secure-carrier/SPEC.md Section 11.3, and only stream data that rule S1 of Section
11 allows on a JOINING pipe. A first batch is at most 8 KiB unless the joiner is
configured otherwise, and an acceptor's Hello limit allows at least that. What it
carries stays unacknowledged at the joiner and is sent again after the Reply as
reinjected information, because the batch counts in no position and an acceptor may
drop it; duplicates are harmless (Section 7.6). 

**First record of the joiner.** After Proven (Section 5.7 step 4), the first record
the joiner writes on an outbound direction MUST carry a complete PIPE_ACK snapshot
(Section 6.2) when the joiner has at least one entry, and SHOULD carry a
PIPE_PING. The snapshot may continue at the start of the next record when it does not fit in
the first. The PIPE_PING counts against the limit of Section 6.4.

**Return frames.** The Reply's *frames* item MAY carry frames for the joiner; they are
protected by the Reply's key in every mode, and count in no position. Before Confirmed
the Reply is the acceptor's only output. In Sealed and Bound the session layer holds the
whole Reply, padding included, to at most 3 times the bytes received on the carrier
(secure-carrier/SPEC.md Section 11.5): it adds padding only after the frames, and frames
that do not fit stay queued for the records that follow Confirmed. In Trusted the limit
does not apply. A joiner applies return frames only once Proven.

### 5.3 Direction and carriers

The handshake needs both directions of the carrier, whatever the attachment's
direction. Direction says which directions stay in use afterwards:

| Direction | Joiner after the handshake | Acceptor after the handshake |
|---|---|---|
| bidi | reads and writes | reads and writes |
| up | writes; ends reading | reads; ends writing after the Reply |
| down | reads; ends writing after its confirmation | writes; ends reading after that confirmation |

A carrier with two halves (Section 2.1) runs one handshake across them: the joiner
writes the Hello and the confirmation on the uplink half and reads the Reply on the
downlink half. The two halves are found together by a pairing token, as for a split
bootstrap (SPEC.md Section 6.1.1), and the pair is one carrier from then on: the
attachment fails as one, and a carrier with direction up or down closes the unused
half when the table says. Before a pair is complete no handshake byte is processed.
A split carrier cannot use Bound mode at present (secure-carrier/SPEC.md Section 11.4): it has no Binding, so a Hello in Bound on it is refused as `auth`.

A carrier the joiner cannot both write and read, even with a second half, cannot be
joined. No field of the handshake travels outside the carrier.

### 5.4 Acceptor processing

An acceptor (normally a listener that serves many sessions) processes a new carrier as follows. Steps 1 to 6 MUST allocate no session
state and MUST perform no session operation; they may hold the carrier and at
most one Hello of it.

Every refusal in steps 1 to 5 goes to the reject policy of
secure-carrier/SPEC.md Section 9, which closes or stalls the carrier and sends
nothing; a stalled carrier keeps its slot of Section 5.6.

1. Apply the pending-join limits of Section 5.6 (except to the streams
   admitted for a lane, Section 5.5). On excess, refuse the carrier.
2. Classify the first bytes (Section 5.5). Parse the Hello's head as
   secure-carrier/SPEC.md Section 11.2 says. On any failure, refuse the carrier.
3. Refuse a Suite outside this implementation, then look up the Locator, whatever
   its length, among every Locator that a session accepts (Section 10.7). Refuse the carrier if no session matches, if the Suite is outside
   the found session's set (`unknown_suite`, without opening the Hello), or if the
   acceptor declines it here for a local resource limit, such as memory pressure.
4. If the session is not yet established (the joiner's transport parameters
   are still in flight), hold the join until establishment completes or the
   join timeout expires. Likewise, if the session has no join keys yet
   (**join pending**, Section 3.3), hold the join, still without session state
   and within the limits of Section 5.6, until the keys exist or the join
   timeout expires; then close the carrier silently (it is not a join
   authentication failure, and an implementation counts it separately). A held
   join occupies a pending slot, so this wait is bounded by the join deadline
   (Section 5.7), which is never unbounded.
5. Open the Hello and parse its payload, with the checks of its key share
   (secure-carrier/SPEC.md Sections 11.3 and 11.5 step 2), always with the join key of the joiner's role in the session (the session knows
   which role the peer has), never with a role inferred from the Pipe ID's parity. On
   failure, refuse the carrier and report a **join authentication failure** to the
   application, a distinct event and statistic (ZMUX_PIPE_AUTH_FAILED
   is its local code). Nothing is sent to the peer.
6. If the Pipe ID has the joiner's parity and has attachment state, its bit
   is set, or it is stale (Section 4.3), close the carrier: the join is a
   replay or arrived too late. Count it in the statistic `join_replays`. A
   Pipe ID of the other parity goes on to step 7.
7. From here on the join is authenticated, so violations are the peer's. Wrong
   parity for the joiner closes the session with PROTOCOL_VIOLATION. A Mode or
   authorization that local policy refuses (Section 10.5) closes the carrier without
   setting the Pipe ID's bit, and is counted. Otherwise the acceptor sets the
   Pipe ID's bit and raises h if the ID is above it. Only then does it perform its side of
   the key exchange (secure-carrier/SPEC.md Section 11.6), so that a replayed Hello costs
   at most one AEAD: a share it refuses (`kex`) closes the carrier, is counted, and leaves
   the bit set. It then applies the first batch (Section 5.2). It then writes the Reply with its decision, which is a read-only
   prediction and changes no state. It answers **refuse** when:
   - the direction is one that the carrier cannot provide;
   - the carrier is a stream of a session running, directly or through
     other sessions of the process, over a pipe of this session, as far as
     the acceptor can see (EXTENSIONS.md Section 10.2; a
     local condition, not a violation);
   - resources are exhausted, including the cap of step 9 as it stands then.

   After a refusal the acceptor closes the carrier; the ID leaves only its bit, and
   no PIPE_ABANDON is sent, since the joiner read the decision. An
   accepted carrier is **Unconfirmed**, holds a slot of Section 5.6 and has no
   attachment state; a replayed Hello therefore moves no pipe, evicts nothing and
   ends no suspension. The carrier stays Unconfirmed until the confirmation of its
   Mode, or the join deadline (Section 5.7), which closes it. A new authenticated Hello of the
   session MAY evict the session's oldest Unconfirmed carrier and MUST NOT be refused
   for lack of room.
8. **Confirmed** (secure-carrier/SPEC.md Section 11.5 step 5): apply step 9, then
   attach the pipe(s): create pipe state, take the joiner's initial request from the
   Hello (Section 9), and process the pipe's records. The acceptor MAY write on its
   outbound direction of the attachment at once, and for a down or bidi attachment
   MUST write at least one record on it within its `zmux_max_ack_delay`. That first
   record MUST carry a complete PIPE_ACK snapshot (Section 6.2) when the acceptor
   has at least one entry, and a PIPE_PING, subject to the PIPE_PING limit
   of Section 6.4 (a pipe whose PIPE_PING is delayed by the limit gets it as
   soon as the limit allows). An acceptor with a non-default request for the joiner's
   outbound direction puts its PIPE_STATUS in the same record (Section 9).
   If step 9 no longer allows the attachment, or a loop is now found, the acceptor
   closes the carrier and sends PIPE_ABANDON(Pipe ID, ZMUX_PIPE_RESOURCE_LIMIT) on another
   outbound pipe, so that the joiner, which is already ACTIVE, backs off (Section
   5.7) instead of rejoining at once; never ZMUX_PIPE_FAILED, which it would answer by
   rejoining. The loop check and the recording that the session now runs over
   the carrier are one atomic step of the process.
   An implementation that can still find a loop later (one formed concurrently
   through another session) removes the attachment abruptly with the same code.
9. Cap on unretired attachments: an acceptor keeps at most max_pipes + H
   attachments joined by the peer that are not retired (live, or failed or
   abandoned but not yet retired, Section 8.4). H, the **attachment
   headroom**, is 8 by default. The bootstrap pair counts as an attachment joined
   by the initiator (Section 2.1). The count is taken after the join or
   failure being processed has been applied. Step 7 only predicts this; the check
   that holds is the one at Confirmed. Once the cap is reached:
   - outside suspension, the acceptor refuses an authenticated join beyond
     the cap as resource exhaustion (step 7 or 8); that is not a session error. A
     refused join is never attached and does not count against the cap: it
     leaves only its bit, inside the replay window of Section 4.3;
   - while the acceptor is SUSPENDED (RESUME.md Section 4), the PIPE_ABANDON
     exchanges that retirement needs cannot complete, so instead of refusing
     the join it **displaces** one attachment of the peer at Confirmed and then accepts
     the join; its decision in step 7 predicts this. It retires locally one
     unretired attachment of the peer that has failed: preferably the oldest
     one whose own PIPE_ABANDON has been acknowledged, otherwise the oldest one
     whose own PIPE_ABANDON has not, dropping its state including that pending
     PIPE_ABANDON (the peer retires it by unilateral retirement, Section 8.4).
     Only if none has failed, all of them are live, and if the join provides a
     direction the acceptor lacks, none of them provides that direction (the
     acceptor would not be suspended otherwise): it then fails the one whose
     **last progress** (the later of the last advance of its outbound
     acknowledged position, the last record received on its inbound
     direction and its attachment) is oldest, as Section 7.2 says but
     without sending PIPE_ABANDON, and retires it locally at once. A join
     that provides no direction the acceptor lacks is refused as outside
     suspension.

     Displacing an attachment drops every PIPE_ABANDON the endpoint owes for
     it: one sent and awaiting acknowledgement is no longer retained or
     re-sent, and one queued but not yet handed to a carrier is removed from
     the queue and never sent. This covers only frames for the displaced
     attachment. A PIPE_ABANDON for a Pipe ID without attachment state is
     unaffected and is sent as Sections 4.3 and 8.1 say, for example the
     PIPE_ABANDON(ZMUX_PIPE_RESOURCE_LIMIT) for a join refused at Confirmed
     (step 8) and the answer to a peer's PIPE_ABANDON for an ID that has no
     state.

   A joiner applies the same cap to the attachments it joined itself: it does
   not join while it has max_pipes + H of them unretired, except that a
   joiner that is SUSPENDED first displaces one of them in the same way
   before a join that provides a direction it lacks: a failed one, preferably
   the oldest whose own PIPE_ABANDON has been acknowledged, otherwise the
   oldest whose own PIPE_ABANDON has not; only when none has failed, the live
   one with the oldest last progress. Its PIPE_ABANDON for the displaced attachment,
   retained or still queued, is dropped as above. Joins that the cap holds back are
   judged one by one, not in the order they were held: each that provides a
   direction the joiner lacks proceeds as soon as a displacement makes room
   for it, while the others keep waiting. A peer may have up to max_pipes + H attachments live at once.

### 5.5 Listener classification

A listener that accepts both new sessions and joins classifies a new carrier
by its first bytes, applying secure-carrier/SPEC.md Section 3.3 and the incremental
check of SPEC.md Section 6.2:

- first byte `02`: a join; on a byte stream no first record begins with it, since a
  Size is at least 9;
- `ff 51 53 30 0d 0a 0d 0a` after a Size from 9 to the listener's own
  `max_record_size`: a new session;
- `ba 05 a1 c9` (PIPE_LANE) after a Size from 22 to the listener's own
  `max_record_size`: a lane (Section 5.8).

Any other prefix is rejected as soon as it is seen. A secure carrier transform
handles its own Establish Profiles and passes first byte `02` on to the core: Join is
never its concern. An adapter that hands a new stream of
a connection already carrying pipes of the endpoint directly to the
endpoint's sessions, without a listener, classifies the same way.

**Lanes and the carrier limits.** The streams that a lane needs are not
held to the carrier limits of Section 5.6 (the listener, session and source
bounds and the eviction), and no other stream escapes them. A new stream
that the adapter delivers with the identity of a multi-stream carrier
connection is admitted outside those bounds only when,
at the moment it arrives, the connection carries lane 0 of at least one
**OPEN inbound pipe** of one of the endpoint's sessions (STATE_MACHINE.md
Section 3.2: the bootstrap pipe of an established session, or a pipe
attached by a Confirmed join, step 8 of Section 5.4, or as joiner once Proven,
Section 5.7 step 4), and fewer
streams of the connection admitted this way are still awaiting their first
record than it carries such pipes (a writer has at most one lane that is
not yet confirmed per pipe, Section 5.8). Every other stream takes a slot
like any carrier. An admitted stream is classified as above: a lane is decided by
its first record (Section 5.8) within the first-record timeout, outside
the bounds throughout; a stream that turns out to be a join or a new
session takes a slot at that moment, as an unauthenticated carrier (refused
or evicting as Section 5.6 says), and goes on through Section 5.4 with both
timeouts.

### 5.6 Pending-join limits

An acceptor bounds the carriers it holds from arrival to Confirmed in Section
5.4, per listener, per session and per source (the streams a lane needs are
outside these bounds, Section 5.5). The per-listener bound covers every stage
before a carrier belongs to a session: from the moment the carrier is taken,
through any handshake or check that precedes its first record, the
classification and the verification of its Hello, and a carrier that the
reject policy stalls (secure-carrier/SPEC.md Section 9), until it is attached,
starts a session, or is closed. When the bound is full, the acceptor closes
its oldest stalled carrier to make room for a new one, and closes the new
carrier when none is stalled; stalled carriers take at most half of the
bound, and a carrier to be stalled beyond that is closed instead. It closes a carrier that has not delivered
its first record or Hello within a first-record timeout after the carrier is
established, or that has not reached Confirmed by the join deadline (Section
5.7), and closes a Hello longer than its limit before buffering it.

A failed join verification holds a slot only until its carrier is closed, so the
resources it consumes are bounded by the per-source and per-listener bounds above.
A listener MUST NOT stop verifying the joins of a session because of failures
attributed to that session. Authenticated joins are
bounded by the cap of Section 5.4 step 9; refused ones leave state only inside
the replay window of Section 4.3.

### 5.7 Joiner behaviour

1. The joiner takes the next pipe ID of its parity (Section 4.1) unless it
   already has max_pipes + H unretired attachments (Section 5.4 step 9). The ID is
   consumed at this moment, whatever then happens.
   After the peer refused a join (decision 1), or abandoned one of the joiner's
   attachments with ZMUX_PIPE_RESOURCE_LIMIT, it SHOULD NOT join again
   until another attachment that the acceptor had attached (not the
   refused or abandoned one) has been retired, or a backoff has elapsed: 1
   s after the first such refusal or abandonment, doubled after each
   consecutive one (refusals and abandonments count together), at most 60
   s, and reset by a successful join that the peer does not then abandon
   with ZMUX_PIPE_RESOURCE_LIMIT. An attachment the joiner itself abandons
   for its own limits does not start a backoff. Only a Reply it has verified can
   start this backoff; a closed carrier cannot.
2. It sends the Hello, with a first batch if it has one, as soon as every handshake
   of the carrier that Bound depends on has completed. The attachment enters JOINING;
   so does its inbound direction, if it has one. A joiner reads the carrier
   only to find the Reply, also on a direction it will not use afterwards.
3. While JOINING:
   - the joiner writes nothing on the carrier after the Hello: no record, no
     control frame, no lane, no PADDING. Its outbound direction is live but not
     usable (Section 11), and it never holds back failover to a
     standby pipe. The only stream data it may have carried is in the first batch;
   - nothing read from the carrier reaches the session. It reads with its own bound,
     not `max_record_size`: a longer unit fails only the attachment. Any first byte
     other than `03` is a QX_TRANSPORT_PARAMETERS record only if it begins as one, and
     one such record is consumed without being processed (a shared listener that
     sent its parameters before it classified the carrier, SPEC.md Section 13.2 rule 7):
     it is not counted in any position and is not acknowledged. Anything else, a
     failed Reply and a CONNECTION_CLOSE included, fails the attachment as a carrier
     failure (step 5) and is never a session error;
   - it does not become SUSPECT and takes no PIPE_PING, since it carries nothing and
     no stall clock runs until it is ACTIVE; its inbound direction is not listed in
     PIPE_ACK;
   - the joiner sends no PIPE_STATUS for the attachment: a request it changes
     meanwhile is sent, with its latest value, once the attachment is ACTIVE (step 4),
     on the attachment itself after its confirmation, or for a down attachment
     after the first record has arrived on it. The acceptor has no attachment state
     before Confirmed, so a PIPE_STATUS that overtook its confirmation on another
     pipe would be ignored (Section 9);
   - a removal of it, local or by a received PIPE_ABANDON, is abrupt, because a
     graceful one would need its carrier.
4. A verified Reply makes the joiner Proven. With decision 0 the attachment becomes
   ACTIVE (its inbound direction OPEN), it writes its confirmation at once and then
   its first record (Section 5.2). Stall clocks start here (Section 7.1). A QX_TRANSPORT_PARAMETERS frame on the
   pipe from now on is a PROTOCOL_VIOLATION (SPEC.md Section 5.1). With decision 1
   the attachment is retired at once without any PIPE_ABANDON, the carrier closed, and
   the backoff of step 1 begins.
5. If the carrier fails while JOINING, or the attachment is not ACTIVE by the join
   deadline below, it fails (Section 7.2) and its first batch and the rest of
   its information are sent elsewhere. The joiner sends PIPE_ABANDON(Pipe ID,
   ZMUX_PIPE_FAILED) on another pipe; the acceptor, which may have seen nothing of it,
   handles that by Section 8.1. No answer the carrier delivers before Proven
   changes the session's state; a suspended session learns that the acceptor is gone
   when its grace period ends.

**Join deadline.** A join's deadline is the join timeout, default 10 s, a timer of its
own: the handshake timeout bounds establishment only. It is at most the maximum join
deadline of 60 s, on which epoch retention rests (Section 10.7); a longer one is a
configuration error. Each end measures it alone:

| End | Starts | Ends |
|---|---|---|
| joiner | the attachment enters JOINING, before the Hello is written | Proven |
| acceptor | the first bytes are classified as a Hello and the carrier takes its slot of Section 5.6, so the first-record timeout comes before it | Confirmed |

Everything between the two counts, including a local delay of a handshake message and
the wait for establishment or the join keys (Section 5.4 step 4). The handshake of the
carrier itself does not count. A stalled carrier (Section 5.4) is closed as
secure-carrier/SPEC.md Section 9 says. The join timeout always has a value.

**Peer attachment.** A joiner reads its attachment as joined at the verified
Reply (step 4), before the acceptor has the confirmation and attachment
state (Section 5.4 steps 7 and 8). An attachment is **attached by the peer**
once the endpoint has evidence that the peer has attachment state for it:

- for an attachment the endpoint joined itself with an outbound direction: a
  PIPE_ACK entry for it, on any lane, acknowledging a position above 0 has
  been received;
- for an attachment the endpoint joined itself with only an inbound
  direction: at least one record has been received on it;
- for every other attachment (the bootstrap pair, and an attachment the
  endpoint accepted as acceptor): from its creation.

The acceptor acknowledges and writes records on an attachment only once it
is Confirmed (Section 5.4 step 8), so each piece of evidence shows that the
peer created its state. The state never reverts while the attachment
exists: a later failure or removal is handled by Sections 7 and 8. Until an
attachment is attached by the peer, the peer may still have none of its
state: an endpoint that removes its other pipes meanwhile may leave the
peer with no pipe of the session, which loses the session unless it
resumes (RESUME.md). An endpoint that replaces a pipe therefore removes the old one only after the replacement is
attached by the peer.

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

**Writer.** An endpoint MAY open a lane on an outbound pipe that is not Sealed
(Section 10.5) while lanes are
available toward the peer (Section 3.6) and its adapter declares that the new
stream is on the same connection as the pipe's lane 0, once a PIPE_ACK has acknowledged a position greater than 0
on the pipe's lane 0, so that the peer has attached the pipe before the lane
arrives. It opens a new stream on the connection of lane 0 and writes on it
one record that holds the PIPE_LANE, with the next lane number of the pipe
(lanes are numbered in increasing order from 1 and each number is used
once), and possibly PADDING, and nothing else until the lane is confirmed.
That record is a record of the pipe like any other: whatever the
endpoint applies to the pipe's records before handing them to the
carrier, size and timing included, applies to it.
PIPE_LANE is ack-eliciting, so the reader acknowledges the record within its
acknowledgement delay (Section 6.3); the lane is **confirmed** when a
PIPE_ACK acknowledges a position greater than 0 on it, and from then on it
takes frames as below. A pipe has at most one lane that is not yet
confirmed. A lane whose stream ends, is reset or fails before it is
confirmed, or that is not confirmed within the join timeout (the
writer then resets it), is **refused**: that is not a failure of the pipe,
nothing on it needs reinjecting (its PIPE_LANE record is outside the send
order, Section 2.5), a later acknowledgement for it is ignored, and the
writer opens no further lane on that pipe. The reverse half of a
bidirectional stream used as a lane only ever ends gracefully and is
ignored by the writer. A
confirmed lane stays open until its pipe ends (Section 8). A writer opens
lanes as it needs them. Each takes one stream of the connection: a lane
that waits for stream credit counts as the pipe's one lane not yet
confirmed, its join timeout runs only from the moment its stream is opened,
and it is dropped when the pipe ends.

**Frames by lane.** In a multipath session any frame may go on any confirmed
lane; the scheduler decides. In a session without
multipath (pipe acknowledgement, Section 3.5), whose stream data must arrive
in order (SPEC.md Section 7.3), the frames of a stream direction's sending
part (STREAM, RESET_STREAM, RESET_STREAM_AT, STREAM_DATA_BLOCKED) all go on
one lane, which the writer changes only once a PIPE_ACK covers every frame of
that sending part on the old lane (Section 6.2; "acknowledged" in the sense
of SPEC.md Section 7.4, a hand-off to the carrier, is not enough); every
other frame goes on lane 0, and a CONNECTION_CLOSE waits as SPEC.md Section
6.5 step 1 says.

**Reader.** A new inbound stream whose first record begins with PIPE_LANE is
handed over with the identity of its carrier connection (Section 5.5). The
reader looks the Lane Key up among its sessions, as a listener looks up a
Session Locator, comparing it in constant time:

1. If no session has that key, or the session has no attachment Pipe ID with
   a live inbound direction whose lane 0 is on the same connection as the
   new stream, or that direction is Sealed (a lane takes no handshake and would
   carry raw records past the pipe's protection), the reader aborts the new stream (it stops reading it: QUIC
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
Section 4.1). A reader reads all lanes of a pipe, none starving another.
PIPE_LANE is never retransmitted or
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
  (Pipe ID, Lane) is not greater than the previous entry's.
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
its adapter (Section 5.3) and that the acceptor has not attached yet.
PIPE_ACK is not ack-eliciting and is never retransmitted.

**Size.** Because a snapshot may be split, PIPE_ACK sets no limit on the
number of pipes.

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
2. no later than its own `zmux_max_ack_delay` after the first such byte,
   and immediately when such a byte arrives on an inbound pipe that has
   not been reported by a PIPE_ACK during the preceding
   `zmux_max_ack_delay` (or never has been);
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
waiting for any other condition.  An endpoint MAY acknowledge more often. It
SHOULD put a PIPE_ACK in the same batch as pending MAX_DATA or
MAX_STREAM_DATA updates.

These deadlines bind every local delay of the record that carries the
PIPE_ACK, a traffic-shaping delay included: whatever such a delay asks
for, a PIPE_ACK that rules 1 to 3 make due leaves no later than the
endpoint's own `zmux_max_ack_delay` after the first ack-eliciting byte it
reports that was not reported before.
An endpoint that wants to delay its acknowledgements longer advertises a
larger `zmux_max_ack_delay`.

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
suspect pipe, and to revalidate a pipe at the application's request. It is never retransmitted.

Because each PIPE_PING makes the peer send, PIPE_PING is chargeable (SPEC.md
Section 9.4, NE-2). An endpoint sends at most 8 PIPE_PING frames in total in
any interval of one second, which leaves room for the QX_PING requests
counted with it by SPEC.md Section 9.4.3 rule 1; when more pipes need a PIPE_PING
(probes, the first record of an accepted join, return-pipe liveness), it
serves those that have waited longest first, each as soon as the limit
allows.

An endpoint MAY add a PIPE_PING to a PIPE_ACK it sends on a pipe pair, so
that the exchange also shows its own outbound direction to work (the
answer to a probe, and in a busy session the PIPE_ACK on a return pipe
that carries nothing else, whose death would otherwise go unnoticed). It
does so only
for an outbound pipe that is **unproven**: during the preceding max(1 s,
T_suspect_p) no ack-eliciting byte was handed to its carrier, its
acknowledged position (for a pipe with lanes, its cumulative
acknowledgement point) did not advance, and it did not become ACTIVE.  In particular a PIPE_ACK that answers a PIPE_PING whose record also
acknowledged the PIPE_PING the pipe sent before carries none, however
long that exchange took.

### 6.5 Choice of pipe for PIPE_ACK and credit frames

Rule S1 of Section 11 says which outbound pipes may carry control frames.
Among them, PIPE_ACK and credit frames go first to a pipe that is known to
work: an endpoint **prefers ACTIVE pipes**, and a SUSPECT or DRAINING pipe
carries such a frame only as an additional copy, or when no ACTIVE outbound
pipe exists. Which ACTIVE pipe carries the primary copy is local.

- **Redundancy.** An endpoint MAY send copies of its latest snapshot and
  of its latest credit limits on further outbound pipes.
- **PIPE_PING answers on its own pipe pair.** The PIPE_ACK that answers a
  PIPE_PING under rule 3 of Section 6.3 SHOULD be sent on the outbound
  direction of the pipe pair (the same attachment) on which the PIPE_PING
  arrived, when that direction exists and is neither suspect nor failed, so
  that a probe measures its own path in both directions and never depends
  on another one.

These copies carry the same value on different pipes. They are an explicit
exception to the rule that limits are sent only when they increase (SPEC.md
Section 4.3) and are never chargeable (SPEC.md Section 9.4.1).

### 6.6 Estimates

For each outbound pipe p the sender keeps an RTT estimate (smoothed_rtt_p,
rttvar_p), sampled from PIPE_ACK entries as RFC 9002 Section 5 does, with the
entry's ACK Delay. Estimates are local; the peer sees only their effect on
timing. A new pipe never inherits another pipe's estimates: until its first
RTT sample its smoothed_rtt is 333 ms and its rttvar 166.5 ms (RFC 9002
Section 6.2.2).

---

## 7. Failure detection and reinjection

### 7.1 Suspect and failed pipes

The sender judges each outbound pipe p from acknowledgement progress and from
the progress of its carrier's writes; the receiver judges each inbound pipe
from the records that arrive on it (Section 7.3). The limits are local; this
section states the bounds a peer relies on.

    PTO_p = smoothed_rtt_p + max(4 x rttvar_p, 1 ms) + peer zmux_max_ack_delay   (RFC 9002 Section 6.2.1)

The pipe has an **ack stall** while it has unacknowledged ack-eliciting bytes
(stream data, control frames or an unanswered PIPE_PING) and its
acknowledged position (for a pipe with lanes, its cumulative acknowledgement
point, Section 2.5) does not advance. It has a **write stall** while bytes
are waiting to be written to it and its carrier makes no progress beyond
what its rate explains. Bytes wait for p when p would write them if its
carrier took bytes now: control frames, acknowledgements and probes due on
p, copies queued for p, and stream data the scheduler would place on p,
whether or not other frames are also due. Stream data the peer's flow
control holds back, data held back by a local rate limiter or a shaping
delay, and data the scheduler would place on another pipe do not wait for
p's carrier, so a pipe with nothing else to write has no write stall while
they wait.

- p becomes **suspect** when a stall has lasted longer than T_suspect_p,
  which is at least max(50 ms, 2 x PTO_p). A JOINING pipe has no stall
  (Section 5.7 step 3). A DRAINING pipe is only marked suspect and fails
  only as Section 8.2 says (drain deadline, carrier error, abrupt removal).
- p becomes **failed**:
  - when its carrier reports an error or an unexpected end, on any of its
    confirmed lanes (a lane that ends before it is confirmed is refused,
    Section 5.8), or the endpoint removes it abruptly;
  - while p has never been confirmed (Section 5.7), only when its join does
    not complete within the join timeout;
  - when a stall has lasted longer than T_fail_p, at least max(2 s, 8 x
    PTO_p) and never below T_suspect_p, while the endpoint has another
    outbound pipe in the ACTIVE state;
  - when a stall has lasted longer than T_dead_p, at least T_suspect_p and
    at least 1 s, while it has none, that is, when p is the **last pipe** of
    its direction (Section 11 uses "usable" in a narrower sense).

A peer that acknowledges as Section 6.3 requires, a shaped peer included,
therefore never makes a
working pipe suspect. Acknowledgements MUST NOT be withheld because the pipe
that would carry them is suspect (Section 11 rule S1), so ack suppression is
never what makes a pipe fail.

A pipe without unacknowledged ack-eliciting bytes cannot have an ack stall.
To detect the failure of an idle pipe, and to give the receiver the records
its inbound liveness check relies on (Section 7.3), the sender sends a
PIPE_PING on each outbound pipe on which no ack-eliciting byte has been
handed to the carrier for the pipe's **probe interval** k, measured from the
moment the pipe became ACTIVE if it never carried one. k is a local setting.
For an outbound pipe of an attachment on which the peer has no outbound
direction (an uplink or a downlink of a split carrier) k is at most 15 s, a
protocol constant that the peer's inbound silence rule (Section 7.3) relies
on, and these probes cannot be turned off. For the outbound direction of a
pipe pair, which only the sender judges, k is the sender's choice, and it may
turn these probes off. A probe reaches the carrier at most k after the
previous ack-eliciting byte of its pipe did.

Probes keep pipes and their carriers in use, not the session: they never
restart the idle timer or the keep-alive timer (SPEC.md Section 6.4). A
QX_PING that keep-alive sends is an ack-eliciting byte like any other and
restarts the probe interval of the pipe that carries it. A session without
multipath sends no probes (Section 3.5). An endpoint MAY probe more often,
within the PIPE_PING limit of Section 6.4.

### 7.2 Effects of suspicion and failure

When an outbound pipe becomes suspect, the sender:

1. stops scheduling new stream data on it. Control frames continue to use it
   as rule S1 of Section 11 allows;
2. reinjects (Section 7.4) the information of every ack-eliciting frame sent
   on it beyond its acknowledged position. Frames that were committed to the
   pipe but not yet handed to its adapter never left: they are taken back and
   scheduled again, and stream directions bound to the pipe are rebound, so streams spread over a stalled pipe move to
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
leave the receiver without a live inbound pipe, the receiver may wait longer. A JOINING inbound pipe is bounded
by the join timeout instead (Section 5.7). With resume in use, the
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
| SESSION_EPOCH | an offer: the same bytes until the answer arrives; an answer: the same bytes until it is acknowledged (Section 10.7) |
| PIPE_ACK, PIPE_PING, PADDING, QX_PING, DATAGRAM, QX_TRANSPORT_PARAMETERS, CONNECTION_CLOSE | nothing |

 Information sent on a pipe is reinjected from
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

A sender may re-send information beyond reinjection (Section 7.4); the
receiver handles every copy as a duplicate (Section 7.6). Such copies
include:

1. the copies of PIPE_ACK and credit frames of Section 6.5;
2. **opportunistic reinjection** (Section 7.5.1);
3. **redundant urgent data**: copies of the STREAM frames of urgent stream
   directions, and of the first data written on a pipe after an idle
   period, on one more pipe of the same tier, at most one copy per byte,
   with the copies whose data is not yet acknowledged bounded by the
   sender.

These copies are not reinjection in the sense of Section 7.4, and SPEC.md
Sections 4.3 and 9.4.3 allow them explicitly; the receiver handles them as duplicates (Section 7.6).

#### 7.5.1 Opportunistic reinjection

 When a stream has data waiting that the sender cannot send,
because of the peer's stream or session flow control or because the stream's binding
waits for its data on another pipe to be acknowledged, and the lowest unacknowledged
data that blocks it (the **blocking data**) was sent on a pipe p well beyond the
acknowledgement time it expected for that data, the sender MAY send that data
again once on another eligible pipe (Section 11).
- Each byte is re-sent opportunistically at most once. The copies are
  ordinary STREAM frames; the receiver handles them as duplicates, which are
  never chargeable (Section 7.6).
- The trigger is that blocked stream, never a timer.

### 7.6 Duplicates at the receiver

Duplicate STREAM data, repeated control frames and outdated limits are normal
in multipath sessions. They are not errors and are handled by the idempotence
rules (SPEC.md Section 4.1 rule 5). They are never chargeable: SPEC.md Section
9.4.1 exempts them in multipath sessions.

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

Rotating a carrier is a join followed, once the new pipe is usable, by this
graceful removal of the old one, the new pipe normally in the old one's fate
group; nothing on the wire marks it. An endpoint that removes attachment P gracefully:

1. stops scheduling new stream data and DATAGRAM frames on its outbound
   direction of P, which enters DRAINING. A DRAINING pipe never becomes
   ACTIVE or SUSPECT again, so a removal is never undone. Control frames may
   still use it as Section 11 rule S1 allows, as a last resort, until the
   **PIPE_ABANDON exchange** for P is complete, that is until the endpoint has
   both sent its PIPE_ABANDON for P (handed it to a carrier, on any pipe)
   and received the peer's; after that it carries only PIPE_ACK and
   CONNECTION_CLOSE. A DRAINING pipe that has an ack stall or
   write stall of T_suspect_P (Section 7.1) is marked suspect: it stays
   DRAINING, moves after every other live pipe in the choice of Section 6.5,
   and the information of the ack-eliciting frames sent on it beyond its
   acknowledged position is reinjected at once (Section 7.4), stream data
   included; if the pipe was only slow, the copies are harmless duplicates;
2. sends PIPE_ABANDON(P, NO_ERROR);
3. keeps processing acknowledgements. The **drain** of P completes at the
   first moment at which the PIPE_ABANDON exchange is complete and every byte
   of stream data it sent on P has been acknowledged; the endpoint checks
   this whenever either can change, that is when it sends its PIPE_ABANDON,
   when it receives the peer's and when an acknowledgement arrives. A pipe
   with no unacknowledged stream data therefore completes its drain as soon
   as the second PIPE_ABANDON of the exchange is sent or received. It then
   shuts down its write side of P gracefully (every lane at a record
   boundary) and P is CLOSED.  Information of other ack-eliciting frames sent on P that is
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
has not done so.

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
state is freed.  An endpoint
answers a peer's PIPE_ABANDON once: a copy that arrives again (reinjected by
the peer, or sent on several pipes) gets no second answer.

**Unilateral retirement.** A peer that never answers must not make the
endpoint keep the attachment forever. When the endpoint's own PIPE_ABANDON
for an attachment has been acknowledged and the peer's PIPE_ABANDON has not
arrived within

    max(2 s, 3 x PTO)

after that acknowledgement, where PTO is the largest PTO_p (Section 7.1) of
the endpoint's ACTIVE outbound pipes, or of its live outbound pipes when none
is ACTIVE, or the PTO of the initial values (Section 6.6) when it has no live
outbound pipe, the endpoint closes whatever carrier of the attachment is
still open and retires the attachment anyway.
While the endpoint is SUSPENDED its PIPE_ABANDON cannot be acknowledged;
the cap of Section 5.4 step 9 then displaces an attachment when a rejoin
needs the room: a failed one, preferably the oldest whose own PIPE_ABANDON
has been acknowledged, otherwise the oldest whose own PIPE_ABANDON has not;
only when none has failed, a live one that cannot serve the missing
direction.

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
or closes the session.

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
  outbound direction the one in the Hello (Section 5.1), otherwise
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
    request in the Hello and sends PIPE_STATUS only as Section 5.7 step 3 says.

**Effective status and priority of an outbound direction.** For each of the
two fields separately, an explicit local setting wins, then the latest
PIPE_STATUS request of the peer for that attachment (the one in the Hello
until a PIPE_STATUS with a sequence number of at least 1 arrives), then the
default (available, priority 0). An endpoint SHOULD honour the peer's request
when it has no local setting. Both the local
setting and the request can change at any time; the sender uses the
effective values in force at each decision.

**Tiers.** The non-standby outbound pipes of a direction with the same
effective priority form a **tier**; lower priorities are the more-preferred
tiers, and standby pipes come after every tier.

**Joining with a priority.** A joiner states the initial status and
priority of the acceptor's outbound direction of a new attachment in the
Hello itself (Section 5.1), so the request is
authenticated with the join and in force from the first byte the acceptor
sends; no request can arrive late and misroute data. An acceptor with a
non-default request for the joiner's outbound direction puts its PIPE_STATUS
in its first record on the attachment (with the PIPE_PING of Section 5.4
step 8), or sends it on another outbound pipe when the attachment gives it
none. Until it arrives the joiner's outbound direction is at the default.

**Standby.** A standby pipe carries new stream data only on failover, when no
non-standby pipe in its direction is usable (Section 11); it is never used
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
returning or new pipe means once the sender treats it as usable, an endpoint that sent such a
takeover request MUST send a PIPE_STATUS restoring its configured request
(Status standby, the same Priority).

---

## 10. Join security

The Join handshake is specified in secure-carrier/SPEC.md Section 11. This section
says where its key comes from and what a session may claim about its protection.

### 10.1 Secret source

The source of a session's secret is fixed by local configuration and is never
derived from what the peer offers:

- **E** is set when the first pipe uses the secure carrier (secure-carrier/SPEC.md),
  or when the application configured the exporter of pipe 0's innermost end-to-end
  handshake;
- **P** is set when the application supplied a PSK or will supply one after
  establishment (a deferred PSK, Section 10.2).

Both enter the session secret when both are set. An endpoint whose first pipe uses
the secure carrier sets E alone. There is no way to make a session secret without
either: a secret from empty input keying material does not exist, because anyone who
saw the bootstrap parameters could compute it.

Secret Flags in `zmux_multipath` state this choice; they do not negotiate it, and they
travel in plaintext wherever the bootstrap carrier does. After both parameters are
known:

1. An endpoint whose flags are 0, or whose peer's flags are 0, has **no source**:
   multipath is not in use (Section 3.3). An endpoint whose first pipe uses the secure
   carrier instead closes the session with TRANSPORT_PARAMETER_ERROR.
2. Flags that are both non-zero and differ close the session with
   TRANSPORT_PARAMETER_ERROR. An endpoint MUST NOT compute a secret from the
   intersection of the flags: an on-path party could then clear a flag and take that
   source out of the secret.
3. Equal flags select those sources.

An implementation never sets E on its own. When handshakes are nested, the innermost one that terminates at both
endpoints is used; a handshake below it may be terminated by an intermediary. That
handshake is the secure carrier's, TLS 1.3 or QUIC terminated by the endpoint's own
process (a TLS connection, or a QUIC connection whose stream is the carrier, RFC 9001
Section 4), or the handshake of a carrier layer that runs its own authenticated key
exchange end to end with the peer and exports keying material in the same way. An
endpoint never sets E from the exporter of a connection below a stream of a multiplexer
running over it (a stream of another zmux session, EXTENSIONS.md Section 10.2, an HTTP/2
stream through a proxy).

**Unmanaged first pipe.** A first pipe that does not use the secure carrier is
**Unmanaged**: nothing in the protocol proves that pipe 0 ends at the peer, and it counts
as the weakest protection (Section 10.5). Joining on such a session is off by default.
Its host proves the pipe end to end, for example with a pinned certificate or an
attestation over an exporter of its own label, and fails the session if the proof fails,
and only then declares it end to end, a local act that
sends nothing. Until an endpoint has declared, it has no join keys: it neither starts
nor accepts a join (Section 3.3).

### 10.2 Inputs

- **Exporter value**. On the secure carrier, `Export("EXPORTER-zmux-session", "", 32)`
  of secure-carrier/SPEC.md Section 8.2, 32 bytes. Otherwise Exporter("EXPORTER-zmux-session",
  "", 32) of the innermost end-to-end
  handshake (Section 10.1) of the carrier of pipe 0
  up, with the interface of [RFC8446] Section 7.5; both endpoints export
  from that same carrier, which is the initiator's outbound and the
  responder's inbound carrier. A carrier that is a stream of a connection
  (a QUIC stream) exports from that connection's TLS handshake. An
  endpoint reads the value once, no later than when it processes the peer's
  transport parameters. The handshake of every carrier that contributes to
  the value must have completed by then; parameters arriving through one
  half of a split bootstrap or in early data do not imply that. When the
  peer's parameters arrive as carrier early data before the carrier
  handshake gives the exporter, the receiver holds them until the handshake
  completes; only the reading of that carrier waits, and a failed handshake
  is a carrier failure. An endpoint whose read otherwise fails when the
  exporter is selected closes the session with INTERNAL_ERROR. For a split bootstrap (SPEC.md Section 6.1.1) over two
  connections, the exporter value is the 64-byte concatenation of the
  exporters of the uplink carrier and the downlink carrier, in that order,
  so that the session secret binds both carriers of the pair. When both
  halves share one connection (equal exporters) the value is that single
  32-byte exporter. When only one half exposes an exporter the value is that
  half's 32 bytes, and the session secret binds that half alone.
- **PSK**: at least 16 bytes supplied by the application, identical at both
  endpoints and unpredictable to third parties.  It may be static or specific to the session. The application may supply the PSK after
  establishment (deferred), for example once it authenticated the peer
  over the session; P is sent from the start, the flags do not change, and
  the endpoint has no join keys until then (Section 3.3). The peer cannot
  tell a deferred PSK from a static one: one end may defer while the other
  has the same bytes configured. When the exporter is also selected, its
  value is read as always, when the peer's parameters arrive, and kept
  until the PSK enters the session secret with it (Section 10.3).

### 10.3 Session secret

    salt           = "zmux session" || initiator Session Nonce || responder Session Nonce
    IKM            = exporter value (or empty) || PSK (or empty)
    session_secret = HKDF-Extract(salt, IKM)                      [RFC5869], SHA-256

SHA-256 is the only hash of the session layer: the session secret and every epoch secret
(Section 10.7) are 32 bytes, whatever the Suites and Profiles in use.

IKM is never empty. The exporter value is 32 bytes, or 64 for a split bootstrap over two
connections with both exporters (Section 10.2). Both endpoints derive the
same length from the same carriers, and the flags fix whether a PSK follows,
so the concatenation is unambiguous between endpoints of one session.

The **session binding** value identifies the session to the application
and is no secret:

    session_binding = HKDF-Extract(salt = "zmux session binding",
                                   IKM = initiator Session Nonce || responder Session Nonce)   SHA-256, 32 bytes

It enters no key derivation of this specification.

### 10.4 Keys

An endpoint derives `join_psk[e]_i` and `join_psk[e]_r` of each usable Suite as
secure-carrier/SPEC.md Section 11.4 says, with `session_crypto` of Section 3.7, from the
secret of each epoch e that it accepts (Section 10.7), and keeps them as long as it keeps
that epoch, so that a join works after pipe 0 has gone.

### 10.5 Record modes and session protection

Each carrier of a session has a record mode, fixed by its handshake:

| Mode | Records | What it proves |
|---|---|---|
| Sealed | protected by the Join's or Establish's keys (secure-carrier/SPEC.md Section 7.1) | membership, and integrity and confidentiality against whoever terminates the carrier |
| Bound | raw zmux records; the carrier protects them | membership on this carrier: a third party that terminates or splices the carrier makes the handshake fail |
| Trusted | raw zmux records; the deployment protects them | membership at handshake time only: anyone on the path can read all records and relay online |

- The Bound carrier contract is secure-carrier/SPEC.md Section 7.2. A stream of a
  connection qualifies only when the connection's own record layer protects every byte
  of every stream end to end between the two endpoints, as QUIC's does; several pipes on
  one QUIC connection share one binding, and those one endpoint joins SHOULD be in one
  non-zero fate group (Section 2.2). The value is `Binding` of secure-carrier/SPEC.md
  Section 8.3.
- The joiner's host chooses a carrier's mode. No end chooses for it and no failure
  changes it: a host that wants another mode opens another carrier. Trusted is an explicit
  configuration of both ends. An acceptor accepts only Sealed unless configured otherwise,
  and refuses by Section 5.4 step 7.
- A first pipe in the secure carrier has its mode from Establish. An Unmanaged first pipe
  (Section 10.1) has none of these.
- A session's protection is that of its weakest pipe, the order being Sealed, then
  Bound, then Trusted, then Unmanaged: scheduling and reinjection can place any stream on
  any pipe, and a Trusted path party can inject frames on its pipe, including a PIPE_ACK
  about a Sealed one. There is no scheduling per mode. An endpoint exposes this weakest
  mode so that a host can authorize by it.
- The first pipe's mode counts for the session's whole life, after that pipe has retired
  too: an Unmanaged session stays Unmanaged, because nothing proved who was at the far end of
  the pipe that made the session, and retiring that pipe neither adds the proof nor undoes
  what it carried. A joined pipe stops counting once it is retired.

### 10.6 Properties

- **Mutual proof.** A join proves the joiner to the acceptor with the Hello and the
  acceptor to the joiner with the Reply. Nothing from the carrier reaches the session of
  a joiner before the Reply passes (Section 5.7 step 3), so a party that terminates the
  carrier cannot read buffered data, forge an acknowledgement or end a session unless
  the mode allows it (Section 10.5).
- **One-time use.** The Hello covers the pipe ID, and each pipe ID is accepted at most
  once (Section 5.4 step 6, with the bounded replay window of Section 4.3). A captured
  Hello cannot join a different pipe ID, direction, fate group, priority request or
  session.
- **No state before verification.** Section 5.4 steps 1 to 6.
- **Replay.** A replayed Hello, including requests that CDNs or HTTP/2 GOAWAY replay, is
  closed at step 6, or if it arrives first it takes the Pipe ID and the original fails as
  if lost. Whoever replays one can neither read the Reply nor make a confirmation, so it
  never reaches step 8: nothing it does moves a pipe, a cap or a suspension. A replayed
  batch is deduplicated by stream ID and offset.
- **Reflection.** Keys are per joiner role.
- **Pending bounds.** Per source, never per session (Section 5.6).
- **Uniform refusal.** Every failure before a join authenticates gets the response of
  the reject policy (secure-carrier/SPEC.md Section 9), which covers the bytes sent and
  the way the carrier ends, not processing time; a
  refusal of an authenticated join reaches only the session's own peer, under the Reply's
  key.
- **Locators.** A locator identifies a session to anyone who sees a Hello, since it is in
  its plaintext head, and links the carriers joined with it, that is those of one epoch
  and of the exchange that ends it (Section 10.7). An acceptor whose reject policy closes
  unknown Locators at once makes a Locator guessable from timing unless at least 8 random
  bytes follow its instance identifier in every locator, a new one of an epoch included;
  it puts them there.
- **Forward secrecy.** Every join runs a key exchange, so its record keys and exporter
  have forward secrecy, while its first batch and return frames do not: the consequences
  of a leaked epoch secret are those of secure-carrier/SPEC.md Section 11.5.

### 10.7 Session epochs

A session's secret evolves in epochs. The secret of epoch 0 is the session secret
(Section 10.3); the secret of each later epoch comes from a key exchange in SESSION_EPOCH
frames. The join keys of epoch e derive from `epoch_secret[e]` (Section 10.4), and a Hello
carries the key phase e mod 2 (secure-carrier/SPEC.md Section 11.2).

```text
SESSION_EPOCH Frame {
  Type (i) = 0x024e93eb,
  Phase (8),
  Epoch (i),
  [Share Length (i), Share (..)],
  [Locator Length (8), Locator (..)],
}
```

- **Phase**: 0 offer, 1 answer. Another value is a PROTOCOL_VIOLATION.
- **Epoch**: the target epoch of the exchange.
- **Share**: the share of the usable Group of highest priority (secure-carrier/SPEC.md
  Section 11.6): the initiator share in an offer, the responder share in an answer. Another
  length, or a share refused as that section says (`kex`), is a PROTOCOL_VIOLATION.
- **Locator**: the sender's Session Locator for the target epoch, 1 to 255 bytes, or
  length 0 to keep its current one.

The frame is available when multipath is in use and the endpoint has the session secret.
An endpoint sends and accepts it only on a Sealed or Bound pipe (Section 10.5); the frame
on any other pipe is a PROTOCOL_VIOLATION. An endpoint that does not have the session
secret yet (join pending, Section 3.3) ignores the frame.

**Derivation.** With `offer_body` and `answer_body` the bytes of the two frames from Phase
through Locator, and the shared secrets of the exchange:

    prk[e+1]          = HKDF-Expand(epoch_secret[e], "zmux epoch" || varint(e+1)
                          || SHA-256(session_crypto) || SHA-256(offer_body || answer_body), 32)
    epoch_secret[e+1] = Combine(prk[e+1], ss_1, ..., ss_n)

Both use SHA-256; `session_crypto` is that of Section 3.7 and `Combine` that of
secure-carrier/SPEC.md Section 11.6.

**Retransmission.** O, the end that sent the offer, repeats it on each new pipe and once
per probe interval of each pipe (Section 7.1) until the answer arrives. A, the other end,
repeats its answer the same way until a PIPE_ACK acknowledges a record that carried it. A
repeated offer or answer MUST be the same bytes as the first, since both bodies enter the
derivation: an endpoint keeps the encoding of its offer or answer. The frame is
ack-eliciting.

**States.** `d` is the epoch an endpoint last completed; it is Stable(d) when it accepts d
alone, and an exchange targets d+1. `R` is 3 x the maximum join deadline (Section 14).

    O: Stable(d) --send offer(d+1)--> Offering
       --recv answer(d+1)--> commit d+1; new joins use d+1; accept {d, d+1}; start timer R
    A: Stable(d) --recv offer(d+1)--> commit d+1; send answer(d+1); accept {d, d+1};
          new joins still use d
       --first of: a record carrying the answer is acknowledged, or a join of phase
          (d+1) mod 2 from the peer verifies--> new joins use d+1; start timer R
    both: --timer R expires--> delete d --> Stable(d+1)

To commit an epoch is to derive its secret, which needs the peer's share; an endpoint
accepts the epochs listed and joins with the epoch stated. An endpoint accepts its new
Locator from the commit of the epoch that announced it, and its old one stops being
accepted when the earlier epoch is deleted. A joiner that joins with epoch d+1 uses the
Locator the peer announced for d+1, if any. Rules:

1. An endpoint sends an offer only in Stable. Only Stable(d) answers offer(d+1).
2. A frame whose Epoch is at most d is ignored.
3. A frame of Epoch d+1 of a phase not later than the last one the endpoint has processed
   or sent is a copy and is ignored.
4. Collision: the session's initiator ignores offer(d+1) while it is Offering or has
   committed d+1. The session's responder that is Offering and receives offer(d+1)
   discards its private values and its offer, and processes the offer as Stable(d).
5. An endpoint that still accepts d and receives offer(d+2) first ends its exchange: it uses
   d+1, deletes d, and then processes the offer as Stable(d+1). An offer(d+2) shows that the
   peer has committed d+1, so switching and deleting d on it is safe.
6. Every other frame is a PROTOCOL_VIOLATION: an Epoch of at least d+3, an Epoch of d+2
   other than an offer in rule 5, or an answer(d+1) at Stable(d).
7. Deleting an epoch deletes its secret, its join keys and its Locators. Carriers already
   joined keep their record keys. A join of d that has not ended when d is deleted fails as
   any join does, and the host may retry it.

When the acknowledgement of an answer arrives more than R late, A's joins of d fail as
any failed join. An endpoint has at most one exchange, one set of private values, two epoch secrets
and two local Locators at a time. The state of an exchange is part of the session's state,
and losing it loses the session (RESUME.md Section 10).

Advancing is local policy. A session with one pipe and no resumption has no joins, and MAY
never advance its epoch.

---

## 11. Scheduling

The scheduler decides, whenever an outbound pipe can accept bytes, which
frames to put on it. The receiver cannot observe the policy, so policies are
local; these rules are normative for every multipath sender, and an endpoint
that schedules differently within them interoperates. This document uses
these terms: a pipe is **usable** when it is ACTIVE and the sender's own
policy lets it take new stream data at all (a sender may hold back a pipe that
just joined or returned, or on which data was reinjected opportunistically, Section 7.5.1, while it has
another); it is **eligible** when, among the usable pipes, the sender's tier
gating lets it take the next chunk: pipes of a less-preferred tier (Section
9) take new stream data only what the more-preferred tiers cannot absorb.

- S1. What each outbound pipe state may carry:

  | State | Frames |
  |---|---|
  | ACTIVE | every frame |
  | JOINING | no record. While no outbound pipe is usable (standby pipes included), stream data in the Hello's first batch (Section 5.2), reinjected information first (Section 7.4). A JOINING pipe is not usable until it is ACTIVE (Section 5.7 step 4) |
  | SUSPECT | every frame except STREAM frames with new stream data (bytes or a FIN not sent before), reinjected information and DATAGRAM frames |
  | DRAINING | as SUSPECT until the PIPE_ABANDON exchange for the attachment is complete (Section 8.2); afterwards only PIPE_ACK and CONNECTION_CLOSE |
  | FAILED, CLOSED | nothing |

  SUSPECT and DRAINING pipes are a last resort: an endpoint puts a frame on
  one only when it has no ACTIVE outbound pipe, or when the frame
  concerns that pipe (its PIPE_PING, the PIPE_ABANDON of its own
  attachment), or as the additional copies of PIPE_ACK and credit frames
  that Section 6.5 describes. A frame sent there is not reinjection. Which
  ACTIVE pipes may take new stream data is further restricted by
  tier gating (S2).
- S2. Tier gating comes first: new stream data, reinjected information,
  opportunistic copies and redundant copies (Section 7.5) go only to the
  eligible pipes (apart from the first batch of S1 while no pipe is usable),
  and the policy chooses among them. A standby pipe takes new stream data
  only on failover, when no non-standby pipe of its direction is usable; it
  is never used for overflow.
- S4. Flow control, the sender's bound on retained data and the preamble
  rule (EXTENSIONS.md Section 4.2) apply to every policy; a FIN-only STREAM
  frame is held back neither by flow control nor by that bound (SPEC.md
  Section 8.2).
- S5. Retransmission only as Section 7 allows.
- S6. PIPE_ACK placement as Section 6.5.
- S7. A stream's DATAGRAM frames follow its
  unacknowledged opening frame (EXTENSIONS.md Section 6.4), even onto a
  less-preferred tier for about a round trip, and are never reinjected.

## 12. Receiver requirements for spread streams

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

## 13. Interactions

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

## 14. Defaults

| Item | Default | Reference |
|---|---|---|
| zmux_ack_threshold | 65536 bytes | 3.2 |
| zmux_max_ack_delay | 25 ms | 3.2 |
| pipe acknowledgement without multipath | `zmux_pipe_ack` sent in every session; PIPE_ACK on pipe 0 by rules 1 and 2 of 6.3, nothing retained | 3.5 |
| lanes accepted per inbound pipe | absent `zmux_max_lanes` means 1 (no lanes); any value allowed | 3.6 |
| replay window W | max(64, 2 x (max_pipes + H)) pipe IDs of the peer's parity below and above the highest verified join; at least max_pipes + H | 4.3 |
| first batch | at most 8 KiB | 5.2 |
| attachment headroom H | 8 | 5.4 |
| unretired attachments of the peer | at most max_pipes + H, the bootstrap pair included; beyond that joins are refused, or, while suspended, one attachment is displaced | 5.4 |
| join timeout | 10 s; at most the maximum join deadline | 5.7 |
| maximum join deadline | 60 s: no join deadline is longer | 5.7, 10.7 |
| epoch retention R | 3 x the maximum join deadline (180 s) after an endpoint starts using the new epoch | 10.7 |
| join refusal backoff | 1 s after the peer's ZMUX_PIPE_RESOURCE_LIMIT refusal or abandonment, doubled per consecutive one, at most 60 s | 5.7 |
| PIPE_PING | at most 8 per session in any one-second interval | 6.4 |

| initial RTT of a pipe | 333 ms (rttvar 166.5 ms) until its own first sample | 6.6 |
| T_suspect | local; at least max(50 ms, 2 x PTO_p) | 7.1 |
| T_fail (another pipe ACTIVE) | local; at least max(2 s, 8 x PTO_p) and T_suspect | 7.1 |
| last-pipe stall limit T_dead | local; at least T_suspect and 1 s | 7.1 |
| idle-pipe probe interval | at most 15 s for attachments without an outbound direction at the peer; local on pipe pairs | 7.1 |
| inbound silence limit | at least probe interval + n_in / 8 s + max(2 s, 2 x srtt_max, G_p - probe interval) | 7.3 |
| unilateral retirement | max(2 s, 3 x PTO) after the own PIPE_ABANDON is acknowledged | 8.4 |
| pipe priority | 0 (any value 0 to 2^62 - 1, lower preferred), status available, per direction | 9 |
---

## 15. Security considerations

- Join authentication: Section 10.
- Unauthenticated carriers can hold listener resources only within Section
  5.6 limits and only until the handshake timeout, or for a join the join
  timeout.
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
  control limits of data and its bound on retained data.
- A lane takes no handshake. A reader refuses a lane on a Sealed pipe, and a writer opens
  one only when its adapter declares the new stream on the connection of lane 0 and the
  pipe is not Sealed. A lane is accepted only with the receiver's Lane Key, which travels
  only in the session's own records (its transport parameters and its lanes),
  and only on the connection of its pipe's lane 0 (Section 5.8).  A
  peer can make the endpoint keep at most L - 1 lanes per inbound pipe,
  their incomplete records within the session's budget (SPEC.md Section
  9.8).
- Carriers joined before an epoch keep the record keys they were joined with, so a leak of
  an endpoint's memory is undone for them only when they are replaced by new joins. An
  epoch exchange on a Bound pipe has the confidentiality of Group 2 and only the integrity
  of that carrier's own handshake.
- Spreading exposes per-pipe traffic volumes to observers of each carrier.
- A CDN or other intermediary that terminates the carrier's TLS sees the
  pipe's content and locators; end-to-end protection then needs
  Sealed carriers (secure-carrier/SPEC.md) or application-level encryption. A session is as
  protected as its weakest pipe (Section 10.5).
