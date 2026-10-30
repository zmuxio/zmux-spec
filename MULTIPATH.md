# zmux Multipath

zmux multipath is part of the zmux core protocol (SPEC.md Section 11):
every conforming implementation implements this document, and a session
uses it when both endpoints negotiate it (Section 3).

---

## 1. Introduction

### 1.1 Overview

A multipath session runs over a changing set of **pipes**: unidirectional,
reliable, ordered byte streams provided by carriers such as TCP or TLS
connections (a pipe pair), WebSockets, HTTP request bodies (uplink only),
HTTP response bodies (downlink only) or bidirectional QUIC streams (a pipe
pair). Pipes are added and
removed at any time.

Seen from the session, the set of pipes is a carrier that can reorder
records across pipes and loses records only when a pipe breaks. The session
therefore applies QUIC's stream semantics unchanged and replaces QUIC's
packet-level machinery with pipe-level machinery:

| QUIC mechanism | zmux multipath |
|---|---|
| packet numbers | implicit byte positions per pipe, or per lane of a pipe (no field on the wire) |
| ACK frames with ranges | PIPE_ACK: one cumulative position per inbound pipe or lane, sent on any reverse pipe |
| loss detection timers, PTO | none; loss happens only when a pipe fails, detected from acknowledgement progress |
| retransmission of information (RFC 9000 Section 13.3) | unchanged, applied to the unacknowledged part of a failed or suspect pipe |
| congestion control | none; each carrier does its own |
| path validation, connection IDs | authenticated PIPE_JOIN |
| connection migration | adding a pipe and removing another |

Every stream's data may be spread frame by frame over all pipes in each
direction, and receivers MUST handle any interleaving (Section 13). A zmux
session without multipath uses only the acknowledgement of Section 6 on its
single pipe pair (Section 3.5).

### 1.2 Conventions

The conventions and terminology of SPEC.md Sections 1.3 and 1.4 apply.

---

## 2. Model

### 2.1 Pipes, pipe pairs and directions

- A **pipe ID** names one attachment of a carrier to the session. The
  attachment provides one pipe (an uplink or a downlink) or a pipe pair (a
  bidirectional carrier). A pipe ID is used for exactly one attachment in
  the life of a session.
- The endpoint that creates the attachment is its **joiner**; the other is
  its **acceptor**. The joiner of pipe ID 0 (the bootstrap pipes) is the
  initiator.
- Directions are named from the joiner: **up** is joiner to acceptor,
  **down** is acceptor to joiner, **bidi** is both.
- On the wire a pipe is identified by its pipe ID and the direction implied
  by the frame: PIPE_ACK and PIPE_STATUS refer to pipes inbound to the
  frame's sender; PIPE_ABANDON refers to the whole attachment.

### 2.2 Fate groups

Each attachment carries a **fate group** chosen by its joiner; 0 means
independent. Attachments of the same joiner with the same non-zero fate
group are expected to fail together (for example an HTTP POST uplink and an
HTTP GET downlink on one HTTP/2 connection). A pipe pair is always one fate
unit. Fate groups never change wire semantics.

Fate group numbers are scoped to the joiner: attachments joined by
different endpoints never share a fate group. Pipes that are streams of one
carrier connection fail together; those that one endpoint joins SHOULD be
in one non-zero fate group of that endpoint. To spread one pipe over
several streams of its connection, an endpoint uses lanes (Section 2.5),
not several pipes.

### 2.3 Positions

The **position** of a pipe, or of each lane of a pipe that has lanes, is a
byte count from the first byte of its record stream (after any out-of-band
join information, and after a leading QX_TRANSPORT_PARAMETERS record that
the joiner discards, Section 5.7 step 3). The sender's position is the
number of bytes passed to the carrier. The receiver's position is the
number of bytes in records that it has completely received and processed
(Section 6.1). On message-framed carriers each message counts as the bytes
of its byte-stream form (SPEC.md Section 3.4).

### 2.4 Carriers

A pipe is a byte source or a byte sink with an end, a graceful shutdown
and an abort. Optional carrier capabilities are a TLS exporter (Section
10.2), the carrier's unsent-byte count and RTT, out-of-band join delivery
(Section 5.3) and lanes (Section 2.5). Any carrier that delivers bytes
reliably and in order can be attached, including a stream of another zmux
session (EXTENSIONS.md Section 10).

Carriers may detect a dead path themselves (idle timeouts, keep-alives):

- A carrier that decides its connection is dead MUST be reported to the
  endpoint at once, as a read or write error or an unexpected end; the
  endpoint then fails the pipe at once (Section 7.1).
- A carrier idle timeout MUST NOT be configured shorter than the interval
  at which the endpoint probes the idle pipe (15 s for an attachment without
  an outbound direction at the peer, Section 7.1) plus a round trip: on an
  idle pipe the PIPE_PING probes are the only traffic. An idle pipe pair
  that is not probed lives as long as its carrier's idle timeout allows.
- While other pipes are ACTIVE, zmux's own detection governs a pipe whose
  carrier is silent but has not reported an error. A carrier's own
  loss-recovery state is not an input to that detection.

### 2.5 Lanes

A pipe whose carrier is one stream of a **multi-stream carrier connection**
(a QUIC connection, HTTP/3 or WebTransport streams on one, an SCTP
association, an HTTP/2 connection, a zmux session carrying a nested
session's pipes) can be carried over several streams of that connection.
Each is a **lane** of the pipe:

- Lane 0 is the carrier stream on which the pipe started: the one that
  carried its PIPE_JOIN or, for an out-of-band join, its first record, and
  for the bootstrap pipe the bootstrap carrier. Lanes 1, 2, ... are streams
  of the same connection that the pipe's writer opens later, each starting
  with a PIPE_LANE frame (Section 5.8). A lane belongs to one direction of
  one attachment.
- Each lane is a record stream of its own, with its own position (Section
  2.3) and its own PIPE_ACK entries (Section 6.2); no record crosses lanes.
  There is no order between the lanes of a pipe, as there is none between
  pipes (SPEC.md Section 4.1 rule 6).
- Everything else belongs to the pipe: attachment, pipe ID, state
  (STATE_MACHINE.md Section 3), PIPE_STATUS and failure detection. A lane
  is not an attachment: it has no PIPE_JOIN and no token, does not count
  toward max_pipes + H (Section 5.4 step 9) and never touches the replay
  window (Section 4.3). It is accepted only with the receiver's Lane Key and
  only on the carrier connection of its pipe's lane 0 (Section 5.8).
- A lane is **confirmed** once a PIPE_ACK acknowledges a position greater
  than 0 on it; before that it carries only its PIPE_LANE, and a lane that
  ends before it is confirmed is **refused**, which costs the pipe nothing.
  The confirmed lanes of a pipe live and fail together: the pipe ends
  gracefully when every lane has ended gracefully at a record boundary
  (Section 7.3), and an error, a reset or an unexpected end of any
  confirmed lane fails the pipe (Section 7.1). A confirmed lane is never
  closed on its own.
- **Cumulative acknowledgement point.** The sender numbers the bytes it
  hands to the lanes of a pipe in the order it hands them over, across
  lanes, excluding each lane's PIPE_LANE record (its **send order**, which
  is local). The pipe's cumulative acknowledgement point is the send-order
  number of its oldest unacknowledged byte, or of the next byte when all are
  acknowledged; without lanes it is the acknowledged position. Wherever
  this document refers to a pipe's acknowledged position as a whole
  (Section 7.1; the last progress of Section 5.4 step 9), a pipe with lanes
  uses this point, and bytes sent are counted in send order. A frame is
  acknowledged, and is "beyond the acknowledged position" for reinjection
  (Sections 7.2, 7.4 and 8.2), by the position of the lane that carried it.
- **Carrier support.** Lanes are an optional carrier capability: opening a
  stream on the connection of a pipe's lane 0, and handing over each new
  inbound stream with the identity of its connection. A pipe pair on a QUIC
  connection (and on HTTP/3 or WebTransport over one), pipe 0 included, is
  always one bidirectional stream, whose two halves are lane 0 of the two
  directions; it is never split into a unidirectional stream per direction,
  and a pipe joined in one direction only is a bidirectional stream too,
  its reverse half unused. A further lane is a unidirectional stream on a
  QUIC connection and on WebTransport; on carriers whose further streams
  are bidirectional it is a bidirectional stream whose reverse half is
  unused: the reader ends that half at once, and its end is no event of the
  pipe. On a connection whose streams
  share one ordered byte stream (HTTP/2 over TCP) lanes recover nothing,
  and an endpoint SHOULD NOT open them there by default. A connection that
  an intermediary splits does not reach the peer as one connection, so
  lanes opened on it are refused (Section 5.8).

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
  10.1): 0x01 E (TLS exporter of the bootstrap carrier), 0x04 P (pre-shared
  key), 0x08 I (insecure join allowed). Other bits, 0x02 included, are
  reserved; senders set them to 0 and receivers ignore them.
- **Session Nonce**: 16 bytes chosen uniformly at random for this session.
- **Session Locator**: 0 to 16 bytes that the peer puts into PIPE_JOIN to
  reach this session through this endpoint's listener; its content is
  chosen by the endpoint. Length 0 means that this endpoint does not accept
  joins. A Locator Length above 16, or a value whose length disagrees with
  Locator Length, is a TRANSPORT_PARAMETER_ERROR.

### 3.2 Companion parameters

| Parameter | ID | Value | Absent means | Range |
|---|---|---|---|---|
| zmux_ack_threshold | 0x2762c7e5 | bytes: the sender of the parameter acknowledges no later than this many ack-eliciting bytes on any pipe | 65536 | at least 1024 |
| zmux_max_ack_delay | 0x17fa1ed6 | milliseconds: the sender of the parameter acknowledges no later than this after receiving an ack-eliciting byte | 25 | below 2^14 (as QUIC's max_ack_delay, RFC 9000 Section 18.2) |

These parameters MUST NOT be sent without `zmux_multipath` or
`zmux_pipe_ack` (SPEC.md Section 5.5). A value out of range is a
TRANSPORT_PARAMETER_ERROR. QUIC's `max_ack_delay` (0x0b) remains
prohibited.

### 3.3 Enablement

Multipath is in use when both endpoints sent `zmux_multipath` and the
secret selection of Section 10.1 yields a source. If both sent the
parameter but no source is selected, multipath is not in use; both
endpoints know this from the two values, and implementations report "join
security unavailable" locally. Whether to send `zmux_multipath` is a
configuration choice.

### 3.4 What changes in a multipath session

Only in multipath sessions:

1. STREAM data may arrive out of order and duplicated (SPEC.md Section 7.3).
2. A pipe failure is not a session failure (SPEC.md Section 6.6); the loss
   of the last pipe in a direction is handled by Section 8.5.
3. The idle and keep-alive timers restart only on application activity
   received or acknowledged, and the idle timer's expiry sends
   CONNECTION_CLOSE(ZMUX_IDLE_TIMEOUT) (SPEC.md Section 6.4). This item also
   applies with pipe acknowledgement (Section 3.5).
4. Information is acknowledged by PIPE_ACK, and endpoints retain
   unacknowledged information (SPEC.md Section 7.4; rule S4 of Section 11 of this document).
5. CONNECTION_CLOSE is sent on every outbound pipe (SPEC.md Section 6.5).

### 3.5 Pipe acknowledgement without multipath

A sender whose carrier reports neither its unsent bytes nor a low-water
mark can see the queue it builds in that carrier only through
acknowledgements. zmux endpoints therefore also acknowledge the single pipe
pair of a session without multipath.

**Parameter.** `zmux_pipe_ack`, ID 0x34b47621, an empty value (a non-empty
value is a TRANSPORT_PARAMETER_ERROR). **Pipe acknowledgement** is in use
when both endpoints sent it and multipath is not in use. A plain QMux peer
ignores the parameter (QMux-02 Section 5.1), so it never sees a PIPE_ACK
(SPEC.md Section 13.2). An endpoint MUST NOT send PIPE_ACK in a session
where neither multipath nor pipe acknowledgement is in use; a receiver
treats such a frame as unknown (SPEC.md Section 4.1 rule 3). The only
PIPE_ACK sent before the endpoint knows is the fence of early data (SPEC.md
Section 6.1.2), whose single entry has Position 0 and is ignored where
PIPE_ACK is in use, and which a peer without it rejects before processing
anything after it.

**Rules.** While pipe acknowledgement is in use, each endpoint acknowledges
its inbound pipe 0 (Section 4.2) as in Sections 6.1 to 6.3, with these
differences:

1. Positions (Section 2.3) count from the first byte of the pipe, the
   QX_TRANSPORT_PARAMETERS record included.
2. PIPE_ACK lists only pipe 0, at most one entry per lane of it; any other
   Pipe ID is a PROTOCOL_VIOLATION (a repeated entry is a
   FRAME_ENCODING_ERROR, Section 6.2), and a Position beyond the bytes the
   receiver has sent on that lane is a PROTOCOL_VIOLATION as in Section
   6.2. Without lanes every PIPE_ACK is a complete snapshot; with lanes a
   snapshot may omit unchanged lanes, with a complete one at least once per
   second (Section 6.2).
3. An endpoint acknowledges by rules 1 and 2 of Section 6.3 only. There is
   no PIPE_PING and no other multipath frame. It SHOULD put the PIPE_ACK
   into a record it writes anyway, and writes a record of its own only when
   a deadline would otherwise pass.
4. Nothing is retained (SPEC.md Section 9.6). Items 1, 2, 4 and 5 of
   Section 3.4 do not apply: data stays in order and a pipe failure ends
   the session. Item 3 applies: the idle and keep-alive timers count
   application activity received or acknowledged (a record that carries
   only PIPE_ACK or credit frames restarts neither), the idle timeout sends
   CONNECTION_CLOSE(ZMUX_IDLE_TIMEOUT), and a graceful end of the peer's
   pipe without CONNECTION_CLOSE is a pipe failure, not an idle close
   (SPEC.md Section 6.4).

Apart from the liveness that the idle timer measures (SPEC.md Section 6.4),
pipe acknowledgement plays no part in failure detection: no acknowledgement
stall fails the pipe.

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
  sender of the parameter. Every PIPE_LANE toward it carries the key.

A value of any other length is a TRANSPORT_PARAMETER_ERROR. Absent means
L = 1: no lanes. Like the parameters of Section 3.2 it MUST NOT be sent
without `zmux_multipath` or `zmux_pipe_ack` (SPEC.md Section 5.5); sent
with either while neither feature ends up in use, it is valid and lanes are
not available.

**Lanes are available** toward an endpoint when it sent L > 1 and multipath
or pipe acknowledgement is in use. The feature is one-directional: each
endpoint opens lanes on its outbound pipes as the peer's L allows, whatever
it announced itself, and only where its carrier offers them. A plain QMux
peer never sends the parameter, so it never sees a PIPE_LANE or a non-zero
Lane (SPEC.md Section 13.2). L bounds what the receiver keeps per pipe (one
incomplete record per lane, SPEC.md Section 9.8); any value is allowed, and
1 turns lanes off.

---

## 4. Pipe identifiers

### 4.1 Allocation

A pipe ID is a variable-length integer. Its least significant bit is the
joiner's role: 0 for the initiator, 1 for the responder. Pipe ID 0 is the
bootstrap pipe pair. Each joiner allocates its pipe IDs in increasing order
(initiator 2, 4, 6, ...; responder 1, 3, 5, ...), takes the next one for
every join attempt, whether or not the carrier connects, and never uses a
pipe ID for a second attempt or a second attachment. Pipe IDs are not
credit: the acceptor bounds what joins can cost it with the cap of Section
5.4 step 9 and the replay window of Section 4.3. A joiner that has used the
largest pipe ID of its parity cannot join again in that session.

### 4.2 Bootstrap pipes

The bootstrap pipe pair has pipe ID 0, joiner the initiator, fate group 0.
Its up direction is the initiator's outbound bootstrap pipe. A split
bootstrap (SPEC.md Section 6.1.1) is still pipe 0 up and pipe 0 down and
one attachment: when either carrier fails, the endpoint fails the
attachment and closes the other carrier too (Section 7.3). A healthy
direction is replaced by joining a new pipe.

### 4.3 Replay window

For the pipe IDs of the peer's parity an endpoint keeps h, the highest pipe
ID of a PIPE_JOIN it has verified (Section 5.4 step 5), and a bitmap that
covers the W pipe IDs of that parity below h and the W above it. For an
initiator peer h starts at 0, the bootstrap pair; for a responder peer,
which has no attachment yet, the bitmap covers its first W pipe IDs until
its first verified join. W is a local bound of at least max_pipes + H
(Section 5.4 step 9), counted in pipe IDs of that parity; the default is
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
receiver has not yet taken (Section 4.1) is a PROTOCOL_VIOLATION. Frames
that reference an ID of the peer's parity without attachment state follow
Sections 6.2, 8.1 and 9. Because only verified joins move the window,
frames injected on an unprotected carrier cannot push it past the joiner's
future pipe IDs.

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
- **Flags**: bits 0-1 (mask 0x03) direction: 0 up, 1 down, 2 bidi, 3
  invalid. Bit 2 (0x04) carrier-bound: the token covers the TLS exporter of
  the handshake that carries the new carrier (Section 10.5). Bit 3 (0x08)
  standby. Bits 4-7 (mask 0xf0) are reserved and MUST be 0.
- **Fate Group**: Section 2.2.
- **Priority** and the standby flag: the joiner's initial request for the
  acceptor's outbound direction of the new attachment, with the meaning of
  a PIPE_STATUS request (Section 9) at sequence number 0. For direction up
  the acceptor has no outbound direction on the attachment: the joiner sends
  Priority 0 without standby, and the acceptor ignores both.
- **Join Token**: 16 bytes, Section 10.5.

The whole frame MUST NOT exceed 64 bytes; with the fields above it is at
most 62 bytes and at least 26. The frame is never retransmitted.

### 5.2 In-band joining

For an attachment whose joiner can write on it (direction up or bidi), the
joiner MAY send PIPE_JOIN as the first frame of the first record of its
outbound pipe. Other frames MAY follow in the same record and in later
records immediately, without waiting for any answer.

The join record MUST then carry a complete PIPE_ACK snapshot (Section 6.2)
when the joiner has at least one entry, as the first record on every new
outbound pipe does (Section 5.7 step 2); the snapshot may continue at the
start of the next record when it does not fit. The join record SHOULD also
carry a PIPE_PING, which makes the acceptor acknowledge at once (Section
6.3) and counts against the limit of Section 6.4.

A first PIPE_JOIN is processed by the listener (Section 5.4), never as a
session frame, and its problems only ever close the carrier. On a pipe that
already belongs to a multipath session, a PIPE_JOIN frame is a
PROTOCOL_VIOLATION if it decodes, and a FRAME_ENCODING_ERROR if it does
not; in any other session it is a frame of a feature not in use (SPEC.md
Section 4.1 rule 3).

### 5.3 Out-of-band joining

For an attachment whose joiner cannot write first (direction down, for
example an HTTP GET response), and optionally for any other attachment, the
joiner hands the PIPE_JOIN frame (Type through Join Token) to its carrier,
which delivers it to the acceptor outside the pipe. The pipe's record stream
then starts directly with ordinary records.

The adapters of both ends agree on how the frame travels and how it is
encoded; it carries exactly the bytes from Type through Join Token, and it is
never placed where intermediaries commonly log it, such as a URL.

### 5.4 Acceptor processing

An acceptor (normally a listener that serves many sessions) processes a new
carrier as follows. Steps 1 to 6 MUST allocate no session state and MUST
perform no session operation; they may hold the carrier and at most one
record (in-band) or 64 bytes (out-of-band) of it.

1. Apply the pending-join limits of Section 5.6 (except to the streams
   admitted for a lane, Section 5.5). On excess, close the carrier.
2. Classify the first bytes (Section 5.5) or take the out-of-band frame.
   Parse PIPE_JOIN. On any parse failure or invalid field, close the carrier.
3. Look up the Session Locator. If no session matches, close the carrier.
4. If the session is not yet established, hold the join until establishment
   completes or the handshake timeout expires.
5. Verify the Join Token in constant time (Section 10.5), always with the
   join key of the peer's role, never with a role inferred from the Pipe
   ID's parity. On failure, close the carrier and report a **join
   authentication failure** locally, distinguishing whether the token was
   carrier-bound (ZMUX_PIPE_AUTH_FAILED is its local code). Nothing is sent
   to the peer.
6. If the Pipe ID has the joiner's parity and has attachment state, its bit
   is set, or it is stale (Section 4.3), close the carrier: the join is a
   replay or arrived too late. A Pipe ID of the other parity goes on to
   step 7.
7. From here on the join is authenticated, so violations are the peer's:
   - wrong parity for the joiner: close the session with
     PROTOCOL_VIOLATION;
   - a direction the carrier cannot provide (for example "up" on a carrier
     that is write-only for the acceptor): close the carrier and send
     PIPE_ABANDON(Pipe ID, ZMUX_PIPE_FAILED);
   - a carrier that is a stream of a session running, directly or through
     other sessions of the process, over a pipe of this session, as far as
     the acceptor can see (EXTENSIONS.md Section 10.2; a local condition):
     close the carrier and send PIPE_ABANDON(Pipe ID,
     ZMUX_PIPE_RESOURCE_LIMIT), so that the joiner backs off (Section 5.7)
     instead of rejoining at once. The check and the recording that the
     session now runs over the carrier are one atomic step of the process;
     a loop found only after step 8 is removed abruptly with the same
     PIPE_ABANDON(Pipe ID, ZMUX_PIPE_RESOURCE_LIMIT), never with
     ZMUX_PIPE_FAILED;
   - resource exhaustion, including the cap of step 9: close the carrier and
     send PIPE_ABANDON(Pipe ID, ZMUX_PIPE_RESOURCE_LIMIT).

   In each non-fatal case the acceptor sets the Pipe ID's bit (Section 4.3)
   and raises h if the ID is above it; the joiner retires the attachment
   when that PIPE_ABANDON arrives, or unilaterally (Section 8.4).
8. Attach the pipe(s): set the Pipe ID's bit, raise h if the ID is above it,
   create pipe state, take the joiner's initial request from the PIPE_JOIN
   (Section 9), and process the rest of the record (in-band) and then the
   pipe's records. The acceptor MAY write on its outbound direction of the
   attachment at once, and for a down or bidi attachment MUST write at
   least one record on it within its `zmux_max_ack_delay`, so that the
   joiner learns that the join succeeded (Section 5.7 step 4). That first
   record MUST carry a complete PIPE_ACK snapshot (Section 6.2) when the
   acceptor has at least one entry, and a PIPE_PING, subject to the
   PIPE_PING limit of Section 6.4 (a delayed PIPE_PING is sent as soon as
   the limit allows); a record of PIPE_ACK alone, not being ack-eliciting,
   would give the new direction no RTT sample. An acceptor with a
   non-default request for the joiner's outbound direction puts its
   PIPE_STATUS in the same record (Section 9). There is no separate
   "joined" frame. A listener that sent its own QX_TRANSPORT_PARAMETERS on
   the carrier before it classified it (SPEC.md Section 13.2 rule 7) counts
   its outbound position from the end of that record (Section 5.7 step 3).
9. **Cap on unretired attachments.** An acceptor keeps at most
   max_pipes + H attachments joined by the peer that are not retired (live,
   or failed or abandoned but not yet retired, Section 8.4). max_pipes is
   the number of attachments each endpoint aims for with its own joins
   (8 by default when multipath is used); H, the **attachment headroom**,
   is 8 by default: room for failed attachments whose retirement has not
   completed. The bootstrap pair counts as an attachment joined by the
   initiator. The count is taken after the join or failure being processed
   has been applied. Once the cap is reached:
   - outside suspension, the acceptor refuses an authenticated join beyond
     the cap as resource exhaustion (step 7); that is not a session error. A
     refused join is never attached and does not count against the cap: it
     leaves only its bit and its pending PIPE_ABANDON, both inside the
     replay window;
   - while the acceptor is SUSPENDED (RESUME.md Section 4), it instead
     **displaces** one attachment of the peer and then accepts the join: it
     retires locally the oldest unretired attachment of the peer that has
     failed and whose own PIPE_ABANDON has not been acknowledged, dropping
     its state including that pending PIPE_ABANDON. If none has failed and
     the join provides a direction the acceptor lacks, it fails the live
     attachment whose **last progress** (the later of the last advance of
     its outbound acknowledged position, the last record received on its
     inbound direction and its attachment) is oldest, as Section 7.2 says
     but without sending PIPE_ABANDON, and retires it locally at once. A
     join that provides no direction the acceptor lacks is refused as
     outside suspension.

   A joiner applies the same cap to the attachments it joined itself: it
   does not join while it has max_pipes + H of them unretired, except that
   a joiner that is SUSPENDED first displaces one of them in the same way
   before a join that provides a direction it lacks.

### 5.5 Listener classification

A listener that accepts both new sessions and joins classifies a new carrier
by the first frame of its first record, applying the incremental check of
SPEC.md Section 6.2 with these alternatives:

- `ff 51 53 30 0d 0a 0d 0a` after a Size from 9 to the listener's own
  `max_record_size`: a new session;
- `9a 24 de 36` (PIPE_JOIN) after a Size from 26 to the listener's own
  `max_record_size`: a join;
- `ba 05 a1 c9` (PIPE_LANE) after a Size from 22 to the listener's own
  `max_record_size`: a lane (Section 5.8).

Any other prefix is rejected as soon as it is seen. Carriers that deliver an
out-of-band join skip classification. New streams of a connection already
carrying pipes of the endpoint that are handed to it without a listener are
classified the same way.

**Lanes and the carrier limits.** A new stream delivered with the identity
of a multi-stream carrier connection is admitted outside the limits of
Section 5.6 only when, at the moment it arrives, the connection carries
lane 0 of at least one **OPEN inbound pipe** of one of the endpoint's
sessions (STATE_MACHINE.md Section 3.2: the bootstrap pipe of an
established session, or a pipe attached by a verified join, Section 5.4
step 8, or as joiner by its first record, Section 5.7 step 4), and fewer
streams of the connection admitted this way are still awaiting their first
record than it carries such pipes. Every other stream takes a slot like any
carrier. An admitted stream is decided as a lane by its first record
(Section 5.8) within the first-record timeout, outside the limits
throughout; a stream that turns out to be a join or a new session takes a
slot at that moment, as an unauthenticated carrier, and goes on through
Section 5.4. Without this rule an established session, which needs no
authentication, could open the listener to unbounded pending carriers.

### 5.6 Pending-join and rate limits

These limits are local policy; the values are RECOMMENDED:

- at most 64 carriers per listener, at most 4 per session and at most 8 per
  source, in steps 1 to 7 of Section 5.4 at any time (the streams admitted
  for a lane excepted, Section 5.5). A source is a client address,
  aggregated to its /64 prefix for IPv6. When the 64 listener slots are
  full, a new carrier evicts the oldest unauthenticated carrier rather than
  being refused;
- a carrier must deliver its first record, or come with its out-of-band
  join, within the **first-record timeout** (default 2 s) after the carrier
  is established; otherwise it is closed. A carrier that has not completed
  step 7 within the handshake timeout (default 10 s) is closed;
- failed join verifications are rate limited per source. A listener MUST
  NOT stop verifying the joins of a session because of failures
  attributed to that session: anyone who knows a locator could otherwise
  block every legitimate join and resume of the session.

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
2. It computes the token and sends the join (in-band) or hands it to the
   carrier (out-of-band). The attachment enters JOINING; so does its
   inbound direction, if it has one. The first record the joiner writes on
   the new outbound direction, if the attachment has one, MUST carry a
   complete PIPE_ACK snapshot (Section 6.2) when the joiner has at least
   one entry: after the PIPE_JOIN for an in-band join, at the start of the
   record for an out-of-band one.
3. While JOINING, its outbound direction is live but not usable (Section
   9): it carries control frames, and new stream data only as rule S1 of
   Section 11 allows. Its information counts as unacknowledged like
   any other. Its inbound direction is not yet listed in PIPE_ACK. The
   joiner sends no PIPE_STATUS for the attachment while it is JOINING; a
   request it changes meanwhile is sent, with its latest value, once the
   attachment is ACTIVE. If the attachment has an inbound direction and the
   first record that arrives on it begins with QX_TRANSPORT_PARAMETERS (a
   shared listener that sent its parameters before it classified the
   carrier, SPEC.md Section 13.2 rule 7), the joiner discards that record
   without processing it: it confirms nothing, is not counted in the pipe's
   position (Section 2.3) and is not acknowledged. A
   QX_TRANSPORT_PARAMETERS frame anywhere else on the pipe is a
   PROTOCOL_VIOLATION (SPEC.md Section 5.1).
4. The attachment becomes ACTIVE when a PIPE_ACK acknowledges a position
   greater than 0 on its up direction, or a complete record arrives on its
   down direction; its inbound direction becomes OPEN with that first
   record.
5. If the carrier fails while JOINING, or the attachment is not ACTIVE
   within the handshake timeout, it fails (Section 7.2) and its information
   is re-sent elsewhere.

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
available toward the peer (Section 3.6) and its carrier offers them, once a
PIPE_ACK has acknowledged a position greater than 0 on the pipe's lane 0.
It opens a new stream on the connection of lane 0 and writes on it one
record that holds the PIPE_LANE, with the next lane number of the pipe
(lanes are numbered in increasing order from 1 and each number is used
once), and possibly PADDING, and nothing else until the lane is confirmed
(Section 2.5). PIPE_LANE is ack-eliciting. A pipe has at most one lane that
is not yet confirmed. A lane whose stream ends, is reset or fails before it
is confirmed, or that is not confirmed within the handshake timeout (the
writer then resets it), is **refused**: that is not a failure of the pipe,
nothing on it needs reinjecting, a later acknowledgement for it is ignored,
and the writer opens no further lane on that pipe. The reverse half of a
bidirectional stream used as a lane only ever ends gracefully and is ignored
by the writer. A confirmed lane stays open until its pipe ends (Section 8).
A lane waiting for the connection's stream credit counts as the pipe's one
unconfirmed lane; its handshake timeout runs only from the moment its
stream is opened, and it is dropped when the pipe ends. Where the
application shares that stream credit (EXTENSIONS.md Section 10.2), a lane
may also not be opened now, which is neither a refusal nor an error.

**Frames by lane.** In a multipath session any frame may go on any
confirmed lane. In a session without multipath (pipe acknowledgement,
Section 3.5), whose stream data must arrive in order (SPEC.md Section 7.3),
the frames of a stream direction's sending part (STREAM, RESET_STREAM,
RESET_STREAM_AT, STREAM_DATA_BLOCKED) all go on one lane, which the writer
changes only once a PIPE_ACK covers every frame of that sending part on the
old lane (a hand-off to the carrier is not enough); every other frame goes
on lane 0, and a CONNECTION_CLOSE waits as SPEC.md Section 6.5 step 1 says.
Frames of different streams can then be reordered, which the rules for
opening and accepting streams allow (SPEC.md Section 7.2 rules 2 and 5), and
limits only grow, so a credit frame that overtakes data is never a
violation.

**Reader.** A new inbound stream whose first record begins with PIPE_LANE is
handed over with the identity of its carrier connection (Section 5.5). The
reader looks the Lane Key up among its sessions, comparing it in constant
time:

1. If no session has that key, or the session has no attachment Pipe ID
   with a live inbound direction whose lane 0 is on the same connection as
   the new stream, the reader aborts the new stream (stops reading it) and
   does nothing else. A session in CLOSING or CLOSED (STATE_MACHINE.md
   Section 4.1) counts as having no such attachment. A stream that names
   another endpoint's session, or a pipe of another connection, is never
   attributed to a session.
2. Otherwise the stream belongs to that pipe. When lanes are not available
   toward the reader, PIPE_LANE is a frame of a feature not in use, a
   FRAME_ENCODING_ERROR (SPEC.md Section 4.1 rule 3). The session is closed
   with PROTOCOL_VIOLATION when Lane is 0 or at least the reader's L, when
   that Lane has already been opened on the pipe, or when the lane's first
   record holds a frame other than the PIPE_LANE and PADDING.
3. Otherwise the stream is lane Lane of the pipe from its first byte: its
   records are processed as the pipe's and its position counts from 0, the
   PIPE_LANE record included. A lane that ends, in any way, before any
   record after its first has arrived is no event of the pipe; after that,
   its end is the pipe's (Section 7.3).

A PIPE_LANE frame anywhere else, on a pipe's lane 0 or after the first
frame of a lane, is a PROTOCOL_VIOLATION if it decodes and lanes are
available toward the receiver, and otherwise a FRAME_ENCODING_ERROR
(SPEC.md Section 4.1 rule 3); decoding errors take precedence. A reader
reads all lanes of a pipe, none starving another. PIPE_LANE is never
retransmitted or reinjected, is never chargeable, and creates no response
state beyond the acknowledgement of its record.

---

## 6. Acknowledgement

### 6.1 Counting positions

A receiver counts a record into a pipe's position (its lane's, Section 2.5)
only after it has completely received the record and applied every frame in
it to session state, so that the information survives the loss of the pipe.
Acknowledgement never relies on the carrier's own acknowledgements: bytes
that the carrier acknowledged but the zmux receiver did not process are not
acknowledged and are re-sent. A record that is incomplete when its pipe
ends is never counted (Section 7.3).

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
  (Pipe ID, Lane) is not greater than the previous entry's, which also
  rejects an entry listed twice.
- **Pipe ID**: a pipe inbound to the frame's sender.
- **Lane**: a lane of that pipe (Section 2.5); 0 for a pipe without lanes.
- **Position**: the sender's receive position on that lane (Section 6.1).
- **ACK Delay**: microseconds between the moment the sender processed the
  record that brought the position to this value and the moment it sent the
  frame.

The PIPE_ACK frames that an endpoint sends together, in one batch of
records, form a **snapshot**. A **complete** snapshot lists every inbound
pipe of the sender that is not retired and whose position is greater than
0, including pipes that have ended but whose attachment is not yet retired,
and for a pipe with lanes every such lane. A snapshot MAY omit the pipes and
lanes whose position has not changed since the last complete snapshot the
endpoint sent; the endpoint MUST send a complete snapshot at least once per
second while it has such pipes, and a snapshot that omits pipes still lists
every change since that complete snapshot. A snapshot that does not fit in
a record is split across several PIPE_ACK frames, each listing a strictly
increasing subset of the entries; a receiver applies each entry on its own.
A pipe on which nothing has arrived yet is not listed. PIPE_ACK is not
ack-eliciting and is never retransmitted.

PIPE_ACK sets no limit on the number of pipes. An endpoint has at most
max_pipes + H unretired attachments joined by the peer and at most
max_pipes + H that it joined itself (Section 5.4 step 9), each with at most
one inbound pipe, so a snapshot has at most 2 x (max_pipes + H) entries
without lanes, plus one entry per further lane, each at most 32 bytes.

A receiver of PIPE_ACK processes each entry:

- a Pipe ID of the receiver's own parity that it has not taken (Section
  4.1): PROTOCOL_VIOLATION;
- a retired Pipe ID (for an ID of the peer's parity without attachment
  state, Section 4.3), or a Position of 0: the entry is ignored;
- a Position greater than the number of bytes the receiver has sent on that
  lane of that pipe, which is 0 for a pipe on which it has no outbound
  direction and for a lane it has not opened: PROTOCOL_VIOLATION, or
  ZMUX_RESUME_FAILED in a session with resume in use (RESUME.md Section 7);
- a Position not greater than the largest already acknowledged: the entry is
  ignored (outdated, normal when PIPE_ACKs travel on different pipes);
- otherwise the lane's position advances: every frame on that lane wholly
  below it is acknowledged (SPEC.md Section 7.4).

### 6.3 When to acknowledge

Each endpoint keeps, per inbound pipe, the ack-eliciting bytes received
since the last PIPE_ACK that reported that pipe, summed over its lanes. A
record is ack-eliciting when it contains at least one ack-eliciting frame
(SPEC.md Section 1.4). An endpoint MUST send a PIPE_ACK:

1. when any inbound pipe has at least its own `zmux_ack_threshold` such
   bytes;
2. no later than its own `zmux_max_ack_delay` after the first such byte;
3. immediately after processing a record that contains PIPE_PING;
4. promptly after an inbound pipe ends, so that its final position (that of
   each of its lanes) is known;
5. as a complete snapshot (Section 6.2) when the PIPE_ACK answers, under
   rule 3, a PIPE_PING that arrived in the first record of a newly attached
   inbound pipe. Together with the snapshots in the first records of new
   outbound pipes (Sections 5.2 and 5.4 step 8) this gives both endpoints
   each other's positions within one round trip of any join, which a
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

PIPE_PING has no fields. It is ack-eliciting and elicits an immediate
PIPE_ACK (Section 6.3 rule 3); the response is the next snapshot, so any
number of PIPE_PINGs produce bounded response state (SPEC.md Section 9.2).
It is never retransmitted. PIPE_PING is chargeable (SPEC.md Section 9.4,
NE-2). An endpoint sends at most 8 PIPE_PING frames in total in any
interval of one second; when more pipes need one, it serves those that
have waited longest first, each as soon as the limit allows.

### 6.5 Choice of pipe for PIPE_ACK and credit frames

Rule S1 of Section 11 says which outbound pipes may carry control frames.
Among them, an endpoint sends PIPE_ACK and credit frames first on ACTIVE
pipes; a JOINING, SUSPECT or DRAINING pipe carries such a frame only as an
additional copy, or when no ACTIVE outbound pipe exists (a DRAINING pipe
marked suspect comes last, Section 8.2). Among the ACTIVE pipes it prefers
non-standby pipes before standby pipes, and among them the lowest effective
priority (Section 9). This order is the **control order**.

- **Primary pipe.** An endpoint SHOULD send PIPE_ACK on the first pipe of
  the control order, so that the peer's RTT samples for different pipes
  share one return path.
- **Redundancy.** When it has at least two live outbound pipes, an endpoint
  SHOULD also send its latest snapshot on the next pipe of the control
  order no later than 50 ms after it sent a changed snapshot on the primary
  pipe, and immediately when the primary pipe becomes suspect.
- **Credit frames.** MAX_DATA, MAX_STREAM_DATA and MAX_STREAMS SHOULD be
  sent on the first pipe of the control order. When the window is the
  bottleneck (the endpoint has received a BLOCKED frame for the scope, or
  sends credit under SPEC.md Section 8.3 rule 3), it SHOULD also put the
  latest value of that limit into the record that carries its next
  redundant snapshot. When the pipe that carried the latest value of a
  limit becomes suspect, the endpoint SHOULD send that value again on an
  ACTIVE pipe.
- **Return-pipe liveness.** When an endpoint sends a PIPE_ACK on a pipe
  whose last ack-eliciting byte was sent, or which became live, more than
  max(1 s, the time after which it would mark that pipe suspect) (Section
  7.1) ago, it SHOULD add a
  PIPE_PING to the same record; a pipe that carries only PIPE_ACK frames
  would otherwise never be judged.
- **PIPE_PING answers on its own pipe pair.** The PIPE_ACK that answers a
  PIPE_PING under rule 3 of Section 6.3 SHOULD also be sent on the outbound
  direction of the attachment on which the PIPE_PING arrived, when that
  direction exists and has not failed, so that a probe of a backup pipe
  does not depend on the primary return path.

These copies carry the same value on different pipes. They are an explicit
exception to the rule that limits are sent only when they increase (SPEC.md
Section 4.3) and are never chargeable (SPEC.md Section 9.4.1).

---

## 7. Failure detection and reinjection

### 7.1 Suspect and failed pipes

An endpoint judges each outbound pipe from the acknowledgement of its
ack-eliciting bytes (stream data, control frames or an unanswered PIPE_PING)
and from the progress of its carrier's writes; for a pipe with lanes,
acknowledgement progress is that of its cumulative acknowledgement point
(Section 2.5). A **stall** is a lack of acknowledgement progress on
ack-eliciting bytes sent on the pipe, or a carrier that accepts no waiting
bytes, lasting longer than a threshold of the endpoint. An outbound pipe
becomes **suspect** when the endpoint concludes from a stall that it may be
dead; this applies to JOINING pipes too, and a DRAINING pipe is only marked
suspect (Section 8.2).
An outbound pipe becomes **failed**:

- when its carrier reports an error or an unexpected end on any of its
  confirmed lanes (a lane that ends before it is confirmed is refused,
  Section 5.8), or the endpoint removes it abruptly;
- while it has never been confirmed (Section 5.7), only when its join does
  not complete within the handshake timeout;
- when the endpoint concludes from a lasting stall that it is dead (not a
  DRAINING pipe, which fails only as Section 8.2 says).

A suspect pipe returns to ACTIVE when its acknowledged position advances and
its carrier accepts bytes again; a DRAINING pipe stays DRAINING (Section
8.2). While an endpoint has no live inbound pipe (RESUME.md Section 4.2), no
acknowledgement can reach it: it then marks no outbound pipe suspect or
failed for lack of acknowledgement progress (carrier errors and write
stalls still count). Acknowledgements MUST NOT be withheld because the pipe
that would carry them is suspect (Section 11 rule S1).

**Probe interval.** On an outbound pipe of an attachment on which the peer
has no outbound direction (an uplink, or a downlink of a split carrier), an
endpoint sends a PIPE_PING whenever no ack-eliciting byte has been sent on
it for 15 s, measured from the moment the pipe became ACTIVE if it never
carried one. The peer judges such an inbound pipe only by the records that
arrive on it (Section 7.3), so this constant is part of the protocol. An
endpoint MAY probe any outbound pipe more often, within the limit of
Section 6.4.

### 7.2 Effects of suspicion and failure

When an outbound pipe becomes suspect, the sender:

1. stops scheduling new stream data on it; control frames continue to use
   it as rule S1 of Section 11 allows;
2. reinjects (Section 7.4) the information of every ack-eliciting frame
   sent on it beyond its acknowledged position; bytes already handed to the
   carrier count as sent and are never taken back;
3. sends PIPE_PING on it, subject to the limit of Section 6.4, and keeps
   processing acknowledgements for it.

When an attachment fails, the endpoint additionally:

4. closes the carrier (abortively if needed);
5. sends PIPE_ABANDON(Pipe ID, ZMUX_PIPE_FAILED) on another outbound pipe
   (Section 8), unless it already sent PIPE_ABANDON for the attachment;
6. keeps the attachment's state until it is retired (Section 8.4).

### 7.3 Inbound pipe end and inbound silence

The end of an inbound pipe is **expected** when it is a graceful end at a
record boundary, of every lane for a pipe with lanes (a lane that ended
before its second record does not count), after the receiver has received
PIPE_ABANDON for the attachment or a CONNECTION_CLOSE, or after it has
itself sent PIPE_ABANDON(NO_ERROR) for the attachment. When an inbound pipe
ends in any other way (carrier error, reset, an end in the middle of a
record, or an unexpected end of stream, on any of its lanes except one that
ends before its second record), or, for an attachment on which the receiver
has no outbound direction, when the receiver judges it dead because no
complete record has arrived on it (see below), the receiver:

1. discards any incomplete record, on every lane, and does not count it;
2. treats the attachment as failed: if the attachment is a pipe pair, its
   own outbound direction fails too (Section 7.2);
3. sends PIPE_ABANDON(Pipe ID, ZMUX_PIPE_FAILED) unless it already sent one,
   and reports the final position in its next PIPE_ACK (Section 6.3 rule 4).

A receiver MUST NOT judge such an inbound pipe dead before no complete
record has arrived on it, from its last complete record or from the moment
it became OPEN, for at least the probe interval of Section 7.1 plus n / 8 s,
n being the number of its live inbound pipes (the delay the PIPE_PING limit
of Section 6.4 can add to the sender's probes), plus max(2 s, 2 x the
largest smoothed RTT of its live outbound pipes), the RTT being 333 ms when
it has none; n and the RTT are taken when the receiver judges.

A JOINING inbound pipe is bounded by the handshake timeout instead (Section
5.7). A pipe pair is judged by its outbound direction (Section 7.1).

### 7.4 Reinjection

Reinjection re-sends information, not records, as RFC 9000 Section 13.3
does after packet loss. For each ack-eliciting frame that was sent on a
suspect or failed pipe beyond the acknowledged position:

| Frame | What is sent again |
|---|---|
| STREAM | the unacknowledged byte ranges and the FIN, re-chunked as convenient, unless the sending part has been reset; after RESET_STREAM_AT, only ranges below the smallest Reliable Size sent. A FIN is re-sent even when the frame carried no data. The frame at offset 0 must still carry the whole preamble (EXTENSIONS.md Section 4.2). |
| RESET_STREAM | the same frame, unchanged, until acknowledged or the sending part is terminal |
| RESET_STREAM_AT | the frame with the current, smallest Reliable Size ([RSA-11] Section 5), until acknowledged or the sending part is terminal |
| STOP_SENDING | the same frame until the receiving part is terminal |
| MAX_DATA, MAX_STREAMS | the current limit |
| MAX_STREAM_DATA | the current limit, only while the receiving part is in "Recv" |
| DATA_BLOCKED, STREAM_DATA_BLOCKED, STREAMS_BLOCKED | the current blocking limit, only while still blocked |
| GOAWAY | the current watermarks |
| PRIORITY_UPDATE | the latest update for the stream, if it is not yet acknowledged |
| PIPE_STATUS | the latest status for the pipe |
| PIPE_ABANDON | the same frame, until it is acknowledged (Section 8.4) |
| PIPE_ACK, PIPE_PING, PADDING, QX_PING, DATAGRAM, PIPE_JOIN, PIPE_LANE, QX_TRANSPORT_PARAMETERS, CONNECTION_CLOSE | nothing |

Reinjected information SHOULD be sent before new data (RFC 9000 Section
13.3). A sender SHOULD NOT re-send information that a PIPE_ACK has
acknowledged in the meantime. Information sent on a pipe is reinjected from
that pipe at most once: a pipe that is suspect and later fails does not
reinject it again, and for frames that carry a current value only the
latest value per scope is re-sent. A copy that is itself lost with the pipe
that carried it is reinjected from that pipe in turn. If the suspect pipe
recovers, queued reinjections that are now acknowledged are dropped.

If no other usable outbound pipe exists, reinjected information waits for a
new pipe (and the session may become SUSPENDED, RESUME.md Section 4).

### 7.5 No retransmission timers

A sender MUST NOT re-send information from a pipe that is not suspect,
failed or abandoned, and MUST NOT use any per-frame or per-record
retransmission timer: each pipe's carrier already recovers losses, and a
second timer-driven layer causes the meltdown of a reliable layer over
another. A sender MUST NOT send the same information on several pipes
proactively, with three exceptions:

1. the copies of PIPE_ACK and credit frames of Section 6.5;
2. **opportunistic reinjection** (Section 7.5.1);
3. **redundant urgent data**: new STREAM data of stream directions the
   sender treats as urgent (by their RFC 9218 urgency) on two pipes of their
   tier, and new stream data of any urgency written first on a pipe that
   has had no acknowledgement progress for a while, also on one more pipe of
   its tier; each piece of data copied at most once, and a bounded amount of
   copies whose data is not yet acknowledged.

These copies are not reinjection in the sense of Section 7.4, are never
driven by a timer, and SPEC.md Sections 4.3 and 9.4.3 allow them; the
receiver handles them as duplicates (Section 7.6).

#### 7.5.1 Opportunistic reinjection

While the sender cannot send new data of a stream because of the peer's
stream flow control, or no new stream data at all because of the peer's
session flow control, it MAY send the **blocking data** (the lowest
unacknowledged data of that stream, or for the session the unacknowledged
stream data sent first) again on another eligible pipe than the one that
carried it, never on a less-preferred tier while a more-preferred one is
eligible (Section 9). Each byte is re-sent this way at most once.

### 7.6 Duplicates at the receiver

Duplicate STREAM data, repeated control frames and outdated limits are
normal in multipath sessions. They are not errors, are handled by the
idempotence rules (SPEC.md Section 4.1 rule 5), and are never chargeable
(SPEC.md Section 9.4.1). Reinjection and duplicate suppression make each
byte of a stream reach the receiving endpoint once and in order whatever
happens to the pipes; that guarantee ends at the receiving zmux endpoint
(SPEC.md Section 7.4).

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
ZMUX_PIPE_FAILED. PIPE_ABANDON refers to the whole attachment. It MAY be
sent on any outbound pipe; it SHOULD be sent on a pipe other than the
abandoned one, except that a graceful removal of one's own outbound pipe MAY
put it as the last frame on that pipe.

PIPE_ABANDON for a pipe ID of the peer's parity without attachment state
(the join may have been lost, held by an intermediary or refused): if the ID
lies within the replay window of Section 4.3 and its bit is not set, the
receiver sets the bit, so that a later join with that ID is a replay, and
answers. In every other case (bit already set, stale, or above the window)
the frame is ignored, and the peer retires the attachment unilaterally
(Section 8.4). PIPE_ABANDON for an ID of the receiver's own parity that it
has not taken (Section 4.1) is a PROTOCOL_VIOLATION.

### 8.2 Graceful removal

An endpoint that removes attachment P gracefully:

1. stops sending new stream data and DATAGRAM frames on its outbound
   direction of P, which enters DRAINING. A DRAINING pipe never becomes
   ACTIVE or SUSPECT again. Control frames may still use it, as a last
   resort (Section 11 rule S1), until the **PIPE_ABANDON exchange** for P
   is complete, that is until the endpoint has both sent its PIPE_ABANDON
   for P (handed it to a carrier, on any pipe) and received the peer's;
   after that it carries only PIPE_ACK and CONNECTION_CLOSE. A DRAINING
   pipe that stalls (Section 7.1) is marked suspect: it stays DRAINING,
   moves after every other live pipe in the choice of Section 6.5, and the
   information of the ack-eliciting frames sent on it beyond its
   acknowledged position is reinjected at once (Section 7.4), stream data
   included;
2. sends PIPE_ABANDON(P, NO_ERROR);
3. keeps processing acknowledgements. The **drain** of P completes at the
   first moment at which the PIPE_ABANDON exchange is complete and every
   byte of stream data it sent on P has been acknowledged, checked whenever
   either can change (when it sends its PIPE_ABANDON, receives the peer's,
   or an acknowledgement arrives). It then shuts down its write side of P
   gracefully (every lane at a record boundary) and P is CLOSED. Waiting
   for the peer's PIPE_ABANDON guarantees that the peer knows the end is
   coming before it sees it (Section 7.3). Information of other
   ack-eliciting frames sent on P that is still unacknowledged at that
   moment is reinjected (Section 7.4). If the drain does not complete
   within a deadline of the endpoint's choosing, P fails instead (Section 7.2, without a second PIPE_ABANDON), and
   its unacknowledged information that was not already reinjected, stream
   data included, is reinjected;
4. keeps reading its inbound direction of P until the peer shuts it down
   or, counted from the moment the PIPE_ABANDON exchange is complete, the
   linger timeout of SPEC.md Section 6.5 expires. A linger expiry after
   this exchange ends the inbound direction gracefully, not as a failure.

An endpoint that receives PIPE_ABANDON(P, NO_ERROR) performs steps 1, 3 and
4 for its own directions of P and sends its own PIPE_ABANDON(P, NO_ERROR) if
it has not done so. Replacing a carrier is a join followed, once the new
pipe works, by a graceful removal of the old one; nothing on the wire marks
it.

### 8.3 Abrupt removal

An endpoint that removes P abruptly treats P as failed (Section 7.2):
reinjection, carrier closed, PIPE_ABANDON(P, ZMUX_PIPE_FAILED), or
PIPE_ABANDON(P, ZMUX_PIPE_RESOURCE_LIMIT) when it removes P for a local
resource limit or a loop (Section 5.4 step 7) rather than because P's path
failed. An endpoint that receives PIPE_ABANDON(P, error other than
NO_ERROR) treats its own directions of P the same way and answers with
PIPE_ABANDON if it has not sent one.

### 8.4 Retirement

An attachment is retired at an endpoint when that endpoint has sent
PIPE_ABANDON for it and that frame has been acknowledged, has received the
peer's PIPE_ABANDON for it, and each of its directions at that endpoint has
ended (outbound CLOSED or FAILED, inbound ENDED). Retirement takes effect at
the first moment all three hold, whichever event completes them (including
the endpoint's own closing of the carrier when the attachment fails or the
linger timeout expires); from then on both directions are RETIRED and the
state is freed. An endpoint answers a peer's PIPE_ABANDON once: a copy that
arrives again gets no second answer, because the endpoint's own
PIPE_ABANDON is already retained and re-sent until it is acknowledged.

**Unilateral retirement.** When the endpoint's own PIPE_ABANDON for an
attachment has been acknowledged and the peer's PIPE_ABANDON has not
arrived within max(2 s, 3 x PTO) after that acknowledgement, the endpoint
closes whatever carrier of the attachment is still open and retires the
attachment anyway. PTO is the RFC 9002 Section 6.2.1 probe timeout, with
the peer's `zmux_max_ack_delay`, of the endpoint's ACTIVE outbound pipes
(the largest; of its live outbound pipes when none is ACTIVE, and of the
initial values smoothed_rtt 333 ms and rttvar 166.5 ms when it has none). A
conforming peer answers within one round trip. While the endpoint is
SUSPENDED its PIPE_ABANDON cannot be acknowledged; the cap of Section 5.4
step 9 then displaces an attachment when a rejoin needs the room.

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
DRAINING; an inbound pipe is live while it is JOINING or OPEN
(STATE_MACHINE.md Section 3.2). When an endpoint has no live outbound pipe,
or no live inbound pipe:

- if resume is in use, the session enters SUSPENDED (RESUME.md Section 4);
- otherwise the session ends: if a live outbound pipe remains, the endpoint
  sends CONNECTION_CLOSE(NO_VIABLE_PATH) on it and follows SPEC.md Section
  6.5; if none remains, it closes its carriers without sending anything. In
  both cases it reports "pipes lost" (SPEC.md Section 6.5, error precedence
  item 4).

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
- **Priority**: any value from 0 to 2^62 - 1; lower values are preferred.
  The default is 0. No value is invalid.
- PIPE_STATUS states the sender's preference about the pipe that is inbound
  to the sender, that is, how the receiver should use its outbound direction
  of that attachment ([MULTIPATH-QUIC] Section 3.3 semantics, with a
  priority added). Each direction of an attachment therefore has its own
  status and priority: an endpoint sets those of its outbound direction
  locally and requests those of the peer's outbound direction with
  PIPE_STATUS. A received PIPE_STATUS never switches the receiver's own
  sending on or off and never changes what it expects to receive.
- Sequence numbers are per pipe ID and per sender, strictly increasing, and
  start at 1. Sequence number 0 is the initial request: for the acceptor's
  outbound direction the one in the PIPE_JOIN (Section 5.1), otherwise
  available with priority 0. A receiver ignores a PIPE_STATUS whose
  Sequence Number is not greater than the last one it received for the
  pipe. Each frame carries both fields, so the latest frame states the
  complete request.
- Pipe IDs that are not attached, with the rules of PIPE_ABANDON (Section
  8.1):
  - an ID of the receiver's own parity that the receiver has not taken
    (Section 4.1) is a PROTOCOL_VIOLATION;
  - an ID of the peer's parity without attachment state, an ID whose
    attachment is retired, or an attachment on which the receiver has no
    outbound direction: the frame is ignored. A receiver never stores a
    request for an attachment it does not have; a joiner states its initial
    request in PIPE_JOIN and sends PIPE_STATUS only once the attachment is
    ACTIVE (Section 5.7 step 3).

**Effective status and priority.** For each of the two fields of an
outbound direction separately, an explicit local setting wins, then the
latest PIPE_STATUS request of the peer for that attachment (the one in
PIPE_JOIN until a PIPE_STATUS with a sequence number of at least 1
arrives), then the default (available, priority 0). An endpoint SHOULD
honour the peer's request when it has no local setting: the request
typically expresses something the sender cannot see, such as the cost of
the path to the peer.

**Tiers.** An outbound pipe is **usable** when it is ACTIVE and the sender
may give it new stream data. The usable non-standby pipes of a direction
form **tiers** by effective priority, the lowest value most preferred;
standby pipes come after every tier. A usable pipe is **eligible** for new
stream data when its tier is the most-preferred tier, or a less-preferred
tier while every more-preferred tier cannot carry the offered data
(overflow) or has no usable pipe (failover); a standby pipe is eligible
only on failover, when no non-standby pipe of its direction is usable.

**Joining with a priority.** A joiner states the initial status and
priority of the acceptor's outbound direction of a new attachment in the
PIPE_JOIN itself (Section 5.1), so the request is covered by the join token
and in force from the first byte the acceptor sends. An acceptor with a
non-default request for the joiner's outbound direction puts its
PIPE_STATUS in its first record on the attachment (Section 5.4 step 8), or
sends it on another outbound pipe when the attachment gives it none.

**Standby.** A standby pipe carries new stream data only when no
non-standby pipe in its direction is usable (failover); it is never used for
overflow, whatever its priority. An endpoint that requests standby for the
peer's outbound direction of an attachment, and starts using its own
standby outbound pipe of that attachment because no non-standby outbound
pipe is usable, SHOULD send a PIPE_STATUS for the attachment with Status
available and the Priority it requests (its configured value, not 0), so
that the peer's outbound direction follows. Once a non-standby outbound
pipe of that direction is usable again, an endpoint that sent such a
takeover request MUST send a PIPE_STATUS restoring its configured request
(Status standby, the same Priority).

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
the bootstrap carriers of pipe 0 are carried by a TLS 1.3 handshake that
terminates at the peer and offers an [RFC8446] Section 7.5 exporter (a TLS
connection, a QUIC connection whose stream is the carrier, [RFC9001]
Section 4, or another channel with such an exporter); an implementation
never sets E on its own, because it cannot tell whether a TLS-terminating
intermediary sits in the path. It sets P only when the application supplied
a PSK, and I only when the application explicitly allowed insecure joins.
An endpoint MUST NOT set I together with P: on a plaintext bootstrap an
attacker could strip the stronger flag and select insecure mode.

### 10.2 Inputs

- **Exporter value**: TLS-Exporter("EXPORTER-zmux-session", "", 32) on the
  carrier of pipe 0 up; both endpoints export from that same carrier. A
  carrier that is a stream of a connection exports from that connection's
  TLS handshake. For a split bootstrap (SPEC.md Section 6.1.1) the exporter
  value is the 64-byte concatenation of the exporters of the uplink carrier
  and the downlink carrier, in that order.
- **PSK**: at least 16 bytes supplied by the application, identical at both
  endpoints and unpredictable to third parties, static or specific to the
  session.

### 10.3 Session secret

    salt           = "zmux session" || initiator Session Nonce || responder Session Nonce
    IKM            = exporter value (or empty) || PSK (or empty)
    session_secret = HKDF-Extract(salt, IKM)                      [RFC5869], SHA-256

The exporter value has a fixed length for a given bootstrap form (32 bytes,
or 64 for a split bootstrap), so the concatenation is unambiguous. In
insecure mode IKM is empty, so anyone who observed the bootstrap parameters
can compute the secret: it offers the protection of MPTCP keys exchanged in
the clear ([RFC7430]) and is only for deployments protected by other means.

### 10.4 Keys

    join_key_i   = HKDF-Expand(session_secret, "zmux join i", 32)
    join_key_r   = HKDF-Expand(session_secret, "zmux join r", 32)

`join_key_i` belongs to the initiator and `join_key_r` to the responder.
All labels are ASCII without a terminating zero.

### 10.5 Join token

    carrier_binding = TLS-Exporter("EXPORTER-zmux-pipe", "", 32) of the new carrier   if the carrier-bound flag is set
                    = empty                                                             otherwise
    token = first 16 bytes of HMAC-SHA256(join_key of the joiner's role,
                                          PIPE_JOIN bytes from Type through Priority || carrier_binding)

HMAC is [RFC2104] with SHA-256. The MAC input is the frame exactly as sent, so varint encodings are covered
as transmitted. The acceptor computes the token with the join key of the
peer's role, never with a role inferred from the Pipe ID (Section 5.4 step
5), and compares in constant time. A joiner sets the carrier-bound flag
only when the new carrier is carried by a TLS 1.3 handshake that terminates
at the peer and both ends can export from it: a TLS connection, or a stream
of a QUIC connection ([RFC9001] Section 4), whose exporter is the
connection's. A carrier without such a handshake (plain TCP, a TLS or QUIC
connection that an intermediary terminates) is never carrier-bound.

Several pipes may be streams of one QUIC connection and then share one
carrier_binding value. This holds only where the connection's own record
layer protects every byte of every stream end to end, as QUIC's does. A
stream of a multiplexer running over the connection (a stream of another
zmux session, EXTENSIONS.md Section 10.2, an HTTP/2 stream through a proxy)
is never carrier-bound, and an endpoint never sets E from the exporter of a
connection below such a stream. The exporter is the one of the completed
handshake, never an early exporter: a carrier-bound join is not sent as TLS
or QUIC early data (0-RTT).

### 10.6 Properties

- **One-time tokens.** The token covers the pipe ID, and each pipe ID is
  accepted at most once (Section 5.4 step 6, with the replay window of
  Section 4.3). A captured token cannot join a different pipe ID,
  direction, fate group, priority request or session; a token replayed on
  another stream of the same connection is a replay of a used Pipe ID.
- **No state before verification**: Section 5.4 steps 1 to 6.
- **Reflection**: tokens are keyed per joiner role.
- **Rate limiting**: per source, never per session (Section 5.6).
- **No distinguishable failure**: the joiner never learns why a join failed;
  the acceptor reports every failed verification locally.
- **Locators** identify a session to anyone who sees a join in clear text;
  where carriers are TLS, they are protected by the carrier.
- **Not authentication inputs**: positions, counters and pipe order are
  never MAC inputs; only explicit fields are.

### 10.7 Choosing a secret source

The secret source is chosen once, from pipe 0 (Section 10.1). Where the
endpoints terminate TLS themselves, the exporter (E) applies, optionally
with carrier-bound tokens for further direct TLS connections or QUIC
streams. Where an intermediary terminates TLS (a CDN, a WebSocket front
end), E is never set and a PSK (P) is used, provisioned per client or
derived per session from the application's own authenticated exchange.
Insecure joins (I) are for deployments protected by other means.

---

## 11. Sender rules

The receiver cannot observe how a sender places frames on its pipes; these
rules constrain every multipath sender:

- S1. What each outbound pipe state may carry:

  | State | Frames |
  |---|---|
  | ACTIVE | every frame |
  | JOINING | control frames; while no outbound pipe is usable (Section 9, standby pipes included), also stream data within the bound of rule S4, reinjected information first; no DATAGRAM frames. A JOINING pipe is not usable until its join is confirmed (Section 5.7 step 4) |
  | SUSPECT | every frame except STREAM frames with new stream data (bytes or a FIN not sent before), reinjected information and DATAGRAM frames |
  | DRAINING | as SUSPECT until the PIPE_ABANDON exchange for the attachment is complete (Section 8.2); afterwards only PIPE_ACK and CONNECTION_CLOSE |
  | FAILED, CLOSED | nothing |

  SUSPECT and DRAINING pipes are a last resort: an endpoint puts a frame on
  one only when it has no JOINING or ACTIVE outbound pipe, or when the frame
  concerns that pipe (its PIPE_PING, the PIPE_ABANDON of its own
  attachment), or as the additional copies of Section 6.5. A frame sent
  there is not reinjection.
- S2. New stream data, reinjected information, the redundant urgent data
  of Section 7.5 exception 3 and the opportunistic copies of Section 7.5.1
  go only to eligible pipes (Section 9), apart from the JOINING pipes of
  S1.
- S3. Retransmission only as Section 7 allows.
- S4. A sender retains the information of every frame that has a
  reinjection rule (Section 7.4) until it is acknowledged, and never
  discards retained information to make room. Its retained stream data
  (each byte counted once, preamble bytes included) is bounded by the
  peer's flow-control limits and by a bound of its own, never below what
  one acknowledgement loop of its pipes needs; at that bound it sends no
  new stream data until acknowledgements arrive. Control frames,
  reinjection and FIN-only STREAM frames (SPEC.md Section 8.2) are not
  held back by it.
- S5. DATAGRAM frames are never reinjected; a stream's datagrams follow its
  unacknowledged opening frame (EXTENSIONS.md Section 6.4).

A pipe with lanes (Section 2.5) is one pipe for every rule above.

---

## 12. Fairness

Every carrier runs its own congestion control, and pipes that share a
bottleneck together take several shares of it; zmux cannot couple the
congestion control of separate carriers ([RFC6356] needs coupling inside
the transport). By default an application
SHOULD open at most one pipe per distinct network interface or relay and
per direction, and SHOULD give pipes that share a bottleneck the same
non-zero fate group.

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

## 14. Interactions

- **GOAWAY**: the PING fence is best effort in multipath sessions
  (EXTENSIONS.md Section 3.3), and a fence request lost with a pipe is
  replaced (SPEC.md Section 4.5); the "not processed" guarantee does not
  depend on it.
- **Closing**: CONNECTION_CLOSE goes on every live outbound pipe; the first
  copy received closes the session (SPEC.md Section 6.5).
- **Datagrams**: never reinjected; lost with their pipe (EXTENSIONS.md
  Section 6.5).
- **Preamble**: always in the frame at offset 0, also after reinjection.

---

## 15. Protocol constants and recommended defaults

Protocol constants:

| Constant | Value | Defined in |
|---|---|---|
| `zmux_ack_threshold`, `zmux_max_ack_delay` when absent | 65536 bytes, 25 ms | 3.2 |
| complete PIPE_ACK snapshot | at least once per second | 6.2 |
| PIPE_PING rate | at most 8 per session in any one-second interval | 6.4 |
| acknowledgement redundancy interval | 50 ms | 6.5 |
| probe interval of attachments without an outbound direction at the peer | 15 s | 7.1 |
| unilateral retirement | max(2 s, 3 x PTO) | 8.4 |
| maximum PIPE_JOIN size, Session Locator, Join Token, Lane Key | 64, 16, 16, 16 bytes | 5.1, 3.1, 3.6 |

Recommended defaults of local bounds that a peer may meet:

| Item | Default | Defined in |
|---|---|---|
| attachment cap | max_pipes + H, max_pipes 8 and H 8 | 5.4 |
| replay window W | max(64, 2 x (max_pipes + H)), at least max_pipes + H | 4.3 |
| join refusal backoff | 1 s, doubling, at most 60 s | 5.7 |
| pending joins | 64 per listener, 4 per session, 8 per source | 5.6 |
| first-record timeout | 2 s | 5.6 |

---

## 16. Registry considerations

This document defines the frames PIPE_JOIN, PIPE_ACK, PIPE_LANE, PIPE_PING,
PIPE_ABANDON and PIPE_STATUS, the transport parameters `zmux_multipath`,
`zmux_ack_threshold`, `zmux_max_ack_delay`, `zmux_pipe_ack` and
`zmux_max_lanes`, the PIPE_JOIN flags, Secret Flags and PIPE_STATUS values,
and the labels of Section 10. Their codepoints are registered in
REGISTRY.md Sections 3, 4, 8 and 9.

---

## 17. Security considerations

- Join authentication: Section 10.
- Unauthenticated carriers hold listener resources only within the limits
  of Section 5.6 and only until the handshake timeout.
- A peer can make the endpoint keep at most max_pipes + H unretired
  attachments (Sections 5.4 step 9 and 8.4), which bounds the size of
  PIPE_ACK snapshots, and, for pipe IDs without attachment state, at most
  the 2W bits of the replay window with their pending PIPE_ABANDON (Section
  4.3). Frames about IDs of the receiver's own parity that it has not taken
  are fatal, and PIPE_STATUS for IDs without attachment state is ignored,
  so no frame makes the receiver store state per unknown ID. Only verified
  joins move the replay window.
- A peer that keeps joining beyond the cap is refused each time; its rate is
  bounded by the listener's per-source limits (Section 5.6).
- An authenticated peer can make the endpoint retain at most its own
  flow-control limits of data and its own bound on retained data (Section
  11 rule S4).
- A lane is accepted only with the receiver's Lane Key, which travels only
  in the session's own records, and only on the connection of its pipe's
  lane 0 (Section 5.8). The key confines a lane to the session that issued
  it, and the connection check to the pipe's own path; whoever knows the key
  reads the session's records already, so the key needs no MAC and no
  replay protection. A peer can make the endpoint keep at most L - 1 lanes
  per inbound pipe.
- Spreading exposes per-pipe traffic volumes to observers of each carrier.
- An intermediary that terminates a carrier's TLS sees the pipe's content
  and locators; end-to-end protection then needs the reserved record
  protection feature (REGISTRY.md Section 7) or application-level
  encryption.

---

## 18. References

### 18.1 Normative

- [RFC8446] The Transport Layer Security (TLS) Protocol Version 1.3, Section
  7.5.
- [RFC9001] Using TLS to Secure QUIC.
- [RFC9002] QUIC Loss Detection and Congestion Control.
- [RFC5869] HKDF. [RFC2104] HMAC. FIPS 180-4 (SHA-256).
- [RFC4648] The Base16, Base32, and Base64 Data Encodings.
- [RSA-11] draft-ietf-quic-reliable-stream-reset-11.
- SPEC.md and the references of SPEC.md Section 15.

### 18.2 Informative

- [MULTIPATH-QUIC] Managing multiple paths for a QUIC connection,
  draft-ietf-quic-multipath-21.
- [RFC6356] Coupled Congestion Control for Multipath Transport Protocols.
- [RFC7430] Analysis of Residual Threats and Possible Fixes for Multipath
  TCP.
