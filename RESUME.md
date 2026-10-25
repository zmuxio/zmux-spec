# zmux Resume

Resume is part of the zmux core protocol (SPEC.md Section 12). It builds on
MULTIPATH.md: it is available only in multipath sessions and reuses
PIPE_JOIN, PIPE_ACK, PIPE_ABANDON and the reinjection rules.

---

## 1. Overview

When every pipe in one direction of a session is lost, a session without
resume closes (MULTIPATH.md Section 8.5). With resume in use, the endpoint
instead **suspends** the session for a grace period. Streams stay open,
writes are buffered up to the flow-control and buffer limits, and the session
continues as soon as new pipes join. Rejoining uses the ordinary PIPE_JOIN
handshake; after it, both endpoints report their cumulative positions in
ordinary PIPE_ACK snapshots and re-send everything the peer has not
received.

Resume is not a second mechanism: a resume is a multipath session whose pipe
count dropped to zero in a direction and then rose again. The extra pieces are
the SUSPENDED state, the grace timer and the fail-closed rules.

---

## 2. Model

- Suspension is a local state of each endpoint. One endpoint can be suspended
  while the other still has pipes, for example when only the uplinks died.
  The other endpoint keeps sending until its own failure detection
  (MULTIPATH.md Section 7.1) fails its outbound pipes, and then suspends too.
- Retained information is exactly the multipath retention of MULTIPATH.md
  Section 11 rule S4: everything not yet acknowledged.
- Positions are zmux-level counts of processed records (MULTIPATH.md Section
  6.1). After all pipes are lost, a receiver's final positions say exactly
  which information arrived; everything beyond them is re-sent.

---

## 3. Negotiation

`zmux_resume`, ID 0x0d5778ce. Value:

```text
zmux_resume Value {
  Resume Grace (i),
}
```

Resume Grace is in milliseconds, at least 1; the value 0 is a
TRANSPORT_PARAMETER_ERROR. There is no upper limit: the memory that suspended
sessions hold is bounded by the listener's limits (Section 9), not by the
grace period, and a deployment may keep sessions resumable for days. The parameter MUST NOT be sent without
`zmux_multipath`.

Resume is in use when multipath is in use, both endpoints sent `zmux_resume`,
and at least one endpoint's Session Locator is non-empty (otherwise neither
could rejoin). The grace period of the session is the smaller of the two
Resume Grace values.

zmux implementations send `zmux_resume` when the application sets a resume
timeout. A server that accepts resume SHOULD also accept
joins (non-empty locator).

---

## 4. The SUSPENDED state

### 4.1 Entry

An endpoint enters SUSPENDED from ACTIVE or DRAINING when the condition of
MULTIPATH.md Section 8.5 holds (no live outbound pipe, or no live inbound
pipe) and resume is in use. It never enters SUSPENDED from CLOSING. When it
leaves SUSPENDED it returns to DRAINING if it was DRAINING before or if a
GOAWAY was sent or received while it was suspended, and to ACTIVE otherwise.
A protocol violation detected while suspended (for example on an inbound pipe
of a partially suspended endpoint) closes the session as in any other state
(SPEC.md Section 6.5).

On entry the endpoint:

1. stops the idle timer and any keep-alive (SPEC.md Section 6.4);
2. starts the grace timer, which expires at entry time plus the grace period;
3. reports a suspension event to the application;
4. keeps all stream, flow-control and retained state.

The grace timer is started once per suspension. Joins that are attempted and
fail while the endpoint is SUSPENDED do not restart it; only leaving
SUSPENDED (Section 4.4) stops it. A client that keeps retrying therefore
cannot keep a dead session alive beyond the grace period.

### 4.2 Behaviour while suspended

- Application writes are accepted into send buffers until stream or session
  flow control, the bound on retained data (MULTIPATH.md Section 11 rule
  S4) or the local
  send buffer limit is reached; then they block. Writes never fail because the
  session is suspended.
- Reads wait for data. Application deadlines and cancellations apply as usual.
- Opening streams is allowed within stream credit; the frames wait for a pipe.
- Datagrams submitted while no outbound pipe exists wait in the send queue of
  EXTENSIONS.md Section 6.5 like any others: the oldest is dropped and
  counted when the queue is full, and a datagram older than the
  application's maximum age is dropped.
- Information that was reinjected from failed pipes waits for a new pipe.
- Frames that cannot be sent for lack of an outbound pipe (PIPE_ABANDON for
  the failed attachments, limit updates, PIPE_ACK, GOAWAY) are kept as
  coalesced state (SPEC.md Section 9.2) and sent on the next live outbound
  pipe (JOINING or ACTIVE, MULTIPATH.md Section 11 rule S1).

An endpoint may be suspended in one direction only (Section 2):

- **No inbound pipe, live outbound pipes.** The endpoint keeps using its
  outbound pipes as in ACTIVE: it sends stream data, credit and other frames
  within flow control and the bound on retained data, so that a peer that can
  still read receives them. Because no acknowledgement can reach it, it marks no outbound pipe suspect or failed for lack of acknowledgement
  progress (carrier errors and write stalls still count, MULTIPATH.md
  Section 7.1). It keeps sending PIPE_ACK snapshots on them
  (MULTIPATH.md Section 6.3), so the peer learns its final positions.
- **No outbound pipe, live inbound pipes.** The endpoint keeps reading and
  processing its inbound pipes and delivering data to the application; what
  it would send waits as above.

### 4.3 Rejoining

Either endpoint may join new pipes while suspended, using MULTIPATH.md
Section 5 unchanged; in practice the endpoint that can reach the other's
listener (usually the initiator) does it, possibly driven by an
application-supplied redial hook. A rejoining endpoint
joins a direction it lacks: an endpoint without a live inbound pipe joins a
down or bidi attachment, and one without a live outbound pipe joins an up or
bidi attachment. The peer may lack a direction too, for example an acceptor
whose only outbound pipe was the downlink that failed: an up attachment
alone cannot be acknowledged then. A joiner whose rejoined up attachment has
not become ACTIVE within the time after which it would mark that pipe
suspect (MULTIPATH.md Section 7.1) SHOULD therefore also join a down or
bidi attachment. A joiner that knows its carriers are split, so that a lost
direction is always paired with a lost peer direction (uplinks and downlinks
through a CDN, an HTTP POST body and a GET response), SHOULD instead join an
up and a down attachment in parallel from the start, which saves that wait.
An inbound pipe that stays open but silent fails (MULTIPATH.md Section 7.3),
after which the endpoint lacks that direction and rejoins it.

Rejoining needs no grant from the peer: the joiner takes fresh pipe IDs
(MULTIPATH.md Section 4.1), and connection attempts cost the acceptor
nothing until a join reaches it. The failed attachments cannot be retired
while suspended, because the PIPE_ABANDON exchanges that retirement needs
cannot complete. The cap on unretired attachments (MULTIPATH.md Section 5.4
step 9) therefore works differently while suspended: an acceptor at the cap
retires the oldest failed attachment locally when a new join arrives instead
of refusing the join, and the joiner does the same with its own failed
attachments before it joins. When none has failed, every attachment at the
cap is live but serves only the direction the endpoint still has (for
example uplinks whose downlinks were lost with their carriers); for a join
that provides the missing direction the endpoint then displaces the live
attachment with the oldest last progress, failing it locally without
PIPE_ABANDON, so that a full set of one-way attachments never holds the
session suspended until the grace period ends. The headroom H of that cap is the room for
failed attachments that rejoining needs. A session therefore survives any
number of rejoins that are cut off after they connect, and a peer that keeps
joining and dropping attachments gains nothing: at most max_pipes + H of its
attachments are attached and unretired at a time.

### 4.4 Leaving SUSPENDED

An endpoint leaves SUSPENDED when it has at least one outbound pipe in the
ACTIVE state and at least one OPEN inbound pipe (STATE_MACHINE.md Section 3).
A JOINING pipe does not count: the condition holds only once the peer has
proven the new pipes with an acknowledgement or a record (MULTIPATH.md
Section 5.7). It then:

1. stops the grace timer and restarts the idle timer;
2. sends the queued frames, including PIPE_ABANDON for its failed
   attachments (MULTIPATH.md Section 8.3), and resumes normal scheduling,
   reinjected information first (Section 6);
3. reports a resumption event to the application.

The positions of Section 5 arrive earlier, with the first records of the new
pipes, whether or not the endpoint leaves SUSPENDED at that moment.

## 5. Positions after a rejoin

No frame is specific to resume: positions travel in PIPE_ACK (MULTIPATH.md
Section 6.2), whose complete snapshot lists every inbound pipe that is not
retired and whose position is greater than 0, failed pipes included, with
one entry per lane for a pipe that had lanes (MULTIPATH.md Section 2.5).
Lanes belong to their pipe: a failed pipe's lanes are never reopened, a
rejoined pipe is a new attachment whose lanes start again from its lane 0,
and what a failed pipe's lanes had not had acknowledged is reinjected from
each lane's own acknowledged position (Section 6). Three
rules of MULTIPATH.md make them arrive without a round trip of their own:

- the first record a joiner writes on a new outbound pipe carries a complete
  snapshot (MULTIPATH.md Section 5.2);
- so does the first record an acceptor writes on a new outbound pipe
  (MULTIPATH.md Section 5.4 step 8);
- the PIPE_ACK that answers a PIPE_PING received in the first record of a
  newly attached inbound pipe is a complete snapshot (MULTIPATH.md Section
  6.3 rule 5), which covers a joiner that rejoins an up attachment only: the
  acceptor's answer travels on any outbound pipe it has.

An endpoint that has no inbound pipe but still has outbound pipes keeps
sending snapshots on them (Section 4.2), and reports the final position of
every inbound pipe that ended (MULTIPATH.md Section 6.3 rule 4). Positions
are cumulative, so an outdated or replayed snapshot is harmless; and because
information is released only once a PIPE_ACK covers it and is otherwise
reinjected, exactly-once delivery never depends on a snapshot being
complete, only the amount of duplicate reinjection does.

---

## 6. Retransmission from the acknowledged positions

Every failed pipe's information beyond its acknowledged position (that of
the lane that carried it, for a pipe with lanes) was queued
for reinjection when the pipe failed (MULTIPATH.md Section 7.4). After a
PIPE_ACK has been processed, information that it acknowledges is removed
from that queue; everything else is sent on the new pipes, before new data.

An endpoint MAY start reinjecting as soon as it has a new outbound pipe:
while the pipe is JOINING and no other pipe is usable it may carry stream
data like an ACTIVE pipe (MULTIPATH.md Section 11 rule S1), and duplicates
are harmless.

---

## 7. Fail-closed conditions

In a session with resume in use the endpoint MUST close the session with
ZMUX_RESUME_FAILED, and MUST NOT attempt to continue, when:

1. a PIPE_ACK entry acknowledges more bytes on a pipe than were sent on it,
   including a non-zero position on a pipe on which the endpoint never had
   an outbound direction: the two states diverged (without resume this is
   the PROTOCOL_VIOLATION of MULTIPATH.md Section 6.2);
2. information that must be re-sent from an acknowledged position is no
   longer available (the gap exceeds the retained data). With the retention
   rule of MULTIPATH.md Section 11 rule S4 this happens only after state
   loss.

If no pipe is available to carry CONNECTION_CLOSE, the endpoint closes
locally. In every case the application sees ZMUX_RESUME_FAILED and knows that
the data in flight is in an unknown state.

---

## 8. Grace expiry

When the grace timer expires, the endpoint closes the session with
ZMUX_RESUME_TIMEOUT. It sends CONNECTION_CLOSE(ZMUX_RESUME_TIMEOUT) on any
live outbound pipe it still has (a partially suspended endpoint), otherwise
it closes locally. It releases all state and removes the session locator from
its listener, so later joins fail at locator lookup (MULTIPATH.md Section
5.4 step 3) and the peer learns that it must start a new session.

A server that lost its state (for example after a restart) cannot resume
any session; joins for its old locators fail in the same way.

---

## 9. Server limits

A listener MUST bound the resources held by suspended sessions: their
number, the total bytes they hold (retained data, unread received data,
reassembly buffers and queued datagrams together), and the pending carriers
per session and per listener (MULTIPATH.md Section 5.6). When a limit would
be exceeded, the listener closes suspended sessions, the longest-suspended
first, with ZMUX_RESUME_TIMEOUT.

---

## 10. Token rotation

- Every rejoin uses a new pipe ID and therefore a new one-time join token
  (MULTIPATH.md Section 10.6); tokens observed before or during suspension
  cannot be replayed.
- Pipe IDs that were attached before the suspension, or whose bit is set in
  the replay window (MULTIPATH.md Section 4.3), are never accepted again;
  that includes every join the joiner abandoned.
- When the new carrier is carried by a TLS 1.3 handshake that terminates
  at the peer (a TLS connection, or a QUIC connection whose stream it is),
  the carrier-bound flag ties the token to that connection (MULTIPATH.md
  Section 10.5).
- The session secret and the locator stay the same for the life of the
  session; zmux does not ratchet keys at resumption, because both endpoints
  would have to agree on the ratchet step across a loss of all pipes, which
  adds a failure mode without protecting against anything a one-time token
  does not already prevent.

---

## 11. Interactions

- **Closing while suspended.** If the application closes a suspended session,
  the endpoint sends CONNECTION_CLOSE on any live outbound pipe it still has
  (SPEC.md Section 6.5); without one it closes locally and discards the
  session, and the peer learns of it when its joins fail or its grace period
  expires.
- **GOAWAY while suspended.** GOAWAY is queued and sent after resumption;
  its watermarks still describe what the sender processed.
- **Idle timeout.** Not running while suspended; the grace timer replaces it.
- **Streams opened while suspended** are sent after resumption like any other
  queued frame.

---

## 12. Security considerations

- Rejoining needs the session secret (MULTIPATH.md Section 10); an attacker
  who knows the locator but not the secret cannot resume a session.
- Positions travel in PIPE_ACK like every acknowledgement. Only an
  authenticated join (MULTIPATH.md Section 10) adds a pipe to the session,
  so an attacker without the session secret, for example someone who only
  knows a locator, cannot inject or replay positions. An on-path attacker of
  an unprotected carrier can forge acknowledgements whether or not they
  follow a suspension, and so make the sender discard data that never
  arrived; against such attackers the carriers must be protected (SPEC.md
  Section 14.2).
- A secret mismatch between the endpoints shows as join authentication
  failures at the acceptor (MULTIPATH.md Section 5.4 step 5); the joiner
  learns of it only when the grace period expires.
- Suspended sessions cost memory; Section 9 bounds it. Deployments SHOULD
  also count suspended sessions in per-client quotas.
- The grace period lets a session outlive its carriers. Applications that
  need prompt failure reporting should keep the grace period short.
