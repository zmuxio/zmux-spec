# zmux Scheduler

Status: normative for zmux implementations (CONFORMANCE.md C-5, C-8 and
Section 4.5), not part of the wire protocol. Everything here is local to the
sender of a multipath session: the estimators it keeps per outbound pipe,
the scheduling policies, tier gating, probation and the sender-side budgets.
A peer cannot tell which algorithm a sender runs, interoperability never
depends on it, and MULTIPATH.md, which defines the protocol, only refers to
the terms defined here (usable, eligible, probation, the estimates). A
single-pipe session and a session without `zmux_multipath` run none of it
and keep none of its state (IMPLEMENTATION.md Section 2.6), except that
with pipe acknowledgement (MULTIPATH.md Section 3.5) the sender keeps the
estimators of Section 2 for its pipe, places its frames on the pipe's lanes
by Section 9.2 when it has lanes and, when the carrier needs it, runs the
limiter of Section 9.1.

Conformance is checked with deterministic scenarios rather than by traces:
which pipe gets a chunk depends on rates and queues that a trace runner does
not model. CONFORMANCE.md Section 4.5 lists the scenarios and their targets.

---

## 1. Model

The sender judges each outbound pipe p from what the protocol gives it:
PIPE_ACK entries (MULTIPATH.md Section 6.2), its own carrier's progress and,
where the adapter reports them, the carrier's unsent bytes and RTT. From
these it keeps the estimates of Section 2. Whenever an outbound pipe can
accept bytes, the scheduler decides which frames to put on it, subject to the
rules of MULTIPATH.md Section 11.1: tier gating (Section 10) first decides
which pipes may take new stream data, and the policy (Sections 3 to 8) then
chooses among them. Probation (Section 11) keeps pipes that have just joined
or come back out of the choice for a while, and the budgets of Section 13
bound what the sender retains.

---

## 2. Estimators

### 2.1 RTT

For each outbound pipe the sender keeps min_rtt, smoothed_rtt and rttvar as
RFC 9002 Section 5 defines, except that min_rtt_p is the minimum of the RTT
samples of p taken in the last 300 s, and keeps its previous value while no
sample was taken in that window. The same filter is the per-pipe RTT input
of receive-window auto-tuning (IMPLEMENTATION.md Section 6.2), so a pipe
keeps one minimum filter, not two. A span shorter than a bulk transfer
would let a standing carrier queue enter min_rtt once the queue-free startup
samples leave the window. The long filter
keeps a minimum for up to 300 s after a path's delay really rose; the
arrival estimate of Section 4 then still counts the pipe's queue through
Q_p / R_p, and the rate estimate follows the slower path at once.

A sample taken while the session is quiet (MULTIPATH.md Section 7.1) does
not enter min_rtt_p unless p has no min_rtt yet; smoothed_rtt and rttvar
take it. In a quiet session the answers to probes return on the probed
pipe pair, not on the common return path of Section 2.2 (MULTIPATH.md
Section 6.5), so such a minimum would carry that pair's return delay into
OWD_p once the samples of the last busy period leave the window.

The send time of a byte is the moment the write call that carries it was
handed to the carrier adapter. A PIPE_ACK entry yields an RTT sample only
when it advances the pipe's acknowledged position and the newly acknowledged
bytes include at least one ack-eliciting byte (RFC 9002 Section 5.1):

    latest_rtt = ack_receipt_time - send_time(byte Position - 1)
    adjusted   = latest_rtt - min(ACK Delay, peer zmux_max_ack_delay)   if that is >= min_rtt
               = latest_rtt                                              otherwise

For a pipe with lanes (MULTIPATH.md Section 2.5) the acknowledged position
is the cumulative acknowledgement point and bytes are numbered in send
order, here and in Section 2.3: an entry yields a sample only when it
advances that point, Position - 1 is the last byte below the new point, and
ACK Delay is that entry's.

No sample, RTT or delivery rate, is taken from entries for a FAILED pipe,
or for bytes that were sent before the sender last entered SUSPENDED
(RESUME.md Section 4), before p last became SUSPECT, or while the sender had
no live inbound pipe (when no acknowledgement could reach it and detection
was paused, MULTIPATH.md Section 7.1), since a sample must never measure an
outage
(after an outage the bytes held through it are acknowledged with delays of
seconds, which would inflate smoothed_rtt and collapse R_p just when
probation, Section 11, needs current estimates), or from an RTT entry whose
ACK Delay exceeds the peer's `zmux_max_ack_delay`. Such an entry still
advances the position and may give a rate sample. It is typically the
redundant copy of MULTIPATH.md Section 6.5, which may be sent up to 50 ms
late on a slower return pipe; as an RTT sample it would measure that pipe
and the redundancy timer, inflate rttvar and so T_suspect. The rule yields when p has had no RTT sample for 1 s: the next entry
that advances p's position is then sampled by RFC 9002's rule, with the ACK
Delay capped at `zmux_max_ack_delay`, so that a peer whose acknowledgements
are always late (behind a slow, busy outbound pipe of its own) cannot freeze
p's estimates.

A new pipe never inherits another pipe's estimates. Until its first sample,
which comes from the acknowledgement of its PIPE_JOIN record, of the
PIPE_PING in the acceptor's first record (MULTIPATH.md Section 5.4 step 8)
or of the first record on it, it uses the RFC 9002 Section 6.2.2 initial
values: smoothed_rtt = 333 ms and rttvar = 166.5 ms. After a resumption,
pipes that stayed live keep their estimates and new pipes start from these
initial values.

### 2.2 One-way delay

The scheduler needs the forward one-way delay of each pipe. Acknowledgements
of all pipes travel on the same lowest-delay return pipe (the primary pipe
of MULTIPATH.md Section 6.5), so each RTT is that pipe's forward delay plus
one common return delay. With min_rtt, which excludes queuing:

    OWD_p = min_rtt_p - min over live outbound pipes q of (min_rtt_q) / 2

The second term estimates the common return delay as half of the smallest
round trip. Differences between OWD values are then exact whatever the
return path, and the values are exact when the fastest pipe is symmetric.
smoothed_rtt / 2 would halve the differences and count carrier queuing
twice. Before a pipe's first sample, OWD_p = smoothed_rtt_p / 2 (166.5 ms).
The answer to a PIPE_PING also returns on the probed pipe pair
(MULTIPATH.md Section 6.5), in a quiet session only there; when that copy
arrives first in a busy session, its sample holds the pair's own return
delay, which is smaller than the common one only for a pipe whose return
is faster than the primary pipe's, typically a pipe of a less-preferred
tier, whose OWD_p is then low by the difference until it carries data.
Quiet samples stay out of min_rtt (Section 2.1).

### 2.3 Delivery rate

R_p, in bytes per second, follows BBR's delivery-rate sampling
(draft-ietf-ccwg-bbr, "Delivery Rate Samples"):

- the pipe keeps `delivered`, its acknowledged byte count, `delivered_time`,
  the time of the last advance, and `first_sent_time`, the send time of the
  most recently acknowledged range; when it sends while nothing is in
  flight on p, it sets `delivered_time` and `first_sent_time` to the current
  time;
- when it sends a byte range on p, the sender records the three values with
  the range;
- when an acknowledgement advances p's position, one sample is taken from
  the most recently sent range it covers: (delivered_now -
  delivered_at_send) / max(send_time - first_sent_time_at_send, ack_time -
  delivered_time_at_send), which is robust against compressed and delayed
  acknowledgements; then `first_sent_time` becomes that range's send time;
- a sample is **application-limited** unless the pipe was the bottleneck
  during the sampled interval, which zmux observes in one way only: at some
  moment between the send and the acknowledgement of the range, the pipe
  was not writable (the carrier refused bytes, a blocking write call was in
  progress, API.md Section 4.1, or the carrier queue limiter of Section 9.1
  held the pipe back) while the endpoint had stream data within
  flow control and the retransmit budget that was not yet committed to any
  pipe. Ranges sent while the pipe stayed writable, or while it was busy
  but the endpoint had nothing more to send (a pipe that carries only small
  urgent messages, each in its own blocking write), are
  application-limited, including when the scheduler had data but chose
  another pipe;
- a range is also application-limited when it was sent while an
  **application-limited bubble** was in flight, as in BBR: at every
  application-limited moment of p, that is, whenever a write opportunity on
  p ends with p still writable because the scheduler had nothing more for
  it, the sender records the number of bytes it has sent on p (its
  position); a range sent before p's acknowledged position has passed the
  last recorded value is application-limited. A new pipe starts with the
  recorded value at position 0, so the ranges it sends before its first
  acknowledgement advance are application-limited too. The sample of such a range
  spans the idle time before the burst: the first ranges of a burst are
  acknowledged one round trip after they were sent, so a burst not much
  longer than a round trip measures a fraction of the rate and, as a
  bottleneck sample, would replace the true maximum once that ages out of
  the window and overstate the drain time of its tier (Section 10);
- R_p is the maximum of the samples taken during the last 10 round trips of
  p (a round trip ends when a byte sent after it began is acknowledged) and
  within the last 10 s; an application-limited sample enters the maximum
  only if it is larger than the current R_p. When no sample is left in the
  window, for example after an idle period, the last value of R_p is kept,
  so idle periods never lower it, while a pipe that really slowed down
  produces new, lower bottleneck samples and its old maximum ages out.
- On a pipe that the carrier queue limiter of Section 9.1 governs, R_p is
  the smaller of that maximum and the limiter's rate bound R_lo, which
  follows a lasting drop of the path's rate within a probe rather than
  within 10 round trips that the queue itself has lengthened.

Until the first sample, R_p is 1 MB/s. R_p is used for the arrival estimate
(Section 4), the queuing terms of failure detection (MULTIPATH.md Section
7.1) and the budgets (Section 13). IMPLEMENTATION.md Section 7 describes the
same estimator.

**Placement rate.** A usable pipe that has no bottleneck sample yet, while
another usable pipe of its tier with the same standby flag has one, is
placed as if it were as fast as the fastest such pipe: where a spreading
policy chooses among the eligible pipes (the arrival estimate of Section 4
and with it the ECF and BLEST tests of Section 5 and the redundant pair of
Section 4.1, the rate weights of Section 6) it uses max(R_p, R_q) instead
of R_p, from the first time that is larger for four of p's own round trips
(Section 2.3), or until p's first bottleneck sample or a penalty
(MULTIPATH.md Section 7.5.1) if one comes sooner. Everything else
(`affinity` bindings, tier gating, failure detection, the limiter, the
budgets) keeps R_p: a binding is not revisited for every chunk, and with
the placement rate in the binding cost would put bulk streams on a slow
pipe. A pipe that joins, or comes back, while the others
carry the load would otherwise never get data on a fast path: its 1 MB/s
start makes BLEST refuse it (with equal one-way delays the reorder budget
of Section 13.2 is one chunk), and the application-limited samples of the
urgent copies it does carry keep it there. Four round trips are enough
for its own samples, application-limited ones included, to reach what it
carries; ending only at a bottleneck sample is not, because a pipe that
flow control keeps below its carrier's rate never takes one. A slower pipe gets what its own rate earns once
the four round trips are over, and what it holds meanwhile is bounded by
its low-water mark or the limiter and covered by opportunistic
reinjection. Under `affinity` a joined pipe takes stream directions as they bind
(Section 7); bindings move only at idle points.

---

## 3. Policies

Whatever the policy, urgent data is also sent redundantly on a second pipe
of its tier when the tier has one (Section 4.1; under `session-affinity` a
tier is a single pipe). Implementations MUST provide these policies, selectable per session and per
stream direction and changeable at any time (API.md Section 5.2, Section
12). Every policy chooses only among the eligible pipes of tier gating
(Section 10); with every pipe at the default priority, as in the
descriptions below, all usable non-standby pipes are eligible. Per-frame
spreading is a first-class mode: every stream, in both directions, can be
scattered over all pipes, and receivers always accept it (MULTIPATH.md
Section 13). The session default is `affinity`; `spread-eta` is the default
spreading policy and is what deployments that scatter every stream
configure.

| Policy | Behaviour | Use |
|---|---|---|
| `affinity` (session default) | each stream direction is bound to one pipe, in the most-preferred tier with room for it (by capacity over all tiers when none has room), and moves only at idle points, on failure or when a more-preferred tier has room for it (Section 7) | zero reordering, many concurrent streams |
| `spread-eta` (default spreading policy) | every chunk of every stream goes to the pipe with the earliest estimated arrival, with ECF/BLEST admission for slower pipes (Section 5) | aggregate bandwidth for one or many streams, lowest latency |
| `spread-round-robin` | every chunk goes to the next pipe of a smooth weighted round-robin over the pipes that have room, by the pipes' Weights (Section 6) | load balancing by configured shares, for example by known capacities |
| `spread-rate-weighted` | the same round-robin, with each pipe's Weight multiplied by its delivery rate R_p (Section 6) | load balancing across the pipes of a tier by their measured rates |
| `session-affinity` | one active pipe per tier and direction, the most-preferred one carrying the data and overflow going chunk by chunk to the next tier's active pipe, the others idle (Section 8) | failover, one path at a time |
| custom | an application-supplied policy object (API.md Section 5.2) | special deployments |

---

## 4. Arrival estimate

For pipe p and a chunk of L bytes:

    ETA_p(L) = (Q_p + L) / R_p + OWD_p

- Q_p: bytes committed to p but not yet transmitted, over its lanes: the
  bytes of the batches handed to p whose write call has not returned (a
  batch counts in full from the start of its call until the call returns,
  then by what the carrier did not take), plus the carrier's unsent bytes
  (API.md Section 7.2 `queued_bytes`, 0 when the adapter reports none). It
  is the one queue measure of a pipe: `unsent_ahead` (MULTIPATH.md Section
  7.1) is Q_p at the moment a batch is handed over, excluding that batch. Reporting them is an optional adapter capability (for example
  `tcpi_notsent_bytes` or SIOCOUTQNSD on a Linux TCP socket). A carrier
  without it (a TLS or WebSocket library that buffers in user space, an
  HTTP/2, HTTP/3 or QUIC stream, a user-supplied adapter, a platform without
  the socket option) contributes only what the core has committed; what it
  buffers itself is then invisible here, which is one reason urgent data is
  scheduled redundantly (Section 4.1);
- R_p: delivery-rate estimate (Section 2.3), 1 MB/s before the first sample,
  or the placement rate of Section 2.3 for a pipe that has no bottleneck
  sample yet;
- OWD_p: one-way delay estimate (Section 2.2). It excludes queuing, which
  Q_p / R_p already counts.

### 4.1 Redundant urgent data

A pipe can stop delivering for a while without becoming suspect: a pause
of the peer or of the network shorter than T_suspect (250 ms at least,
MULTIPATH.md Section 7.1), the first T_suspect of a real failure, or a
queue that Q_p does not show (a deep network buffer, a carrier that buffers
without reporting it). Data already given to such a pipe waits. For bulk
data that costs little, but an urgent message waits the whole pause.
zmux therefore schedules urgent data on two pipes, using the redundant
scheduling that MULTIPATH.md Section 7.5 allows as its third exception:

- **Which data.** The STREAM frames, FIN included, of stream directions
  whose redundancy is on. The scheduler parameter `redundancy` (API.md
  Section 6) sets the session default: `urgent`, the default, turns it on
  for stream directions of urgency 0 to 2; `off` turns redundancy off,
  including the idle copy below. `SetRedundant` (API.md Section 5.1)
  overrides the default for one stream direction, in either direction.
- **The pair.** A chunk of such data may be written by any pipe that the
  policy would give it, and also by either of the two pipes of its
  eligible tier (Section 10) with the smallest ETA_p(L), whichever has a
  write opportunity first. A copy of the chunk is then queued for the pipe
  of the same eligible tier with the smallest ETA_p(L) other than the one
  that wrote it, and written at that pipe's next write opportunity ahead
  of its other stream data. In a tier with a single usable pipe, which under
  `session-affinity` is every tier (Section 8), the pair is that pipe's
  lane 0 and its **copy lane** when the pipe has lanes (Section 9.2): the
  copy goes to the copy lane, a lane that carries no bound stream
  direction, within the same set, bound and switch, so that a packet lost
  on one carrier stream does not hold the message back for a round trip.
  Lanes share their connection's fate, so this pair protects against loss
  and head-of-line blocking, not against a stalled path; the copy after an
  idle period below always needs a second pipe. Without lanes the data of
  a single-pipe tier is sent once.
- **The first chunk after an idle period.** Unless `redundancy` is `off` or
  the stream direction's redundancy is turned off, the first
  `spread_chunk` of new stream data, of any urgency, that the sender writes
  on a pipe that has nothing unacknowledged and has had no acknowledgement
  progress for max(1 s, T_suspect_p) (counted from the moment the pipe
  became ACTIVE if it never had any) is copied to the pipe of the same
  eligible tier with the smallest ETA_p(L) other than that pipe; without
  one it is sent once (a lane of the same pipe does not count: it shares
  the pipe's fate). After an idle period nothing shows whether the
  pipe is still alive, and probing it first would delay the data by a
  round trip on every idle period (MULTIPATH.md Section 7.1).
- **One copy, bounded.** A chunk gets at most one copy, whichever rule
  asks for it. The copy bytes that are queued or sent and whose data is
  still owed and not yet acknowledged on any pipe never exceed

      redundancy_bound = max(spread_chunk, retransmit_budget / 64)

  (Section 13.1); a chunk that would exceed it is sent once. The bound
  follows the path, so that on long or fast paths, where an urgent stream
  has more than one `spread_chunk` in flight, every chunk can still be
  copied, while a bulk stream marked urgent costs at most about 1/16 of a
  round trip's data more. A copy leaves the bound when its data is
  acknowledged on any pipe or is no longer owed (its stream direction is
  reset, below the Reliable Size of RESET_STREAM_AT only what stays owed
  counts), and a queued copy is dropped, and leaves the bound, when its
  data is acknowledged or no longer owed or when the pipe it waits for
  becomes suspect, fails, starts draining or is removed.

Copies are ordinary STREAM frames that the receiver handles as duplicates
(MULTIPATH.md Section 7.6), and like every frame they count in the
position, the rate samples and the retention of the pipe that carries them.
They are not reinjection: the rules of MULTIPATH.md Section 7.4 apply to
them as to any frame sent on a pipe that later becomes suspect, and both
pipes of a pair belong to one eligible tier, so tier gating, standby and
the peer's PIPE_STATUS requests are respected. Late binding (Section 9)
holds for copies too: a queued copy waits outside the carrier until its pipe
has a write opportunity, and can be dropped until then. A stream direction that is sent redundantly can be reordered
at the receiver even under `affinity`, when the bound makes a chunk go
once on the other pipe of the pair.

The cost is the volume of urgent data once more. The bound scales with the
path because a bound of one `spread_chunk` leaves most urgent bytes of a
long, fast path without a copy.

---

## 5. `spread-eta`

On an opportunity to write on pipe p, for the next chunk of the most urgent
stream direction (chunk size `spread_chunk`, default 16 KiB, never above what
fits in one record of the peer's `max_record_size`):

1. f = the eligible pipe (Section 10) with the smallest ETA_f(L), and f = p
   when ETA_p(L) equals that smallest value (ties go to the pipe that has
   the write opportunity, so equal pipes share a stream instead of one of
   them waiting for ECF and BLEST); p is considered only if it is eligible
   itself.
2. If f = p, send the chunk on p.
3. Otherwise p is slower than f. Admit p only if both hold:
   - **ECF** (Lim et al., CoNEXT 2017): ETA_p(L) <= (1 + beta) x ETA_f(B +
     L), where B is the **backlog** that f would have to carry before the
     last ready byte of this stream direction if p were not used. Data is
     **ready** when it is ready to send within the stream's send credit
     and not yet committed to a pipe. For an incremental stream direction
     of urgency u with r bytes ready,

         B = min(session send credit, U + N_u + sum over the incremental stream directions j of level u of min(ready_j, r)) - L

     where U is the ready data of the more urgent levels, N_u the ready
     data of the non-incremental stream directions of level u, and the sum
     includes this stream direction (min(r, r) = r): the round robin of
     IMPLEMENTATION.md Section 5 serves the incremental stream directions
     of a level in turns, so f finishes one with less ready data first and
     serves one with more only as much as this one before this one is done,
     while a non-incremental stream direction, once its turn comes, keeps it
     until its ready data is sent, so all of it counts. With a single
     stream direction, B is its ready data less this chunk. A
     non-incremental stream direction takes all ready data of its level in
     place of N_u and the sum (as n = 1 below).
     beta = 0.25. For urgency 0 to 2, B counts only the more urgent levels
     (B = min(session send credit, U)) and beta = 0.
     The chunk is sent on p only if it arrives no later than it would after
     waiting for f to work through the backlog;
   - **BLEST**: what f sends while the chunk travels on p, F = R_f x
     (ETA_p(L) - OWD_f), must fit in the credit that would otherwise stall f
     while the slow pipe delivers: F <= W, the smaller of the session's
     remaining send credit and the reorder budget (Section 13.2), and F / n
     <= the stream's remaining send credit, where n is the number of
     incremental stream directions of the chunk's urgency level that have
     data ready within their send credit, this one included (n = 1 for a
     non-incremental stream direction), at every urgency, 0 to 2 included.
     The stream directions of a level share f in the round robin of
     IMPLEMENTATION.md Section 5, so only the stream's share of F can stall
     on its own credit; the session's credit and the reorder budget take all
     of it.
4. If p is not admitted for this stream direction, try the next stream
   direction in priority order; if none is admitted, leave p idle until the
   next opportunity. Implementations evaluate admission per urgency level
   and ready amount: ECF depends only on L, the level, r and whether the
   stream direction is incremental (the sum of
   min(ready_j, r) over a level comes from the ready amounts of its
   incremental stream directions kept in order, O(log n), N_u is one sum
   per level, and the result is the same for every stream direction with
   the same r), BLEST on two thresholds, F for the session and F / n for a
   stream, with n one count per level; this step therefore never costs a
   pass over the streams per stream.

Comparing only the head chunk, without the backlog, never admits a pipe whose
one-way delay exceeds the fastest pipe's by more than a few milliseconds, and
one bulk stream then gets only the fastest pipe's rate. BLEST keeps a
slowed pipe from pulling the session below the fast pipe alone.

Both per-stream terms follow the round robin. Comparing all of F with one
stream's credit would make the choice of pipes depend on how the receiver
sizes its windows rather than on the paths: with receive-window auto-tuning
(IMPLEMENTATION.md Section 6.2) each of many streams gets a window for its
own share of the rate, so F would exceed every stream's credit and the
slower pipes would never be admitted. Counting the whole level's backlog for
every stream direction in ECF would make a short stream behind long ones
look as if it had to wait for all of them.

---

## 6. Weighted round-robin: `spread-round-robin` and `spread-rate-weighted`

One engine serves both policies; they differ only in the weight a pipe
counts with:

| Policy | Weight of pipe q in the round-robin |
|---|---|
| `spread-round-robin` | Weight_q |
| `spread-rate-weighted` | Weight_q x R_q |

**Engine.** Chunks are assigned in smooth weighted round-robin order over
the candidates: the writable pipes of the eligible tier being served
(Section 10, rule T2) when the chunk is scheduled. Each pipe keeps a current
value, 0 when it becomes eligible. For each chunk, every candidate's current
value is increased by its weight; the candidate with the largest current
value takes the chunk (on a tie, the lowest pipe ID), and the sum of the
candidates' weights is subtracted from its current value. Pipes that are not
candidates for a chunk (not writable, or not in an eligible tier) are
skipped and accumulate nothing, so a pipe that is full never builds up a
claim it would redeem in a burst later. With weights 2 and 1 the chunks go
1st, 2nd, 1st, 1st, 2nd, 1st and so on. The current values are kept per
policy instance: one set per direction for the session's policy, and one for
each stream direction that has its own round-robin policy.

`spread-round-robin` does not use the estimators, so its shares are exactly
the configured ones as long as every pipe has room; with Weights
proportional to the pipes' capacities the load is shared by capacity,
whatever the demand. Weights are 64-bit and the current values range over
plus or minus the sum of the candidates' weights; an implementation whose
arithmetic cannot hold that sum scales the weights of the direction down
proportionally (for example by one common right shift, keeping every
non-zero weight at least 1), which changes the shares only by the rounding.

`spread-rate-weighted` shares the load of a tier by measured rate, which is
what tiered deployments use to share the preferred pipes; its weights are
real numbers (R_q in bytes per second) and an implementation computes them
in floating point. It has no admission check of its own: the round-robin
gives a slower pipe chunks only in proportion to its rate, and flow control
bounds what can be outstanding.

**Weights.** Every outbound pipe has a Weight, a local setting of the
sender (API.md Section 4.2) with no upper limit and default 1; it is not
sent to the peer. `spread-eta`, `affinity` and `session-affinity` ignore
it. A Weight of 0 excludes the pipe from the two round-robin policies unless
every candidate has Weight 0, in which case they treat all Weights as 1.
Weights never override tier gating: a pipe of a tier that is not eligible,
or an unusable pipe, is skipped whatever its Weight.

---

## 7. `affinity`

A stream direction is bound to one pipe when it first has data to send. The
binding uses these quantities:

- the **expected bytes** E of a stream direction: the bytes the application
  has written to it that are not yet committed to a pipe, and at least its
  stream send buffer limit (IMPLEMENTATION.md Section 6.4) while an
  application write on it waits for send-buffer space. They are what the
  sender can observe of its demand: a bulk writer counts with a whole
  stream buffer even when the shared session buffer gives it less, a
  long-lived stream that writes a little at a time counts with what it has
  queued;
- A_p: the sum of the expected bytes of the other stream directions bound to
  pipe p (a direction that is moving to p counts for p);
- the **binding wait** of p, (Q_p + A_p) / R_p, and the **binding cost** of
  p for a direction, OWD_p + (Q_p + A_p + E) / R_p.

A tier (Section 10) **has room** for a direction when one of its pipes has a
binding wait of at most the overflow delay D. The direction binds within the
most-preferred tier that has room, to the pipe with the smallest binding
cost; stream directions whose expected bytes are below 256 KiB, or whose
urgency is 0 to 2, bind to the tier's pipe with the smallest OWD. When no
tier has room, the direction binds to the pipe with the smallest binding
cost among the pipes of every tier, which balances bulk streams by capacity
(each pipe gets streams in proportion to its rate, so they progress at
similar rates); small and urgent directions then bind to the smallest OWD
of the most-preferred tier. Room is judged per pipe, not per tier: a
tier-level test ((Q_t + A_t) / R_t) fails as soon as one bulk stream is
bound to the preferred tier. A binding is changed only:

- at an idle point, when no unacknowledged data of that stream direction
  remains on its pipe, by the rule above;
- when the pipe becomes suspect, draining or failed, or stops being usable;
- when a tier more preferred than the bound pipe's has room for this
  direction. The direction is then rebound as a provisional binding is
  (below), so that a binding that went to a less-preferred tier returns once
  the preferred tier has room. A binding made while no tier had room never
  causes a rebind by itself, and a direction that moves adds its expected
  bytes to its new pipe, so bindings do not move back and forth under
  sustained load.

A bound direction never overflows chunk by chunk: when its pipe has no room,
it waits. Overflow under `affinity` happens at binding time, by binding new
stream directions to a less-preferred tier while the preferred tiers have no
room.

A binding made before every usable pipe has an RTT sample is
**provisional**: at that time all pipes look alike (initial estimates), and a
bulk stream never reaches an idle point, so a chance binding to the slowest
pipe would be permanent. Once all usable pipes have a
sample, the endpoint re-evaluates provisional bindings; if another pipe is
preferred, it stops new data of that stream direction until its data on the
old pipe is acknowledged (an idle point the sender creates) and then binds to
the preferred pipe. Within one pipe, data stays in order, so the receiver
never reorders.

---

## 8. `session-affinity`

All traffic in a direction uses one active pipe per tier: the usable pipe
with the smallest OWD of the tier (Section 10; standby pipes on failover).
The active pipe of the most-preferred tier carries the data, and the other
pipes carry none and are probed. Tier rules apply as for the spreading
policies, with each tier reduced to its active pipe: while that pipe cannot
send its backlog within D, chunks overflow to the active pipe of the next
eligible tier, and they return as soon as the tier is no longer saturated.
Counting the idle pipes of the tier in R_t would overstate its capacity and
delay the overflow. The active pipe changes on suspicion or failure, when a
pipe of a more preferred tier becomes usable, or by application request.

---

## 9. Late binding

MULTIPATH.md Section 11.1 rule S10 requires that frames are bound to a pipe
only when that pipe can accept bytes. A pipe SHOULD have at most one chunk
plus the carrier's low-water mark of bytes committed but not yet
transmitted, with writes driven by writability (IMPLEMENTATION.md Section
8). A low-water mark is an optional carrier capability: a TCP socket on
Linux has TCP_NOTSENT_LOWAT, set to max(32 KiB, R_p x RTT_min_p / 8) per
pipe in multipath sessions (max(128 KiB, send rate x RTT_min / 8) on a
single pipe, IMPLEMENTATION.md Section 9.1); a carrier without one is given
at most that many bytes beyond what it reports as sent, or, when it reports
nothing, what a small send buffer of its own leaves room for
(IMPLEMENTATION.md Section 8), and the limiter of Section 9.1 paces what
such a carrier is given. The rate term matters only on long, fast
paths, where a fixed low-water mark makes the writer refill the carrier
every millisecond and costs 8 to 12 % of a 1 Gbit/s x 100 to 200 ms path.
Bytes in a carrier's send buffer can no longer be moved to another pipe;
deep buffers, in the carrier or in the network, are the main cause of
head-of-line blocking across heterogeneous pipes, and what the sender
cannot see of them is covered for urgent data by Section 4.1.

### 9.1 Carrier queue limiter

A low-water mark and a count of unsent bytes are optional adapter
capabilities. A carrier that has neither (a TLS or WebSocket library with
its own buffer, an HTTP/2, HTTP/3 or QUIC stream, KCP, an in-process pipe, a
user-supplied adapter) accepts bytes until its own buffer is full, and that
buffer becomes the queue in front of every urgent frame. The write
call and batch bounds (IMPLEMENTATION.md Section 4) cannot help: they bound
each call, not what the carrier holds. On such a pipe the sender
therefore paces itself from what the peer acknowledges, in a multipath
session and, with pipe acknowledgement (MULTIPATH.md Section 3.5), on the
single pipe of a session without multipath, in the same way:
in the manner of BBR (Cardwell et al., ACM Queue 2016), which needs nothing
from the carrier.

- **Inputs**, all from PIPE_ACK (Section 2): R_p, the delivery rate;
  RTT_base_p, kept as BBR keeps min_rtt: the smallest u = adjusted RTT
  sample - unsent_ahead / R_p seen so far (unsent_ahead as in MULTIPATH.md
  Section 7.1, zero on such a carrier; the initial smoothed_rtt before the
  first sample), which only the base refresh below replaces; one value, no
  window of samples. A sample below RTT_base_p lowers it at once, at any
  time, during a base refresh too (queues only add delay, so no sample is
  below the path's minimum); at the end of a refresh RTT_base_p is set to
  what the refresh measured, as said there, even when that is above a
  sample taken earlier in the refresh: a refresh exists to learn a base
  that rose, and the samples of bytes sent before the old queue drained
  predate the rise. The base is not a minimum over a sliding window: that
  would let the pipe's own standing queue into the base and hide it from
  S_p and from the refresh;
  and the standing queue S_p, the minimum of u over the last
  max(smoothed_rtt_p / 2, the interval since the previous sample) minus
  RTT_base_p. **Bytes in flight**, wherever this section uses them (the
  in-flight cap, the drain exit, the gain cycle, "nothing in flight" in the
  base refresh), are the pipe's positions from its acknowledged position
  up to the end of the last ack-eliciting byte sent (RFC 9002 Section 2),
  0 when every ack-eliciting byte is acknowledged: a record of PIPE_ACK or
  PADDING alone elicits no acknowledgement (MULTIPATH.md Section 6.3), so
  bytes after the last ack-eliciting one may stay unacknowledged
  indefinitely, and counting them would hold a paced pipe below its cap,
  or in a refresh, forever. On a pipe with lanes (Section 9.2) the
  acknowledged position is the cumulative acknowledgement point
  (MULTIPATH.md Section 2.5), and the **held bytes** are those
  acknowledged on their own lanes but still beyond that point: they have
  arrived, and only a hole on another lane keeps the point back. The
  in-flight cap, the drain exit and the gain cycle leave out of the bytes
  in flight all held bytes during start-up and, afterwards, the smallest
  number of held bytes at any moment of the last RTT_base_p, the refresh's
  cap of A included ("nothing in flight" in the base refresh still means
  everything acknowledged). A
  hole repaired within a round trip thus still counts and slows the pipe
  while it lasts, which drains what an overestimated R_p queued, while
  bytes held a whole round trip, which at a high loss rate or on a long
  fast path is always the case because some lane always waits for a
  retransmission, no longer fill the cap with bytes that have arrived.
- **Pacing.** New stream data, reinjection and copies leave at
  pacing_gain x R_p, in quanta: the sender needs no per-packet timer and no
  timer finer than its own granularity G (at most 1 ms); it may send up to
  max(2 x spread_chunk, pacing_gain x R_p x G) at once, so it wakes at most
  1/G times a second whatever the rate (at 10 Gbit/s and G = 1 ms a quantum
  is 1.25 MB, 1000 wake-ups a second; the bytes go out in the write calls
  of IMPLEMENTATION.md Section 4). The allowance accrues with the time that
  actually elapsed: a wake-up that comes late sends what accrued, up to
  twice that quantum, rather than discarding it (a pacer that drops what
  accrued while its timer was late loses the rate by the lateness of every
  wake-up). Above the 2 x spread_chunk floor that cap is 2 x G of accrual
  at every rate, so a wake-up up to G late loses nothing at 10 Gbit/s any
  more than at 100 Mbit/s. The gain is 2.885 at start-up, until R_p has not grown by 25 %
  over three round trips (Section 2.3's round trips) that each took a
  bottleneck sample: as in BBR, a round trip limited by the application
  says nothing about the path; 1 / 2.885 while the
  queue that start-up built drains, until S_p <= B or the bytes in flight
  are at most max(R_p x RTT_base_p, A) (A of the in-flight cap below: what
  the peer may leave unacknowledged stays in flight however empty the
  queue, so on a pipe whose R_p x RTT_base_p is below A, a LAN path, the
  bytes in flight never fall to R_p x RTT_base_p, and on a real host,
  whose timers and scheduling jitter by more than B, S_p <= B may never
  hold either: the pipe would stay at 1 / 2.885; an exit at R_p x
  RTT_base_p + A would leave up to A standing in the queue on every pipe);
  then a cycle of 1.25, 0.75 and six times 1, each for max(RTT_base_p, 1
  ms), which probes for more rate and drains what the probe queued, except
  that the 0.75 phase lasts until the bytes in flight are at most R_p x
  RTT_base_p + A, and at most four phase durations (as BBR v2's
  ProbeBW_DOWN). It thus ends early when the probe could queue less than
  the phase drains (a hidden buffer below a quarter of R_p x RTT_base_p,
  or a carrier that its own send buffer limits, as a QUIC stream's), and it
  continues while a queue that an overestimated R_p built still stands.
  The cycle is not restarted early after a rate rise: that follows a
  recovering path faster but costs urgent latency while it does.
- **In-flight cap.** What is sent and not yet acknowledged stays below
  2 x R_p x (RTT_base_p + B) + A, where B is the latency budget
  (IMPLEMENTATION.md Section 6.2, 100 us) and A = min(the peer's
  `zmux_ack_threshold`, R_p x the peer's `zmux_max_ack_delay`) +
  `spread_chunk`, what the peer may leave unacknowledged. The cap keeps
  acknowledgements clocking the sender when the rate estimate is too high.
- **Base refresh.** Once start-up is over, the cap drops to A for at
  least max(200 ms, smoothed_rtt_p) (a probe), and then until the bytes in
  flight when it began are all acknowledged (a queue that takes longer to
  drain) and a byte sent after they were has been measured (or nothing is
  in flight), so that the pipe's own queue drains and the base is measured
  again; a probe that ended at max(200 ms, smoothed_rtt_p) because the old
  queue had drained just before, with no such byte measured yet, would
  have measured nothing. The probe starts when no sample has been within
  B + A / R_p + RTT_base_p / 8 of RTT_base_p for 10 s, or when no sample has been within
  2 x (RTT_base_p + B) + A / R_p, more than the in-flight cap can queue,
  for max(1 s, 8 x RTT_base_p). After a probe RTT_base_p is the minimum of
  the samples of bytes sent after the old queue drained (the previous base
  if there is none): the samples of the bytes in flight when the probe
  began carry the queue it was there to drain. At 10 Gbit/s a refresh
  holds the pipe for about 0.23 s at 100 ms (290 MB) and 0.43 s at 200 ms
  (530 MB); with the gain cycle above, a steady pipe needs none. The
  relative term of the first rule keeps a path whose round
  trips jitter by more than B + A / R_p (on most wide-area paths: at
  10 Gbit/s A / R_p is 65 us) from probing every 10 s, each probe holding
  the pipe at A for at least 200 ms; a base that rose by less than an
  eighth needs no probe, because the cap's gain of 2 covers it. A base that rose (a longer
  route, a competing flow) is thus learnt within about a second when it
  rose beyond what the cap explains, and within 10 s otherwise; a cap
  sized on the old base would keep the rate samples, and with them R_p,
  below the path's rate, and the pipe would spiral down. In normal
  operation the cap keeps the samples below that bound and the second rule
  does not fire.
- **Rate bound.** A sample above what the cap can queue has two causes:
  the path's delay rose, or its rate fell and R_p, a maximum over 10 round
  trips that the queue has made long, still holds the old rate, so the cap
  keeps about a second of queue (100 to 20 Mbit/s on a 100 ms path). The probe tells them
  apart. When it starts, the sender notes r_q, the largest bottleneck rate
  sample (Section 2.3) of the last max(500 ms, 4 x RTT_base_p), which while
  the queue stood measured what the path delivers. When the probe ends and
  a byte sent after the old queue drained came back within
  RTT_base_p + B + A / r_q of the old base (A / r_q: what the probe's own
  A in flight may have queued at that rate), the base did not rise: the
  queue was the sender's, the path is slower, and if r_q < R_p the sender
  sets R_lo = r_q and keeps the old base. Otherwise the base rose and R_lo
  is untouched. Afterwards every round trip (Section 2.3) that ends without
  a standing queue (S_p <= B + A / R_p) raises R_lo to the largest
  bottleneck sample it took, so a recovered path is followed at the pace
  of the probing gain of 1.25, and R_lo is dropped once the maximum of
  Section 2.3 has come down to it, or 10 s after a probe set it unless a
  later probe sets it again (so a pipe the scheduler then starves cannot
  keep a stale bound). The probe's extension lasts as long as the old
  queue takes to drain at the path's new rate, which that queue costs
  anyway. A delay rise is never taken for a rate drop: its samples sit
  above the old base by more than A / r_q, and on short paths, whose round
  trips stay short, the maximum follows by itself.

A pipe held back by the limiter is not writable for the scheduler and is
the bottleneck for rate samples (Section 2.3), but it has no write stall
(MULTIPATH.md Section 7.1); control frames are never held back. Such a pipe
does not ask its driver to write while it has nothing but held-back bytes
(control frames aside): the time it is next allowed to send is a timer
deadline of the session, so that a held pipe neither spins nor wakes its
writer for nothing. A carrier
that has a low-water mark or reports its unsent bytes is bounded by that,
and the limiter does not apply to it (API.md Section 4.1 lists the
capabilities an adapter declares).

A carrier with a congestion controller and a send buffer of its own (a
QUIC stack) keeps what the limiter gives it until its window lets it out;
such a window typically grows far beyond what the limiter keeps in flight,
so the limiter, not the stack, sets the rate and the queue. An
adapter SHOULD NOT bound the stack's send window (bytes written to it and
not yet acknowledged by the carrier) below the limiter's in-flight cap:
such a bound is a cap with a gain below 2, which costs latency and rate
when the path changes. Over such a carrier a pipe on one stream without
lanes is the limiter's open case on a long fast path with loss: some byte
is always held behind a lost packet, and the sender cannot tell that from a
queue. A QUIC adapter therefore offers lanes
(API.md Section 4.1).

Without pacing, the in-flight cap alone queues up to a round trip on long
paths. The cap uses RTT_base_p, not the 300 s min_rtt of Section 2.1, which
would starve a pipe whose delay rose, and a gain below 2 loses rate on such
a pipe. The limiter does not apply to carriers that have a low-water mark,
where it would cost a little throughput.

A session without multipath acknowledges its single pipe when both
endpoints are zmux endpoints (pipe acknowledgement, MULTIPATH.md Section
3.5), and the limiter applies to that pipe exactly as above; nothing is
retained, the acknowledgements serve these inputs only. Without them the
sender has no usable signal: QX_PING may be sent only 16 times a second
(SPEC.md Section 9.4.3), credit updates come in steps of a quarter of a
window or 256 KiB, too coarse to drive a limiter. With pipe
acknowledgement the single pipes of CONFORMANCE.md Q-1s to Q-7s meet the
multipath targets; without it only the receive window of SPEC.md Section
8.3 rule 6 bounds the queue. A plain QMux peer, or a peer that does not send
`zmux_pipe_ack`, leaves the sender with the receive-window bound alone.

### 9.2 Lanes

A pipe with lanes (MULTIPATH.md Section 2.5) is one pipe for this
document: one set of estimates, taken at its cumulative acknowledgement
point, one Q_p (the unsent bytes of all its lanes), one place in its tier,
one probation and one limiter. Lanes change only where the pipe's frames go,
and the scheduler's choice of pipe never sees them:

1. **Lane 0** carries the control frames, PIPE_PING and the stream
   directions whose redundancy is on (Section 4.1; urgency 0 to 2 by
   default; in a session without multipath, which sends nothing twice,
   the stream directions of urgency 0 to 2).
2. **Data lanes.** Every other stream direction is bound, when it next has
   a chunk for the pipe, to the confirmed data lane (MULTIPATH.md Section
   5.8) with the fewest bound stream directions, then the fewest unacknowledged bytes, and stays bound until
   none of its data on the pipe is unacknowledged; it then binds again at
   its next chunk ("unacknowledged" meaning not yet covered by a PIPE_ACK,
   also in a session without multipath). When every data lane has a bound stream direction and
   the peer's L allows, the writer opens a lane, as the carrier's stream
   credit permits (MULTIPATH.md Section 5.8), which takes stream directions once it
   is confirmed, so a pipe has about as many lanes as stream directions
   sending on it at once; meanwhile they share the lanes it has. Binding
   is what isolates them: a lost packet then holds back only the stream
   direction whose lane it was on. In a session without multipath the
   binding is also the order rule of MULTIPATH.md Section 5.8, which allows
   a stream direction to move to another lane only once its frames on the
   old one are acknowledged; the rule above never moves it earlier.
3. **Copy lane.** In a multipath session (the only one that sends copies),
   in a tier whose only usable pipe has lanes, the pair of Section 4.1 is
   lane 0 and the copy lane, the lowest-numbered lane other
   than lane 0 that has no bound stream direction, opened for the purpose
   when there is none; data stream directions do not bind to it while it
   serves as the copy lane.
4. **Writing.** New stream data is committed to the pipe while the pipe as a
   whole has room (Section 9: Q_p, the unsent bytes of all its lanes,
   below the low-water mark; on a pipe the limiter of Section 9.1 paces,
   within its allowance), unchanged. That room goes to the stream
   directions in the order of stream scheduling (IMPLEMENTATION.md Section
   5), whatever lanes they are bound to: lanes add no order of their own.
   An implementation that fills the writer of each lane separately keeps
   that order fair across lanes: it MUST offer the room to lane 0 first,
   then to the copy lane, then to the data lanes in turns, each round
   starting with the data lane after the one that last took a batch.
   When the limiter or the carrier connection lets only a batch or two
   through a round, a fixed order gives them to the first lanes every
   time, and a stream direction bound to a later lane waits until those
   have nothing left to send (IMPLEMENTATION.md Section 13.8 N13). Lane 0 and the copy lane also take
   control frames and redundant data while Q_p is at or above the mark,
   as long as their own unsent bytes are below it, because each lane is a
   stream of its own in the carrier and does not wait behind the others'
   queue. The exception needs lanes whose unsent bytes the adapter reports
   against a low-water mark (Section 9), and it is to that room only: on a
   pipe the limiter paces (lanes without an unsent count, API.md Section
   4.1) the limiter governs the pipe as a whole, lane 0 and the copy lane
   included, and holds back their redundant data as any data, while
   control frames pass it on every lane (Section 9.1). An adapter that can prioritise the
   streams of its connection gives lane 0 the highest priority; one that
   cannot, and sends round robin among streams with data, still gives lane
   0 a packet in every round.
5. **Estimates and detection** use the cumulative acknowledgement point
   (MULTIPATH.md Section 2.5); the positions of the lanes only acknowledge
   frames, and tell the limiter which bytes are held (Section 9.1).

The lanes of a pipe are opened only on its own carrier connection, so they
share its congestion controller: they change what waits behind a lost
packet, not the rate, and a deployment whose paths are all QUIC is better
served by running the API over QUIC itself (API.md Section 8) than by
lanes. Stream directions are bound to lanes, rather than hashed onto them
or spread record by record, because binding is what isolates a stream
direction from a loss on another lane. An adapter whose stack exposes the
unsent bytes of each stream reports them (API.md Section 4.1); one whose
stack does not need not turn lanes off.

---

## 10. Tiers

Every outbound pipe has an effective priority, a number in which lower
values are preferred, and may be standby (MULTIPATH.md Section 9). zmux has
no notion of the kind of path a pipe runs over: priorities are set by the
application and express its preferences, typically cost, which the
scheduler cannot measure. A pipe of a less-preferred tier carries next to
nothing while the more-preferred tiers can absorb the demand within the
overflow delay D, even when it has the lower delay or the higher capacity,
and takes only what they cannot absorb. Tier gating decides which pipes may
take new stream data; the policy then chooses among them. Priorities can be
changed at any time, locally for the outbound direction and by PIPE_STATUS
request for the peer's (MULTIPATH.md Section 9); the definitions below are
per direction and are evaluated at every scheduling decision (Section 9)
with the values in force at that moment.

Example configurations for a session with direct (hole-punched) pipes and a
pipe through a relay server; each is just a choice of priorities:

| Configuration | Direct pipes | Relay pipe | Effect |
|---|---|---|---|
| relay as fallback (the relay is paid for) | priority 0 | priority 10 | the relay carries data only while the direct tier is saturated (overflow: what it cannot send within D) or has failed (failover) |
| relay as an equal peer (cost does not matter) | priority 0 | priority 0 | all pipes form one tier; the policy spreads over them by its own criteria (for example `spread-round-robin` with weights, Section 6) |
| relay preferred (a fast, free relay; direct paths flaky) | priority 10 | priority 0 | the direct pipes carry data only while the relay is saturated or has failed |
| relay for failover only | priority 0 | standby | the relay never takes overflow, only failover |

Priorities need not be consecutive and have no upper limit: a deployment can
leave room between tiers (0, 10, 20) to insert new ones later.

- **usable**: ACTIVE (a JOINING pipe is not usable until its join is
  confirmed, MULTIPATH.md Section 5.7 step 4), not penalized (MULTIPATH.md
  Section 7.5.1), not in probation (Section 11) and not awaiting
  revalidation (Section 11.1). A pipe in probation or awaiting
  revalidation counts as usable only when no pipe in neither state,
  standby pipes included, is usable in its direction: a standby pipe that
  has taken over keeps the traffic until the returning pipe's probation
  ends, and when every candidate is in one of the two states, all of them
  count.
- **tiers**: the usable non-standby pipes of the direction grouped by
  effective priority, from the most preferred (the lowest value) on; the
  distinct values present are compared, and implementations sort them
  rather than index an array by priority. Under `session-affinity` a tier
  consists of its active pipe only (Section 8): the other pipes of the tier
  carry nothing and count neither in Q_t nor in R_t.
- **offered data**: for the most-preferred tier, X_1 is all data ready to be
  scheduled: stream data that the application has written, that is within
  the stream's and the session's send credit and not yet committed to a
  pipe, plus reinjected information (MULTIPATH.md Section 7.4) not yet
  committed. Each following tier is offered what the tier before it cannot
  absorb within D:

      X_(t+1) = max(0, X_t - max(0, R_t x D - Q_t))

  where Q_t is the sum of Q_p (bytes committed to p and not yet transmitted,
  Section 4) and R_t the sum of R_p over the pipes of tier t.
- **age**: how long the oldest data waiting for tier t has waited. Data
  waits from the moment the application asked to send it: bytes written
  without waiting, from their write; the bytes of a write that had to wait
  for send-buffer space, whether already buffered or not, from the moment
  the stream's writes began waiting, where a write that begins waiting
  within 1 ms of the stream's previous write completing continues the same
  wait (a writer that keeps its buffer full, write after write, has waited
  since its first write waited); data that waited for send credit, from
  when the credit arrived (data that waited for both counts from the later
  of the two starts); reinjected information, from when it was queued for
  reinjection. For a tier other than the most-preferred one, the age counts
  from no earlier than the moment the tier last became eligible.
- **saturated**: tier t is saturated when either holds:
  - **drain**: its backlog would take longer than D to send, Q_t + X_t >
    R_t x D;
  - **age**: its age is D or more.

  The drain test overflows a burst as soon as it arrives, by what exceeds
  R_t x D, but it sees only what the send buffer holds and depends on the
  rate estimates. The age test is a measurement and needs no estimate. It
  catches a writer whose demand exceeds the send buffer: the session send
  buffer, at most the retransmit budget, is usually smaller than R_t x D,
  so X_t never reaches it. It also catches a rate estimate that is still
  too high after a pipe slowed down. The age test alone would start a
  sustained overflow about D later than the drain test. Buffered bytes
  count from the write, not from their entry into the buffer, so that a
  burst larger than the buffer is seen; and the age is not a busy-period
  test ("X_t > 0 continuously for D"), which would fire whenever several
  flows keep a tier busy for D although no byte waits that long. D is the overflow delay, one value
  per session for both directions: the scheduler parameter
  `overflow_delay`, default 1 s (API.md Section 6), so data waits about D
  at most for the preferred tier before the next tier helps. A pipe that is
  not writable is not saturated by that: it takes nothing until its carrier
  accepts bytes again, and its tier overflows only by the tests above.
- **eligible tiers**: the most-preferred tier, and each following tier while
  every tier before it is saturated. The **eligible pipes** are the pipes
  of the eligible tiers. When every tier is saturated, every tier is
  eligible: each pipe takes data as it drains.
- **failover**: when no non-standby pipe is usable at all (every one is
  JOINING, SUSPECT, DRAINING, FAILED or penalized), the standby pipes form
  the tiers in the same way. Standby pipes never take data for overflow:
  when the non-standby pipes are usable but saturated, data waits for them.

Rules:

- T1. New stream data, reinjected information (MULTIPATH.md Section 7.4),
  opportunistic copies (MULTIPATH.md Section 7.5.1) and redundant copies
  (Section 4.1) go only to eligible pipes, apart from the stream data that a
  JOINING pipe may carry while no outbound pipe is usable (MULTIPATH.md
  Section 11.1 rule S1). A pipe of a
  less-preferred tier therefore takes a chunk only while every
  more-preferred tier is saturated (**overflow**) or has no usable pipe
  (**failover**). The decision is made per chunk, so a less-preferred tier
  takes the part of a burst that the preferred tiers cannot send within D,
  in whole chunks, and traffic returns to the preferred tier as soon as its
  backlog fits again.
- T2. The policy runs within each eligible tier separately (Sections 5 to 8):
  at a write opportunity of pipe p it chooses among the pipes of p's tier.
  Within a tier `spread-round-robin` shares the load by the configured
  weights, `spread-rate-weighted` by weight times measured rate and
  `spread-eta` by arrival time; a pipe of an overflow tier is not compared
  with the pipes of a more-preferred tier, since the tiers have already
  decided that it takes the overflow.
- T3. Stream urgency does not bypass tiers. An application that wants urgent
  streams on the lowest-delay pipe whatever its tier uses a custom policy:
  it receives each pipe's effective priority, standby flag and probation
  state, whether its tier is saturated, and which tiers are eligible (API.md
  Section 5.2), and may override the gating.
- T4. Control frames follow MULTIPATH.md Section 6.5: ACTIVE pipes first,
  then the most-preferred tier, then the lowest one-way delay. PIPE_PING
  probes go to every pipe, standby pipes and less-preferred tiers included
  (those every `standby_probe`, 1 s, while the session is busy, so that a
  backup that died silently is known before the failover needs it, and in
  a quiet session at the probe interval plus once when traffic resumes,
  MULTIPATH.md Section 7.1), and the
  PIPE_ACK answering a probe also returns on the probed pipe pair
  (MULTIPATH.md Section 6.5), and the estimators of Section 2 run for every pipe, so that overflow and
  failover decisions use current estimates.
- T5. DATAGRAM frames go to the eligible pipe with the lowest estimated
  arrival time (MULTIPATH.md Section 11.1 rule S7).

---

## 11. Probation

Every pipe that joins (every pipe but the bootstrap pair), and every pipe
that returns from SUSPECT to ACTIVE, is **in probation** until it has been
ACTIVE, without becoming suspect, for

    probation_p = min(base_p x 2^k_p, max(base_p, 2 x U_max))
    base_p      = max(1 s, 4 x smoothed_rtt_p)   for a path with history
                = 4 x smoothed_rtt_p             for a first join without history

where base_p is evaluated with the current smoothed_rtt_p, `Probation` (a
per-pipe option, API.md Section 4.2; 0 disables probation) replaces it, and
U_max is the uptime cap below (without an observed uptime the cap does not
apply). A
path has **history** once the pipe, or for a non-zero fate group any pipe of
its group in that direction, has been suspect or has failed (an attachment
abandoned with ZMUX_PIPE_RESOURCE_LIMIT does not count: it did not fail
with its path, MULTIPATH.md Section 7.1); a pipe that
returns from SUSPECT always has. Only a pipe that joins a path that has
never had trouble skips the 1 s floor: a stable new path then carries data
after a few round trips, while any path that has failed once is damped by
the floor and the backoff below. A new path acquires history at its first
outage. k_p starts at 0. When p becomes suspect while in probation, or within
10 x max(1 s, 4 x smoothed_rtt_p) after its last probation ended, k_p
becomes min(k_p + 1, 6); when p has stayed out of probation that long
without becoming suspect, k_p returns to 0. These backoff windows always
use the 1 s floor (or the `Probation` value when it is set), also for a pipe
whose first probation had none: the first outage of a new path is
then damped like any other. A pipe that becomes suspect while in probation
starts a new probation, with k_p raised, when it returns.

**Uptime cap.** U_max is the longest uptime the history has seen: the
longest ACTIVE period, of the pipe or, for a non-zero fate group, of any
pipe of its group in that direction, that began once the path had history
(with a return from SUSPECT, or with the join of a pipe to a path with
history) and ended when that pipe became suspect or failed. It is part of
the history and is cleared with it: when the path has stayed out of
probation for the backoff window without becoming suspect, k_p returns to 0
and U_max is forgotten. The backoff exists to outlast the up periods of a
flapping path, and it need not outlast them by more than a factor of two:
while the path flaps, each up period is about U_max at most, so a probation
of 2 x U_max still outlasts it and the pipe takes no data, and once the
flapping stops the pipe returns after about 2 x U_max rather than up to
64 x base_p. The ACTIVE period before a path's first trouble does
not count: it shows how long the path worked before it ever failed, not
how long it stays up once it flaps. An up period that ever exceeds twice the
longest before it ends a probation early, at the cost of one more return,
which the next suspicion then answers with a longer cap.

The probation history (k_p, U_max and the end of the last probation) is kept per
joiner, non-zero fate group (MULTIPATH.md Section 2.2: groups are scoped to
their joiner) and direction, and per pipe
for pipes of fate group 0. A pipe that joins a non-zero fate group starts
from the group's history, and a suspicion or failure of any pipe of the
group, other than a pipe of an attachment whose graceful removal has
started or that was abandoned with ZMUX_PIPE_RESOURCE_LIMIT (MULTIPATH.md
Section 7.1), counts for the rules above as one of its
own: a failure during the group's probation, or within the backoff window after it
ended, raises k. A path whose outages outlast T_fail, so that every comeback
is a new join, is thereby damped like one that only becomes suspect.
Applications that re-establish the same path, for example by punching it
again, give the new pipe the old one's fate group, and join it from the
endpoint that joined the old one.

While in probation a pipe carries control frames and probes, but takes new
stream data, reinjection and opportunistic copies only when no pipe out of
probation, standby pipes included, is usable in its direction (a session
whose other pipes have all failed, or a resumed session, uses its new pipes
at once). The sender probes a pipe in probation with a PIPE_PING at least
once every max(smoothed_rtt_p, 200 ms); the PIPE_PING limit of MULTIPATH.md
Section 6.4 takes precedence, so with many pipes in probation the probes are
spaced further apart. The probes are what can make the pipe suspect during
probation, and they keep its estimates current for when probation ends.

Probation keeps a direct path that has just been punched, or has just come
back, from taking traffic it may lose again; the backoff makes a path that
keeps flapping stay unused (after a few cycles probation outlasts its up
periods) until it has been stable for a while: at most 64 x base_p, and
at most twice its longest up period once it has shown one. Probation
applies to every return, however short the suspicion: otherwise a pipe
whose outages are each shorter than T_fail would skip probation after each
of them. A suspicion that turns out to be false costs the pipe at least 1 s
of new stream data.

### 11.1 Revalidation

An application that learns of a network change asks for revalidation
(API.md Section 4.2, `RevalidatePipes` and `Revalidate`). Each named live
outbound pipe that is not already awaiting revalidation is then **awaiting
revalidation**, and the sender sends a PIPE_PING on it at once, or, when the
PIPE_PING limit of MULTIPATH.md Section 6.4 defers it, as soon as the limit
allows (longest-waiting first); the pipe awaits revalidation from the
request on, not from the PIPE_PING. It stops awaiting revalidation when its
acknowledged position reaches that PIPE_PING, so an acknowledgement of
bytes sent before the request does not end it; and when it becomes
suspect, fails, starts draining or is removed. While it awaits
revalidation it is usable as Section 10 defines it only when no pipe in
neither probation nor revalidation is usable in its direction, so new
stream data, reinjection and copies go to the other pipes, and nothing
already committed to it is taken back. Revalidation is not a suspicion and
touches no other state: the pipe stays ACTIVE, nothing is reinjected, it
enters no probation, k_p and the history of Section 11 do not change, and a
pipe in probation stays in its probation. An unanswered PIPE_PING is an
unacknowledged ack-eliciting byte, so a pipe that vanished becomes suspect
after T_suspect by the ordinary rule of MULTIPATH.md Section 7.1, and the
returns that follow are handled as usual. A pipe that answers has cost new
data at most one round trip; one that is gone has stopped taking new data
at once instead of after T_suspect, or for an idle pipe after its next
probe. Every pipe then carries an unacknowledged PIPE_PING, and the pipe
of a bidirectional attachment is also answered on its own pipe pair
(MULTIPATH.md Section 6.5), so a failure of the pipes that carry the
acknowledgements right after a revalidation does not make it suspect.

---

## 12. Policy changes

The policy of a session and of each stream direction can be changed at any
time (API.md Section 5.2). A change takes effect at the next scheduling
decision: frames already committed to a pipe, and data already sent, are not
moved; chunks scheduled after the change use the new policy. State of the
previous policy is discarded (affinity bindings, round-robin current
values); a policy switched to starts from its initial state, so a switch to
`affinity` binds each stream direction at its next chunk. Weights and
priorities can likewise be changed at any time and are used from the next
decision on.

---

## 13. Budgets

### 13.1 Retransmit budget

A sender retains the information of every frame that has a reinjection rule
in MULTIPATH.md Section 7.4 until it is acknowledged; DATAGRAM, QX_PING and
the other frames that are never reinjected are not retained and do not
count. Its retained stream data (the bytes of unacknowledged STREAM frames,
each byte counted once, including preamble bytes) is bounded by the peer's
flow-control limits, and additionally by the retransmit budget:

    retransmit_budget = clamp(4 x sum over live pipes of R_p x (smoothed_rtt_p + peer zmux_max_ack_delay),
                              1 MiB, send_buffer_max)

where send_buffer_max is the maximum of the session send buffer, a memory
bound (the `send_buffer` parameter `max`: by default 64 MiB and, above
that, what the memory budget grants, which retained data is charged to; a
configured value is fixed; never below `send_buffer`, API.md Section 6). There is no fixed upper limit: the retained data can never
exceed the peer's session credit anyway, and a cap bounds a multipath
session to that much data per round trip whatever the pipes and windows.
The memory is taken only as
the budget asks for it, and counts against the listener's memory budget.

R_p is the windowed-maximum delivery rate of Section 2.3, which idle and
application-limited periods never lower. The 1 MiB floor always applies,
not only before the first estimate: the budget is a memory policy and must
never throttle a pipe below what one acknowledgement loop needs. The sender
sends new stream data while its retained stream data is below the budget,
cutting the last STREAM frame so that the total reaches the budget exactly,
and then schedules no new stream data (backpressure to the application)
until acknowledgements arrive; control frames and reinjection are not
blocked, and neither is a FIN-only STREAM frame, which adds no bytes (its
FIN is retained for reinjection like any frame's information; SPEC.md
Section 8.2). It never discards retained information to make room. Examples:

| Situation | R x (srtt + max_ack_delay) | Budget |
|---|---|---|
| first round trip, no estimate (R = 1 MB/s, srtt 333 ms) | 1 MB/s x 0.358 s = 358 KB, x 4 = 1.4 MB | 1.4 MB |
| after an idle minute on a 100 Mbit/s, 10 ms pipe (R kept at 12.5 MB/s) | 12.5 MB/s x 35 ms = 437 KB, x 4 = 1.75 MB | 1.75 MB |
| 0.5 ms LAN at 10 Mbit/s | 1.25 MB/s x 25.5 ms = 32 KB, x 4 = 128 KB | 1 MiB (floor) |
| 0.5 ms LAN at 100 Mbit/s | 12.5 MB/s x 25.5 ms = 319 KB, x 4 = 1.28 MB | 1.28 MB |
| 0.5 ms LAN at 10 Gbit/s | 1.25 GB/s x 25.5 ms = 31.9 MB, x 4 = 127 MB | 127 MB, within the memory budget (a bound: only what is in flight is retained) |
| 10 Gbit/s x 100 ms | 1.25 GB/s x 125 ms = 156 MB, x 4 = 625 MB | 625 MB, within the memory budget |
| 2 x 1 Gbit/s, 50 and 100 ms one way, acknowledgements over the faster pipe (RTT 100 and 150 ms) | 125 MB/s x 0.125 s + 125 MB/s x 0.175 s = 37.5 MB, x 4 = 150 MB | 150 MB, within the memory budget |
| 100 Mbit/s with 10 ms RTT + 20 Mbit/s with 55 ms RTT, acknowledgements returning on the fast pipe | 12.5 x 0.035 + 2.5 x 0.080 = 637.5 KB, x 4 = 2.55 MB | 2.55 MB |

The max_ack_delay term matters on fast, short pipes: without it the budget
would be smaller than what the peer may legitimately hold unacknowledged
until its acknowledgement timer fires, and the rate would collapse. The term
is not bounded by the peer's acknowledgement threshold, because the budget
also sizes reinjection.

### 13.2 Reorder budget

The reorder budget bounds how much out-of-order data a spreading sender may
cause at the receiver:

    reorder_budget = min(peer's currently available session credit,
                         2 x sum over live pipes of R_p x (OWD_max - OWD_min) + spread_chunk)

where OWD_max and OWD_min are the largest and smallest estimated one-way
delays of the usable pipes. It scales with the rate and the delay spread that
spreading has to absorb, so it never blocks aggregation by itself; a fixed
cap would. Flow control remains the bound. It is a sender-side admission
input of `spread-eta` (Section 5). Receivers need no separate
reorder limit: reassembly memory is bounded by flow control (SPEC.md Section
9.8).

### 13.3 Memory

Per outbound pipe, retained data is about

    M_retx,p = U_p + R_p x (OWD_p + OWD_ack + D_ack)

where U_p is committed-but-unsent bytes, OWD_ack the return delay and D_ack
the acknowledgement delay. The session total (retained, reorder, unread and
send-queued bytes) is bounded by the receive buffer cap, the send buffer and
the retransmit budget, and across sessions by the listener's memory budget
(IMPLEMENTATION.md Section 6).

---

## 14. Defaults

| Item | Default | Reference |
|---|---|---|
| initial SRTT for a pipe | 333 ms (rttvar 166.5 ms) until its own first sample | 2.1 |
| min_rtt | minimum of the RTT samples of the last 300 s, kept when there is none; samples taken while the session is quiet do not enter it once it has a value; also the per-pipe RTT input of auto-tuning | 2.1 |
| one-way delay estimate | min_rtt_p - min over live pipes of min_rtt / 2; smoothed_rtt_p / 2 before the first sample | 2.2 |
| initial delivery rate | 1 MB/s until the first sample; then the maximum of delivery-rate samples over the last 10 round trips within 10 s, kept across idle periods, raised but never lowered by application-limited samples (including ranges sent while an application-limited bubble was in flight) | 2.3 |
| scheduling policy | affinity; spreading policy spread-eta | 3 |
| spread_chunk | 16 KiB | 5 |
| ECF hysteresis beta | 0.25 (0 for urgency 0 to 2) | 5 |
| ECF backlog and BLEST credit | B = the more urgent levels' ready data + the sum over the level of min(ready_j, r), within the session credit, less the chunk (urgency 0 to 2: the more urgent levels only); F = R_f x (ETA_p - OWD_f) <= min(session credit, reorder budget) and F / n <= the stream's credit, n the incremental stream directions of the level with data ready | 5 |
| pipe weight | 1 (any value 0 to 2^64 - 1; local) | 6 |
| carrier low-water mark (TCP_NOTSENT_LOWAT where the carrier has one) | max(32 KiB, R_p x RTT_min_p / 8) per pipe | 9 |
| carrier queue limiter (carriers with neither a low-water mark nor an unsent count; multipath sessions and single pipes with pipe acknowledgement) | pacing at gain x R_p (2.885 start-up, 1 / 2.885 drain, then 1.25, 0.75, 6 x 1 per base RTT, the 0.75 phase until in flight <= R_p x RTT_base_p + A, at most 4 phases) in quanta of max(2 x spread_chunk, gain x R_p x G), G the timer granularity (at most 1 ms), the allowance accruing with elapsed time up to two quanta; in flight counted to the last ack-eliciting byte; in flight < 2 x R_p x (RTT_base_p + B) + min(ack threshold, R_p x max_ack_delay) + spread_chunk; drain until S_p <= B or in flight <= max(R_p x RTT_base_p, A); start-up ends after three round trips with a bottleneck sample and no 25 % growth; RTT_base_p the smallest sample at any time, set by a probe to what it measured (BBR min_rtt); probe after 10 s without a sample within B + A / R_p + RTT_base_p / 8 of the base or max(1 s, 8 x RTT_base_p) without one the cap explains, ending only once a byte sent after the old queue drained is measured; rate bound R_lo set by a probe that finds the base unchanged | 9.1 |
| redundant urgent data | on for urgency 0 to 2 (scheduler parameter `redundancy`, per-stream setting); pair = the two pipes of the eligible tier with the smallest ETA, or lane 0 and the copy lane of a single-pipe tier's pipe with lanes; the first chunk after max(1 s, T_suspect) without acknowledgement progress also copied within its tier; one copy per chunk; copies outstanding at most max(spread_chunk, retransmit budget / 64) | 4.1 |
| lanes | one pipe for the scheduler; lane 0 for control, PIPE_PING and redundant stream directions; other stream directions bound to the data lane with the fewest bound, then the fewest unacknowledged bytes, until their data on the pipe is acknowledged; a new lane when every data lane is bound, up to the peer's `zmux_max_lanes`; in a single-pipe tier the copy lane pairs with lane 0 for redundant urgent data | 9.2, 4.1 |
| overflow delay D | 1 s, one value per session; scheduler parameter `overflow_delay` | 10 |
| backup-pipe probe interval | `standby_probe`, 1 s: standby pipes and pipes of a less-preferred tier than the most-preferred usable one, while the session is busy (a traffic frame, QX_PING excluded, within the 15 s busy window); in a quiet session the probe interval, and one PIPE_PING on each when traffic resumes (MULTIPATH.md Section 7.1) | MULTIPATH 7.1 |
| probation | min(base x 2^k, max(base, 2 x U_max)), base = max(1 s, 4 x smoothed_rtt_p) (4 x smoothed_rtt_p for the first join of a path without history), k up to 6, U_max the longest up period since the path's history began; for every join and every return from SUSPECT; per-pipe option `Probation` replaces base (0 disables) | 11 |
| retransmit budget | clamp(4 x sum R_p x (SRTT_p + max_ack_delay), 1 MiB, send_buffer_max), send_buffer_max 64 MiB and above that within the memory budget by default (a configured value is fixed) | 13.1 |
| reorder budget | min(peer session credit, 2 x sum R_p x (OWD_max - OWD_min) + spread_chunk) | 13.2 |

---

## 15. Verification

Every implementation drives its own scheduler and estimators, together with
the failure detection and reinjection of MULTIPATH.md Section 7, through the
scenarios of CONFORMANCE.md Section 4.5 before a release and meets each
target there. IMPLEMENTATION.md Sections 8 and 9.2 give implementation
notes.
