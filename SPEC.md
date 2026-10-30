# zmux Protocol Specification

This document, [MULTIPATH.md](./MULTIPATH.md) and [RESUME.md](./RESUME.md)
define the zmux core protocol; [EXTENSIONS.md](./EXTENSIONS.md) defines
negotiated features, and [REGISTRY.md](./REGISTRY.md) lists every
codepoint.

This specification replaces the earlier zmux wire format, whose first bytes
are the ASCII magic `ZMUX` (the "legacy format"). There is no version
negotiation and no dual stack: a legacy endpoint and an endpoint of this
specification fail at the first bytes they exchange (Section 6.2).

---

## 1. Introduction

### 1.1 What zmux is

zmux is a session protocol that multiplexes many flow-controlled byte streams
and optional datagrams over a set of reliable byte pipes. The set may contain
a single bidirectional pipe (for example one TCP or TLS connection) or many
unidirectional pipes of different carriers that are added and removed while
the session runs. A session survives the loss of individual pipes, can spread
every stream across all pipes frame by frame, and can be resumed after every
pipe has been lost.

The session layer uses the stream semantics of QUIC version 1 [RFC9000]:
stream identifiers, offsets, final sizes, absolute flow-control limits and
idempotent control frames. These semantics are exactly what a session needs
when its frames travel over several pipes and therefore arrive out of order,
duplicated or not at all when a pipe breaks.

### 1.2 Relationship to QUIC and QMux

zmux is its own protocol. It is not a profile of QMux, and it does not depend
on QMux being standardized. It keeps a single wire encoding that is compatible
with QMux [QMUX-02]:

- the record framing of QMux-02 Section 3.2 (Section 3 of this document);
- QUIC version 1 frame encodings (RFC 9000 Section 19) for all core frames;
- the QMux-02 frames QX_TRANSPORT_PARAMETERS and QX_PING, and the
  `max_record_size` transport parameter;
- zmux frames and transport parameters that use codepoints at or above 2^14,
  which never collide with QUIC or QMux codepoints.

Compatibility is a hard invariant in one direction: **a zmux endpoint talking
to a plain QMux endpoint behaves as a correct QMux-02 endpoint** (Section 13).
Between two zmux endpoints, zmux rules take precedence over QMux rules where
they differ; every such difference is gated on a transport parameter that a
plain QMux endpoint never sends. The one difference that precedes the peer's
parameters, early data under the zmux floor (Section 6.1.2), is sent only
toward a peer the application declares to be a zmux endpoint and is fenced
so that a plain QMux peer processes none of it; early data under remembered
parameters is QMux-02's own 0-RTT. zmux defines no second, "native" codec.

### 1.3 Conventions

The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT", "SHOULD",
"SHOULD NOT", "RECOMMENDED", "NOT RECOMMENDED", "MAY", and "OPTIONAL" in the
zmux documents are to be interpreted as described in BCP 14 [RFC2119]
[RFC8174] when, and only when, they appear in all capitals.

Packet and frame diagrams use the notation of RFC 9000 Section 1.3. `(i)`
denotes a QUIC variable-length integer (RFC 9000 Section 16). All multi-byte
fixed-width integers are big-endian.

A reference such as "RFC 9000 Section 4.5" applies that text normatively, with
the deltas stated here.

Timers: every timer, timeout, deadline and detection condition of duration
T that starts at time t0 fires at the first event (a received byte, a
completed write, an expiring timer) at which now >= t0 + T. This single
convention applies throughout the zmux documents, whatever the wording:
"expires after T", "has elapsed", "has lasted longer than T", "for longer
than T" and "more than T ago" all mean now >= t0 + T. Traces assert states
at t0 + T - 1 ms and t0 + T. Where this document says "as QUIC", read every
mention of "packet acknowledgement" according to Section 7.4.

SRTT, where a session-level rule uses it (Sections 4.5 and 6.5, EXTENSIONS.md
Section 3.3), is the largest smoothed RTT among the
session's live outbound pipes (JOINING, ACTIVE, SUSPECT or DRAINING;
MULTIPATH.md Section 8.5), each estimated as RFC 9002 Section 5.3 does;
failed pipes do not count. Outside multipath
sessions, where zmux takes no RTT samples of its own, it is an estimate from
QX_PING or the carrier when the implementation has one. When no estimate
exists it is 333 ms (RFC 9002 Section 6.2.2).

### 1.4 Terminology

- **Endpoint**: one side of a session.
- **Initiator / responder**: the two session roles. The initiator plays the
  role of the QUIC client for stream and pipe identifiers. Section 5.6 and
  EXTENSIONS.md Section 2 define how roles are determined.
- **Carrier**: an underlying reliable transport object, such as a TCP
  connection, a TLS connection, a WebSocket, an HTTP request body or an HTTP
  response body.
- **Pipe**: a unidirectional, reliable, ordered byte stream between the two
  endpoints, provided by a carrier. Bytes written to a pipe arrive in order or
  not at all; a pipe never loses or reorders bytes while it is alive.
- **Pipe pair**: the two pipes of one attachment in opposite directions: the
  two directions of a bidirectional carrier, or the two carriers of a split
  bootstrap (Section 6.1.1). A pipe pair shares one pipe ID and fails as a
  unit (MULTIPATH.md Section 7.3).
- **Outbound / inbound pipe**: a pipe the local endpoint writes to / reads
  from.
- **Fate group**: a set of pipes that are expected to fail together, for
  example two HTTP streams on one HTTP/2 connection (MULTIPATH.md Section 2.2).
- **Carrier adapter**: the part of an implementation that connects a
  carrier to a session and reports its events (MULTIPATH.md Section 2.4).
- **Bootstrap pipes**: the pipe or pipe pair over which the session is
  established. Their pipe ID is 0.
- **Record**: the unit of framing on a pipe (Section 3).
- **Position**: the number of bytes of a pipe that the receiving zmux endpoint
  has completely processed, counted from the first byte of the pipe
  (MULTIPATH.md Section 6.1).
- **Multipath session**: a session in which the `zmux_multipath` feature is in
  use (Section 5.5). In any other session there is exactly one pipe in each
  direction.
- **Plain QMux peer**: a peer that sends none of the zmux transport parameters.
- **Ack-eliciting frame**: any frame other than PADDING, PIPE_ACK and
  CONNECTION_CLOSE.
- **Release (of a stream)**: the moment the local application gives up its
  handle to a stream, after which the endpoint will never deliver data or
  events for that stream to the application (Section 7.6).
- **Compliant peer**: a peer that follows every sender requirement of the zmux
  documents.
- **Control frame**: any frame other than STREAM, DATAGRAM and PADDING.
- **Memory budget**: a local bound on the memory that all sessions of a
  listener (or a session without a listener) may commit to buffers and
  window growth; it is policy of the endpoint, not a protocol value.

### 1.5 Document map

| Document | Content | Status |
|---|---|---|
| SPEC.md (this document) | records, frames, transport parameters, session, streams, flow control, robustness rules, errors, QMux compatibility | normative, core |
| MULTIPATH.md | pipes, lanes, joining, acknowledgement, failure detection, reinjection, pipe removal, sender rules, join security | normative, core |
| RESUME.md | suspension and resumption after all pipes in a direction are lost | normative, core |
| EXTENSIONS.md | role negotiation, GOAWAY, stream preamble, PRIORITY_UPDATE, datagram flows, application error-code conventions, nested sessions and relays, duplicate sessions | normative, negotiated features and conventions |
| STATE_MACHINE.md | stream, pipe and session state machines | normative summary |
| REGISTRY.md, assets/registry.json | every codepoint and registration policy | normative |
| CONFORMANCE.md, TRACES.md, fixtures/ | conformance requirements, invalid inputs, executable traces | normative for conformance claims |
| WIRE_EXAMPLES.md | byte-exact examples | informative, machine-checked |

How an implementation schedules frames across pipes, sizes its windows and
buffers, and presents its API is local and outside these documents, within
the sender rules they state.

---

## 2. Protocol model

### 2.1 Layering

```text
application         streams, datagrams, open info, priorities
zmux session        stream state, flow control, credits, GOAWAY, preamble   (Sections 5-10, EXTENSIONS.md)
zmux pipe layer     pipe IDs, positions, PIPE_ACK, failure detection,        (MULTIPATH.md, RESUME.md)
                    reinjection, joins, resume
records             Size-prefixed records of frames, one record stream per pipe or lane (Section 3)
carriers            TCP, TLS, Unix sockets, WebSocket, HTTP bodies, QUIC streams, in-memory pipes
```

The pipe layer is inert unless the session is a multipath session. A session
that is not a multipath session has no retransmission state: a frame is final
once its record has been passed to the carrier (Section 7.4); with pipe
acknowledgement (MULTIPATH.md Section 3.5) it counts positions on pipe 0 and
sends PIPE_ACK for the sender's queue control only.

### 2.2 Pipes

zmux assumes that each pipe:

1. delivers bytes in the order they were written, without loss or
   duplication, for as long as the pipe is alive;
2. is congestion controlled by its carrier when it crosses a shared network;
3. is protected (integrity, confidentiality, peer authentication) by its
   carrier unless the endpoints deliberately run without protection (Section
   14.2).

These are the properties QMux-02 Section 3.1 requires of its transport,
applied per direction. zmux adds no congestion control, loss-recovery timers
or packet protection of its own. A pipe on a stream of a multi-stream carrier
connection (a QUIC connection, for example) may in addition be carried over
several streams of that connection, its **lanes** (MULTIPATH.md Section 2.5):
each lane has these properties, and there is no order between lanes.

A pipe ends in one of two ways:

- **graceful end**: the writer shuts down its write side at a record
  boundary after a protocol exchange that makes the end expected (Section
  6.5, MULTIPATH.md Section 8), or, in a session without acknowledgement
  (Section 6.4; a session with a plain QMux peer), without any frame when
  its idle timer expires;
- **failure**: anything else, including a carrier error, a carrier reset, a
  TLS alert, an end of stream in the middle of a record, an end of stream that
  the session did not expect in a multipath session, or a failure decision by
  the endpoint (MULTIPATH.md Section 7).

### 2.3 Session

A session is the set of streams, flow-control state and feature state shared
by two endpoints, plus the set of pipes currently attached to it. Records on
different pipes are processed independently; frames carry all the information
needed to apply them in any order (Section 4.1).

**Priority governs sending only.** Stream priorities and pipe priorities
decide what an endpoint sends, on which pipe and lane, and whether it is
copied. A receiver applies what arrives as soon as it arrives and never
reorders or delays received data by priority; a priority signalled by the
peer (the PRIORITY TLV, PRIORITY_UPDATE) is a request about the receiver's
own sending part (EXTENSIONS.md Section 5). The acknowledgements and credit
frames a receiver returns are sending and follow the sender's rules
(MULTIPATH.md Section 6.5). Received data waits only for the stream's own
byte order, for the stream's own preamble when the
preamble is in use (Section 7.2, EXTENSIONS.md Section 4.4), and for
nothing else; flow control limits memory, not the order of processing.

### 2.4 Operating modes

| Mode | When | Pipes | Acknowledgements and retained data |
|---|---|---|---|
| plain QMux | the peer is a plain QMux peer | one pipe pair | none |
| zmux single-pipe | both are zmux endpoints, `zmux_multipath` not in use | one pipe pair, each direction with lanes where its carrier offers them (MULTIPATH.md Section 2.5) | PIPE_ACK on the pipe pair (`zmux_pipe_ack`, MULTIPATH.md Section 3.5), for the sender's queue control only; nothing retained |
| zmux multipath | `zmux_multipath` in use | any number, added and removed at any time, each with lanes where its carrier offers them | PIPE_ACK; unacknowledged information retained |
| zmux multipath with resume | `zmux_multipath` and `zmux_resume` in use | as above, plus a suspended state with zero pipes in a direction | as above |

Multipath and resume are part of the core protocol: every conforming
implementation implements them (CONFORMANCE.md Section 2). They are enabled
per session, because a multipath session must retain unacknowledged data for
its whole lifetime (MULTIPATH.md Section 7.4) and a session that does not need
them should not pay that cost. They are negotiated through transport
parameters so that a zmux endpoint can still talk to a plain QMux peer.

---

## 3. Records

### 3.1 Record format

Every pipe carries a sequence of records, every lane of a pipe that has lanes
its own (MULTIPATH.md Section 2.5). The format is that of QMux-02
Section 3.2:

```text
Record {
  Size (i),
  Frames (..),
}
```

- **Size**: the length in bytes of the Frames field. It does not include the
  Size field itself.
- **Frames**: one or more complete frames (Section 4). A frame MUST NOT span
  records. A frame that omits its own length field (STREAM without the LEN
  bit, DATAGRAM type 0x30) extends to the end of the record.

The first record of a pipe (of a lane) begins at the first byte the carrier
delivers on it. For a pipe joined out of band (MULTIPATH.md Section 5.3), the join
information is not part of the pipe's byte stream.

### 3.2 Record size limit

The `max_record_size` transport parameter (QMux-02 Section 5.2, ID
0x0571c59429cd0845) declares the largest Size value the sender of the
parameter accepts. If absent, the limit is 16382. A value below 16382 is a
TRANSPORT_PARAMETER_ERROR.

- A sender MUST NOT send a record whose Size exceeds the peer's limit. Before
  the peer's transport parameters have been processed, the limit is 16382.
- Wherever this specification compares received data with a limit that the
  receiver "has advertised", it means the largest value the receiver has
  written to a pipe, not values it has decided on but not yet sent.
- A receiver MUST close the session with FRAME_ENCODING_ERROR as soon as it
  has decoded a Size field that exceeds its own limit, without reading the
  record body.
- A receiver MUST close the session with FRAME_ENCODING_ERROR when a record
  has Size 0, or when the last frame of a record is truncated (QMux-02
  Section 3.2).
- The first record of a bootstrap pipe is checked by Section 6.2 before these
  rules apply: a first record whose Size is below 9 or above the receiver's
  limit is a PROTOCOL_VIOLATION.
- A pipe that ends in the middle of a record, even with a graceful end of its
  carrier, has failed. The incomplete record is never processed. In a session
  that is not a multipath session, the session is terminated (Section 6.6);
  in a multipath session the record is discarded and the pipe is failed
  (MULTIPATH.md Section 7.3).

zmux endpoints advertise a `max_record_size` of 65535 and SHOULD NOT advertise
more: a larger limit increases the memory an endpoint commits to partially
received records (QMux-02 Section 12.2) and brings no throughput gain. Senders still build smaller records where Section 3.3 says so.

### 3.3 Record boundaries

Senders choose record boundaries freely within the size limit. Over TLS, a
sender SHOULD build records of at most 16382 bytes of frames so that each
record, with a 2-byte Size, fills one TLS record, and SHOULD write several
records with a single carrier write.

### 3.4 Message-framed carriers

Some carriers deliver messages rather than a byte stream, for example
WebSocket binary messages. On such a carrier, if both ends of the carrier
use the **message framing**, each message carries exactly one record's
Frames field and the Size field is omitted: the message length is the Size.
This is the mapping that existing QMux implementations use for WebSocket.
Both ends of a carrier MUST agree on the framing out of band (it is a
property of the carrier); a carrier that does not
preserve message boundaries uses the byte-stream form of Section 3.1.

With message framing:

- an empty message is a record without frames: the receiver accepts it as
  a no-op and charges it like a record that consists only of PADDING frames
  (Section 9.4.1, NE-1); a sender counts it among those records (Section
  9.4.3 rule 1). The QMux mapping to WebSocket [QMUX-WS] allows it; its only
  effect is that of a received record: it restarts the idle timer where a
  received record does (Section 6.4). zmux endpoints do not send it. On a
  byte-stream carrier a record with Size 0 remains a FRAME_ENCODING_ERROR
  (Section 3.2);
- a message longer than the receiver's `max_record_size` is a record above
  the limit (FRAME_ENCODING_ERROR), and a message of a kind the carrier does
  not carry (for example a WebSocket text message) is a pipe failure;
- the check of Section 6.2 applies to the first message, with the message
  length as the Size (an empty first message is below 9:
  PROTOCOL_VIOLATION);
- a message is processed only when it has arrived completely; a carrier that
  ends inside a message has failed (Section 3.2);
- for positions (MULTIPATH.md Section 2.3), each message counts as the
  number of bytes its record would occupy in the byte-stream form with the
  shortest Size encoding: the length of the message plus the length of the
  variable-length integer encoding of that length (1 for an empty message).
  Sender and receiver count
  the same way, so positions agree whatever the framing.

---

## 4. Frames

### 4.1 General rules

1. Frame encodings are those of RFC 9000 Section 19 for QUIC frames, QMux-02
   Sections 4.2 and 4.3 for QX frames, RFC 9221 Section 4 for DATAGRAM,
   draft-ietf-quic-reliable-stream-reset-11 Section 4 for RESET_STREAM_AT, and
   the zmux documents for zmux frames.
2. The Frame Type field MUST use the shortest possible variable-length
   encoding (RFC 9000 Section 12.4). A receiver MUST close the session with
   PROTOCOL_VIOLATION when a frame type is encoded in more bytes than
   necessary.
3. A receiver MUST close the session with FRAME_ENCODING_ERROR when it
   receives a frame of a type it does not know, a prohibited frame type
   (Section 4.4), or a frame of a zmux feature that is not in use in the
   session (Section 5.5). The exception is DATAGRAM, for which RFC 9221
   Section 3 requires PROTOCOL_VIOLATION.
4. A receiver MUST close the session with FRAME_ENCODING_ERROR when a frame is
   malformed: a field is truncated, a length field exceeds the remaining
   record, a field holds a value outside the range its definition allows, or
   a reserved bit that this specification requires to be zero is set.
5. **Idempotence.** Every frame defined for zmux is idempotent: receiving a
   valid frame more than once, or receiving an outdated frame after a newer
   one, has no effect beyond what the first or newest copy had. Every future
   frame type MUST preserve this property (REGISTRY.md Section 2.4). In a
   multipath session duplicates and reordering are normal (MULTIPATH.md
   Section 7).
6. Frames are processed in the order they appear within a record, and records
   are processed in the order they arrive on their pipe, or on their lane of
   a pipe that has lanes. No ordering exists between pipes, nor between the
   lanes of a pipe.

### 4.2 Frame inventory

| Frame | Type | Defined in | Available |
|---|---|---|---|
| PADDING | 0x00 | RFC 9000 19.1 | always |
| RESET_STREAM | 0x04 | RFC 9000 19.4 | always |
| STOP_SENDING | 0x05 | RFC 9000 19.5 | always |
| STREAM | 0x08-0x0f | RFC 9000 19.8 | always |
| MAX_DATA | 0x10 | RFC 9000 19.9 | always |
| MAX_STREAM_DATA | 0x11 | RFC 9000 19.10 | always |
| MAX_STREAMS | 0x12-0x13 | RFC 9000 19.11 | always |
| DATA_BLOCKED | 0x14 | RFC 9000 19.12 | always |
| STREAM_DATA_BLOCKED | 0x15 | RFC 9000 19.13 | always |
| STREAMS_BLOCKED | 0x16-0x17 | RFC 9000 19.14 | always |
| CONNECTION_CLOSE | 0x1c-0x1d | RFC 9000 19.19 | always |
| RESET_STREAM_AT | 0x24 | reliable-stream-reset-11 Section 4 | peer sent `reset_stream_at` |
| DATAGRAM | 0x30-0x31 | RFC 9221 Section 4 | peer sent `max_datagram_frame_size` > 0 |
| QX_TRANSPORT_PARAMETERS | 0x3f5153300d0a0d0a | QMux-02 4.2 | first frame of a bootstrap pipe only |
| QX_PING | 0x348c67529ef8c7bd-0x348c67529ef8c7be | QMux-02 4.3 | always |
| GOAWAY | 0x1911f43e | EXTENSIONS.md 3 | `zmux_goaway` in use |
| PRIORITY_UPDATE | 0x073d08cf | EXTENSIONS.md 5 | `zmux_priority` in use |
| PIPE_JOIN | 0x1a24de36 | MULTIPATH.md 5 | `zmux_multipath` in use; first frame of a joined pipe only |
| PIPE_ACK | 0x0b2b3eeb | MULTIPATH.md 6 | `zmux_multipath` or `zmux_pipe_ack` in use |
| PIPE_LANE | 0x3a05a1c9 | MULTIPATH.md 5.8 | lanes available toward the receiver (MULTIPATH.md 3.6); first frame of a lane only, its first record holding nothing else but PADDING |
| PIPE_PING | 0x269b43e6 | MULTIPATH.md 6.4 | `zmux_multipath` in use |
| PIPE_ABANDON | 0x23b35b74 | MULTIPATH.md 8 | `zmux_multipath` in use |
| PIPE_STATUS | 0x227b2811 | MULTIPATH.md 9 | `zmux_multipath` in use |

"Available" is the condition under which a sender may send the frame and a
receiver must accept it. RESET_STREAM_AT and DATAGRAM follow the unilateral
QUIC rule: they may be sent to a peer that advertised support for receiving
them. Every zmux feature is in use only when both endpoints advertised it
(Section 5.5).

### 4.3 Core frames: zmux deltas

The semantics of RFC 9000 apply to every core frame. This section lists only
the zmux additions.

**PADDING.** Has no semantics. A record that contains only PADDING frames is
chargeable (Section 9.4). Senders SHOULD NOT send records consisting only of
PADDING frames except as keep-alive or traffic shaping within the limits of
Section 9.4.3.

**STREAM.** Sections 7.3 and 7.5. A sender MUST NOT send a STREAM frame that
carries no data and no FIN bit, except to open a stream when the stream
preamble is not in use. In a session that is not a multipath session, a
sender MUST send each stream's data in offset order with no gap or overlap
(QMux-02 Section 4.1); in a multipath session it MAY send data in any order
and MAY send the same range more than once (MULTIPATH.md Section 7). A frame
whose offset plus length exceeds 2^62-1 is a FRAME_ENCODING_ERROR.

**RESET_STREAM and RESET_STREAM_AT.** An endpoint sends them only as a result
of an application action on the stream or in response to STOP_SENDING (RFC
9000 Section 3.5). The zmux stack never resets a stream on its own initiative
because of a peer protocol violation (Section 9.3); the only stack-initiated
stream terminations are the REJECTED cases of EXTENSIONS.md Sections 3.4
and 7.2 (after GOAWAY, and new peer streams under local memory pressure).
RESET_STREAM_AT is used as specified in reliable-stream-reset-11 Sections 4
and 5; zmux endpoints always advertise `reset_stream_at` (Section 5.3). In a
session that is not a multipath session, a sender MUST have sent every byte
below the Reliable Size before it sends RESET_STREAM_AT, so that the stream's
data precedes its reset on the single pipe as QMux-02 Section 9.2 assumes; a
receiver still processes in-order data below the Reliable Size that arrives
after the reset. In a multipath session the two may arrive in any order.

**STOP_SENDING.** A receiver of STOP_SENDING for a sending part in the "Ready"
or "Send" state MUST respond with RESET_STREAM or RESET_STREAM_AT carrying
the same error code (RFC 9000 Section 3.5). Once a sending part has
received STOP_SENDING, the endpoint MUST NOT send further STREAM frames
carrying new data or a FIN for that stream, even if the application's write
was already in progress; the stream ends with the reset. If the sending part
had already sent RESET_STREAM_AT with a Reliable Size above the data it has
sent, the response reduces it: RESET_STREAM, or RESET_STREAM_AT with a
Reliable Size not above the data already sent (reliable-stream-reset-11
Section 5), so that no new data is owed.

**Final size of a reset.** The Final Size of RESET_STREAM or RESET_STREAM_AT
counts against flow control like data (RFC 9000 Section 4.5): a sender MUST
NOT send a reset whose Final Size exceeds the peer's limit for that stream,
or makes the session sum exceed the peer's MAX_DATA (Section 8.2). The Final
Size of RESET_STREAM is the amount of data sent. RESET_STREAM_AT needs a
Final Size at least as large as its Reliable Size: when the application asks
for a Reliable Size that the current credit does not cover, the endpoint
keeps sending the data below it as credit arrives and sends RESET_STREAM_AT
only once the credit covers the Reliable Size. If STOP_SENDING arrives
meanwhile, the response above applies, with a Final Size equal to the data
already sent.

**MAX_DATA, MAX_STREAM_DATA, MAX_STREAMS.** Absolute limits (RFC 9000 Sections
4.1 and 4.6). A sender MUST NOT send a value that does not exceed the last
value it sent for the same scope, with one exception, in multipath sessions
only: it may repeat the current value as reinjection (MULTIPATH.md Section
7.4) or as an additional copy on another pipe (MULTIPATH.md Sections 6.5 and
7.5). Such repeats carry the same value and are never chargeable (Section
9.4.1). In a session that is not a multipath session, and towards a plain
QMux peer, there are no repeats. A receiver MUST ignore a value that does not
increase the limit (RFC 9000 Sections 4.1, 4.6 and 13.3).

**DATA_BLOCKED, STREAM_DATA_BLOCKED, STREAMS_BLOCKED.** Advisory (Section
8.4). A receiver MUST close the session with PROTOCOL_VIOLATION when the
limit reported in such a frame exceeds the largest limit it has ever
advertised for that scope: a compliant sender cannot be blocked at a limit it
was never given, and forged increasing limits would otherwise escape the
budget of Section 9.4.

**CONNECTION_CLOSE.** Type 0x1c carries a transport error code (Section
10.2), type 0x1d an application error code. Senders SHOULD keep the Reason
Phrase to at most 1024 bytes of UTF-8. Section 6.5 defines how it is sent and
processed.

**Diagnostic details.** When the stack closes a session with a transport
error (type 0x1c), the Reason Phrase SHOULD say what failed in a form that
both people and tools can read: a short human-readable text, optionally
followed by a line feed (0x0a) and one or more space-separated `key=value`
pairs. Keys are lower-case ASCII; values are decimal integers or `0x`
followed by hexadecimal digits. The defined keys are `stream` (stream ID),
`pipe` (pipe number), `offset`, `length`, `limit` (the limit that was
exceeded), `value` (the offending value of a field), `pos` (byte position in
the pipe, MULTIPATH.md Section 2.3) and `rec` (byte position in the record);
the frame type is the frame's own Frame Type field. For example
`stream data beyond the limit\nstream=4 offset=2048 length=100 limit=2048`.
The whole phrase stays within the 1024 bytes above. This is a use of the
Reason Phrase that RFC 9000 Section 19.19 leaves free, so any QMux endpoint
may receive it. A receiver MUST NOT change its behaviour because of the
phrase; it MAY parse the pairs for logs and error reports, MUST ignore
unknown keys, and treats a phrase whose last line does not
parse as plain text. An application close (0x1d) carries what the
application gave.

### 4.4 Prohibited QUIC frames

The following QUIC version 1 frames MUST NOT be sent. A receiver MUST close
the session with FRAME_ENCODING_ERROR (QMux-02 Section 4): PING (0x01), ACK
(0x02-0x03), CRYPTO (0x06), NEW_TOKEN (0x07), NEW_CONNECTION_ID (0x18),
RETIRE_CONNECTION_ID (0x19), PATH_CHALLENGE (0x1a), PATH_RESPONSE (0x1b),
HANDSHAKE_DONE (0x1e).

Liveness probing uses QX_PING (Section 4.5) and, per pipe, PIPE_PING
(MULTIPATH.md Section 6.4).

### 4.5 QX_PING

QX_PING is defined in QMux-02 Section 4.3. The request type is
0x348c67529ef8c7bd; the response type is 0x348c67529ef8c7be.

- A request MUST carry a Sequence Number strictly greater than that of every
  request previously sent in the session. A receiver MUST close the session
  with PROTOCOL_VIOLATION when a request's Sequence Number is less than or
  equal to that of a previously received request, except in a multipath
  session. There requests travel on different pipes, and a request that has
  stopped being outstanding (below) is followed by a new one, possibly on a
  faster pipe, so a compliant peer's requests can arrive out of order: a
  request whose Sequence Number does not exceed that of a previously
  received request is **outdated** and is ignored, neither answered nor
  chargeable (Section 9.4.1). Every other session, and therefore every
  session with a plain QMux peer, keeps the order check.
- A response echoes a received request's Sequence Number. When several
  requests are pending, an endpoint MUST answer only the largest one, with
  one response (this bounds the response state, Section 9.2).
- A receiver MUST close the session with PROTOCOL_VIOLATION when a response
  carries a Sequence Number greater than the largest request it has sent.
- A sender MUST NOT have more than one request outstanding, and MUST NOT send
  more requests than allowed by Section 9.4.3. A request stops being
  outstanding when a response with an equal or larger Sequence Number
  arrives, when the pipe that carried the request fails, or max(1 s, 3 x
  SRTT) after it was sent, whichever comes first: in a multipath session the
  request or its response may be lost with a pipe. A protocol step that waits
  for a response (the GOAWAY fence of EXTENSIONS.md Section 3.3) then sends a
  new request. Receivers do not enforce this rule; the budget of Section 9.4
  bounds abuse.
- In a multipath session a response MAY be sent on any outbound pipe. Pipe
  liveness does not depend on QX_PING (MULTIPATH.md Section 7.1).

A QX_PING request is chargeable (Section 9.4) only when it arrives while the
receiver still owes a response to an earlier request, that is, when one of
the two will be superseded without an answer; a request is answered once its
response has been produced for a carrier (handed to it, or committed to a
pipe). A request that is answered has an effect and costs its sender a
record per response, so it is neutral. Responses are chargeable only when
they are outdated (NE-8). A peer that probes its RTT at a fixed cadence,
whatever its number of requests outstanding, is therefore charged only when
two of its requests arrive before the first is answered; a flood of
requests within one read is charged for all but one, and a receiver whose
writing is blocked for long (the peer reads nothing) charges every such
request and may close the session.

---

## 5. Transport parameters

### 5.1 The QX_TRANSPORT_PARAMETERS frame

QX_TRANSPORT_PARAMETERS (QMux-02 Section 4.2) carries the endpoint's
transport parameters, encoded as in RFC 9000 Section 18:

```text
QX_TRANSPORT_PARAMETERS Frame {
  Type (i) = 0x3f5153300d0a0d0a,
  Length (i),
  Transport Parameters (..),
}
```

The 8-byte encoding of the type is `ff 51 53 30 0d 0a 0d 0a`.
QMux-02 renders these bytes as "\xffQMX\r\n\r\n"; that rendering is wrong (the
second to fourth bytes are ASCII "QS0"), and the numeric value
0x3f5153300d0a0d0a is the normative one.

Rules:

1. Each endpoint MUST send QX_TRANSPORT_PARAMETERS as the first frame of the
   first record on each of its outbound bootstrap pipes (on lane 0, the
   bootstrap carrier itself), as soon as the pipe
   is writable, without waiting for the peer (QMux-02 Section 4.2).
2. QX_TRANSPORT_PARAMETERS MUST NOT appear anywhere else. A receiver MUST
   close the session with PROTOCOL_VIOLATION when the first frame of an
   inbound bootstrap pipe is not QX_TRANSPORT_PARAMETERS, or when a
   QX_TRANSPORT_PARAMETERS frame appears in any other position, on any
   joined pipe or on any lane. The one exception is a record that a shared listener sent
   before it knew the carrier was a join: a joiner discards a
   QX_TRANSPORT_PARAMETERS record that leads the inbound direction of a new
   attachment without processing it (MULTIPATH.md Section 5.7 step 3).
3. Until it has processed the peer's transport parameters, an endpoint MUST
   NOT send any frame other than QX_TRANSPORT_PARAMETERS, PADDING, QX_PING
   and CONNECTION_CLOSE, except the early data of Section 6.1.2: STREAM
   frames, preceded by a PIPE_ACK fence toward a zmux peer, sent under
   remembered parameters in carrier early data as QMux-02 Sections 4.2 and
   8.2 allow, or under the zmux floor toward a peer that its application
   declares to be a zmux endpoint. It sends no other stream data before it
   knows the peer's flow-control limits.

### 5.2 Processing rules

1. Unknown transport parameters MUST be ignored (RFC 9000 Section 7.4.2).
2. A parameter MUST NOT appear more than once. A receiver MUST close the
   session with TRANSPORT_PARAMETER_ERROR on a duplicate.
3. A receiver MUST close the session with TRANSPORT_PARAMETER_ERROR when it
   receives one of the QUIC version 1 parameters that QMux-02 Section 5.1
   prohibits: original_destination_connection_id (0x00),
   stateless_reset_token (0x02), max_udp_payload_size (0x03),
   ack_delay_exponent (0x0a), max_ack_delay (0x0b), disable_active_migration
   (0x0c), preferred_address (0x0d), active_connection_id_limit (0x0e),
   initial_source_connection_id (0x0f), retry_source_connection_id (0x10).
4. A receiver MUST close the session with TRANSPORT_PARAMETER_ERROR when a
   known parameter's value is malformed (for example trailing bytes after a
   variable-length integer) or out of the range its definition allows.
5. Transport parameters are unilateral declarations and never change during a
   session.
6. **GREASE.** Each endpoint MUST include at least one transport parameter
   with an identifier of the form 31 * N + 27 (RFC 9000 Section 18.1), chosen
   with a random N for each session, carrying 0 to 16 random bytes. Receivers
   ignore these like any unknown parameter.

### 5.3 Core parameters

| Parameter | ID | Absent means | zmux endpoints send | Notes |
|---|---|---|---|---|
| max_idle_timeout | 0x01 | no idle timeout | 30000 (ms) | Section 6.4 |
| initial_max_data | 0x04 | 0 | 1048576 | session receive window |
| initial_max_stream_data_bidi_local | 0x05 | 0 | 262144 | |
| initial_max_stream_data_bidi_remote | 0x06 | 0 | 262144 | |
| initial_max_stream_data_uni | 0x07 | 0 | 262144 | |
| initial_max_streams_bidi | 0x08 | 0 | 256 | Section 5.3.1 |
| initial_max_streams_uni | 0x09 | 0 | 16 by default, raised to 256 with MAX_STREAMS when the application first accepts a unidirectional stream (Section 8.5 rule 4); the configured value, at once, when the application sets `max_incoming_uni_streams` | Section 5.3.1 |
| reset_stream_at | 0x1d | not supported | always (empty value) | reliable-stream-reset-11 Section 3 |
| max_datagram_frame_size | 0x20 | 0 (not supported) | its own `max_record_size` (65535) when datagrams are enabled | EXTENSIONS.md Section 6 |
| max_record_size | 0x0571c59429cd0845 | 16382 | 65535 | Section 3.2 |

The "zmux endpoints send" column gives the RECOMMENDED values of a
general-purpose implementation; applications may configure others, at or
above the early-data floor of Section 6.1.2. The
flow-control values are starting points that receivers may grow as data
flows. A zmux endpoint MUST send the initial flow-control and stream-limit
parameters explicitly, because their absent value is 0.

#### 5.3.1 Why 256 concurrent streams

Credit is returned only when the application releases a stream (Section
8.5), so `initial_max_streams_bidi` is a hard cap on the number of streams
the peer can have open at once; a cap of 100 (the HTTP/2 minimum) would
make sessions with several hundred concurrent streams wait for credit under
normal load. Receive buffers are bounded by the buffer cap of Section 8.3
rule 4 rather than by the stream count, and Rapid-Reset-style attacks are
not made easier by a larger cap, because credit returns only after the
application releases a stream. Unidirectional credit starts at 16 by default (Section 8.5 rule 4).

### 5.4 zmux parameters

zmux-allocated parameters use identifiers at or above 2^14. Plain QMux peers
ignore them (QMux-02 Section 5.1).

| Parameter | ID | Defined in |
|---|---|---|
| zmux_role | 0x17240207 | EXTENSIONS.md 2 |
| zmux_goaway | 0x04528a5f | EXTENSIONS.md 3 |
| zmux_preamble | 0x0439e07a | EXTENSIONS.md 4 |
| zmux_priority | 0x31e7139d | EXTENSIONS.md 5 |
| zmux_datagram_flows | 0x0af391ed | EXTENSIONS.md 6 |
| zmux_multipath | 0x30dc2978 | MULTIPATH.md 3 |
| zmux_ack_threshold | 0x2762c7e5 | MULTIPATH.md 3.2 |
| zmux_pipe_ack | 0x34b47621 | MULTIPATH.md 3.5 |
| zmux_max_ack_delay | 0x17fa1ed6 | MULTIPATH.md 3.2 |
| zmux_max_lanes | 0x27856ef0 | MULTIPATH.md 3.6 |
| zmux_resume | 0x0d5778ce | RESUME.md 3 |

### 5.5 Features in use

A zmux feature is **in use** in a session when both endpoints sent the
parameter that announces it, and any additional condition in the feature's
definition holds. When a feature is not in use, neither endpoint sends its
frames and neither applies its rules; its frames are unknown frames (Section
4.1 rule 3).

| Feature | In use when |
|---|---|
| role negotiation | both sent `zmux_role` |
| GOAWAY | both sent `zmux_goaway` |
| stream preamble | both sent `zmux_preamble` (each also sends `reset_stream_at`) |
| PRIORITY_UPDATE | both sent `zmux_priority` |
| datagram flows | both sent `zmux_datagram_flows` |
| multipath | both sent `zmux_multipath` and their secret flags select a secret source (MULTIPATH.md 10.1) |
| pipe acknowledgement | both sent `zmux_pipe_ack` and multipath is not in use (MULTIPATH.md 3.5) |
| lanes, toward one endpoint | that endpoint sent `zmux_max_lanes` greater than 1, and multipath or pipe acknowledgement is in use (MULTIPATH.md 3.6; one-directional: the other endpoint may open lanes, the announcing one only as the other's value allows) |
| resume | multipath is in use, both sent `zmux_resume`, and at least one Session Locator is non-empty (RESUME.md 3) |

RESET_STREAM_AT and DATAGRAM keep their unilateral QUIC meaning (Section 4.2).
zmux endpoints send `zmux_goaway`, `zmux_preamble`, `zmux_priority`,
`zmux_pipe_ack`, `zmux_max_lanes` and `reset_stream_at` in every session
(`zmux_preamble`, `zmux_pipe_ack` and `reset_stream_at` are required by
Section 6.1.2); the other zmux parameters depend on configuration. Between
two zmux endpoints, pipe acknowledgement or multipath is therefore always in
use: every such session is a session with acknowledgement (Section 6.4).

A parameter that a feature definition requires to accompany another one
(for example `zmux_ack_threshold` and `zmux_max_lanes` require `zmux_multipath` or `zmux_pipe_ack`) MUST NOT be sent
alone; a receiver MUST close the session with TRANSPORT_PARAMETER_ERROR when
it is. The check is on the parameters the peer sent, not on the features in
use: a companion sent together with the parameter it requires is valid even
when that feature does not end up in use (multipath without a common secret
source, pipe acknowledgement that the other endpoint did not announce); its
value is still checked, and it has no effect unless a feature it
accompanies is in use.

### 5.6 Roles

Unless role negotiation is in use, the initiator is the endpoint that
established the bootstrap carrier: the TCP or TLS client, or the HTTP client
for HTTP carriers. The other endpoint is the responder. EXTENSIONS.md Section
2 defines `zmux_role` for symmetric connections, such as a TCP
simultaneous open after NAT traversal, where no transport role exists.

The initiator uses the stream identifiers of the QUIC client (RFC 9000
Section 2.1) and even pipe identifiers (MULTIPATH.md Section 4.1).

---

## 6. Session lifecycle

### 6.1 Establishment

1. The initiator establishes the bootstrap carrier, either one bidirectional
   carrier (pipe pair 0) or, for carriers that are unidirectional, one
   outbound and one inbound carrier paired as Section 6.1.1 describes. The
   core treats both cases as pipe pair 0. On QUIC (and on HTTP/3 or
   WebTransport over it) pipe pair 0 is always one bidirectional stream.
2. Each endpoint sends QX_TRANSPORT_PARAMETERS (Section 5.1).
3. The session is **established** at an endpoint once it has sent its own
   parameters and processed the peer's. Role negotiation, feature selection
   and, for multipath sessions, the session secret (MULTIPATH.md Section 10)
   are fixed at that moment.
4. An endpoint MUST abandon establishment when it is not established within
   its handshake timeout (default 10 s). It closes the carrier without
   sending CONNECTION_CLOSE and reports a handshake timeout locally.

Over TLS, the responder SHOULD send its QX_TRANSPORT_PARAMETERS in the same
flight as its TLS Finished message so that the initiator does not wait an
extra round trip (QMux-02 Section 4.2). A listener that accepts both new
sessions and joins on one endpoint does the same: a joiner discards a
QX_TRANSPORT_PARAMETERS record that leads the inbound direction of a join
carrier (Section 13.2 rule 7).

#### 6.1.1 Pairing a split bootstrap

Split bootstrap is an optional feature (CONFORMANCE.md Section 2, C-10). It
serves only deployments that have no bidirectional carrier at all; an
implementation that does not offer it accepts only a bidirectional bootstrap
carrier, and its listener closes a carrier that arrives with a pairing token.
Nothing on the wire depends on whether an endpoint offers it.

When the only carriers available are unidirectional (for example an HTTP POST
request body for the uplink and an HTTP GET response body for the downlink),
the two carriers that form pipe pair 0 are paired with a **pairing token**:

1. The initiator chooses 16 random bytes, the pairing token, and its carrier
   adapter sends it out of band with both carriers, in a field the two
   adapters agree on, never where intermediaries commonly log it.
2. The initiator writes its first record (QX_TRANSPORT_PARAMETERS) on the
   uplink as soon as it is writable.
3. The responder's listener holds the first carrier of a pair, with at most
   one record of data, until the carrier with the same token and the opposite
   direction arrives, and then starts the session on the two carriers. It
   holds at most 64 unpaired carriers, and at most 8 from one source, and
   drops each after the handshake timeout; an uplink carrier must deliver its
   first record within the first-record timeout of MULTIPATH.md Section 5.6.
   A token is used for exactly one pair; a third carrier with the same token
   is closed.
4. The responder then writes its QX_TRANSPORT_PARAMETERS on the downlink.

The pairing token is a bearer secret until the session is established:
anyone who learns it before the responder pairs the carriers can take the
place of either carrier. Carriers that use a split bootstrap MUST therefore
be confidential end to end (TLS that terminates at the responder), and an
initiator that has any bidirectional carrier SHOULD bootstrap on it and add
unidirectional pipes with authenticated joins (MULTIPATH.md Section 5)
instead. The session secret derived after establishment (MULTIPATH.md
Section 10) protects every later join, but not the bootstrap pair itself.

#### 6.1.2 Early data

An endpoint may send stream data before it has processed the peer's
transport parameters: its **early data**, which saves the round trip the
first stream byte would otherwise wait for. Early data is sent under an
**assumed parameter set**, which stands for the peer's parameters until the
real ones have been processed, as RFC 9000 Section 7.4.1 lets remembered
parameters stand for them. There are two such sets, and an endpoint uses at
most one of them in a session:

1. **Remembered parameters** (QMux-02 Sections 4.2 and 8.2): on a carrier
   that resumes a TLS 1.3 session with early data (TLS early data, or QUIC
   0-RTT for a pipe on a QUIC stream), a client MAY send its first records
   as carrier early data, under the server's parameters remembered from the
   session that issued the ticket, exactly as RFC 9000 Section 7.4.1
   describes. A client that cannot store data with a ticket MAY instead
   remember the parameters of the most recent session with the same server
   identity (the name it verified) and use them with any ticket of that
   identity; this relies on the server keeping its parameters stable
   (Replay, below). Without a verified server name there is no such
   identity and no such memory. Toward a plain QMux peer this is QMux-02's
   own 0-RTT and nothing more. Remembered values are never used outside carrier early
   data.
2. **The zmux floor**: when the endpoint uses no remembered parameters and
   its application declares the peer to be a zmux endpoint, it MAY send
   early data under the floor below, which every zmux endpoint advertises
   at least. Nothing on the wire tells an endpoint beforehand that the peer
   is a zmux endpoint (ALPN identifiers do not distinguish zmux, Section
   6.2), so the declaration is the application's. A declaration that may be
   wrong (for example one inferred from an earlier session with the same
   peer) fails, when it is wrong, as the Outcome below describes.

**The floor.** Every zmux endpoint, that is every endpoint that sends a
parameter of Section 5.4, MUST send `zmux_preamble`, `zmux_pipe_ack` and
`reset_stream_at`, and MUST advertise at least:

| Parameter | Floor |
|---|---|
| `initial_max_data` | 16384 |
| `initial_max_streams_bidi` | 4, or 0 for an endpoint that accepts no bidirectional streams |
| `initial_max_stream_data_bidi_remote` | 4096, unless `initial_max_streams_bidi` is 0 |
| `zmux_preamble` (Maximum Preamble Size) | 1024 |

Under the floor an endpoint sends data on at most the first four
bidirectional streams it opens, at most 4096 bytes of each, preamble
included, and at most 16384 in total; `max_record_size` is 16382 (Section
3.2). Early data commits no more memory than the parameters already do.
Accepting it never obliges an endpoint to keep a session: a listener may
close the session (CONNECTION_REFUSED, or ZMUX_EXCESSIVE_LOAD for abuse),
but never discards early data while keeping the session open. Receivers do
not check the floor, except an endpoint that sent early data (Outcome,
below).

**Remembered zmux parameters.** RFC 9000 Section 7.4.1 requires every
transport parameter to say whether it is stored for 0-RTT:

| Parameter | Stored for 0-RTT | A server that accepts carrier early data |
|---|---|---|
| `max_record_size`, `zmux_preamble` | mandatory | sends a value at least the remembered one |
| `zmux_pipe_ack`, `reset_stream_at` | mandatory | sends it when it was remembered |
| `max_datagram_frame_size`, `zmux_datagram_flows`, `zmux_goaway`, `zmux_priority`, `max_idle_timeout` | optional; early data does not use them | no constraint |
| `zmux_role`, `zmux_multipath`, `zmux_ack_threshold`, `zmux_max_ack_delay`, `zmux_max_lanes`, `zmux_resume` | prohibited: their values belong to one session or are taken fresh from the handshake | no constraint |

**Sending.** Early data, under either set, obeys all of the following:

1. The endpoint's role does not depend on the peer: it has a transport role
   (Section 5.6) and sends no `zmux_role`, or sends its transport role in it
   (EXTENSIONS.md Section 2.3). An endpoint that sends `zmux_role` with Role
   auto sends no early data.
2. It consists of STREAM frames that open and carry the sending parts of
   bidirectional or, under remembered parameters, unidirectional streams of
   the endpoint, within the assumed set's stream counts, windows and record
   size; the FIN bit is allowed. When the assumed set contains
   `zmux_preamble`, every stream starts with its preamble (EXTENSIONS.md
   Section 4.2), no larger than the set's Maximum Preamble Size. No other
   frame is added except those of Section 5.1 rule 3 and the PIPE_ACK of
   rule 3.
3. When the assumed set contains zmux parameters (always under the floor), a
   PIPE_ACK frame with one entry, Pipe ID 0, Lane 0, Position 0, ACK Delay 0
   (`8b 2b 3e eb 01 00 00 00 00`, 9 bytes), precedes the first early STREAM
   frame on the pipe, normally directly after QX_TRANSPORT_PARAMETERS in the
   same record. Under a remembered set without zmux parameters (a plain
   QMux peer) there is no PIPE_ACK and no preamble.
4. It is sent on the bootstrap pipe (lane 0) only; joins carry none, and a
   carrier-bound join is never carrier early data (MULTIPATH.md Section
   10.5).

The PIPE_ACK is a **fence**. A peer for which PIPE_ACK is in use (pipe
acknowledgement or multipath; every zmux peer, Section 5.5) ignores an
entry with Position 0 (MULTIPATH.md Section 6.2). Any other peer, every
plain QMux peer included, cannot know the frame's length and closes the
session with FRAME_ENCODING_ERROR at it (RFC 9000 Section 12.4, which
QMux-02 Section 4 inherits; Section 4.1 rule 3 for a zmux receiver), so it
processes no frame after it and never delivers a preamble as stream data.

**Receiving.** A receiver applies no rule of its own to early data that
arrives after the carrier handshake: the sender's parameters lead its first
record and are processed first, and its data is checked against the limits
the receiver advertised, which cover the floor. A zmux endpoint that
advertises `initial_max_streams_bidi` 0 closes the session with
STREAM_LIMIT_ERROR at the first early STREAM frame sent under the floor,
before it delivers anything.

**Outcome.** When it processes the peer's parameters, an endpoint that sent
early data:

1. if the assumed set contained zmux parameters and PIPE_ACK is not in use
   toward the peer, or if the early data was sent under the floor and the
   peer's `initial_max_streams_bidi` is 0, knows that the peer processed
   none of its early data and closed, or will close, the session. It closes
   the session with NO_ERROR and reports "early data rejected" to its
   application; every stream it opened fails with that error, and may be
   opened again on a new session, the peer having processed none of them;
2. otherwise, if the peer's parameters are below the assumed set (below the
   floor, or below remembered values that the table above or RFC 9000
   Section 7.4.1 says the server must not reduce), closes the session with
   TRANSPORT_PARAMETER_ERROR; the peer may have processed part of the early
   data;
3. otherwise continues: the early data counts against the peer's limits like
   data sent after them, and no credit is ever taken back. From then on the
   peer's parameters apply, except that bytes still sent as carrier early
   data stay within the remembered values.

Until it knows that its early data stands (the peer's parameters are
processed, and carrier early data was accepted), an endpoint keeps the
early frames' content. If the carrier rejects its early data (TLS 1.3 early
data or QUIC 0-RTT not accepted), none of it reached the peer: the endpoint
treats everything it sent as early data, stream data, FIN bits and stream
resets alike, as never sent, writes
QX_TRANSPORT_PARAMETERS again as the first record once the handshake is
complete, and sends the early streams' data again from offset 0 only after
it has processed the peer's parameters; the streams do not fail. The same
holds for everything sent between the rejection and that point: nothing is
final until it has been sent again after the peer's parameters. An
endpoint that sent `zmux_multipath` keeps its early frames as
unacknowledged information until the peer's parameters are processed, and
discards them then unless multipath is in use (Section 9.6). Establishment
that fails for any other reason fails the early streams with the session
error, with no guarantee about what the peer processed.

**Replay.** Carrier early data can be replayed by an attacker (RFC 8446
Section 8): a replayed open, for example a proxied connection with
OPEN_INFO, would be repeated. Early data under the floor travels after the
carrier handshake and is never replayed. Therefore:

- a client sends carrier early data only when its application allows its
  early streams to be replayed, and never under the floor;
- a server accepts carrier early data only when its application allows it,
  reports to its application every stream whose opening frame arrived as
  carrier early data as possibly replayed until the handshake has
  completed, and applies the anti-replay measures of RFC 8446 Section 8 that
  its TLS library offers; otherwise it rejects carrier early data;
- a server that accepts carrier early data sends transport parameters that
  satisfy the remembered values (RFC 9000 Section 7.4.1 and the table
  above), as QMux-02 Section 8.2 requires. Since a client may have
  remembered the parameters of any earlier session with the same server
  identity, the servers behind an identity keep the values the table marks
  mandatory stable across the tickets they issue for it, or bind acceptance
  to them (for example a configuration version stored in the ticket); a
  server that lowers any of them, or cannot tell whether a ticket predates a
  change, rejects carrier early data for those tickets (without a bound
  version: every older ticket) and never accepts early data under limits it
  no longer meets. A value at or above every value it has issued tickets
  under needs nothing.

### 6.2 Identification and version

**Over TLS**, the endpoints MUST agree on the application protocol with ALPN
[RFC7301], as QMux-02 Section 8.1 requires. zmux does not identify itself
through ALPN: zmux features are negotiated with transport parameters, so a
zmux endpoint presents the same ALPN identifiers as a QMux endpoint of the
same application.

- ALPN identifiers are configurable. The default identifier is `qmux-02`, the
  bare QMux draft-02 identifier, used for an application agreed out of band.
  Applications with their own identity configure their own identifiers: the
  `qmux-02.<application>` form, which QMux implementations use to carry an
  application protocol (for example `qmux-02.myapp-v4`), or a name in the
  style of QMux-02 Section 8.1.
- Because QMux draft-01 and draft-02 use identical codepoints, each
  configured identifier has at most one **draft-01 alias**, derived exactly
  as follows, with byte-exact, case-sensitive comparison (RFC 7301):
  1. `qmux-02` has the alias `qmux-01`;
  2. an identifier that begins with `qmux-02.` has the alias in which that
     prefix is replaced by `qmux-01.`, with the same application part;
  3. any other identifier that ends in `-02` has the alias in which that
     suffix is replaced by `-01`;
  4. any other identifier has no alias.

  A server selects, in its own order of preference, the first configured
  identifier that the client offered either as it is or as its alias; when
  it selected an alias, the session follows QMux-02 semantics all the same.
  A client offers its configured identifiers and MAY append their aliases
  after them. No other form is an alias: draft-00 forms (`qmux-00`,
  `qmux-00.<application>`), whose record format differs, are never accepted
  through this rule.

  | Configured | Also accepted | Not accepted (examples) |
  |---|---|---|
  | `qmux-02` | `qmux-01` | `qmux-00`, `qmux-01.x`, `qmux` |
  | `qmux-02.myapp-v4` | `qmux-01.myapp-v4` | `qmux-01.myapp-v3`, `qmux-00.myapp-v4`, `myapp-v4`, `QMUX-01.myapp-v4` |
  | `h3qx-02` | `h3qx-01` | `h3qx-00`, `h3qx` |
  | `myapp-02` | `myapp-01` | `myapp-1` |
  | `myapp` | none | `myapp-01` |
- Carriers that negotiate the application protocol by other means than ALPN
  use the same identifiers and the same alias rule; how the identifier
  travels is defined by the carrier adapter.
- The TLS client MUST abort the handshake when no protocol is negotiated,
  with a no_application_protocol alert (QMux-02 Section 8.1) where its TLS
  library can send one. A library that learns the outcome only after the
  handshake and cannot then send a fatal alert closes the connection before
  sending any record on it, with the strongest signal it has (a fatal alert
  if possible, otherwise close_notify and closing the transport); the server
  treats it like a failed handshake.

**Without TLS**, the first bytes identify the protocol. A receiver MUST
validate the first record of a bootstrap pipe incrementally, byte by byte as
the bytes arrive, and MUST reject the pipe as soon as the received prefix
cannot begin a valid first record:

- the Size field, once complete, MUST be at least 9 and at most the
  receiver's own `max_record_size` (a peer that has processed the
  receiver's parameters may send first records larger than 16382, for example
  a split-bootstrap responder or a QMux peer that coalesces frames after its
  parameters);
- the next 8 bytes MUST be `ff 51 53 30 0d 0a 0d 0a`.

Only this prefix is checked; the rest of the first record is processed as
usual. A listener that also accepts pipe joins applies the same check with
the alternative PIPE_JOIN prefix of MULTIPATH.md Section 5.5.

The check applies over TLS too. When it fails, the endpoint closes the session
at once with PROTOCOL_VIOLATION (the QMux-02 Section 4.2 error for a first
frame that is not QX_TRANSPORT_PARAMETERS): it writes CONNECTION_CLOSE after
its own QX_TRANSPORT_PARAMETERS, as a courtesy to a broken QMux peer, and
reports "not a zmux or QMux peer" to the application. This holds for every
carrier on which the endpoint would have started a session, including a
listener's carrier that matched neither alternative. A carrier that can only
carry a join (a listener that accepts no new sessions) is closed without
writing anything (MULTIPATH.md Section 5.4). Consequences:

- A legacy-format endpoint sends the magic `ZMUX` (`5a 4d 55 58`). A receiver
  of this specification decodes `5a 4d` as Size 6733 and rejects the pipe at
  the third byte (`55`), without waiting for more data or a timeout.
- An endpoint of this specification starts with a Size whose value is at
  least 9. A legacy endpoint compares it with `Z` (0x5a) and fails at its
  first byte. A Size of exactly 0x5a would still fail at the second byte,
  which a legacy endpoint compares with `M`.
- HTTP/1.1 and HTTP/2 prefaces fail within the first three bytes (QMux-02
  Section 4.2 chose the type value for this purpose).

### 6.3 Session states

STATE_MACHINE.md Section 4 defines the session state machine: ESTABLISHING,
ACTIVE, DRAINING (after GOAWAY), SUSPENDED (resume), CLOSING and CLOSED.

### 6.4 Idle timeout and keep-alive

The effective idle timeout is the minimum of the two `max_idle_timeout`
values, ignoring a value of 0; when both are 0 or absent there is none (RFC
9000 Section 10.1). The idle timer starts when the session is established.

A **session with acknowledgement** is one in which multipath or pipe
acknowledgement (MULTIPATH.md Section 3.5) is in use: every session between
two zmux endpoints (Section 5.5). A **session without acknowledgement** is
one with a plain QMux peer, or with a peer that sent neither
`zmux_pipe_ack` nor a usable `zmux_multipath`.

- In a session without acknowledgement, the idle timer restarts when a
  record has been completely received or completely passed to the carrier
  (QMux-02 Section 7.1), with one exception: passing a record that carries
  only QX_PING requests and PADDING to the carrier does not restart it, so
  that an endpoint's own keep-alive cannot keep a session with a vanished or
  hung peer alive (Section 13.2 rule 8). RFC 9000 Section 10.1 (restart on
  the first ack-eliciting packet sent after a receive) is not adopted: on a
  carrier without visible acknowledgements it would idle out a peer that
  only receives.
- In a session with acknowledgement, the idle timer counts only application
  activity, and a sent record counts only once the peer has acknowledged it.
  The timer restarts when a record that carries an **activity frame** has
  been completely received on any pipe, or when a PIPE_ACK newly
  acknowledges a record that carried one; activity frames are STREAM,
  RESET_STREAM, RESET_STREAM_AT, STOP_SENDING, DATAGRAM and QX_PING
  (requests and responses). PIPE_ACK, PIPE_PING and the other pipe-layer and
  control frames never keep the session alive, or the probing of idle pipes
  (MULTIPATH.md Section 7.1) would keep every multipath session alive
  forever. The peer acknowledges an activity record within its
  `zmux_max_ack_delay` (MULTIPATH.md Section 6.3).
- While a session is SUSPENDED, the idle timer is stopped and the resume
  grace timer runs instead (RESUME.md Section 4).

When the idle timer expires:

- in a session without acknowledgement, the endpoint shuts down the write
  side of its outbound pipe without sending any frame, then proceeds as in
  Section 6.5 step 3 (QMux-02 Section 7.2);
- in a session with acknowledgement, the endpoint sends CONNECTION_CLOSE with
  the transport code ZMUX_IDLE_TIMEOUT on every live outbound pipe (on lane
  0 of a pipe with lanes, without the wait of Section 6.5 step 1, which
  applies to closes without error and application closes) and proceeds as
  in Section 6.5.

Either way the endpoint reports an idle timeout to its application, and the
peer reports an idle timeout caused by the peer: after the graceful shutdown
below in a session without acknowledgement, or on receiving
ZMUX_IDLE_TIMEOUT in a session with acknowledgement.

How a peer's pipe ends therefore depends on the kind of session:

- In a session without acknowledgement, an endpoint that observes the peer
  gracefully shut down its write side of the bootstrap pipe at a record
  boundary, without having received CONNECTION_CLOSE, treats it as the
  peer's idle-timeout close (QMux-02 Section 7.3): it enters CLOSING, shuts
  down its own write side gracefully without sending any frame, and reports
  an idle timeout caused by the peer. An end in the middle of a record is a
  failure instead (Section 3.2).
- In a session with acknowledgement, a zmux peer always closes with
  CONNECTION_CLOSE, so a graceful end of the peer's pipe, at record
  boundaries on every lane, while no CONNECTION_CLOSE has been sent or
  received (the session is ACTIVE or DRAINING) is not expected (MULTIPATH.md
  Section 7.3) and is a pipe failure. Without multipath it ends the session
  as Section 6.6 says, reported as pipes lost; in a multipath session
  MULTIPATH.md Section 7 handles it. A lane that ends while other lanes of
  its pipe are still open decides nothing until every lane has ended.

CONNECTION_CLOSE with ZMUX_IDLE_TIMEOUT is thus the peer's idle timeout, any
other CONNECTION_CLOSE is the peer's close with that code, and a pipe that
ends without one is a pipe failure.

An endpoint that wants to keep an idle session alive sends a QX_PING request
when the idle timer has not restarted for its keep-alive period. The
keep-alive timer counts exactly the same activity as the idle timer, so
probing cannot postpone a keep-alive past the idle timeout, and the request
itself restarts neither: the response arriving or, in a session with
acknowledgement, the PIPE_ACK that acknowledges the request's record does. A keep-alive that is never answered therefore lets the idle timeout
close the session. The keep-alive period MUST be less than the effective
idle timeout.

### 6.5 Closing

**Initiating a close.** An endpoint that closes a session:

1. MUST send a CONNECTION_CLOSE frame (0x1c for a transport error, 0x1d for
   an application close) on every outbound pipe that is not failed, and MUST
   flush it to the carrier before step 2, waiting at most the linger timeout
   of step 3 for a pipe that does not accept bytes. It MUST NOT send any other
   frame after CONNECTION_CLOSE on that pipe.
   In a session without multipath whose pipe has lanes (MULTIPATH.md
   Section 2.5), the CONNECTION_CLOSE of a close without error and of an
   application close goes on lane 0 only once a PIPE_ACK covers every
   record the endpoint wrote on its other lanes, waiting at most the linger
   timeout of step 3, and less if the peer's CONNECTION_CLOSE arrives
   first; it keeps processing what it receives meanwhile. There is no order
   between lanes, and a close that overtook their records would make the
   peer discard data that Section 7.4 counts as delivered.
2. MUST then shut down the write side of every outbound pipe gracefully (for
   example a TCP FIN or a TLS close_notify), on every lane of a pipe with
   lanes, never abortively at this point.
3. SHOULD keep reading every inbound pipe until the peer shuts down its write
   side or the **linger timeout** expires, discarding what it reads without
   processing it (QMux-02 Section 7.2). The linger timeout is
   min(2 s, max(250 ms, 3 x SRTT)), with SRTT the largest smoothed RTT known
   for the session, or 333 ms when none is known. It starts when step 1 is
   complete, that is when CONNECTION_CLOSE has been handed to the carrier of
   every outbound pipe (or step 1's own wait has ended), not when the
   application asked to close; the two coincide when the carriers accept
   bytes at once. The bytes read during
   lingering are bounded only by time; an endpoint MAY stop reading earlier
   when it has read more than one session receive window.
4. After the linger period it closes the carriers. An abortive close (TCP
   RST) is allowed only after the linger timeout has expired.

**Receiving a close.** On processing a CONNECTION_CLOSE, an endpoint enters
CLOSING, reports the peer's error to the application, sends at most one
CONNECTION_CLOSE on each outbound pipe (QMux-02 Section 7.3), and continues
with steps 2 to 4.

**Error precedence.** The error reported to the application for a session is
the first of the following that happened:

1. a CONNECTION_CLOSE frame that was received and processed;
2. a local close (including the close after rejected early data, Section
   6.1.2) or a locally detected protocol error;
3. an idle timeout (local, or the peer's graceful shutdown in a session
   without acknowledgement, Section 6.4),
   handshake timeout or resume timeout;
4. a carrier failure, or the loss of every pipe in a direction without resume
   (reported as "pipes lost").

To honour rule 1, an endpoint MUST process every complete record it has
already received on a pipe before acting on that pipe's failure, and a
failure of a local write MUST NOT replace a peer CONNECTION_CLOSE that was
already processed. Pending local writes and opens fail with the session error.

### 6.6 Pipe failure outside multipath

In a session that is not a multipath session, the failure of either bootstrap
pipe terminates the session immediately (QMux-02 Section 7.4): the endpoint
closes its carriers without sending frames and reports a carrier failure
(Section 6.5, error precedence item 4) unless an earlier event takes precedence. A graceful
end of the inbound pipe at a record boundary is not a failure but the peer's
close in a session without acknowledgement, and after a processed
CONNECTION_CLOSE; in a session with pipe acknowledgement it is a failure
when no CONNECTION_CLOSE has been sent or received (the session is ACTIVE or
DRAINING, Section 6.4). For a pipe with lanes the end of the pipe is the end
of every lane, in any order; a lane that ends inside a record, or with an
error, is a failure at once. In a multipath session pipe failures are handled by
MULTIPATH.md Section 7, and the loss of the last pipe in a direction by
MULTIPATH.md Section 8.5 and RESUME.md.

### 6.7 Draining

Graceful shutdown with a "not processed" guarantee uses GOAWAY (EXTENSIONS.md
Section 3). Without GOAWAY in use, an application closes the session with an
application CONNECTION_CLOSE after its streams have finished.

---

## 7. Streams

### 7.1 Identifiers and types

Stream identifiers and types are those of RFC 9000 Section 2.1. The initiator
(Section 5.6) uses the client-initiated identifiers (least significant bit 0).
The stream index of a stream is its identifier shifted right by two bits.

### 7.2 Opening

1. A sender MUST allocate a stream identifier at the moment it emits the first
   frame of that stream, and MUST allocate identifiers of each type in
   increasing order without gaps. It MUST emit the first frames of the streams
   of one type in identifier order: no stream's first frame is sent before the
   first frame of every lower-numbered stream of that type that it has
   allocated. A stream that an application opens and cancels before any frame
   has been emitted consumes no identifier and puts nothing on the wire. If
   the application asked for the identifier
   before the first frame, the identifier is allocated at that moment instead
   (it is **pinned**), and the endpoint emits the stream's opening frame as
   soon as flow control and, in a multipath session, its bound on retained
   data (MULTIPATH.md Section 11 rule S4)
   allow, and in any case before the first frame of any later stream of that
   type: a STREAM frame at offset 0 that carries the preamble and no
   application data when the preamble is in use, or a STREAM frame with no
   data and no FIN otherwise. Pinning counts as the stream's first frame
   wherever this specification refers to it: settings that travel in the
   preamble can no longer change after it, and a later priority change is
   sent as PRIORITY_UPDATE. If such a stream is then cancelled before it sent
   application data, the endpoint sends a reset (RESET_STREAM_AT with a
   Reliable Size equal to the preamble size, or RESET_STREAM without a
   preamble), so that the identifier never stays silently open at the peer.
   After a GOAWAY a pinned stream is not a new stream (EXTENSIONS.md Section
   3.5 rule 2): below the watermark its opening frame is still sent; a pinned
   identifier at or above the watermark is never sent, and the stream fails
   locally as rejected (EXTENSIONS.md Section 3.5 rule 3).

   An endpoint MUST NOT assign an identifier, by a first frame or by
   pinning, whose stream index is at or above the peer's current limit for
   that type (RFC 9000 Section 4.6). When the application asks for the
   identifier and no credit is available, pinning waits until a MAX_STREAMS
   frame raises the limit; waiting streams are assigned identifiers in the
   order in which they asked.
2. A receiver opens streams implicitly as RFC 9000 Section 3.2 describes:
   STREAM, RESET_STREAM, RESET_STREAM_AT and STREAM_DATA_BLOCKED open a
   receiving part; MAX_STREAM_DATA, STOP_SENDING and PRIORITY_UPDATE open a
   peer-initiated bidirectional stream. Opening a stream opens every
   lower-numbered stream of the same type (RFC 9000 Section 2.1).
3. A frame that opens a stream whose index is at or above the limit the
   receiver has advertised is a STREAM_LIMIT_ERROR (RFC 9000 Section 4.6).
4. A frame that references a locally initiated stream that has not been
   opened, or that sends on a stream in the wrong direction, is a
   STREAM_STATE_ERROR (RFC 9000 Sections 19.4 to 19.13).
5. Peer-initiated streams are offered to the application through accept.
   Without the stream preamble, a stream is offered as soon as it is opened,
   in stream-identifier order per type, including when it is opened
   implicitly by a higher-numbered stream before any frame of its own arrived
   (the QUIC behaviour). When the preamble is in use, a stream is offered as
   soon as its preamble has been received (EXTENSIONS.md Section 4.4), in
   the order in which the preambles are processed, unless the stack rejects
   it (EXTENSIONS.md Sections 3.4, 4.4 rule 5 and 7.2); it
   never waits for a lower-numbered stream whose preamble has not arrived:
   such a stream is open (rule 2), counts against the stream limit and is
   offered when its own preamble arrives. Outside multipath and lanes the
   preambles of a conforming peer arrive in identifier order (rule 1); in a
   multipath session and over lanes they need not, and an application that
   needs the order in which its peer opened streams carries it itself (for
   example in an application TLV, EXTENSIONS.md Section 4.3).

### 7.3 Data ordering

- **Not a multipath session.** Each stream's data arrives in order (QMux-02
  Section 4.1). A receiver MUST close the session with PROTOCOL_VIOLATION when
  a STREAM frame's offset differs from the end of the previously received data
  of that stream. The data can be delivered to the application directly.
  With lanes, the sender keeps each stream direction's frames on one lane
  until a PIPE_ACK covers them (MULTIPATH.md Section 5.8), so this holds
  unchanged.
- **Multipath session.** The QMux-02 Section 4.1 rule does not apply. A
  receiver MUST accept the frames of a stream in any order and interleaving,
  on any pipes, and MUST reassemble by offset (RFC 9000 Section 2.2). Data at
  an offset that has already been received MUST be identical; a receiver MAY
  check this and, if it does, MUST treat a mismatch as PROTOCOL_VIOLATION.
  Receive buffering is bounded by the stream's advertised limit (Section 8);
  a receiver MUST NOT discard data that is within the limit, and data beyond
  the limit is a FLOW_CONTROL_ERROR. How the peer fragments the data is
  never a violation and never charged (Section 9.4.1): the memory it could
  cost is bounded by the receiver's representation (Section 9.8).

### 7.4 Stream states and acknowledgement

The stream states of RFC 9000 Section 3 apply; STATE_MACHINE.md Section 2
lists the zmux deltas. RFC 9000 refers to acknowledgements. In zmux:

- **not a multipath session**: information is acknowledged when the record
  carrying it has been passed to the carrier (QMux-02 Section 4). A sending
  part reaches "Data Recvd" when its FIN has been written and "Reset Recvd"
  when its RESET_STREAM has been written;
- **multipath session**: information is acknowledged when a PIPE_ACK covers
  the position of the record that carried it (MULTIPATH.md Section 6).

**Delivery boundary.** Every delivery guarantee of zmux, including
exactly-once, in-order delivery across the pipes of a multipath session and
across a resumption, ends at the peer's zmux endpoint: an acknowledgement, a
completed write, a finished stream ("Data Recvd") or a closed session never
says that the peer's application has read, acted on or durably stored the
data. Applications that need that use an acknowledgement of their own, as
over TCP. The statements about processing that zmux carries (ZMUX_REJECTED
and the GOAWAY watermarks, EXTENSIONS.md Sections 3.4 and 7.2) say that
something was *not* processed, and are never inferred from delivery.

### 7.5 Termination

- The FIN bit, RESET_STREAM, RESET_STREAM_AT and STOP_SENDING behave as in
  RFC 9000 Sections 3.1 to 3.5 and reliable-stream-reset-11 Section 5.
- **Final size** rules of RFC 9000 Section 4.5 apply, including
  FINAL_SIZE_ERROR when a final size changes or data is received beyond it,
  and the rule of reliable-stream-reset-11 Section 5.2 that the error code and
  final size of a stream never change.
- **A reset stream is never complete.** A receiver MUST report a reset
  (RESET_STREAM or RESET_STREAM_AT, whatever its error code, including 0) to
  the application instead of a clean end of stream, even if all data up to
  the final size has arrived, unless the application had already read the
  end of the stream before the reset arrived. Only a FIN that the sender did
  not later reset ends a stream cleanly. Libraries reset with code 0 when a
  handle is dropped without an explicit finish (EXTENSIONS.md Section 7.4),
  so treating such a reset as completion would turn truncated data into
  apparently complete data.
- **Data after the final size is known.** A STREAM frame whose data lies
  within a known final size is processed normally; only bytes that were
  already received are duplicates. In a multipath session data can arrive
  after the FIN or the reset that announced the final size, because frames
  of one stream travel on different pipes; after RESET_STREAM_AT the sender
  also still delivers the data below the Reliable Size (reliable-stream-reset-11
  Section 5). Outside multipath sessions the ordering rule of Section 7.3
  still applies to such a frame. Data beyond the final size is a
  FINAL_SIZE_ERROR in every mode.

### 7.6 Release and state discard

An endpoint discards a stream's state once both of the following hold:

1. every part of the stream is in a terminal state (sending part "Data Recvd"
   or "Reset Recvd"; receiving part "Data Read" or "Reset Read"), and
2. the local application has released the stream: it has closed or dropped
   every handle to it, or it never accepted it and the session is closing.

Until then the stream counts against the concurrency limit it was opened
under (Section 8.5), and any data the application has not read, the stream's
open information and the final status remain available to the application.

A stream that the stack itself rejects (EXTENSIONS.md Section 3.4) is never
offered to the application and needs no release: its state is discarded as
soon as both parts are terminal.

Frames that reference a stream whose state has been discarded, identified by
an index at or below the highest index opened for that type, are ignored.
They are never chargeable (Section 9.4.1): they are normal when frames cross,
and the endpoint keeps no state to tell a crossing frame from a repeated one.

---

## 8. Flow control

### 8.1 Model

Flow control is that of RFC 9000 Section 4: per-stream limits
(MAX_STREAM_DATA) and a session limit (MAX_DATA), both absolute byte offsets,
plus cumulative stream-count limits (MAX_STREAMS). Violations are
FLOW_CONTROL_ERROR or STREAM_LIMIT_ERROR and are session errors (RFC 9000
Section 4.1).

### 8.2 Session accounting

The amount of data a peer has sent in a session, for MAX_DATA purposes, is
the sum over all streams of max(highest received offset, final size), where a
final size counts once it is known from a FIN, RESET_STREAM or
RESET_STREAM_AT (RFC 9000 Sections 4.4 and 4.5). Duplicate data does not
count twice. A receiver MUST close the session with FLOW_CONTROL_ERROR as soon
as this sum exceeds the session limit it has advertised, including when the
excess comes from a final size in a reset.

The sending side accounts the same way: a sender MUST NOT send STREAM data or
a reset whose final size would make the sum exceed the peer's MAX_DATA, and
MUST NOT refund credit for data it already counted, for example when a stream
it opened is reset.

A STREAM frame that carries no data and only the FIN bit, at the offset the
stream has already sent up to (a **FIN-only** frame), adds nothing to the
sum, so no credit holds it back: once the stream's earlier data is sent,
the sender sends it whatever stream or session credit remains (RFC 9000
Section 4.1 limits data only). Holding it back could deadlock, because a
peer that reads one stream to its end before reading others never releases
the session credit the FIN would wait for. For the same reason a multipath
sender's bound on retained data never holds it back (MULTIPATH.md Section
11 rule S4), on whichever pipe carries it. A FIN-only frame that opens its
stream (possible only without the preamble) still needs stream-count
credit (Section 8.5).

### 8.3 Issuing credit

For each scope (a stream's receiving part, or the session) a receiver keeps
the limit it has advertised, L, and the limit it could advertise from the
credit it has released, C: for a stream, the bytes released plus the
stream's current window; for the session, the bytes released plus the
session window W. C - L is credit that is **released but not yet sent**. A
stream's credit is released when the application consumes data (reads it,
or the stream is released and its data discarded); preamble bytes
(EXTENSIONS.md Section 4) count as consumed once the preamble has been
parsed. Session credit is released as rule 4 describes, and in one more
case: whenever the receiver receives RESET_STREAM or RESET_STREAM_AT for a
stream, including one whose final size it already knew from a FIN and one
that lowers the Reliable Size, it MUST release session credit at once for
every byte at or above the stream's current smallest Reliable Size (0 for
RESET_STREAM) and below its final size that it has neither received nor
already released. Those bytes count against the session (Section 8.2) but
will never be delivered: in a multipath session they can be lost with a
failed pipe, and after the reset the sender does not reinject them
(MULTIPATH.md Section 7.4), so without this release they would hold session
credit for ever and could stall the session between two healthy pipes.
Bytes below the Reliable Size are still delivered and are released by rule
4 when they arrive. Every byte releases credit at most once in each scope;
duplicate data releases nothing, and neither does a byte at or above the
Reliable Size that arrives after its credit was released this way. A byte
counts as received when it arrived, whether it is still buffered, was read
or was discarded on arrival (after STOP_SENDING, for example).

1. **No waiting for BLOCKED.** A receiver MUST issue credit based on what its
   application consumes and on its memory, and MUST NOT wait for or require a
   DATA_BLOCKED, STREAM_DATA_BLOCKED or STREAMS_BLOCKED frame before doing so
   (RFC 9000 Section 4.2).
2. **Regular updates.** A receiver MUST send MAX_STREAM_DATA for a stream, or
   MAX_DATA, no later than when C - L for that scope reaches one quarter of
   the scope's window, and MAY send it earlier. It SHOULD NOT send
   MAX_STREAM_DATA once the stream's receiving part has left the "Recv"
   state (RFC 9000 Section 3.2).
3. **Blocked liveness.** The receiver MUST send C immediately, that is with
   the next records it writes, without waiting for any other condition and
   whatever data it holds unread:
   - when the peer is **blocked** in the scope and C > L. The peer counts as
     blocked when its remaining credit in the scope (L minus the data it has
     sent there, Section 8.2) is 0, or when the receiver has received a
     DATA_BLOCKED or STREAM_DATA_BLOCKED frame for the scope at the current
     limit;
   - when the peer is **nearly blocked**, its remaining credit below one
     quarter of the window, and C - L has reached min(W/4, 32 KiB), W being
     the scope's window. The minimum increment avoids a stream of tiny
     updates (one per small read) while the peer can still send.
   Both conditions are observable at the receiver, so no state holds
   released credit back from a blocked peer: a scope can stay blocked only
   while C = L, that is while the receiver has released nothing, which
   happens only when nothing is consumed (and, for the session, the buffer
   cap of rule 4 is reached). A nearly blocked peer either receives credit
   or, once it has used its remaining credit, is blocked.
4. **Session credit (receiver policy).** Beyond rules 2 and 3, when a
   receiver releases session credit is local policy. A receiver SHOULD
   release session credit for a byte when the byte is received into a
   stream buffer, as long as the session's unread buffered bytes are below
   its **buffer cap**. A byte received while the buffered bytes are at or
   above the cap is held: its credit is released when the byte is consumed,
   or as soon as the buffered bytes fall below the cap again, whichever comes
   first. While at the cap, the receiver also stops growing its windows
   until the buffered bytes are below half the cap. The cap is derived from
   the receiver's own transport parameters and streams:

       buffer_cap = max(session window cap, S)
       S = n_bidi x initial_max_stream_data_bidi_remote
         + n_uni  x initial_max_stream_data_uni
         + n_local x initial_max_stream_data_bidi_local

   where the session window cap is the receiver's configured or default
   bound on its session window (a fixed value, whatever the session window
   has grown to), the stream windows are the ones the
   receiver sent (so the peer cannot inflate the cap), n_bidi and n_uni are
   the numbers of peer-initiated bidirectional and unidirectional streams
   the receiver currently lets the peer have open at once (Section 8.5), and
   n_local is the number of bidirectional streams the receiver has opened
   whose receiving part is not yet terminal. S is what all streams together
   can hold at their initial windows, so streams that stop being read at
   their initial windows never stall the session: session credit keeps
   turning over on receipt and the streams that are read keep receiving.
   Only streams whose windows grew and that then stopped being read can fill
   the cap. The session's receive memory stays below the cap plus one
   session window, whatever the peer does. Releasing session credit only on
   reads, with no cap, lets streams nobody reads absorb the whole session
   window and starve the others; releasing it on receipt with no cap gives
   up any per-session memory bound.
5. Credit updates SHOULD be coalesced per scope and sent with the next
   outgoing records; an endpoint MUST NOT send more than one MAX_DATA and one
   MAX_STREAM_DATA per stream in a single record.

### 8.4 BLOCKED frames

A sender SHOULD send DATA_BLOCKED, STREAM_DATA_BLOCKED or STREAMS_BLOCKED when
it has data or streams it cannot send because of a limit, and MUST NOT send
more than one such frame per scope and limit value, except as reinjection in a
multipath session (MULTIPATH.md Section 7.4). The receiver uses them as a
hint only: rule 3 of Section 8.3 already makes it send released credit.

A sender that cannot send because the next **indivisible** unit does not fit
in the remaining credit, although some credit remains, SHOULD send
DATA_BLOCKED or STREAM_DATA_BLOCKED carrying the current limit at once. The
indivisible unit is the first STREAM frame of a stream with its preamble
(EXTENSIONS.md Section 4.2), which must be sent whole. Without the frame the
receiver sees a peer that is only nearly blocked and may wait for a larger
increment (Section 8.3 rule 3); with it, rule 3 applies to a blocked peer and
any released credit is sent. Conforming zmux implementations send it
(CONFORMANCE.md Section 2, C-5). These frames are advisory: a receiver takes no mandatory
action on them, and their absence never delays credit (Section 8.3).

### 8.5 Stream concurrency credit

1. A peer-initiated stream occupies one unit of the stream-count credit of
   its type from the moment it is opened (explicitly or implicitly) until its
   state is discarded (Section 7.6), whichever endpoint terminated it and
   however it was terminated.
2. An endpoint MUST NOT raise MAX_STREAMS for a stream before that stream's
   state has been discarded. In particular, a stream that the peer opened and
   reset before the application accepted it still returns its credit only
   after the application has accepted and released it, and an endpoint MUST
   offer such streams through accept like any other. The only streams that
   are never offered are those the stack rejects (Section 7.6).
3. An endpoint SHOULD raise MAX_STREAMS as streams are discarded, and SHOULD
   coalesce the increases into one frame per batch. It MUST raise the limit
   no later than when the number of streams of a type discarded since the
   last raise reaches half of that type's concurrency limit, rounded up.
   When it raises the limit, the new value MUST be the initial limit of that
   type plus the number of streams of that type discarded so far, so that the
   number of streams the peer can have open stays constant (RFC 9000 Section
   4.6). The exception is an endpoint that has sent GOAWAY: it raises
   MAX_STREAMS no more (EXTENSIONS.md Section 3.3). Memory pressure is not an
   exception: an endpoint that cannot afford new streams keeps raising
   MAX_STREAMS and rejects new peer streams with ZMUX_REJECTED instead
   (EXTENSIONS.md Section 7.2); rejected streams are
   discarded at once and return their credit like any other.
4. **Staged unidirectional credit (local policy).** An endpoint MAY
   advertise a small `initial_max_streams_uni` and raise unidirectional
   stream credit only when its application first accepts a unidirectional
   stream (the first accept call, even one that has to wait): it then sends
   MAX_STREAMS (unidirectional) with its full concurrency N, and from then on
   N is the initial limit of rule 3. zmux implementations do this by
   default, advertising 16 and raising to N = 256, unless the application
   configures `max_incoming_uni_streams`, which they then
   advertise at once in their transport parameters, 0 included. A peer can
   therefore open its first 16 unidirectional streams without waiting (the
   control and QPACK streams of HTTP/3, a few MoQ-style streams), while an
   application that never accepts unidirectional streams lets a peer store
   at most 16 initial stream windows in them (4 MiB with the defaults). An
   endpoint that has sent GOAWAY raises no credit. This changes only
   what the endpoint advertises and when; it is ordinary QUIC stream credit
   (RFC 9000 Section 4.6), so plain QMux peers are unaffected.

This is the defence against Rapid Reset (CVE-2023-44487) and MadeYouReset
(CVE-2025-8671): stream credit measures application work, not protocol state.

---

## 9. Robustness rules

The rules of this section are normative for every mode, including plain QMux
mode. They make the defect classes of stream multiplexing and of the HTTP/2
attack history impossible or explicitly bounded.

### 9.1 Continuous reading

An endpoint MUST keep reading every inbound pipe and processing complete
records regardless of whether the application reads any stream, accepts
streams or reads datagrams (QMux-02 Section 6). It MUST NOT couple pipe reads
to application reads of a single stream, and MUST NOT stop reading because an
accept queue, a datagram queue or an outbound queue is full. The amount of
data it may have to buffer is bounded by the flow-control limits it has
advertised, the stream-count credit and the datagram queue limit (Section
9.8). An endpoint MUST NOT let a blocked or pending carrier write stop it
from reading its inbound pipes: two endpoints blocked in writes to full
carriers would otherwise never read again.

### 9.2 Bounded response state

For every frame that a peer can send to make an endpoint send something, the
endpoint keeps at most a fixed amount of pending response state:

| Trigger | Response state kept |
|---|---|
| QX_PING request | the largest sequence number to echo (one response) |
| STOP_SENDING | at most one pending RESET_STREAM per stream |
| PIPE_PING | one "acknowledge immediately" flag for the whole session |
| PIPE_ABANDON | at most one pending PIPE_ABANDON per pipe |
| any change to a limit | the latest value per scope (MAX_DATA, MAX_STREAM_DATA per stream, MAX_STREAMS per type) |

Pending control information MUST be held as coalesced state per scope, never
as an unbounded queue of frames. Because the number of streams is bounded by
credit, and per-pipe state by the cap of unretired attachments and the replay
window (MULTIPATH.md Sections 5.4 and 4.3), the total pending control state
of a session is bounded even when the peer stops reading.

### 9.3 Peer violations are session errors

Every violation of this specification by the peer that a receiver is told to
detect is a session error: the endpoint closes the session with
CONNECTION_CLOSE type 0x1c carrying the transport error code of Section 10.
An endpoint MUST NOT respond to a peer violation with RESET_STREAM,
RESET_STREAM_AT or STOP_SENDING, and MUST NOT ignore it, unless this
specification says that the condition is not a violation (for example
duplicates in a multipath session).

Some sender requirements cannot be checked from what a receiver sees, for
example the presence of GREASE, or the completeness of a PIPE_ACK snapshot
beyond the checks of MULTIPATH.md Section 6.2. Receivers do not check them
and MUST NOT close a session because of them. The order of PIPE_ACK
entries, by contrast, is checked (MULTIPATH.md Section 6.2).

Stream-level frames carry only application error codes (RFC 9000 Section
20.2). A stream-level failure that the local application causes or observes is
reported as a stream error and never escalates to the session, including
stream races such as STOP_SENDING crossing a local finish.

### 9.4 The no-effect budget

An endpoint MAY enforce a budget on frames that have no effect. If it does, it
MUST use exactly the accounting below or a more lenient one (larger capacity,
faster refill or fewer chargeable frames). The accounting is designed so that
a compliant peer is never closed for its normal traffic, whatever the
implementation (RFC 9000 Section 21.9; QMux-02 Section 12.2).

The budget counts frames, not bytes. Frames whose cost is the cost of reading
their bytes (DATAGRAM, PIPE_ACK, PADDING inside a record that carries other
frames) are not charged; the carrier's rate bounds them.

#### 9.4.1 Chargeable frames (closed list)

A received frame is **chargeable** only if it matches a row of this list.
Every other frame is **neutral**: it neither costs nor earns tokens.

| # | Chargeable when |
|---|---|
| NE-1 | a record consists only of PADDING frames, or is an empty message of a message-framed carrier (Section 3.4) (charged once per record) |
| NE-2 | a QX_PING request that arrives while the receiver has not yet answered an earlier one (Section 4.5); a PIPE_PING (every one) |
| NE-3 | MAX_DATA or MAX_STREAMS that does not increase its limit; MAX_STREAM_DATA that does not increase the limit of its sending part |
| NE-4 | DATA_BLOCKED, STREAM_DATA_BLOCKED or STREAMS_BLOCKED whose limit is not greater than the largest limit already reported in a BLOCKED frame of the same scope (the session, a stream, a stream type) |
| NE-5 | a STREAM frame that neither opens its stream nor carries new data or new final-size information (duplicate data, an empty frame) |
| NE-6 | RESET_STREAM or RESET_STREAM_AT that changes no state (a repeat, or RESET_STREAM_AT that does not reduce the Reliable Size); a second STOP_SENDING for the same sending part |
| NE-7 | PRIORITY_UPDATE whose sequence number is not greater than the last one applied to that stream |
| NE-8 | GOAWAY that lowers no watermark; a QX_PING response whose sequence number is not greater than that of every earlier response |

Two exemptions apply to the list:

1. **Crossing frames.** A frame that references a stream part that is in a
   terminal state (Section 7.6), or a stream whose state has been discarded,
   is never chargeable. Such frames are normal when frames cross (a
   STOP_SENDING crossing a FIN, a MAX_STREAM_DATA crossing a reset, a
   PRIORITY_UPDATE crossing the end of a stream), and an endpoint keeps no
   state for a discarded stream from which it could tell a crossing frame
   from a repeat.
2. **Multipath duplicates.** In a multipath session NE-3, NE-4 and NE-6 to
   NE-8 do not apply, and NE-5 applies only to STREAM frames that carry
   neither data nor a FIN bit and do not open their stream. A STREAM frame
   whose data and FIN are fully covered by data already received or by a
   known final size, including a FIN-only copy, is never chargeable there.
   Reinjection makes duplicate and outdated copies normal, and their number
   depends on how much was in flight on a failed pipe, not on the peer's
   intent (MULTIPATH.md Section 7.6). NE-1 and NE-2 remain chargeable; an
   outdated QX_PING request (Section 4.5) is not a request that awaits an
   answer and is never chargeable. For
   the same reason the frames that exist only in multipath sessions are never
   chargeable, except PIPE_PING (NE-2).

A STREAM frame that carries new data is never chargeable, however little it
carries and wherever it lands relative to data already received. A
compliant sender in a multipath session cuts stream data at every
application write and every chunk and spreads the frames over pipes of
different delay, so small ranges isolated beyond holes are its normal
traffic, which a receiver cannot tell from deliberate fragmentation. What
fragmentation could cost is memory, and Section 9.8 bounds that
independently of the frames.

#### 9.4.2 The budget

The session has one budget B, in tokens:

- B starts at C = 4096 and never exceeds C;
- each chargeable frame subtracts 1, in the order frames are processed;
- B gains 32 tokens per second of elapsed time (continuously or in steps of
  at most one second), up to C.

When B becomes negative, the endpoint MUST close the session with
ZMUX_EXCESSIVE_LOAD. Implementations that claim zmux conformance enforce
exactly these parameters (CONFORMANCE.md Section 2), so that traces can test
the boundary.

#### 9.4.3 Sender rules (the compliant floor)

A compliant peer follows these rules:

1. It sends at most 16 PADDING-only records (empty messages included,
   Section 3.4), QX_PING requests and PIPE_PING
   frames together in any one-second interval,
   whether or not the receiver will charge them (MULTIPATH.md Section 6.4
   limits PIPE_PING accordingly), and has at most one QX_PING request outstanding (Section
   4.5).
2. It sends limit updates only when they increase the limit (Section 4.3,
   which also lists the multipath copies), at
   most one BLOCKED frame per scope and limit with increasing limits (Section
   8.4), at most one RESET_STREAM or RESET_STREAM_AT per stream except
   reductions of the Reliable Size, at most one STOP_SENDING per stream,
   strictly increasing PRIORITY_UPDATE sequence numbers, GOAWAY frames that
   each lower a watermark (EXTENSIONS.md Section 3.3), and at most one
   QX_PING response per request.
3. It sends no STREAM frame without data or FIN except to open a stream
   without a preamble.
4. In a multipath session it repeats information only as reinjection
   (MULTIPATH.md Section 7.4) or as the copies of MULTIPATH.md Section 7.5:
   the second copies of PIPE_ACK and credit frames (MULTIPATH.md Section 6.5),
   opportunistic reinjection (MULTIPATH.md Section 7.5.1) and redundant
   urgent data with the copy of the first chunk after an idle period
   (MULTIPATH.md Section 7.5). None of these is chargeable.
5. It cuts new stream data into frames of whatever size its writes and
   its scheduler produce and, in a multipath session, sends them on any
   pipes, in any order across pipes (Section 7.3). Nothing bounds how
   small or how scattered they are, because new data is never charged
   (Section 9.4.1).

Outside multipath sessions such a peer sends no frame of kinds NE-3 to NE-8
except crossing frames, which are exempt, and the rare outdated QX_PING
response (at most one per second, because it has at most one request to
answer at a time). In multipath sessions NE-3 to NE-8 do not apply. A
compliant peer therefore spends at most about 17 tokens per second against a
refill of 32, for any rate of stream opening and closing, crossing frames and
reinjection. CONFORMANCE.md Section 4.2 requires a regression test for this
floor.

### 9.5 Bounded metadata

Per-stream metadata is carried only in the stream preamble (EXTENSIONS.md
Section 4): a single length-prefixed TLV block that MUST be contained in the
single STREAM frame at offset 0 and is bounded by a limit the receiver
declares. zmux has no header compression, no metadata continuation across
frames and no session-level decoding state (CONTINUATION flood, HPACK bomb).

### 9.6 No retention without multipath

In a session where multipath is not in use, an endpoint MUST NOT retain the
content of frames after their records have been passed to the carrier, and
MUST NOT send PIPE_ACK or any other acknowledgement frame unless pipe
acknowledgement is in use (MULTIPATH.md Section 3.5), except the fence of
early data, which it sends before it knows (Section 6.1.2). Before the
peer's parameters are processed, whether multipath will be in use is not
known: an endpoint that sent early data and `zmux_multipath` keeps its early
frames until then (Section 6.1.2). Pipe acknowledgement serves the sender's queue control only; it retains no data. Retransmission state
exists only in multipath sessions.

### 9.7 GREASE

Extension points are exercised so that they do not ossify [RFC9170]:

- transport parameters: Section 5.2 rule 6;
- stream preamble TLV types: EXTENSIONS.md Section 4.3;
- frame types are not greased: an unknown frame type is fatal (RFC 9000
  Section 12.4), and new frame types are only sent after negotiation.

### 9.8 Resource bounds

| Resource | Bound |
|---|---|
| partially received record | the local `max_record_size` per pipe; with lanes (MULTIPATH.md Section 2.5) at most one per lane, whose bytes are part of what the carrier connection's own receive window admits, so they need no bound of their own |
| stream receive buffers | the advertised stream limits; per session the buffer cap plus one session window (Section 8.3 rule 4); across sessions, new grants are limited by the listener's memory budget |
| reassembly in multipath sessions | the same limits (no separate reorder limit is needed, RFC 9000 Section 21.7) |
| memory per byte held | received stream data held for the application in a session, in order or out of order, MUST cost at most a fixed multiple of the sum of its streams' spans (a stream's span runs from the offset the application has read up to, to the highest offset received, which flow control charges to the stream and the session, Section 8.2), plus a constant per stream and the read buffer that each pipe or lane (MULTIPATH.md Section 2.5) is being read into, however the peer fragments and frames it: bookkeeping per range and any part of a receive buffer the data keeps alive count; CONFORMANCE.md Section 4.2 tests 2.5 x |
| implicitly opened streams | the advertised stream-count limits (RFC 9000 Section 21.8) |
| pending response state | Section 9.2 |
| received datagrams not yet read | queue limits in datagrams and a session-wide byte limit, which also covers datagrams held for streams not yet opened; excess datagrams are dropped (EXTENSIONS.md Section 6.5) |
| pipes | at most max_pipes + H unretired attachments joined by the peer (MULTIPATH.md Section 5.4 step 9, Section 8.4), which bounds PIPE_ACK snapshots (split across frames when needed), and the replay window for IDs without attachment state (MULTIPATH.md Section 4.3) |
| unauthenticated carriers (pending joins, unpaired split-bootstrap carriers) | per-listener, per-session and per-source counts and the first-record timeout (MULTIPATH.md Section 5.6, Section 6.1.1) |
| retained data for reinjection | the peer's flow-control limits and the sender's own bound (MULTIPATH.md Section 11 rule S4) |
| suspended sessions | count and total buffered bytes, retained and received (RESUME.md Section 9) |

---

## 10. Errors

### 10.1 Error spaces

| Space | Carried in | Owner |
|---|---|---|
| transport error codes | CONNECTION_CLOSE 0x1c, PIPE_ABANDON | the zmux protocol (this document and RFC 9000 Section 20.1) |
| application error codes | CONNECTION_CLOSE 0x1d, RESET_STREAM, RESET_STREAM_AT, STOP_SENDING, GOAWAY | the application; EXTENSIONS.md Section 7 defines conventions |

A stack never places a transport error code in a stream-level frame, and an
application never chooses a transport error code.

### 10.2 Transport error codes

The QUIC transport error codes of RFC 9000 Section 20.1 apply with their QUIC
meaning. zmux uses NO_ERROR, INTERNAL_ERROR, CONNECTION_REFUSED,
FLOW_CONTROL_ERROR, STREAM_LIMIT_ERROR, STREAM_STATE_ERROR, FINAL_SIZE_ERROR,
FRAME_ENCODING_ERROR, TRANSPORT_PARAMETER_ERROR, PROTOCOL_VIOLATION and
NO_VIABLE_PATH. CONNECTION_REFUSED is what a listener sends, after its own
QX_TRANSPORT_PARAMETERS, when it refuses a new session for policy reasons,
for example its limit on concurrent sessions (Section 14.3). zmux adds:

| Code | Value | Meaning | Sent on the wire |
|---|---|---|---|
| ZMUX_EXCESSIVE_LOAD | 0x25ff75ef | the peer exhausted the no-effect budget (Section 9.4) | yes |
| ZMUX_ROLE_CONFLICT | 0x277db2c3 | role negotiation failed (EXTENSIONS.md Section 2) | yes |
| ZMUX_RESUME_TIMEOUT | 0x0eb87167 | the resume grace period expired (RESUME.md Section 8) | yes, on any live outbound pipe; otherwise reported locally |
| ZMUX_RESUME_FAILED | 0x0f05f74e | a resume could not be completed safely (RESUME.md Section 7) | yes |
| ZMUX_PIPE_FAILED | 0x296a5bf5 | PIPE_ABANDON reason: the pipe failed or was removed abruptly | yes (PIPE_ABANDON only) |
| ZMUX_PIPE_RESOURCE_LIMIT | 0x0b4fa113 | PIPE_ABANDON reason: the endpoint cannot keep the pipe | yes (PIPE_ABANDON only) |
| ZMUX_PIPE_AUTH_FAILED | 0x09613938 | a join failed authentication | never: local reporting only (MULTIPATH.md Section 5.4) |
| ZMUX_IDLE_TIMEOUT | 0x2736d55c | the sender's idle timer expired in a session with acknowledgement (Section 6.4); the receiver reports an idle timeout caused by the peer | yes |

NO_VIABLE_PATH (0x10) is sent on a remaining outbound pipe when a multipath
session lost every pipe in a direction without resume in use (MULTIPATH.md
Section 8.5). Locally that termination is reported as "pipes lost" (Section 6.5, error precedence item 4), like a carrier failure outside multipath.

A receiver MUST treat an unknown transport error code in CONNECTION_CLOSE as a
generic transport error (INTERNAL_ERROR semantics for the application) and in
PIPE_ABANDON as ZMUX_PIPE_FAILED.

### 10.3 Which code to send

| Condition | Code |
|---|---|
| record Size 0, Size above the limit, truncated last frame | FRAME_ENCODING_ERROR |
| first bytes that cannot begin a valid first record (Section 6.2), including a first record whose Size is below 9 or above the limit | PROTOCOL_VIOLATION |
| unknown, prohibited or not-in-use frame type; malformed frame | FRAME_ENCODING_ERROR |
| DATAGRAM when not advertised, or larger than advertised (a frame that is otherwise well formed) | PROTOCOL_VIOLATION (RFC 9221 Section 3) |
| DATAGRAM whose Length runs past the end of the record, whatever the advertised size | FRAME_ENCODING_ERROR |
| frame type not minimally encoded | PROTOCOL_VIOLATION |
| QX_TRANSPORT_PARAMETERS anywhere but first on a bootstrap pipe, with a valid parameter block | PROTOCOL_VIOLATION |
| QX_TRANSPORT_PARAMETERS anywhere but first on a bootstrap pipe, with a parameter block that is not valid (any condition of the next row) | TRANSPORT_PARAMETER_ERROR |
| transport parameter duplicate, prohibited, malformed, out of range or alone without its required companion | TRANSPORT_PARAMETER_ERROR |
| stream opened beyond the limit | STREAM_LIMIT_ERROR |
| frame for an unopened local stream or wrong direction | STREAM_STATE_ERROR |
| RESET_STREAM_AT changes error code | STREAM_STATE_ERROR |
| final size changed or exceeded | FINAL_SIZE_ERROR |
| flow-control limit exceeded (stream or session) | FLOW_CONTROL_ERROR |
| out-of-order STREAM data outside multipath | PROTOCOL_VIOLATION |
| QX_PING sequence violations (outside multipath; Section 4.5) | PROTOCOL_VIOLATION |
| BLOCKED frame above any advertised limit | PROTOCOL_VIOLATION |
| stream preamble violations | PROTOCOL_VIOLATION |
| multipath violations not listed otherwise in MULTIPATH.md | PROTOCOL_VIOLATION |
| no-effect budget exhausted | ZMUX_EXCESSIVE_LOAD |
| idle timeout in a session with acknowledgement | ZMUX_IDLE_TIMEOUT |
| new session refused by listener policy | CONNECTION_REFUSED |

**Precedence.** When one frame violates several rules, decode errors take
precedence over semantic and placement errors: a receiver first decodes the
frame completely (Type, every field, and for QX_TRANSPORT_PARAMETERS the
parameter block), and a failure there is FRAME_ENCODING_ERROR, or
TRANSPORT_PARAMETER_ERROR for a parameter block that is not valid, even when
the frame would also be misplaced, unexpected or too large for a negotiated
limit (PROTOCOL_VIOLATION and the other semantic codes). Decoding a
parameter block includes every check of Sections 5.2 to 5.4 that needs only
the block itself: encoding, duplicates, prohibited parameters, values out of
range and missing companions. An implementation that builds a table while
it decodes therefore reports a duplicate the same way as one that validates
afterwards. Only a frame that
decodes is checked against the semantic rules. Record-level errors (Section
3.2) come before any frame in the record is decoded.

The traces of CONFORMANCE.md check these codes exactly.

---

## 11. Multipath

Multipath is part of the core protocol and is specified in
[MULTIPATH.md](./MULTIPATH.md). It changes two rules of this document, and
only in multipath sessions: stream data may arrive out of order (Section
7.3, replacing QMux-02 Section 4.1), and the failure of a pipe does not
terminate the session (Section 6.6, replacing QMux-02 Section 7.4). A third
difference applies to every session with acknowledgement, multipath or
not: the idle timer, with the keep-alive timer, is driven only by
application activity received or acknowledged, and its expiry sends
CONNECTION_CLOSE(ZMUX_IDLE_TIMEOUT) (Section 6.4).

## 12. Resume

Resume is part of the core protocol and is specified in
[RESUME.md](./RESUME.md): when every pipe in a direction is lost, a session
with resume in use enters the SUSPENDED state for a grace period instead of
closing, and continues when new pipes join.

---

## 13. QMux compatibility

Plain-QMux interoperability in the degenerate case is a hard invariant of
zmux. No later version of this document may weaken it.

### 13.1 Detection

A peer is a plain QMux peer when its QX_TRANSPORT_PARAMETERS contains none of
the zmux parameters of Section 5.4. No other signal (ALPN, carrier, address)
changes how a zmux endpoint treats the peer. Once a zmux endpoint has
processed the peer's parameters, the decision is final for the session.
Early data (Section 6.1.2) is sent before that decision. Under remembered
parameters it is QMux-02's 0-RTT and follows the peer's remembered
parameters. Under the zmux floor it is sent on the application's declaration
alone; a plain QMux peer that is wrongly declared to be a zmux endpoint
closes the session at the PIPE_ACK fence without processing any of it, and
the zmux endpoint reports "early data rejected". Without remembered
parameters or the declaration nothing is sent before the decision.

### 13.2 Behaviour towards a plain QMux peer

A zmux endpoint whose peer is a plain QMux peer is a QMux-02 endpoint:

1. The session uses exactly one bidirectional pipe pair, without lanes.
   Multipath, resume, lanes,
   role negotiation, GOAWAY, the stream preamble, PRIORITY_UPDATE and datagram
   flows are not in use. Roles come from the carrier (Section 5.6).
2. The endpoint sends no zmux frame (Section 4.2 rows from GOAWAY onwards), no
   PIPE_ACK and no out-of-order or repeated STREAM data. Every stream's data
   is sent in offset order (QMux-02 Section 4.1). The one exception is the
   fence of early data, which reaches a plain QMux peer only when it is
   wrongly declared a zmux endpoint, or when its remembered parameters were
   those of a zmux endpoint, and which ends the session before the peer
   processes any early data (Section 13.1). Early data under a plain QMux
   peer's remembered parameters is QMux-02 Section 8.2 0-RTT, without fence
   or preamble.
3. It sends RESET_STREAM_AT only if the peer advertised `reset_stream_at`, and
   DATAGRAM only if the peer advertised `max_datagram_frame_size`.
4. It still sends its zmux transport parameters, which the peer ignores
   (QMux-02 Section 5.1 allows parameters that are specified to be usable with
   QMux; this document is that specification), and GREASE parameters, which
   QMux-02 Section 5.1 explicitly allows.
5. The failure of the carrier terminates the session (QMux-02 Section 7.4).
6. Every robustness rule of Section 9 applies unchanged. The rules are
   compatible with QMux-02: they reject only traffic that QMux-02 or RFC 9000
   already forbids, or traffic that QMux-02 Section 12.2 allows an endpoint to
   treat as abusive.
7. **Transport parameters of a shared listener.** QMux-02 Section 4.2
   requires each endpoint to send its QX_TRANSPORT_PARAMETERS as soon as the
   carrier is available. A listener that accepts both new sessions and pipe
   joins on one endpoint does not know yet whether a new carrier starts a
   session or a join (MULTIPATH.md Section 5.5). It SHOULD send its
   parameters at once all the same, over TLS 1.3 with its Finished flight
   (Section 6.1): when the carrier turns out to be a join, the joiner
   discards that leading record (MULTIPATH.md Section 5.7 step 3) and the
   acceptor counts its outbound position from the end of it (MULTIPATH.md
   Section 5.4 step 8). A listener MAY instead wait until the first
   record's prefix shows a session start; that is a bounded exception to
   the QMux-02 MUST, which costs its sessions one round trip on TLS 1.3. A
   plain QMux client does not wait for the server's parameters, so
   interoperability is unaffected either way.
8. **Bounded exception: idle timer and keep-alive.** QMux-02 Section 7.1
   restarts the idle timer on every record sent. A zmux endpoint does not
   restart it for a sent record that carries only QX_PING requests and
   PADDING (Section 6.4), so that its own keep-alive cannot keep a session
   with a peer that no longer answers alive. Towards a QMux peer that
   answers QX_PING the behaviour is identical; towards one that does not, the
   zmux endpoint closes at its idle timeout instead of keeping the session
   open.

**Known peer behaviour.** Some QMux implementations accept
`reset_stream_at` but treat RESET_STREAM_AT like RESET_STREAM and can drop
data below the Reliable Size that their application has not yet read. A
zmux sender that needs data delivered reliably before a reset cannot depend
on RESET_STREAM_AT with such a peer; it finishes the stream instead, or
waits for an application-level acknowledgement before it resets.

Some QMux implementations send QX_PING requests at a fixed cadence whether
or not earlier ones were answered. Under the accounting of Section 9.4 such
requests are free as long as each one arrives after the previous one was
answered, which holds at any cadence slower than the receiver's write path;
charging every request would close a session that probes every 20 ms after
about four minutes.

A zmux endpoint MUST accept everything a correct QMux-02 peer may send,
including: PADDING anywhere in a record; STREAM frames with or without the OFF
and LEN bits; QX_PING requests whose sequence starts at any value, including
0; CONNECTION_CLOSE type 0x1c with Frame Type 0; records of any size up to its
own `max_record_size`; empty messages on a message-framed carrier (Section
3.4); and transport parameters it does not know, such as the
`application_protocols` parameter (0x3d4f9c2a8b1e6075) that some QMux
implementations use on carriers without TLS.

### 13.3 ALPN and identification

ALPN identifies the application protocol, not zmux or QMux (QMux-02 Section
8.1). A zmux endpoint presents the same identifiers as a QMux endpoint
(Section 6.2): `qmux-02` by default, `qmux-02.<application>` for an
application, an application's own identifier used directly as a WebSocket
subprotocol [QMUX-WS], and the draft-01 forms such as `qmux-01` and `h3qx-01` on
receipt. zmux implementations MUST let the application choose the
identifiers. Without TLS, the first-record check of Section
6.2 applies to both kinds of peer: a plain QMux peer's first record is also
QX_TRANSPORT_PARAMETERS.

On carriers without TLS, some QMux implementations negotiate the application
protocol with an `application_protocols` transport parameter
(0x3d4f9c2a8b1e6075, a list of length-prefixed names). zmux does not define
this parameter; implementations MAY offer it as an optional interoperability
hook. Without it, both sides of a plain TCP or Unix
carrier must agree on the application out of band (QMux-02 Section 3.1).

### 13.4 What zmux needs and QMux does not provide

Where zmux needs behaviour that QMux-02 does not define (multipath, resume,
role negotiation, GOAWAY, preambles, priorities, datagram flows), zmux defines
it, gates it behind a zmux transport parameter, and never uses it with a peer
that did not send that parameter. This is how zmux needs win over QMux
alignment without breaking Section 13.2.

### 13.5 Draft tracking

zmux pins draft-ietf-quic-qmux-02 and draft-ietf-quic-reliable-stream-reset-11
(whose wire format is identical to the -09 revision that QMux-02 cites). When
QMux is published as an RFC, zmux aligns once, in a new protocol version with
a new ALPN identifier; it does not maintain two record formats in one
version. CONFORMANCE.md Section 6 defines the interoperability test plan
against existing QMux implementations.

---

## 14. Security considerations

### 14.1 Lessons applied

| Attack or defect | Rule |
|---|---|
| HTTP/2 Ping, Reset and Settings floods (CVE-2019-9512, 9514, 9515) | bounded response state (9.2); parameters never change (5.2); budget (9.4) |
| HTTP/2 resource loop, empty-frame and data-dribble attacks (CVE-2019-9511, 9513, 9516-9518) | no dependency-tree priorities (EXTENSIONS.md 5); budget (9.4); flow-control bounds (9.8) |
| Rapid Reset (CVE-2023-44487) | stream credit returns only after application release (8.5) |
| MadeYouReset (CVE-2025-8671) | peer violations are session errors, never stream resets (9.3) |
| CONTINUATION flood (VU#421644), HPACK bomb (CVE-2016-1544) | single-frame bounded preamble, no compression (9.5) |
| Settings with excessive entries (CVE-2020-11080) | transport parameters once, no duplicates (5.2) |
| zero-window deadlock | credit without BLOCKED and zero-window liveness (8.3) |
| stalls from coupling transport reads to application reads | continuous reading (9.1) |
| legitimate churn closed as abuse | closed chargeable list, crossing-frame exemption and compliant floor (9.4) |
| close code lost, abortive close before CLOSE | close procedure and error precedence (6.5) |
| TCP-over-TCP meltdown | no retransmission timers (MULTIPATH.md 7.5) |
| MPTCP keys in the clear (RFC 8684, RFC 7430) | join keys from the TLS exporter or a PSK; no join on plaintext without a PSK (MULTIPATH.md 10) |
| Terrapin (CVE-2023-48795) | implicit counters are never authentication inputs; joins and resume MAC explicit fields (MULTIPATH.md 10, RESUME.md 5) |
| crashes and memory exhaustion from datagram fragmentation over reliable streams | no datagram fragmentation (EXTENSIONS.md 6) |

### 14.2 Carrier protection

zmux provides no confidentiality or integrity for frames; carriers do
(Section 2.2). Without a protecting carrier, an on-path attacker can read and
modify every stream and can terminate the session (QMux-02 Section 12.2).
Join authentication (MULTIPATH.md Section 10) protects only against attaching
foreign pipes; it does not protect pipe contents. A reserved record
protection feature (REGISTRY.md Section 7) is intended for carriers such as
CDN-terminated HTTP that cannot provide end-to-end protection.

### 14.3 Resource exhaustion

Section 9.8 lists the bound of every peer-controlled resource. Implementations
also need a per-listener bound on concurrent sessions and on unauthenticated
carriers; these are deployment policy.

---

## 15. References

### 15.1 Normative

- [RFC9000] QUIC: A UDP-Based Multiplexed and Secure Transport, May 2021.
- [QMUX-02] K. Oku et al., QMux, draft-ietf-quic-qmux-02, 6 July 2026.
- [RSA-11] M. Seemann, K. Oku, QUIC Stream Resets with Partial Delivery,
  draft-ietf-quic-reliable-stream-reset-11, 6 September 2026.
- [RFC9221] An Unreliable Datagram Extension to QUIC, March 2022.
- [RFC9218] Extensible Prioritization Scheme for HTTP, June 2022 (priority
  model only).
- [RFC8446] TLS 1.3 (Section 7.5, exporters).
- [RFC5869] HKDF. [RFC2104] HMAC. FIPS 180-4 (SHA-256).
- [RFC7301] ALPN. [RFC2119], [RFC8174] requirement keywords.
- [RFC9002] QUIC Loss Detection and Congestion Control.

### 15.2 Informative

- [RFC9297] HTTP Datagrams and the Capsule Protocol (context model).
- [RFC9298] Proxying UDP in HTTP.
- [RFC9170] Long-Term Viability of Protocol Extension Mechanisms.
- [RFC1928] SOCKS Protocol Version 5.
- draft-ietf-quic-multipath-21; RFC 8684 (MPTCP); RFC 6182; RFC 7430.
- [QMUX-WS] draft-lcurley-qmux-websocket-00, the QMux mapping to WebSocket.
