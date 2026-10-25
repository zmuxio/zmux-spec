"""Single source of truth for every zmux codepoint.

``tools/build_registry.py`` exports this module to ``assets/registry.json`` and
to the generated tables in REGISTRY.md. ``tools/validate_assets.py`` checks
that the three agree and that every zmux-allocated codepoint obeys the
allocation rules in REGISTRY.md Section 2.

zmux-allocated values were chosen deterministically:

    v = 2**14 + (first 8 bytes of SHA-256("zmux <kind> <name>[#i]") as a
         big-endian integer) mod (2**30 - 2**14)

with the smallest i >= 0 for which v collides with neither an IANA QUIC
registration (at the time of allocation), another zmux value, a transport-parameter
GREASE value (31*N + 27) nor a TLV/error GREASE value (0x1f*N + 0x21).
``ALLOCATION_SUFFIX`` records i when it is not 0.
"""

PROTOCOL_NAME = "zmux"
ALPN_DEFAULT = "qmux-02"
ALPN_ALSO_ACCEPTED = ["qmux-01"]
# Draft-01 aliases of configured ALPN identifiers (SPEC.md Section 6.2):
# "qmux-02" -> "qmux-01"; "qmux-02.<app>" -> "qmux-01.<app>"; other "*-02" -> "*-01";
# anything else has no alias. Byte-exact, case-sensitive.
ALPN_ALIAS_RULE = ["qmux-02 -> qmux-01", "qmux-02.<application> -> qmux-01.<application>",
                   "<name>-02 -> <name>-01", "otherwise no alias"]
ALPN_ALIAS_EXAMPLES = [
    {"configured": "qmux-02", "alias": "qmux-01",
     "not_accepted": ["qmux-00", "qmux-01.x", "qmux"]},
    {"configured": "qmux-02.myapp-v4", "alias": "qmux-01.myapp-v4",
     "not_accepted": ["qmux-01.myapp-v3", "qmux-00.myapp-v4", "myapp-v4",
                      "QMUX-01.myapp-v4"]},
    {"configured": "h3qx-02", "alias": "h3qx-01", "not_accepted": ["h3qx-00", "h3qx"]},
    {"configured": "myapp-02", "alias": "myapp-01", "not_accepted": ["myapp-1"]},
    {"configured": "myapp", "alias": None, "not_accepted": ["myapp-01"]},
]


def alpn_alias(identifier):
    """The draft-01 alias of a configured ALPN identifier, or None."""
    if identifier == "qmux-02":
        return "qmux-01"
    if identifier.startswith("qmux-02."):
        return "qmux-01." + identifier[len("qmux-02."):]
    if identifier.endswith("-02"):
        return identifier[:-3] + "-01"
    return None
QMUX_DRAFT = "draft-ietf-quic-qmux-02"

# --- Frame types ------------------------------------------------------------
# kind: "core"      QUIC v1 frame used by zmux (RFC 9000 Section 19)
#       "qmux"      frame defined by QMux-02 and used unchanged
#       "quic-ext"  QUIC extension frame used unchanged when negotiated
#       "zmux"      zmux-allocated frame (only after negotiation)
#       "prohibited" QUIC v1 frame that MUST NOT appear (FRAME_ENCODING_ERROR)
FRAME_TYPES = [
    # name, first, last, kind, gate, spec, ack_eliciting, retransmission rule
    dict(name="PADDING", first=0x00, last=0x00, kind="core", gate=None,
         spec="SPEC.md#4.3", ack_eliciting=False, retransmit="never"),
    dict(name="PING", first=0x01, last=0x01, kind="prohibited", gate=None,
         spec="SPEC.md#4.4"),
    dict(name="ACK", first=0x02, last=0x03, kind="prohibited", gate=None,
         spec="SPEC.md#4.4"),
    dict(name="RESET_STREAM", first=0x04, last=0x04, kind="core", gate=None,
         spec="SPEC.md#4.3", ack_eliciting=True, retransmit="until-acked-or-stream-terminal"),
    dict(name="STOP_SENDING", first=0x05, last=0x05, kind="core", gate=None,
         spec="SPEC.md#4.3", ack_eliciting=True, retransmit="until-recv-terminal"),
    dict(name="CRYPTO", first=0x06, last=0x06, kind="prohibited", gate=None,
         spec="SPEC.md#4.4"),
    dict(name="NEW_TOKEN", first=0x07, last=0x07, kind="prohibited", gate=None,
         spec="SPEC.md#4.4"),
    dict(name="STREAM", first=0x08, last=0x0F, kind="core", gate=None,
         spec="SPEC.md#4.3", ack_eliciting=True, retransmit="data-unless-reset"),
    dict(name="MAX_DATA", first=0x10, last=0x10, kind="core", gate=None,
         spec="SPEC.md#4.3", ack_eliciting=True, retransmit="current-value"),
    dict(name="MAX_STREAM_DATA", first=0x11, last=0x11, kind="core", gate=None,
         spec="SPEC.md#4.3", ack_eliciting=True, retransmit="current-value-while-recv"),
    dict(name="MAX_STREAMS", first=0x12, last=0x13, kind="core", gate=None,
         spec="SPEC.md#4.3", ack_eliciting=True, retransmit="current-value"),
    dict(name="DATA_BLOCKED", first=0x14, last=0x14, kind="core", gate=None,
         spec="SPEC.md#4.3", ack_eliciting=True, retransmit="while-blocked"),
    dict(name="STREAM_DATA_BLOCKED", first=0x15, last=0x15, kind="core", gate=None,
         spec="SPEC.md#4.3", ack_eliciting=True, retransmit="while-blocked"),
    dict(name="STREAMS_BLOCKED", first=0x16, last=0x17, kind="core", gate=None,
         spec="SPEC.md#4.3", ack_eliciting=True, retransmit="while-blocked"),
    dict(name="NEW_CONNECTION_ID", first=0x18, last=0x18, kind="prohibited", gate=None,
         spec="SPEC.md#4.4"),
    dict(name="RETIRE_CONNECTION_ID", first=0x19, last=0x19, kind="prohibited", gate=None,
         spec="SPEC.md#4.4"),
    dict(name="PATH_CHALLENGE", first=0x1A, last=0x1A, kind="prohibited", gate=None,
         spec="SPEC.md#4.4"),
    dict(name="PATH_RESPONSE", first=0x1B, last=0x1B, kind="prohibited", gate=None,
         spec="SPEC.md#4.4"),
    dict(name="CONNECTION_CLOSE", first=0x1C, last=0x1D, kind="core", gate=None,
         spec="SPEC.md#4.3", ack_eliciting=False, retransmit="never"),
    dict(name="HANDSHAKE_DONE", first=0x1E, last=0x1E, kind="prohibited", gate=None,
         spec="SPEC.md#4.4"),
    dict(name="RESET_STREAM_AT", first=0x24, last=0x24, kind="quic-ext",
         gate="reset_stream_at", spec="SPEC.md#4.3", ack_eliciting=True,
         retransmit="until-acked-or-stream-terminal"),
    dict(name="DATAGRAM", first=0x30, last=0x31, kind="quic-ext",
         gate="max_datagram_frame_size", spec="EXTENSIONS.md#6", ack_eliciting=True,
         retransmit="never"),
    dict(name="QX_TRANSPORT_PARAMETERS", first=0x3F5153300D0A0D0A, last=0x3F5153300D0A0D0A,
         kind="qmux", gate=None, spec="SPEC.md#5.1", ack_eliciting=True, retransmit="never"),
    dict(name="QX_PING", first=0x348C67529EF8C7BD, last=0x348C67529EF8C7BE, kind="qmux",
         gate=None, spec="SPEC.md#4.5", ack_eliciting=True, retransmit="never"),
    # zmux-allocated frames
    dict(name="GOAWAY", first=0x1911F43E, last=0x1911F43E, kind="zmux", gate="zmux_goaway",
         spec="EXTENSIONS.md#3", ack_eliciting=True, retransmit="current-value"),
    dict(name="PRIORITY_UPDATE", first=0x073D08CF, last=0x073D08CF, kind="zmux",
         gate="zmux_priority", spec="EXTENSIONS.md#5", ack_eliciting=True,
         retransmit="latest-per-stream"),
    dict(name="PIPE_JOIN", first=0x1A24DE36, last=0x1A24DE36, kind="zmux",
         gate="zmux_multipath", spec="MULTIPATH.md#5", ack_eliciting=True,
         retransmit="never"),
    dict(name="PIPE_ACK", first=0x0B2B3EEB, last=0x0B2B3EEB, kind="zmux",
         gate=("zmux_multipath", "zmux_pipe_ack"), spec="MULTIPATH.md#6", ack_eliciting=False,
         retransmit="never"),
    dict(name="PIPE_LANE", first=0x3A05A1C9, last=0x3A05A1C9, kind="zmux",
         gate="zmux_max_lanes", spec="MULTIPATH.md#5.8", ack_eliciting=True,
         retransmit="never"),
    dict(name="PIPE_PING", first=0x269B43E6, last=0x269B43E6, kind="zmux",
         gate="zmux_multipath", spec="MULTIPATH.md#6.4", ack_eliciting=True,
         retransmit="never"),
    dict(name="PIPE_ABANDON", first=0x23B35B74, last=0x23B35B74, kind="zmux",
         gate="zmux_multipath", spec="MULTIPATH.md#8", ack_eliciting=True,
         retransmit="until-acked"),
    dict(name="PIPE_STATUS", first=0x227B2811, last=0x227B2811, kind="zmux",
         gate="zmux_multipath", spec="MULTIPATH.md#9", ack_eliciting=True,
         retransmit="latest-per-pipe"),
]

# --- Transport parameters ---------------------------------------------------
# format: "varint" | "empty" | "role" | "multipath" | "lanes"
# scope:  "qmux"   permitted by QMux-02 Section 5.1 or 5.2 (or a QMux extension)
#         "zmux"   zmux-allocated
#         "prohibited"  QUIC v1 parameter prohibited by QMux-02 Section 5.1
TRANSPORT_PARAMETERS = [
    dict(name="original_destination_connection_id", id=0x00, scope="prohibited"),
    dict(name="max_idle_timeout", id=0x01, scope="qmux", format="varint",
         absent_default=0, zmux_sends="30000", spec="SPEC.md#5.3"),
    dict(name="stateless_reset_token", id=0x02, scope="prohibited"),
    dict(name="max_udp_payload_size", id=0x03, scope="prohibited"),
    dict(name="initial_max_data", id=0x04, scope="qmux", format="varint",
         absent_default=0, zmux_sends="1048576", spec="SPEC.md#5.3"),
    dict(name="initial_max_stream_data_bidi_local", id=0x05, scope="qmux", format="varint",
         absent_default=0, zmux_sends="262144", spec="SPEC.md#5.3"),
    dict(name="initial_max_stream_data_bidi_remote", id=0x06, scope="qmux", format="varint",
         absent_default=0, zmux_sends="262144", spec="SPEC.md#5.3"),
    dict(name="initial_max_stream_data_uni", id=0x07, scope="qmux", format="varint",
         absent_default=0, zmux_sends="262144", spec="SPEC.md#5.3"),
    dict(name="initial_max_streams_bidi", id=0x08, scope="qmux", format="varint",
         absent_default=0, zmux_sends="256", spec="SPEC.md#5.3"),
    dict(name="initial_max_streams_uni", id=0x09, scope="qmux", format="varint",
         absent_default=0, zmux_sends="16 (raised to 256 by MAX_STREAMS on the first accept), or the configured value",
         spec="SPEC.md#5.3"),
    dict(name="ack_delay_exponent", id=0x0A, scope="prohibited"),
    dict(name="max_ack_delay", id=0x0B, scope="prohibited"),
    dict(name="disable_active_migration", id=0x0C, scope="prohibited"),
    dict(name="preferred_address", id=0x0D, scope="prohibited"),
    dict(name="active_connection_id_limit", id=0x0E, scope="prohibited"),
    dict(name="initial_source_connection_id", id=0x0F, scope="prohibited"),
    dict(name="retry_source_connection_id", id=0x10, scope="prohibited"),
    dict(name="reset_stream_at", id=0x1D, scope="qmux", format="empty",
         absent_default="not supported", zmux_sends="always", spec="SPEC.md#5.3"),
    dict(name="max_datagram_frame_size", id=0x20, scope="qmux", format="varint",
         absent_default=0, zmux_sends="own max_record_size (65535) when datagrams are enabled",
         spec="EXTENSIONS.md#6"),
    dict(name="max_record_size", id=0x0571C59429CD0845, scope="qmux", format="varint",
         absent_default=16382, zmux_sends="65535", spec="SPEC.md#3.2"),
    # zmux-allocated
    dict(name="zmux_role", id=0x17240207, scope="zmux", format="role",
         absent_default="transport role", zmux_sends="when configured", spec="EXTENSIONS.md#2"),
    dict(name="zmux_goaway", id=0x04528A5F, scope="zmux", format="empty",
         absent_default="not supported", zmux_sends="always", spec="EXTENSIONS.md#3"),
    dict(name="zmux_preamble", id=0x0439E07A, scope="zmux", format="varint",
         absent_default="not supported",
         zmux_sends="4096 (less when the stream windows are smaller)", spec="EXTENSIONS.md#4"),
    dict(name="zmux_priority", id=0x31E7139D, scope="zmux", format="empty",
         absent_default="not supported", zmux_sends="always", spec="EXTENSIONS.md#5"),
    dict(name="zmux_datagram_flows", id=0x0AF391ED, scope="zmux", format="empty",
         absent_default="not supported", zmux_sends="when datagrams are enabled",
         spec="EXTENSIONS.md#6"),
    dict(name="zmux_multipath", id=0x30DC2978, scope="zmux", format="multipath",
         absent_default="not supported", zmux_sends="when more than one pipe or resume is enabled",
         spec="MULTIPATH.md#3"),
    dict(name="zmux_pipe_ack", id=0x34B47621, scope="zmux", format="empty",
         absent_default="not supported", zmux_sends="always", spec="MULTIPATH.md#3.5"),
    dict(name="zmux_ack_threshold", id=0x2762C7E5, scope="zmux", format="varint",
         absent_default=65536, zmux_sends="omitted (65536)", spec="MULTIPATH.md#3.2"),
    dict(name="zmux_max_ack_delay", id=0x17FA1ED6, scope="zmux", format="varint",
         absent_default=25, zmux_sends="omitted (25)", spec="MULTIPATH.md#3.2"),
    dict(name="zmux_max_lanes", id=0x27856EF0, scope="zmux", format="lanes",
         absent_default="1 lane (no lanes)",
         zmux_sends="always: Max Lanes 258 and a random 16-byte Lane Key",
         spec="MULTIPATH.md#3.6"),
    dict(name="zmux_resume", id=0x0D5778CE, scope="zmux", format="varint",
         absent_default="not supported", zmux_sends="the resume grace in ms when resume is enabled",
         spec="RESUME.md#3"),
    dict(name="zmux_record_protection", id=0x1DBE26BC, scope="zmux-reserved", format="opaque",
         absent_default="not supported", zmux_sends="never (reserved)", spec="REGISTRY.md#7"),
]

ALLOCATION_SUFFIX = {
    ("transport_error", "ZMUX_PIPE_AUTH_FAILED"): 1,
}

# --- Transport error codes (CONNECTION_CLOSE 0x1c, PIPE_ABANDON) ------------
TRANSPORT_ERRORS = [
    dict(name="NO_ERROR", code=0x00, source="RFC 9000", wire=True),
    dict(name="INTERNAL_ERROR", code=0x01, source="RFC 9000", wire=True),
    dict(name="CONNECTION_REFUSED", code=0x02, source="RFC 9000", wire=True),
    dict(name="FLOW_CONTROL_ERROR", code=0x03, source="RFC 9000", wire=True),
    dict(name="STREAM_LIMIT_ERROR", code=0x04, source="RFC 9000", wire=True),
    dict(name="STREAM_STATE_ERROR", code=0x05, source="RFC 9000", wire=True),
    dict(name="FINAL_SIZE_ERROR", code=0x06, source="RFC 9000", wire=True),
    dict(name="FRAME_ENCODING_ERROR", code=0x07, source="RFC 9000", wire=True),
    dict(name="TRANSPORT_PARAMETER_ERROR", code=0x08, source="RFC 9000", wire=True),
    dict(name="PROTOCOL_VIOLATION", code=0x0A, source="RFC 9000", wire=True),
    dict(name="NO_VIABLE_PATH", code=0x10, source="RFC 9000", wire=True),
    dict(name="ZMUX_EXCESSIVE_LOAD", code=0x25FF75EF, source="zmux", wire=True),
    dict(name="ZMUX_ROLE_CONFLICT", code=0x277DB2C3, source="zmux", wire=True),
    dict(name="ZMUX_RESUME_TIMEOUT", code=0x0EB87167, source="zmux", wire=True),
    dict(name="ZMUX_RESUME_FAILED", code=0x0F05F74E, source="zmux", wire=True),
    dict(name="ZMUX_PIPE_FAILED", code=0x296A5BF5, source="zmux", wire=True),
    dict(name="ZMUX_PIPE_RESOURCE_LIMIT", code=0x0B4FA113, source="zmux", wire=True),
    dict(name="ZMUX_PIPE_AUTH_FAILED", code=0x09613938, source="zmux", wire=False),
    dict(name="ZMUX_IDLE_TIMEOUT", code=0x2736D55C, source="zmux", wire=True),
]

# --- Application error code conventions (0x1d, RESET_STREAM, STOP_SENDING,
#     RESET_STREAM_AT, GOAWAY) ------------------------------------------------
APP_ERROR_BASE = 0x5A4D0000
APPLICATION_ERRORS = [
    dict(name="NO_ERROR", code=0x00, socks5_rep=None),
    dict(name="ZMUX_CANCELLED", code=0x5A4D0001, socks5_rep=None),
    dict(name="ZMUX_REJECTED", code=0x5A4D0002, socks5_rep=None),
    dict(name="ZMUX_CARRIER_FAILED", code=0x5A4D0003, socks5_rep=None),
    dict(name="ZMUX_PROXY_GENERAL_FAILURE", code=0x5A4D0101, socks5_rep=0x01),
    dict(name="ZMUX_PROXY_NOT_ALLOWED", code=0x5A4D0102, socks5_rep=0x02),
    dict(name="ZMUX_PROXY_NETWORK_UNREACHABLE", code=0x5A4D0103, socks5_rep=0x03),
    dict(name="ZMUX_PROXY_HOST_UNREACHABLE", code=0x5A4D0104, socks5_rep=0x04),
    dict(name="ZMUX_PROXY_CONNECTION_REFUSED", code=0x5A4D0105, socks5_rep=0x05),
    dict(name="ZMUX_PROXY_TTL_EXPIRED", code=0x5A4D0106, socks5_rep=0x06),
    dict(name="ZMUX_PROXY_COMMAND_NOT_SUPPORTED", code=0x5A4D0107, socks5_rep=0x07),
    dict(name="ZMUX_PROXY_ADDRESS_TYPE_NOT_SUPPORTED", code=0x5A4D0108, socks5_rep=0x08),
    dict(name="ZMUX_PROXY_DNS_FAILURE", code=0x5A4D0110, socks5_rep=0x04),
    dict(name="ZMUX_PROXY_CONNECT_TIMEOUT", code=0x5A4D0111, socks5_rep=0x06),
]

# --- Stream preamble TLV types ---------------------------------------------
PREAMBLE_TLVS = [
    dict(name="OPEN_INFO", type=0x01, status="active", value="opaque bytes, 0..(limit) bytes",
         spec="EXTENSIONS.md#4.3"),
    dict(name="PRIORITY", type=0x02, status="active",
         value="1 byte: urgency (3 bits) | incremental (1 bit) | 0000", spec="EXTENSIONS.md#4.3"),
    dict(name="DEADLINE", type=0x03, status="reserved",
         value="reserved: relative deadline in ms (varint); MUST NOT be sent",
         spec="EXTENSIONS.md#4.3"),
    dict(name="PIPE_CARRIER", type=0x05, status="active",
         value="empty: the stream carries a pipe pair of a nested session", spec="EXTENSIONS.md#4.3"),
]
# Application TLV types: never allocated by zmux, delivered to the application (GREASE values
# inside the range stay GREASE).
APP_TLV_FIRST = 0x1000
APP_TLV_LAST = 0x3FFF

# --- PIPE_JOIN flags, multipath secret flags --------------------------------
PIPE_DIRECTIONS = {0: "up", 1: "down", 2: "bidi"}
PIPE_JOIN_FLAG_CARRIER_BOUND = 0x04
PIPE_JOIN_FLAG_STANDBY = 0x08
PIPE_JOIN_RESERVED_MASK = 0xF0
MULTIPATH_SECRET_FLAGS = {0x01: "E (TLS exporter)", 0x04: "P (pre-shared key)",
                          0x08: "I (insecure join allowed)"}
PIPE_STATUS_VALUES = {0: "available", 1: "standby"}
PIPE_PRIORITY_DEFAULT = 0
PIPE_WEIGHT_DEFAULT = 1
ROLE_VALUES = {0: "initiator", 1: "responder", 2: "auto"}

# --- Labels ------------------------------------------------------------------
LABELS = {
    "tls_exporter_session": "EXPORTER-zmux-session",
    "tls_exporter_pipe": "EXPORTER-zmux-pipe",
    "hkdf_salt_prefix": "zmux session",
    "hkdf_join_initiator": "zmux join i",
    "hkdf_join_responder": "zmux join r",
    "http_join_header": "Zmux-Pipe-Join",
    "http_pair_header": "Zmux-Pipe-Pair",
}

LIMITS = {
    "max_join_frame_bytes": 64,
    "max_locator_bytes": 16,
    "join_token_bytes": 16,
    "lane_key_bytes": 16,
    "default_max_record_size": 16382,
    "zmux_max_record_size": 65535,
    "max_preamble_tp_overhead": 32,
    "pairing_token_bytes": 16,
    "max_unpaired_carriers": 64,
    "budget_capacity": 4096,
    "budget_refill_per_second": 32,
    "sender_probe_rate_per_second": 16,
    "pipe_ping_rate_per_second": 8,
    "first_record_timeout_ms": 2000,
    "unauthenticated_carriers_per_source": 8,
    "probe_interval_ms": 15000,
    "ack_redundancy_ms": 50,
    "unretired_attachments_extra": 8,
    "replay_window_floor_ids": 64,
    "join_refusal_backoff_min_ms": 1000,
    "join_refusal_backoff_max_ms": 60000,
    "unilateral_retirement_floor_ms": 2000,
    "early_data_max_data": 16384,
    "early_data_max_streams_bidi": 4,
    "early_data_max_stream_data": 4096,
    "early_data_max_preamble": 1024,
}


def grease_tp(n):
    return 31 * n + 27


def is_grease_tp(v):
    return v % 31 == 27


def grease_tlv(n):
    return 0x1F * n + 0x21


def is_grease_tlv(v):
    return v >= 0x21 and (v - 0x21) % 0x1F == 0


def frame_by_name(name):
    for f in FRAME_TYPES:
        if f["name"] == name:
            return f
    raise KeyError(name)


def frame_by_type(t):
    for f in FRAME_TYPES:
        if f["first"] <= t <= f["last"]:
            return f
    return None


def tp_by_name(name):
    for p in TRANSPORT_PARAMETERS:
        if p["name"] == name:
            return p
    raise KeyError(name)


def tp_by_id(i):
    for p in TRANSPORT_PARAMETERS:
        if p["id"] == i:
            return p
    return None


def transport_error(name):
    for e in TRANSPORT_ERRORS:
        if e["name"] == name:
            return e["code"]
    raise KeyError(name)


def transport_error_name(code):
    for e in TRANSPORT_ERRORS:
        if e["code"] == code:
            return e["name"]
    return None


def app_error(name):
    for e in APPLICATION_ERRORS:
        if e["name"] == name:
            return e["code"]
    raise KeyError(name)


def tlv_by_name(name):
    for t in PREAMBLE_TLVS:
        if t["name"] == name:
            return t
    raise KeyError(name)
