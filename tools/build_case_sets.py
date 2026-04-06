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


def build():
    golden = load_json(ASSETS / "golden_cases.json")

    sets = {
        "codec_valid": [],
        "codec_invalid": [],
        "preface": [],
        "stream_lifecycle": [],
        "flow_control": [],
        "unidirectional": [],
        "open_metadata": [],
        "priority_update": [],
    }

    for case in golden["cases"]:
        cid = case["id"]
        source = case["source"]
        category = case["category"]

        if source == "wire_corpus":
            if category.endswith("_valid"):
                sets["codec_valid"].append(cid)
            else:
                sets["codec_invalid"].append(cid)
            if cid.startswith("preface_"):
                sets["preface"].append(cid)
            if "open_metadata" in cid:
                sets["open_metadata"].append(cid)
            if "priority_update" in cid:
                sets["priority_update"].append(cid)

        elif source == "state_corpus":
            sets["stream_lifecycle"].append(cid)
            stream_kind = case.get("stream_kind")
            if stream_kind and stream_kind.startswith("uni_"):
                sets["unidirectional"].append(cid)
            if "goaway" in cid:
                sets["flow_control"].append(cid)

        elif source == "invalid_corpus":
            layer = category
            if layer in {"flow_control"}:
                sets["flow_control"].append(cid)
            if "open_metadata" in cid:
                sets["open_metadata"].append(cid)
            if "priority_update" in cid:
                sets["priority_update"].append(cid)
            if "uni" in cid:
                sets["unidirectional"].append(cid)
            if cid.startswith("preface_"):
                sets["preface"].append(cid)

    for name in list(sets.keys()):
        sets[name] = sorted(dict.fromkeys(sets[name]))

    FIXTURES.mkdir(parents=True, exist_ok=True)
    write_json(
        FIXTURES / "case_sets.json",
        {
            "schema": "zmux-case-sets-v1",
            "version": 1,
            "generated_from": "assets/golden_cases.json",
            "sets": sets,
        },
    )


if __name__ == "__main__":
    build()
