#!/usr/bin/env python3
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, obj):
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
        f.write("\n")


def build():
    registry = load_json(ASSETS / "registry.json")
    wire = load_json(ASSETS / "wire_corpus.json")
    state = load_json(ASSETS / "state_corpus.json")
    invalid = load_json(ASSETS / "invalid_corpus.json")

    cases = []

    for case in wire["cases"]:
        entry = {
            "id": case["id"],
            "source": "wire_corpus",
            "category": case["kind"],
        }
        if "hex" in case:
            entry["hex"] = case["hex"]
        if "expect" in case:
            entry["expect"] = case["expect"]
        if "expect_error" in case:
            entry["expect_error"] = case["expect_error"]
        if "receiver_limits" in case:
            entry["receiver_limits"] = case["receiver_limits"]
        if "notes" in case:
            entry["notes"] = case["notes"]
        cases.append(entry)

    for case in state["cases"]:
        cases.append(
            {
                "id": case["id"],
                "source": "state_corpus",
                "category": "state_scenario",
                "stream_kind": case.get("stream_kind"),
                "ownership": case.get("ownership"),
                "scope": case.get("scope"),
                "initial_state": case.get("initial_state"),
                "steps": case["steps"],
            }
        )

    for case in invalid["cases"]:
        entry = {
            "id": case["id"],
            "source": "invalid_corpus",
            "category": case["layer"],
            "description": case["description"],
            "expected_result": case["expected_result"],
        }
        if "hex" in case:
            entry["hex"] = case["hex"]
        if "input_shape" in case:
            entry["input_shape"] = case["input_shape"]
        cases.append(entry)

    golden = {
        "schema": "zmux-golden-cases-v1",
        "version": 1,
        "generated_from": {
            "registry": "assets/registry.json",
            "wire_corpus": "assets/wire_corpus.json",
            "state_corpus": "assets/state_corpus.json",
            "invalid_corpus": "assets/invalid_corpus.json",
        },
        "protocol_snapshot": {
            "preface_ver": registry["protocol"]["preface_ver"],
            "proto_ver": registry["protocol"]["proto_ver"],
            "integer_encoding": registry["protocol"]["integer_encoding"],
            "integer_max": registry["protocol"]["integer_max"],
        },
        "cases": cases,
    }

    write_json(ASSETS / "golden_cases.json", golden)


if __name__ == "__main__":
    build()
