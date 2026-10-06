#!/usr/bin/env python3
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
FIXTURES = ROOT / "fixtures"

# Share the case-set builder's portable-state rules without leaving __pycache__ in tools/.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_case_sets import PORTABLE_EVENTS, PORTABLE_RESULTS, is_portable_state_case  # noqa: E402


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def require(condition: bool, message: str):
    if not condition:
        raise ValueError(message)


def is_hex_string(s: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]*", s)) and len(s) % 2 == 0


def format_scalar(value):
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def to_yaml_lines(value, indent=0):
    prefix = " " * indent
    lines = []

    if isinstance(value, dict):
        for key, item in value.items():
            rendered_key = json.dumps(str(key), ensure_ascii=False)
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}{rendered_key}:")
                lines.extend(to_yaml_lines(item, indent + 2))
            else:
                lines.append(f"{prefix}{rendered_key}: {format_scalar(item)}")
        return lines

    if isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}-")
                lines.extend(to_yaml_lines(item, indent + 2))
            else:
                lines.append(f"{prefix}- {format_scalar(item)}")
        return lines

    return [f"{prefix}{format_scalar(value)}"]


class VarintError(Exception):
    pass


class FrameRejected(Exception):
    def __init__(self, code: str, reason: str):
        super().__init__(f"{code}: {reason}")
        self.code = code


def parse_varint(buf: bytes, offset: int = 0):
    """Decode one varint62 at offset; raise VarintError when truncated or non-canonical."""
    if offset >= len(buf):
        raise VarintError("truncated")
    length = 1 << (buf[offset] >> 6)
    if offset + length > len(buf):
        raise VarintError("truncated")
    value = buf[offset] & 0x3F
    for byte in buf[offset + 1 : offset + length]:
        value = (value << 8) | byte
    if value < 64:
        shortest = 1
    elif value < 1 << 14:
        shortest = 2
    elif value < 1 << 30:
        shortest = 4
    else:
        shortest = 8
    if length != shortest:
        raise VarintError("non-canonical")
    return value, length


def payload_varint(payload: bytes, offset: int, field: str):
    """Mandatory payload varint: any decode failure is a malformed payload (SPEC 3.2/4.3)."""
    try:
        return parse_varint(payload, offset)
    except VarintError as exc:
        raise FrameRejected("FRAME_SIZE", f"{field} {exc}")


def walk_tlvs(payload: bytes, offset: int, container: str):
    """Structural TLV walk; container-structural errors are FRAME_SIZE (SPEC 7.2)."""
    while offset < len(payload):
        _, n = payload_varint(payload, offset, f"{container} TLV type")
        offset += n
        length, n = payload_varint(payload, offset, f"{container} TLV length")
        offset += n
        if length > len(payload) - offset:
            raise FrameRejected("FRAME_SIZE", f"{container} TLV value overruns payload")
        offset += length


# Standardized STREAM-METADATA-TLVs whose value format is one varint62 (REGISTRY 5.1).
VARINT_METADATA_TLVS = {"stream_priority", "stream_group"}


def check_metadata_values(block: bytes, offset: int, registry, container: str):
    """Standardized varint62 STREAM-METADATA values of a structurally valid TLV block.

    Values are interpreted in TLV order until a duplicate singleton drops the block;
    an invalid value before that point makes the payload malformed (SPEC 3.2, 7.2).
    DIAG-TLV values are never judged: an invalid one is ignored (SPEC 7.1).
    """
    names = registry["tlv_namespaces"]["STREAM_METADATA_TLV"]
    seen = set()
    for typ, value in read_tlvs(block, offset):
        name = names.get(str(typ))
        if name is None:
            continue
        if typ in seen:
            return
        seen.add(typ)
        if name not in VARINT_METADATA_TLVS:
            continue
        try:
            _, used = parse_varint(value, 0)
        except VarintError as exc:
            raise FrameRejected("FRAME_SIZE", f"{container} {name} value {exc}")
        if used != len(value):
            raise FrameRejected("FRAME_SIZE", f"{container} {name} value has trailing bytes")


def reference_frame_check(raw: bytes, registry, limits=None):
    """Reference classification of one complete frame per the SPEC 3.2/4.3/6.11/7.2 error codes.

    Returns None for a structurally valid frame or raises FrameRejected with the session
    error name. It models codec-level rules only (no negotiated capabilities or stream state).
    """
    defaults = registry["defaults"]
    limits = dict(limits or {})
    max_frame_payload = limits.get("max_frame_payload") or defaults["max_frame_payload"]
    max_control = limits.get("max_control_payload_bytes") or defaults["max_control_payload_bytes"]
    max_extension = limits.get("max_extension_payload_bytes") or defaults["max_extension_payload_bytes"]
    frame_types = {int(code): name for code, name in registry["frame_types"].items()}

    try:
        frame_length, n = parse_varint(raw, 0)
    except VarintError as exc:
        raise FrameRejected("PROTOCOL", f"frame_length {exc}")
    if frame_length < 2:
        raise FrameRejected("FRAME_SIZE", "frame_length < 2")
    require(len(raw) >= n + 2, "reference check needs the frame code and stream_id")
    code = raw[n]
    frame_type = frame_types.get(code & 0x1F)
    flags = code & 0xE0
    if frame_type is None:
        raise FrameRejected("PROTOCOL", "unknown core frame type")
    stream_len = 1 << (raw[n + 1] >> 6)
    if frame_length < 1 + stream_len:
        raise FrameRejected("FRAME_SIZE", "frame_length < 1 + encoded_length(stream_id)")
    require(len(raw) >= n + 1 + stream_len, "reference check needs the complete stream_id")
    try:
        stream_id, _ = parse_varint(raw, n + 1)
    except VarintError as exc:
        raise FrameRejected("PROTOCOL", f"stream_id {exc}")
    if frame_type == "DATA":
        payload_limit = max_frame_payload
    elif frame_type == "EXT":
        payload_limit = max_extension
    else:
        payload_limit = max_control
    # Receivers reject an oversized frame from its header, before reading the payload.
    if frame_length - 1 - stream_len > payload_limit:
        raise FrameRejected("FRAME_SIZE", "payload exceeds receiver limit")
    require(len(raw) >= n + frame_length, "reference check needs a complete frame")
    payload = raw[n + 1 + stream_len : n + frame_length]
    if frame_type == "DATA":
        if flags & 0x80:
            raise FrameRejected("PROTOCOL", "reserved DATA flag")
    elif flags:
        raise FrameRejected("PROTOCOL", "non-zero flags on non-DATA frame")
    if frame_type in {"DATA", "STOP_SENDING", "RESET", "ABORT"} and stream_id == 0:
        raise FrameRejected("PROTOCOL", f"{frame_type} on stream_id 0")
    if frame_type in {"PING", "PONG", "GOAWAY", "CLOSE"} and stream_id != 0:
        raise FrameRejected("PROTOCOL", f"{frame_type} on non-zero stream_id")

    if frame_type in {"MAX_DATA", "BLOCKED"}:
        _, used = payload_varint(payload, 0, frame_type)
        if used != len(payload):
            raise FrameRejected("PROTOCOL", f"trailing bytes after {frame_type} value")
    elif frame_type in {"PING", "PONG"}:
        if len(payload) < 8:
            raise FrameRejected("FRAME_SIZE", f"{frame_type} payload shorter than 8 bytes")
    elif frame_type in {"STOP_SENDING", "RESET", "ABORT", "CLOSE"}:
        _, used = payload_varint(payload, 0, "error_code")
        walk_tlvs(payload, used, "DIAG")
    elif frame_type == "GOAWAY":
        offset = 0
        for field in ("last_accepted_bidi_stream_id", "last_accepted_uni_stream_id", "error_code"):
            _, used = payload_varint(payload, offset, field)
            offset += used
        walk_tlvs(payload, offset, "DIAG")
    elif frame_type == "EXT":
        try:
            ext_type, used = parse_varint(payload, 0)
        except VarintError as exc:
            raise FrameRejected("PROTOCOL", f"ext_type {exc}")
        if registry["ext_subtypes"].get(str(ext_type)) == "PRIORITY_UPDATE":
            if stream_id == 0:
                raise FrameRejected("PROTOCOL", "PRIORITY_UPDATE on stream_id 0")
            walk_tlvs(payload, used, "PRIORITY_UPDATE")
            check_metadata_values(payload, used, registry, "PRIORITY_UPDATE")
    elif frame_type == "DATA" and flags & 0x20:
        metadata_len, used = payload_varint(payload, 0, "metadata_len")
        if metadata_len > len(payload) - used:
            raise FrameRejected("FRAME_SIZE", "metadata_len overruns DATA payload")
        walk_tlvs(payload[: used + metadata_len], used, "OPEN_METADATA")
        check_metadata_values(payload[: used + metadata_len], used, registry, "OPEN_METADATA")
    return None


def encode_varint(value: int) -> bytes:
    """Canonical varint62 encoding."""
    for length, prefix in ((1, 0x00), (2, 0x40), (4, 0x80), (8, 0xC0)):
        if value < 1 << (8 * length - 2):
            raw = bytearray(value.to_bytes(length, "big"))
            raw[0] |= prefix
            return bytes(raw)
    raise ValueError(f"{value} exceeds varint62")


def frame_extent(raw: bytes) -> int:
    """Bytes a frame case may carry: the frame itself or, when frame_length is too
    small for the stream_id, the complete announced stream_id encoding."""
    frame_length, n = parse_varint(raw, 0)
    extent = n + frame_length
    if len(raw) > n + 1:
        extent = max(extent, n + 1 + (1 << (raw[n + 1] >> 6)))
    return extent


def read_tlvs(block: bytes, offset: int = 0):
    """(type, value) pairs of a TLV block that already passed walk_tlvs."""
    tlvs = []
    while offset < len(block):
        typ, n = parse_varint(block, offset)
        offset += n
        length, n = parse_varint(block, offset)
        offset += n
        tlvs.append((typ, block[offset : offset + length]))
        offset += length
    return tlvs


def repeats_singleton(tlvs, singleton_types) -> bool:
    seen = set()
    for typ, _ in tlvs:
        if typ in singleton_types:
            if typ in seen:
                return True
            seen.add(typ)
    return False


def decode_metadata_tlvs(block: bytes, offset: int, registry, decoded, dropped_key: str):
    names = registry["tlv_namespaces"]["STREAM_METADATA_TLV"]
    tlvs = read_tlvs(block, offset)
    if repeats_singleton(tlvs, {int(t) for t in names}):
        decoded[dropped_key] = True
        tlvs = []
    decoded["stream_metadata_tlvs"] = [
        {"type": names.get(str(typ), f"stream_metadata_type({typ})"), "raw": value} for typ, value in tlvs
    ]


def decode_diag_tlvs(payload: bytes, offset: int, registry, decoded):
    names = registry["tlv_namespaces"]["DIAG_TLV"]
    tlvs = read_tlvs(payload, offset)
    if repeats_singleton(tlvs, {int(t) for t in names}):
        decoded["diag_block_dropped"] = True
        return
    for typ, value in tlvs:
        if names.get(str(typ)) == "debug_text":
            try:
                decoded["debug_text"] = value.decode("utf-8")
            except UnicodeDecodeError:
                pass


def decode_frame(raw: bytes, registry, limits=None):
    """Decode one structurally valid frame into the field names the wire corpus uses."""
    reference_frame_check(raw, registry, limits)
    frame_length, n = parse_varint(raw, 0)
    code = raw[n]
    stream_id, stream_len = parse_varint(raw, n + 1)
    payload = raw[n + 1 + stream_len : n + frame_length]
    frame_type = registry["frame_types"][str(code & 0x1F)]
    frame = {
        "frame_length": frame_length,
        "frame_type": frame_type,
        "code": code,
        "flags": [name for bit, name in ((0x20, "OPEN_METADATA"), (0x40, "FIN")) if code & bit],
        "stream_id": stream_id,
        "payload": payload,
        "payload_hex": payload.hex(),
    }
    decoded = {}
    if frame_type == "MAX_DATA":
        decoded["max_offset"] = parse_varint(payload, 0)[0]
    elif frame_type == "BLOCKED":
        decoded["blocked_at"] = parse_varint(payload, 0)[0]
    elif frame_type in {"STOP_SENDING", "RESET", "ABORT", "CLOSE"}:
        decoded["error_code"], used = parse_varint(payload, 0)
        decode_diag_tlvs(payload, used, registry, decoded)
    elif frame_type == "GOAWAY":
        goaway, offset = {}, 0
        for field in ("last_accepted_bidi_stream_id", "last_accepted_uni_stream_id", "error_code"):
            goaway[field], used = parse_varint(payload, offset)
            offset += used
        decoded["goaway"] = goaway
        decode_diag_tlvs(payload, offset, registry, decoded)
    elif frame_type == "EXT":
        ext_type, used = parse_varint(payload, 0)
        decoded["ext_type_value"] = ext_type
        ext_name = registry["ext_subtypes"].get(str(ext_type))
        if ext_name:
            decoded["ext_type"] = ext_name
        if ext_name == "PRIORITY_UPDATE":
            decode_metadata_tlvs(payload, used, registry, decoded, "priority_update_dropped")
    elif frame_type == "DATA":
        if code & 0x20:
            metadata_len, used = parse_varint(payload, 0)
            decoded["metadata_len"] = metadata_len
            decode_metadata_tlvs(
                payload[: used + metadata_len], used, registry, decoded, "open_metadata_block_dropped"
            )
            decoded["application_payload_hex"] = payload[used + metadata_len :].hex()
        else:
            decoded["application_payload_hex"] = payload.hex()
    frame["decoded"] = decoded
    return frame


def ping_padding_tag(key: int, token: int) -> int:
    """SPEC 6.4 ping_padding_tag over unsigned 64-bit arithmetic."""
    mask = (1 << 64) - 1
    z = (key ^ token ^ 0x6D1D9F6D33F9772D) & mask
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & mask
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & mask
    return z ^ (z >> 31)


class PrefaceRejected(Exception):
    def __init__(self, code: str, reason: str):
        super().__init__(f"{code}: {reason}")
        self.code = code


def reference_preface_check(raw: bytes, registry):
    """Reference parse of one complete session preface (SPEC 2.1-2.6, 3.2, 5.1).

    Returns the decoded fields or raises PrefaceRejected with the establishment CLOSE
    code SPEC 2.4 and 2.6 recommend. Checks that need both prefaces (role conflicts,
    protocol-version overlap) are not modeled.
    """
    proto = registry["protocol"]
    require(len(raw) >= 6, "reference check needs the fixed preface fields")
    if raw[:4] != bytes.fromhex(proto["magic_hex"]):
        raise PrefaceRejected("PROTOCOL", "invalid magic")
    if raw[4] != proto["preface_ver"]:
        raise PrefaceRejected("UNSUPPORTED_VERSION", "unsupported preface_ver")
    role = registry["roles"].get(str(raw[5]))
    if role is None:
        raise PrefaceRejected("PROTOCOL", "invalid role")
    preface = {"preface_ver": raw[4], "role": role}
    offset = 6
    for field in ("tie_breaker_nonce", "min_proto", "max_proto", "capabilities", "settings_len"):
        try:
            preface[field], used = parse_varint(raw, offset)
        except VarintError as exc:
            raise PrefaceRejected("PROTOCOL", f"{field} {exc}")
        offset += used
    # A receiver rejects an oversized settings block from its length, before reading it.
    if preface["settings_len"] > proto["max_preface_settings_bytes"]:
        raise PrefaceRejected("FRAME_SIZE", "settings_len exceeds max_preface_settings_bytes")
    require(len(raw) == offset + preface["settings_len"], "reference check needs exactly one complete preface")
    block = raw[offset:]
    settings, seen, offset = {}, set(), 0
    while offset < len(block):
        try:
            setting_id, used = parse_varint(block, offset)
            offset += used
            length, used = parse_varint(block, offset)
            offset += used
        except VarintError as exc:
            raise PrefaceRejected("PROTOCOL", f"settings TLV header {exc}")
        if length > len(block) - offset:
            raise PrefaceRejected("PROTOCOL", "settings TLV value overruns settings_tlv")
        value = block[offset : offset + length]
        offset += length
        if setting_id in seen:
            raise PrefaceRejected("PROTOCOL", f"duplicate setting ID {setting_id}")
        seen.add(setting_id)
        name = registry["settings"].get(str(setting_id))
        # Unknown IDs and preface_padding values are never interpreted (SPEC 5.1, 5.5).
        if name is None or name == "preface_padding":
            continue
        try:
            settings[name], used = parse_varint(value, 0)
        except VarintError as exc:
            raise PrefaceRejected("PROTOCOL", f"setting {name} {exc}")
        if used != len(value):
            raise PrefaceRejected("PROTOCOL", f"setting {name} has trailing bytes")
    preface["settings"] = settings
    preface["settings_raw"] = block
    if role == "auto" and preface["tie_breaker_nonce"] == 0:
        raise PrefaceRejected("PROTOCOL", "role = auto with tie_breaker_nonce = 0")
    for name, minimum in registry["compatibility_minima"].items():
        if settings.get(name, registry["defaults"][name]) < minimum:
            raise PrefaceRejected("PROTOCOL", f"{name} below its minimum")
    return preface


def validate_manifest(manifest, files_present):
    require(manifest.get("schema") == "zmux-assets-manifest-v1", "invalid manifest schema")
    require(isinstance(manifest.get("assets"), list) and manifest["assets"], "manifest assets missing")
    seen = set()
    for item in manifest["assets"]:
        path = item["path"]
        require(path not in seen, f"duplicate manifest entry: {path}")
        seen.add(path)
        require((ROOT / path).exists(), f"manifest path does not exist: {path}")
    for path in files_present:
        if path.name.endswith(".json") and path.name != "manifest.json":
            logical = f"assets/{path.name}"
            require(logical in seen, f"asset missing from manifest: {logical}")


def validate_registry(registry):
    require(registry.get("schema") == "zmux-registry-v1", "invalid registry schema")
    proto = registry["protocol"]
    require(proto["integer_encoding"] == "varint62", "unexpected integer encoding")
    require(proto["integer_byte_order"] == "big-endian", "unexpected integer byte order")
    require(proto["integer_max"] == 2**62 - 1, "unexpected integer max")
    validate_registry_coverage(registry)
    minima = registry.get("compatibility_minima", {})
    require(minima.get("max_frame_payload") == 16384, "unexpected frame-payload compatibility minimum")
    require(minima.get("max_control_payload_bytes") == 4096, "unexpected control-payload compatibility minimum")
    require(minima.get("max_extension_payload_bytes") == 4096, "unexpected extension-payload compatibility minimum")


RESERVED_RANGE_KINDS = {
    "reserved",
    "reserved_for_future_standard_assignment",
    "experimental",
    "private_use",
    "standard_extension_space",
}

# Closed namespaces end at a fixed value; open ones end in an open-ended range.
NAMESPACE_LAST_VALUE = {"capability_bits": 61, "frame_types": 31}
OPEN_NAMESPACE_PROBE = 1024


def registry_assignments(registry):
    """Assigned values per reserved_ranges namespace."""
    capability_bits = set()
    for mask in registry["capabilities"]:
        mask = int(mask)
        require(mask > 0 and mask & (mask - 1) == 0, f"capability {mask} is not a single bit")
        capability_bits.add(mask.bit_length() - 1)
    tlv = registry["tlv_namespaces"]
    return {
        "capability_bits": capability_bits,
        "frame_types": {int(k) for k in registry["frame_types"]},
        "setting_ids": {int(k) for k in registry["settings"]},
        "stream_metadata_tlv_types": {int(k) for k in tlv["STREAM_METADATA_TLV"]},
        "diag_tlv_types": {int(k) for k in tlv["DIAG_TLV"]},
        "ext_subtype_ids": {int(k) for k in registry["ext_subtypes"]},
    }


def validate_registry_coverage(registry):
    """Every value of every namespace is assigned or in exactly one reserved range."""
    ranges = registry.get("reserved_ranges", {})
    assignments = registry_assignments(registry)
    require(set(ranges) == set(assignments), f"reserved_ranges namespaces {sorted(ranges)} != {sorted(assignments)}")
    for namespace, assigned in assignments.items():
        last = NAMESPACE_LAST_VALUE.get(namespace)
        probe_end = last if last is not None else OPEN_NAMESPACE_PROBE
        owners = {value: ["assigned"] for value in assigned}
        open_ended = False
        for item in ranges[namespace]:
            require(item.get("kind") in RESERVED_RANGE_KINDS, f"{namespace} range has unknown kind {item.get('kind')}")
            start, end = item["from"], item["to"]
            if end is None:
                require(last is None, f"{namespace} is closed but has an open-ended range")
                open_ended = True
                end = probe_end
            require(start <= end, f"{namespace} range {start}-{end} is empty")
            for value in range(start, end + 1):
                owners.setdefault(value, []).append(item["kind"])
        for value in range(0, probe_end + 1):
            require(value in owners, f"{namespace} value {value} is neither assigned nor reserved")
            require(len(owners[value]) == 1, f"{namespace} value {value} is covered twice: {owners[value]}")
        require(max(owners) <= probe_end, f"{namespace} covers values beyond {probe_end}")
        if last is None:
            require(open_ended, f"{namespace} needs an open-ended range")


def registry_md_sections():
    """REGISTRY.md bullet and table lines keyed by section number."""
    sections, current, bullet = {}, None, None
    for line in (ROOT / "REGISTRY.md").read_text(encoding="utf-8").split("\n"):
        heading = re.match(r"^#{2,3} (\d+(?:\.\d+)?)\.? ", line)
        if heading:
            current = heading.group(1)
            sections[current] = []
            continue
        if current is None:
            continue
        if line.startswith("- "):
            sections[current].append(line[2:])
        elif line.startswith("  ") and sections[current] and line.strip():
            sections[current][-1] += " " + line.strip()
        elif line.startswith("|"):
            sections[current].append(line)
    return sections


def registry_md_values(lines):
    """Bullets of the form `N` = name."""
    values = {}
    for line in lines:
        match = re.match(r"^`(\d+)` = (\S+)", line)
        if match:
            values[match.group(1)] = match.group(2)
    return values


RANGE_NAMESPACES = (
    ("capability bit position", "capability_bits"),
    ("frame type", "frame_types"),
    ("setting ID", "setting_ids"),
    ("STREAM-METADATA-TLV type", "stream_metadata_tlv_types"),
    ("DIAG-TLV type", "diag_tlv_types"),
    ("`EXT` subtype ID", "ext_subtype_ids"),
)

RANGE_KIND_PHRASES = (
    ("reserved for future standard assignment", "reserved_for_future_standard_assignment"),
    ("standard-extension space", "standard_extension_space"),
    ("experimental", "experimental"),
    ("private-use", "private_use"),
    ("reserved", "reserved"),
)


def validate_registry_md(registry):
    """Cross-check registry.json against the normative REGISTRY.md lists."""
    sections = registry_md_sections()
    proto = registry["protocol"]
    constants = {}
    for line in sections["1"]:
        match = re.match(r'^`(\w+) = "?(\w+)"?`$', line)
        if match:
            constants[match.group(1)] = match.group(2)
    require(constants.get("magic") == proto["magic_ascii"], "REGISTRY.md magic differs from registry.json")
    for name in ("preface_ver", "proto_ver", "max_preface_settings_bytes"):
        require(int(constants.get(name, -1)) == proto[name], f"REGISTRY.md {name} differs from registry.json")
    checks = (
        ("1", registry["roles"], "role values"),
        ("2", registry["settings"], "setting IDs"),
        ("2.2", registry["scheduler_hints"], "scheduler_hints"),
        ("4", registry["frame_types"], "frame types"),
        ("5.1", registry["tlv_namespaces"]["STREAM_METADATA_TLV"], "STREAM-METADATA-TLV types"),
        ("5.2", registry["tlv_namespaces"]["DIAG_TLV"], "DIAG-TLV types"),
        ("7", registry["ext_subtypes"], "EXT subtypes"),
    )
    for section, expected, label in checks:
        require(registry_md_values(sections[section]) == expected, f"REGISTRY.md {label} differ from registry.json")
    defaults = {}
    for line in sections["2.1"]:
        match = re.match(r"^`(\w+) = (\d+)`$", line)
        if match:
            defaults[match.group(1)] = int(match.group(2))
    require(defaults == registry["defaults"], "REGISTRY.md default values differ from registry.json")
    minima = {}
    for line in sections["2.4"]:
        match = re.search(r"`(\w+) >= (\d+)`", line)
        if match:
            minima[match.group(1)] = int(match.group(2))
    require(minima == registry["compatibility_minima"], "REGISTRY.md minima differ from registry.json")
    capabilities, flags = {}, {}
    for line in sections["3"]:
        match = re.match(r"^`1 << (\d+)` = (\S+)", line)
        if match:
            capabilities[str(1 << int(match.group(1)))] = match.group(2)
    require(capabilities == registry["capabilities"], "REGISTRY.md capability bits differ from registry.json")
    for line in sections["4"]:
        match = re.match(r"^`0x([0-9a-f]+)` = (\S+)", line)
        if match:
            flags[str(int(match.group(1), 16))] = match.group(2)
    require(flags == registry["frame_flags"], f"REGISTRY.md frame flags {flags} differ from registry.json")
    errors = {}
    for line in sections["6"]:
        match = re.match(r"^\| `(\d+)` \| `(\w+)` \|", line)
        if match:
            errors[match.group(1)] = match.group(2)
    require(errors == registry["errors"], "REGISTRY.md error codes differ from registry.json")
    ranges = {}
    for line in sections["8"]:
        match = re.match(r"^(.+?) `(>= )?(\d+)(?:-(\d+))?` (?:is|are) (.+)$", line)
        require(match is not None, f"REGISTRY.md reserved-range bullet not understood: {line}")
        prefix, open_ended, start, end, phrase = match.groups()
        namespace = next((ns for text, ns in RANGE_NAMESPACES if prefix.removesuffix("s") == text), None)
        kind = next((kind for text, kind in RANGE_KIND_PHRASES if phrase.startswith(text)), None)
        require(namespace and kind, f"REGISTRY.md reserved-range bullet not understood: {line}")
        start = int(start)
        stop = None if open_ended else int(end) if end else start
        ranges.setdefault(namespace, set()).add((start, stop, kind))
    json_ranges = {
        namespace: {(item["from"], item["to"], item["kind"]) for item in items}
        for namespace, items in registry["reserved_ranges"].items()
    }
    require(ranges == json_ranges, "REGISTRY.md Section 8 reserved ranges differ from registry.json reserved_ranges")


def validate_registry_yaml(registry):
    path = ASSETS / "registry.yaml"
    require(path.exists(), "registry.yaml missing")
    expected = "\n".join(to_yaml_lines(registry)) + "\n"
    actual = path.read_text(encoding="utf-8")
    require(actual == expected, "registry.yaml is out of sync with registry.json")


def validate_wire_corpus(wire, registry):
    require(wire.get("schema") == "zmux-wire-corpus-v1", "invalid wire corpus schema")
    frame_names = set(registry["frame_types"].values()) - {"reserved"}
    ext_names = set(registry["ext_subtypes"].values())
    errors = set(registry["errors"].values())

    for case in wire["cases"]:
        if "hex" in case:
            require(is_hex_string(case["hex"]), f"invalid hex in wire case {case['id']}")
        if "expect" in case and "frame_type" in case["expect"]:
            require(case["expect"]["frame_type"] in frame_names, f"unknown frame type in wire case {case['id']}")
        decoded = case.get("expect", {}).get("decoded", {})
        if "ext_type" in decoded:
            require(decoded["ext_type"] in ext_names, f"unknown EXT subtype in wire case {case['id']}")
        if "expect_error" in case:
            require(case["expect_error"] in errors, f"unknown error in wire case {case['id']}")
        check_wire_case_against_reference(case, registry)


def check_wire_case_against_reference(case, registry):
    """Cross-check wire fixtures against the reference codec rules and their expectations."""
    if "hex" not in case:
        return
    cid = case["id"]
    raw = bytes.fromhex(case["hex"])
    kind = case["kind"]
    if kind == "preface_valid":
        try:
            preface = reference_preface_check(raw, registry)
        except PrefaceRejected as exc:
            raise ValueError(f"valid wire case {cid} rejected by reference check: {exc}")
        check_preface_expect(case, preface, registry)
    elif kind == "frame_valid":
        try:
            frame = decode_frame(raw, registry, case.get("receiver_limits"))
        except FrameRejected as exc:
            raise ValueError(f"valid wire case {cid} rejected by reference check: {exc}")
        require(
            len(raw) == frame_extent(raw),
            f"valid wire case {cid} must be exactly one frame ({len(raw)} bytes, frame needs {frame_extent(raw)})",
        )
        check_frame_expect(case, frame)
    elif kind == "frame_invalid":
        check_invalid_frame_extent(cid, raw)
        try:
            reference_frame_check(raw, registry, case.get("receiver_limits"))
        except FrameRejected as exc:
            require(
                exc.code == case["expect_error"],
                f"wire case {cid} expects {case['expect_error']} but the SPEC error-code rules give {exc}",
            )
        else:
            raise ValueError(f"invalid wire case {cid} passes the reference check")
    elif kind == "bytes_invalid":
        try:
            parse_varint(raw, 0)
        except VarintError:
            require(
                case["expect_error"] == "PROTOCOL",
                f"standalone varint62 case {cid} must expect PROTOCOL (SPEC 3.2)",
            )
        else:
            raise ValueError(f"invalid varint case {cid} decodes successfully")
    else:
        raise ValueError(f"wire case {cid} has unknown kind {kind}")


def check_invalid_frame_extent(cid: str, raw: bytes):
    """An invalid frame vector carries one frame and nothing after it."""
    try:
        extent = frame_extent(raw)
    except VarintError:
        return
    require(
        len(raw) <= extent,
        f"invalid frame case {cid} carries {len(raw) - extent} byte(s) beyond its frame; fix frame_length or the hex",
    )


PREFACE_EXPECT_FIELDS = ("preface_ver", "role", "tie_breaker_nonce", "min_proto", "max_proto", "capabilities", "settings_len")


def check_preface_expect(case, preface, registry):
    expect = case.get("expect", {})
    cid = case["id"]
    unknown = set(expect) - set(PREFACE_EXPECT_FIELDS) - {"settings"}
    require(not unknown, f"wire case {cid} has unknown preface expectations {sorted(unknown)}")
    for field in PREFACE_EXPECT_FIELDS:
        if field in expect:
            require(
                expect[field] == preface[field],
                f"wire case {cid} expects {field} = {expect[field]} but the bytes carry {preface[field]}",
            )
    if "settings" in expect:
        non_default = {k: v for k, v in preface["settings"].items() if v != registry["defaults"].get(k)}
        require(
            non_default == expect["settings"],
            f"wire case {cid} expects settings {expect['settings']} but the bytes carry {non_default}",
        )


FRAME_EXPECT_FIELDS = {"frame_length", "frame_type", "flags", "stream_id", "payload_hex", "decoded"}
DECODED_EXPECT_FIELDS = {
    "max_offset",
    "blocked_at",
    "error_code",
    "debug_text",
    "diag_block_dropped",
    "goaway",
    "ext_type",
    "ext_type_value",
    "stream_metadata_tlvs",
    "application_payload_hex",
    "open_metadata_block_dropped",
    "ping_padding_tag",
}


def check_frame_expect(case, frame):
    expect = case.get("expect", {})
    cid = case["id"]
    unknown = set(expect) - FRAME_EXPECT_FIELDS
    require(not unknown, f"wire case {cid} has unknown frame expectations {sorted(unknown)}")
    for field in ("frame_length", "frame_type", "flags", "stream_id"):
        if field in expect:
            require(
                expect[field] == frame[field],
                f"wire case {cid} expects {field} = {expect[field]} but the bytes carry {frame[field]}",
            )
    if "payload_hex" in expect:
        require(expect["payload_hex"].lower() == frame["payload_hex"], f"wire case {cid} payload_hex mismatch")
    decoded_expect = expect.get("decoded", {})
    unknown = set(decoded_expect) - DECODED_EXPECT_FIELDS
    require(not unknown, f"wire case {cid} has unknown decoded expectations {sorted(unknown)}")
    decoded = frame["decoded"]
    for field, want in decoded_expect.items():
        if field == "stream_metadata_tlvs":
            check_metadata_tlv_expect(cid, decoded.get(field), want)
        elif field == "ping_padding_tag":
            check_ping_padding_tag_expect(cid, frame, want)
        else:
            got = decoded.get(field)
            require(got == want, f"wire case {cid} expects decoded {field} = {want!r} but the bytes give {got!r}")


def check_metadata_tlv_expect(cid, actual, expected):
    require(actual is not None, f"wire case {cid} expects metadata TLVs but the frame carries none")
    require(len(actual) == len(expected), f"wire case {cid} metadata TLV count {len(actual)} != {len(expected)}")
    for index, (got, want) in enumerate(zip(actual, expected)):
        require(got["type"] == want["type"], f"wire case {cid} metadata TLV {index} type {got['type']} != {want['type']}")
        if "value" in want:
            try:
                value, used = parse_varint(got["raw"], 0)
            except VarintError as exc:
                raise ValueError(f"wire case {cid} metadata TLV {index} value {exc}")
            require(used == len(got["raw"]) and value == want["value"], f"wire case {cid} metadata TLV {index} value")
        if "value_hex" in want:
            require(got["raw"].hex() == want["value_hex"].lower(), f"wire case {cid} metadata TLV {index} value_hex")


def check_ping_padding_tag_expect(cid, frame, want):
    payload = frame["payload"]
    token = bytes.fromhex(want["token_hex"])
    tag = int(want["tag_hex"], 16)
    require(frame["frame_type"] == "PING", f"wire case {cid} ping_padding_tag needs a PING")
    require(payload[:8] == token, f"wire case {cid} token_hex is not the PING token")
    require(payload[8:16].hex() == want["tag_hex"].lower(), f"wire case {cid} tag_hex is not PING bytes 8..15")
    require(
        ping_padding_tag(want["ping_padding_key"], int.from_bytes(token, "big")) == tag,
        f"wire case {cid} tag_hex is not ping_padding_tag(key, token) (SPEC 6.4)",
    )


HALF_STATES = {
    "send_half": {"absent", "send_open", "send_stop_seen", "send_fin", "send_reset", "send_aborted"},
    "recv_half": {"absent", "recv_open", "recv_fin", "recv_stop_sent", "recv_reset", "recv_aborted"},
}

# Halves that do not exist for a stream kind (STATE_MACHINE 2).
ABSENT_HALF = {"uni_local_send_only": "recv_half", "uni_local_receive_only": "send_half"}


def check_half_states(cid, stream_kind, states):
    require(isinstance(states, dict), f"state case {cid} half-state must be an object")
    for half, value in states.items():
        require(value in HALF_STATES.get(half, ()), f"state case {cid} has unknown {half} {value!r}")
    absent = ABSENT_HALF.get(stream_kind)
    if absent and absent in states:
        require(states[absent] == "absent", f"state case {cid}: {stream_kind} has no {absent}")


def validate_state_corpus(state):
    require(state.get("schema") == "zmux-state-corpus-v1", "invalid state corpus schema")
    allowed_keys = {"expect_state", "expect_result"}
    for case in state["cases"]:
        require("id" in case and "steps" in case, "state case missing id or steps")
        cid = case["id"]
        stream_kind = case.get("stream_kind")
        initial = case.get("initial_state")
        # Portable cases name all of their preconditions (examples/fixture_mapping.md).
        if stream_kind or "ownership" in case or initial is not None:
            require(
                stream_kind and case.get("ownership") and initial is not None,
                f"state case {cid} must give stream_kind, ownership, and initial_state together",
            )
            if initial != "idle":
                check_half_states(cid, stream_kind, initial)
        for step in case["steps"]:
            require("event" in step, f"state step missing event in case {cid}")
            require(any(k in step for k in allowed_keys), f"state step missing expectation in case {cid}")
            if "expect_state" in step:
                check_half_states(cid, stream_kind, step["expect_state"])
            result = step.get("expect_result", "")
            # A violation on a never-opened stream ID cannot be answered stream-locally
            # (SPEC 9.1/9.6), so it is always a session PROTOCOL error.
            if initial == "idle" and result.endswith("_violation"):
                require(
                    result == "protocol_violation",
                    f"idle-stream violation in state case {cid} must be protocol_violation",
                )
            # A stream-local ABORT answers a frame on an already opened stream only (SPEC 9.6).
            if result.startswith("abort_"):
                require(
                    isinstance(initial, dict),
                    f"state case {cid} expects {result} but starts before the stream exists",
                )


# State cases that name their preconditions but depend on window accounting the
# reference harness seeds out of band (pending credit, buffered unread data, a
# small frame-size limit, a pacing threshold), so they must not be portable.
SEEDED_STATE_CASES = {
    "stream_flow_control_noop_controls_do_not_force_flush",
    "stream_blocked_force_flushes_pending_credit_below_pacing_threshold",
    "stream_blocked_after_read_stop_force_flushes_session_only",
    "read_stop_discard_restores_session_budget_but_not_stream_budget",
}


def validate_portable_state_cases(state):
    """Portable cases are runnable from fixture data alone (examples/fixture_mapping.md 2)."""
    portable = [case for case in state["cases"] if is_portable_state_case(case)]
    require(portable, "state corpus has no portable cases")
    ids = {case["id"] for case in state["cases"]}
    for cid in sorted(SEEDED_STATE_CASES):
        require(cid in ids, f"seeded state case {cid} missing")
    for case in portable:
        require(
            case["id"] not in SEEDED_STATE_CASES,
            f"state case {case['id']} depends on seeded window state and cannot be portable",
        )
    # Equal preconditions and equal event prefixes must never expect different outcomes.
    seen = {}
    for case in portable:
        events = ()
        for step in case["steps"]:
            events += (step["event"],)
            key = (case["stream_kind"], case["ownership"], json.dumps(case["initial_state"], sort_keys=True), events)
            first_id, expected = seen.setdefault(key, (case["id"], {}))
            for field in ("expect_result", "expect_state"):
                if field not in step:
                    continue
                require(
                    expected.setdefault(field, step[field]) == step[field],
                    f"portable state cases {first_id} and {case['id']} expect different {field} "
                    f"after {list(events)} from the same preconditions",
                )


def portable_vocabulary_tables():
    """Return the first-column tokens of the Event and Result tables in fixture_mapping.md 2.1."""
    tables, current = {}, None
    path = ROOT / "examples" / "fixture_mapping.md"
    for line in path.read_text(encoding="utf-8").split("\n"):
        header = re.fullmatch(r"\| (Event|Result) \| Meaning \|", line)
        if header:
            current = tables.setdefault(header.group(1), [])
        elif current is not None and line.startswith("|"):
            token = re.fullmatch(r"`([A-Za-z0-9_]+)`", table_cells(line)[0].strip())
            if token:
                current.append(token.group(1))
        else:
            current = None
    return tables


def validate_portable_vocabulary_doc():
    tables = portable_vocabulary_tables()
    for name, want in (("Event", PORTABLE_EVENTS), ("Result", PORTABLE_RESULTS)):
        tokens = tables.get(name, [])
        require(len(tokens) == len(set(tokens)), f"fixture_mapping.md portable {name} table repeats a token")
        require(
            set(tokens) == want,
            f"fixture_mapping.md portable {name} table differs from tools/build_case_sets.py: "
            f"missing {sorted(want - set(tokens))}, extra {sorted(set(tokens) - want)}",
        )


def validate_invalid_corpus(invalid, registry):
    require(invalid.get("schema") == "zmux-invalid-corpus-v1", "invalid invalid-corpus schema")
    errors = set(registry["errors"].values())
    for case in invalid["cases"]:
        require("id" in case and "expected_result" in case, "invalid case missing id or expected_result")
        result = case["expected_result"]
        if "error" in result:
            require(result["error"] in errors, f"unknown error in invalid case {case['id']}")
        if "hex" in case:
            require(is_hex_string(case["hex"]), f"invalid hex in invalid case {case['id']}")
        check_invalid_case_error_code(case, registry)
    wrong_side = {
        case.get("input_shape", {}).get("incoming_frame")
        for case in invalid["cases"]
        if case["id"].endswith("_wrong_side_uni")
    }
    require(
        wrong_side == WRONG_SIDE_UNI_FRAMES,
        f"wrong-side unidirectional invalid cases cover {sorted(wrong_side)}, want {sorted(WRONG_SIDE_UNI_FRAMES)}",
    )


# Codes fixed by SPEC 3.2 / 4.3 / 6.11 / 7.2 for invalid-corpus cases described only by shape.
FRAME_SHAPE_ERROR_CODES = {
    "frame_length_too_small": "FRAME_SIZE",
    "frame_length_smaller_than_stream_id_prefix": "FRAME_SIZE",
    "frame_pong_too_short": "FRAME_SIZE",
    "frame_priority_update_truncated_tlv_header": "FRAME_SIZE",
    "frame_priority_update_tlv_value_overrun": "FRAME_SIZE",
    "frame_ext_payload_underflow": "PROTOCOL",
    "frame_max_data_trailing_garbage": "PROTOCOL",
    "frame_blocked_trailing_garbage": "PROTOCOL",
}

# Non-opening core frames whose first appearance on an unused stream ID is a session error.
UNUSED_STREAM_FIRST_FRAMES = {"MAX_DATA", "BLOCKED", "STOP_SENDING", "RESET"}

# Establishment codes fixed by SPEC 2.4 for preface cases that need both prefaces.
PREFACE_SHAPE_ERROR_CODES = {
    "preface_auto_equal_nonce_conflict": "ROLE_CONFLICT",
    "preface_explicit_same_role_conflict": "ROLE_CONFLICT",
    "preface_no_protocol_version_overlap": "UNSUPPORTED_VERSION",
}

# Every frame a wrong-side peer can send on an opened unidirectional stream (SPEC 9.6).
WRONG_SIDE_UNI_FRAMES = {"DATA", "BLOCKED", "RESET", "MAX_DATA", "STOP_SENDING"}


def check_invalid_case_error_code(case, registry):
    cid = case["id"]
    result = case["expected_result"]
    shape = case.get("input_shape", {})
    if cid in FRAME_SHAPE_ERROR_CODES:
        require(
            result == {"scope": "session", "error": FRAME_SHAPE_ERROR_CODES[cid]},
            f"invalid case {cid} must expect session {FRAME_SHAPE_ERROR_CODES[cid]}",
        )
    capabilities = shape.get("capabilities")
    if capabilities is not None:
        require(
            isinstance(capabilities, list) and set(capabilities) <= set(registry["capabilities"].values()),
            f"invalid case {cid} names an unknown capability",
        )
    # SPEC 7.6 ignores an unnegotiated PRIORITY_UPDATE before parsing its payload, so a
    # payload-level expectation must say the capability was negotiated.
    if shape.get("ext_type") == "PRIORITY_UPDATE" and ("payload_shape" in shape or "stream_metadata_tlvs" in shape):
        require(
            "priority_update" in (capabilities or []),
            f"invalid case {cid} judges a PRIORITY_UPDATE payload, so it must negotiate priority_update",
        )
    if shape.get("initial_stream_state") == "idle":
        require(
            "error" not in result or result.get("scope") == "session",
            f"invalid case {cid} on an unused stream ID cannot have a stream-scoped error (SPEC 9.6)",
        )
        if shape.get("incoming_frame") in UNUSED_STREAM_FIRST_FRAMES:
            require(
                result == {"scope": "session", "error": "PROTOCOL"},
                f"invalid case {cid} must expect session PROTOCOL (SPEC 9.1)",
            )
    if cid in PREFACE_SHAPE_ERROR_CODES:
        require(
            result == {"scope": "session_establishment", "error": PREFACE_SHAPE_ERROR_CODES[cid]},
            f"invalid case {cid} must expect session_establishment {PREFACE_SHAPE_ERROR_CODES[cid]}",
        )
    if cid.endswith("_wrong_side_uni"):
        require(
            result == {"scope": "stream", "error": "STREAM_STATE"},
            f"invalid case {cid} on an opened unidirectional stream must expect stream STREAM_STATE (SPEC 9.6)",
        )
    if case.get("layer") == "preface" and "error" in result:
        require(
            result.get("scope") == "session_establishment",
            f"invalid preface case {cid} must use scope session_establishment",
        )
    if case.get("layer") == "preface" and "hex" in case:
        try:
            reference_preface_check(bytes.fromhex(case["hex"]), registry)
        except PrefaceRejected as exc:
            require(
                exc.code == result.get("error"),
                f"invalid case {cid} expects {result.get('error')} but the SPEC establishment codes give {exc}",
            )
        else:
            raise ValueError(f"invalid case {cid} passes the reference preface check")
    if case.get("layer") == "frame" and "hex" in case and "error" in result:
        check_invalid_frame_extent(cid, bytes.fromhex(case["hex"]))
        try:
            reference_frame_check(bytes.fromhex(case["hex"]), registry)
        except FrameRejected as exc:
            require(
                exc.code == result["error"],
                f"invalid case {cid} expects {result['error']} but the SPEC error-code rules give {exc}",
            )
        else:
            raise ValueError(f"invalid case {cid} passes the reference check")


def validate_golden_cases(golden, registry, wire, state, invalid):
    require(golden.get("schema") == "zmux-golden-cases-v1", "invalid golden-cases schema")
    require(isinstance(golden.get("cases"), list) and golden["cases"], "golden cases missing")

    expected_ids = set()
    expected_ids.update(case["id"] for case in wire["cases"])
    expected_ids.update(case["id"] for case in state["cases"])
    expected_ids.update(case["id"] for case in invalid["cases"])

    seen = set()
    for case in golden["cases"]:
        cid = case["id"]
        require(cid not in seen, f"duplicate golden case id: {cid}")
        seen.add(cid)
        require(cid in expected_ids, f"golden case not backed by source corpus: {cid}")

    require(seen == expected_ids, "golden case set does not match source corpora")
    snapshot = golden["protocol_snapshot"]
    require(snapshot["preface_ver"] == registry["protocol"]["preface_ver"], "golden preface_ver mismatch")
    require(snapshot["proto_ver"] == registry["protocol"]["proto_ver"], "golden proto_ver mismatch")
    require(snapshot["integer_encoding"] == registry["protocol"]["integer_encoding"], "golden integer encoding mismatch")
    require(snapshot["integer_max"] == registry["protocol"]["integer_max"], "golden integer max mismatch")


def validate_fixture_bundle():
    index_path = FIXTURES / "index.json"
    require(index_path.exists(), "fixture bundle index missing")
    index = load_json(index_path)
    require(index.get("schema") == "zmux-fixture-bundle-v1", "invalid fixture bundle schema")
    require(index.get("generated_from") == "assets/golden_cases.json", "unexpected fixture bundle source")
    require(isinstance(index.get("files"), list) and index["files"], "fixture bundle files missing")

    total = 0
    for item in index["files"]:
        path = ROOT / item["path"]
        require(path.exists(), f"fixture bundle file missing: {item['path']}")
        count = 0
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                json.loads(line)
                count += 1
        require(count == item["count"], f"fixture count mismatch for {item['path']}")
        total += count

    golden = load_json(ASSETS / "golden_cases.json")
    require(total == len(golden["cases"]), "fixture bundle total count mismatch")


def validate_case_sets():
    path = FIXTURES / "case_sets.json"
    require(path.exists(), "fixture case_sets missing")
    case_sets = load_json(path)
    require(case_sets.get("schema") == "zmux-case-sets-v1", "invalid case_sets schema")
    require(isinstance(case_sets.get("sets"), dict) and case_sets["sets"], "case_sets missing groups")

    golden = load_json(ASSETS / "golden_cases.json")
    known_ids = {case["id"] for case in golden["cases"]}
    covered = set()
    for name, ids in case_sets["sets"].items():
        require(isinstance(ids, list), f"case set {name} is not a list")
        require(len(ids) == len(set(ids)), f"case set {name} repeats a case id")
        for cid in ids:
            require(cid in known_ids, f"case set {name} references unknown case id: {cid}")
        covered.update(ids)
    # A CI split built only from case sets must still run every case.
    require(not known_ids - covered, f"golden case ids in no case set: {sorted(known_ids - covered)}")
    # portable_state is exactly the state cases runnable from fixture data alone.
    want = {
        case["id"] for case in golden["cases"] if case["source"] == "state_corpus" and is_portable_state_case(case)
    }
    require(set(case_sets["sets"].get("portable_state", [])) == want, "case set portable_state must equal the portable state cases")
    # The codec sets are exactly the byte-level wire fixtures.
    wire_cases = [case for case in golden["cases"] if case["source"] == "wire_corpus"]
    for name, valid in (("codec_valid", True), ("codec_invalid", False)):
        want = {case["id"] for case in wire_cases if case["category"].endswith("_valid") == valid}
        require(set(case_sets["sets"].get(name, [])) == want, f"case set {name} must equal the wire fixtures")


def hex_bytes(text: str):
    """Bytes of a string made only of space-separated hex pairs, else None."""
    text = text.strip()
    if not re.fullmatch(r"(?:[0-9a-f]{2})(?: [0-9a-f]{2})*", text):
        return None
    return bytes.fromhex(text)


def last_integer(text: str) -> int:
    numbers = re.findall(r"\b(?:0x[0-9a-f]+|\d+)\b", text)
    require(numbers, f"no integer in {text!r}")
    return int(numbers[-1], 0)


def documented_bytes(text: str):
    """Bytes after the last '=' of a field value such as 'varint62(8) = 08'."""
    raw = hex_bytes(text.rsplit("=", 1)[-1])
    for value, encoded in re.findall(r"varint62\((\d+)\) = ((?:[0-9a-f]{2} ?)+)", text):
        require(
            encode_varint(int(value)) == bytes.fromhex(encoded),
            f"varint62({value}) is {encode_varint(int(value)).hex(' ')}, not {encoded.strip()}",
        )
    return raw


def documented_tlvs(text: str):
    """Encoded bytes of each 'TLV(type=..=N, len=L, value=..)' description in text."""
    tlvs = []
    for match in re.finditer(r"TLV\(type=", text):
        depth, index = 1, match.end()
        while depth:
            depth += {"(": 1, ")": -1}.get(text[index], 0)
            index += 1
        type_part, len_part, value_part = text[match.end() : index - 1].split(", ", 2)
        require(value_part.startswith("value="), f"TLV description without value: {text!r}")
        value_text = value_part[len("value=") :]
        value = b"" if value_text == "(empty)" else documented_bytes(value_text)
        require(value is not None, f"TLV value is not hex: {value_part!r}")
        length = int(len_part.split("=", 1)[1])
        require(len(value) == length, f"TLV len={length} but its value has {len(value)} bytes: {text!r}")
        tlvs.append(encode_varint(last_integer(type_part)) + encode_varint(length) + value)
    return tlvs


def wire_example_sections():
    """(section, lines) for every '###' subsection of WIRE_EXAMPLES.md."""
    sections, current = [], None
    for line in (ROOT / "WIRE_EXAMPLES.md").read_text(encoding="utf-8").split("\n"):
        heading = re.match(r"^### (\d+\.\d+[a-z]?) ", line)
        if heading:
            current = (heading.group(1), [])
            sections.append(current)
        elif line.startswith("## "):
            current = None
        elif current is not None:
            current[1].append(line)
    return sections


def example_blocks(lines):
    blocks, block = [], None
    for line in lines:
        if line.startswith("```"):
            if block is None:
                block = []
            else:
                blocks.append(block)
                block = None
        elif block is not None:
            block.append(line)
    return blocks


def example_fields(lines):
    """Backticked 'name = value' bullets of a 'Fields:' list, with continuation lines."""
    fields, active = [], False
    for line in lines:
        if line.strip() == "Fields:":
            active = True
            continue
        if not active:
            continue
        if line.startswith("- "):
            fields.append(line[2:])
        elif line.startswith("  ") and fields:
            fields[-1] += " " + line.strip()
        elif line.strip():
            break
    parsed = []
    for bullet in fields:
        match = re.match(r"^`([a-z_]+) = (.+?)`", bullet)
        if match:
            parsed.append((match.group(1), match.group(2), bullet))
    return parsed


PREFACE_FIELD_OFFSETS = {"preface_ver": 4, "role": 5}


def check_example_fields(section, fields, raw, registry):
    where = f"WIRE_EXAMPLES {section}"
    if raw[:4] == b"ZMUX":
        preface = reference_preface_check(raw, registry)
        for name, value, bullet in fields:
            if name == "magic":
                require(value.strip('"').encode() == raw[:4], f"{where} magic")
            elif name in PREFACE_FIELD_OFFSETS:
                require(last_integer(value) == raw[PREFACE_FIELD_OFFSETS[name]], f"{where} {name} = {value}")
            elif name in PREFACE_EXPECT_FIELDS:
                require(last_integer(value) == preface[name], f"{where} {name} = {value} but bytes give {preface[name]}")
            elif name == "settings_tlv":
                for tlv in documented_tlvs(bullet):
                    require(tlv in preface["settings_raw"], f"{where} settings_tlv {tlv.hex(' ')} not in the bytes")
            else:
                raise ValueError(f"{where} has an unchecked preface field {name}")
        return
    frame = decode_frame(raw, registry)
    payload = frame["payload"]
    decoded = frame["decoded"]
    for name, value, bullet in fields:
        if name in ("frame_length", "stream_id"):
            require(last_integer(value) == frame[name], f"{where} {name} = {value} but bytes give {frame[name]}")
        elif name == "code":
            require(last_integer(value) == frame["code"], f"{where} code = {value} but bytes give {frame['code']:#04x}")
        elif name == "payload":
            expected = documented_bytes(value)
            require(expected is not None and payload.startswith(expected), f"{where} payload = {value}")
        elif name == "metadata_len":
            require(last_integer(value) == decoded.get("metadata_len"), f"{where} metadata_len = {value}")
        elif name == "application_payload":
            require(documented_bytes(value).hex() == decoded.get("application_payload_hex"), f"{where} {name}")
        elif name == "ext_type":
            require(last_integer(value) == decoded.get("ext_type_value"), f"{where} ext_type = {value}")
        elif name in ("last_accepted_bidi_stream_id", "last_accepted_uni_stream_id"):
            require(last_integer(value) == decoded["goaway"][name], f"{where} {name} = {value}")
        elif name == "error_code":
            got = decoded["goaway"]["error_code"] if "goaway" in decoded else decoded.get("error_code")
            require(last_integer(value) == got, f"{where} error_code = {value}")
        elif name == "token":
            require(documented_bytes(value) == payload[:8], f"{where} token = {value}")
        elif name == "ping_padding_tag":
            tag = documented_bytes(value)
            require(tag == payload[8:16], f"{where} ping_padding_tag = {value}")
            key = re.search(r"ping_padding_key = (0x[0-9a-f]+)", bullet)
            require(key is not None, f"{where} ping_padding_tag needs its ping_padding_key")
            require(
                ping_padding_tag(int(key.group(1), 16), int.from_bytes(payload[:8], "big")) == int.from_bytes(tag, "big"),
                f"{where} ping_padding_tag is not the SPEC 6.4 tag for that key and token",
            )
        elif name in ("metadata_tlvs", "diag_tlvs", "ext_payload"):
            tlvs = documented_tlvs(bullet)
            for tlv in tlvs:
                require(tlv in payload, f"{where} {name} {tlv.hex(' ')} not in the payload")
            if not tlvs and documented_bytes(value) is not None:
                require(payload.endswith(documented_bytes(value)), f"{where} {name} = {value}")
        else:
            raise ValueError(f"{where} has an unchecked frame field {name}")


def check_known_answer_tags(section, lines):
    for key, token, tag in re.findall(
        r"`ping_padding_tag\((0x[0-9a-f]+), (0x[0-9a-f]+)\) = ((?:[0-9a-f]{2} ?){8})`", "\n".join(lines)
    ):
        require(
            ping_padding_tag(int(key, 16), int(token, 16)) == int(tag.replace(" ", ""), 16),
            f"WIRE_EXAMPLES {section} ping_padding_tag({key}, {token}) is not {tag}",
        )


# Bare stream_id encodings shown in WIRE_EXAMPLES 2.1: canonical 4 and the non-canonical 40 04.
WIRE_EXAMPLE_VARINT_FRAGMENTS = {"04": True, "4004": False}

# Section 2 examples whose bytes are structurally valid and invalid only in context, with
# the invalid-corpus case that carries their session error.
WIRE_EXAMPLE_CONTEXT_CASES = {
    "2.4": "frame_first_reset_on_unused_stream",
    "2.5a": "preface_auto_equal_nonce_conflict",
    "2.6a": "frame_data_open_metadata_on_open_stream",
}

# How WIRE_EXAMPLES Section 2 prose states the session error of an example.
STATED_SESSION_ERROR = re.compile(r"session `(\w+)` error|`(\w+)` session error")

FRAME_NAMES_IN_TRACES = re.compile(r"\b([A-Z_]+(?:\|[A-Z_]+)*)\(([^)]*)\)")


def check_trace_line(section, hex_text, note, registry):
    """One annotated frame of a Section 3 trace agrees with its annotation."""
    where = f"WIRE_EXAMPLES {section} {hex_text!r}"
    raw = hex_bytes(hex_text)
    require(raw is not None, f"{where} is not hex")
    try:
        frame = decode_frame(raw, registry)
    except FrameRejected as exc:
        raise ValueError(f"{where} is not a valid frame: {exc}")
    require(len(raw) == frame_extent(raw), f"{where} is not exactly one frame")
    match = FRAME_NAMES_IN_TRACES.search(note)
    require(match is not None, f"{where} annotation names no frame")
    name, args = match.groups()
    parts = name.split("|")
    require(parts[0] == frame["frame_type"], f"{where} is {frame['frame_type']}, annotated {name}")
    require(sorted(parts[1:]) == sorted(frame["flags"]), f"{where} flags {frame['flags']} annotated {name}")
    stream = re.search(r"stream[= ](\d+)", note)
    if stream:
        require(int(stream.group(1)) == frame["stream_id"], f"{where} stream_id is {frame['stream_id']}")
    if note.startswith("session ") or " session " in note:
        require(frame["stream_id"] == 0, f"{where} is annotated session-scoped")
    decoded = frame["decoded"]
    errors = {name: int(code) for code, name in registry["errors"].items()}
    for key, value in re.findall(r"(\w+)=(\"[^\"]*\"|\w+)", args):
        if key == "stream":
            continue
        if key == "code":
            require(errors[value] == decoded.get("error_code"), f"{where} code is {decoded.get('error_code')}, not {value}")
        elif key in ("max_offset", "blocked_at"):
            require(int(value) == decoded.get(key), f"{where} {key} is {decoded.get(key)}")
        elif key in ("last_accepted_bidi", "last_accepted_uni"):
            require(int(value) == decoded["goaway"][key + "_stream_id"], f"{where} {key}")
        elif key == "open_info":
            tlvs = {tlv["type"]: tlv["raw"] for tlv in decoded.get("stream_metadata_tlvs", [])}
            require(tlvs.get("open_info") == value.strip('"').encode(), f"{where} open_info")
        else:
            raise ValueError(f"{where} has an unchecked annotation {key}")
    for word in re.findall(r"\b[A-Z_]{4,}\b", args):
        if word in errors and "code=" + word not in args:
            require(decoded.get("goaway", {}).get("error_code") == errors[word], f"{where} error code is not {word}")
    quoted = re.findall(r'(?<![=\w])"([^"]*)"', args)
    if quoted:
        require(decoded.get("application_payload_hex") == quoted[0].encode().hex(), f"{where} payload is not {quoted[0]!r}")
    if "empty payload" in args:
        require(decoded.get("application_payload_hex") == "", f"{where} payload is not empty")


def validate_wire_examples(wire, invalid, registry):
    """Every hex example in WIRE_EXAMPLES.md decodes as documented and is in the corpus."""
    corpus = {}
    for case in wire["cases"] + invalid["cases"]:
        if "hex" in case:
            corpus.setdefault(case["hex"].lower(), []).append(case)
    invalid_by_id = {case["id"]: case for case in invalid["cases"]}
    for section, lines in wire_example_sections():
        part = int(section.split(".")[0])
        blocks = example_blocks(lines)
        check_known_answer_tags(section, lines)
        text = "\n".join(lines)
        corpus_codes = set()
        if section in WIRE_EXAMPLE_CONTEXT_CASES:
            context = invalid_by_id.get(WIRE_EXAMPLE_CONTEXT_CASES[section])
            require(context is not None, f"WIRE_EXAMPLES {section} context case is missing from the invalid corpus")
            corpus_codes.add(fixture_error_code(context))
        for block in blocks:
            for line in block:
                hex_text, _, note = line.partition(";")
                hex_text, note = hex_text.strip(), note.strip()
                if not hex_text:
                    continue
                if part == 3 and note:
                    check_trace_line(section, hex_text, note, registry)
                    continue
                raw = hex_bytes(hex_text)
                require(raw is not None, f"WIRE_EXAMPLES {section} block line {line!r} is not hex")
                if raw.hex() in WIRE_EXAMPLE_VARINT_FRAGMENTS:
                    try:
                        parse_varint(raw, 0)
                        canonical = True
                    except VarintError:
                        canonical = False
                    require(canonical == WIRE_EXAMPLE_VARINT_FRAGMENTS[raw.hex()], f"WIRE_EXAMPLES {section} {hex_text}")
                    continue
                cases = corpus.get(raw.hex())
                require(cases, f"WIRE_EXAMPLES {section} bytes {hex_text} match no wire or invalid corpus case")
                if part == 1:
                    require(
                        any(case.get("kind", "").endswith("_valid") for case in cases),
                        f"WIRE_EXAMPLES {section} is a valid example but its corpus case is not *_valid",
                    )
                if part == 2:
                    corpus_codes.update(filter(None, map(fixture_error_code, cases)))
        if part == 2:
            # The prose must state exactly the corpus codes, so naming the default code
            # beside an exception (2.6d) cannot hide a swapped code.
            stated = {a or b for a, b in STATED_SESSION_ERROR.findall(re.sub(r"\s+", " ", text))}
            require(corpus_codes, f"WIRE_EXAMPLES {section} has no corpus case with a session error")
            require(
                stated == corpus_codes,
                f"WIRE_EXAMPLES {section} states session errors {sorted(stated)} but its corpus cases give {sorted(corpus_codes)}",
            )
        fields = example_fields(lines)
        if fields:
            require(len(blocks) == 1 and len(blocks[0]) == 1, f"WIRE_EXAMPLES {section} Fields need one hex line")
            check_example_fields(section, fields, hex_bytes(blocks[0][0].partition(";")[0]), registry)


# SPEC 4.3 error-code table rows and the fixture cases that pin each row's code.
SPEC_FRAME_CODE_TABLE_CASES = {
    "`frame_length < 2`": ("invalid_frame_length_too_small", "frame_length_too_small"),
    "`frame_length < 1 + encoded_length(stream_id)`": (
        "invalid_frame_length_smaller_than_stream_id",
        "frame_length_smaller_than_stream_id_prefix",
    ),
    "derived payload length above the receiver's limit for that frame class": (
        "invalid_oversized_frame_for_receiver_limit",
    ),
    "missing, truncated, or non-canonical mandatory payload field": (
        "invalid_noncanonical_mandatory_payload_varint",
        "invalid_truncated_mandatory_payload_varint",
        "invalid_stop_sending_missing_error_code",
    ),
    "container-structural TLV error inside a frame payload": (
        "frame_priority_update_truncated_tlv_header",
        "frame_priority_update_tlv_value_overrun",
    ),
    "invalid `stream_priority` or `stream_group` value in an interpreted `OPEN_METADATA` block or `PRIORITY_UPDATE` payload": (
        "frame_priority_update_noncanonical_value",
    ),
    "trailing bytes after the one `MAX_DATA` / `BLOCKED` value": (
        "invalid_max_data_trailing_byte",
        "frame_max_data_trailing_garbage",
        "frame_blocked_trailing_garbage",
    ),
    "`EXT` payload too short for its `ext_type`, or malformed `ext_type`": (
        "invalid_ext_payload_underflow",
        "frame_ext_payload_underflow",
    ),
    "non-canonical `frame_length` or `stream_id` encoding": ("invalid_noncanonical_varint_stream_id",),
}

# Phrases of the CONFORMANCE 2 expected-code lists and the code whose bullet must hold them.
CONFORMANCE_CODE_PHRASES = {
    "`frame_length = 0` or `frame_length = 1`": "FRAME_SIZE",
    "`frame_length < 1 + encoded_length(stream_id)`": "FRAME_SIZE",
    "frame payloads larger than receiver limits": "FRAME_SIZE",
    "missing, truncated, or non-canonical mandatory payload fields": "FRAME_SIZE",
    "`PING` or `PONG` payloads shorter than `8` bytes": "FRAME_SIZE",
    "container-structural TLV errors": "FRAME_SIZE",
    "`stream_priority` or `stream_group` value": "FRAME_SIZE",
    "non-canonical `frame_length` or `stream_id` encodings": "PROTOCOL",
    "trailing bytes after the one `MAX_DATA` or `BLOCKED` value": "PROTOCOL",
    "an `EXT` payload too short for its `ext_type`": "PROTOCOL",
    "invalid preface fields and setting values": "PROTOCOL",
}


def fixture_error_code(case):
    return case.get("expect_error") or case.get("expected_result", {}).get("error")


def validate_error_code_tables(wire, invalid):
    """SPEC 4.3's code table and CONFORMANCE 2's code lists agree with the pinned fixtures."""
    by_id = {case["id"]: case for case in wire["cases"] + invalid["cases"]}
    lines = (ROOT / "SPEC.md").read_text(encoding="utf-8").split("\n")
    header = "| Condition | Session error |"
    require(header in lines, "SPEC 4.3 error-code table not found")
    rows = {}
    for row in lines[lines.index(header) + 2 :]:
        if not row.startswith("|"):
            break
        condition, code = (cell.strip() for cell in table_cells(row))
        match = re.match(r"`(\w+)`", code)
        require(match is not None, f"SPEC 4.3 row {condition!r} names no error code")
        rows[condition] = match.group(1)
    require(
        set(rows) == set(SPEC_FRAME_CODE_TABLE_CASES),
        f"SPEC 4.3 code table rows differ from the pinned rows: {sorted(set(rows) ^ set(SPEC_FRAME_CODE_TABLE_CASES))}",
    )
    for condition, code in rows.items():
        for cid in SPEC_FRAME_CODE_TABLE_CASES[condition]:
            require(cid in by_id, f"SPEC 4.3 row {condition!r} names missing fixture {cid}")
            require(
                fixture_error_code(by_id[cid]) == code,
                f"SPEC 4.3 row {condition!r} says {code} but fixture {cid} expects {fixture_error_code(by_id[cid])}",
            )

    text = (ROOT / "CONFORMANCE.md").read_text(encoding="utf-8")
    section = text.split("Expected session error codes for the structural cases above", 1)[1]
    section = section.split("\n\n", 2)[1]
    bullets = {}
    for bullet in re.split(r"\n(?=- `)", section):
        match = re.match(r"- `(\w+)`: (.*)", bullet, re.S)
        require(match is not None, f"CONFORMANCE 2 expected-code bullet is malformed: {bullet[:40]!r}")
        bullets[match.group(1)] = re.sub(r"\s+", " ", match.group(2))
    require(set(bullets) == {"FRAME_SIZE", "PROTOCOL"}, f"CONFORMANCE 2 code lists cover {sorted(bullets)}")
    for phrase, code in CONFORMANCE_CODE_PHRASES.items():
        holders = sorted(name for name, body in bullets.items() if phrase in body)
        require(holders == [code], f"CONFORMANCE 2 lists {phrase!r} under {holders}, want [{code!r}]")


def table_cells(row: str):
    return re.split(r"(?<!\\)\|", row.strip().strip("|"))


def validate_markdown_tables():
    r"""Every table starts with header and delimiter rows and keeps its cell count.

    GFM splits cells on any unescaped pipe, even inside a code span, so table cells
    must write `DATA\|FIN`.
    """
    for path in sorted(ROOT.glob("*.md")) + sorted((ROOT / "examples").glob("*.md")):
        lines = path.read_text(encoding="utf-8").split("\n")
        in_fence, index = False, 0
        while index < len(lines):
            line = lines[index]
            if line.startswith("```"):
                in_fence = not in_fence
            if in_fence or not line.startswith("|"):
                index += 1
                continue
            start = index
            while index < len(lines) and lines[index].startswith("|"):
                index += 1
            rows = lines[start:index]
            where = f"{path.name}:{start + 1}"
            require(
                len(rows) >= 2 and re.fullmatch(r"\|(?:\s*:?-+:?\s*\|)+", rows[1].replace(" ", "")),
                f"{where}: table rows without a header and delimiter row",
            )
            width = len(table_cells(rows[0]))
            for offset, row in enumerate(rows):
                for span in re.findall(r"`[^`]*`", row):
                    require(not re.search(r"(?<!\\)\|", span), f"{path.name}:{start + offset + 1}: escape | in {span}")
                require(len(table_cells(row)) == width, f"{path.name}:{start + offset + 1}: row has a different cell count")


def main():
    manifest = load_json(ASSETS / "manifest.json")
    registry = load_json(ASSETS / "registry.json")
    wire = load_json(ASSETS / "wire_corpus.json")
    state = load_json(ASSETS / "state_corpus.json")
    invalid = load_json(ASSETS / "invalid_corpus.json")
    golden = load_json(ASSETS / "golden_cases.json")

    json_files = list(ASSETS.glob("*.json"))
    validate_manifest(manifest, json_files)
    validate_registry(registry)
    validate_registry_yaml(registry)
    validate_wire_corpus(wire, registry)
    validate_state_corpus(state)
    validate_portable_state_cases(state)
    validate_portable_vocabulary_doc()
    validate_invalid_corpus(invalid, registry)
    validate_golden_cases(golden, registry, wire, state, invalid)
    validate_fixture_bundle()
    validate_case_sets()
    validate_registry_md(registry)
    validate_wire_examples(wire, invalid, registry)
    validate_error_code_tables(wire, invalid)
    validate_markdown_tables()

    print("assets_ok")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"asset_validation_failed: {exc}", file=sys.stderr)
        sys.exit(1)
