#!/usr/bin/env python3
"""Regenerate every generated asset (deterministic), in dependency order."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"


def run(script):
    subprocess.run([sys.executable, str(TOOLS / script)], check=True, cwd=str(ROOT))


def main():
    run("build_registry.py")   # assets/registry.json, REGISTRY.md tables
    run("build_wire.py")       # WIRE_EXAMPLES.md, fixtures/wire/*
    run("build_traces.py")     # fixtures/profiles.json, fixtures/traces/**
    run("build_manifest.py")   # assets/manifest.json


if __name__ == "__main__":
    main()
