# zmux Resume

Status: normative, part of the zmux core protocol (SPEC.md Section 12).
Resume builds on MULTIPATH.md: it is available only in multipath sessions and
reuses Join, PIPE_ACK, PIPE_ABANDON and the reinjection rules.

---

## 1. Overview

When every pipe in one direction of a session is lost, a session without
resume closes (MULTIPATH.md Section 8.5). With resume in use, the endpoint
instead **suspends** the session for a grace period. Streams stay open,
writes are buffered up to the flow-control and buffer limits, and the session
continues as soon as new pipes join. Rejoining uses the ordinary Join
handshake; after it, both endpoints report their cumulative positions in
ordinary PIPE_ACK snapshots and re-send everything the peer has not
received.

---

## 2. Model

- Suspension is a local state of each endpoint. One endpoint can be suspended
  while the other still has pipes, for example when only the uplinks died.
  The other endpoint keeps sending until its own failure detection
  (MULTIPATH.md Section 7.1) fails its outbound pipes, and then suspends too.
- Retained information is everything not yet acknowledged (MULTIPATH.md
  Section 7.4).
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

A server that accepts resume SHOULD also accept joins (non-empty locator).

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
SUSPENDED (Section 4.4) stops it.

### 4.2 Behaviour while suspended

- Stream state and credit are kept; frames, including those of streams
  opened within stream credit, wait for a pipe.
- Datagrams follow the queue rules of EXTENSIONS.md Section 6.5.
- Information that was reinjected from failed pipes waits for a new pipe.
- Frames that cannot be sent for lack of an outbound pipe (PIPE_ABANDON for
  the failed attachments, limit updates, PIPE_ACK, GOAWAY) are kept as
  coalesced state (SPEC.md Section 9.2) and sent on the next live outbound
  pipe once it is ACTIVE (MULTIPATH.md Section 11 rule S1).

An endpoint may be suspended in one direction only (Section 2):

- **No inbound pipe, live outbound pipes.** The endpoint keeps using its
  outbound pipes as in ACTIVE: it sends stream data, credit and other frames
  within flow control and the retransmit budget, so that a peer that can
  still read receives them. Because no acknowledgement can reach it, it does
  not fail outbound pipes for lack of acknowledgement progress
  (MULTIPATH.md Section 7.1). It keeps sending PIPE_ACK snapshots on them
  (MULTIPATH.md Section 6.3), so the peer learns its final positions.
- **No outbound pipe, live inbound pipes.** The endpoint keeps reading and
  processing its inbound pipes and delivering data to the application; what
  it would send waits as above.

### 4.3 Rejoining

Either endpoint may join new pipes while suspended, using MULTIPATH.md
Section 5 unchanged; a session that is join pending (MULTIPATH.md Section 3.3) has
no join keys, so one that suspends before that ends cannot rejoin and ends at the
grace expiry (Section 8).

A rejoining endpoint SHOULD join one bidi attachment, on a carrier pair where its
carriers are split (MULTIPATH.md Section 5.3): one handshake then restores both
directions of both endpoints at once. An endpoint joins separate directions only
when the deployment needs them apart: without a live inbound pipe it joins a down or
bidi attachment, and without a live outbound pipe an up or bidi attachment. The peer
may lack a direction too, for example an acceptor whose only outbound pipe was the
downlink that failed: an up attachment alone cannot be acknowledged then. A joiner
whose rejoined up attachment has seen no acknowledgement within that pipe's T_suspect
(MULTIPATH.md Section 7.1) SHOULD therefore also
join a down or bidi attachment. An inbound pipe that stays open but silent is failed by
the inbound silence rule of MULTIPATH.md Section 7.3, after which the endpoint lacks that
direction and rejoins it.

Rejoining needs no grant from the peer: the joiner takes fresh pipe IDs
(MULTIPATH.md Section 4.1), and connection attempts cost the acceptor
nothing until a join reaches it. The failed attachments cannot be retired
while suspended, because the PIPE_ABANDON exchanges that retirement needs
cannot complete. The cap on unretired attachments (MULTIPATH.md Section 5.4
step 9) therefore works differently while suspended: an acceptor at the cap
retires the oldest failed attachment locally when a new join is Confirmed instead
of refusing the join, and the joiner does the same with its own failed
attachments before it joins. When none has failed, every attachment at the
cap is live but serves only the direction the endpoint still has (for
example uplinks whose downlinks were lost with their carriers); for a join
that provides the missing direction the endpoint then displaces the live
attachment with the oldest last progress, failing it locally without
PIPE_ABANDON, so that a full set of one-way attachments never holds the
session suspended until the grace period ends. The headroom H of that cap is the room for
failed attachments that rejoining needs.

### 4.4 Leaving SUSPENDED

An endpoint leaves SUSPENDED when it has at least one outbound pipe in the
ACTIVE state and at least one OPEN inbound pipe (STATE_MACHINE.md Section 3).
A JOINING pipe does not count, and an acceptor's Unconfirmed carrier has no pipe at
all, so a replayed Hello cannot end a suspension. A joiner's pipe counts once the Reply
has proven the peer; an acceptor's once the join is Confirmed (MULTIPATH.md Sections
5.4 step 8 and 5.7 step 4). It then:

1. stops the grace timer and restarts the idle timer;
2. sends the queued frames, including PIPE_ABANDON for its failed
   attachments (MULTIPATH.md Section 8.3), and resumes normal scheduling,
   reinjected information first (Section 6);
3. reports a resumption event to the application.

An epoch exchange that was in progress continues: its current frame is sent on the new
pipes (MULTIPATH.md Section 10.7).

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
every inbound pipe that ended (MULTIPATH.md Section 6.3 rule 4).

---

## 6. Retransmission from the acknowledged positions

Every failed pipe's information beyond its acknowledged position (that of
the lane that carried it, for a pipe with lanes) was queued
for reinjection when the pipe failed (MULTIPATH.md Section 7.4). After a
PIPE_ACK has been processed, information that it acknowledges is removed
from that queue; everything else is sent on the new pipes, before new data.

An endpoint MAY start reinjecting as soon as it has a new outbound pipe:
while the pipe is JOINING and no other pipe is usable it may put stream
data in the Hello's first batch (MULTIPATH.md Section 11 rule S1), and duplicates
are harmless. How long it waits for the peer's positions before reinjecting
more is a local choice.

---

## 7. Fail-closed conditions

In a session with resume in use the endpoint MUST close the session with
ZMUX_RESUME_FAILED, and MUST NOT attempt to continue, when:

1. a PIPE_ACK entry acknowledges more bytes on a pipe than were sent on it,
   including a non-zero position on a pipe on which the endpoint never had
   an outbound direction: the two states diverged (without resume this is
   the PROTOCOL_VIOLATION of MULTIPATH.md Section 6.2);
2. information that must be re-sent from an acknowledged position is no
   longer available (the gap exceeds the retained data). Because information
   is retained until it is acknowledged, this happens only after state loss.

If no pipe is available to carry CONNECTION_CLOSE, the endpoint closes
locally. In every case the application sees ZMUX_RESUME_FAILED and knows that
the data in flight is in an unknown state.

---

## 8. Grace expiry

When the grace timer expires, the endpoint closes the session with
ZMUX_RESUME_TIMEOUT. It sends CONNECTION_CLOSE(ZMUX_RESUME_TIMEOUT) on any
live outbound pipe it still has (a partially suspended endpoint), otherwise
it closes locally. It releases all state and removes every Session Locator of the
session that it accepts (two during an epoch exchange) from its listener, so later
joins fail at locator lookup (MULTIPATH.md Section
5.4 step 3) and the peer learns that it must start a new session.

A server that lost its state (for example after a restart) cannot resume
any session; joins for its old locators fail in the same way. A joiner learns
this from its grace period ending, or earlier from its host, which MAY start a new
session in parallel and close the old one. Nothing on an unproven carrier can end a
session.

---

## 9. Server limits

A listener MUST bound the resources held by suspended sessions:

- the number of suspended sessions;
- the total bytes that suspended sessions hold, retained (unacknowledged)
  data, unread received data, reassembly buffers and queued datagrams
  together;
- the pending carriers per session and per listener (MULTIPATH.md Section
  5.6).

When a limit would be exceeded, the listener closes suspended
sessions, the longest-suspended first, with ZMUX_RESUME_TIMEOUT.

---

## 10. Rejoin keys

- Every rejoin uses a new pipe ID and new random values (MULTIPATH.md Section
  10.6); a Hello observed before or during suspension cannot be replayed to any
  effect.
- Pipe IDs that were attached before the suspension, or whose bit is set in
  the replay window (MULTIPATH.md Section 4.3), are never accepted again;
  that includes every join the joiner abandoned.
- The session secret evolves by epochs and the Session Locators change with them
  (MULTIPATH.md Section 10.7); a resumption itself starts no epoch. An epoch exchange
  in progress at the suspension goes on over the new pipes. Its state is part of the
  session's state: an endpoint that has lost it has lost the session.

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
  a Trusted or Unmanaged pipe can forge acknowledgements whether or not they
  follow a suspension, and so make the sender discard data that never
  arrived; against such attackers the carriers must be protected
  (MULTIPATH.md Section 10.5).
- A secret mismatch between the endpoints shows as join authentication
  failures at the acceptor (MULTIPATH.md Section 5.4 step 5); the joiner
  learns of it only when the grace period expires.
- Suspended sessions cost memory; Section 9 bounds it.
