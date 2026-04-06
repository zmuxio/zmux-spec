#!/usr/bin/env python3
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
FIXTURES = ROOT / "fixtures"


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, obj):
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
        f.write("\n")


def write_ndjson(path: Path, rows):
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False))
            f.write("\n")


def build_bundle():
    golden = load_json(ASSETS / "golden_cases.json")
    FIXTURES.mkdir(parents=True, exist_ok=True)

    wire_valid = []
    wire_invalid = []
    state_cases = []
    invalid_cases = []

    for case in golden["cases"]:
        source = case["source"]
        category = case["category"]
        if source == "wire_corpus":
            if category.endswith("_valid"):
                wire_valid.append(case)
            else:
                wire_invalid.append(case)
        elif source == "state_corpus":
            state_cases.append(case)
        elif source == "invalid_corpus":
            invalid_cases.append(case)

    write_ndjson(FIXTURES / "wire_valid.ndjson", wire_valid)
    write_ndjson(FIXTURES / "wire_invalid.ndjson", wire_invalid)
    write_ndjson(FIXTURES / "state_cases.ndjson", state_cases)
    write_ndjson(FIXTURES / "invalid_cases.ndjson", invalid_cases)

    index = {
        "schema": "zmux-fixture-bundle-v1",
        "version": 1,
        "generated_from": "assets/golden_cases.json",
        "files": [
            {
                "path": "fixtures/wire_valid.ndjson",
                "kind": "wire_valid",
                "count": len(wire_valid),
            },
            {
                "path": "fixtures/wire_invalid.ndjson",
                "kind": "wire_invalid",
                "count": len(wire_invalid),
            },
            {
                "path": "fixtures/state_cases.ndjson",
                "kind": "state_cases",
                "count": len(state_cases),
            },
            {
                "path": "fixtures/invalid_cases.ndjson",
                "kind": "invalid_cases",
                "count": len(invalid_cases),
            },
        ],
    }
    write_json(FIXTURES / "index.json", index)


if __name__ == "__main__":
    build_bundle()
