# zmux Conformance

Status: normative for any claim of zmux conformance. A claim names the
specification revision (the commit of this repository) it was tested
against.

---

## 1. Scope

There is one conformance class: a **zmux implementation** implements the
whole core protocol (SPEC.md, MULTIPATH.md, RESUME.md), the sender policy of
SCHEDULER.md and every negotiated feature of EXTENSIONS.md, apart from the
optional features of requirement C-14. Multipath and resume are core: an
implementation cannot claim conformance without them, even though sessions
use them only when configured (SPEC.md Section 2.4); the claim is about the
library, and every build of it contains them (IMPLEMENTATION.md Section
2.6). The helpers of API.md Appendix A are outside conformance. The four
zmux libraries (Go, Rust, Java, Python through the Rust core) make this
claim.

Third-party endpoints that implement only part of zmux do not claim
conformance; the protocol stays interoperable with them through the feature
negotiation of SPEC.md Section 5.5 and the plain-QMux invariant of SPEC.md
Section 13.

---

## 2. Implementation requirements

| # | Requirement | Reference |
|---|---|---|
| C-1 | Implement every MUST of SPEC.md, MULTIPATH.md, RESUME.md and EXTENSIONS.md. | all |
| C-2 | Pass every trace in `fixtures/traces/` with a trace runner shipped in the implementation's repository (Section 3). | TRACES.md |
| C-3 | Accept every entry of `fixtures/wire/valid.ndjson` and reject every entry of `fixtures/wire/invalid.ndjson` with the listed error (Section 5). | |
| C-4 | Enforce the no-effect budget with exactly the parameters of SPEC.md Section 9.4.2 (more lenient values are allowed by the protocol, but conforming libraries use the reference values so that traces test the boundary). | SPEC 9.4 |
| C-5 | Use the failure-detection formulas and defaults of MULTIPATH.md Section 7.1 (including the write-call bound and the write-stall measurement), the inbound silence limit of MULTIPATH.md Section 7.3, the acknowledgement rules of MULTIPATH.md Sections 6.3 and 6.5 (including the SHOULDs of 6.5: ACTIVE primary and redundant acknowledgement pipes, credit-frame pipes, return-pipe liveness), the PIPE_PING in the join record (MULTIPATH.md Section 5.2) and in the first record of an accepted down or bidi join (MULTIPATH.md Section 5.4 step 8), the refusal backoff of MULTIPATH.md Section 5.7 step 1, reinjection pacing after a rejoin (RESUME.md Section 6), parallel rejoining of split carriers where the deployment is known to split them (RESUME.md Section 4.3), tier gating and saturation (SCHEDULER.md Section 10), probation with its uptime cap and revalidation (SCHEDULER.md Sections 11 and 11.1), redundant urgent data and the copy of the first chunk after an idle period (SCHEDULER.md Section 4.1), the carrier queue limiter for carriers without a low-water mark or unsent count (SCHEDULER.md Section 9.1), the PIPE_STATUS requests of MULTIPATH.md Section 9 (precedence of local settings over the peer's request, the initial request in PIPE_JOIN and the deferral of changes made while JOINING, the announcement when standby pipes take over and its withdrawal when a non-standby pipe is usable again), the estimators of SCHEDULER.md Section 2 (RTT, one-way delay, delivery rate), the budgets of SCHEDULER.md Section 13, the credit rules and receiver policy of SPEC.md Section 8.3, the QX_PING in the first record of an endpoint without a carrier minimum RTT (SPEC.md Section 8.3 rule 6; trace runners report one, TRACES.md Section 3.3, so traces show none), the BLOCKED frame for a preamble that does not fit in the remaining credit (SPEC.md Section 8.4), staged unidirectional credit (SPEC.md Section 8.5 rule 4) and the linger timeout of SPEC.md Section 6.5 unless the application overrides them. | |
| C-6 | Schedule at transmission time (late binding, MULTIPATH.md Section 11.1 rule S10): frames are bound to a pipe only when that pipe is being written. Traces rely on it. | |
| C-7 | Provide the test hooks of TRACES.md Section 3.3, unreachable from production configuration. | TRACES 3.3 |
| C-8 | Implement the built-in scheduling policies `affinity`, `session-affinity`, `spread-eta`, `spread-round-robin` and `spread-rate-weighted` (one round-robin engine, SCHEDULER.md Section 6) and a custom-policy hook, selectable and changeable at any time per session and per stream direction, tier gating for all of them, and pass the required scheduler tests of Section 4.5. | SCHEDULER 3, 6, 10, 12 |
| C-9 | Never place a transport error code in a stream frame and never let an application choose one. | SPEC 10.1 |
| C-10 | Report session errors with the precedence of SPEC.md Section 6.5 and stream outcomes as STATE_MACHINE.md Section 2.5 lists. | |
| C-11 | Reset with code 0 when an unfinished sending part is dropped; never report a reset stream as complete. | EXTENSIONS 7.4; SPEC 7.5 |
| C-12 | Pass the stress and regression tests of Section 4 and the interoperability plan of Section 6 before a release. | |
| C-13 | Follow the concurrency rules of IMPLEMENTATION.md Section 2.5 and pass the tests of Section 4.7 before a release. | ARCHITECTURE 4 rule 8 |
| C-14 | Optional features: split bootstrap (SPEC.md Section 6.1.1) MAY be omitted. An implementation that offers it passes the traces whose identifiers start with `multipath.bootstrap.split_`; one that does not skips them and states so in its conformance claim. Streams as carriers (API.md Section 4.6, EXTENSIONS.md Section 10.2) MAY be omitted as well; an implementation that offers them follows EXTENSIONS.md Section 10.2 and passes test 10 of Section 4.7. Nothing on the wire depends on either choice. | SPEC 6.1.1, EXTENSIONS 10.2 |
| C-15 | Single-pipe sessions pay nothing for multipath (IMPLEMENTATION.md Section 2.6): no per-pipe or per-stream multipath state, and the measurements of Section 4.6 within their bounds. | IMPLEMENTATION 2.6 |

---

## 3. Executable traces

`fixtures/traces/index.json` lists every trace with its references. The format and the runner obligations are in
TRACES.md. Groups:

| Group | Covers |
|---|---|
| `core.handshake` | first frame, parameter rules, GREASE, identification of foreign peers, handshake timeout, early data (sending within the floor behind the PIPE_ACK fence, receiving, rejection by a peer that cannot take it) |
| `core.records` | record framing, frame-type rules, prohibited and unknown frames, incremental parsing |
| `core.streams` | opening, ordering, stream states, STOP_SENDING, resets, identifier allocation, release semantics |
| `core.flow` | stream and session credit, zero-window liveness, session accounting, stream-count credit after release, Rapid Reset |
| `core.robustness` | QX_PING rules, bounded response state, the no-effect budget and its compliant floor |
| `core.close` | close procedure, linger, error precedence, idle timeout, keep-alive |
| `core.reset_stream_at` | RESET_STREAM_AT rules |
| `core.pipe_ack` | pipe acknowledgement without multipath (MULTIPATH.md Section 3.5), and the idle timeout of a session with acknowledgement (SPEC.md Section 6.4) |
| `core.lanes` | lanes (MULTIPATH.md Sections 2.5, 3.6 and 5.8) in a session without multipath: PIPE_LANE, positions and PIPE_ACK entries per lane, head-of-line blocking on one lane, opening only after lane 0 is acknowledged, invalid lanes and entries, a lane's failure |
| `ext.*` | role negotiation, GOAWAY, preamble, PRIORITY_UPDATE, datagrams and flows |
| `compat.qmux` | behaviour towards a plain QMux peer |
| `multipath.*` | negotiation, joins (in band, out of band, replay, refusal backoff), spread reordering, acknowledgement, failover and reinjection, pipe removal, pipe status, close, idle, budget, lanes (acknowledgement per lane, graceful end of every lane, a lane's end failing its pipe) |
| `resume.*` | suspension, positions after a rejoin, fail-closed conditions, grace expiry, the attachment cap while suspended |
| `regression` | stream races and credit accounting: STOP_SENDING crossing a finish, credit not refunded on reset, a reset with code 0 |

The current counts are in `assets/manifest.json`.

---

## 4. Tests beyond traces

### 4.1 Fuzzing

- The record and frame decoder, the transport-parameter decoder and the
  preamble parser are fuzzed (coverage-guided, at least 10^8 executions per
  release) with the wire corpus as seed. A crash, a hang, unbounded memory or
  a decode whose re-encoding is not **semantically equal** is a failure.
  Inputs need not be minimally encoded: RFC 9000 Section 16 allows
  variable-length integers in more bytes than necessary (frame types
  excepted, RFC 9000 Section 12.4), and SPEC.md Section 13.2 requires
  accepting anything a correct QMux peer may send, so byte-for-byte equality
  is not required. For every input that decodes, encoding the decoded value
  with the implementation's own encoder:
  1. produces no more bytes than the input, counting every variable-length
     integer that the encoder writes in a reserved fixed width (a Size or
     Length filled in afterwards, as RFC 9000 allows) at its shortest
     length; an encoder that writes shortest forms throughout is compared
     byte for byte;
  2. decodes to a value equal to the first decode;
  3. is stable: decoding and encoding it again yields the same bytes.
- The session state machine is fuzzed with random sequences of peer records,
  API calls, pipe events and time steps; the invariants of Section 4.4 must
  hold.

### 4.2 Differential testing

The Go core and the Rust core (which Python shares) receive identical inputs
(trace steps generated at random) and must produce identical outputs: the same
frames in the same records on the same pipes, the same events and the same
errors. Implementation-chosen details (record boundaries, the order of
frames in a batch) are normalised before comparison.

### 4.3 Compliant-floor stress test

Two endpoints with default configuration, connected over one TCP pipe and,
separately, over two TCP pipes with `spread-eta`:

1. run at least 3000 bidirectional streams in under 5 s, each doing open,
   write 20 KB, finish, and closing the receive side without reading the
   response (STOP_SENDING crossing the response FIN);
2. run the same with both sides cancelling half of the streams at random
   points;
3. in the two-pipe case, kill one pipe every 500 ms and re-add it.

No session may close. Then a flood of chargeable frames (for example
QX_PING requests sent while an earlier one is unanswered, PIPE_PING frames
in the two-pipe case, or, on the single pipe, MAX_DATA frames that do not
raise the limit) must still close the session with ZMUX_EXCESSIVE_LOAD.

In the two-pipe case, a peer then fragments on purpose: on as many streams
as the session window allows, it sends one byte at every other offset up
to each stream's window, in 1-byte STREAM frames alternating between the
pipes, once in records of those frames alone and once with each frame in
its own record padded to 64 KiB. No session may close, and the receiver's
memory for received stream data stays within 2.5 x the session's
flow-control consumption (SPEC.md Section 8.2) plus 4 KiB per stream plus
the read buffer each pipe or lane is being read into (SPEC.md Section 9.8; the bound
is the session's, IMPLEMENTATION.md Section 3). Filling the holes
afterwards delivers every stream complete.

### 4.4 Invariants

Checked after every step of fuzzing and simulation:

- delivered stream bytes are exactly the bytes written, in order, without
  duplication, for every stream that ended with FIN;
- a stream that was reset is never reported as complete;
- received data never exceeds advertised limits; session accounting equals
  the sum of max(offset, final size);
- retained (unacknowledged) bytes never exceed the peer's limits or the
  retransmit budget;
- pending response state stays within SPEC.md Section 9.2;
- no retained data exist in sessions without multipath, and no PIPE_ACK
  unless pipe acknowledgement is in use (MULTIPATH.md Section 3.5).

### 4.5 Required scheduler tests (multipath simulation)

Scheduling is not observable on the wire in a way traces can pin down: which
pipe gets a chunk depends on rates and queues that a trace runner does not
model. The scheduler is therefore tested with deterministic scenarios.

**Pipe model.** The targets below assume this model of the scenarios. Each
pipe is a FIFO carrier with a fixed rate and one-way delay, a send queue
with low-water-mark semantics (TCP_NOTSENT_LOWAT where a carrier has it:
32 KiB in multipath sessions, 128 KiB on a single pipe), optionally a buffer
the sender cannot see (a network bottleneck its own carrier or a competing
flow fills, or a user-space carrier that reports neither unsent bytes nor an
RTT; the sender's RTTs always come from PIPE_ACK samples), short stalls, and
a failure or slowdown. The application writes into stream send buffers that
share the session send buffer fairly (IMPLEMENTATION.md Section 6.4), and the
receiver acknowledges per MULTIPATH.md Sections 6.3 and 6.5, releases stream
credit on consumption and session credit on receipt (SPEC.md Section 8.3).
The sender sees only what the protocol gives it and implements, as written,
the estimators of SCHEDULER.md Section 2, failure detection (MULTIPATH.md
Section 7.1), opportunistic reinjection (MULTIPATH.md Section 7.5.1),
redundant urgent data (SCHEDULER.md Section 4.1), the carrier queue limiter
(SCHEDULER.md Section 9.1), all five policies with pipe weights and online
policy changes (SCHEDULER.md Sections 3 to 8 and 12), tier gating
(SCHEDULER.md Section 10), probation with its uptime cap and revalidation
(SCHEDULER.md Section 11) and the budgets of SCHEDULER.md Section 13. A
carrier delivers what it held the moment its path returns; the S-3s targets
are judged against this model, because a real carrier's retransmission
backoff after a stall sets a tail that no scheduler removes.

**Obligation.** Every implementation drives its own scheduler and estimators
(its sans-IO core, with acknowledgements, credit and failures fed by a harness
that reproduces the pipe model above) through these scenarios before a
release and must meet each target. Because the 50 ms acknowledgement
copies make the outcome depend on where in that interval a failure or
slowdown happens, S-8, S-10, T-3 and T-4 run at 12 evenly spaced phases (and
the other rows that name phases at the count they give) and are judged on
the worst phase. Scenarios with a random workload (F, S-3s, Q-4, T-8,
AF-flows, SA-T-8, T-6r and every other row that names seeds) run the seeds
and length their row gives (10 seeds of 60 s unless stated) and are judged
on the mean over the harness's own seeds; a release runs 30 seeds of the
rows with 10, never a chosen subset. Where such a row gives a maximum over
the seeds, the maximum is reported but does not gate: it depends on which
workload the seeds draw far more than on the scheduler. Random draws that depend on what the
scheduler sends (packet loss, jitter) are the harness's own. A maximum that
one unlucky draw decides (the p99.9 of S-3s) is not a target.

Pipes are written rate / one-way delay; reverse pipes mirror them. A
harness uses its own low-water rule, which can leave more bytes in a failed
pipe's carrier and raise T_suspect on a long fast path; H-5 is therefore
judged by the bound of its row, which takes the implementation's own
T_suspect, and by the 750 ms cap.

| # | Scenario | Target |
|---|---|---|
| S-1 | 100 Mbit/s / 5 ms + 20 Mbit/s / 50 ms, one bulk stream, 4 MiB window, `spread-eta` | goodput plus the redundant copies of the urgent stream >= 99 % of 120 Mbit/s (118.8) |
| S-2 | as S-1, 16 bulk streams | as S-1; Jain index >= 0.99 |
| S-3 | as S-1, plus a 1 KiB urgent message every 10 ms (present in every scenario; urgent, so sent redundantly, SCHEDULER.md Section 4.1) | urgent p99 <= `affinity` p99 + 1 ms |
| S-3s | as S-3, every pipe pair stalls 6 times a minute for 50 to 300 ms (lognormal, median 120 ms), 10 seeds x 60 s | urgent p99, mean / maximum over the seeds, <= 45 / 70 ms; p99.9, mean over the seeds, <= 100 ms |
| S-3q | as S-3, the fast pipe's carrier buffers 4 MiB in user space and reports neither its unsent bytes nor an RTT | urgent p99 <= 30 ms; goodput plus the redundant copies >= 97 % of 120 Mbit/s (116.4) |
| Q-1 | as S-3, both carriers buffer 1 MiB that way | as S-3q |
| Q-2 | one 1 Gbit/s / 50 ms pipe of a multipath session, receiver auto-tuning, its carrier buffering 64 MiB that way | >= 98 % of 1 Gbit/s; urgent p99 <= the pipe's one-way delay + 30 ms (80 ms) |
| Q-3 | as Q-2 on a 1 Gbit/s / 50 us path with a 4 MiB carrier buffer | >= 98 %; urgent p99 <= 1.5 ms |
| Q-4 | F over the carriers of Q-1, 10 seeds x 60 s | flow completion p99, mean / maximum, <= 40 ms / 300 ms / 2.05 s and 55 ms / 400 ms / 3.65 s; urgent p99 <= 30 ms |
| Q-5 | S-11d over the carriers of Q-1 (pipe B's one-way delay rises from 50 to 150 ms at 10 s; measured from 20 s) | goodput plus copies >= 96 % of 120 Mbit/s (115.2); urgent p99 <= 30 ms |
| Q-6 | 1 Mbit/s / 200 ms + 1 Gbit/s / 50 ms, the fast pipe's carrier hides 64 MiB, its one-way delay rises to 75 ms at 10 s; measured from 20 to 80 s, over several base refreshes | >= 97 % of 1001 Mbit/s; urgent p99 <= 125 ms |
| Q-7 | one 2 Mbit/s / 20 ms pipe whose carrier hides 1 MiB | urgent p99 <= 400 ms |
| Q-1s | one pipe of a session without multipath, with pipe acknowledgement (MULTIPATH.md Section 3.5): pipe A of S-3 (100 Mbit/s / 5 ms) alone, its carrier hiding 1 MiB that way | urgent p99 <= 30 ms; goodput >= 97 % of 100 Mbit/s |
| Q-2s | as Q-2 without multipath | as Q-2 |
| Q-3s | as Q-3 without multipath | as Q-3 |
| Q-4s | the flows of F over the carrier of Q-1s, 10 seeds x 60 s | flow completion p99, mean and maximum, <= 1.1 x the same over a carrier with a low-water mark; urgent p99 <= 30 ms |
| Q-5s | as Q-1s, the pipe's one-way delay rises from 5 to 150 ms at 10 s; measured from 20 s | goodput >= 97 % of the same over a carrier with a low-water mark (the 4 MiB window); urgent p99 <= the new one-way delay + 30 ms (180 ms) |
| Q-7s | as Q-7 without multipath | as Q-7 |
| Q-9s | one 100 Mbit/s / 50 ms pipe without multipath, its carrier hiding 4 MiB, the path's rate drops to 20 Mbit/s at 10 s; measured from 15 s | urgent p99 <= 1.1 x that of the same pipe at 20 Mbit/s from the start |
| Q-9 | two 100 Mbit/s / 50 ms pipes of a multipath session, each carrier hiding 4 MiB, one drops to 20 Mbit/s at 10 s; measured from 15 s | urgent p99 <= 1.1 x that with the slower rate from the start |
| Q-10s | one 10 Gbit/s / 50 us pipe without multipath, its carrier hiding 16 MiB | >= 98 % of 10 Gbit/s; urgent p99 <= 1.5 ms |
| Q-11s | one 10 Gbit/s / 50 ms pipe without multipath, its carrier hiding 256 MiB, default window caps | >= 98 % of 10 Gbit/s; urgent p99 <= the one-way delay + 30 ms (80 ms) |
| Q-12s | as Q-11s, the rate drops to 2 Gbit/s at 10 s; measured from 15 s | >= 97 % of 2 Gbit/s; urgent p99 <= 80 ms |
| LQ | carriers without an unsent count or low-water mark at 10 Gbit/s / 50 ms one way (a QUIC stream as a pipe), no multipath, measured from 10 s over 60 s (120 s for 256 MiB): hidden buffers of 4 MiB, 16 MiB and 256 MiB; a carrier whose 64 MiB send buffer also holds its unacknowledged bytes and so limits it; 2 ms of one-way jitter with 256 MiB hidden; the same send-buffer model with 256 MiB at 100 ms one way | goodput >= 99 % of 10 Gbit/s (the send-buffer-limited carrier: >= 99 % of what it carries without the limiter); urgent p99 <= the one-way delay + 30 ms (80 ms; 130 ms at 100 ms one way), + 50 ms with jitter |
| S-4 | as S-1 with a 1 MiB window | >= the fast pipe alone (99 Mbit/s) |
| S-5 | 1 Gbit/s / 5 ms + 1 Gbit/s / 55 ms, one bulk stream, 32 MiB window; also with receiver auto-tuning (IMPLEMENTATION.md Section 6.2) and the default caps | >= 99 % of 2 Gbit/s (the reorder budget and the stream send buffer must not cap it) |
| S-6 | 100 Mbit/s / 5 ms + 1 Mbit/s / 300 ms, one bulk stream, 16 MiB window | >= the fast pipe alone (99 Mbit/s) |
| S-7 | as S-1, `affinity`, 16 bulk streams | >= 95 % (114 Mbit/s), zero reordering, Jain index >= 0.95 |
| S-8 | as S-1, the fast pipe fails at 10 s, 12 phases, `spread-eta` and `affinity` | stall <= (last acknowledgement in flight) + T_suspect + reinjected / surviving rate + surviving OWD, and <= 400 ms; duplicate bytes beyond the redundant copies <= the failed pipe's unacknowledged bytes; urgent p99 <= 80 ms in every phase |
| S-9 | one pipe, no multipath, pipe acknowledgement not in use (a plain QMux peer) | no acknowledgement frames |
| S-9a | as S-9 with pipe acknowledgement, over a carrier with a low-water mark | acknowledgement bytes <= 0.1 % of the goodput; goodput and urgent p99 as S-9 |
| S-10 | as S-1, the slow pipe drops from 20 to 0.2 Mbit/s at 10 s, 12 phases | >= the fast pipe alone in every phase (99 Mbit/s) |
| S-12 | one 1 Gbit/s / 0.5 ms pipe carries a bulk stream (32 MiB window); an equal pipe joins at 3 s; measured from 6 s; S-12e the same with 100 Mbit/s / 5 ms pipes and a 4 MiB window | >= 97 % of 2 Gbit/s (the placement rate of SCHEDULER.md Section 2.3); S-12e >= 97 % of 200 Mbit/s |
| F | as S-1, finite flows (70 % 16 KiB, 25 % 256 KiB, 5 % 4 MiB) at 60 % load, 10 seeds x 60 s | flow completion p99, mean over the seeds <= 30 ms / 190 ms / 2.05 s and maximum <= 45 ms / 300 ms / 3.65 s |
| I-1 | the S-1 pipes, only an urgent message every 2 s, the fast pipe fails at 31 s while idle | the first message after the failure arrives within the slow pipe's one-way delay + 15 ms (65 ms) |

**Fairness.** Sixteen bulk streams of equal urgency must share fairly under
every policy, on one tier (the S-1 pipes) and across the tiered pipes below
(all three of them carrying overflow, since bulk demand saturates the
preferred tier). The target is the Jain index of the per-stream goodput.

| # | Policy | Target |
|---|---|---|
| J-eta, J-T-eta | `spread-eta` | Jain >= 0.99; aggregate >= 95 % of the pipes' capacity |
| J-rw, J-T-rw | `spread-rate-weighted` | as above |
| J-rr, J-T-rr | `spread-round-robin`, weights by capacity | as above |
| J-aff, J-T-aff | `affinity` | Jain >= 0.95; aggregate >= 95 % |
| J-sa, J-T-sa | `session-affinity` | Jain >= 0.99; aggregate >= 95 % of the active pipes' capacity (A and C on the tiered pipes) |

**Tiered pipes** (SCHEDULER.md Sections 6 and 10). Three pipes: A
50 Mbit/s / 30 ms and B 30 Mbit/s / 40 ms at priority 2, C 100 Mbit/s /
20 ms at priority 5 (for example a relay kept as the fallback of two direct
paths). Reverse pipes mirror them, with the same priorities unless stated.
One stream paced at the offered rate, the urgent message of S-3, a 16 MiB
window, `spread-eta` unless stated, probation and the overflow delay D (1 s)
at their defaults. Shares are of the stream data sent from 3 s on (from 11 s
in T-3).

| # | Scenario | Target |
|---|---|---|
| T-1 | 40 Mbit/s offered, each spreading policy | C carries <= 1 % of the data |
| T-1-rr | as T-1, `spread-round-robin` with weights A 5, B 3, C 1 | C <= 1 %; A and B within 1 point of 62.5 % / 37.5 % |
| T-1-switch | as T-1, the policy changes from `spread-eta` to `spread-round-robin` (weights as T-1-rr) at 10 s | from 10 s, A and B within 1 point of 62.5 % / 37.5 %; C <= 1 % |
| T-2 | 150 Mbit/s offered, `spread-eta`, `spread-rate-weighted` and `spread-round-robin` | >= 97 % of the offered rate delivered (data waits about D before C helps); A and B carry >= 95 % of their 80 Mbit/s |
| T-3 | as T-1, A fails at 10 s, 12 phases | stall <= 400 ms; after 11 s B carries >= 29.5 Mbit/s and C the rest; urgent p99 <= 80 ms |
| T-4 | as T-1, A and B fail at 10 s, 12 phases | stall <= 400 ms |
| DF | as T-4 with C's carrier dying silently at 5 s; DF-1: C standby at A's and B's priority, DF-2: C a less-preferred tier (priority 5) | stall <= 400 ms |
| T-5 | as T-4, A and B recover at 20 s | C <= 1 % of the data in every 1 s window from 22 s on |
| T-6 | A and C only; A fails and recovers every 2 s from 10 s to 60 s | stream data moves back to A at most 4 times during the flapping; C carries >= 95 % of the data from 10 to 60 s; A carries data again within max(base_p, 2 x U_max) + 1 s after the flapping ends (SCHEDULER.md Section 11; U_max about 2.25 s here) |
| T-6c | as T-6, A fails and recovers every 1 s (outages far shorter than T_fail) | as T-6 |
| T-6r | as T-6, A flaps irregularly: up 0.5 to 8 s, down 0.5 to 3 s (uniform), seeds 1 to 3 | the returns during the flapping and C's share are those without the uptime cap; A carries data again within max(base_p, 2 x U_max) + 1 s after the flapping ends |
| T-7 | as T-1, reverse priorities C 2, A and B 5 | forward: C <= 1 %; reverse: >= 99 % of the acknowledgement and credit frames that are not redundancy copies (MULTIPATH.md Section 6.5) on C |
| T-8 | finite flows (the F mix) at 15 / 29 / 45 % of the preferred tier's 80 Mbit/s, 10 seeds x 60 s, each spreading policy | C share, mean / maximum over the seeds: <= 0.5 / 1.5 %, <= 1.25 / 3.5 %, <= 3.5 / 6.5 %; at 29 %, flow completion p99 mean <= 62 ms / 170 ms / 1.10 s and maximum <= 65 ms / 210 ms / 1.35 s |
| T-9 | a 16 MiB burst every 5.8 s, each spreading policy | per burst: C takes no more than the burst minus R_1 x D (6.78 MB) and the burst completes within 1.45 s (A and B alone need 1.69 s) |
| AF-flows | `affinity`, finite flows at 29 % and 50 % of the preferred tier, 10 seeds x 60 s, against `affinity` on A and B alone (no C) | flow completion p99 of each class, mean and maximum over the seeds, <= 1.1 x the baseline's |
| SA-T | `session-affinity`: T-1, T-2, T-8 (29 %), T-9 | T-1: C <= 1 %; T-2: >= 97 % of what A and C carry (150 Mbit/s); T-8: everything delivered (one pipe per tier, so the load is 48 % of A, and C carries the overflow); T-9: completion within 1.7 s |

**High bandwidth-delay paths** (IMPLEMENTATION.md Section 6.5). The size
limits are configurable memory bounds; these scenarios check that nothing
else caps the rate on long, fast paths and that failure handling scales with
the RTT. Receiver auto-tuning unless stated.

| # | Scenario | Target |
|---|---|---|
| H-1 | one pipe, 1 Gbit/s / 50 ms, default window caps (64 / 96 MiB) | >= 95 % of 1 Gbit/s |
| H-2 | one pipe, 10 Gbit/s / 100 ms, window caps 1 / 1.5 GiB | >= 95 % of 10 Gbit/s |
| H-6 | one pipe, 10 Gbit/s / 50 ms (100 ms RTT), default caps | >= 95 % of 10 Gbit/s |
| H-7 | two 10 Gbit/s / 50 ms pipes, `spread-eta`, default caps and send buffer maximum | >= 95 % of 20 Gbit/s |
| H-8 | two 10 Gbit/s / 250 us pipes, `spread-eta`, default caps | >= 95 % of 20 Gbit/s |
| H-3 | 1 Gbit/s / 50 ms + 1 Gbit/s / 100 ms, `spread-eta`, default window caps, `send_buffer` maximum 128 MiB | >= 95 % of 2 Gbit/s |
| H-4 | A 1 Gbit/s / 150 ms at priority 0, C 200 Mbit/s / 20 ms at priority 5, 64 MiB window, `send_buffer` maximum 128 MiB; 500 Mbit/s and 1.1 Gbit/s offered | 500 Mbit/s: C <= 1 %; 1.1 Gbit/s: A carries >= 95 % of its rate and >= 97 % is delivered |
| H-5 | two 1 Gbit/s / 100 ms pipes, 128 MiB window, `send_buffer` maximum 256 MiB, the first fails at 10 s, 12 phases | stall <= (last acknowledgement in flight) + T_suspect + reinjected / surviving rate + surviving OWD, and <= 750 ms; duplicates beyond the redundant copies <= the failed pipe's unacknowledged bytes |

**Nested sessions** (EXTENSIONS.md Section 10). A nested session's pipes
are a direct carrier N (pipe 0: 100 Mbit/s / 20 ms, with a low-water mark)
and two streams through relays R1, R2 (200 Mbit/s / 30 ms and 200 Mbit/s /
40 ms), whose hop buffers the nested sender cannot see (the hops' windows);
one bulk stream, receiver auto-tuning, `spread-eta`.

| # | Scenario | Target |
|---|---|---|
| NS-1 | relays as a less-preferred tier (priorities 0, 1, 1), 8 MiB hidden per relay | goodput plus copies >= 97 % of 500 Mbit/s |
| NS-2 | as NS-1, R1 fails at 10 s, 12 phases | stall <= 400 ms in every phase |
| NS-3 | as NS-1, N fails at 10 s, 12 phases | as NS-2 |
| NS-4 | one tier, 128 MiB hidden per relay (hop windows grown large) | goodput plus copies >= 97 % of 500 Mbit/s; urgent p99 <= 30 ms |
| NS-5 | as NS-4, R1 fails at 10 s, 12 phases | as NS-2 |
| NS-6 | two relayed 10 Gbit/s pipes (10 and 15 ms), 256 MiB hidden each | >= 98 % of 20 Gbit/s |

**Many streams with auto-tuned windows** (SCHEDULER.md Section 5, ECF's
backlog and BLEST's stream term by the round robin's share). Receiver
auto-tuning, `spread-eta`, the urgent message of S-3.

| # | Scenario | Target |
|---|---|---|
| AW-2 | S-2 (16 bulk streams over the S-1 pipes) with auto-tuned windows | as S-2 |
| AW-5 | 16 bulk streams over the S-5 pipes, auto-tuned | >= 99 % of 2 Gbit/s |
| AW-q2 | 16 bulk streams over a 50 Mbit/s / 40 ms relay and a 100 Mbit/s / 20 ms path | >= 97 % of 150 Mbit/s |
| AW-q4 | 16 bulk streams over a 1 Gbit/s / 30 ms relay and a 10 Gbit/s / 50 ms QUIC path with 0.1 % packet loss (an early penalty on the QUIC pipe), 3 seeds x 15 s; release: 30 seeds | goodput >= 97 % of 11 Gbit/s in every seed |
| AW-F | F with auto-tuned windows | as F |

**Lanes** (MULTIPATH.md Section 2.5, SCHEDULER.md Sections 4.1 and 9.2).
In these rows a pipe's carrier is a QUIC connection whose stream (lane)
delivers in order, a lost packet holds the later records of its lane back
for a round trip plus 1 ms, and the connection's rate is the path's
(loss-agnostic congestion control). D is the pipe pair on one bidirectional
carrier stream without lanes, B the pipe over 16 lanes with the copy lane;
flows at 60 % load (the F mix), receiver auto-tuning, `spread-eta`, the
urgent message every 10 ms; 3 seeds x 60 s, judged on the means
of 30 seeds for a release. Targets apply to B.

| # | Scenario | Target |
|---|---|---|
| QL-fl | one 100 Mbit/s / 25 ms QUIC path of a multipath session, packet loss 0, 0.1 % and 1 % | 1 %: 16 KiB p99 mean <= 96 ms, urgent p99 mean <= 30 ms; 0.1 %: urgent p99 mean <= 30 ms; 0: each completion p99 mean <= 1.05 x D's |
| QS-fl | QL-fl without multipath (pipe acknowledgement) | 1 %: 16 KiB p99 mean <= 97 ms, urgent p99 mean <= 80 ms |
| QM-fl | a lossless 50 Mbit/s / 40 ms relay beside a 100 Mbit/s / 20 ms QUIC path with loss, one tier | 1 %: 16 KiB p99 mean <= 79 ms; urgent p99 mean <= 50 ms |
| QX | one 10 Gbit/s / 50 ms QUIC path, 0.1 % loss, one bulk stream and flows at 750 MB/s, 3 seeds | urgent p99 <= the one-way delay + 5 ms (55 ms) |
| QL-b16 | 16 bulk streams on the QL path, 0 to 1 % loss | goodput >= 97 % of 100 Mbit/s |
| QM-f | the QUIC path of QM fails at 10 s (1 % loss, one bulk stream), 6 phases | stall <= 450 ms in every phase (the surviving relay is 40 ms one way) |

Every stream's first chunk carries its preamble, and a stream is offered
when its own preamble has arrived (SPEC.md Section 7.2 rule 5); all rows
above are measured that way.


**Pipe liveness** (MULTIPATH.md Sections 6.5, 7.1 and 7.3, the liveness
bound). The S-1 pipes (A 100 Mbit/s 5 ms, B 20 Mbit/s 50 ms one way,
reverse pipes mirrored) unless stated; both ends probe their outbound
pipes. Quiet rows send one message at 0 and nothing from then on, measure
from 30 s and kill a pipe silently (a black hole) at 300.5 s; busy rows run
the bulk stream of S-3 and kill it at 10 s. With the defaults the bounds
are k + n_out / 8 s + T_suspect = 15.5 s for an idle pipe, R_p + max_ack_delay
+ n_out / 8 s + T_suspect = 1.525 s for an acknowledgement pipe of a busy
session and L_in = 17.25 s for an uplink; detection is the first suspicion
of either direction. Every row also requires that no pipe is suspected
falsely and that no compliant row lets the budget of SPEC.md Section 9.4
fall below 4 000 tokens.

| # | Scenario | Target |
|---|---|---|
| LV-1 | quiet, 10 min, two and four pipe pairs | every carrier silent for at most k + a round trip; OWD_p within 1 ms of the true one-way delay |
| LV-2 | quiet; the primary return direction alone, pipe pair A or B dies | detection <= 15.5 s |
| LV-3 | busy; the primary return direction dies at 10 s | detection <= 1.525 s; goodput >= 99 % of the same without the death |
| LV-4 | quiet for 320 s, then an urgent message every 10 ms | urgent p99 as with traffic throughout |
| LV-5 | records held up to H_p = 2 s, k = 28 s, one end probing, an intermediary that ends carriers silent for 30 s; B an uplink (no reverse direction), k 15 s | carrier silence < 30 s; the uplink never fails while it works, and fails within L_in when it dies |
| LV-7 | the tiered pipes (C at priority 5), quiet, busy from 60 to 90 s, quiet again | at most 8 PIPE_PING frames in any second at each end |
| LV-8 | a peer that sends 40 or 1 000 PIPE_PING frames per second beyond the limit | closed with ZMUX_EXCESSIVE_LOAD within 4 096 / (r - 32) s + 5 % |
| LV-K0 | as LV-2 with B and probes off at one end only (`probe_interval` 0) | detection by the other end <= 15.5 s |
| LV-SB | as LV-2 with B a standby pipe pair | detection <= 15.5 s |
| LV-OW | busy; B an uplink that dies at 10 s | the sender's detection within T_suspect of its oldest unacknowledged byte; the receiver's within L_in of the last complete record |

### 4.6 Performance gates

IMPLEMENTATION.md Section 9 defines the targets and the CI gates. Every
implementation measures and publishes at least the latency-under-load row
(64 B request-response p99 with a bulk stream on the same session) and the
footprint rows, the latter with the measurement method of IMPLEMENTATION.md
Section 9.2.

**Single-pipe cost of multipath** (requirement C-15). Before a release each
implementation shows that a session without multipath and resume pays
nothing for them:

1. **State.** After the footprint measurement of IMPLEMENTATION.md Section
   9.2, the multipath side storage of IMPLEMENTATION.md Section 2.6 holds no
   bytes for any of the N sessions, their pipes or their streams (checked
   with the allocator or heap accounting the footprint measurement uses, or
   with a test hook that reports the side storage's size).
2. **Speed.** The single-pipe rows of IMPLEMENTATION.md Section 9.2 (one
   stream with 64 KiB writes, CPU per GB, 64 B request-response p50, p99
   under load, steady-state allocations) are measured against a comparison
   build whose path layer is the single-pipe implementation of the internal
   interface (IMPLEMENTATION.md Section 2.6), alternating the two builds over
   at least 5 rounds and comparing medians: throughput within 2 %, CPU per
   GB within 3 %, p50 and p99 within 5 %, and no extra allocation per frame.
   The comparison build exists only in the benchmark harness.

### 4.7 Concurrency and liveness tests

Every implementation runs these tests before a release (requirement C-13),
each one also checking that threads, tasks, goroutines, file descriptors and
pooled buffers return to their baseline and that no call waits past a
watchdog:

1. **Deterministic simulation**: a seeded harness drives driver and core
   over in-memory carriers with a virtual clock (Go `testing/synctest`,
   Rust paused tokio time, Java an injected clock and a manually driven
   event loop, Python a fake-clock event loop), with random API calls,
   carrier failures and stalls, resets, GOAWAY and closes; the invariants
   of Section 4.4 hold after every step, and every failure replays from
   its seed.
2. **Race detection**: Go `-race` on every test; Rust `loom` for the
   synchronisation units, `shuttle` (or equivalent) for the std driver and
   seeded single-threaded runtime tests for the tokio driver; Java
   jcstress and Lincheck (or equivalent); Python stress tests on the
   free-threaded build.
3. **Close races**: each kind of wait of IMPLEMENTATION.md Section 2.5.4
   raced, at random points over at least 10^4 iterations, against session
   close, stream reset, STOP_SENDING, peer GOAWAY, local shutdown,
   cancellation, deadline expiry and carrier failure: every call returns
   with a defined outcome, and a cancellation racing with completion never
   loses or duplicates data.
4. **Stuck carriers**: a carrier whose writes block (the peer stops
   reading) while the handshake times out, the idle timeout fires, the
   application closes, the peer ends the pipe or a read fails: the session
   reaches CLOSED and releases everything within the bound of
   IMPLEMENTATION.md Section 2.5.5.
5. **Re-entry and failures**: every hook, the custom policy and
   `allow_connection_window_increase` included (all run outside every lock,
   IMPLEMENTATION.md Section 2.5.2), calls back into the session (each
   public method), and every blocking call is made from a completion
   callback, on the executor that delivers it and on a thread that drives
   sessions: no call deadlocks, and each completes or fails with the
   documented error of IMPLEMENTATION.md Section 2.5.3. A custom policy that
   calls a blocking zmux method gets that error (Java, Rust, Python) or
   completes without deadlock (Go). In Go a policy that blocks for 100 ms
   stalls no session: each writer takes `affinity` after 1 ms and the late
   answer counts as stale. In Java, Rust and Python blocking inside `Pick`
   breaks the API contract (API.md Section 5.2) and is not tested; instead a
   policy that takes 1 ms per call must trip no session timer. Under
   bulk load on two pipes, at least 99 % of a custom policy's answers are
   applied (the generation is not advanced by metrics), and a policy that
   answers with an allowance of 1 MiB per stream direction is called at
   most once per MiB of each direction's data, not once per chunk (sticky
   answers, IMPLEMENTATION.md Section 2.5.2). Panics or exceptions
   injected into hooks, carriers and executors close the session with
   INTERNAL_ERROR and wake every waiter; in a policy they count as an invalid
   answer, and in `allow_connection_window_increase` as a refusal.
6. **Multiple waiters** on one handle, where the API allows them.
7. **Peer input**: fuzzing (Section 4.1) also with an adversarial multipath
   peer, under sanitizers where available; no crash, hang or unbounded
   memory, and the work per input byte stays bounded (a test feeds
   adversarial gaps and orderings and counts the operations per byte, such
   as comparisons, allocations and interval-node visits, against a fixed
   bound, rather than timing them). A peer that joins and drops pipes in a
   loop while the application reads no events leaves memory bounded
   (IMPLEMENTATION.md Section 2.5.7).
8. **Slow carriers** (traces cannot model a kernel send buffer): a
   0.25 Mbit/s pipe that is the last usable one, on a non-blocking socket
   with its low-water mark, written continuously for 60 s, is never judged
   suspect (MULTIPATH.md Section 7.1, the U / R_p allowance); a carrier that
   then stops taking bytes is judged suspect within T_suspect of the moment
   its unsent bytes stop decreasing.
9. **Scheduling oracles** run over the frames that every multipath test and
   trace produces: stream data appears on a JOINING pipe only while no other
   outbound pipe is usable
   (MULTIPATH.md Section 11.1 rule S1); no stream data goes to a pipe in
   probation while another usable pipe is out of probation (SCHEDULER.md Section 11).
   The trace validator does not model pipe states, so these are checked
   by the implementations' runners.
10. **Stream carriers and relays** (libraries that offer them, API.md
    Section 4.6 and Appendix A): a nested session over two relayed
    streams and a direct carrier, three sessions per relay hop in one
    process. (a) Every event of the splice table of API.md Appendix A.2,
    and each of the mappings of EXTENSIONS.md Section 10.2, on either side
    and in both directions, at random points of a bulk transfer: the
    propagated code and the end of every stream are as specified, the
    nested session reinjects and continues, and nothing leaks. (b) A
    relay's downstream reader stops reading: the relay holds at most the
    upstream window, plus what the downstream holds unsent within its
    peer's credit, plus one chunk (API.md Appendix A.2), the nested pipe
    becomes suspect and fails, the reset reaches both ends, and every hop
    releases its session credit (EXTENSIONS.md Section 10.3). (c) Splices
    cancelled at random points, with limiters whose `ready` waits and is
    cancelled: no task, buffer or pending limiter call survives, and the
    bytes charged equal the bytes forwarded.
11. **Application byte counters** (API.md Section 7.1): under random writes,
    reads, resets, stops and datagram drops, at both ends and at a splicing
    relay, every counter is monotonic, readable concurrently from other
    threads without blocking the session, and at the end of each stream
    finished and read to its end the writer's `bytes_written` equals the
    reader's `bytes_read`, whatever pipes failed, were reinjected or carried
    redundant copies.
12. **Pipe retirement and liveness timers**: pipes retired at random points
    with their terminal and non-terminal events read, unread and dropped
    by the queue's bound: each `PipesRetired` has `retired` >= 1, counts
    every retired pipe once, counts `failed` or `closed` exactly for the
    pipes whose terminal state was unread, never counts their dropped
    non-terminal events, and no summary or terminal event is lost
    (API.md Section 4.5). With a probe interval below 1 s no probe waits
    longer than it; an unanswered keep-alive is not repeated before new
    activity (SPEC.md Section 6.4); a policy decision with a probe
    interval below 1 s is rejected and reported while the pool keeps its
    pipes up (API.md Appendix A).
13. **Configuration bounds and exported defaults**: `probe_interval` and
    `ProbeInterval` accept 1 s and the setting that turns idle probes off,
    and give the configuration error for 1 ns and 999 ms, in the session
    configuration and in pipe and pool options; `probe_jitter`,
    `ProbeJitter` and the pool jitters accept 0 and give the configuration
    error for 1 and for a negative value; `standby_probe` accepts 1 s and
    the setting that turns standby probes off, and gives the configuration
    error for 999 ms; `keep_alive_period` accepts 1 s and the setting that turns
    keep-alive off, and gives the configuration error for 0 and 999 ms;
    `max_idle_timeout` accepts 1 ms and no timeout, and gives the
    configuration error for 999 us; a value that no setting of an option covers gives the
    configuration error. Every option left unset behaves as its default,
    and one given an explicit setting (0, no timeout, off, unlimited, each
    in the language's idiom) behaves as stated for that setting (API.md
    Section 6). A
    pool decision with a probe interval of 500 ms, from an application
    policy and from any other policy the library offers, is rejected,
    reported and treated as keeping the pool's behaviour; no pipe is
    dialled with 1 s in its place (API.md Appendix A); so is a decision
    with a negative dial delay, which is not dialled at once in its place.
    A pool decision with a probe interval of 0 turns idle probes off on
    the pipes dialled under it; one without a probe interval leaves them
    at the pool's or the session's setting. A negative, NaN or
    infinite shaping limit, and a padding ratio limit above 1, give the
    configuration error at the earliest check and are never corrected.
    An unset shaping limit does not bound anything; a padding byte limit
    of 0 lets no padding out; without a ratio limit no 16382-byte
    allowance applies and only the padding byte limit bounds padding.
    Under a padding ratio limit of 0.01, the first padding-only record of
    a session, of 16382 bytes, goes out whole; a padding-only record
    beyond the allowance is deferred and later sent at its full length
    with the clamped count unchanged, and one cut by the padding byte
    limit increments it (API.md Section 6). The exported default
    constants equal the values of API.md Section 6 and the defaults in
    effect when nothing is set.
14. **Shaping configuration files** (libraries that load them, API.md
    Section 6): every case of the shared vectors `contract.json`,
    `pool_policy.json` and `session_limits.json` (in `testdata/shaping/`
    of the Rust implementation) loads or fails as listed, with an error
    containing the listed text, and the pool policy cases yield the listed
    decisions.

15. **Pools over a mode** (libraries that offer them, API.md Section
    4.8): a pool member is dialed on the kind the carrier cache holds as
    usable; once a more preferred kind's backoff expires the pool upgrades
    by make before break (no reinjection, no stall, the old member retired
    only after the new one settled); a failed upgrade leaves the old
    member and doubles that kind's backoff; an `auto` dial runs no other
    upgrade logic than its one-member pool.
16. **Carrier cache interchange**: export then import yields the same
    decisions; the shared JSON cases (unknown member, unknown kind,
    expired entry, duplicate key and kind, unsupported version, negative
    backoff) import or fail as API.md Section 4.8 says, a failed import
    leaving the cache unchanged.
17. **Pipe loss statistics**: a pipe that fails with unacknowledged bytes
    reports them as `bytes_lost` and `records_lost` as its estimate;
    `bytes_retransmitted` and `loss_rate` are absent for an adapter without
    a retransmission count. A write overhead reaches the shaper factory
    unchanged, including one set by an inbound transform, and a value
    outside [0, 65535] is the configuration error.

### 4.8 Auto-tuning tests

Every implementation runs these against the rule of IMPLEMENTATION.md
Section 6.2 with its defaults (requirement C-12), on real kernel TCP unless
stated. Loopback runs alternate the variants over at least 5 rounds,
take the median, and are made with the host load below half its cores.

| # | Scenario | Target |
|---|---|---|
| H-1' | one pipe, 1 Gbit/s x 100 ms RTT (`tc netem`), CUBIC, bottleneck queue >= 1 x BDP, default caps, one stream for 20 s | the last 5 s >= 90 % of the link; time to 90 % <= that of raw TCP + 2 s |
| AT-1 | Linux loopback, one session, 64 B request-response alongside one bulk stream, both directions; the runtime has at least as many workers as the two ends have busy tasks together (at least 6: two session drivers, the bulk writer and reader, the echo and the request-response client), or the ends run in separate processes | p99 <= the relative latency target of IMPLEMENTATION.md Section 9.2, p99_idle + w + 1.9 x Q (gate p99_idle + w + 2.4 x Q for Go and Rust), with the unloaded request-response p99, the host's wake-up p99 and Q = max(B, K x RTT_min) measured in the same run and reported with the host's load and core count; the bulk stream >= 90 % of its throughput without the request-response traffic and >= 85 % of a fixed 16 MiB window's |
| AT-2 | as AT-1, the steady window W and arrival rate R of the bulk stream | W / R <= 2 x max(K x RTT_in, B) |
| AT-3 | as H-1', the carrier's RTT disabled (credit loop and QX_PING only), no low-water mark | the last 5 s >= 90 %; steady window <= 1.5 x that of H-1'; RTT input <= 1.1 x the path RTT |
| AT-4 | one stream, 1 Gbit/s x 20 ms RTT | >= 80 % of the link within 2 s (IMPLEMENTATION.md Section 9.2) |
| AT-5 | simulated 2.5 Gbit/s x 200 ms (a shaper without TCP below), caps >= 4 x BDP | the last 5 s >= 95 % |
| AT-6 | 100 concurrent bulk streams, 1 Gbit/s x 100 ms | total >= 95 %, Jain index >= 0.9, heap <= 1.1 x what the same implementation needs for this row with every stream's receive window fixed at the smallest value that meets the row's rate targets, measured by the implementation itself |

Request-response latency over a long path under bulk load is the bottleneck
queue that TCP below the session fills; zmux bounds it only through
its windows and sets no target for it.

### 4.9 Pitfall regression tests

IMPLEMENTATION.md Section 13 lists common implementation mistakes, by label. Before a release (requirement C-12) every
implementation covers each label: with the test below, with the existing
trace or test named, or, where nothing observable from outside shows the
mistake, with the **review item** given, which the release review checks
and records. Tests use in-memory carriers and a virtual clock unless they
say otherwise, and check the baselines and the watchdog of Section 4.7.

**Concurrency and wake-ups (IMPLEMENTATION.md Section 13.1)**

- **W1.** 10^4 iterations: a stream reader with nothing buffered starts a
  read (taking the read-through path where the language has one) while,
  at a random point, the carrier fails or the session is closed. Pass:
  every read returns the session's error before the watchdog. Section 4.7
  items 2 and 3 cover the general case; the hand-off of read-through and
  write-through is one of the units model-checked under item 2.
- **W2.** The peer's stream window is fixed at 16 KiB and it sends no
  credit. Write 3 KiB and wait for send capacity with `min` = 16 KiB; the
  3 KiB are then written to the carrier. Pass: the wait returns once they
  are written, with a capacity above 12 KiB, although no credit frame
  arrived. Then the writer loops (wait with `min` = 64 KiB, write what the
  wait returned, repeat) while a scripted peer grants 1 MiB of credit in
  steps of 4 KiB. Pass: the writer's task is woken at most once per wait,
  so at most once per 64 KiB of credit plus once for a last wait that
  returns below `min` with nothing unsent.
- **W3.** One pipe, two streams, a reader waiting on each; the peer sends
  data for the two streams alternately, one record each, 10^4 times, with
  the virtual clock stopped. Pass: every read completes without the clock
  advancing (no wake-up depends on an unrelated timer).
- **W4.** Two pipes; an application ping whose QX_PING request goes out on
  pipe A, which then fails before the response arrives. Pass: the ping
  completes over pipe B with a new request within max(1 s, 3 x SRTT) plus
  one round trip, or fails with its documented error; it never waits past
  the watchdog.
- **W5.** For libraries with read-through or write-through: one pipe; a
  test hook pauses an application thread for 50 ms while it holds the
  receiving side inside a read-through, and then the sending side inside a
  write-through. Pass: meanwhile a due timer fires within the clock's
  granularity, and frames of another stream are written within one write
  call of becoming ready. AT-1 of Section 4.8 also catches the mistake as
  latency under load.
- **W6.** Two sessions in one process share one datagram limiter (API.md
  Section 5.6) whose `try_ready` and `charge` take a lock L; a second
  thread repeatedly takes L and, holding it, calls `Stats` and
  `SendDatagram` on both sessions. 10^4 datagrams each way. Pass: no
  deadlock before the watchdog. Section 4.7 items 5 and 10 (c) cover the
  other hooks and limiters.
- **W7.** With a test-hook counter of core-lock acquisitions: two pipes,
  four bulk streams, steady state. Pass: at most 2 acquisitions per driver
  wake-up on average, not counting application calls. Where a language
  cannot count them, review item: one critical section per wake-up as
  IMPLEMENTATION.md Section 13.1 W7 describes.
- **W8.** Write-interest oracle, run over every multipath test and trace
  and over Q-1 and Q-1s of Section 4.5 on the implementation's own
  harness: whenever the core reports that a pipe wants to write, the next
  `poll_transmit` for that pipe produces at least one byte. In particular
  a pipe held back by the carrier queue limiter while redundant or
  opportunistic copies are queued for it reports no write interest until
  its release.

**Flow control and credit (IMPLEMENTATION.md Section 13.2)**

- **C1.** Two pipes; on a peer-opened stream the frame carrying [3, P +
  100), P the preamble's size, arrives on pipe B before the frame carrying
  [0, P) on pipe A; the application reads the 100 bytes. Pass: the
  session's and the stream's `recv_buffered` (API.md Section 7.2) are 0,
  and the session credit the endpoint has released (advertised, plus
  released and not yet advertised, which stays below the update threshold
  of IMPLEMENTATION.md Section 6.2) has advanced by every byte consumed. In the deterministic simulation of Section 4.7 item 1 also
  check after every step that the session's `recv_buffered` equals the sum
  over its streams.
- **C2.** Traces `multipath.credit.reset_releases_lost_bytes`,
  `multipath.credit.reset_lowering_reliable_size_releases`,
  `regression.reset_does_not_refund_session_credit`,
  `core.flow.final_size_counts_for_session` and
  `ext.preamble.reset_before_any_data`. In addition, after the runs of
  Section 4.3 with pipes killed, once every stream has ended and nothing
  is buffered, each endpoint's released session limit (advertised, plus
  released and not yet advertised, below the update threshold of
  IMPLEMENTATION.md Section 6.2) is at least the sum of the final sizes of
  all streams plus its initial session window (no credit left
  unreleased).
- **C3.** Traces `multipath.spread.duplicates_are_harmless`,
  `multipath.spread.reorder_across_pipes` and
  `multipath.budget.duplicates_not_charged`; the session accounting
  invariant of Section 4.4. With redundant copies and opportunistic
  reinjection active, a stream's `bytes_received` equals the number of
  distinct offsets received.
- **C4.** Review item: each running sum of IMPLEMENTATION.md Section 13.2
  C4 has a debug recount that the simulations of Sections 4.5 and 4.7 run
  with. Observable part: trace `multipath.redundancy.byte_bound` (the copy
  counter is released on acknowledgement).

**Memory (IMPLEMENTATION.md Section 13.3)**

- **M1.** One pipe and, separately, two pipes with `spread-eta`; four
  streams of 1 GiB each; the application reads with 64 KiB reads, then
  again with 1000-byte reads (which leave small rests of referenced
  pieces). Live heap is sampled every 100 ms with the allocator or heap
  accounting of the footprint measurement. Pass: the growth above the
  baseline never exceeds 2.125 x the sum of the streams' spans (from the
  read offset to the highest received offset, SPEC.md Section 9.8) plus two
  read buffers per pipe or lane plus the pool's retained size, and it returns to
  the baseline within 1 s after the streams end. A unit test of the core
  in addition checks the session bound of IMPLEMENTATION.md Section 3
  directly (retired buffers referenced at most twice the referenced bytes)
  after every arrival, every consumption, a stream reset that drops
  referenced pieces, and the end of a pipe or lane whose buffer retires,
  with small pieces each alone in its buffer, unread and then read in part.
- **M2.** The deliberate fragmentation test of Section 4.3.
- **M3.** Section 4.6 (no allocation per frame), plus: two pipes, with
  `spread-eta` and with `spread-round-robin`, 128 MiB and then 512 MiB of
  bulk data with PIPE_ACK, credit updates and timers. Pass: both transfers
  allocate the same number of times within a constant of 4.
- **M4.** The idle multipath footprint of IMPLEMENTATION.md Section 9.2,
  measured right after the joins and again after 60 s of quiet (probes
  running). Pass: no growth, and no allocation of more than 4 KiB per probe
  record sent.
- **M5.** Where an implementation reports its own memory (a budget
  charge, a diagnostic figure): the report for an idle session and for one
  with a bulk stream in progress is within 5 % of what the allocator
  measures for it, and the `used` figure a listener passes to
  `OnMemoryPressure` (API.md Section 4.4) equals the sum of its sessions'
  charges.
- **M6.** Section 4.7 (baselines after every test), plus pipe churn: 1000
  rounds of adding a pipe and removing it gracefully, abruptly, by carrier
  failure and by the peer's PIPE_ABANDON. Pass: carriers closed equal
  carriers opened, one attachment remains, and the event queue stays
  within the bound of IMPLEMENTATION.md Section 2.5.7. The two-pipe runs of
  Section 4.3 item 3 and `multipath.remove.failed_attachment_retired`
  cover the protocol side.

**Timers (IMPLEMENTATION.md Section 13.4)**

- **T1.** Every duration parameter of API.md Section 6 and of the
  scheduler set, one at a time, to the largest value the language's
  duration type holds (the probe intervals also to that value minus 1 s);
  two tiers, a bulk transfer, then 20 s of quiet and traffic again. Pass:
  no overflow, abort or exception, and no deadline that is "never" fires.
- **T2.** Traces `multipath.tier.return_after_probation`,
  `multipath.tier.first_join_probation_without_history` and
  `multipath.tier.probation_capped_by_uptime` (probation ends with no
  other input). Review item: where the earliest deadline is cached, debug
  builds compare it with a full recomputation.
- **T3.** One pipe with pipe acknowledgement over a carrier that reports
  neither unsent bytes nor a low-water mark (the limiter paces it), a
  simulated 100 Mbit/s x 100 ms path, the driver's timers firing 0.5 ms and,
  in a second run, 2 ms late; bulk for 12 s. Pass: the last 5 s carry at
  least 90 % of the path rate, and paced bulk STREAM frames average at
  least 4 KiB.
- **T4.** The quiet-session rows of IMPLEMENTATION.md Section 9.2 (Section
  4.6), with the timer firings per session per minute reported.

**Estimators and scheduler (IMPLEMENTATION.md Section 13.5)**

- **E1.** One pipe with pipe acknowledgement (and, separately, two pipes),
  10 ms RTT: the endpoint only receives for 120 s, sending acknowledgements
  alone, then writes 1 MiB. Pass: every RTT sample taken afterwards, and
  `smoothed_rtt`, stay below 2 x the path RTT plus the peer's
  `zmux_max_ack_delay`.
- **E2.** Trace `multipath.tier.return_after_probation` (no samples from
  a suspicion), Q-6 and Q-12s of Section 4.5 on the implementation's own
  harness (base refresh). Review item: every sample source records the
  epoch it was sent in, and QX_PING samples feed a paced pipe only before
  its first PIPE_ACK sample.
- **E3.** One pipe with pipe acknowledgement over a paced carrier, 1
  Gbit/s x 1 ms: the endpoint receives a bulk stream for 10 s, sending
  only acknowledgements, then writes 1 MiB. Pass: the 1 MiB is delivered
  within 2 s.
- **E4.** With a counter of stream-queue entries visited by the scheduler:
  two pipes, `spread-eta`, bulk data on 10 and then on 1000 streams. Pass:
  visits per chunk with 1000 streams are at most twice those with 10, and
  M3 holds.

**Write path (IMPLEMENTATION.md Section 13.6)**

- **P1.** One pipe over Linux loopback TCP, bulk only: at least 90 % of
  the bytes go in write calls larger than 64 KiB; with the request-response
  traffic of AT-1 added, calls built while it waits hold at most the
  write-call bound of IMPLEMENTATION.md Section 4.
- **P2.** For each room r from 0 to 8 bytes left in a batch, a stream is
  finished with no data pending. Pass: the peer decodes every record and
  receives the FIN in a record. Fuzzing (Section 4.1) and every trace's
  record parsing cover the general case.
- **P3.** Two pipes, redundancy and opportunistic reinjection active, pipe
  B slowing as in S-10; a stream writes 1 MiB and is reset with a Reliable
  Size of 100 KiB while copies are queued. Pass: every STREAM byte the
  receiver gets, copies included, equals the written byte at its offset,
  and no FIN follows the reset. Trace
  `multipath.streams.stop_sending_reduces_reliable_size`.
- **P4.** Two and four pipes over Linux loopback TCP, `spread-eta`, one
  bulk stream with PIPE_ACK and credit flowing back, and the same over an
  in-memory carrier that returns one 16 KiB chunk per read: counted over
  the transfer after its first 16 MiB, at least 90 % of the bytes go in
  write calls that end at the batch or at the carrier's room, not at the
  write-call bound of IMPLEMENTATION.md Section 4 (with nothing
  latency-sensitive waiting, the only input is acknowledgements and
  credit).
- **P5.** Trace `multipath.redundancy.partial_copy_no_fin`.

**Multipath as a layer (IMPLEMENTATION.md Section 13.7)**

- **L1.** Section 4.6 (state and speed of single-pipe sessions).
- **L2.** Trace `multipath.failover.suspect_reinjects_and_probes` (the
  moment of suspicion).

**Lanes (IMPLEMENTATION.md Section 13.8)**

- **N12.** A session with lanes offered on its carrier closes; while it is
  CLOSING, and again after it is CLOSED, a pipe is attached to it and a
  lane stream with its Lane Key is handed to the adapter's accept path
  (on a connection that another session shares, or directly by the test,
  since the session's own connection is gone once CLOSED). Pass: the
  attach fails and its carrier is closed, the lane stream is aborted, and
  the baselines of Section 4.7 show no lane state and no Lane Key entry
  left.
- **N13.** One pipe over an in-memory multi-stream carrier that reports no
  unsent count (so the limiter paces it), virtual clock, 8 data lanes, one
  bulk stream direction bound to each, the limiter's rate fixed so that a
  round lets through fewer batches than there are data lanes. Pass: over 2
  s every direction carries at least half of an equal share of the bytes,
  and a 16 KiB stream opened during the transfer has its first byte written
  within 9 rounds that let a batch through (one per data lane, plus one).
- **N14.** One pipe over an in-memory multi-stream carrier that reports no
  unsent count, virtual clock, round trip 100 ms, two data lanes each
  carrying a bulk stream direction; the test can hold back the delivery of
  chosen records of a lane (a lost packet) while the other lane is
  acknowledged, and reads the limiter's bytes in flight through a test
  hook. Pass, in start-up: the bytes in flight leave out every held byte
  (acknowledged on lane 2 behind a hold on lane 1). Pass, after start-up:
  while a hold on lane 1 lasts 50 ms, the bytes in flight include the
  bytes lane 2 acknowledged behind it; when lane 1 is held without a gap
  for 1 s (each repaired record followed by a held one), the bytes in
  flight leave out the smallest held count of the last RTT_base_p, and the
  pipe carries at least 90 % of R_p over that second.
- **N15.** Review item: every adapter that wraps a carrier's pipe I/O keeps
  the identity of the carrier connection, so that an inbound lane on a
  wrapped carrier is accepted.

---

### 4.10 Listener entry, admission and preamble tests

Each implementation tests, through its real entry points:

- **One admission entry.** With an admission configured on the listener,
  a carrier through each entry point the implementation offers (accept
  loop over TCP and TLS, every HTTP carrier form, WebSocket, QUIC, a
  direct `HandlePipe`), a join and a resumption included, reaches the
  decision function exactly once; no public entry point delivers a carrier
  to the listener without it.
- **Order.** A carrier whose source has no free slot is closed before its
  TLS handshake starts; the slot is held during the handshake, the peek and
  the decision (a source at `MaxPendingPerSource` with carriers in each of
  these steps refuses the next one); the inbound transform runs only after
  Accept; nothing is written on the carrier before Accept.
- **Asynchronous decision.** A decision that has not returned by the end of
  the peek timeout closes the carrier and counts `peek_timed_out`; a
  decision pending when the listener stops taking carriers is cancelled
  (the function observes the cancellation) and counts `closed`; errors and
  out-of-range k count `closed`. With an injected clock (where the language
  offers one), these timers follow it.
- **Statistics.** For a mix of verdicts and failed handshakes, the
  `AdmissionStats` sum equals the carriers that held a source slot,
  `rejected` counts no admission verdict, and a failed handshake under an
  admission counts in `handshake_failed` and not in `setup_failures`.
- **Stall limit.** With `MaxPendingPerSource` = M, the M-th concurrent Stall
  from one source is closed at once and counts `stall_overflow`; a new
  carrier from that source still gets a slot.
- **Source.** Behind a trusted intermediary, the decision's source, the
  `JoinHint` source and the per-source bound all use the reported client
  address; from an untrusted peer the report is ignored; two IPv6 sources in
  one /64 share the per-source bound.
- **Closing.** During `Shutdown`, a new session's carrier is refused
  (`refused`), while a join and a resumption of a running or suspended
  session are accepted until the sessions have ended or the context ends;
  after `Close`, every carrier is refused, joins and resumptions included,
  the accept loops have returned and pending decisions are cancelled.
- **Unknown joins.** With the listener answering unknown joins, a
  resumption join to a restarted listener (same instance identifier, no
  state) receives the fixed close and the suspended client session ends
  with ZMUX_RESUME_FAILED well before its grace period; a malformed join, a
  bad token, a replay, a join refused by the admission and a locator with
  another instance identifier still get no byte; without the option an
  unknown locator gets no byte either. A non-suspended session whose join
  gets the close keeps running.
- **Host value.** An Accept decision's value reaches the inbound
  transform, the session (from its bootstrap, the uplink half of a split
  one) and every pipe (from the carrier of its join), unchanged by
  resumption; it is absent without an admission and ignored on other
  verdicts; a join whose value differs is still accepted by zmux.
- **QUIC connection entry.** A connection handed in through the
  single-connection entry passes the admission once and is counted as one
  accepted by the listener's accept loop is.
- **Preamble size.** A preamble setting beyond the peer's Maximum Preamble
  Size (or, before the peer's parameters, the remembered value or 1024)
  fails at once with the preamble-size error; when the limit known at
  identifier assignment is smaller than the one checked at setting, only
  that stream fails, locally, and no frame of it is sent.

## 5. Invalid input

Every condition below MUST be detected and MUST produce the listed result. The
corpus entries in `fixtures/wire/invalid.ndjson` and the traces cover each
row.

Each corpus line is a JSON object with `id`, `kind`, `gates` (the features
and announcements in use, by transport-parameter name), `note`, and for
invalid entries `error` (the transport error). An entry may also give
`limits`, sized gates such as `{"max_datagram_frame_size": 12}` (the value
the receiver advertised). Valid entries are minimally encoded and re-encode
to the same bytes, except those marked `"non_minimal": true`, which must be
accepted and re-encode under the semantic equality of Section 4.1 to the
minimal encoding of their `frame`:

- `frame`: `hex` is one frame;
- `record`: `hex` is a byte stream of records that are not the first record
  of a bootstrap pipe;
- `messages`: `messages` is a list of messages of a message-framed carrier
  (SPEC.md Section 3.4), each one record's Frames field; valid entries also
  give `positions`, the bytes each message counts for in pipe positions.

The receiver's own `max_record_size` is 65535 unless an entry gives
`max_record_size`. A carrier that ends inside a record is a pipe failure,
not a corpus error; traces cover it.

### 5.1 Records and frames

| Condition | Result |
|---|---|
| record Size 0 (not the first record of a bootstrap pipe, see 5.2) | FRAME_ENCODING_ERROR |
| record Size above the local `max_record_size` (detected from the Size field alone; not the first record) | FRAME_ENCODING_ERROR |
| last frame truncated at the record end; any truncated field; a length field larger than the remaining record | FRAME_ENCODING_ERROR |
| a pipe ends inside a record, even gracefully | single pipe: session ends (pipes lost); multipath: the record is discarded and the pipe fails |
| message-framed carrier: message longer than `max_record_size` | FRAME_ENCODING_ERROR (corpus `message-oversize`; trace `core.records.message_oversize`) |
| message-framed carrier: empty message | accepted as a no-op, not chargeable, like a PADDING-only record (corpus `message-empty-no-op`, valid; traces `core.records.message_empty` and `core.records.message_empty_not_charged`) |
| frame type encoded in more bytes than necessary | PROTOCOL_VIOLATION |
| unknown frame type | FRAME_ENCODING_ERROR |
| PING, ACK, CRYPTO, NEW_TOKEN, NEW_CONNECTION_ID, RETIRE_CONNECTION_ID, PATH_CHALLENGE, PATH_RESPONSE, HANDSHAKE_DONE | FRAME_ENCODING_ERROR |
| a zmux frame whose feature is not in use; RESET_STREAM_AT without having advertised it | FRAME_ENCODING_ERROR |
| DATAGRAM without having advertised `max_datagram_frame_size`, or a well-formed DATAGRAM larger than advertised (corpus `datagram-above-limit`) | PROTOCOL_VIOLATION |
| DATAGRAM whose Length runs past the record, even when it also exceeds `max_datagram_frame_size` (corpus `datagram-overrun-above-limit`: decode errors take precedence, SPEC.md Section 10.3) | FRAME_ENCODING_ERROR |
| variable-length integer fields (not frame types) encoded in more bytes than necessary | accepted (corpus `max-stream-data-non-minimal`) |
| STREAM offset plus length above 2^62 - 1 | FRAME_ENCODING_ERROR |
| MAX_STREAMS, STREAMS_BLOCKED, GOAWAY value above 2^60 (stream counts: a stream ID has 62 bits, two of them for the type) | FRAME_ENCODING_ERROR |
| RESET_STREAM_AT Reliable Size above Final Size | FRAME_ENCODING_ERROR |
| PRIORITY_UPDATE reserved bits set | FRAME_ENCODING_ERROR |
| PIPE_JOIN that does not decode (locator length 0 or above 16, direction 3, reserved flag 0x10 to 0x80 set, a missing Priority field) on a pipe that belongs to the session | FRAME_ENCODING_ERROR (a first PIPE_JOIN processed by a listener only closes the carrier, 5.5) |
| PIPE_ACK with no entry; PIPE_ACK whose entries are not in strictly increasing Pipe ID order (including a repeated pipe) | FRAME_ENCODING_ERROR |
| PIPE_STATUS Status other than 0 or 1, or a frame without the Priority field (every Priority value is valid; corpus `pipe-status-priority-max`) | FRAME_ENCODING_ERROR (corpus `pipe-status-invalid`, `pipe-status-missing-priority`) |

### 5.2 Establishment and parameters

| Condition | Result |
|---|---|
| first bytes that cannot begin a QX_TRANSPORT_PARAMETERS record (legacy zmux format, HTTP, TLS on a plain socket; a first record whose Size is below 9 or above the local `max_record_size`) | PROTOCOL_VIOLATION as soon as the mismatching byte arrives, after the local QX_TRANSPORT_PARAMETERS |
| QX_TRANSPORT_PARAMETERS anywhere but first, or on a joined pipe | PROTOCOL_VIOLATION; TRANSPORT_PARAMETER_ERROR when its parameter block is also not valid, whether malformed or containing a duplicate, prohibited or out-of-range parameter (corpus `record-misplaced-tp-malformed`, `record-misplaced-tp-duplicate`; decode errors first, SPEC.md Section 10.3) |
| duplicate parameter; prohibited QUIC parameter; malformed value | TRANSPORT_PARAMETER_ERROR |
| `max_record_size` below 16382; `initial_max_streams_*` above 2^60 | TRANSPORT_PARAMETER_ERROR |
| `reset_stream_at`, `zmux_goaway`, `zmux_priority`, `zmux_datagram_flows` with a non-empty value | TRANSPORT_PARAMETER_ERROR |
| `zmux_role` with an invalid role, a missing or extra nonce | TRANSPORT_PARAMETER_ERROR |
| `zmux_preamble` without `reset_stream_at`, below 1, above `max_record_size` - 32 or above the sender's stream windows | TRANSPORT_PARAMETER_ERROR |
| `zmux_multipath` malformed or with a locator above 16 bytes | TRANSPORT_PARAMETER_ERROR |
| a companion parameter (`zmux_ack_threshold`, `zmux_max_ack_delay`, `zmux_max_lanes`) without `zmux_multipath` or `zmux_pipe_ack`; companion values out of range, `zmux_max_lanes` 0 included | TRANSPORT_PARAMETER_ERROR |
| role conflict | ZMUX_ROLE_CONFLICT |

### 5.3 Streams and flow control

| Condition | Result |
|---|---|
| stream opened at or above the advertised limit (by any frame) | STREAM_LIMIT_ERROR |
| frame for an unopened local stream; frame in the wrong direction | STREAM_STATE_ERROR |
| changed error code in a repeated reset | STREAM_STATE_ERROR |
| changed final size; data beyond the final size | FINAL_SIZE_ERROR |
| stream or session data beyond the advertised limit, including through a final size | FLOW_CONTROL_ERROR |
| out-of-order or repeated STREAM data outside multipath | PROTOCOL_VIOLATION |
| BLOCKED frame above any limit ever advertised | PROTOCOL_VIOLATION |
| QX_PING request not above the previous; response above the largest request sent | PROTOCOL_VIOLATION |
| no-effect budget exhausted | ZMUX_EXCESSIVE_LOAD |

### 5.4 Negotiated features

| Condition | Result |
|---|---|
| preamble split across frames, above the limit, malformed TLV, repeated OPEN_INFO or PRIORITY, bad PRIORITY TLV | PROTOCOL_VIOLATION |
| opener's stream with final size 0, plain RESET_STREAM, or Reliable Size below the preamble, without a local STOP_SENDING | PROTOCOL_VIOLATION |
| PRIORITY_UPDATE for an unopened local stream or for the sender's own unidirectional stream | STREAM_STATE_ERROR |
| datagram flow naming a unidirectional stream, an unopened local stream or a stream beyond the limit; truncated flow prefix | PROTOCOL_VIOLATION |
| GOAWAY that raises a watermark; PRIORITY_UPDATE with an old sequence number | ignored (chargeable outside multipath sessions) |

### 5.5 Multipath and resume

| Condition | Result |
|---|---|
| unauthenticated join: malformed, unknown locator, bad token, pending-join and per-source limits, no first record within the first-record timeout | carrier closed without any response; session unaffected; a bad token is reported as `JoinAuthFailed`; the one exception is the next row |
| admitted, well-formed join whose locator begins with the listener's instance identifier and names no session, with the listener answering unknown joins | the bytes `07 1c 8f 05 f7 4e 00 00` (message framing: the 7-byte message without the first byte), after the listener's QX_TRANSPORT_PARAMETERS if it sent them, then the carrier ended; counted as `rejected`. Off (the default), or for any other locator: no response |
| CONNECTION_CLOSE(ZMUX_RESUME_FAILED) as the first record on a JOINING attachment | a SUSPENDED session closes at once with ZMUX_RESUME_FAILED, locally; otherwise only the attachment fails and further joins back off |
| authenticated join whose pipe ID has attachment state, has its replay bit set or is stale (MULTIPATH.md Section 4.3) | carrier closed, counted in `join_replays`; session unaffected |
| authenticated join with the wrong parity | PROTOCOL_VIOLATION |
| authenticated join beyond the cap of unretired attachments, outside suspension | carrier closed, PIPE_ABANDON(ZMUX_PIPE_RESOURCE_LIMIT); session unaffected |
| PIPE_ACK, PIPE_ABANDON or PIPE_STATUS for a pipe ID of the receiver's own parity that it has not taken | PROTOCOL_VIOLATION |
| PIPE_ACK position above the bytes sent, including any position above 0 for a pipe the receiver does not send on | PROTOCOL_VIOLATION; ZMUX_RESUME_FAILED with resume in use |
| PIPE_JOIN not first on its pipe | PROTOCOL_VIOLATION |
| PIPE_ACK entries whose (Pipe ID, Lane) do not increase strictly | FRAME_ENCODING_ERROR |
| PIPE_ACK position above 0 on a lane the receiver has not opened | PROTOCOL_VIOLATION |
| new carrier streams of a multi-stream connection beyond the pending-carrier or per-source bounds (MULTIPATH.md Sections 5.5 and 5.6): on a connection that carries no OPEN inbound pipe of the endpoint (none yet, only carriers before attachment, or only pipes that have failed or ended); beyond one stream awaiting its first record per OPEN inbound pipe on the connection; or classified as a join or a new session | each takes a slot: refused or evicting as any unauthenticated carrier; only streams admitted for a lane stay outside the bounds, with the first-record timeout |
| a new carrier stream whose PIPE_LANE carries an unknown Lane Key, or names a pipe without a live inbound direction whose lane 0 is on the same connection | the stream closed; session unaffected |
| PIPE_LANE with Lane 0 or at least the receiver's `zmux_max_lanes`, a lane number already opened on the pipe, PIPE_LANE on lane 0 or not first on its lane | PROTOCOL_VIOLATION (FRAME_ENCODING_ERROR when lanes are not available toward the receiver or the frame does not decode) |
| a lane's first record holding a frame other than PIPE_LANE and PADDING | PROTOCOL_VIOLATION |
| an inbound lane that, after its second record, ends inside a record, is reset or fails | the pipe fails (outside multipath, the session ends, SPEC.md Section 6.6); before its second record: no event |
| the writer's own lane closed or reset before it is confirmed | refused: no failure, nothing reinjected, no further lane on that pipe |
| QX_TRANSPORT_PARAMETERS leading the joiner's inbound direction of a new attachment | discarded, not counted in the position |
| outdated PIPE_ACK, entries with Position 0, duplicate STREAM data, outdated limits, PIPE_STATUS with an old sequence number or for a pipe ID of the peer's parity without attachment state, PIPE_ABANDON for such an ID outside the replay window or with its bit set | ignored, never chargeable |

---

## 6. Interoperability with QMux implementations

Plain-QMux interoperability is a hard invariant (SPEC.md Section 13).

### 6.1 Targets

| Implementation | Status | Use |
|---|---|---|
| Rust `qmux` crate 0.6.2 (moq-dev/web-transport), draft-02 | primary target; required for every release | full matrix of Section 6.2 |
| shiguredo/webtransport-py (sans-IO, Python + C++ dwnx core) | required where practical (it has no DATAGRAM, reset or stop) | streams, flow control, close |
| okdaichi/qmux-go v0.3.0 | optional | streams, datagrams, QX_PING; no RESET_STREAM_AT |
| ngtcp2/dwnx (C) | optional once released | streams |
| Apache Traffic Server (HTTP/3 over QMux, ALPN `h3qx-01`) | optional; needs an HTTP/3 layer on zmux and an ATS build with a QMux-enabled quiche fork | HTTP/3 requests |

There is no Java QMux implementation; the Java library is covered through
its interoperability with the Go and Rust libraries.

### 6.2 Test matrix

For each target, with zmux as client and as server, over TCP and over TLS
(ALPN `qmux-02`, and `qmux-01`/`h3qx-01` where the target uses draft-01
identifiers). Interoperability drivers configure `max_incoming_uni_streams`
explicitly: with the default staged credit (SPEC.md Section 8.5 rule 4) a
peer can open only 16 unidirectional streams before the zmux side accepts
one, which tests written for QUIC defaults may exceed. Reset tests that rely on
RESET_STREAM_AT delivering data below the Reliable Size are skipped against
targets that treat it as a plain reset (SPEC.md Section 13.2, "Known peer
behaviour"):

1. establishment: both QX_TRANSPORT_PARAMETERS frames, GREASE and zmux
   parameters ignored by the target, features reported as "none" on the zmux
   side;
2. bidirectional and unidirectional streams opened by either side, FIN,
   half-close, 1 GiB transfers with window growth;
3. RESET_STREAM, STOP_SENDING and, where supported, RESET_STREAM_AT, with
   error codes above 2^32 where the target allows them;
4. flow control at zero window, stream-count credit exhaustion and recovery;
5. QX_PING keep-alive and the target's RTT pings;
6. DATAGRAM, where supported, at the largest size each side allows;
7. CONNECTION_CLOSE with transport and application codes in both directions,
   and the target's own error codes (the `qmux` crate uses transport code
   1002 for its violations);
8. idle timeout with both values;
9. WebSocket carriers with the `Records` framing used by the `qmux` crate and
   qmux-go, with both subprotocol forms (`qmux-02.<application>` and an
   application identifier used directly, SPEC.md Section 6.2), and empty
   messages from the target accepted as no-ops (SPEC.md Section 3.4);
10. the `qmux` crate's `application_protocols` parameter, with zmux's
    optional hook enabled and disabled;
11. zmux as client with `early_data` wrongly set toward the target (SPEC.md
    Section 6.1.2): the target closes at the PIPE_ACK fence with
    FRAME_ENCODING_ERROR, its application sees no stream, and the zmux side
    reports `EarlyDataRejected`. This is the only test in which the target
    receives a zmux frame.

Pass criteria: no protocol error on either side and every byte delivered (test 11
aside, whose pass condition is its own), the
zmux side's `no_effect_budget` statistic (API.md Section 7) never drops below
4000 tokens under the target's traffic (the target sends no chargeable frames
beyond its pings and empty messages), and a packet capture of the zmux side shows no zmux frame
type, no PIPE_ACK and no out-of-order STREAM data (SPEC.md Section 13.2),
except the fence of test 11.

### 6.3 Placement

Every implementation's CI runs an interoperability smoke test against the
`qmux` crate and the Go library; the full matrix runs nightly.

---

## 7. Release gates

A release of any implementation requires:

1. CI green: unit tests, the trace runner over all traces, the wire corpus,
   fuzz smoke, and the race and concurrency checks of Section 4.7 (Go
   `-race`, Rust `loom` or equivalent, Java jcstress or equivalent, the
   Python free-threaded stress tests);
2. the interoperability smoke test (Section 6.3) and the nightly matrix
   without failures;
3. performance gates (IMPLEMENTATION.md Section 9).
