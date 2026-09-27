"""llv1 W3: the flow-mode layer above the switch file and the class defaults.

Contracts:
  1. With no implementation-flow record the layer does not exist: every step
     any shipped call site asks `selected_mode` about gets today's answer,
     and asking writes nothing.
  2. Under a `librelane` record every such step gets the layer's answer,
     with no switch file, on a plain project and on a chip-path project
     alike (the layer outranks the class defaults).
  3. The call sites are DERIVED from the tree (AST over programs/), and every
     step they ask about has a defined answer under the flag: in the layer,
     or in the named out-of-layer set (the analog arms, refused at the front
     door in v1).
  4. The owner's kept checks: 13 and 25 stay vibe-ic; 8, 24, 26 and 31 are
     `dual`, because their `librelane` mode replaces vibe-ic's judgement.
  5. A switch file naming a step the flag decides refuses the run with
     IMPL_SWITCH_CONFLICT (decision 18), even when both say the same thing;
     a switch naming only out-of-layer steps is honoured.
  6. A damaged record refuses by its own reason class; `orfs` has no layer.
  7. `class_defaults_in_force` is empty under the flag.
  8. Step 9's LibreLane arm runs with no switch file (it used to crash).
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

import _plugin_tree  # noqa: F401 — puts programs/ on sys.path
import _impl_flow as IF
import librelane_contract as LC
import _owner_declared as _OD  # noqa: E402 — tests/, the one attestation fixture

PROGRAMS = Path(LC.__file__).resolve().parent
#: The ruling, restated as the contract: under `--librelane` LibreLane
#: produces the step except where the owner keeps vibe-ic's check.
LAYER = {
    "2": "direct", "3": "direct", "4": "direct", "5": "direct",
    "7": "librelane", "8": "dual", "9": "librelane", "10": "librelane",
    "13": "direct",
    "15": "librelane", "15.5ic": "librelane", "17": "librelane",
    "18": "librelane", "19": "librelane", "20": "librelane",
    "21": "librelane", "22": "librelane", "23": "librelane",
    "24": "dual", "25": "direct", "26": "dual", "26.5ic": "librelane",
    "31": "dual", "32": "librelane", "33": "librelane", "34": "librelane",
    "37": "librelane", "DT2": "librelane", "DT3": "librelane",
}
#: Steps a call site asks about that the flag deliberately does not decide.
OUT_OF_LAYER = {"A6", "A7"}
#: Call sites whose step is a runtime value no literal names. Each entry says
#: where its population comes from; a stale entry fails the test.
DYNAMIC_SITES = {
    ("librelane_pv_signoff.py", "step"): "STATE_KEYS",
    ("phase3_one_shot_runner.py", "step"): ("DT2", "DT3"),
}
_CALLEES = {"selected_mode", "_ll_selected_mode"}


def _tree(project: Path) -> dict:
    return {str(p.relative_to(project)): (hashlib.sha256(p.read_bytes()).hexdigest()
                                          if p.is_file() else "dir")
            for p in sorted(project.rglob("*"))}


def _record(project: Path, impl: str = "librelane") -> None:
    IF.write_record(project, impl, resolved_by="test")


def _switch(project: Path, steps: dict) -> None:
    p = project / "phase3" / "librelane_switch.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"steps": steps}))


def _chip(project: Path) -> Path:
    st = project / "input" / "submission_template"
    st.mkdir(parents=True, exist_ok=True)
    (st / "SELF_TAPEOUT.txt").write_text("# self tape-out\n")
    (st / "tapeout_declaration.json").write_text(json.dumps(_OD.attest(
        {"schema": "vibe-ic/tapeout_declaration/1",
         "answers": {"deliverable": "DIE"}})))
    return project


def _constants(module: ast.Module) -> dict:
    out = {}
    for node in module.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            try:
                out[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                pass
    return out


def _loop_values(parents: list, name: str, consts: dict):
    """The literal population a loop or comprehension binds `name` to."""
    for node in reversed(parents):
        gens = []
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            gens = node.generators
        elif isinstance(node, ast.For):
            gens = [node]
        for gen in gens:
            if isinstance(gen.target, ast.Name) and gen.target.id == name:
                if isinstance(gen.iter, ast.Name) and gen.iter.id in consts:
                    return tuple(consts[gen.iter.id])
                try:
                    return tuple(ast.literal_eval(gen.iter))
                except ValueError:
                    return None
    return None


def call_sites():
    """[(file, line, steps)] for every shipped `selected_mode` call."""
    sites = []
    for path in sorted(PROGRAMS.rglob("*.py")):
        rel = path.relative_to(PROGRAMS)
        if rel.parts[0] == "tests" or path.name == "librelane_contract.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "selected_mode" not in text:
            continue
        module = ast.parse(text)
        consts = _constants(module)

        def walk(node, parents):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
                if name in _CALLEES and len(node.args) >= 2:
                    arg = node.args[1]
                    if isinstance(arg, ast.Constant):
                        steps = (arg.value,)
                    elif isinstance(arg, ast.Name):
                        steps = _loop_values(parents, arg.id, consts)
                        if steps is None and arg.id in consts:
                            steps = (consts[arg.id],)
                        if steps is None:
                            source = DYNAMIC_SITES.get((path.name, arg.id))
                            if isinstance(source, str):
                                table = getattr(__import__(path.stem), source)
                                steps = tuple(sorted({v[0] for v in table.values()}))
                            else:
                                steps = source
                    else:
                        steps = None
                    sites.append((str(rel), node.lineno, steps))
            for child in ast.iter_child_nodes(node):
                walk(child, parents + [node])

        walk(module, [])
    return sites


def test_every_call_site_is_enumerated_and_resolved():
    sites = call_sites()
    assert sites, "the instrument found no call site; it cannot see"
    unresolved = [s for s in sites if not s[2]]
    assert not unresolved, unresolved
    # Positive control on the instrument: sites known to exist are found.
    files = {s[0] for s in sites}
    for known in ("phase3_one_shot_runner.py", "design_one_shot_runner.py",
                  "librelane_route.py", "librelane_pv_signoff.py"):
        assert known in files
    # Every DYNAMIC_SITES entry still names a real site (no stale entries).
    for (name, var) in DYNAMIC_SITES:
        assert any(Path(f).name == name for f, _, _ in sites), (name, var)


def test_the_contract_carries_the_ruled_layer():
    assert getattr(LC, "IMPL_STEP_MODES", {}).get("librelane") == LAYER


def test_every_step_a_call_site_asks_about_is_decided_under_the_flag():
    asked = {step for _, _, steps in call_sites() for step in steps}
    undecided = sorted(asked - set(LAYER) - OUT_OF_LAYER)
    assert not undecided, f"call sites ask about steps the flag leaves open: {undecided}"
    assert all(m in ("direct", "librelane", "dual") for m in LAYER.values())


@pytest.mark.parametrize("chip", [False, True])
def test_no_record_gives_todays_answer_at_every_site_and_writes_nothing(tmp_path, chip):
    project = _chip(tmp_path) if chip else tmp_path
    before = _tree(project)
    for _, _, steps in call_sites():
        for step in steps:
            assert getattr(LC, "impl_step_modes", lambda p: None)(project) is None
            expected = (LC._class_default(project, step, {}) or "direct")
            assert LC.selected_mode(project, step) == expected, step
    assert _tree(project) == before


@pytest.mark.parametrize("chip", [False, True])
def test_under_librelane_every_site_gets_the_layers_answer(tmp_path, chip):
    project = _chip(tmp_path) if chip else tmp_path
    _record(project)
    for _, _, steps in call_sites():
        for step in steps:
            if step in LAYER:
                assert LC.selected_mode(project, step) == LAYER[step], step
    # The chain the chip class defaults run is LibreLane's under the flag
    # too, but because the flag decides it, not the class.
    assert LC.class_defaults_in_force(project) == {}


def test_the_owners_kept_checks_are_not_replaced_by_the_tool(tmp_path):
    _record(tmp_path)
    kept = {"13": "direct", "25": "direct", "2": "direct",
            "8": "dual", "24": "dual", "26": "dual", "31": "dual",
            "23": "librelane", "32": "librelane"}
    assert {s: LC.selected_mode(tmp_path, s) for s in kept} == kept


@pytest.mark.parametrize("mode", ["librelane", "direct", "dual"])
def test_a_switch_naming_a_step_the_flag_decides_refuses(tmp_path, mode):
    _record(tmp_path)
    _switch(tmp_path, {"21": mode})
    for step in ("21", "9", "A6"):       # the run refuses, not just step 21
        with pytest.raises(LC.Refusal) as exc:
            LC.selected_mode(tmp_path, step)
        assert exc.value.code == "IMPL_SWITCH_CONFLICT"
        assert "'21'" in str(exc.value)


def test_a_switch_naming_only_out_of_layer_steps_is_honoured(tmp_path):
    _record(tmp_path)
    _switch(tmp_path, {"A6": "librelane"})
    assert LC.selected_mode(tmp_path, "A6") == "librelane"
    assert LC.selected_mode(tmp_path, "21") == "librelane"
    assert LC.selected_mode(tmp_path, "13") == "direct"


def test_without_a_record_the_switch_file_still_rules(tmp_path):
    _switch(tmp_path, {"21": "dual", "13": "librelane"})
    assert LC.selected_mode(tmp_path, "21") == "dual"
    assert LC.selected_mode(tmp_path, "13") == "librelane"


def test_a_damaged_record_refuses_never_the_default(tmp_path):
    path = IF.record_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    with pytest.raises(LC.Refusal) as exc:
        LC.selected_mode(tmp_path, "21")
    assert exc.value.code == IF.IMPL_RECORD_UNREADABLE


def test_a_mode_with_no_layer_refuses_by_name(tmp_path):
    path = IF.record_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "schema": IF.SCHEMA, "impl": "orfs", "flag": "--orfs",
        "resolved_at": "2026-09-28T00:00:00Z", "resolved_by": "test",
        "tool_defaults": {}, "image": None}))
    with pytest.raises(LC.Refusal) as exc:
        LC.selected_mode(tmp_path, "21")
    assert exc.value.code == IF.IMPL_NOT_YET_SUPPORTED


def test_step9_librelane_arm_runs_without_a_switch_file(tmp_path):
    import phase3_one_shot_runner as P3
    _record(tmp_path)
    assert LC.selected_mode(tmp_path, "9") == "librelane"
    assert not (tmp_path / "phase3/librelane_switch.json").exists()
    res = P3._step_synth_librelane(tmp_path, "top", None, "no-container")
    assert res.status == "FAIL"
    assert "LL_SYNTH_INPUT_MISSING" in res.detail
