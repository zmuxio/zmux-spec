#!/usr/bin/env python3
"""Write assets/manifest.json: an inventory of the generated assets."""

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def manifest():
    files = ["assets/registry.json", "fixtures/profiles.json", "fixtures/wire/valid.ndjson",
             "fixtures/wire/invalid.ndjson", "fixtures/traces/index.json", "WIRE_EXAMPLES.md"]
    traces = sorted((ROOT / "fixtures/traces").rglob("*.ndjson"))
    index = json.loads((ROOT / "fixtures/traces/index.json").read_text())["traces"]
    groups = {}
    for t in index:
        g = t["id"].split(".")[0]
        groups[g] = groups.get(g, 0) + 1
    return {
        "protocol": "zmux",
        "counts": {
            "traces": len(traces),
            "trace_steps": sum(t["steps"] for t in index),
            "traces_by_group": dict(sorted(groups.items())),
            "wire_valid": len((ROOT / "fixtures/wire/valid.ndjson").read_text().splitlines()),
            "wire_invalid": len((ROOT / "fixtures/wire/invalid.ndjson").read_text().splitlines()),
        },
        "sha256": {f: _sha(ROOT / f) for f in files},
        "traces_sha256": hashlib.sha256(b"".join(_sha(p).encode() for p in traces)).hexdigest(),
    }


def main():
    (ROOT / "assets" / "manifest.json").write_text(json.dumps(manifest(), indent=2) + "\n")
    print("manifest: assets/manifest.json")


if __name__ == "__main__":
    sys.exit(main())
