#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"


def run(script_name: str):
    script_path = TOOLS / script_name
    subprocess.run([sys.executable, str(script_path)], check=True, cwd=str(ROOT))


def main():
    run("build_golden_cases.py")
    run("export_fixture_bundle.py")
    run("build_case_sets.py")
    run("export_registry_yaml.py")
    run("validate_assets.py")


if __name__ == "__main__":
    main()
