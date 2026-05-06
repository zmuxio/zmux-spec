# zmux Specification

This repository defines the `zmux` protocol family.

`zmux` is a **single-connection** multiplexer for reliable, ordered,
full-duplex byte streams. Each bidirectional `zmux` stream is intended to
behave like a full-duplex connection-style byte stream, including graceful
half-close and abortive reset. The documents in this repository define the
core wire protocol, registries, extension rules, implementation guidance, and
test assets for that single-connection core.

`zmux` does not remove head-of-line blocking already present in the underlying
transport. Over one underlying byte stream, transport-level HOL remains. The
goal of `zmux` is to avoid adding extra mux-level HOL on top of that
transport.

`zmux` also does **not** define multi-connection session composition.
Multi-connection composition should be implemented above `zmux` by combining
multiple independent `zmux` sessions in a separate project.

## Recommended Reading Order

For understanding and implementation work, read the Markdown set in this
order:

1. [ARCHITECTURE.md](./ARCHITECTURE.md)
   What belongs in `zmux`, what stays out, and how same-version features are
   added.
2. [SPEC.md](./SPEC.md)
   Normative wire protocol and lifecycle rules.
3. [REGISTRY.md](./REGISTRY.md)
   Stable numeric assignments and defaults.
4. [STATE_MACHINE.md](./STATE_MACHINE.md)
   Compact half-state transition reference for implementers.
5. [CONFORMANCE.md](./CONFORMANCE.md)
   Validation targets and release-claim gates.
6. [WIRE_EXAMPLES.md](./WIRE_EXAMPLES.md)
   Byte-level examples for codecs and tests.
7. [IMPLEMENTATION.md](./IMPLEMENTATION.md)
   Non-normative implementation guidance and readiness order.
8. [API_SEMANTICS.md](./API_SEMANTICS.md)
   Non-normative cross-language binding guidance.

## Machine-readable assets

The repository also includes non-normative machine-readable assets derived from
the normative documents:

- [assets/registry.json](./assets/registry.json)  
  Stable numeric assignments and defaults in machine-readable form.
- [assets/registry.yaml](./assets/registry.yaml)  
  YAML export of the stable machine-readable registry.
- [assets/manifest.json](./assets/manifest.json)  
  Inventory of the machine-readable asset set.
- [assets/wire_corpus.json](./assets/wire_corpus.json)  
  Small valid and invalid wire examples for codec tests.
- [assets/state_corpus.json](./assets/state_corpus.json)  
  Representative stream-state scenarios for behavior tests.
- [assets/invalid_corpus.json](./assets/invalid_corpus.json)  
  Edge-case invalid inputs and expected outcomes for parser and state-machine
  tests.
- [assets/golden_cases.json](./assets/golden_cases.json)  
  Unified generated test-case view built from the core asset corpora.
- [tools/validate_assets.py](./tools/validate_assets.py)  
  Local validation script for the machine-readable assets.
- [tools/build_golden_cases.py](./tools/build_golden_cases.py)  
  Generator for the unified golden test-case view.
- [tools/export_registry_yaml.py](./tools/export_registry_yaml.py)  
  Deterministic exporter from `assets/registry.json` to `assets/registry.yaml`.
- [tools/export_fixture_bundle.py](./tools/export_fixture_bundle.py)  
  Exporter that turns the unified golden cases into sharded fixture files for
  test harnesses.
- [tools/build_case_sets.py](./tools/build_case_sets.py)  
  Generator for fixture groupings such as codec-only,
  flow-control, `session_lifecycle`, `open_metadata`, or `priority_update`.
- [tools/rebuild_assets.py](./tools/rebuild_assets.py)  
  Serial rebuild script for all generated assets and fixture bundles.
- [fixtures/index.json](./fixtures/index.json)  
  Generated fixture-bundle index for harness consumption.
- [fixtures/case_sets.json](./fixtures/case_sets.json)  
  Generated grouping of fixture IDs by test-suite category.

## Implementation usage notes

For implementation work, the repository-default order is:

1. run parser and codec tests from `fixtures/wire_valid.ndjson` and
   `fixtures/wire_invalid.ndjson`
2. run stream and session behavior tests from `fixtures/state_cases.ndjson`
3. run policy and edge-case checks from `fixtures/invalid_cases.ndjson`
4. use `fixtures/case_sets.json` to select subsets such as codec-only,
   flow-control, `session_lifecycle`, unidirectional-stream,
   `open_metadata`, or `priority_update`
5. use `tools/rebuild_assets.py` when regenerating derived assets locally
6. use `tools/validate_assets.py` in CI to catch drift in the repository

The generation steps are intentionally ordered. `golden_cases`, the fixture
bundle, and the case sets should be regenerated serially rather than in
parallel so the derived files stay in sync.

See [examples/README.md](./examples/README.md) for a language-agnostic harness
outline, [examples/fixture_mapping.md](./examples/fixture_mapping.md) for a
field-to-assertion mapping guide, and [IMPLEMENTATION.md](./IMPLEMENTATION.md)
for the repository-default build order and readiness gates.

These assets do not override the Markdown specifications. If any discrepancy is
found, the normative Markdown documents take precedence.

## Compatibility Target

The current public compatibility target in this repository is `zmux-v1`:

- `preface_ver = 1`
- `proto_ver = 1`
- single-link `zmux v1`
- `open_metadata`
- `priority_update`
- optional preface and PING/PONG length padding settings
- correct negotiated handling of `priority_hints` and `stream_groups`
- forward-compatible `EXT` envelope parsing and ignore/skip behavior

In this repository, a release claiming `zmux-v1` compatibility is expected to
implement the currently standardized `zmux v1` surface above. Same-version
extension documents still define their own negotiation and validation rules.

The base wire contract still includes:

- session preface parsing and negotiation
- explicit `initiator` / `responder` role negotiation
- `role = auto` negotiation and collision handling
- canonical `varint62` parsing and encoding
- the unified frame codec
- `DATA`
- `MAX_DATA`
- `PING`
- `PONG`
- standardized `ping_padding_key` and `preface_padding` settings
- `STOP_SENDING`
- `BLOCKED`
- `RESET`
- `ABORT`
- `GOAWAY`
- `CLOSE`
- `DATA|FIN`

### Capability and carriage summary

In `zmux v1`, `priority_hints` and `stream_groups` define advisory metadata
semantics. The standardized peer-visible carriage paths for those values are
`OPEN_METADATA` on the first opening `DATA` frame and `priority_update` after
the stream is already open. Those semantic bits do not by themselves imply an
independent carriage path. New deployments SHOULD therefore negotiate
`priority_hints` and `stream_groups` together with at least one standardized
carriage path, unless they intentionally use those semantic bits only for
local metadata surfaces.

Practical dependency matrix:

- `priority_update` without `priority_hints` and without `stream_groups` has no
  standardized semantic effect
- `priority_hints` or `stream_groups` without `open_metadata` and without
  `priority_update` are wire-valid but do not provide peer-visible standardized
  carriage
- `open_metadata` may still be useful on its own for peer-visible `open_info`

Open-time priority, group, and opaque `open_info` values become peer-visible
only when a negotiated carriage path actually carries them. Without
`OPEN_METADATA` on the first opening `DATA` or a negotiated later
`priority_update`, those values are local sender-policy inputs only.

Implementation note: although `OPEN_METADATA` is encoded inside the first
opening `DATA` / `DATA|FIN` payload, its metadata prefix does not consume
stream or session `MAX_DATA`. Only the trailing application-data bytes count
against flow-control windows.

Support for specific standardized `EXT` subtypes is claim-specific. Support for
the `EXT` frame envelope itself is part of forward-compatible core behavior.
The `EXT` envelope does not implicitly open streams in `zmux v1`.

### Protocol boundary

`zmux-v1` compatibility is a protocol claim. It covers the wire format,
preface negotiation, stream-ID ownership, stream lifecycle, flow control,
extension negotiation, error signalling, and forward-compatible parsing rules.
It does not standardize public API names, constructors, default-configuration
surfaces, adapter-specific limits, scheduler internals, buffer-pool strategy,
or keepalive tuning policy.

Protocol-facing behavior that should stay aligned across implementations:

- application `DATA` and new-stream creation begin only after peer preface
  parsing completes, and after role resolution completes when `role = auto`
- `role = auto` is a core establishment mode, while explicit
  `initiator` / `responder` roles remain preferred when a deployment already
  has deterministic endpoint ordering
- graceful send completion, read-side stop, send-side reset, and whole-stream
  abort remain distinct protocol actions mapped to `DATA|FIN`,
  `STOP_SENDING`, `RESET`, and `ABORT`
- a stream ID becomes peer-visible only through the first opening-eligible
  stream-scoped frame, and implementations must not create peer-observable
  gaps or reuse consumed IDs

### Compatibility claims

The document set uses three related naming layers:

| Layer | Names used in this repository | Purpose |
| --- | --- | --- |
| protocol claims | `zmux-wire-v1`, `zmux-open_metadata`, `zmux-priority_update` | declare which standardized wire surfaces an implementation claims |
| protocol compatibility profile | `zmux-v1` | summarizes public protocol compatibility breadth |
| non-protocol guidance profiles | `zmux-api-semantics-profile-v1`, `zmux-stream-adapter-profile-v1`, `zmux-reference-profile-v1` | document optional local binding, adapter, and reference-policy behavior |
| negotiated capability bits | `priority_hints`, `stream_groups`, `open_metadata`, `priority_update` | control on-wire semantics and carriage paths during negotiation |

Protocol claims are made separately for:

- `zmux-wire-v1`
- `zmux-open_metadata`
- `zmux-priority_update`

Separate claims remain useful for incremental bring-up, targeted testing, and
partial internal milestones. Public protocol compatibility and release claims
should use `zmux-v1`:

- `zmux-v1`: implements the currently standardized `zmux v1` surface in this
  repository, including the base wire contract, `open_metadata`,
  `priority_update`, and the correct negotiated handling of `priority_hints`
  and `stream_groups`
- `zmux-reference-profile-v1`: optional non-protocol profile for
  implementations that also follow the repository-default binding, sender,
  memory, liveness, and scheduling guidance documented in
  [API_SEMANTICS.md](./API_SEMANTICS.md) and
  [IMPLEMENTATION.md](./IMPLEMENTATION.md)
