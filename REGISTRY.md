# zmux Registry

This document assigns stable numeric values for `zmux v1`.

It is organized by common implementation lookup order:

1. session and preface constants
2. settings and defaults
3. capabilities
4. frame and flag values
5. TLV namespaces
6. error codes
7. `EXT` subtype IDs
8. reserved ranges

## 1. Session and preface constants

- `magic = "ZMUX"`
- `preface_ver = 1`
- `proto_ver = 1`
- `max_preface_settings_bytes = 4096`

Role values:

- `0` = initiator
- `1` = responder
- `2` = auto

## 2. Settings and defaults

These IDs are used inside preface `settings_tlv`.

- `1` = initial_max_stream_data_bidi_locally_opened
- `2` = initial_max_stream_data_bidi_peer_opened
- `3` = initial_max_stream_data_uni
- `4` = initial_max_data
- `5` = max_incoming_streams_bidi
- `6` = max_incoming_streams_uni
- `7` = max_frame_payload
- `8` = idle_timeout_millis
- `9` = keepalive_hint_millis
- `10` = max_control_payload_bytes
- `11` = max_extension_payload_bytes
- `12` = scheduler_hints

### 2.1 Standard default values

These values define the standard defaults applied when the corresponding
settings are omitted from the session preface.

- `initial_max_stream_data_bidi_locally_opened = 65536`
- `initial_max_stream_data_bidi_peer_opened = 65536`
- `initial_max_stream_data_uni = 65536`
- `initial_max_data = 262144`
- `max_incoming_streams_bidi = 256`
- `max_incoming_streams_uni = 256`
- `max_frame_payload = 16384`
- `idle_timeout_millis = 0`
- `keepalive_hint_millis = 0`
- `max_control_payload_bytes = 4096`
- `max_extension_payload_bytes = 4096`
- `scheduler_hints = 0`

### 2.2 Standard advisory enums

`scheduler_hints`:

- `0` = unspecified_or_balanced
- `1` = latency
- `2` = balanced_fair
- `3` = bulk_throughput
- `4` = group_fair

### 2.3 Receive-window interpretation summary

For traffic flowing toward the advertising endpoint:

- `initial_max_stream_data_bidi_locally_opened`: applies when that
  bidirectional stream was opened by the advertising endpoint
- `initial_max_stream_data_bidi_peer_opened`: applies when that bidirectional
  stream was opened by the peer
- `initial_max_stream_data_uni`: applies to peer-opened unidirectional streams
  because only the peer can send `DATA` on them

### 2.4 Capability-linked minimum receive limits

These minima are compatibility requirements for negotiated behavior, not new
default values.

- all `zmux v1` peers: `max_frame_payload >= 16384`
- all `zmux v1` peers: `max_control_payload_bytes >= 4096`
- all `zmux v1` peers: `max_extension_payload_bytes >= 4096`

## 3. Capability bits

- `1 << 0` = priority_hints
- `1 << 1` = stream_groups
- `1 << 2` = multilink_basic (deprecated, retired, reserved, not reusable)
- `1 << 3` = priority_update
- `1 << 4` = open_metadata

Dependency guidance for active capability claims:

- `priority_hints` and `stream_groups` define advisory metadata semantics
- `priority_update` is only a carriage/update path; it has no standalone
  meaning unless paired with at least one semantic capability
- `open_metadata` is a first-opening-`DATA` carriage path; it may carry
  `open_info` alone or, when paired with the corresponding semantic
  capabilities, `stream_priority` and `stream_group`
- advertising `priority_hints` or `stream_groups` without also negotiating
  either `open_metadata` or `priority_update` is wire-valid, but it does not
  provide a standardized peer-visible carriage path

## 4. Core frame and flag registry

Frame types:

- `1` = DATA
- `2` = MAX_DATA
- `3` = STOP_SENDING
- `4` = PING
- `5` = PONG
- `6` = BLOCKED
- `7` = RESET
- `8` = ABORT
- `9` = GOAWAY
- `10` = CLOSE
- `11` = EXT

Frame flags:

- `0x20` = OPEN_METADATA
- `0x40` = FIN
- `0x80` = reserved and unusable in `zmux v1`

The lower 5 bits of the code byte carry the frame type. The upper 3 bits carry
the flags.

## 5. TLV namespaces

### 5.1 STREAM-METADATA-TLV types

- `1` = stream_priority
- `2` = stream_group
- `3` = open_info

Value formats:

- `stream_priority`: `varint62`, `0` = default, larger values = stronger
  preference for low-latency treatment
- `stream_group`: `varint62`, `0` = no explicit group, non-zero values are
  advisory opaque grouping hints for scheduling, fairness, or placement; they
  do not define standardized higher-layer semantics
- `open_info`: opaque bytes; standardized only as peer-visible open-time
  metadata with no core mux semantics; applications that need multiple logical
  fields should encode them inside this one byte string using their own
  higher-layer format

### 5.2 DIAG-TLV types

- `1` = debug_text
- `2` = retry_after_millis
- `3` = offending_stream_id
- `4` = offending_frame_type

Value formats:

- `debug_text`: UTF-8 text
- `retry_after_millis`: `varint62`
- `offending_stream_id`: `varint62`
- `offending_frame_type`: `varint62`

Standardized DIAG-TLV behavior:

- all currently standardized DIAG-TLV types are singleton within one enclosing
  frame
- duplicate standardized singleton DIAG-TLVs invalidate only the DIAG block for
  that frame; they do not alter the enclosing frame's primary semantics
- `debug_text` is human-readable diagnostics only
- `retry_after_millis` is an advisory retry hint only
- `offending_stream_id` and `offending_frame_type` are advisory machine-readable
  context only

## 6. Error codes

Error-code space is partitioned as follows:

- `0-255`: core `zmux` protocol errors
- `>= 256`: non-core passthrough error space

Core `zmux v1` transports non-core error codes unchanged and does not assign
built-in semantics beyond preserving the numeric value.

| Code | Name | Allowed on | Retry class | Default API kind | Severity scope |
| --- | --- | --- | --- | --- | --- |
| `0` | `NO_ERROR` | `CLOSE`, `GOAWAY`, optionally stream-terminal frames when no richer error applies | no automatic retry conclusion | graceful/no-error shutdown | session or stream |
| `1` | `PROTOCOL` | `CLOSE`, exceptionally `ABORT` when a violation is confined to one already-open stream | not retryable by default | protocol error | session-first |
| `2` | `FLOW_CONTROL` | `CLOSE`, `ABORT` | retryable only after peer credit changes or a new session | flow-control error | session or stream |
| `3` | `STREAM_LIMIT` | `CLOSE`, `ABORT` | retryable after stream-credit recovery or replacement session | admission error | session or stream |
| `4` | `REFUSED_STREAM` | `ABORT`, optionally `GOAWAY`-driven local mapping | retryable on a later stream/session | refused stream open | stream |
| `5` | `STREAM_STATE` | `ABORT` | not retryable on the same stream; maybe retryable on a new stream | stream-state error | stream |
| `6` | `STREAM_CLOSED` | `ABORT` | not retryable on the same stream | closed-stream error | stream |
| `7` | `SESSION_CLOSING` | `ABORT`, `GOAWAY`, `CLOSE` | retryable only on a replacement session | draining/closing error | session-first |
| `8` | `CANCELLED` | `STOP_SENDING`, `RESET`, `ABORT` | application-policy dependent | local/remote cancellation | stream |
| `9` | `IDLE_TIMEOUT` | `CLOSE` | retryable on a new session | idle timeout | session |
| `10` | `FRAME_SIZE` | `CLOSE` | not retryable by default | parse/frame-size error | session |
| `11` | `UNSUPPORTED_VERSION` | `CLOSE` during establishment | retryable only after version downgrade | version error | session |
| `12` | `ROLE_CONFLICT` | `CLOSE` during establishment | retryable only via new establishment attempt | role-resolution error | session |
| `13` | `INTERNAL` | `CLOSE`, `ABORT`, `RESET` | implementation-policy dependent | internal error | session or stream |

These columns are registry guidance, not extra wire fields:

- `Allowed on` indicates the repository-default surfaces where the code is most
  appropriate
- `Retry class` is advisory guidance for APIs and higher layers
- `Default API kind` is the recommended local error-family classification
- `Severity scope` indicates whether the code is normally session-first,
  stream-first, or valid in either scope

## 7. Standard `EXT` subtype IDs

- `1` = PRIORITY_UPDATE
- `2` = ML_READY (retired, reserved, not reusable)
- `3` = ML_ATTACH (retired, reserved, not reusable)
- `4` = ML_ATTACH_ACK (retired, reserved, not reusable)
- `5` = ML_DRAIN_REQ (retired, reserved, not reusable)
- `6` = ML_DRAIN_ACK (retired, reserved, not reusable)

`PRIORITY_UPDATE` is defined by [SPEC.md](./SPEC.md).
The retired `ML_*` subtype numbers remain reserved for historical continuity
but are no longer part of the active standardized surface.

## 8. Reserved ranges

The following ranges remain reserved:

- capability bit positions `5-31` are reserved for future standard assignment
- capability bit positions `32-47` are experimental
- capability bit positions `48-61` are private-use
- frame types `12-31` are reserved
- setting IDs `256-511` are experimental
- setting IDs `512-1023` are reserved for future standard assignment
- setting IDs `>= 1024` are private-use
- STREAM-METADATA-TLV types `256-511` are experimental
- STREAM-METADATA-TLV types `512-1023` are reserved for future standard
  assignment
- STREAM-METADATA-TLV types `>= 1024` are private-use
- DIAG-TLV types `256-511` are experimental
- DIAG-TLV types `512-1023` are reserved for future standard assignment
- DIAG-TLV types `>= 1024` are private-use
- `EXT` subtype IDs `7-255` are standard-extension space
- `EXT` subtype IDs `256-511` are experimental
- `EXT` subtype IDs `512-1023` are reserved for future standard assignment
- `EXT` subtype IDs `>= 1024` are private-use

Allocated values remain reserved even after deprecation or retirement. Standard
documents MUST NOT silently reuse an older assignment for different semantics
within the same registry namespace.
