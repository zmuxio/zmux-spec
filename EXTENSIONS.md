# zmux Negotiated Features

Each feature in this document is announced with a zmux
transport parameter and is **in use** only when both endpoints sent that
parameter (SPEC.md Section 5.5). zmux endpoints send `zmux_goaway`,
`zmux_preamble`, `zmux_priority` and `reset_stream_at` in every session;
`zmux_role` and `zmux_datagram_flows` depend on configuration. When a feature
is not in use, its frames are unknown frames and its rules do not apply. With
a plain QMux peer none of these features is in use (SPEC.md Section 13).
Sections 7, 10 and 11 are conventions built on these features and on the
core protocol, not features of their own.

---

## 1. Conventions

All codepoints are listed in REGISTRY.md. Error codes named here are defined
in Section 7 (application codes) and SPEC.md Section 10.2 (transport codes).

---

## 2. Role negotiation (`zmux_role`)

### 2.1 Purpose

QUIC and QMux take the client and server roles from the transport. Symmetric
connections, such as a TCP simultaneous open after NAT traversal, have no
such roles. `zmux_role` lets each endpoint declare a role or ask for an
automatic decision, with no extra round trip.

### 2.2 Parameter

ID 0x17240207. Value:

```text
zmux_role Value {
  Role (i),
  [Nonce (64)],
}
```

Role is 0 (initiator), 1 (responder) or 2 (auto). Nonce is present if and
only if Role is 2; it is a random 64-bit value chosen for the session. Any
other value or length is a TRANSPORT_PARAMETER_ERROR.

### 2.3 Resolution

When both endpoints sent `zmux_role`, each computes its role from the two
values:

| Local | Peer | Local role |
|---|---|---|
| initiator | responder | initiator |
| responder | initiator | responder |
| initiator | initiator | ZMUX_ROLE_CONFLICT |
| responder | responder | ZMUX_ROLE_CONFLICT |
| auto | initiator | responder |
| auto | responder | initiator |
| initiator | auto | initiator |
| responder | auto | responder |
| auto (nonce a) | auto (nonce b) | initiator if a > b, responder if a < b, ZMUX_ROLE_CONFLICT if a = b |

On ZMUX_ROLE_CONFLICT the endpoint closes the session with that transport
error. Equal nonces usually mean that an endpoint connected to itself or that
a middlebox reflected its bytes; implementations SHOULD NOT retry
automatically after a conflict.

When only one endpoint sent `zmux_role`, role negotiation is not in use and
both endpoints use the transport roles (SPEC.md Section 5.6). An endpoint
that is configured for automatic or explicit roles but whose peer did not send
`zmux_role` uses its transport role; if it has none, or if its configured
explicit role contradicts it, it closes the session with ZMUX_ROLE_CONFLICT.

Roles are fixed when the session is established. Stream frames depend on
roles, so an endpoint whose role depends on the peer's parameters (it sends
Role auto, or has no transport role) sends no early data (SPEC.md Section
6.1.2 rule 1).

---

## 3. GOAWAY

### 3.1 Parameter

`zmux_goaway`, ID 0x04528a5f, empty value. A non-empty value is a
TRANSPORT_PARAMETER_ERROR.

### 3.2 Frame

```text
GOAWAY Frame {
  Type (i) = 0x1911f43e,
  Maximum Bidirectional Streams (i),
  Maximum Unidirectional Streams (i),
  Application Protocol Error Code (i),
}
```

- **Maximum Bidirectional Streams / Maximum Unidirectional Streams**
  (watermarks): counts, like MAX_STREAMS. Streams initiated by the GOAWAY's
  receiver with a stream index (SPEC.md Section 7.1) below the watermark may
  have been processed or may still be processed by the GOAWAY's sender.
  Streams with an index at or above the watermark have not been processed and
  will not be. A value above 2^60 is a FRAME_ENCODING_ERROR.
- **Application Protocol Error Code**: why the sender is going away
  (Section 7); NO_ERROR for an ordinary shutdown. Informational.

### 3.3 Sending: two phases with a PING fence

An endpoint A that wants to stop accepting new streams:

1. **Phase 1.** Sends GOAWAY with both watermarks equal to the stream-count
   limits it has currently granted to the peer (its last MAX_STREAMS values),
   followed by a QX_PING request (the fence). From now on A does not raise
   MAX_STREAMS and is DRAINING. No stream the peer could already be opening is
   refused by this first GOAWAY.
2. **Phase 2.** When the fence completes, A sends a final GOAWAY. The fence
   completes when A has received a QX_PING response whose sequence number is
   at least the fence's; in a multipath session, and in a session whose pipe
   toward A has lanes (MULTIPATH.md Section 2.5), A additionally waits for SRTT
   (SPEC.md Section 1.3, the largest smoothed RTT of its pipes), because the
   response may have overtaken stream frames on a slower pipe or lane. If the fence
   request stops being outstanding without a response (SPEC.md Section 4.5,
   for example because its pipe failed), A sends a new request as the fence.
   In the final GOAWAY, each watermark is one more than the highest index of a
   peer-initiated stream of that type that A has offered to its application,
   or 0 if none. A MAY use a larger watermark, up to the phase-1 value, to
   keep streams it has received but not yet offered; it then MUST offer or
   reject them. A stream below the final watermark whose preamble has not
   arrived yet (a lower-numbered stream than one already offered, SPEC.md
   Section 7.2 rule 5) is offered when it arrives, like any stream below the
   watermark: to the peer it may have been processed. If neither final
   watermark is lower than the phase-1 value, A sends no final GOAWAY.
3. A finishes the streams below the watermarks and then closes the session
   with an application CONNECTION_CLOSE, or closes earlier at a deadline of its
   own choosing.

A MAY skip phase 1 and send the final GOAWAY directly; more streams are then
rejected. A MUST NOT send a watermark larger than one it sent before for the
same type, and every GOAWAY after the first MUST lower at least one
watermark. The fence is a round trip, not a guessed delay (the two-phase
pattern of RFC 9113 Section 6.8). zmux needs no timeout on it: if the
response never comes, A still closes at its own deadline (step 3), and the
per-stream guarantee of Section 3.4 does not depend on the fence.

### 3.4 The "not processed" guarantee

After sending a GOAWAY with watermark W for a stream type, A:

- MUST NOT offer to its application any peer-initiated stream of that type
  whose index is at least W, whether it already exists or arrives later
  (including streams opened implicitly in a multipath session);
- MUST terminate each such stream with REJECTED: RESET_STREAM(ZMUX_REJECTED)
  on its sending part (bidirectional streams) and STOP_SENDING(ZMUX_REJECTED)
  on its receiving part, and MUST NOT process any of its data. Such a stream
  needs no application release: it is discarded once both parts are terminal
  (SPEC.md Section 7.6).

This, together with Section 7.2, gives the peer a guarantee: **a stream with
an index at or above the lowest watermark it received, and any stream
terminated with ZMUX_REJECTED, was never processed by the application of the
GOAWAY's sender and can be retried elsewhere.**

ZMUX_REJECTED is the only stream termination that the zmux stack initiates on
its own (SPEC.md Section 4.3): after GOAWAY as above, and under the local
memory pressure of a listener, which rejects new peer
streams instead of withholding stream credit. The other permitted use is an
application that declines a stream before reading any of its data.

### 3.5 Receiving

An endpoint B that receives GOAWAY:

1. keeps, per stream type, the minimum watermark of all GOAWAY frames
   received. A GOAWAY that lowers neither watermark has no effect and, outside
   multipath sessions, is chargeable (SPEC.md Section 9.4); a larger watermark
   may be an outdated
   frame delivered late in a multipath session and is ignored, not an error;
2. MUST NOT open any new stream, whatever the watermarks, as HTTP/2 (RFC
   9113 Section 6.8) and HTTP/3 require: A is draining. Local open attempts
   fail with the retryable "going away" error, and so
   does every stream the application opened that has neither emitted its
   first frame nor been pinned (an open that waited for credit, or a stream
   opened before establishment): it sends nothing, holds no identifier and
   fails with the same "going away" error. A stream that has emitted its
   first frame, or is pinned (pinning counts as the first frame, SPEC.md
   Section 7.2), is not new: below the watermark it continues, a pinned
   stream sending its opening frame as soon as credit allows; at or above the
   watermark it is handled by rule 3, and a pinned identifier there is never
   sent. This
   fits the two-phase sending of Section 3.3: the first GOAWAY refuses
   nothing that is already on its way, and from it on B opens nothing, so the
   final watermark only has to cover streams B emitted before it learned of
   the first;
3. MUST report every stream it opened (emitted or pinned) with an index at or
   above the watermark as not processed and retryable ("rejected") to its application at once, without waiting for A's
   RESET_STREAM and STOP_SENDING;
4. enters DRAINING itself (STATE_MACHINE.md Section 4): existing streams
   continue; B may send its own GOAWAY.

In a multipath session GOAWAY is re-sent with the current watermarks when its
pipe fails (MULTIPATH.md Section 7.4).

---

## 4. Stream preamble

### 4.1 Parameter

`zmux_preamble`, ID 0x0439e07a. Value: Maximum Preamble Size (i), the largest
preamble, including its Length field, that the sender of the parameter
accepts.

- The value MUST be at least 1, at most the sender's own `max_record_size`
  minus 32, and at most the sender's own `initial_max_stream_data_bidi_remote`
  and `initial_max_stream_data_uni`, so that a peer always has the record room
  and the flow-control credit for a full preamble.
- An endpoint that sends `zmux_preamble` MUST also send `reset_stream_at`.
- Violations of the rules above are TRANSPORT_PARAMETER_ERROR.
- An endpoint SHOULD NOT advertise more than one quarter of its own
  `initial_max_data`, so that several preambles fit in the initial session
  window. This is a limit on what an endpoint advertises, not a check on the
  peer's value: receivers do not enforce it. A sender whose next preamble does
  not fit in the remaining session or stream credit sends a BLOCKED frame at
  the current limit (SPEC.md Section 8.4), so the stream is never stuck
  either way.
- zmux implementations send 4096, or the smallest of their initial stream
  windows and one quarter of their `initial_max_data` when that is smaller,
  and never less than the early-data floor of 1024 (SPEC.md Section 6.1.2),
  which the floors of those windows always allow.

### 4.2 Format and placement

When the preamble is in use, the sending part of every stream that an endpoint
opens, bidirectional or unidirectional, starts at offset 0 with a preamble:

```text
Preamble {
  Length (i),
  TLV (..) ...,
}

TLV {
  Type (i),
  Length (i),
  Value (..),
}
```

- **Length**: the number of bytes of TLVs that follow. The preamble size is
  the size of the Length field plus Length.
- The preamble is stream data: it counts against stream and session flow
  control, and the application's own data starts at the offset where the
  preamble ends. The receiving application never reads the preamble bytes
  as data.
- The STREAM frame that carries offset 0 MUST contain the whole preamble. It
  may also carry application data and the FIN bit. A stream whose opener has
  nothing to say still carries an empty preamble (one byte `00`). This rule
  holds for every copy of the frame, including copies re-sent after
  reinjection (MULTIPATH.md Section 7.4).
- The peer's sending part of a bidirectional stream has no preamble.
- Because the preamble occupies the first bytes of the stream, application
  data can never overtake it on the wire.

### 4.3 TLV types

| Type | Name | Value | Rules |
|---|---|---|---|
| 0x00 | reserved | | never assigned; ignored like an unknown type |
| 0x01 | OPEN_INFO | opaque bytes | at most once; the opener's metadata for the peer application, for example a proxy target address |
| 0x02 | PRIORITY | 1 byte: urgency (3 most significant bits), incremental (next bit), 4 bits 0 | at most once; the opener's initial priority request for the peer's sending part (Section 5.4); on a unidirectional stream, which has no such part, it is checked as below and then ignored |
| 0x03 | DEADLINE | reserved | MUST NOT be sent; ignored on receipt |
| 0x05 | PIPE_CARRIER | empty | at most once; the stream is a carrier: its data, in both directions, is the record stream of a pipe pair of another zmux session (Section 10); ignored by a receiver that does not use nested sessions, and on a unidirectional stream |
| 0x1000 to 0x3fff, except GREASE values | application | opaque bytes | any number, in any order, repeated types allowed; never interpreted by zmux; delivered to the receiving application in the order received (Section 4.4) |
| 0x1f x N + 0x21 | GREASE | any | ignored on receipt |
| other | unknown | | ignored on receipt |

- A second OPEN_INFO, PRIORITY or PIPE_CARRIER TLV in one preamble is a
  PROTOCOL_VIOLATION. A PRIORITY TLV whose length is not 1 or whose low 4
  bits are not 0, and a PIPE_CARRIER TLV whose length is not 0, are a
  PROTOCOL_VIOLATION.
- **Application TLVs.** Types 0x1000 to 0x3fff (two-byte encodings) belong
  to applications, like the application error codes of Section 7: zmux
  never allocates them, never interprets them and gives them no semantics.
  The GREASE values inside the range (0x1f x N + 0x21) stay GREASE and are
  never delivered. An application TLV costs its type (2 bytes), its length
  and its value; all TLVs together stay within the receiver's Maximum
  Preamble Size (Section 4.1, 4096 bytes from zmux implementations), the
  only bound, so an application that needs more metadata than that puts it
  in the stream's data. Section 4.7 gives conventions for common metadata;
  they are examples, not part of the protocol.
- TLVs may appear in any order.
- **GREASE.** An endpoint MUST include at least one GREASE TLV, with a random
  N and 0 to 8 random value bytes, in the preamble of the first stream it
  opens in each session, and MAY include one in other preambles.
- New TLV types are registered as REGISTRY.md Section 5 describes. A new
  type whose meaning a receiver must understand needs a transport parameter;
  TLVs themselves are always ignorable.

### 4.4 Receiving

1. The receiver parses the preamble from the data of the STREAM frame at
   offset 0 alone. It MUST close the session with PROTOCOL_VIOLATION when:
   - the frame's data does not contain the complete Length field, or is
     shorter than the preamble size;
   - the preamble size exceeds the receiver's Maximum Preamble Size;
   - a TLV's Type or Length is truncated, or its Value extends beyond the
     preamble;
   - a TLV violates Section 4.3.
2. The receiver MUST close the session with PROTOCOL_VIOLATION when, for a
   stream on whose receiving part it has not sent STOP_SENDING:
   - the final size of the opener's sending part is 0;
   - the opener's sending part is reset with RESET_STREAM (0x04);
   - it is reset with RESET_STREAM_AT whose Reliable Size is smaller than the
     preamble size (checked once both are known).
3. A stream is offered to the application only once its preamble has been
   received; it does not wait for lower-numbered streams (SPEC.md Section 7.2
   rule 5), and rule 5 and Section 3.4 say when it is never offered. In a
   multipath session, data at higher offsets may arrive before the preamble;
   it is buffered within flow-control limits. Opening, reset, termination
   and release of such a stream otherwise follow SPEC.md Section 7.
4. A stream whose preamble is complete is offered to the application even if
   it is already terminal, with its OPEN_INFO, its PRIORITY, whether it
   carries PIPE_CARRIER, its application TLVs (type and value, in the order
   received) and the data and final status that arrived. The preamble's contents remain available until
   the application releases the stream (SPEC.md Section 7.6).
5. A stream on which the receiver sent STOP_SENDING before the preamble
   arrived (for example a stream rejected after GOAWAY) is never offered; its
   preamble, if it arrives, is discarded.

### 4.5 Resets

When the preamble is in use, an endpoint that resets the sending part of a
stream it opened MUST use RESET_STREAM_AT with a Reliable Size at least the
preamble size, so that the peer application learns which stream ended,
unless it is responding to STOP_SENDING, in which case it SHOULD use a
Reliable Size of 0 or RESET_STREAM (reliable-stream-reset-11 Section 5.4).
The receiver delivers the preamble and any data below the Reliable Size, then
the reset (reliable-stream-reset-11 Section 5).

### 4.6 Design notes

The preamble is one frame, length
prefixed and bounded by the receiver (SPEC.md Section 9.5); there is no header
compression and no continuation (CONTINUATION flood, HPACK bomb). It costs one
byte per stream when empty. It follows the stream-header model of WebTransport
over HTTP/3, which also protects its stream header with RESET_STREAM_AT.

### 4.7 Metadata conventions (informative)

Overlays, relays and proxies often need routing and ownership metadata at
stream open: where the stream should end, on whose behalf it runs, how many
more relays it may cross, and a trace identifier. The preamble carries such
metadata in the opening frame, so it costs no round trip and is never
overtaken by data. zmux defines none of it; applications use their TLV
range (Section 4.3), for example for a destination, an owner, a hop limit
and a trace context.

- **Hop limit and loops.** A relay that forwards a stream copies the
  metadata into the next hop's preamble, decrements the hop limit, and
  rejects a stream that arrives with hop limit 0 instead of forwarding it,
  with ZMUX_PROXY_TTL_EXPIRED (Section 7.3) on both parts. A hop limit
  bounds every forwarding loop that routing state may form while it
  converges; the bound is the application's choice, there is no protocol
  maximum.
- **Integrity.** TLVs are protected by each hop's carrier (SPEC.md Section
  2.2 property 3), not end to end: every relay can read and rewrite them.
  A receiver trusts a TLV only as far as it trusts the peer that sent it
  to have set it. Ownership metadata in particular is set by the first
  relay that authenticated the origin, from that authenticated identity,
  and a relay does not accept it from a peer that is not trusted to assert
  it; an application that needs end-to-end integrity puts a signature or
  MAC over the value into the value.
- **Placement.** A relay forwards metadata it does not understand
  unchanged, so that conventions can evolve without upgrading every relay.

---

## 5. PRIORITY_UPDATE

### 5.1 Parameter

`zmux_priority`, ID 0x31e7139d, empty value.

### 5.2 Frame

```text
PRIORITY_UPDATE Frame {
  Type (i) = 0x073d08cf,
  Stream ID (i),
  Sequence Number (i),
  Priority (8),
}
```

Priority: urgency in the three most significant bits (0 to 7), incremental in
the next bit, then four bits that MUST be 0; otherwise FRAME_ENCODING_ERROR.

### 5.3 Semantics

PRIORITY_UPDATE asks the receiver to schedule **its own sending part** of the
stream with the given priority, using the model of RFC 9218 Sections 4 and
10: urgency 0 (most urgent) to 7, default 3; incremental default false;
non-incremental streams of the same urgency are served one after the other in
stream-identifier order, incremental streams share bandwidth round-robin.
Priorities are hints; a receiver MAY ignore them. An endpoint's own sending
priorities are local and need no frame.

### 5.4 Rules

- **Applicable streams**: bidirectional streams opened by either endpoint, and
  unidirectional streams opened by the frame's receiver.
- A PRIORITY_UPDATE for a unidirectional stream opened by the frame's sender,
  or for a stream opened by the frame's receiver that the receiver has not yet
  opened, is a STREAM_STATE_ERROR.
- A PRIORITY_UPDATE for a bidirectional stream opened by the frame's sender
  that the receiver has not seen yet opens it (SPEC.md Section 7.2), subject
  to stream-count credit.
- **Sequence numbers** are per stream and per sender, strictly increasing,
  starting at 1. Sequence 0 is the implicit sequence of the PRIORITY TLV of
  the stream's preamble. A receiver applies an update only if its sequence
  number is greater than the last one applied to that stream; otherwise the
  frame has no effect (chargeable outside multipath sessions, SPEC.md Section
  9.4). This makes updates
  safe to reorder and to repeat, which a multipath session needs; HTTP/3 gets
  the same effect from its ordered control stream (RFC 9218 Section 7).
- A PRIORITY_UPDATE for a terminal or discarded stream has no effect.
- In a multipath session the latest update per stream is re-sent when its
  pipe fails (MULTIPATH.md Section 7.4).

No dependency trees, stream groups or weights exist (RFC 9113 Appendix B;
CVE-2019-9513).

---

## 6. Datagrams

### 6.1 Base

zmux uses the DATAGRAM frames of RFC 9221 as QMux-02 Section 9.1 permits:

- support for receiving is announced with `max_datagram_frame_size` (0x20);
  zmux implementations send their own `max_record_size` (65535) when the
  application enables datagrams, the QMux equivalent of RFC 9221's
  recommended 65535 (QMux-02 Section 9.1);
- a DATAGRAM received without having announced support, or larger than
  announced, is a PROTOCOL_VIOLATION (RFC 9221 Section 3);
- the largest DATAGRAM frame is bounded by the peer's `max_datagram_frame_size`
  and by the peer's `max_record_size` (QMux-02 Section 9.1);
- DATAGRAM frames are not flow controlled (RFC 9221 Section 5.3).

**No fragmentation.** zmux never splits a datagram across frames or records
and never reassembles one. A datagram that does not fit is refused locally
with a "too large" error; the application then uses a stream.

### 6.2 Datagram flows parameter

`zmux_datagram_flows`, ID 0x0af391ed, empty value. When it is in use, every
DATAGRAM's data has the format of Section 6.3. When it is not in use, the
data is opaque to zmux (RFC 9221 Section 5.1).

### 6.3 Flow prefix

```text
Datagram Data {
  Flow (i),
  [Context ID (i)],
  Payload (..),
}
```

- **Flow**: 0 for a datagram that belongs to the session as a whole; n > 0
  for a datagram associated with the bidirectional stream with identifier
  n - 1.
- **Context ID**: present if and only if Flow > 0. An application-defined
  value that distinguishes kinds of datagrams within one association, in the
  style of RFC 9298 Section 4: 0 is the default context (for example UDP
  payloads to the stream's target); other values are allocated by convention
  of the application, even values by the stream's opener and odd values by
  the other endpoint.
- A truncated Flow or Context ID is a PROTOCOL_VIOLATION.

The stream carries the association's life cycle: its preamble names the
target, its end ends the association, and stream-count credit bounds the
number of associations (the MASQUE model of RFC 9297 and RFC 9298). No
separate association identifiers, dissociate messages or garbage collection
exist.

### 6.4 Association rules

- Flow - 1 MUST be a bidirectional stream ID; a unidirectional one is a
  PROTOCOL_VIOLATION.
- A locally initiated stream that has not been opened: PROTOCOL_VIOLATION.
- A peer-initiated stream with an index at or above the limit the receiver
  advertised: PROTOCOL_VIOLATION.
- A peer-initiated stream within the limit that has not been opened yet
  (possible in multipath sessions): the receiver drops the datagram or holds
  it for at most one smoothed RTT until the stream opens (RFC 9297 Section
  2.1); held datagrams count against the byte limit of Section 6.5. A
  multipath sender SHOULD avoid this: while the opening frame of a stream is
  unacknowledged, it sends that stream's datagrams after it on a pipe that
  carries the frame or a reinjected copy of it, and while no such pipe may
  carry DATAGRAM frames (MULTIPATH.md Section 11 rule S1) it keeps them in
  its send queue, so they cannot overtake the opening frame.
- A stream whose parts are both terminal, that the application has
  released, or whose state is discarded: the datagram is dropped and
  counted. This is not an error and not chargeable.
- DATAGRAM frames never open streams.

### 6.5 Queues and loss

- A sender keeps a bounded queue of datagrams waiting for a pipe; when it
  is full, the oldest is dropped. A datagram may also be dropped when it has
  waited longer than an application-set maximum age. Sending never blocks.
- A receiver keeps bounded queues of datagrams the application has not
  read, following the per-stream model of RFC 9297 Section 2: datagrams
  with Flow 0, and all datagrams when datagram flows are not in use, go to
  the session queue; datagrams with Flow > 0 go only to the queue of their
  stream, never to the session queue. When a queue is full, its oldest
  datagram is dropped. All received datagrams not yet read, held for
  streams not yet opened or on their way to a queue, together stay within a
  session-wide byte limit; a datagram that would exceed it makes room by
  dropping the oldest unread datagrams of the session.
- Drops never cause an error or a budget charge.
- In a session that is not a multipath session, a datagram that has been
  written to the carrier is delivered (QMux-02 Section 1.1). In a multipath
  session, datagrams are never reinjected, and are lost with a failed pipe: they are truly
  unreliable.
- zmux never sends a datagram twice: the redundant copies of urgent stream
  data (MULTIPATH.md Section 7.5) and opportunistic reinjection apply to
  stream data only. A datagram is dropped, never queued without bound,
  when its queue is full; an application that needs redundancy sends it
  twice itself.

### 6.6 Datagram sizes

A DATAGRAM frame costs its type (1 byte), a Length field when it is not
the last frame of its record (2 bytes for frames of 64 bytes to 16383
bytes, 4 above), and, with datagram flows in use, the Flow field (1 byte for
Flow 0; 1 to 8 bytes for a stream's Flow) and, for Flow > 0 only, the
Context ID (1 byte for values below 64). The largest payload is therefore the smaller of the
peer's `max_datagram_frame_size` and its `max_record_size`, minus that
overhead:

| Peer | Largest frame | Largest session datagram payload |
|---|---|---|
| zmux endpoint (both parameters 65535) | 65535 | 65533 with datagram flows (type and Flow 0), 65534 without |
| plain QMux peer with the default record size and a `max_datagram_frame_size` of at least 16382 | 16382 | 16381 (datagram flows are never in use with it) |

A stream's datagrams cost the length of the stream's Flow and Context ID
more. Every IP packet of an MTU up to 65 000 bytes fits a zmux peer, and
the packets of 1500-byte and 9000-byte (jumbo) links fit every zmux peer,
and every plain QMux peer whose parameters allow frames of that size, with
room for the overhead, over a single pipe and over every pipe of a multipath session alike: the
size limits are those of the peer, not of a pipe. Over TLS a sender keeps
records at 16382 bytes of frames (SPEC.md Section 3.3), so datagrams up to
that size share a record with other frames and larger ones occupy a record
of their own. Implementations report the current largest payload, which
applications use as the MTU of a packet interface.

### 6.7 Datagrams across relays and nested sessions

Datagram semantics are per session and per hop:

- **Relayed hop by hop** (a relay forwards the datagrams of one session,
  or of a stream's datagram flow, to another session):
  every hop applies its own queues and drops. A datagram is delivered end
  to end at most once, may be lost at any hop, and is not reordered on a
  single-pipe hop, but may be reordered across the pipes of a multipath
  hop. A relay never queues datagrams beyond the destination session's
  send queue: forwarding never blocks and never waits for credit, since
  DATAGRAM frames are not flow controlled.
- **Inside a nested session** (Section 10) the nested session's datagrams
  travel in the records of its pipes, and a pipe that runs over a relayed
  stream is reliable and ordered: such datagrams are not lost at the relays
  and arrive in order on that pipe, at the price of waiting behind any
  stream data queued before them on it. They are still dropped by the
  nested session's own queues, and lost with a pipe that fails. Traffic
  that relies on loss rather than delay, such as IP packets whose own
  transport recovers loss, is better relayed hop by hop.

---

## 7. Application error-code conventions

Application error codes belong to the application (SPEC.md Section 10.1).
zmux reserves the block 0x5a4d0000 to 0x5a4d0fff for conventions that zmux
libraries and zmux-based applications share; applications SHOULD NOT use that
block for their own meanings. All values fit in 32 bits for adapters whose
QUIC library limits error codes to 32 bits.

### 7.1 General codes

| Code | Value | Meaning |
|---|---|---|
| NO_ERROR | 0x0 | no error; for STOP_SENDING, "no more data wanted" |
| ZMUX_CANCELLED | 0x5a4d0001 | the endpoint abandoned the stream or operation; data may or may not have been processed |
| ZMUX_REJECTED | 0x5a4d0002 | the stream was not processed (Section 7.2) |
| ZMUX_CARRIER_FAILED | 0x5a4d0003 | the stream carried something that failed: it was a pipe of a nested session that the nested session failed or aborted, or one hop of a relayed stream whose other side was lost (Section 10); data already delivered stands, the rest is lost, and the stream is not safe to retry blindly |

### 7.2 REJECTED

ZMUX_REJECTED asserts that **none of the stream's data was processed by the
application of the endpoint that sent the code**, so the stream's opener can
retry it, on this session or another, without risk of duplicate processing
(the meaning of HTTP/2 REFUSED_STREAM, RFC 9113 Section 8.7, and
H3_REQUEST_REJECTED, RFC 9114 Section 8.1). A stream rejected after GOAWAY
was never offered to that application; one that the application declined was
offered, but its data was not read.

- Only the endpoint that did not open the stream may send ZMUX_REJECTED.
- It is sent with RESET_STREAM on its sending part (bidirectional streams)
  and STOP_SENDING on its receiving part.
- It is used after GOAWAY (Section 3.4), by a stack under local memory
  pressure for new peer streams, which are rejected without allocating
  receive buffers and are never offered to the application, and when an
  application declines a stream before reading any of its data.
- A stream opener that receives ZMUX_REJECTED reports a retryable error.

### 7.3 Proxy open-failure codes

A proxying receiver that cannot open the target named in a stream's
OPEN_INFO reports the reason with RESET_STREAM on its sending part and
STOP_SENDING on its receiving part. Opening succeeds silently: the opener
sends its data without waiting (no round trip), and the failure arrives
asynchronously. The codes map to SOCKS5 replies ([RFC1928] Section 6) so that
SOCKS front ends can report them unchanged.

| Code | Value | SOCKS5 REP |
|---|---|---|
| ZMUX_PROXY_GENERAL_FAILURE | 0x5a4d0101 | 0x01 general failure |
| ZMUX_PROXY_NOT_ALLOWED | 0x5a4d0102 | 0x02 not allowed by ruleset |
| ZMUX_PROXY_NETWORK_UNREACHABLE | 0x5a4d0103 | 0x03 network unreachable |
| ZMUX_PROXY_HOST_UNREACHABLE | 0x5a4d0104 | 0x04 host unreachable |
| ZMUX_PROXY_CONNECTION_REFUSED | 0x5a4d0105 | 0x05 connection refused |
| ZMUX_PROXY_TTL_EXPIRED | 0x5a4d0106 | 0x06 TTL expired |
| ZMUX_PROXY_COMMAND_NOT_SUPPORTED | 0x5a4d0107 | 0x07 command not supported |
| ZMUX_PROXY_ADDRESS_TYPE_NOT_SUPPORTED | 0x5a4d0108 | 0x08 address type not supported |
| ZMUX_PROXY_DNS_FAILURE | 0x5a4d0110 | 0x04 host unreachable |
| ZMUX_PROXY_CONNECT_TIMEOUT | 0x5a4d0111 | 0x06 TTL expired |

For REP values 0x01 to 0x08 the code is 0x5a4d0100 + REP. These codes also
imply that no stream data was forwarded to the target.

### 7.4 Codes chosen by libraries

When a zmux library terminates a stream because of a local API action that
carries no explicit code, it uses code 0:

- RESET_STREAM (or RESET_STREAM_AT covering the preamble) with code 0 when the
  application drops or closes a sending part without an explicit finish
  (a handle released, closed or collected while unfinished, or a scope left
  through an error). Only an explicit finish
  (FIN) is a clean end; a reset is never complete (SPEC.md Section 7.5);
- STOP_SENDING with code 0 when the application drops or closes a receiving
  part before reading its end;
- ZMUX_REJECTED as Section 7.2 describes;
- ZMUX_CARRIER_FAILED when a stream used as a carrier is aborted (Section
  10.2) and when a relay loses the other side of a spliced stream (Section
  10.5).

ZMUX_CANCELLED is for applications that want to say "abandoned" explicitly;
libraries never choose it on their own.

---

## 8. Transport error codes

zmux transport error codes are part of the core protocol and are defined in
SPEC.md Section 10.2: ZMUX_EXCESSIVE_LOAD, ZMUX_ROLE_CONFLICT,
ZMUX_RESUME_TIMEOUT, ZMUX_RESUME_FAILED, ZMUX_PIPE_FAILED,
ZMUX_PIPE_RESOURCE_LIMIT, ZMUX_IDLE_TIMEOUT and the local-only
ZMUX_PIPE_AUTH_FAILED.

---

## 9. Reserved extension points

The codepoints and frameworks below are reserved (REGISTRY.md Section 7). Endpoints of this specification MUST NOT use them.

| Extension point | Reserved | Notes |
|---|---|---|
| DEADLINE preamble TLV | TLV type 0x03 | relative deadline in milliseconds (variable-length integer), relative because clocks differ |
| carrier implicit acknowledgement | behaviour only | adapters such as HTTP POST, whose 2xx response proves full delivery, could report it as an acknowledgement; endpoints MUST NOT treat carrier events as acknowledgements |
| qlog event schema | none | QUIC qlog events plus pipe events |
| padding and traffic-shaping hooks | none | adapter level; PADDING-only records are limited by SPEC.md Section 9.4.3 |
| record protection | transport parameter 0x1dbe26bc | AEAD protection of records for carriers that cannot protect end to end, with keys from the session secret; receivers ignore the parameter until it is specified |

---

## 10. Nested sessions and relays

### 10.1 Model

A stream of one zmux session (the **outer** session) can be the carrier of
a pipe pair of another zmux session (the **nested** session). Two
endpoints then keep one end-to-end nested session whose pipes run over
different routes: for example a direct carrier, and streams that cross one
or more **relays**, each relay being an endpoint of two outer sessions that
splices a stream of one to a stream of the other (Section 10.5). The
nested session's tiers, failure detection, reinjection, redundancy and
resume (MULTIPATH.md, RESUME.md) then move its traffic from
one route to another without its streams noticing, which per-hop relaying
of each stream cannot do: a stream relayed hop by hop dies with any relay
it crosses.

Nothing here changes the wire format of either session. The outer session
sees ordinary streams; the nested session sees ordinary pipes. The
PIPE_CARRIER TLV (Section 4.3) and the code ZMUX_CARRIER_FAILED (Section
7.1) only let endpoints and relays recognise such streams and report their
failures.

### 10.2 The stream carrier

An implementation that offers streams as carriers behaves as follows.

- **One bidirectional stream is one pipe pair**, with byte-stream framing
  (SPEC.md Section 3.1). The endpoint that opens the stream is the pipe's
  joiner, or the initiator for a bootstrap pipe; the opener's sending part
  is the up direction (MULTIPATH.md Section 2.1).
- **Preamble.** The opener's preamble carries PIPE_CARRIER, plus any
  application TLVs (for example routing metadata, Section 4.7). The pipe's
  record stream starts at the first byte after the preamble, so positions
  (MULTIPATH.md Section 2.3) count from there; in the other direction it
  starts at offset 0. A receiver that does not use nested sessions offers
  such a stream to its application like any other. PIPE_CARRIER on a
  unidirectional stream is checked as Section 4.3 says and then ignored.
  When the outer session does not use the preamble (a plain QMux peer),
  the stream carries no TLV, the record stream starts at offset 0 in both
  directions, an abort uses RESET_STREAM, and the receiving application
  recognises the carrier stream by its own means.
- **Operations.**

| Pipe operation | Stream operation |
|---|---|
| write | write; "not writable" while the stream lacks stream or session credit or send-buffer space, the backpressure every adapter has |
| graceful end of the sending half | finish (FIN) |
| abort of the sending half | reset with ZMUX_CARRIER_FAILED (RESET_STREAM_AT with the preamble as Reliable Size on the opener's part, Section 4.5) |
| close of the receiving half before its end | stop with ZMUX_CARRIER_FAILED |
| end of the received data (FIN) | graceful end of the inbound pipe |
| reset received (any code) | failure of the inbound pipe |
| STOP_SENDING received (any code) | failure of the outbound pipe |
| outer session closed or lost | failure of both pipes |

- **No queue capabilities.** The adapter reports neither unsent bytes nor
  a low-water mark nor a carrier RTT: bytes waiting in the outer session's
  send buffer, in each relay and in each hop's carrier form a queue the
  adapter cannot see; only the nested session's own acknowledgements
  (multipath or pipe acknowledgement, MULTIPATH.md Section 3.5) show it.
- **Lanes.** The adapter reports the outer session as the carrier
  connection and can open further streams of it, each with PIPE_CARRIER, as
  lanes of the pipe (MULTIPATH.md Sections 2.5 and 5.8; the receiving
  application hands them to the nested session like any carrier
  stream). A lane carries the nested
  peer's Lane Key, so it reaches its pipe also through a relay, as long as
  the relay forwards it over the same outer session as the pipe's lane 0
  (otherwise it names no pipe on the connection it arrives on and is
  refused, which costs the pipe nothing); other clients of the relay, which
  do not know the key, cannot attach a stream to the pipe. Whether lanes
  pay off depends on the outer sessions' own pipes (MULTIPATH.md Section
  2.5).
- **Join security.** Joins over relayed streams are authenticated by the
  nested session's secret (MULTIPATH.md Section 10); a relay cannot forge
  or redirect them. A relayed stream carries no TLS of its own, so the
  nested session's secret source is a pre-shared key or the exporter of
  a bootstrap carrier whose TLS ends at the peer (MULTIPATH.md Section
  10.1). Records are visible to relays, which are protected by each hop's
  carrier (SPEC.md Section 2.2); an application that needs confidentiality
  from its relays runs TLS over the stream, as over any untrusted carrier.
- **No pipe of a session may run over that session**, directly or through
  other sessions: its credit and acknowledgements would then depend on its
  own progress. Nesting has no other depth limit. Relays bound the loops
  that routing state can form with a hop limit (Section 4.7).
- A nested session with a plain QMux implementation at one end is a
  single-pipe session without acknowledgements: it works, but its sender
  cannot see the relays' queue.

### 10.3 Flow control and freedom from deadlock

Two flow controls are stacked and independent. The nested session's
credit bounds what its streams may have outstanding end to end; each
outer session's credit bounds each hop. Neither waits for the other in a
cycle:

1. The nested sender's write on a relayed pipe waits for the first hop's
   credit, which the first relay releases when it reads that stream.
2. A relay forwards from hop i to hop i + 1 only while hop i + 1 has credit
   (Section 10.5): hop i waits for hop i + 1 and never for anything
   upstream of it.
3. The last hop's credit is released when the nested receiver reads its
   pipe. Every endpoint reads every pipe continuously, whatever its
   application does (SPEC.md Section 9.1), never lets a read wait behind a
   write, and holds only what the nested
   session's own credit allows, which the nested peer cannot exceed.

Each chain of waits therefore ends at a reader that does not wait.

Chains meet in the outer sessions' shared session credit. A relay that
cannot forward a stream (its downstream gives no capacity) stops reading
it, and the stream then holds its receive window in the outer session.
Below the buffer cap of SPEC.md Section 8.3 rule 4 that slows no other
stream, but stalled streams whose windows had grown can fill the cap,
and then every stream of that outer session waits for session credit,
other nested pipes and other relayed streams included. What ends such a
wait depends on what the stalled streams carry:

- **Carrier streams.** A lasting stall is a stall of a nested pipe, which
  the nested session detects like any stalled pipe (MULTIPATH.md Section
  7.1) and fails: it aborts the pipe, the reset travels hop by hop without
  waiting for capacity (Section 10.5), every hop releases the
  session credit of the lost bytes at once (SPEC.md Section 8.3), and the
  nested session reinjects the pipe's unacknowledged data on its other
  pipes. An outer session that carries only carrier streams is therefore
  never blocked beyond the nested sessions' failure detection, although a
  pipe that waited behind others at the cap may be failed with them and
  then recovers by reinjection and a new join.
- **Streams relayed hop by hop** (Section 10.5) have no such detector. A
  stream whose final reader stops reading holds its window at every hop
  until an application reads or ends it: head-of-line blocking bounded by
  the buffer cap and the memory budget, as on any session whose
  application stops reading streams whose windows grew, not a credit
  deadlock.

Relays keep stalled streams from reaching the cap either by bounding the
receive windows of the streams they splice so that the streams that may stall together stay below the
outer session's buffer cap, or, more simply, by carrying carrier streams
and streams relayed hop by hop, or traffic classes that must not block one
another, over separate outer sessions. A cycle of waits through several
sessions, should routing ever create one, thus ends with the nested
session's failure detection for carrier streams and with the application
for streams relayed hop by hop; no rule of the protocol makes it
permanent.

### 10.4 Overhead

Every nested record is outer stream data, so it pays a second STREAM frame
header and record Size. The nested session's PIPE_ACK travels end to
end as outer stream data, and each hop acknowledges its own pipes as usual.

### 10.5 Relays

Relays SHOULD splice the stream's data verbatim and
SHOULD NOT parse the nested session's records:

- the data is forwarded without copying and without per-nested-stream
  state: a relay's cost per byte is a hand-over of buffer ownership from
  one stream to the other;
- acknowledgement, failure detection, reinjection and resume stay end to
  end, where both endpoints see every route;
- the relay's failures are reported to the nested session as pipe
  failures, which is all it needs.

The price is visibility: a relay sees one stream per nested pipe and its
bytes, which include the nested session's framing, acknowledgements,
reinjected data and redundant copies. Exact application byte counts exist
only at the nested session's endpoints. An application
that needs per-stream metadata or exact per-stream accounting at a relay
relays those streams hop by hop instead (one outer stream per application
stream, spliced at each relay, with the metadata of Section 4.7), and gives
up end-to-end migration for them. Both can run side by side over the same
outer sessions.

A relay that forwards a carrier stream opens the next hop's stream with
PIPE_CARRIER as well, so that the last hop can tell
it apart. A relay forwards data from one hop to the next only while the
next hop has credit. It forwards a plain reset or a STOP_SENDING with its
code to the other side at once, and on the loss of one side's session
resets and stops the other side's stream with ZMUX_CARRIER_FAILED at once;
on a stream opened with a preamble that reset is RESET_STREAM_AT with a
Reliable Size covering the downstream preamble (Section 4.5). A reset with
Reliable Size s SHOULD be forwarded reliably: the relay forwards the bytes
below s, as the next hop's credit allows, then resets the downstream stream
with RESET_STREAM_AT, the same code and a Reliable Size covering the
forwarded bytes in the downstream stream's offsets (its preamble
included); when the downstream session does not use RESET_STREAM_AT (a
plain QMux peer that did not announce it), it resets with RESET_STREAM once
the forwarded bytes have been passed to the carrier.

---

## 11. Duplicate sessions

Two endpoints that dial each other at about the same time, for example the
two sides of an overlay link, end up with two sessions where they wanted
one. The rule below picks the same survivor at both ends from information
both have, so that each can act alone. It uses only existing frames.

**When it applies.** The **dialer** of a session is the endpoint that
established its bootstrap carrier (the TCP, TLS or HTTP client, or for a
symmetric carrier the endpoint the application designates), whatever role
negotiation later decides (Section 2). An endpoint applies the rule to two
established sessions S1 and S2 (transport parameters exchanged) that its
application has identified as connecting the same two endpoints, for
example by the authenticated identity of the peer, and that were dialed by
different endpoints: one by each side. Two sessions with the same dialer
are not a race; the dialer alone decides which to keep, and its peer
leaves both to it. The rule works only when both endpoints apply it;
towards a peer that does not (a plain QMux peer included), the application
decides alone, accepting that the peer may keep opening streams on the
session it closes.

**Survivor.** Each endpoint compares, for each session, a value of the
session's dialer:

1. the application identities of the two dialers, when the application
   supplies them (the same byte strings at both ends, compared bytewise):
   the session dialed by the endpoint with the smaller identity survives;
2. otherwise, when both sessions use multipath, the Session Nonce that the
   dialer sent in `zmux_multipath` (MULTIPATH.md Section 3.1): the session
   whose dialer's nonce is smaller survives;
3. otherwise, when both sessions negotiated roles with two `auto` nonces
   (Section 2), the dialer's role nonce, in the same way.

Without any of these the rule does not apply, and the application decides
(it can always supply identities). Equal values mean that an endpoint
reached itself, which role negotiation reports as ZMUX_ROLE_CONFLICT
(Section 2.3).

**Draining the other session.** Both endpoints stop opening streams on the
losing session at once and open new ones on the survivor. Each drains the
losing session: two-phase GOAWAY when GOAWAY is in use (Section 3), so
that streams the peer opened concurrently are either processed or reported
as retryable, then an application close with NO_ERROR once its streams
have ended (SPEC.md Section 6.7). Both ends sending GOAWAY is harmless. Without
GOAWAY in use the losing session is closed after its streams have ended.

**Keeping the route.** The losing session's carrier cannot become a pipe of
the survivor: its byte stream belongs to the losing session from its first
record, and changing that would need a new frame. When the survivor uses
multipath, the endpoint that dialed the losing session MAY instead dial
the same route again and join the new carrier to the survivor (MULTIPATH.md
Section 5), so that the route is kept at the cost of one carrier handshake,
made after the decision and off the path of any stream.

**Avoiding the race.** An endpoint that already has an established
multipath session with a peer joins a new carrier to it instead of
starting another session; the race then exists only while both endpoints
dial within one establishment time of each other.
