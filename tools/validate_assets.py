#!/usr/bin/env python3
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
FIXTURES = ROOT / "fixtures"


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
    minima = registry.get("compatibility_minima", {})
    require(minima.get("max_frame_payload") == 16384, "unexpected frame-payload compatibility minimum")
    require(minima.get("max_control_payload_bytes") == 4096, "unexpected control-payload compatibility minimum")
    require(minima.get("max_extension_payload_bytes") == 4096, "unexpected extension-payload compatibility minimum")


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


def validate_state_corpus(state):
    require(state.get("schema") == "zmux-state-corpus-v1", "invalid state corpus schema")
    allowed_keys = {"expect_state", "expect_result"}
    for case in state["cases"]:
        require("id" in case and "steps" in case, "state case missing id or steps")
        for step in case["steps"]:
            require("event" in step, f"state step missing event in case {case['id']}")
            require(any(k in step for k in allowed_keys), f"state step missing expectation in case {case['id']}")


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
    for name, ids in case_sets["sets"].items():
        require(isinstance(ids, list), f"case set {name} is not a list")
        for cid in ids:
            require(cid in known_ids, f"case set {name} references unknown case id: {cid}")


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
    validate_invalid_corpus(invalid, registry)
    validate_golden_cases(golden, registry, wire, state, invalid)
    validate_fixture_bundle()
    validate_case_sets()

    print("assets_ok")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"asset_validation_failed: {exc}", file=sys.stderr)
        sys.exit(1)
