"""Native row-cone equivalence and safe fallback; no claim is waived."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import formal_structural_check as fsc


def cell(kind, a, y, b=None, s=None):
    conn = {"A": a, "Y": y}
    params = {"A_SIGNED": 0, "A_WIDTH": len(a), "Y_WIDTH": len(y)}
    if b is not None:
        conn["B"] = b
        params.update(B_SIGNED=0, B_WIDTH=len(b))
    if s is not None:
        conn["S"] = s
        params = {"WIDTH": len(y)}
    return {"type": kind, "parameters": params, "connections": conn,
            "port_directions": {p: "output" if p == "Y" else "input" for p in conn}}


def model(cells):
    return {"modules": {"transition_pair": {"attributes": {}, "ports": {
        "d": {"direction": "input", "bits": [2]},
        "q": {"direction": "input", "bits": [3]},
        "n": {"direction": "output", "bits": [4]}},
        "cells": cells, "netnames": {}}}}


@pytest.mark.parametrize("defect,reason", [
    ("feedback", "combinational-feedback"),
    ("multiple", "non-unique-output-driver"),
    ("repeated-output", "non-unique-output-driver"),
    ("literal-output", "non-unique-output-driver"),
    ("input-output", "non-unique-output-driver"),
    ("unknown", "non-total-or-unsupported-cell")])
def test_non_extendable_models_keep_whole_module(defect, reason):
    cells = {"row": cell("$and", [2], [4], [3])}
    cells.update({
        "feedback": {"extra": cell("$eqx", [5], [5], [2])},
        "multiple": {"extra": cell("$not", [2], [4])},
        "repeated-output": {"extra": cell("$not", [2, 3], [5, 5])},
        "literal-output": {"extra": cell("$not", [2], ["0"])},
        "input-output": {"extra": cell("$not", [3], [2])},
        "unknown": {"extra": cell("$unknown", [2], [5])}}[defect])
    assert fsc._reset_row_cone_eligibility(model(cells)) == {"eligible": False, "reason": reason}


@pytest.mark.parametrize("direction", [None, "inout"])
def test_unknown_directions_keep_whole_module(direction):
    data = model({"row": cell("$and", [2], [4], [3])})
    data["modules"]["transition_pair"]["cells"]["row"]["port_directions"]["A"] = direction
    assert fsc._reset_row_cone_eligibility(data) == {
        "eligible": False, "reason": "unknown-native-pin-direction"}


def native(tmp_path, data, commands, cone):
    if not shutil.which("yosys"):
        pytest.fail("native Yosys required for this focused repair control")
    tmp_path.mkdir()
    (tmp_path / "model.json").write_text(json.dumps(data))
    lines = [f"read_json {tmp_path / 'model.json'}", "hierarchy -top transition_pair"]
    if cone:
        lines += ["select -module transition_pair", "select w:n %% %ci*"]
    for i, cmd in enumerate(commands):
        lines += [f"log VIBEIC_TRANSITION_QUERY {i}",
                  "sat -timeout 10 -enable_undef -set-def-inputs " + cmd + " -show-inputs -show n",
                  f"log VIBEIC_TRANSITION_COMPLETE {i}"]
    script = tmp_path / "query.ys"
    script.write_text("\n".join(lines) + "\n")
    run = subprocess.run(["yosys", "-Q", "-T", "-s", str(script)], capture_output=True, text=True, timeout=30)
    (tmp_path / "native.log").write_text(run.stdout + run.stderr)
    assert run.returncode == 0, run.stdout + run.stderr
    return fsc._transition_query_progress(run.stdout + run.stderr, [str(i) for i in range(len(commands))])


@pytest.mark.parametrize("literal", [None, "x", "z"])
def test_native_undef_shared_q_and_cofactor_are_identical(tmp_path, literal):
    # n uses the exact same shared current Q and undef source twice through
    # reconvergence. The off-cone branch is total but must not normalize X.
    source = 8 if literal is None else literal
    data = model({"inv": cell("$not", [source], [5]),
                  "both": cell("$and", [source], [6], [5]),
                  "row": cell("$mux", [6], [4], [3], [2]),
                  "extra": cell("$xor", [2], [7], [3])})
    assert fsc._reset_row_cone_eligibility(data)["eligible"]
    commands = ["-set-any-undef n", "-prove n 0", "-prove n 1",
                "-set d 1 -prove n q", "-set d 0 -set-any-undef n"]
    full = native(tmp_path / "full", data, commands, False)
    cone = native(tmp_path / "cone", data, commands, True)
    assert full["by_identity"] == cone["by_identity"]
    # Pinned native Yosys treats this Z arithmetic differently from X. Keep
    # that exact native behavior; do not replace it with a Python X/Z rule.
    assert full["by_identity"]["0"]["result"] == ("NO_UNDEF" if literal == "z" else "HAS_UNDEF")
    assert full["by_identity"]["3"]["result"] == "PROVED"


def test_native_feedback_can_restrict_shared_domain_and_is_not_pruned(tmp_path):
    data = model({"row": cell("$and", [2], [4], ["1"]),
                  "feedback": cell("$eqx", [5], [5], [2])})
    assert not fsc._reset_row_cone_eligibility(data)["eligible"]
    full = native(tmp_path / "full", data, ["-prove n 1"], False)
    unsound = native(tmp_path / "unsafe-cone", data, ["-prove n 1"], True)
    assert full["by_identity"]["0"]["result"] == "PROVED"
    assert unsound["by_identity"]["0"]["result"] == "REFUTED"
