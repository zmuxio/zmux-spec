"""Render registry tables and the JSON export (shared by builder and validator)."""

import re

from . import registry as R


def hx(v):
    if v < 0x100:
        return f"0x{v:02x}"
    if v < 0x10000:
        return f"0x{v:04x}"
    if v < (1 << 32):
        return f"0x{v:08x}"
    return f"0x{v:016x}"


def frame_range(f):
    return hx(f["first"]) if f["first"] == f["last"] else f"{hx(f['first'])}-{hx(f['last'])}"


def table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def md_frames():
    rows = []
    for f in R.FRAME_TYPES:
        if f["kind"] == "prohibited":
            rows.append([f["name"], frame_range(f), "QUIC v1, prohibited", "-", "-", "-",
                         f["spec"]])
            continue
        rows.append([f["name"], frame_range(f), {
            "core": "QUIC v1 (RFC 9000)", "qmux": "QMux-02", "quic-ext": "QUIC extension",
            "zmux": "zmux"}[f["kind"]],
            " or ".join(f["gate"]) if isinstance(f["gate"], tuple) else (f["gate"] or "-"),
            "yes" if f.get("ack_eliciting") else "no", f.get("retransmit", "-"), f["spec"]])
    return table(["Frame", "Type", "Origin", "Gate", "Ack-eliciting", "Retransmission",
                  "Defined in"], rows)


def md_tps():
    rows = []
    for p in R.TRANSPORT_PARAMETERS:
        if p["scope"] == "prohibited":
            rows.append([p["name"], hx(p["id"]), "QUIC v1, prohibited (TRANSPORT_PARAMETER_ERROR)",
                         "-", "-", "SPEC.md#5.2"])
            continue
        origin = {"qmux": "QUIC/QMux", "zmux": "zmux", "zmux-reserved": "zmux (reserved)"}[
            p["scope"]]
        rows.append([p["name"], hx(p["id"]), origin + ", " + p["format"],
                     str(p.get("absent_default")), str(p.get("zmux_sends")), p["spec"]])
    return table(["Parameter", "ID", "Origin, format", "Absent means", "zmux endpoints send",
                  "Defined in"], rows)


def md_tlvs():
    rows = [[t["name"], hx(t["type"]), t["status"], t["value"], t["spec"]] for t in
            R.PREAMBLE_TLVS]
    rows.append(["application", f"{hx(R.APP_TLV_FIRST)}..{hx(R.APP_TLV_LAST)}", "application",
                 "opaque bytes; never allocated by zmux, delivered to the application; GREASE values excluded",
                 "EXTENSIONS.md#4.3"])
    rows.append(["GREASE", "0x1f*N+0x21", "grease", "any, ignored", "EXTENSIONS.md#4.3"])
    return table(["TLV", "Type", "Status", "Value", "Defined in"], rows)


def md_transport_errors():
    rows = [[e["name"], hx(e["code"]), e["source"], "yes" if e["wire"] else "never (local only)"]
            for e in R.TRANSPORT_ERRORS]
    return table(["Code", "Value", "Defined by", "Sent on the wire"], rows)


def md_app_errors():
    rows = [[e["name"], hx(e["code"]),
             "-" if e["socks5_rep"] is None else f"0x{e['socks5_rep']:02x}"]
            for e in R.APPLICATION_ERRORS]
    return table(["Code", "Value", "SOCKS5 REP"], rows)


def md_fixed():
    rows = []
    for k, v in R.ROLE_VALUES.items():
        rows.append(["zmux_role Role", k, v])
    for k, v in R.PIPE_DIRECTIONS.items():
        rows.append(["PIPE_JOIN Flags direction (mask 0x03)", k, v])
    rows.append(["PIPE_JOIN Flags", hx(R.PIPE_JOIN_FLAG_CARRIER_BOUND), "carrier-bound"])
    rows.append(["PIPE_JOIN Flags", hx(R.PIPE_JOIN_FLAG_STANDBY),
                 "standby (initial request for the acceptor's outbound direction)"])
    rows.append(["PIPE_JOIN Flags reserved mask", hx(R.PIPE_JOIN_RESERVED_MASK), "must be 0"])
    for k, v in R.MULTIPATH_SECRET_FLAGS.items():
        rows.append(["zmux_multipath Secret Flags", hx(k), v])
    for k, v in R.PIPE_STATUS_VALUES.items():
        rows.append(["PIPE_STATUS Status", k, v])
    rows.append(["PIPE_STATUS Priority", "0 to 2^62 - 1",
                 f"lower preferred; default {R.PIPE_PRIORITY_DEFAULT}; every value is valid"])
    return table(["Field", "Value", "Meaning"], rows)


def md_labels():
    rows = [[k, f"`{v}`"] for k, v in R.LABELS.items()]
    rows += [[k, str(v)] for k, v in R.LIMITS.items()]
    rows.append(["protocol_name", R.PROTOCOL_NAME])
    rows.append(["alpn_default", f"`{R.ALPN_DEFAULT}`"])
    rows.append(["alpn_also_accepted", ", ".join(f"`{a}`" for a in R.ALPN_ALSO_ACCEPTED)])
    rows.append(["alpn_draft01_alias", "; ".join(R.ALPN_ALIAS_RULE)])
    rows.append(["qmux_draft", R.QMUX_DRAFT])
    return table(["Name", "Value"], rows)


SECTIONS = {
    "frames": md_frames,
    "transport_parameters": md_tps,
    "preamble_tlvs": md_tlvs,
    "transport_errors": md_transport_errors,
    "application_errors": md_app_errors,
    "fixed_values": md_fixed,
    "labels": md_labels,
}


def fill_markers(text, sections):
    for name, content in sections.items():
        pat = re.compile(rf"(<!-- BEGIN GENERATED: {name} -->\n)(.*?)(<!-- END GENERATED: {name} -->)",
                         re.S)
        if not pat.search(text):
            raise ValueError(f"missing generated marker {name}")
        text = pat.sub(lambda m: m.group(1) + content.rstrip("\n") + "\n" + m.group(3), text)
    return text


def render_registry_md(text):
    return fill_markers(text, {k: f() for k, f in SECTIONS.items()})


def registry_json():
    def num(v):
        return {"value": v, "hex": hx(v)}

    return {
        "protocol": {"name": R.PROTOCOL_NAME,
                     "alpn_default": R.ALPN_DEFAULT, "alpn_also_accepted": R.ALPN_ALSO_ACCEPTED,
                     "alpn_alias_rule": R.ALPN_ALIAS_RULE,
                     "alpn_alias_examples": R.ALPN_ALIAS_EXAMPLES,
                     "qmux_draft": R.QMUX_DRAFT,
                     "reliable_stream_reset_draft": "draft-ietf-quic-reliable-stream-reset-11"},
        "frame_types": [dict({k: v for k, v in f.items() if k not in ("first", "last")},
                             first=num(f["first"]), last=num(f["last"])) for f in R.FRAME_TYPES],
        "transport_parameters": [dict({k: v for k, v in p.items() if k != "id"}, id=num(p["id"]))
                                 for p in R.TRANSPORT_PARAMETERS],
        "transport_errors": [dict({k: v for k, v in e.items() if k != "code"}, code=num(e["code"]))
                             for e in R.TRANSPORT_ERRORS],
        "application_errors": [dict({k: v for k, v in e.items() if k != "code"},
                                    code=num(e["code"]))
                               for e in R.APPLICATION_ERRORS],
        "application_error_block": {"first": hx(R.APP_ERROR_BASE),
                                    "last": hx(R.APP_ERROR_BASE + 0xFFF)},
        "preamble_tlvs": [dict({k: v for k, v in t.items() if k != "type"}, type=num(t["type"]))
                          for t in R.PREAMBLE_TLVS],
        "preamble_tlv_application_range": {"first": hx(R.APP_TLV_FIRST), "last": hx(R.APP_TLV_LAST)},
        "grease": {"transport_parameter": "31*N+27", "preamble_tlv": "0x1f*N+0x21"},
        "fixed_values": {
            "zmux_role": {str(k): v for k, v in R.ROLE_VALUES.items()},
            "pipe_join_direction": {str(k): v for k, v in R.PIPE_DIRECTIONS.items()},
            "pipe_join_carrier_bound": hx(R.PIPE_JOIN_FLAG_CARRIER_BOUND),
            "pipe_join_standby": hx(R.PIPE_JOIN_FLAG_STANDBY),
            "pipe_join_reserved_mask": hx(R.PIPE_JOIN_RESERVED_MASK),
            "multipath_secret_flags": {hx(k): v for k, v in R.MULTIPATH_SECRET_FLAGS.items()},
            "pipe_status": {str(k): v for k, v in R.PIPE_STATUS_VALUES.items()},
            "pipe_priority": {"min": 0, "max": (1 << 62) - 1,
                              "default": R.PIPE_PRIORITY_DEFAULT,
                              "order": "lower preferred; every value valid"},
        },
        "labels": R.LABELS,
        "limits": R.LIMITS,
        "allocation": {
            "method": "v = 2^14 + (SHA-256('zmux <kind> <name>[#i]')[0:8] as big-endian) mod (2^30 - 2^14)",
            "suffix": {f"{k[0]}:{k[1]}": v for k, v in R.ALLOCATION_SUFFIX.items()},
        },
    }
