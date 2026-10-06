#!/usr/bin/env python3
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
FIXTURES = ROOT / "fixtures"

# Portable state-case vocabulary, documented in examples/fixture_mapping.md
# Section 2.1. Every token has one wire- or API-level meaning that any
# implementation can drive and observe without reference-implementation internals.
PORTABLE_EVENTS = {
    "peer_first_DATA",
    "peer_first_ABORT",
    "peer_first_RESET",
    "peer_first_MAX_DATA",
    "peer_first_BLOCKED",
    "peer_first_opening_frame_stream_id_gap",
    "peer_DATA",
    "peer_DATA_FIN",
    "peer_BLOCKED",
    "peer_STOP_SENDING",
    "peer_late_DATA",
    "peer_late_RESET",
    "peer_late_STOP_SENDING",
    "peer_same_MAX_DATA",
    "peer_same_BLOCKED",
    "local_DATA_FIN",
    "local_STOP_SENDING",
    "local_MAX_DATA",
}

PORTABLE_RESULTS = {
    "protocol_violation",
    "abort_stream_state",
    "local_invalid",
    "sender_must_finish_with_reset_or_fin",
    "restore_session_budget_only_after_terminal_data",
    "no_control_flush",
}

# Results that hold only on a fully terminal stream. On a stream that can still
# receive, whether a control frame is flushed depends on window accounting the
# case does not state (pending credit, zero-window grants, pacing thresholds).
TERMINAL_ONLY_RESULTS = {"no_control_flush"}

TERMINAL_HALVES = {
    "send_half": {"absent", "send_fin", "send_reset", "send_aborted"},
    "recv_half": {"absent", "recv_fin", "recv_reset", "recv_aborted"},
}


def is_terminal_state(state) -> bool:
    return (
        isinstance(state, dict)
        and set(state) == set(TERMINAL_HALVES)
        and all(state[half] in TERMINAL_HALVES[half] for half in TERMINAL_HALVES)
    )


def is_portable_state_case(case) -> bool:
    """A state case any implementation can run from the fixture data alone.

    It names its preconditions, and every step uses the portable vocabulary.
    Other cases are reference-implementation regression scenarios, depend on
    unstated receive-window accounting, or encode local abuse policy.
    """
    initial = case.get("initial_state")
    if not (case.get("stream_kind") and case.get("ownership") and initial is not None):
        return False
    for step in case["steps"]:
        if step["event"] not in PORTABLE_EVENTS:
            return False
        result = step.get("expect_result")
        if result is None:
            continue
        if result not in PORTABLE_RESULTS:
            return False
        if result in TERMINAL_ONLY_RESULTS and not is_terminal_state(initial):
            return False
    return True


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
        "session_lifecycle": [],
        "flow_control": [],
        "unidirectional": [],
        "open_metadata": [],
        "priority_update": [],
        "frame_invalid": [],
        "local_policy": [],
        "portable_state": [],
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
            scope = case.get("scope")
            if scope == "session":
                sets["session_lifecycle"].append(cid)
            else:
                sets["stream_lifecycle"].append(cid)
            stream_kind = case.get("stream_kind")
            if stream_kind and stream_kind.startswith("uni_"):
                sets["unidirectional"].append(cid)
            if scope == "flow_control":
                sets["flow_control"].append(cid)
            if scope == "open_metadata" or "open_metadata" in cid:
                sets["open_metadata"].append(cid)
            if "priority_update" in cid:
                sets["priority_update"].append(cid)
            if is_portable_state_case(case):
                sets["portable_state"].append(cid)

        elif source == "invalid_corpus":
            layer = category
            if layer in {"flow_control"}:
                sets["flow_control"].append(cid)
            # codec_invalid stays equal to the byte-level wire_invalid fixtures.
            if layer == "frame":
                sets["frame_invalid"].append(cid)
            if layer == "state":
                sets["stream_lifecycle"].append(cid)
            if layer == "session":
                sets["session_lifecycle"].append(cid)
            if layer in {"open_semantics", "api_semantics", "security"}:
                sets["local_policy"].append(cid)
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
