# zmux State Machines

This document collects the rules of SPEC.md, MULTIPATH.md, RESUME.md and
EXTENSIONS.md as state machines. Where a table here and a referenced section
disagree, the referenced section wins.

---

## 1. Notation

States are written in capitals. "On X" is an input event (a frame, an API
action, a timer, a carrier event). "Do Y" is an action. Stream states use the
names of RFC 9000 Section 3 unchanged.

---

## 2. Streams

### 2.1 Base machines

The sending-part and receiving-part machines of RFC 9000 Sections 3.1 and 3.2
apply, with the bidirectional composition of RFC 9000 Section 3.4 and the
solicited transitions of RFC 9000 Section 3.5:

```text
sending part:   Ready -> Send -> Data Sent -> Data Recvd
                  |       |         |
                  +-------+---------+--> Reset Sent -> Reset Recvd

receiving part: Recv -> Size Known -> Data Recvd -> Data Read
                  |         |             |
                  +---------+-------------+--> Reset Recvd -> Reset Read
```

### 2.2 zmux deltas

| # | Delta | Defined in |
|---|---|---|
| D1 | "Acknowledged" means "passed to the carrier" outside multipath, and "covered by PIPE_ACK" in multipath sessions. Outside multipath, Data Sent -> Data Recvd and Reset Sent -> Reset Recvd therefore happen as soon as the FIN or reset has been written. | SPEC 7.4 |
| D2 | RESET_STREAM_AT moves the sending part to Data Sent (not Reset Sent) and the receiving part to Size Known, as reliable-stream-reset-11 Section 5.3 defines; Data Recvd is reached once all data below the smallest Reliable Size is acknowledged / received. | SPEC 7.5 |
| D3 | When the stream preamble is in use, a peer-initiated stream has a pre-offer phase: it exists and counts against credit, but is not offered to the application until its preamble is complete. | EXTENSIONS 4.4 |
| D4 | After both parts are terminal, the stream waits for the application's release before its state is discarded and its credit returns. | SPEC 7.6, 8.5 |
| D5 | Once a sending part has received STOP_SENDING, it sends no new data and no FIN; it answers with RESET_STREAM or RESET_STREAM_AT. | SPEC 4.3 |
| D6 | PRIORITY_UPDATE is permitted in every state of a part the receiver sends on and opens peer-initiated bidirectional streams. | EXTENSIONS 5.4 |
| D7 | A peer stream at or above a GOAWAY watermark the endpoint sent, or a new peer stream arriving while the listener is under memory pressure, is rejected by the stack and never offered; a stream the application rejects was offered, but none of its data was read. Either way the endpoint sends RESET_STREAM(ZMUX_REJECTED) on its sending part (bidirectional streams), which moves that part to Reset Sent, and STOP_SENDING(ZMUX_REJECTED) on its receiving part, which then waits for the peer's reset or FIN. | EXTENSIONS 3.4, 7.2 |
| D8 | In multipath sessions, every frame may be duplicated or reordered. A frame that does not advance a state is ignored. | MULTIPATH 7.6 |

### 2.3 Life cycle of a stream record

```text
   (no state)
       |  first frame sent (local) / first opening frame received or implied (peer)
       v
   OPEN ----------------------------------------------+
       |  preamble complete (peer streams, preamble in use)
       v                                              |
   OFFERED (peer streams) / OWNED (local streams)     |  rejected (GOAWAY, application)
       |  both parts terminal                         v
       v                                          REJECTED (parts terminal, never offered)
   TERMINAL                                           |
       |  application released the stream             |  session closing or immediately
       v                                              v
   DISCARDED: state freed; MAX_STREAMS may grow by one for peer streams
```

- Local streams have no OFFERED step: the application owns them from the
  first API call. Their identifier is allocated at the first emitted frame
  (SPEC 7.2).
- A peer stream that becomes TERMINAL before it is offered is still offered
  (with its preamble, data and final status) and must be released by the
  application before it is discarded (SPEC 8.5).
- A stream rejected by the stack (REJECTED above) was never offered, so no
  application release is needed; it is discarded as soon as both parts are
  terminal (SPEC 7.6). A stream the application rejects is an ordinary
  OFFERED stream and is discarded when it is released.

### 2.4 Frames by state

RFC 9000 Section 3.3 lists the frames permitted in each state. In addition:

| Frame | Sending part states | Receiving part states |
|---|---|---|
| RESET_STREAM_AT | sent from Send or Data Sent | received in any state; transitions as D2 |
| PRIORITY_UPDATE | received in any state (applies to the sending part) | sent in any state (requests a priority for the peer's sending part) |
| DATAGRAM with a flow prefix | n/a | associated with a stream that is not discarded; otherwise dropped (EXTENSIONS 6.4) |

Frames for a DISCARDED stream are ignored (SPEC 7.6).

### 2.5 Application-visible outcomes

These outcomes are normative for traces (TRACES.md) and for the errors an
implementation reports to its application. "Remote" means the peer caused it.

| Situation | Read returns | Write returns |
|---|---|---|
| peer sent FIN, all data read | end of stream | (unaffected) |
| peer RESET_STREAM(c) / RESET_STREAM_AT(c) after reliable data read | stream reset, code c, remote, even if all data and a FIN arrived, unless the end of stream was already read (SPEC 7.5) | (unaffected) |
| peer STOP_SENDING(c) | (unaffected) | stream stopped, code c, remote; for every write, including one already in progress |
| local stop(c) | closed by local stop, code c | (unaffected) |
| local reset(c) | (unaffected) | closed by local reset, code c |
| ZMUX_REJECTED from peer | stream rejected (retryable) | stream rejected (retryable) |
| stream index at or above a received GOAWAY watermark | stream rejected (retryable) | stream rejected (retryable) |
| stream opened locally, neither emitted nor pinned, when a GOAWAY arrives, and every open call after it (EXTENSIONS 3.5 rule 2) | going away (retryable) | going away (retryable) |
| session closed by peer CONNECTION_CLOSE | session error with the peer's code | same |
| session closed locally or by timeout | session error with the local reason | same |

A stream-level outcome never closes the session (SPEC 9.3). When several
apply, the first one that happened wins, except that a session error replaces
an in-progress blocking wait of any kind: a blocked writer or reader is woken
with the session error as soon as the session ends.

---

## 3. Pipes

### 3.1 Outbound pipe (the local endpoint writes)

```text
                    join sent or handed off
   (none) ---------------------------------> JOINING ---------------------------+
                                               |   \  stall                      | carrier error,
              first PIPE_ACK > 0 (up) or       |    +----------------+           | join not confirmed
              first record received (down)     v                     v           v within the handshake timeout
   session established (pipe 0) ---------> ACTIVE <------------> SUSPECT ----------> FAILED
   acceptor verified a down/bidi join -------^   stall /              |  lasting stall,  ^  ^
                                                 ack advances and     |  error, abrupt   |  |
                                                 writes resume        |  removal         |  |
                                   graceful     |                     |                  |  |
                                   removal      v    graceful removal |                  |  |
                                             DRAINING <---------------+------------------+  |
                                                |  \ drain not complete in time,           |
                                                |   +- carrier error, abrupt removal -------+
                                                |  PIPE_ABANDON exchange complete and
                                                |  all stream data sent on it acknowledged
                                                v
                                             CLOSED (write side shut down)

   FAILED or CLOSED, after own PIPE_ABANDON acknowledged, and either the
   peer's PIPE_ABANDON received or unilateral retirement, and inbound side
   ended -> RETIRED; FAILED -> RETIRED also by local retirement at the cap
   while suspended (MULTIPATH 5.4 step 9). ACTIVE, SUSPECT and DRAINING ->
   FAILED also on a carrier error or an abrupt removal.
```

A "stall" is the lack of acknowledgement or write progress from which the
endpoint concludes that a pipe may be dead (MULTIPATH 7.1). A DRAINING pipe
never returns to ACTIVE or SUSPECT: a stall only marks it suspect, which
moves it to the end of the pipe choice of MULTIPATH 6.5 and reinjects its
unacknowledged information. A pipe whose join was never confirmed fails
only on a carrier error or the handshake timeout.

| From | Event | To | Actions |
|---|---|---|---|
| (none) | session established: pipe 0 (both bootstrap carriers) | ACTIVE | |
| (none) | joiner allocates pipe ID and sends or hands off PIPE_JOIN | JOINING | carries control frames; not usable until ACTIVE, so it never holds back failover to a standby pipe; new stream data only while no outbound pipe is usable (MULTIPATH 5.7, 9, 11 rule S1) |
| (none) | acceptor verified a join with a down or bidi direction | ACTIVE | acceptor writes a record carrying a complete PIPE_ACK snapshot (when it has an entry), PIPE_PING and its PIPE_STATUS request, if any, within max_ack_delay (MULTIPATH 5.4 step 8) |
| JOINING | PIPE_ACK acknowledges position > 0 (up), or a record arrives on the down direction | ACTIVE | |
| JOINING | stall (acknowledgement stalls only while the endpoint has a live inbound pipe) | SUSPECT | stop new stream data; reinject; PIPE_PING (MULTIPATH 7.2) |
| JOINING | graceful removal (local) or PIPE_ABANDON(NO_ERROR) received | DRAINING | stop new stream data; PIPE_ABANDON(NO_ERROR) if not yet sent |
| JOINING, SUSPECT (never confirmed) | carrier error, or not confirmed within the handshake timeout | FAILED | reinject; PIPE_ABANDON(ZMUX_PIPE_FAILED) |
| ACTIVE | stall (acknowledgement stalls only while the endpoint has a live inbound pipe) | SUSPECT | stop new stream data; reinject; PIPE_PING |
| SUSPECT | acknowledged position advances and the carrier accepts writes | ACTIVE | drop reinjections now acknowledged |
| SUSPECT (confirmed) | lasting stall (acknowledgement stalls only while the endpoint has a live inbound pipe) | FAILED | close carrier; PIPE_ABANDON(ZMUX_PIPE_FAILED) |
| SUSPECT | carrier error, abrupt removal, PIPE_ABANDON(error) received | FAILED | close carrier; PIPE_ABANDON(ZMUX_PIPE_FAILED) if not yet sent |
| ACTIVE | carrier error, abrupt removal, PIPE_ABANDON(error) received | FAILED | reinject; close carrier; PIPE_ABANDON(ZMUX_PIPE_FAILED), or ZMUX_PIPE_RESOURCE_LIMIT for a removal for a local resource limit or a loop (MULTIPATH 8.3) |
| ACTIVE, SUSPECT | graceful removal (local) or PIPE_ABANDON(NO_ERROR) received | DRAINING | stop new stream data; PIPE_ABANDON(NO_ERROR) if not yet sent |
| DRAINING | stall | DRAINING (marked suspect) | last choice for control frames; reinject the unacknowledged information, stream data included, once |
| DRAINING | PIPE_ABANDON exchange complete (own sent, peer's received) and all stream data sent on the pipe acknowledged, checked when the own PIPE_ABANDON is sent, the peer's arrives or an acknowledgement arrives (MULTIPATH 8.2) | CLOSED | graceful write shutdown; reinject the information of unacknowledged control frames |
| DRAINING | drain not complete in time; carrier error; abrupt removal; PIPE_ABANDON(error) received | FAILED | reinject what was not yet reinjected; close carrier; no second PIPE_ABANDON |
| FAILED, CLOSED | own PIPE_ABANDON acknowledged, peer's PIPE_ABANDON received or unilateral retirement (max(2 s, 3 x PTO) after that acknowledgement), inbound side ended; at the first moment all hold, including the moment the failure or the drain itself completes them (MULTIPATH 8.4) | RETIRED | free state; for an attachment the peer joined, its pipe ID's bit stays set in the replay window until it slides out (MULTIPATH 4.3, 8.4) |
| FAILED | the endpoint is SUSPENDED, at its cap of unretired attachments, a rejoin needs the room, and this is the oldest failed attachment whose own PIPE_ABANDON is unacknowledged (MULTIPATH 5.4 step 9) | RETIRED | free state, including the pending PIPE_ABANDON |
| JOINING, ACTIVE, SUSPECT, DRAINING | the endpoint is SUSPENDED, at its cap of unretired attachments with none failed, a join that provides a direction it lacks needs the room, and this attachment has the oldest last progress (MULTIPATH 5.4 step 9) | RETIRED | reinject; close carrier; free state; no PIPE_ABANDON |

Modifiers: the effective **priority** and **standby** flag of the outbound
direction (local setting, else the peer's PIPE_STATUS request, else priority
0 and available) and the **suspect mark** of a DRAINING pipe restrict
sending (MULTIPATH 6.5, 8.2, 9) without being states.

New stream data may be sent only in ACTIVE, and in JOINING, reinjected
information first, while no outbound pipe is usable; control frames may use
SUSPECT and DRAINING pipes as a last resort (MULTIPATH 11 rule S1). An
outbound pipe is **live** in JOINING, ACTIVE, SUSPECT and DRAINING.

### 3.2 Inbound pipe (the local endpoint reads)

| From | Event | To | Actions |
|---|---|---|---|
| (none) | bootstrap, or acceptor verified a join (up or bidi) | OPEN | count positions; acknowledge |
| (none) | joiner sent or handed off a join with direction down or bidi | JOINING | not listed in PIPE_ACK yet; a QX_TRANSPORT_PARAMETERS record leading it is discarded (MULTIPATH 5.7 step 3) |
| JOINING | first complete record arrives | OPEN | the attachment becomes ACTIVE (MULTIPATH 5.7) |
| JOINING | carrier error, or not OPEN within the handshake timeout | ENDED (failed) | as for OPEN below |
| OPEN | graceful end at a record boundary after PIPE_ABANDON or CONNECTION_CLOSE was received, or after the endpoint sent PIPE_ABANDON(NO_ERROR) for the attachment | ENDED (graceful) | final position in next PIPE_ACK |
| OPEN | linger timeout after the PIPE_ABANDON exchange completed | ENDED (graceful) | close carrier |
| OPEN | carrier error, reset, end inside a record, or unexpected end | ENDED (failed) | discard incomplete record; fail own outbound direction of a pair; PIPE_ABANDON(ZMUX_PIPE_FAILED) (MULTIPATH 7.3) |
| OPEN, attachment without a local outbound direction | judged dead after no complete record for at least the minimum of MULTIPATH 7.3 | ENDED (failed) | as above; close carrier |
| OPEN | local abrupt removal | ENDED (failed) | close carrier |
| ENDED | attachment retired (MULTIPATH 8.4; at once when the end of this direction completes the conditions) | RETIRED | stop listing in PIPE_ACK |

An inbound pipe is **live** while JOINING or OPEN. Only OPEN inbound pipes
count for leaving SUSPENDED (RESUME 4.4).

### 3.3 Lanes

A pipe with lanes (MULTIPATH 2.5) has the states above as a whole; its
lanes have none of their own. A lane exists from its PIPE_LANE until the
pipe ends: "acknowledged position advances" and every stall in the tables
above are taken at the pipe's cumulative acknowledgement point (MULTIPATH
2.5, 7.1); "carrier error", "reset", "end inside a record" and "unexpected
end" apply to any lane and fail the whole pipe; a graceful end is the
graceful end of every lane at a record boundary (MULTIPATH 7.3, 8.2); and
"count positions; acknowledge" counts each lane's position (MULTIPATH 6.1).
A lane that names no live pipe is closed without any transition, and a
writer's lane that ends before it is confirmed (a PIPE_ACK position above 0
on it, within the handshake timeout) is refused, also without any
transition (MULTIPATH 5.8); only confirmed lanes fail their pipe, and on
the reader's side an inbound lane that ends before its second record is no
event.

| Pipe state (outbound) | Lanes the writer may open |
|---|---|
| JOINING | none (lane 0 not yet acknowledged) |
| ACTIVE, SUSPECT | new lanes once a position greater than 0 on lane 0 is acknowledged, none after a refused one; a lane carries only its PIPE_LANE until confirmed; SUSPECT pipes take only what rule S1 of MULTIPATH 11 allows, on any confirmed lane |
| DRAINING, FAILED, CLOSED, RETIRED | none |

### 3.4 Attachments without multipath

Outside multipath sessions there is one attachment, pipe 0, with no
JOINING, SUSPECT or DRAINING states: the failure of either direction, on any
of its lanes, ends the session (SPEC 6.6).

---

## 4. Session

### 4.1 States

| State | Meaning | Timers |
|---|---|---|
| ESTABLISHING | own parameters sent, peer's not yet processed; streams opened now send only early data (SPEC 6.1.2) | handshake timeout |
| ACTIVE | normal operation | idle timeout, keep-alive |
| DRAINING | GOAWAY sent or received; no new streams; existing streams continue | idle timeout, keep-alive, GOAWAY fence |
| SUSPENDED | resume in use and no live outbound or no live inbound pipe | resume grace |
| CLOSING | CONNECTION_CLOSE sent or received, or idle timeout expired; write sides shut down; reading until end or linger timeout | linger timeout |
| CLOSED | all state released | none |

QUIC's "closing" and "draining" states (RFC 9000 Section 10.2) both map to
zmux CLOSING. zmux DRAINING is the GOAWAY phase.

### 4.2 Transitions

| From | Event | To | Actions |
|---|---|---|---|
| ESTABLISHING | peer parameters processed, valid | ACTIVE | fix roles, features, session secret |
| ESTABLISHING | peer parameters processed after early data was sent, and the peer cannot take it (no PIPE_ACK in use, or `initial_max_streams_bidi` 0) | CLOSING | CONNECTION_CLOSE(NO_ERROR); report early data rejected; the early streams fail with it (SPEC 6.1.2) |
| ESTABLISHING | invalid parameters or first frame | CLOSING | CONNECTION_CLOSE(transport error) |
| ESTABLISHING | handshake timeout | CLOSED | close carriers without frames |
| ESTABLISHING | first bytes rejected (SPEC 6.2) | CLOSING | QX_TRANSPORT_PARAMETERS, then CONNECTION_CLOSE(PROTOCOL_VIOLATION) |
| ACTIVE | GOAWAY sent or received | DRAINING | EXTENSIONS 3.3, 3.5 |
| ACTIVE, DRAINING | no live outbound or inbound pipe, resume in use | SUSPENDED | RESUME 4.1 |
| ACTIVE, DRAINING | no live outbound or inbound pipe, resume not in use (multipath) | CLOSING or CLOSED | CONNECTION_CLOSE(NO_VIABLE_PATH) if an outbound pipe is live; report pipes lost (MULTIPATH 8.5) |
| ACTIVE, DRAINING | bootstrap pipe failure, not a multipath session | CLOSED | report carrier failure (SPEC 6.6) |
| ACTIVE, DRAINING | graceful end of the inbound bootstrap pipe without CONNECTION_CLOSE, session without acknowledgement (plain QMux peer) | CLOSING | shut down writes without frames; report the peer's idle close (SPEC 6.4) |
| ACTIVE, DRAINING | graceful end of the inbound bootstrap pipe (every lane) without CONNECTION_CLOSE, pipe acknowledgement in use | CLOSED | a pipe failure: report pipes lost (SPEC 6.4, 6.6) |
| SUSPENDED | an ACTIVE outbound pipe and an OPEN inbound pipe exist | DRAINING if it was DRAINING or a GOAWAY was sent or received while suspended, otherwise ACTIVE | stop grace timer; send queued frames (RESUME 4.4); the first records of the new pipes already carried complete PIPE_ACK snapshots (RESUME 5) |
| SUSPENDED | local protocol error detected (for example on an inbound pipe of a partially suspended endpoint) | CLOSING, or CLOSED without a live outbound pipe | CONNECTION_CLOSE on every live outbound pipe (SPEC 6.5) |
| SUSPENDED | grace timer expired | CLOSED (or CLOSING if an outbound pipe is live) | ZMUX_RESUME_TIMEOUT (RESUME 8) |
| SUSPENDED | the listener's limits on suspended sessions would be exceeded (RESUME 9) | CLOSED (or CLOSING if an outbound pipe is live) | ZMUX_RESUME_TIMEOUT |
| SUSPENDED | application closes | CLOSED (or CLOSING if an outbound pipe is live) | CONNECTION_CLOSE on any live outbound pipe (RESUME 11) |
| any but CLOSED | fail-closed resume condition | CLOSING or CLOSED | ZMUX_RESUME_FAILED (RESUME 7) |
| ACTIVE, DRAINING | local close or local protocol error | CLOSING | CONNECTION_CLOSE on every live outbound pipe, then shut down writes (SPEC 6.5) |
| ACTIVE, DRAINING, SUSPENDED | CONNECTION_CLOSE received | CLOSING | report peer error; at most one CONNECTION_CLOSE per outbound pipe; shut down writes |
| ACTIVE, DRAINING | idle timeout | CLOSING | shut down writes without frames; in sessions with acknowledgement (multipath or pipe acknowledgement) CONNECTION_CLOSE(ZMUX_IDLE_TIMEOUT) first (SPEC 6.4) |
| DRAINING | all streams done and application closes | CLOSING | application CONNECTION_CLOSE(NO_ERROR) |
| CLOSING | every inbound pipe ended, or linger timeout | CLOSED | close carriers; abortive close only now |

### 4.3 Error precedence

The session error is the first of: a processed peer CONNECTION_CLOSE, a local
close or detected violation, a timeout, a carrier failure or the loss of
every pipe in a direction ("pipes lost") (SPEC 6.5).
