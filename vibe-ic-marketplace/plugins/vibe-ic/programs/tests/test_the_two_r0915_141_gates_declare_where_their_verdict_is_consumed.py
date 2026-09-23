"""R-0915-141 left two gates UNDECLARED and AUDIT_ONLY — they declare it now.

MEASURED (lane ictier1): on main cdaeed38c `flow_gate_enforcement_audit` exits 1
with {'new_and_unrecorded': ['undeclared::foundry_handoff_package_check',
'undeclared::tapeout_signoff_check']}, and four tests that run the audit over
the shipped tree are red (test_issue1035_five_gates_declare_where_they_are_
enforced x2, test_two_gates_declare_where_their_verdict_is_consumed x2).

WHICH LANDING: 560a7427a (R-0915-141, merged in #2525). At its parent 414e224b1
the audit read both gates ENFORCED / INLINE_BLOCKING with no declaration,
because phase3_one_shot_runner's `_PRE_AUDIT_PRODUCERS` spawned them inline and
mapped the exit status to a row. R-0915-141 correctly replaced them there with
the programs that PRODUCE the steps' declared outputs (tapeout_checklist_gen,
foundry_handoff_pack_gen), and nothing spawns the two checkers inline any more.

WHICH SIDE IS WRONG: the gates. Their verdict was never a runner decision -- the
runner's own docstring for that loop said "a refusal here is disclosed and does
not withhold the release -- these steps' own yaml clauses already decide them
in the audit". It is consumed where it always was: the `program_exit_zero`
clauses of step 36 (`tapeout_signoff_check . --mode tapeout ...`) and step 38
(`foundry_handoff_package_check . ...`) in flow_compliance_check. So each gate
DECLARES `ENFORCEMENT: advisory` and says where its verdict is consumed.
Recording them in the baseline register would be a baseline, not a decision.

NOT A GREP: every assertion reads the audit's emitted JSON or the parsed flow.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

_PROGRAMS = Path(__file__).resolve().parents[1]
_FLOW = _PROGRAMS.parent / "flow" / "phase1_phase2_phase3.yaml"
_AUDIT = _PROGRAMS / "flow_gate_enforcement_audit.py"

#: (gate, flow step whose gate clause consumes its verdict)
_GATES = (("tapeout_signoff_check", 36), ("foundry_handoff_package_check", 38))


def _audit(tmp_path):
    out = tmp_path / "gea.json"
    r = subprocess.run([sys.executable, str(_AUDIT), "--json", str(out)],
                       capture_output=True, text=True)
    return r.returncode, json.loads(out.read_text()), r.stdout


def _clause_commands(step):
    cmds = []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "program_exit_zero":
                    cmds.append(v if isinstance(v, str) else v.get("command"))
                else:
                    walk(v)
        elif isinstance(node, list):
            for x in node:
                walk(x)
    walk(step.get("gate"))
    return [c for c in cmds if c]


def test_both_gates_declare_advisory_and_the_audit_reads_it(tmp_path):
    rc, doc, out = _audit(tmp_path)
    rows = {g["gate"]: g for g in doc["gates"]}
    for gate, _step in _GATES:
        assert rows[gate]["declared"] == "advisory", (gate, rows[gate])
        # advisory is the TRUE word: nothing spawns it inline
        assert rows[gate]["enforcement"] == "AUDIT_ONLY", rows[gate]
    undeclared = {g["gate"] for g in doc.get("undeclared_audit_only", [])}
    assert not undeclared & {g for g, _ in _GATES}, undeclared


def test_the_audit_names_neither_gate_as_new(tmp_path):
    rc, doc, out = _audit(tmp_path)
    for gate, _step in _GATES:
        assert gate not in out, out[-2000:]
    assert rc == 0, out[-2000:]


def test_the_consuming_clause_the_declaration_names_exists(tmp_path):
    """The declaration says the verdict is consumed by step N's gate clause;
    the flow must actually carry that clause running that program."""
    steps = {s.get("id"): s for s in yaml.safe_load(_FLOW.read_text())["steps"]}
    for gate, step_id in _GATES:
        cmds = _clause_commands(steps[step_id])
        assert any(c.split()[0] == gate for c in cmds), (gate, step_id, cmds)
        src = (_PROGRAMS / f"{gate}.py").read_text()[:4000]
        assert f"step {step_id}" in src.lower(), gate
