"""R3 prompt-derived controls for the seven route fail-closed repairs."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import benchmark_dispatch as bd  # noqa: E402
import route_decision as rd  # noqa: E402
import task_nature_route as tnr  # noqa: E402


@pytest.mark.parametrize("raw", [
    '{"disposition":"NEEDS_CLARIFICATION","disposition":"CONFIRM"}',
    '{"metadata":{"evidence":"a","evidence":"b"}}',
    '{"evidence":{"binding":{"x":1,"x":2}}}',
    '{"model": Infinity}',
])
def test_r3_strict_json_rejects_duplicate_or_nonfinite_evidence(raw):
    with pytest.raises(ValueError):
        bd._strict_json_loads(raw)


def test_r3_strict_json_valid_control_is_byte_stable():
    raw = '{"disposition":"CONFIRM","metadata":{"evidence":[1,2]}}'
    assert bd._strict_json_loads(raw) == bd._strict_json_loads(
        json.dumps(json.loads(raw), separators=(",", ":")))


def test_r3_default_and_typed_ultra_front_doors_are_distinct():
    request = {"prompt": "Design a neutral RTL module."}
    assert rd.mode_intent(semantic_request=request)["authority"] == "PROGRAM_DEFAULT"
    evidence = rd.explicit_ultra_evidence("execution mode: ultra")
    assert evidence is not None
    intent = rd.mode_intent(
        semantic_request=request, explicit_user_evidence=evidence)
    assert intent["authority"] == "USER_EXPLICIT_ULTRA"
    assert intent["ultra_match"] is True


@pytest.mark.parametrize("prompt,target", [
    ("Design an IC and deliver a GDS.", "rtl"),
    ("Design a chip for tapeout.", "ip_hardmacro"),
    ("Complete a DIE and generate GDSII.", "rtl"),
])
def test_r3_owner_delivery_floor_rejects_ai_downgrade(prompt, target):
    req = tnr.prompt_delivery_requirements(prompt)
    resolved = tnr.resolve_prompt_delivery_target(req, target)
    assert resolved["ok"] is False


def test_r3_ip_hardmacro_views_are_not_a_die_conflict():
    prompt = "Deliver an IP hardmacro and produce a GDS with LEF, Liberty and Verilog views."
    req = tnr.prompt_delivery_requirements(prompt)
    assert req["route_family"] == "HARDMACRO"
    assert tnr.resolve_prompt_delivery_target(req, "ip_hardmacro")["ok"] is True


def test_r3_common_dependency_terminals_are_reachable():
    assert rd.dependency_closed_join(["5", "37"])["step"] == "39"
    assert rd.dependency_closed_join(["33", "37"])["step"] == "36"

