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

## Document set

1. [SPEC.md](./SPEC.md)  
   Normative core protocol specification.
2. [REGISTRY.md](./REGISTRY.md)  
   Stable numeric assignments and default values.
3. [ARCHITECTURE.md](./ARCHITECTURE.md)  
   Architectural boundary, extension discipline, and versioning rules.
4. [IMPLEMENTATION.md](./IMPLEMENTATION.md)  
   Repository-default implementation guidance, scheduling profile, and
   implementer checklist for single-link implementations.
5. [CONFORMANCE.md](./CONFORMANCE.md)  
   Interoperability and behavioral validation targets.
6. [STATE_MACHINE.md](./STATE_MACHINE.md)  
   Normative stream- and session-state transition reference.
7. [API_SEMANTICS.md](./API_SEMANTICS.md)  
   Recommended cross-language stream API contract and default stream-adapter
   profile.
8. [WIRE_EXAMPLES.md](./WIRE_EXAMPLES.md)  
   Byte-level examples for codecs and tests.

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

## Current standardized scope

The current standardized protocol surface is:

- `preface_ver = 1`
- `proto_ver = 1`
- core single-link `zmux v1`
- `open_metadata`
- `priority_update`

### Repository-default library-surface defaults

Repository-default library profiles are intentionally stricter than the widest
API surface:

- application `DATA` and new-stream creation begin only after peer preface
  parsing completes, and after role resolution completes when `role = auto`
- repository-default libraries support `role = auto` as part of core
  establishment; higher-level deployment helpers MAY still default to explicit
  `initiator` / `responder` roles on obviously asymmetric client/server paths
- repository-default libraries SHOULD usually hide role choice from ordinary
  callers: helpers built around obvious dial/accept paths should map directly
  to explicit roles, while generic session constructors over an already
  established duplex byte stream should default to `role = auto` unless the
  caller explicitly overrides it
- repository-default API guidance distinguishes semantic operation families
  from any one concrete naming scheme
- bindings MAY expose a stream-style convenience profile, a fuller control
  surface, or both
- repository-default stream-style convenience profiles SHOULD use mainstream
  names such as `Close()`, `CloseRead()`, `CloseWrite()`, and `Reset()`; the
  primary explicit whole-stream abort entry, when exposed in that surface,
  SHOULD carry a numeric code together with optional reason text
- fuller control surfaces MAY expose caller-selected codes and diagnostics on
  `STOP_SENDING`, `RESET`, `ABORT`, `GOAWAY`, or `CLOSE`
- repository-default API surfaces SHOULD keep one primary spelling per
  operation family inside each exposed surface; when a numeric stream
  identifier is exposed, `StreamID()` is the repository-default name
- repository-default bindings SHOULD avoid introducing custom primary stream
  verbs when established stream/connection-style names already express the
  same behavior clearly

## Minimum core interoperability

A minimal core implementation is expected to support:

- session preface parsing and negotiation
- explicit `initiator` / `responder` role negotiation
- `role = auto` negotiation and collision handling
- canonical `varint62` parsing and encoding
- the unified frame codec
- `DATA`
- `MAX_DATA`
- `PING`
- `PONG`
- `STOP_SENDING`
- `BLOCKED`
- `RESET`
- `ABORT`
- `GOAWAY`
- `CLOSE`
- `DATA|FIN`

Core `zmux v1` therefore includes explicit half-close support:

- write-half close by `DATA|FIN`
- read-side stop by `STOP_SENDING` as the directional reader-side close
  control
- send-half abort by `RESET`
- full-stream abort by `ABORT`

Beyond the minimum core interoperability surface, the current document set also
defines:

- parsing the `EXT` frame envelope and ignoring unknown `EXT` subtypes
- first-`DATA` open-time metadata carriage through `OPEN_METADATA`
- priority hints
- stream-group hints
- `priority_update`

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

Repository-default APIs MAY still expose open-time priority or group options
for first-batch scheduling. Those values become peer-visible only when
`OPEN_METADATA` is negotiated and actually used on the first opening `DATA`;
otherwise they remain local sender-policy inputs unless later
`priority_update` frames carry the negotiated advisory metadata. Repository-
default APIs MAY also expose optional opaque `open_info` bytes for peer-
visible open-time metadata. Ordinary open calls remain metadata-free by
default.

Implementation note: although `OPEN_METADATA` is encoded inside the first
opening `DATA` / `DATA|FIN` payload, its metadata prefix does not consume
stream or session `MAX_DATA`. Only the trailing application-data bytes count
against flow-control windows.

Support for specific standardized `EXT` subtypes is claim-specific. Support for
the `EXT` frame envelope itself is part of forward-compatible core behavior.
The `EXT` envelope does not implicitly open streams in `zmux v1`.

### Claims and implementation profiles

The document set uses three related naming layers:

| Layer | Names used in this repository | Purpose |
| --- | --- | --- |
| repository claims | `zmux-wire-v1`, `zmux-api-semantics-profile-v1`, `zmux-stream-adapter-profile-v1`, `zmux-open_metadata`, `zmux-priority_update` | declare which standardized wire or API surfaces an implementation claims |
| implementation profiles | `zmux-core-v1`, `zmux-full-v1`, `zmux-reference-profile-v1` | summarize expected implementation breadth |
| negotiated capability bits | `priority_hints`, `stream_groups`, `open_metadata`, `priority_update` | control on-wire semantics and carriage paths during negotiation |

Repository-level claims are made separately for:

- `zmux-wire-v1`
- `zmux-api-semantics-profile-v1`
- `zmux-stream-adapter-profile-v1`
- `zmux-open_metadata`
- `zmux-priority_update`

Implementation profile levels:

- `zmux-core-v1`: implements the mandatory single-link core wire contract and
  forward-compatible ignore/skip behavior for unsupported same-version
  extensions, including explicit-role and `role = auto` establishment
- `zmux-full-v1`: implements `zmux-core-v1` plus all currently active
  standardized optional same-version surfaces in this repository, currently
  `open_metadata`, `priority_update`, and the correct negotiated handling of
  `priority_hints` and `stream_groups`
- `zmux-reference-profile-v1`: implements `zmux-full-v1` plus the
  repository-default API, sender, memory, liveness, and scheduling guidance
  documented in [API_SEMANTICS.md](./API_SEMANTICS.md) and
  [IMPLEMENTATION.md](./IMPLEMENTATION.md)
