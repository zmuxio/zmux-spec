#!/usr/bin/env python3
"""Write assets/registry.json and the generated tables of REGISTRY.md."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from zmuxwire import render  # noqa: E402


def main():
    (ROOT / "assets").mkdir(exist_ok=True)
    data = render.registry_json()
    (ROOT / "assets" / "registry.json").write_text(json.dumps(data, indent=2) + "\n")
    md = ROOT / "REGISTRY.md"
    md.write_text(render.render_registry_md(md.read_text()))
    print("registry: assets/registry.json, REGISTRY.md")


if __name__ == "__main__":
    main()
