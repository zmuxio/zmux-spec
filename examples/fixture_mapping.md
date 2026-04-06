# Fixture Field Mapping

This document is non-normative.

It explains how the generated fixture assets are intended to map onto common
parser, state-machine, and API assertions in language implementations.

## 1. Wire fixtures

Files:

- `fixtures/wire_valid.ndjson`
- `fixtures/wire_invalid.ndjson`

Recommended assertion mapping:

- `hex`  
  Feed into the frame or preface decoder as raw bytes.
- `expect.frame_length`  
  Assert the parsed frame length.
- `expect.frame_type`  
  Assert the decoded core frame type name.
- `expect.flags`  
  Assert the decoded flag set after applying the code-byte split.
- `expect.stream_id`  
  Assert the parsed stream ID.
- `expect.payload_hex`  
  Assert the remaining payload bytes after frame-header parsing.
- `expect.decoded.*`  
  Assert frame-type-specific semantic decoding such as `max_offset`,
  `blocked_at`, `error_code`, or `ext_type`.
- `expect_error`  
  Assert the decoder or session layer reports the named protocol error.

## 2. State fixtures

File:

- `fixtures/state_cases.ndjson`

Recommended assertion mapping:

- `initial_state`  
  Seed the local state-machine model. Depending on the case, this may be a
  simple stream-level marker such as `idle`, or a structured half-state object
  such as `{ "send_half": "send_open", "recv_half": "recv_open" }`.
- `stream_kind`  
  Select the correct stream-kind rules, especially for unidirectional cases.
- `ownership`  
  Decide whether the incoming or outgoing side owns the stream ID.
- `steps[].event`  
  Feed the named logical event into the state machine or API layer.
- `steps[].expect_state`  
  Assert the resulting conceptual state. For stream lifecycle cases, prefer
  asserting the send-half and receive-half states separately when a structured
  object is provided.
- `steps[].expect_result`  
  Assert a semantic outcome such as protocol violation, local invalid action,
  or permitted refusal.

## 3. Invalid-policy fixtures

File:

- `fixtures/invalid_cases.ndjson`

Recommended assertion mapping:

- `layer` / `category`  
  Route the case to parser, flow-control, state, preface, or extension tests.
- `input_shape`  
  Build the local preconditions for the invalid condition.
- `expected_result.scope`  
  Assert whether the result is stream-local, session-wide, or
  session-establishment-local.
- `expected_result.error`  
  Assert the named error code if the outcome is an error.
- `expected_result.action`  
  Assert non-error outcomes such as ignore or forbid-send.

## 4. Case-set fixtures

File:

- `fixtures/case_sets.json`

Recommended use:

- select subsets for targeted test jobs
- split CI into codec, state, flow-control, unidirectional, or
  `open_metadata` / `priority_update` jobs
- keep language-specific harnesses aligned on the same logical case groups

## 5. Golden-case view

File:

- `assets/golden_cases.json`

Recommended use:

- consume when one harness wants a single source of truth
- consume the sharded `fixtures/*.ndjson` files when test organization by layer
  is more convenient
