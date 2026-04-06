#!/usr/bin/env python3
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


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


def export_registry_yaml():
    registry = load_json(ASSETS / "registry.json")
    lines = to_yaml_lines(registry)
    content = "\n".join(lines) + "\n"
    with (ASSETS / "registry.yaml").open("w", encoding="utf-8") as f:
        f.write(content)


if __name__ == "__main__":
    export_registry_yaml()
