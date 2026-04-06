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

- `fixtures/state_cases.ndjson`

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

- codec-only
- flow-control
- open-hints
- unidirectional-stream behavior
- `priority_update`

## Suggested CI order

1. `tools/build_golden_cases.py`
2. `tools/export_fixture_bundle.py`
3. `tools/build_case_sets.py`
4. `tools/validate_assets.py`
5. implementation-specific parser/state tests
