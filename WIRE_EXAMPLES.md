# zmux Wire Examples

This document provides small reference encodings for `zmux v1`.

The examples cover the currently standardized `zmux-v1` wire surface in this
repository.

All integers except `magic`, `preface_ver`, `role`, and the one-byte `code`
field use canonical `varint62` encoding.

Hex bytes are shown space-separated.

## 1. Core valid examples

### 1.1 Minimal initiator preface

Fields:

- `magic = "ZMUX"`
- `preface_ver = 1`
- `role = initiator (0)`
- `tie_breaker_nonce = 0`
- `min_proto = 1`
- `max_proto = 1`
- `capabilities = 0`
- `settings_len = 0`

Bytes:

```text
5a 4d 55 58 01 00 00 01 01 00 00
```

### 1.2 Minimal responder preface

Fields:

- `magic = "ZMUX"`
- `preface_ver = 1`
- `role = responder (1)`
- `tie_breaker_nonce = 0`
- `min_proto = 1`
- `max_proto = 1`
- `capabilities = 0`
- `settings_len = 0`

Bytes:

```text
5a 4d 55 58 01 01 00 01 01 00 00
```

### 1.3 Auto-role preface with non-zero tie-breaker nonce

Fields:

- `magic = "ZMUX"`
- `preface_ver = 1`
- `role = auto (2)`
- `tie_breaker_nonce = 5`
- `min_proto = 1`
- `max_proto = 1`
- `capabilities = 0`
- `settings_len = 0`

Bytes:

```text
5a 4d 55 58 01 02 05 01 01 00 00
```

### 1.4 First `DATA` opening initiator bidirectional stream 4 with payload `"hi"`

Fields:

- `frame_length = 4`
- `code = DATA = 0x01`
- `stream_id = 4`
- `payload = 68 69`

Bytes:

```text
04 01 04 68 69
```

### 1.5 `DATA|FIN` on stream 4 with empty payload

Fields:

- `frame_length = 2`
- `code = FIN | DATA = 0x40 | 0x01 = 0x41`
- `stream_id = 4`

Bytes:

```text
02 41 04
```

### 1.6 Session `MAX_DATA` value 1024

Fields:

- `frame_length = 4`
- `code = MAX_DATA = 0x02`
- `stream_id = 0`
- `payload = varint62(1024) = 44 00`

Bytes:

```text
04 02 00 44 00
```

### 1.7 Minimal `PING` with token `01 02 03 04 05 06 07 08`

Fields:

- `frame_length = 10`
- `code = PING = 0x04`
- `stream_id = 0`

Bytes:

```text
0a 04 00 01 02 03 04 05 06 07 08
```

### 1.8 Variable-length `PONG` echoing token and opaque bytes

Fields:

- `frame_length = 14`
- `code = PONG = 0x05`
- `stream_id = 0`
- `payload = 01 02 03 04 05 06 07 08 aa bb cc dd`

Bytes:

```text
0e 05 00 01 02 03 04 05 06 07 08 aa bb cc dd
```

### 1.9 `STOP_SENDING` on stream 4 with error code `CANCELLED`

Fields:

- `frame_length = 3`
- `code = STOP_SENDING = 0x03`
- `stream_id = 4`
- `payload = varint62(8) = 08`

Bytes:

```text
03 03 04 08
```

### 1.10 Empty `DATA|FIN` on stream 4 after prior `STOP_SENDING`

Fields:

- `frame_length = 2`
- `code = FIN | DATA = 0x41`
- `stream_id = 4`

Bytes:

```text
02 41 04
```

This is one valid sender-side conclusion after the peer requested
`STOP_SENDING` and the sender has no more bytes to emit.

### 1.11 `RESET` on stream 4 after prior `STOP_SENDING`

Fields:

- `frame_length = 3`
- `code = RESET = 0x07`
- `stream_id = 4`
- `payload = varint62(8) = 08`

Bytes:

```text
03 07 04 08
```

This is the other valid sender-side conclusion after `STOP_SENDING`: abort the
outbound half with the requested or locally chosen error code.

### 1.12 Session `BLOCKED` at offset 1024

Fields:

- `frame_length = 4`
- `code = BLOCKED = 0x06`
- `stream_id = 0`
- `payload = varint62(1024) = 44 00`

Bytes:

```text
04 06 00 44 00
```

### 1.13 `ABORT` on stream 4 with error code `CANCELLED`

Fields:

- `frame_length = 3`
- `code = ABORT = 0x08`
- `stream_id = 4`
- `payload = varint62(8) = 08`

Bytes:

```text
03 08 04 08
```

### 1.14 `PRIORITY_UPDATE` on stream 4 setting `stream_priority = 2`

Fields:

- `frame_length = 6`
- `code = EXT = 0x0b`
- `stream_id = 4`
- `ext_type = PRIORITY_UPDATE = 1`
- `ext_payload = STREAM-METADATA-TLV(type=stream_priority=1, len=1, value=02)`

Bytes:

```text
06 0b 04 01 01 01 02
```

### 1.15 `DATA|OPEN_METADATA` opening stream 4 with `stream_priority = 2` and payload `"hi"`

Fields:

- `frame_length = 8`
- `code = OPEN_METADATA | DATA = 0x20 | 0x01 = 0x21`
- `stream_id = 4`
- `metadata_len = 3`
- `metadata_tlvs = STREAM-METADATA-TLV(type=stream_priority=1, len=1, value=02)`
- `application_payload = 68 69`

Bytes:

```text
08 21 04 03 01 01 02 68 69
```

### 1.16 `DATA|OPEN_METADATA` opening stream 4 with `open_info = "ssh"` and payload `"hi"`

Fields:

- `frame_length = 10`
- `code = OPEN_METADATA | DATA = 0x20 | 0x01 = 0x21`
- `stream_id = 4`
- `metadata_len = 5`
- `metadata_tlvs = STREAM-METADATA-TLV(type=open_info=3, len=3, value=73 73 68)`
- `application_payload = 68 69`

Bytes:

```text
0a 21 04 05 03 03 73 73 68 68 69
```

### 1.17 `ABORT` on stream 4 with error code `REFUSED_STREAM` and `debug_text = "no"`

Fields:

- `frame_length = 7`
- `code = ABORT = 0x08`
- `stream_id = 4`
- `payload = varint62(4) = 04`
- `diag_tlvs = DIAG-TLV(type=debug_text=1, len=2, value=6e 6f)`

Bytes:

```text
07 08 04 04 01 02 6e 6f
```

### 1.17a First-frame `ABORT` opening previously unseen peer-owned stream 4

Fields:

- `frame_length = 3`
- `code = ABORT = 0x08`
- `stream_id = 4`
- `payload = varint62(8) = 08`

Bytes:

```text
03 08 04 08
```

In `zmux v1`, this is a valid first frame on a previously unseen valid
peer-owned stream ID and creates hidden terminal bookkeeping by default.

### 1.18 `DATA|OPEN_METADATA|FIN` opening stream 4 with `open_info = "ssh"` and empty payload

Fields:

- `frame_length = 8`
- `code = OPEN_METADATA | FIN | DATA = 0x20 | 0x40 | 0x01 = 0x61`
- `stream_id = 4`
- `metadata_len = 5`
- `metadata_tlvs = STREAM-METADATA-TLV(type=open_info=3, len=3, value=73 73 68)`
- empty application payload

Bytes:

```text
08 61 04 05 03 03 73 73 68
```

### 1.19 Zero-length `DATA|OPEN_METADATA` opening stream 4 with `open_info = "ssh"`

Fields:

- `frame_length = 8`
- `code = OPEN_METADATA | DATA = 0x21`
- `stream_id = 4`
- `metadata_len = 5`
- `metadata_tlvs = STREAM-METADATA-TLV(type=open_info=3, len=3, value=73 73 68)`
- empty application payload

Bytes:

```text
08 21 04 05 03 03 73 73 68
```

This is valid in `zmux v1` and opens the stream without carrying application
bytes.

Because only the trailing application-data portion of `DATA` participates in
flow-control accounting, this opening frame remains valid even when the
currently available stream `MAX_DATA` is `0`.

### 1.20 `DATA|OPEN_METADATA` with duplicate singleton metadata TLVs

Fields:

- `frame_length = 11`
- `code = OPEN_METADATA | DATA = 0x21`
- `stream_id = 4`
- `metadata_len = 6`
- `metadata_tlvs = two STREAM-METADATA-TLV entries for singleton type=stream_priority`
- `application_payload = 68 69`

Bytes:

```text
0b 21 04 06 01 01 02 01 01 03 68 69
```

This frame still opens stream 4 and carries the `DATA` payload `"hi"`, but
the duplicated singleton metadata block is ignored as one dropped metadata
block.

### 1.21 Permissive `GOAWAY` accepting bidirectional stream 4 and no new peer-opened unidirectional streams

Fields:

- `frame_length = 5`
- `code = GOAWAY = 0x09`
- `stream_id = 0`
- `last_accepted_bidi_stream_id = 4`
- `last_accepted_uni_stream_id = 0`
- `error_code = NO_ERROR = 0`

Bytes:

```text
05 09 00 04 00 00
```

### 1.22 Session `CLOSE` with error code `INTERNAL` and `debug_text = "no"`

Fields:

- `frame_length = 7`
- `code = CLOSE = 0x0a`
- `stream_id = 0`
- `payload = varint62(13) = 0d`
- `diag_tlvs = DIAG-TLV(type=debug_text=1, len=2, value=6e 6f)`

Bytes:

```text
07 0a 00 0d 01 02 6e 6f
```

### 1.22a `ABORT` with duplicate singleton `DIAG-TLV debug_text`

Fields:

- `frame_length = 11`
- `code = ABORT = 0x08`
- `stream_id = 4`
- `payload = varint62(4) = 04`
- `diag_tlvs = DIAG-TLV(type=debug_text=1, len=2, value=6e 6f)` followed by
  another `DIAG-TLV(type=debug_text=1, len=2, value=6f 6b)`

Bytes:

```text
0b 08 04 04 01 02 6e 6f 01 02 6f 6b
```

Repository-default handling keeps the primary `ABORT(REFUSED_STREAM)`
semantics and drops the duplicated standardized singleton DIAG block.

### 1.23 Stream-scoped `MAX_DATA` value 8192 on stream 4

Fields:

- `frame_length = 4`
- `code = MAX_DATA = 0x02`
- `stream_id = 4`
- `payload = varint62(8192) = 60 00`

Bytes:

```text
04 02 04 60 00
```

### 1.24 Stream-scoped `BLOCKED` at offset 4096 on stream 4

Fields:

- `frame_length = 4`
- `code = BLOCKED = 0x06`
- `stream_id = 4`
- `payload = varint62(4096) = 50 00`

Bytes:

```text
04 06 04 50 00
```

### 1.25 `RESET` on stream 4 with `CANCELLED` and `debug_text = "timeout"`

Fields:

- `frame_length = 12`
- `code = RESET = 0x07`
- `stream_id = 4`
- `payload = varint62(8) = 08`
- `diag_tlvs = DIAG-TLV(type=debug_text=1, len=7, value=74 69 6d 65 6f 75 74)`

Bytes:

```text
0c 07 04 08 01 07 74 69 6d 65 6f 75 74
```

### 1.26 Initiator preface with capabilities and one setting

Fields:

- `magic = "ZMUX"`
- `preface_ver = 1`
- `role = initiator (0)`
- `tie_breaker_nonce = 0`
- `min_proto = 1`
- `max_proto = 1`
- `capabilities = 25` (priority_hints | priority_update | open_metadata = 0x19)
- `settings_len = 4`
- `settings_tlv = TLV(type=max_frame_payload=7, len=2, value=varint62(32768) = 80 00)`

Bytes:

```text
5a 4d 55 58 01 00 00 01 01 19 04 07 02 80 00
```

Note: `capabilities = 25` is encoded as `varint62(25) = 19`. The setting
`max_frame_payload = 32768` is encoded as a 2-byte varint62 `80 00` inside a
TLV with type `7` and length `2`.

### 1.27 `EXT` with unknown subtype on stream 4 (forward-compatibility)

Fields:

- `frame_length = 6`
- `code = EXT = 0x0b`
- `stream_id = 4`
- `ext_type = 99` (unknown)
- `ext_payload = aa bb`

Bytes:

```text
06 0b 04 40 63 aa bb
```

A conforming receiver MUST ignore this unknown `EXT` subtype without failing
the session.

## 2. Core invalid examples

### 2.1 Invalid non-canonical varint example

`stream_id = 4` must be encoded canonically as:

```text
04
```

The following longer form is invalid in `zmux v1`:

```text
40 04
```

An implementation should reject such non-canonical encodings as protocol
errors.

### 2.2 Invalid oversized frame example

Assume the receiver advertised:

- `max_frame_payload = 16384`

Then a frame header advertising:

```text
80 00 40 03 01 04
```

means:

- `frame_length = 16387`
- `code = DATA`
- `stream_id = 4`
- derived payload length = 16385

That frame is invalid for that receiver even before reading the payload bytes.
The receiver should treat it as a `FRAME_SIZE` session error.

### 2.3 Invalid truncated `varint62` example

In `zmux v1`, the high two bits of the first byte announce the total encoded
length of a `varint62`.

The following byte sequence is invalid because the first byte announces a
4-byte integer, but only 3 bytes are present:

```text
80 00 01
```

An implementation should reject this as a session `PROTOCOL` error.

### 2.4 Invalid first `RESET` on a previously unused peer-owned stream

The following frame is structurally valid as a `RESET`, but if it is the first
frame observed on a previously unused valid peer-owned stream ID, it is a
session `PROTOCOL` error in `zmux v1`:

```text
03 07 04 08
```

### 2.5 Invalid `role = auto` preface with zero `tie_breaker_nonce`

The following preface is structurally well-formed but invalid because
`role = auto` requires a non-zero nonce:

```text
5a 4d 55 58 01 02 00 01 01 00 00
```

### 2.5a Invalid equal-nonce `role = auto` collision across two prefaces

The following pair of prefaces is individually well-formed, but together they
cause `ROLE_CONFLICT` because both peers use `role = auto` with the same
non-zero nonce:

```text
5a 4d 55 58 01 02 05 01 01 00 00
5a 4d 55 58 01 02 05 01 01 00 00
```

### 2.6 Invalid truncated `STOP_SENDING` with no mandatory `error_code`

The following frame header is structurally well-formed as a stream-scoped
frame, but its payload is too short to carry the mandatory
`varint62 error_code` required by `STOP_SENDING`:

```text
02 03 04
```

Repository-default handling is a session `FRAME_SIZE` error.

### 2.6a Invalid `DATA|OPEN_METADATA` on an already-open stream

The following frame is structurally valid as `DATA|OPEN_METADATA`, but it is
invalid if stream 4 was already opened earlier in the same session, because
`OPEN_METADATA` is only permitted on the first opening `DATA` / `DATA|FIN` for
that stream:

```text
0a 21 04 05 03 03 73 73 68 68 69
```

Repository-default handling is a session `PROTOCOL` error.

## 3. Pairwise interaction sequences

These sequences are illustrative interaction traces built from the valid and
invalid single-frame examples above. They do not replace the normative rules in
[SPEC.md](./SPEC.md) or [STATE_MACHINE.md](./STATE_MACHINE.md).

### 3.1 `STOP_SENDING(CANCELLED)` followed by graceful sender conclusion

Sequence:

```text
03 03 04 08    ; STOP_SENDING(stream=4, code=CANCELLED)
02 41 04       ; DATA|FIN(stream=4, empty payload)
```

This is the graceful sender-side conclusion path after reader-side stop when
no meaningful outbound tail remains.

### 3.2 `STOP_SENDING(CANCELLED)` followed by abortive sender conclusion

Sequence:

```text
03 03 04 08    ; STOP_SENDING(stream=4, code=CANCELLED)
03 07 04 08    ; RESET(stream=4, code=CANCELLED)
```

This is the abortive sender-side conclusion path after reader-side stop.

### 3.3 Peer `FIN` followed by invalid late `DATA`

Sequence:

```text
02 41 04       ; DATA|FIN(stream=4, empty payload)
04 01 04 68 69 ; late DATA(stream=4, "hi")
```

After the receiver has already observed peer `FIN` on that direction, the
later `DATA` is invalid.

### 3.4 Peer `RESET` followed by ignored late in-flight `DATA`

Sequence:

```text
03 07 04 08    ; RESET(stream=4, code=CANCELLED)
04 01 04 68 69 ; late in-flight DATA(stream=4, "hi")
```

The late `DATA` is ignored rather than treated as a fresh new stream-state
violation. Its payload bytes still follow the discard-and-budget-release path.

### 3.5 Peer `ABORT` followed by ignored late in-flight `DATA`

Sequence:

```text
03 08 04 08    ; ABORT(stream=4, code=CANCELLED)
04 01 04 68 69 ; late in-flight DATA(stream=4, "hi")
```

After `ABORT`, both halves are terminal. The late `DATA` is ignored and its
payload bytes still follow the discard-and-budget-release path.

### 3.6 Permissive `GOAWAY`, then rejected too-new peer stream

Sequence:

```text
05 09 00 04 00 00 ; GOAWAY(last_accepted_bidi=4, last_accepted_uni=0, NO_ERROR)
04 01 08 68 69    ; peer attempts new bidi stream 8 with DATA("hi")
03 08 08 05       ; local ABORT(stream=8, code=REFUSED_STREAM)
```

This illustrates the normal repository-default rejection path after a peer
opens a stream beyond the accepted `GOAWAY` watermark.

### 3.7 Zero-length `DATA|OPEN_METADATA` opener, then later payload

Sequence:

```text
08 21 04 05 03 03 73 73 68 ; DATA|OPEN_METADATA(stream=4, open_info="ssh", empty payload)
04 01 04 68 69             ; later DATA(stream=4, "hi")
```

This opens the stream, conveys open-time metadata, and leaves later payload
delivery to subsequent `DATA` frames.

### 3.8 Session `BLOCKED` followed by `MAX_DATA` advancement

Sequence:

```text
04 06 00 44 00    ; session BLOCKED(blocked_at=1024)
04 02 00 48 00    ; session MAX_DATA(max_offset=2048)
```

This illustrates the normal receiver-side response after the sender reports
being blocked at the current session limit: the receiver advances the session
receive window. The sender may then resume `DATA` transmission.

### 3.9 `DATA|OPEN_METADATA` with `stream_priority` and `stream_group`

Sequence:

```text
0b 21 04 06 01 01 02 02 01 05 68 69
```

Fields:

- `frame_length = 11`
- `code = OPEN_METADATA | DATA = 0x21`
- `stream_id = 4`
- `metadata_len = 6`
- `metadata_tlvs = STREAM-METADATA-TLV(type=stream_priority=1, len=1, value=02)` +
  `STREAM-METADATA-TLV(type=stream_group=2, len=1, value=05)`
- `application_payload = 68 69`

This opens stream 4 with both `stream_priority = 2` and `stream_group = 5`,
followed by application data `"hi"`.
