# zmux Negotiated Features

Status: normative. Bilateral features are in use as defined in SPEC.md
Section 5.5. Section 6 defines datagrams, including the QMux form and the
rules for sessions between zmux endpoints. Sections 7, 10 and 11 define
conventions built on the core protocol and its extensions.

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
error and SHOULD NOT retry automatically.

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
watermark.

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

ZMUX_REJECTED is the only stream termination that a zmux endpoint initiates
on its own (SPEC.md Section 4.3): after GOAWAY as above, and when it rejects
new peer streams under local resource pressure instead of withholding stream
credit. An application may also decline a stream before reading any of its
data.

### 3.5 Receiving

An endpoint B that receives GOAWAY:

1. keeps, per stream type, the minimum watermark of all GOAWAY frames
   received. A GOAWAY that lowers neither watermark has no effect and, outside
   multipath sessions, is chargeable (SPEC.md Section 9.4); a larger watermark
   may be an outdated
   frame delivered late in a multipath session and is ignored, not an error;
2. MUST NOT open any new stream, whatever the watermarks, as HTTP/2 (RFC
   9113 Section 6.8) and HTTP/3 require: A is draining. Local open attempts
   fail with a retryable "going away" error, and so
   does every stream the application opened that has neither emitted its
   first frame nor been pinned (an open that waited for credit, or a stream
   opened before establishment): it sends nothing, holds no identifier and
   fails with the same "going away" error. A stream that has emitted its
   first frame, or is pinned (pinning counts as the first frame, SPEC.md
   Section 7.2), is not new: below the watermark it continues, a pinned
   stream sending its opening frame as soon as credit allows; at or above the
   watermark it is handled by rule 3, and a pinned identifier there is never
   sent.;
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

- The value MUST be at least 1, at most 8160 (the carrier record floor of
  SPEC.md Section 3.2 minus 32, so that the offset-0 STREAM frame carrying a
  full preamble fits any carrier), at most the sender's own `max_record_size`
  minus 32, and at most the sender's own `initial_max_stream_data_bidi_remote`
  and `initial_max_stream_data_uni`, so that a peer always has the record room
  and the flow-control credit for a full preamble.
- An endpoint that sends `zmux_preamble` MUST also send `reset_stream_at`.
- Violations of the rules above are TRANSPORT_PARAMETER_ERROR.
- A sender whose next preamble does not fit in the remaining session or
  stream credit sends a BLOCKED frame at the current limit (SPEC.md Section
  8.4).

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
  Preamble Size (Section 4.1), the
  only bound, so an application that needs more metadata than that puts it
  in the stream's data. Section 4.6 gives conventions for common metadata;
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

### 4.6 Metadata conventions (informative)

Applications may carry routing and ownership metadata (destination, owner,
hop limit, trace context) in application TLVs; zmux defines none of it. A
relay that forwards such metadata decrements a hop limit and rejects a
stream that arrives with hop limit 0 with ZMUX_PROXY_TTL_EXPIRED (Section
7.3) on both parts. TLVs are protected by each hop's carrier (SPEC.md
Section 2.2 property 3), not end to end; a relay forwards metadata it does
not understand unchanged.

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
  9.4).
- A PRIORITY_UPDATE for a terminal or discarded stream has no effect.
- In a multipath session the latest update per stream is re-sent when its
  pipe fails (MULTIPATH.md Section 7.4).

No dependency trees, stream groups or weights exist (RFC 9113 Appendix B;
CVE-2019-9513).

---

## 6. Datagrams

### 6.1 Base

The frame encoding and unilateral `max_datagram_frame_size` parameter follow
RFC 9221 Sections 3 and 4 as permitted by QMux-02 Section 9.1. An endpoint
MUST NOT send a DATAGRAM without a positive value from the receiver, and
MUST close the session with PROTOCOL_VIOLATION if a well-formed DATAGRAM is
received without having advertised support or exceeds the advertised size
(SPEC.md Section 10.3). DATAGRAM is exempt from flow control
(RFC 9221 Section 5.3).

### 6.2 Wire format selection

With a plain QMux peer (SPEC.md Section 13), an endpoint MUST treat the
Datagram Data as opaque application bytes. Sections 6.3 to 6.5 and 6.8 to 6.10
apply only between zmux endpoints.

The `zmux_datagram_flows` parameter (0x0af391ed) is retired. Endpoints
MUST NOT send it. A receiver MUST close the session with
TRANSPORT_PARAMETER_ERROR if the parameter is present, regardless of its
value (SPEC.md Section 5.5).

### 6.3 Datagram Data

A DATAGRAM between zmux endpoints MUST use the following Datagram Data format.

```text
Datagram Data {
  Sequence (i),
  Flow (i),
  [Context ID (i)],
  Payload (..),
}
```

- **Sequence** identifies the logical datagram in its sending direction
  (Section 6.9).
- **Flow** is 0 for a session datagram. A positive value identifies
  the bidirectional stream with ID Flow - 1.
- **Context ID** MUST be present exactly when Flow is positive. Context 0 is
  the default context. Other contexts have application-defined meanings,
  with even values allocated by the stream opener and odd values by its
  peer.
- A receiver MUST treat a truncated required field as PROTOCOL_VIOLATION,
  subject to the early discard in Section 6.9.

### 6.4 Association rules

For Flow > 0:

- The receiver MUST treat a unidirectional stream ID, an unopened locally
  initiated stream or a peer-initiated stream at or beyond its advertised
  limit as PROTOCOL_VIOLATION.
- For a peer-initiated stream within the limit that has yet to open, the
  receiver MAY hold the datagram pending the opening frame for at most one
  smoothed RTT. It MUST discard the datagram if it cannot hold it or its
  holding period expires. Holding and release follow Sections 6.5 and 6.9.
- The receiver MUST discard a datagram for a stream whose two parts are
  terminal, that the application has released or whose state was discarded.
- A DATAGRAM MUST NOT open a stream.

For multipath placement before the opening frame is acknowledged, see
MULTIPATH.md Section 11 rule S7.

### 6.5 Delivery and loss

A receiver MUST deliver Flow 0 datagrams to the session and Flow > 0
datagrams only to the associated stream. It MUST bound the total bytes of
unread and held datagrams and MAY discard datagrams when there is insufficient
space. Datagram delivery has no ordering guarantee. Datagram discards are
neutral under the no-effect accounting (SPEC.md Section 9.4).

A sender MUST NOT retransmit or reinject a datagram. It MAY dispatch extra
copies under MULTIPATH.md Section 7.5.2.

### 6.6 Datagram sizes

A sender MUST fit a datagram in one frame and use a pipe on which the
entire frame fits in a record or in the carrier datagram channel (Section 6.8).
The frame fits within the peer's advertised `max_datagram_frame_size` and,
for records, within the applicable record size limit (SPEC.md Section 3.2).
`max_datagram_frame_size` counts only the DATAGRAM frame, including its Type
and Datagram Data. On a channel, the complete channel datagram MUST fit the
carrier's channel size limit and follow the format in Section 6.8.

The frame overhead is one byte for the type (0x30 or 0x31), plus the encoded
Length for type 0x31. For a zmux peer the sender also subtracts the encoded
sizes of Sequence, Flow and, for Flow > 0, Context ID. A shortest-form
Sequence occupies 1, 2, 4 or 8 bytes at the boundaries 64, 2^14 and
2^30. A shortest-form Flow 0 occupies one byte.

| Peer | Largest record DATAGRAM frame | Largest Flow 0 or plain QMux payload, type 0x30 |
|---|---|---|
| zmux peer with both limits 65535 and a pipe that can carry that size | 65535 | 65532, 65531, 65529 or 65525 bytes for a Sequence of 1, 2, 4 or 8 bytes, respectively |
| plain QMux peer with the default record size and a `max_datagram_frame_size` of at least 16382 | 16382 | 16381 bytes |

### 6.7 Datagrams across relays and nested sessions

Datagram semantics apply per session. A relay forwarding a datagram between
sessions sends it as a new datagram in the destination session. A nested
session (Section 10) uses its own sequence space regardless of the sequences
of the outer session.

### 6.8 Datagrams on a carrier's datagram channel

Some carriers have an unreliable datagram service of their own besides the
reliable stream that is the pipe: QUIC DATAGRAM frames (RFC 9221) on the
connection a QUIC stream pipe belongs to, HTTP Datagrams (RFC 9297) of the
HTTP/3 request stream that is the pipe, and the datagrams of the
WebTransport session a WebTransport stream pipe belongs to. A DATAGRAM frame
can be sent in a carrier datagram.

`zmux_carrier_datagrams`, ID 0x3d274c5f, empty value: the sender accepts
DATAGRAM frames on the datagram channels of its pipes. An endpoint sends it
only when it has announced datagram support (`max_datagram_frame_size` >
0, Section 6.1). The channel of a pipe is **in use** when both endpoints sent
the parameter and the pipe's carrier has the channel (negotiated by the
carrier itself: QUIC `max_datagram_frame_size`, HTTP/3
`SETTINGS_H3_DATAGRAM`, WebTransport datagram support).

`zmux_carrier_datagram_padding`, ID 0x2071d4ee, empty value, declares support
for the PADDING field below. It requires `zmux_carrier_datagrams` and a
positive `max_datagram_frame_size` in the same transport parameter block
(SPEC.md Section 5.5). Channel padding is **in use** on a pipe when both
endpoints sent `zmux_carrier_datagram_padding` and the pipe's channel is in
use. The exchanged parameters also apply after resuming the same session
(SPEC.md Section 5.2).

**Format.** A channel datagram MUST use this format:

```text
Channel Datagram {
  [Pipe Stream ID (i)],
  [PADDING (..)],
  Type (i) = 0x30,
  Datagram Data (..),
}
```

- **Pipe Stream ID** is the carrier stream ID of the pipe, as the carrier
  numbers it (the QUIC stream ID, or the WebTransport stream's ID within its
  QUIC connection), present on QUIC and WebTransport channels. On an HTTP/3
  channel, the HTTP Datagram's Quarter Stream ID names the request stream
  and the channel datagram starts at the next field.
- **PADDING** is zero or more one-byte PADDING frames (`0x00`, SPEC.md
  Section 4.3), permitted only when channel padding is in use. When present,
  PADDING and the DATAGRAM frame together occupy at most 65535 bytes. This
  limit excludes the Pipe Stream ID and is independent of `max_record_size`.
- **Type** starts the single DATAGRAM frame. Type `0x30` has no Length field.
  **Datagram Data** (Section 6.3) consumes the remainder of the carrier
  datagram, including any trailing zero bytes.

**Sending.** A sender MAY use a pipe's channel only after the session is
established (SPEC.md Section 6.1) and the pipe attachment is available for
sending with its channel in use, subject to Section 6.6 and, in multipath
sessions, MULTIPATH.md Section 11 rule S7.

**Receiving.** The receiver MUST resolve the pipe from its carrier stream
association before examining the frame region. It MUST discard a datagram
that cannot be routed to an attached pipe or whose pipe's channel is out of
use. For a datagram routed to a channel in use, the receiver MUST close the
session with PROTOCOL_VIOLATION if the frame region violates the format
above. Sections 6.1, 6.3 to 6.5, 6.9 and 6.10 apply to the DATAGRAM frame.
Channel datagrams do not advance PIPE_ACK positions or restart idle timers.

**Relation to other mechanisms.** The plain QMux rules are in Section 6.2
and SPEC.md Section 13. A pipe that is a stream of a nested
session, or a relayed carrier, has no channel; hop-by-hop relaying (Section
6.7) uses each hop's channel where that hop has one.

### 6.9 Sequence and deduplication

A sender MUST use one counter per session direction across all Flows,
pipes and channels. It MUST assign the next Sequence exactly once, starting
at 0 and incrementing by one, immediately before dispatching the first copy
of a logical datagram. The sender MUST stop sending new logical datagrams
after assigning 2^62 - 1. Every copy MUST carry identical Datagram Data
(Sequence, Flow, Context ID if present and Payload).

A receiver MUST maintain one deduplication window per session direction
across Flows, pipes and channels. Its width W is local and MUST be between
64 and 16384 inclusive. The first datagram with a valid header and
association establishes the highest seen value H. When a valid Sequence q exceeds H,
the receiver MUST advance H to q and forget occupied positions outside the
new window, even if admission fails. A Flow eligible for holding, or one
whose stream has since closed, is a valid association for this purpose.

Once H is established, the receiver MUST discard q if q <= H - W or if
its position in the window is already occupied. This check follows
validation of the outer frame and parsing of Sequence, and precedes parsing
Flow, association checks and queue or holding-space allocation. Section 6.10 accounts for arrivals
independently of the deduplication outcome.

- The receiver MUST occupy an eligible position only on successful
  admission to a delivery queue or to holding space pending a stream's
  opening frame. Admission and occupation MUST take effect together.
- A duplicate MUST NOT refresh the holding deadline. When a held datagram
  expires before it can be admitted to a delivery queue, the receiver MUST
  clear its position if still in the window. It MUST also clear the
  position if transfer from holding space to the delivery queue fails.
- A position committed to a delivery queue MUST remain occupied until it
  leaves the window, including after application delivery.

The sender and receiver MUST preserve the counter and deduplication window
when the session resumes (RESUME.md Section 6). A separate session has
independent sequence and deduplication state.

### 6.10 DATAGRAM_ACK

An endpoint MAY send DATAGRAM_ACK in a reliable record after receiving
datagrams under Section 6.3. It MAY return the report on any reverse pipe
that can carry the frame (MULTIPATH.md Section 11 rule S1). Without a
qualifying return pipe it MAY wait within its reporting-state bounds or
discard the report. The frame is available only between endpoints of the
same zmux family (SPEC.md Section 5.5), from the endpoint that advertised
a positive `max_datagram_frame_size` for the reported direction. SPEC.md Section 4.1 defines
the handling of frames outside their availability conditions.

```text
DATAGRAM_ACK Frame {
  Type (i) = 0x28bbd99f,
  Pipe ID (i),
  Channel (8),
  Largest Sequence (i),
  Ack Delay (i),
  Range Count (i),
  First Range (i),
  Ack Range (..) ...,
}
```

- **Pipe ID** identifies the inbound zmux pipe on which the reported
  datagrams arrived (MULTIPATH.md Section 4). It can differ from the
  pipe carrying the report.
- **Channel** is 0 for a record on any lane of that pipe and 1 for its
  carrier datagram channel. Other values are invalid.
- **Largest Sequence**, **Range Count**, **First Range** and **Ack Range**
  encode inclusive descending ranges as in RFC 9000 Sections 19.3 and
  19.3.1, with Sequence in place of packet number. Range Count is the
  number of additional Gap and Ack Range Length pairs after First Range.
  First Range is subtracted from Largest Sequence to obtain the smallest
  value in the first range. Each next range ends at the preceding
  range's smallest value minus Gap minus 2. Its start is that end minus
  Ack Range Length.
- **Ack Delay** is the elapsed time from receipt of the reported Largest
  Sequence on that pipe and channel to dispatch of this report, in
  microseconds without an exponent.

A receiver MUST reject an invalid Channel, truncated field or range
arithmetic below zero, a Range Count above 4090, or an encoded frame
above 8192 bytes with FRAME_ENCODING_ERROR. Count and byte bounds apply
before acting on a range. A receiver MUST match ranges against bounded
dispatch state without enumerating sequence values in a range.

The reporting endpoint MUST report only arrivals on the named pipe and
channel that pass the outer frame, Sequence and any applicable Flow checks.
Reporting MUST be independent of whether the copy was admitted, including
for duplicates and sequences outside the window. An endpoint MUST NOT send a report for a tuple with
no new arrival since its last report. Reports for a tuple MUST be spaced
by at least max(25 ms, srtt / 4), using the locally measured smoothed RTT
of the reporting pipe pair if available and 25 ms otherwise. In the half-open
window (now - 1 second, now] ending at a report, the endpoint MUST send at most
twice as many DATAGRAM_ACK frames as it has live inbound pipes at that
report's dispatch.

Both endpoints MUST bound their reporting state in size and age. The sender
of a datagram MUST match reports only against copies actually dispatched on
the reported pipe and channel and still tracked. It MUST ignore unmatched
entries and MUST NOT account for a tracked copy twice. A report signals
arrival at the endpoint. It MUST NOT be used to infer application delivery
or advance the acknowledgement of any reliable pipe position. An absent
report is insufficient evidence of forward loss. The endpoint MUST NOT use
DATAGRAM_ACK for congestion control or trigger retransmission from it.

DATAGRAM_ACK is non-ack-eliciting and neutral under SPEC.md Section 9.4.
The reporting endpoint MUST NOT retransmit or reinject it.

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
| ZMUX_PACKET_ERROR | 0x5a4d0004 | the bytes of a packet stream were malformed: a capsule above the receiver's bound, or the stream ended inside a capsule (Section 12) |

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
- It is used after GOAWAY (Section 3.4), by an endpoint under local
  resource pressure for new peer streams, which are rejected without being
  offered to the application, and when an application declines a stream
  before reading any of its data.
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

When a zmux library ends a stream or a session because of a local API
action that carries no explicit code, it uses the codes below:

- ZMUX_CANCELLED with RESET_STREAM (or RESET_STREAM_AT covering the
  preamble) when the application drops or closes a sending part without an
  explicit finish, including garbage collection of an unfinished handle. Only
  an explicit finish (FIN) is a clean end; a reset is never complete
  (SPEC.md Section 7.5);
- ZMUX_CANCELLED in CONNECTION_CLOSE when the application leaves a session's
  scope through an exception where the language reports it, so that the peer can tell it from a normal close;
- STOP_SENDING with code 0 when the application drops or closes a receiving
  part before reading its end;
- ZMUX_REJECTED as Section 7.2 describes;
- ZMUX_CARRIER_FAILED when a stream used as a carrier is aborted (Section
  10.2) and when a relay loses the other side of a spliced stream (Section
  10.3).

---

## 8. Transport error codes

zmux transport error codes are part of the core protocol and are defined in
SPEC.md Section 10.2: ZMUX_EXCESSIVE_LOAD, ZMUX_ROLE_CONFLICT,
ZMUX_RESUME_TIMEOUT, ZMUX_RESUME_FAILED, ZMUX_PIPE_FAILED,
ZMUX_PIPE_RESOURCE_LIMIT, ZMUX_IDLE_TIMEOUT and the local-only
ZMUX_PIPE_AUTH_FAILED.

---

## 9. Reserved extension points

The codepoints and frameworks below are fixed now and implemented later
(REGISTRY.md Section 7). Endpoints of this specification MUST NOT use them.

| Extension point | Reserved | Notes |
|---|---|---|
| DEADLINE preamble TLV | TLV type 0x03 | relative deadline in milliseconds (variable-length integer), relative because clocks differ |
| carrier implicit acknowledgement | behaviour only | adapters such as HTTP POST, whose 2xx response proves full delivery, may later report it as an acknowledgement; endpoints MUST NOT treat carrier events as acknowledgements yet |
| qlog event schema | none | QUIC qlog events plus pipe events |

---

## 10. Nested sessions and relays

### 10.1 Model

A stream of one zmux session (the **outer** session) can be the carrier of
a pipe pair of another zmux session (the **nested** session). Two
endpoints then keep one end-to-end nested session whose pipes run over
different routes: for example a direct carrier, and streams that cross one
or more **relays**, each relay being an endpoint of two outer sessions that
splices a stream of one to a stream of the other. 

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
  application TLVs (for example routing metadata, Section 4.6). The pipe's
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

- **Lanes.** The adapter reports the outer session as the carrier
  connection and can open further streams of it, each with PIPE_CARRIER, as
  lanes of the pipe (MULTIPATH.md Sections 2.5 and 5.8; the receiving
  application hands them to the nested session like any carrier stream). A
  lane carries the nested
  peer's Lane Key, so it reaches its pipe also through a relay, as long as
  the relay forwards it over the same outer session as the pipe's lane 0
  (otherwise it names no pipe on the connection it arrives on and is
  refused, which costs the pipe nothing); other clients of the relay, which
  do not know the key, cannot attach a stream to the pipe.
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
  that routing state can form with a hop limit (Section 4.6).
- A nested session with a plain QMux implementation at one end is a
  single-pipe session without acknowledgements.

### 10.3 Relays

Relays SHOULD splice the stream's data verbatim and
SHOULD NOT parse the nested session's records: acknowledgement, failure
detection, reinjection and resume stay end to end, and the relay's failures
reach the nested session as pipe failures.

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

Two endpoints that dial each other at about the same time can end up with
two sessions. The rule below picks the same survivor at both ends.

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
the survivor. When the survivor uses
multipath, the endpoint that dialed the losing session MAY instead dial
the same route again and join the new carrier to the survivor (MULTIPATH.md
Section 5), so that the route is kept at the cost of one carrier handshake,
made after the decision and off the path of any stream.


## 12. Packet streams

A packet stream is a bidirectional stream whose two endpoints agree, by
convention of the application (for example in the stream's preamble), to
carry packets: messages that each go either as a datagram associated with
the stream (Section 6.3) or in the stream's bytes. No parameter or frame
announces it.

The bytes of a packet stream in each direction are a sequence of capsules
in the format of RFC 9297 Section 3.2:

```text
Capsule {
  Type (i),
  Length (i),
  Value (..),
}
```

- Type 0x00 is DATAGRAM (RFC 9297 Section 3.5). Its Value is exactly the
  data a datagram associated with the stream would carry after the Flow
  field: `Context ID (i)` followed by the payload. A packet sent either way
  is therefore the same Context ID and payload, and a relay may move a
  packet between the two forms without changing it.
- A receiver skips capsules of types it does not know.
- A receiver bounds the Length it accepts; a capsule above its bound, or a
  stream that ends inside a capsule, is an error of the packet stream: the
  receiver resets its sending part and sends STOP_SENDING, both with
  ZMUX_PACKET_ERROR (Section 7.1).
- Packets are not ordered relative to each other: those in capsules arrive
  in order and reliably, those sent as datagrams may be lost or arrive
  earlier or later. A packet is never fragmented.
- A packet stream ends like any stream; datagrams associated with it that
  arrive after its end are dropped (Section 6.4).
