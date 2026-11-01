# zmux Architecture

Status: informative, with the extension policy of Section 5 binding on
future revisions.

---

## 1. Scope

zmux is a session protocol and a family of libraries for multiplexing
flow-controlled byte streams and datagrams over reliable byte pipes:

- one session over one bidirectional carrier (TCP, TLS, Unix socket,
  WebSocket, QUIC stream, in-memory pipe), where zmux is wire-compatible with
  QMux and costs nothing beyond it;
- one session over many pipes, each unidirectional or bidirectional, from
  different carriers (for example HTTP uplinks and downlinks, several TCP
  connections over different interfaces), with per-frame spreading,
  transparent failover and dynamic addition and removal;
- one session that outlives all its pipes for a grace period (resume);
- sessions nested in sessions: a stream of one session as a pipe of
  another, so that an end-to-end session can run over direct carriers and
  relayed streams at once and move between them (EXTENSIONS.md Section 10).

Out of scope:

- running directly on unreliable datagrams (use QUIC);
- congestion control, loss-recovery timers and packet protection: carriers do
  this, and doing it again inside zmux would cause TCP-over-TCP effects;
- rate limiting, byte budgets and quotas: a general limiter wraps the
  standard stream interfaces, and zmux offers only exact byte counters and
  limiter hooks where no wrapper can reach (API.md Section 7.1);
- header compression, dependency-tree priorities, settings changed in the
  middle of a session, datagram fragmentation over reliable pipes;
- interoperability with the legacy zmux format, which fails at the first bytes.

---

## 2. Layering

```text
+------------------------------------------------------------------+
| application: streams, datagrams, open info, priorities           |
+------------------------------------------------------------------+
| zmux session (SPEC.md, EXTENSIONS.md)                             |
|   QUIC v1 stream semantics: offsets, final sizes, absolute limits |
|   GOAWAY, preamble, PRIORITY_UPDATE, datagram flows, roles        |
+------------------------------------------------------------------+
| zmux pipe layer (MULTIPATH.md, RESUME.md)                         |
|   pipe IDs, positions, PIPE_ACK, failure detection, reinjection,  |
|   joins and their security, suspension and resume                 |
|   sender policy (SCHEDULER.md): estimators, policies, tiers,      |
|   probation, budgets; local, invisible on the wire                |
|   allocated and run only once multipath or resume is in use       |
+------------------------------------------------------------------+
| records: QMux-02 Size-prefixed records, one stream per pipe       |
+------------------------------------------------------------------+
| carriers: pluggable adapters, each a read half and/or a write half|
+------------------------------------------------------------------+
```

The libraries implement both zmux layers in one sans-IO core per session
(IMPLEMENTATION.md Section 2); drivers only move bytes between carriers and
the core.

Inside the core the two zmux layers meet at a narrow internal interface of
about nine calls (IMPLEMENTATION.md Section 2.6): the stream layer reports
what it sent and offers chunks in priority order; the pipe layer reports
what was acknowledged or lost and decides where each chunk goes. Multipath
is negotiated at runtime, never removed at build time: every build contains
it, and a session that does not negotiate multipath or resume allocates none
of the pipe layer's state and takes one branch per hot path to skip it, so a
single-pipe session costs the same as a library without multipath
(CONFORMANCE.md Section 4.6 measures this). The protocol documents follow
the same boundary: MULTIPATH.md and RESUME.md say what goes on the wire and
how each endpoint reacts, SCHEDULER.md how a sender chooses among its pipes,
which no peer can observe.

---

## 3. Relationship to QUIC and QMux

- **Semantics from QUIC.** Frames that travel over several pipes arrive out
  of order, duplicated or not at all when a pipe breaks; QUIC's stream
  semantics were designed for exactly that. Using them unchanged means zmux
  needs no ordering layer of its own and no session-wide sequence numbers
  that would let one slow pipe block every stream.
- **Encoding compatible with QMux.** zmux keeps the QMux-02 record format and
  QUIC v1 frame encodings. There is one wire encoding, not a "native" one
  beside a compatible one.
- **zmux is its own protocol.** It does not depend on QMux being
  standardised; it pins QMux-02 and aligns once when QMux becomes an RFC
  (SPEC.md Section 13.5). zmux-only behaviour is negotiated with transport
  parameters; when the peer is a plain QMux endpoint none of it is used, and
  the session is a correct QMux session (the hard invariant of SPEC.md
  Section 13). Where zmux needs and QMux alignment conflict, zmux needs win,
  subject to that invariant.
- **Interoperability targets.** The Rust `qmux` crate first, then the other
  QMux implementations (CONFORMANCE.md Section 6).

---

## 4. Design rules

1. **Single pipe is the cheapest case.** Without multipath: no retained
   data, no reordering structures, and acknowledgements only for the
   sender's queue control (pipe acknowledgement, MULTIPATH.md Section 3.5;
   SPEC.md Section 9.6).
2. **Every frame is idempotent.** Duplicates and stale copies are harmless,
   so failover can re-send information without coordination.
3. **Peer violations are session errors.** No stream-level punishment that an
   attacker can turn into resource churn (MadeYouReset).
4. **Every peer-controlled resource is bounded by credit or by a fixed
   constant** (SPEC.md Section 9.8). Responses are coalesced state, never
   queues.
5. **Abuse detection never punishes compliant traffic.** The no-effect budget
   charges only a closed list of frames and refills with progress (SPEC.md
   Section 9.4).
6. **Normative behaviour lives in the normative documents.** Anything that
   affects interoperability (liveness, budgets, retry semantics, error
   precedence) is in SPEC.md and its siblings, not in implementation guides.
7. **Executable traces are the conformance contract.** Every implementation
   runs the same traces (TRACES.md).
8. **Concurrency safety by construction.** A session's state changes under
   one lock that is never held across I/O, application code (hooks and
   policies included) or a blocking wait; wake-ups and callbacks run after it is released, and a blocking
   call from a thread that drives sessions fails instead of deadlocking;
   every queue is
   bounded; every exit path, panics and exceptions included, releases what
   it holds and wakes every waiter; no peer input can crash or hang an
   endpoint. IMPLEMENTATION.md Section 2.5 states the rules and
   CONFORMANCE.md Section 4.7 the tests that check them.

---

## 5. Extension policy

### 5.1 What counts as an extension

Anything that adds a frame type, a transport parameter, a preamble TLV type,
an error code, or changes the behaviour of an existing element.

### 5.2 How an extension is added

1. **Specify** it in a document of this repository (a new section of
   EXTENSIONS.md for small features, its own document otherwise) with:
   wire format, negotiation, sender and receiver rules, error handling,
   interaction with multipath (retransmission rule, ack-elicitation,
   duplicates and reordering), interaction with resume, the bounded response
   state it creates, and its classification for the no-effect budget.
2. **Allocate** codepoints with the method of REGISTRY.md Section 2.2 and add
   them to `tools/zmuxwire/registry.py`.
3. **Gate** it on a transport parameter. A frame of the extension is sent only
   when the feature is in use (SPEC.md Section 5.5). This preserves plain-QMux
   interoperability and the "unknown frame is fatal" rule.
4. **Test** it: wire-corpus entries, WIRE_EXAMPLES entries, and traces,
   including invalid input and the behaviour towards peers without the
   extension.
5. **Run** `python3 tools/rebuild_assets.py && python3 tools/validate_assets.py`.

### 5.3 Compatibility rules

- Existing codepoints never change meaning.
- Transport parameters are never renegotiated within a session.
- A feature is in use only when both endpoints announced it, except where the
  feature defines a unilateral capability (as DATAGRAM and RESET_STREAM_AT
  do).
- Reserved bits that must be zero stay zero; new semantics use new
  parameters, not reserved bits.
- GREASE (SPEC.md Section 9.7) keeps the extension points open.

### 5.4 Versioning

zmux is identified by its first bytes (QX_TRANSPORT_PARAMETERS) and by QMux
ALPN identifiers on TLS. zmux keeps one name and version line:
there is no version field, no version negotiation and no dual stack.
Additive changes are extensions.
A change that alters existing encodings (for example aligning with a
published QMux RFC) is a new protocol version with new ALPN identifiers; a
single implementation version never runs two record formats.

The wire format is frozen as of zmux-go v1.3.2 and zmux-rs, zmux-java and
zmux-python v1.2.5: from these versions on, the wire changes only by
extensions under Sections 5.2 and 5.3, and a change to an existing encoding
is a new protocol version as above. Every entry of an implementation's
change log is marked `[API]`, `[wire]`, both, or `[internal]` for changes
that touch neither; a `[wire]` entry names the specification sections it
follows.

### 5.5 Reserved directions

REGISTRY.md Section 7 lists extension points whose codepoints and frameworks
are fixed now and whose specifications come later: the DEADLINE TLV,
implicit acknowledgements from carriers, the qlog schema and record protection for carriers
without end-to-end TLS. Redundant scheduling of urgent data is part of the
scheduler (SCHEDULER.md Section 4.1), and early data toward a known zmux
peer is part of establishment (SPEC.md Section 6.1.2).

---

## 6. Repository layout

| Path | Content |
|---|---|
| `README.md` | overview and reading order |
| `SPEC.md`, `MULTIPATH.md`, `RESUME.md` | core protocol (normative) |
| `SCHEDULER.md` | multipath sender policy: estimators, scheduling policies, tiers, probation, budgets (normative for zmux implementations, not part of the protocol) |
| `EXTENSIONS.md` | negotiated features (normative) |
| `STATE_MACHINE.md` | state machines (normative summary) |
| `REGISTRY.md`, `assets/registry.json` | codepoints (normative) |
| `CONFORMANCE.md`, `TRACES.md` | conformance requirements and the trace format |
| `WIRE_EXAMPLES.md` | generated byte-exact examples |
| `IMPLEMENTATION.md`, `API.md` | guidance |
| `fixtures/profiles.json`, `fixtures/traces/` | executable traces |
| `fixtures/wire/` | wire corpus |
| `assets/manifest.json` | inventory and checksums of generated assets |
| `tools/` | codec, generators and validator (Python 3.12+, standard library only) |
