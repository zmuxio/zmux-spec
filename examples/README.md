# zmux Harness Outline

This directory is non-normative.

Its purpose is to show how an implementation can consume the generated fixture
assets without depending on one specific language or test framework.

## Recommended test layers

### 1. Parser and codec tests

Use:

- `fixtures/wire_valid.ndjson`
- `fixtures/wire_invalid.ndjson`

Typical checks:

- valid inputs decode into the expected fields
- re-encoding preserves canonical `varint62`
- invalid inputs fail with the expected session error

### 2. Stream and session behavior tests

Use:

- `fixtures/state_cases.ndjson`, starting with the `portable_state` case set;
  the remaining state cases are reference-implementation regression scenarios
  (see [fixture_mapping.md](./fixture_mapping.md) Section 2)

Typical checks:

- stream opening behavior
- half-close behavior
- directional `RESET` behavior
- `ABORT` terminal behavior
- repeated `GOAWAY` monotonic handling

### 3. Policy and edge-case tests

Use:

- `fixtures/invalid_cases.ndjson`

Typical checks:

- duplicate singleton TLV handling
- wrong-side unidirectional control frames
- flow-control violations
- early-send policy edge cases

### 4. Subset selection

Use:

- `fixtures/case_sets.json`

This lets an implementation run smaller suites such as:

- byte-level codec cases (`codec_valid`, `codec_invalid`) and frame-layer
  invalid cases (`frame_invalid`)
- preface and establishment cases (`preface`)
- flow-control
- `open_metadata`
- unidirectional-stream behavior
- `priority_update`
- portable state-machine cases (`portable_state`)

Every fixture ID belongs to at least one set.

## Suggested CI order

1. `tools/build_golden_cases.py`
2. `tools/export_fixture_bundle.py`
3. `tools/build_case_sets.py`
4. `tools/validate_assets.py`
5. implementation-specific parser/state tests
