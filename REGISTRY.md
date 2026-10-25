# zmux Registry

This document lists every codepoint that zmux defines,
uses or prohibits, the GREASE rules, and the policy for future registrations.
The machine-readable form is [assets/registry.json](./assets/registry.json).
Both are generated from `tools/zmuxwire/registry.py`; the tables between
`BEGIN GENERATED` and `END GENERATED` markers must not be edited by hand.
`python3 tools/validate_assets.py` checks that this file, the JSON file and
the source agree, and that every zmux value obeys Section 2.

---

## 1. Spaces

| Space | Encoding | Shared with | Section |
|---|---|---|---|
| frame types | variable-length integer, shortest form | QUIC frame types (IANA), QMux | 3 |
| transport parameters | variable-length integer | QUIC transport parameters (IANA), QMux | 4 |
| stream preamble TLV types | variable-length integer | none (zmux only) | 5 |
| transport error codes | variable-length integer | QUIC transport error codes (IANA) | 6.1 |
| application error-code conventions | variable-length integer | the application's own space | 6.2 |
| PIPE_JOIN flags, multipath secret flags, PIPE_STATUS values, zmux_role values | fixed | none | 8 |
| labels (TLS exporter, HKDF, HTTP header) | ASCII strings | none | 9 |

---

## 2. Registration policy

### 2.1 Ranges

- zmux never allocates a frame type, transport parameter or transport error
  code below 2^14. Values below 2^14 belong to QUIC and its IANA registries
  (RFC 9000 Section 22); zmux only uses them as QUIC defines them.
- zmux-allocated values lie in [2^14, 2^30): they encode in four bytes, are
  outside the QUIC Standards Action range, and avoid every IANA QUIC
  registration at the time of allocation (RFC 9000 Section 22.1.2 recommends values of
  2^14 and above, chosen at random).
- Values at and above 2^30 (eight-byte encodings) are left to QUIC and QMux,
  which use them for provisional registrations (for example QMux's
  QX_TRANSPORT_PARAMETERS), and to experiments (Section 2.5).
- Transport parameter values of the form 31 x N + 27 are GREASE (RFC 9000
  Section 18.1) and are never allocated. Preamble TLV types and zmux error
  codes of the form 0x1f x N + 0x21 are never allocated either.

### 2.2 Allocation method

zmux values are derived deterministically so that anyone can recompute them:

    v = 2^14 + (first 8 bytes of SHA-256("zmux <kind> <name>[#i]") as a big-endian integer)
              mod (2^30 - 2^14)

where `<kind>` is `frame`, `transport_parameter` or `transport_error`, and i
(written only when non-zero) is the smallest integer that avoids a collision
with an IANA registration, another zmux value, or a GREASE value. The table
`ALLOCATION_SUFFIX` in the source records each non-zero i.

### 2.3 Policies

The zmux registries follow the QUIC policies of RFC 9000 Section 22.1:

- **Provisional registration** (first come, first served): an entry with a
  name, value, contact and date, requested in the zmux-spec repository. The
  value is computed with the method of Section 2.2. Provisional entries that
  are unused for a year may be reclaimed (RFC 9000 Section 22.1.3).
- **Permanent registration** (specification required): a published,
  stable specification reviewed by the zmux-spec maintainers against Section
  2.4.
- Preamble TLV types below 0x40 require a permanent registration; types
  0x40 to 0xfff and from 0x4000 on may be provisional. Types 0x1000 to
  0x3fff are the application range (EXTENSIONS.md Section 4.3): never
  registered or allocated by zmux, like the application error codes.
- Application error codes outside 0x5a4d0000 to 0x5a4d0fff are not registered:
  they belong to applications.

### 2.4 Requirements for new frames and parameters

A new frame type or transport parameter MUST:

1. be negotiated: a new frame type is gated on a transport parameter, and is
   never sent to a peer that did not announce it (SPEC.md Section 5.5), so
   that plain-QMux interoperability (SPEC.md Section 13) is preserved;
2. be idempotent: duplicates and late, outdated copies have no further effect
   (SPEC.md Section 4.1 rule 5);
3. state its retransmission rule for multipath sessions (MULTIPATH.md Section
   7.4), whether it is ack-eliciting, and its classification for the
   no-effect budget (SPEC.md Section 9.4): a new frame is never chargeable
   unless the closed list is amended by a new version of SPEC.md;
4. state the response state it creates and show that it is bounded (SPEC.md
   Section 9.2);
5. not change the meaning of any existing codepoint.

### 2.5 Experiments

Experimental frames and parameters use randomly chosen eight-byte values
(2^30 and above), are negotiated like any other extension, and are used only
between endpoints under common control. They are never registered.

### 2.6 Compatibility and versioning

- Existing codepoints never change meaning, and transport parameters are
  never renegotiated within a session.
- A feature is in use only when both endpoints announced it, except where
  the feature defines a unilateral capability (as DATAGRAM and
  RESET_STREAM_AT do).
- Reserved bits that must be zero stay zero; new semantics use new
  parameters, not reserved bits. GREASE (Section 8) keeps the extension
  points open.
- zmux has no version field and no version negotiation: it is identified by
  its first bytes (SPEC.md Section 6.2) and runs under the application's
  ALPN identifiers. Additive changes are extensions. A change that alters
  existing encodings (for example aligning with a published QMux RFC) is a
  new protocol version with new ALPN identifiers.

---

## 3. Frame types

<!-- BEGIN GENERATED: frames -->
| Frame | Type | Origin | Gate | Ack-eliciting | Retransmission | Defined in |
|---|---|---|---|---|---|---|
| PADDING | 0x00 | QUIC v1 (RFC 9000) | - | no | never | SPEC.md#4.3 |
| PING | 0x01 | QUIC v1, prohibited | - | - | - | SPEC.md#4.4 |
| ACK | 0x02-0x03 | QUIC v1, prohibited | - | - | - | SPEC.md#4.4 |
| RESET_STREAM | 0x04 | QUIC v1 (RFC 9000) | - | yes | until-acked-or-stream-terminal | SPEC.md#4.3 |
| STOP_SENDING | 0x05 | QUIC v1 (RFC 9000) | - | yes | until-recv-terminal | SPEC.md#4.3 |
| CRYPTO | 0x06 | QUIC v1, prohibited | - | - | - | SPEC.md#4.4 |
| NEW_TOKEN | 0x07 | QUIC v1, prohibited | - | - | - | SPEC.md#4.4 |
| STREAM | 0x08-0x0f | QUIC v1 (RFC 9000) | - | yes | data-unless-reset | SPEC.md#4.3 |
| MAX_DATA | 0x10 | QUIC v1 (RFC 9000) | - | yes | current-value | SPEC.md#4.3 |
| MAX_STREAM_DATA | 0x11 | QUIC v1 (RFC 9000) | - | yes | current-value-while-recv | SPEC.md#4.3 |
| MAX_STREAMS | 0x12-0x13 | QUIC v1 (RFC 9000) | - | yes | current-value | SPEC.md#4.3 |
| DATA_BLOCKED | 0x14 | QUIC v1 (RFC 9000) | - | yes | while-blocked | SPEC.md#4.3 |
| STREAM_DATA_BLOCKED | 0x15 | QUIC v1 (RFC 9000) | - | yes | while-blocked | SPEC.md#4.3 |
| STREAMS_BLOCKED | 0x16-0x17 | QUIC v1 (RFC 9000) | - | yes | while-blocked | SPEC.md#4.3 |
| NEW_CONNECTION_ID | 0x18 | QUIC v1, prohibited | - | - | - | SPEC.md#4.4 |
| RETIRE_CONNECTION_ID | 0x19 | QUIC v1, prohibited | - | - | - | SPEC.md#4.4 |
| PATH_CHALLENGE | 0x1a | QUIC v1, prohibited | - | - | - | SPEC.md#4.4 |
| PATH_RESPONSE | 0x1b | QUIC v1, prohibited | - | - | - | SPEC.md#4.4 |
| CONNECTION_CLOSE | 0x1c-0x1d | QUIC v1 (RFC 9000) | - | no | never | SPEC.md#4.3 |
| HANDSHAKE_DONE | 0x1e | QUIC v1, prohibited | - | - | - | SPEC.md#4.4 |
| RESET_STREAM_AT | 0x24 | QUIC extension | reset_stream_at | yes | until-acked-or-stream-terminal | SPEC.md#4.3 |
| DATAGRAM | 0x30-0x31 | QUIC extension | max_datagram_frame_size | yes | never | EXTENSIONS.md#6 |
| QX_TRANSPORT_PARAMETERS | 0x3f5153300d0a0d0a | QMux-02 | - | yes | never | SPEC.md#5.1 |
| QX_PING | 0x348c67529ef8c7bd-0x348c67529ef8c7be | QMux-02 | - | yes | never | SPEC.md#4.5 |
| GOAWAY | 0x1911f43e | zmux | zmux_goaway | yes | current-value | EXTENSIONS.md#3 |
| PRIORITY_UPDATE | 0x073d08cf | zmux | zmux_priority | yes | latest-per-stream | EXTENSIONS.md#5 |
| PIPE_JOIN | 0x1a24de36 | zmux | zmux_multipath | yes | never | MULTIPATH.md#5 |
| PIPE_ACK | 0x0b2b3eeb | zmux | zmux_multipath or zmux_pipe_ack | no | never | MULTIPATH.md#6 |
| PIPE_LANE | 0x3a05a1c9 | zmux | zmux_max_lanes | yes | never | MULTIPATH.md#5.8 |
| PIPE_PING | 0x269b43e6 | zmux | zmux_multipath | yes | never | MULTIPATH.md#6.4 |
| PIPE_ABANDON | 0x23b35b74 | zmux | zmux_multipath | yes | until-acked | MULTIPATH.md#8 |
| PIPE_STATUS | 0x227b2811 | zmux | zmux_multipath | yes | latest-per-pipe | MULTIPATH.md#9 |
<!-- END GENERATED: frames -->

"Gate" names the transport parameter that must be in use (zmux features) or
announced by the receiver (RESET_STREAM_AT, DATAGRAM, and PIPE_LANE, which
needs a receiver's `zmux_max_lanes` above 1 with multipath or pipe
acknowledgement in use: lanes available toward the receiver, MULTIPATH.md
Section 3.6). "Retransmission" is the
multipath reinjection rule of MULTIPATH.md Section 7.4.

---

## 4. Transport parameters

<!-- BEGIN GENERATED: transport_parameters -->
| Parameter | ID | Origin, format | Absent means | zmux endpoints send | Defined in |
|---|---|---|---|---|---|
| original_destination_connection_id | 0x00 | QUIC v1, prohibited (TRANSPORT_PARAMETER_ERROR) | - | - | SPEC.md#5.2 |
| max_idle_timeout | 0x01 | QUIC/QMux, varint | 0 | 30000 | SPEC.md#5.3 |
| stateless_reset_token | 0x02 | QUIC v1, prohibited (TRANSPORT_PARAMETER_ERROR) | - | - | SPEC.md#5.2 |
| max_udp_payload_size | 0x03 | QUIC v1, prohibited (TRANSPORT_PARAMETER_ERROR) | - | - | SPEC.md#5.2 |
| initial_max_data | 0x04 | QUIC/QMux, varint | 0 | 1048576 | SPEC.md#5.3 |
| initial_max_stream_data_bidi_local | 0x05 | QUIC/QMux, varint | 0 | 262144 | SPEC.md#5.3 |
| initial_max_stream_data_bidi_remote | 0x06 | QUIC/QMux, varint | 0 | 262144 | SPEC.md#5.3 |
| initial_max_stream_data_uni | 0x07 | QUIC/QMux, varint | 0 | 262144 | SPEC.md#5.3 |
| initial_max_streams_bidi | 0x08 | QUIC/QMux, varint | 0 | 256 | SPEC.md#5.3 |
| initial_max_streams_uni | 0x09 | QUIC/QMux, varint | 0 | 16 (raised to 256 by MAX_STREAMS on the first accept), or the configured value | SPEC.md#5.3 |
| ack_delay_exponent | 0x0a | QUIC v1, prohibited (TRANSPORT_PARAMETER_ERROR) | - | - | SPEC.md#5.2 |
| max_ack_delay | 0x0b | QUIC v1, prohibited (TRANSPORT_PARAMETER_ERROR) | - | - | SPEC.md#5.2 |
| disable_active_migration | 0x0c | QUIC v1, prohibited (TRANSPORT_PARAMETER_ERROR) | - | - | SPEC.md#5.2 |
| preferred_address | 0x0d | QUIC v1, prohibited (TRANSPORT_PARAMETER_ERROR) | - | - | SPEC.md#5.2 |
| active_connection_id_limit | 0x0e | QUIC v1, prohibited (TRANSPORT_PARAMETER_ERROR) | - | - | SPEC.md#5.2 |
| initial_source_connection_id | 0x0f | QUIC v1, prohibited (TRANSPORT_PARAMETER_ERROR) | - | - | SPEC.md#5.2 |
| retry_source_connection_id | 0x10 | QUIC v1, prohibited (TRANSPORT_PARAMETER_ERROR) | - | - | SPEC.md#5.2 |
| reset_stream_at | 0x1d | QUIC/QMux, empty | not supported | always | SPEC.md#5.3 |
| max_datagram_frame_size | 0x20 | QUIC/QMux, varint | 0 | own max_record_size (65535) when datagrams are enabled | EXTENSIONS.md#6 |
| max_record_size | 0x0571c59429cd0845 | QUIC/QMux, varint | 16382 | 65535 | SPEC.md#3.2 |
| zmux_role | 0x17240207 | zmux, role | transport role | when configured | EXTENSIONS.md#2 |
| zmux_goaway | 0x04528a5f | zmux, empty | not supported | always | EXTENSIONS.md#3 |
| zmux_preamble | 0x0439e07a | zmux, varint | not supported | 4096 (less when the stream windows are smaller) | EXTENSIONS.md#4 |
| zmux_priority | 0x31e7139d | zmux, empty | not supported | always | EXTENSIONS.md#5 |
| zmux_datagram_flows | 0x0af391ed | zmux, empty | not supported | when datagrams are enabled | EXTENSIONS.md#6 |
| zmux_multipath | 0x30dc2978 | zmux, multipath | not supported | when more than one pipe or resume is enabled | MULTIPATH.md#3 |
| zmux_pipe_ack | 0x34b47621 | zmux, empty | not supported | always | MULTIPATH.md#3.5 |
| zmux_ack_threshold | 0x2762c7e5 | zmux, varint | 65536 | omitted (65536) | MULTIPATH.md#3.2 |
| zmux_max_ack_delay | 0x17fa1ed6 | zmux, varint | 25 | omitted (25) | MULTIPATH.md#3.2 |
| zmux_max_lanes | 0x27856ef0 | zmux, lanes | 1 lane (no lanes) | always: Max Lanes 258 and a random 16-byte Lane Key | MULTIPATH.md#3.6 |
| zmux_resume | 0x0d5778ce | zmux, varint | not supported | the resume grace in ms when resume is enabled | RESUME.md#3 |
| zmux_record_protection | 0x1dbe26bc | zmux (reserved), opaque | not supported | never (reserved) | REGISTRY.md#7 |
<!-- END GENERATED: transport_parameters -->

"zmux endpoints send" is the RECOMMENDED value of a general-purpose
implementation (SPEC.md Section 5.3).

---

## 5. Stream preamble TLV types

<!-- BEGIN GENERATED: preamble_tlvs -->
| TLV | Type | Status | Value | Defined in |
|---|---|---|---|---|
| OPEN_INFO | 0x01 | active | opaque bytes, 0..(limit) bytes | EXTENSIONS.md#4.3 |
| PRIORITY | 0x02 | active | 1 byte: urgency (3 bits) | incremental (1 bit) | 0000 | EXTENSIONS.md#4.3 |
| DEADLINE | 0x03 | reserved | reserved: relative deadline in ms (varint); MUST NOT be sent | EXTENSIONS.md#4.3 |
| PIPE_CARRIER | 0x05 | active | empty: the stream carries a pipe pair of a nested session | EXTENSIONS.md#4.3 |
| application | 0x1000..0x3fff | application | opaque bytes; never allocated by zmux, delivered to the application; GREASE values excluded | EXTENSIONS.md#4.3 |
| GREASE | 0x1f*N+0x21 | grease | any, ignored | EXTENSIONS.md#4.3 |
<!-- END GENERATED: preamble_tlvs -->

Types 0x1f x N + 0x21 are GREASE (Section 8), inside the application range
too. Every TLV is ignorable by a receiver that does not know it
(EXTENSIONS.md Section 4.3); application TLVs are delivered to the
application, which may give its own types any meaning (EXTENSIONS.md
Section 4.7 shows a convention).

---

## 6. Error codes

### 6.1 Transport error codes

<!-- BEGIN GENERATED: transport_errors -->
| Code | Value | Defined by | Sent on the wire |
|---|---|---|---|
| NO_ERROR | 0x00 | RFC 9000 | yes |
| INTERNAL_ERROR | 0x01 | RFC 9000 | yes |
| CONNECTION_REFUSED | 0x02 | RFC 9000 | yes |
| FLOW_CONTROL_ERROR | 0x03 | RFC 9000 | yes |
| STREAM_LIMIT_ERROR | 0x04 | RFC 9000 | yes |
| STREAM_STATE_ERROR | 0x05 | RFC 9000 | yes |
| FINAL_SIZE_ERROR | 0x06 | RFC 9000 | yes |
| FRAME_ENCODING_ERROR | 0x07 | RFC 9000 | yes |
| TRANSPORT_PARAMETER_ERROR | 0x08 | RFC 9000 | yes |
| PROTOCOL_VIOLATION | 0x0a | RFC 9000 | yes |
| NO_VIABLE_PATH | 0x10 | RFC 9000 | yes |
| ZMUX_EXCESSIVE_LOAD | 0x25ff75ef | zmux | yes |
| ZMUX_ROLE_CONFLICT | 0x277db2c3 | zmux | yes |
| ZMUX_RESUME_TIMEOUT | 0x0eb87167 | zmux | yes |
| ZMUX_RESUME_FAILED | 0x0f05f74e | zmux | yes |
| ZMUX_PIPE_FAILED | 0x296a5bf5 | zmux | yes |
| ZMUX_PIPE_RESOURCE_LIMIT | 0x0b4fa113 | zmux | yes |
| ZMUX_PIPE_AUTH_FAILED | 0x09613938 | zmux | never (local only) |
| ZMUX_IDLE_TIMEOUT | 0x2736d55c | zmux | yes |
<!-- END GENERATED: transport_errors -->

### 6.2 Application error-code conventions

<!-- BEGIN GENERATED: application_errors -->
| Code | Value | SOCKS5 REP |
|---|---|---|
| NO_ERROR | 0x00 | - |
| ZMUX_CANCELLED | 0x5a4d0001 | - |
| ZMUX_REJECTED | 0x5a4d0002 | - |
| ZMUX_CARRIER_FAILED | 0x5a4d0003 | - |
| ZMUX_PROXY_GENERAL_FAILURE | 0x5a4d0101 | 0x01 |
| ZMUX_PROXY_NOT_ALLOWED | 0x5a4d0102 | 0x02 |
| ZMUX_PROXY_NETWORK_UNREACHABLE | 0x5a4d0103 | 0x03 |
| ZMUX_PROXY_HOST_UNREACHABLE | 0x5a4d0104 | 0x04 |
| ZMUX_PROXY_CONNECTION_REFUSED | 0x5a4d0105 | 0x05 |
| ZMUX_PROXY_TTL_EXPIRED | 0x5a4d0106 | 0x06 |
| ZMUX_PROXY_COMMAND_NOT_SUPPORTED | 0x5a4d0107 | 0x07 |
| ZMUX_PROXY_ADDRESS_TYPE_NOT_SUPPORTED | 0x5a4d0108 | 0x08 |
| ZMUX_PROXY_DNS_FAILURE | 0x5a4d0110 | 0x04 |
| ZMUX_PROXY_CONNECT_TIMEOUT | 0x5a4d0111 | 0x06 |
<!-- END GENERATED: application_errors -->

The block 0x5a4d0000 to 0x5a4d0fff is reserved for zmux conventions
(EXTENSIONS.md Section 7); other values belong to applications.

---

## 7. Reserved extension points

| Item | Codepoint | Status |
|---|---|---|
| record protection | transport parameter `zmux_record_protection` 0x1dbe26bc | reserved; MUST NOT be sent; ignored on receipt |
| DEADLINE preamble TLV | TLV type 0x03 | reserved; MUST NOT be sent; ignored on receipt |
| carrier implicit acknowledgement | none (adapter behaviour) | reserved; carrier events are not acknowledgements |
| qlog event schema | none | informative: QUIC qlog events plus pipe events |
| padding and shaping hooks | none | adapter level |

---

## 8. GREASE and fixed values

### 8.1 GREASE

| Extension point | GREASE values | Sender rule | Receiver rule |
|---|---|---|---|
| transport parameter IDs | 31 x N + 27 | at least one per QX_TRANSPORT_PARAMETERS, random N, 0 to 16 random bytes (SPEC.md 5.2) | ignore |
| stream preamble TLV types | 0x1f x N + 0x21 | at least one in the first preamble an endpoint sends in a session, 0 to 8 random bytes (EXTENSIONS.md 4.3) | ignore |
| reserved bits of `zmux_multipath` Secret Flags (0xf2) | any | send 0 | ignore |
| frame types | none | not greased: unknown frame types are fatal and new frames are negotiated (SPEC.md 9.7) | |
| reserved bits of PIPE_JOIN Flags (0xf0), PRIORITY_UPDATE and PRIORITY TLV (low 4 bits) | none | send 0 | non-zero is an error (MULTIPATH.md 5.1; EXTENSIONS.md 4.3, 5.2) |

### 8.2 Fixed values

<!-- BEGIN GENERATED: fixed_values -->
| Field | Value | Meaning |
|---|---|---|
| zmux_role Role | 0 | initiator |
| zmux_role Role | 1 | responder |
| zmux_role Role | 2 | auto |
| PIPE_JOIN Flags direction (mask 0x03) | 0 | up |
| PIPE_JOIN Flags direction (mask 0x03) | 1 | down |
| PIPE_JOIN Flags direction (mask 0x03) | 2 | bidi |
| PIPE_JOIN Flags | 0x04 | carrier-bound |
| PIPE_JOIN Flags | 0x08 | standby (initial request for the acceptor's outbound direction) |
| PIPE_JOIN Flags reserved mask | 0xf0 | must be 0 |
| zmux_multipath Secret Flags | 0x01 | E (TLS exporter) |
| zmux_multipath Secret Flags | 0x04 | P (pre-shared key) |
| zmux_multipath Secret Flags | 0x08 | I (insecure join allowed) |
| PIPE_STATUS Status | 0 | available |
| PIPE_STATUS Status | 1 | standby |
| PIPE_STATUS Priority | 0 to 2^62 - 1 | lower preferred; default 0; every value is valid |
<!-- END GENERATED: fixed_values -->

---

## 9. Labels and limits

<!-- BEGIN GENERATED: labels -->
| Name | Value |
|---|---|
| tls_exporter_session | `EXPORTER-zmux-session` |
| tls_exporter_pipe | `EXPORTER-zmux-pipe` |
| hkdf_salt_prefix | `zmux session` |
| hkdf_join_initiator | `zmux join i` |
| hkdf_join_responder | `zmux join r` |
| http_join_header | `Zmux-Pipe-Join` |
| http_pair_header | `Zmux-Pipe-Pair` |
| max_join_frame_bytes | 64 |
| max_locator_bytes | 16 |
| join_token_bytes | 16 |
| lane_key_bytes | 16 |
| default_max_record_size | 16382 |
| zmux_max_record_size | 65535 |
| max_preamble_tp_overhead | 32 |
| pairing_token_bytes | 16 |
| max_unpaired_carriers | 64 |
| budget_capacity | 4096 |
| budget_refill_per_second | 32 |
| sender_probe_rate_per_second | 16 |
| pipe_ping_rate_per_second | 8 |
| first_record_timeout_ms | 2000 |
| unauthenticated_carriers_per_source | 8 |
| probe_interval_ms | 15000 |
| ack_redundancy_ms | 50 |
| unretired_attachments_extra | 8 |
| replay_window_floor_ids | 64 |
| join_refusal_backoff_min_ms | 1000 |
| join_refusal_backoff_max_ms | 60000 |
| unilateral_retirement_floor_ms | 2000 |
| early_data_max_data | 16384 |
| early_data_max_streams_bidi | 4 |
| early_data_max_stream_data | 4096 |
| early_data_max_preamble | 1024 |
| protocol_name | zmux |
| alpn_default | `qmux-02` |
| alpn_also_accepted | `qmux-01` |
| alpn_draft01_alias | qmux-02 -> qmux-01; qmux-02.<application> -> qmux-01.<application>; <name>-02 -> <name>-01; otherwise no alias |
| qmux_draft | draft-ietf-quic-qmux-02 |
<!-- END GENERATED: labels -->

---

## 10. Machine-readable registry

`assets/registry.json` contains the objects `protocol`, `frame_types`,
`transport_parameters`, `transport_errors`, `application_errors`,
`application_error_block`, `preamble_tlvs`, `preamble_tlv_application_range`,
`grease`, `fixed_values`, `labels`, `limits` and `allocation`. Integer
codepoints appear both as numbers (`value`, which may exceed 2^53 for QMux
values, so consumers should prefer the hex form) and as lowercase hex strings
(`hex`). A frame type's `gate` is the transport parameter whose feature
must be in use for the frame to be sent and accepted, null when the frame is
always available, or a list of parameters of which any one suffices
(PIPE_ACK: `zmux_multipath` or `zmux_pipe_ack`).
