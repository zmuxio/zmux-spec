# zmux Architecture

This document defines the architectural boundary of the `zmux` protocol
family.

Its purpose is to keep one place authoritative for:

- what belongs in the single-link core
- how optional same-version behavior is added
- which compatibility levers exist
- what stays outside `zmux` entirely

It does not restate the wire format. Use it together with
[SPEC.md](./SPEC.md) and [REGISTRY.md](./REGISTRY.md).

## 1. Core protocol scope

Core `zmux` covers only generic multiplexing semantics over **one** reliable,
ordered, full-duplex byte stream:

- session preface and negotiation
- frame layout
- stream ID ownership
- stream lifecycle
- session lifecycle
- flow-control semantics
- core error signaling
- capability and extension-handling rules

Core `zmux` must remain valid on any reliable, ordered, full-duplex byte
stream. That bearer may be chosen by the deployment, but `zmux` itself
standardizes only one bearer per session.

## 2. Extension mechanisms

When extending `zmux`, use the following mechanisms in this order:

1. container-scoped TLVs for skippable metadata or advisory values
2. capability bits for optional same-version behavior requiring peer opt-in
3. `EXT` subtypes for dedicated extension entry points
4. extension documents for optional same-version behavior that still preserves
   the core contract
5. a new `proto_ver` for changes to the core interoperability contract
6. a new `preface_ver` for changes to the session preface itself

Capabilities and TLVs MUST NOT be used to hide changes that alter the
mandatory wire contract of the current `proto_ver`.

## 3. Document classes

Core document:

- session establishment
- frame format
- stream and session lifecycle
- flow control
- base error signaling

Extension document:

- optional same-version behavior while preserving the base frame model and
  single-connection core contract

Higher-layer document:

- application-level semantics that use `zmux` streams without changing the
  multiplexing contract
- orchestration across multiple independent `zmux` sessions, if a deployment
  needs that above the protocol

## 4. Compatibility levers

`zmux` uses four compatibility levers:

1. `preface_ver`
2. `proto_ver`
3. capability bits
4. TLV and `EXT` space

`preface_ver` changes only when the session preface format or negotiation
structure changes.

`proto_ver` changes when the core interoperability contract changes. Core
contract changes include:

- frame-header changes
- core frame-meaning changes
- core flag-meaning changes on already standardized ungated paths
- flow-control model changes
- stream-ID ownership changes
- mandatory stream or session state-machine changes
- making already advertised `MAX_DATA` limits revocable or reducible

Capabilities are optional same-version behavior switches. Use one when:

- both peers remain within the same `proto_ver`
- the behavior is optional
- the sender depends on peer understanding of that behavior
- a safe fallback exists when the bit is not negotiated

TLV or `EXT` should be used for structured same-version additions that do not
change the core contract.

A same-version capability document MAY also assign meaning to a previously
reserved flag bit on an existing frame type when all of the following hold:

- use of that flag is gated on explicit capability negotiation
- older peers that ignore the capability bit will never be sent the new flag
  combination
- the extension does not change the mandatory baseline meaning of the original
  flagless frame for peers that do not negotiate it

## 5. Registry and retirement rules

Standard documents allocate values from [REGISTRY.md](./REGISTRY.md).

Use shared ranges as follows:

- standard documents use the main assigned ranges
- experiments use experimental ranges
- private deployments use private-use ranges

Experimental and private-use values are not assumed to interoperate outside the
environment that defined them.

Assigned values may become deprecated or retired over time, but their numeric
assignments remain reserved.

Within one registry namespace:

- deprecation warns new implementations away from a feature
- retirement means the feature should not be used in new deployments
- neither state permits silent reuse of the same numeric value for different
  semantics

An optional capability or extension feature may be retired from the active
standard surface without forcing a new `proto_ver`, provided that:

- the mandatory core interoperability contract is unchanged
- the retired numeric assignments remain reserved
- older peers can still ignore the absence of that optional behavior safely

## 6. Local implementation policy

Some behavior should remain local policy rather than standardized wire
behavior.

Local-policy examples are:

- batching thresholds
- scheduler internals
- fairness heuristics
- ping cadence and keepalive jitter
- buffer-pool strategy
- local observability surfaces
- collision-retry policy for `role = auto`
- provisional local-open time limits and hard caps
- tombstone compaction and retention policy
- hidden control-opened churn detection thresholds
- accept-queue notification coalescing strategy
- lifecycle event surface and handler invocation model

These affect performance and traffic shape, but they are not part of the base
interoperability contract.

## 7. What belongs in core

The following belong in the base protocol:

- compact unified framing
- canonical integer encoding
- connection-style stream semantics
- typed stream-ID ownership
- implicit stream open on the first opening-eligible stream-scoped core frame
- `DATA|FIN` half-close
- `PING`
- `PONG`
- `BLOCKED`
- `STOP_SENDING`
- `RESET`
- `ABORT`
- `GOAWAY`
- `CLOSE`
- session and stream receive windows
- immutable session parameters after preface
- unilateral receive limits
- stable numeric error codes
- skippable TLV metadata
- capability negotiation for optional same-version behavior

## 8. What belongs in extension documents

The following do not belong in the base core specification, but they may still
belong in extension documents within the `zmux` repository:

- additional advisory scheduling metadata
- optional same-version metadata carriage
- capability-gated behavior that does not change the mandatory core contract

Extension documents for `zmux v1` must not redefine core stream-opening rules.
In particular, they must not make `EXT` implicitly open a stream, and they
must not invent new first-frame behaviour for previously unseen peer-owned
stream IDs unless a future `proto_ver` explicitly changes the core opening
contract.

The current active standardized optional same-version metadata surfaces are
`open_metadata` and `priority_update`.

## 9. What stays out of zmux

The following are outside the `zmux` protocol boundary:

- multi-connection composition
- connection bundling, pooling, or hot-plug orchestration
- connection-attempt retry orchestration beyond one failed session-establishment
  attempt
- shared logical sessions across multiple bearers
- retransmission
- cross-link reordering recovery
- a second congestion-control algorithm above the transport
- packet-boundary preservation semantics
- automatic stream ID reuse within one session
- post-preface mutation of core session limits
- transport-family-specific branches in the core wire protocol

These either belong in a higher-layer project or remain outside `zmux`
entirely.

## 10. Admission checklist

Before standardizing a new feature, ask:

1. Is it part of generic multiplexing semantics?
2. Does it remain valid on any reliable, ordered, full-duplex byte stream?
3. Can older peers safely ignore it within the same `proto_ver`?
4. Does it require a new document class instead of a core change?
5. Does it simplify interoperability more than it complicates the protocol?
6. Does it solve a problem inside one `zmux` session rather than orchestrating
   multiple independent `zmux` sessions above the protocol?

If the answer is unclear, the feature should not be added to the core
specification.

## 11. Current standardization target

The current protocol family target is:

- core single-link `zmux v1`
- `open_metadata`
- `priority_update`

The architectural split between base wire rules and same-version extensions
remains important, but this repository's public compatibility target is the
complete current `zmux-v1` surface above rather than separate public `core`
and `full` release tiers.

The current target does not include multi-connection session aggregation,
protocol retransmission, or protocol reordering recovery.
