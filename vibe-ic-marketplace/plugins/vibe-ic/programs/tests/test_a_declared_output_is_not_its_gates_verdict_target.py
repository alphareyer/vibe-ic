"""R-0915-141 — a step's declared output is a document the RUN produces.

Never the file its gate writes its verdict to. The gate READS that document and
writes its verdict elsewhere.

WHY, AND IT IS NOT A STYLE POINT. The audit refuses self-certified evidence on
three criteria, and the third is "holding a verdict document only this step's gate
emits". When a step's declared `required_output` IS its gate's `--json` target,
that criterion matches BY CONSTRUCTION -- whoever wrote the file, however early.
MEASURED on spm run22: `tapeout_checklist_gen` rewrote the checklist at 08:51:23
and the 09:02:35 audit still refused it. No ordering can settle that, which is why
the re-stamp fix (the other half of this branch) was necessary but not sufficient.

THE SPLIT, for the ruling's two steps:
  step 36  producer  tapeout_checklist_gen -> reports/audit/tapeout_checklist.json
           gate      tapeout_signoff_check . --mode tapeout
                     --json reports/audit/tapeout_signoff.json
  step 38  producer  foundry_handoff_pack_gen -> the four package artefacts
           gate      foundry_handoff_package_check . --json
                     reports/phase3/foundry_handoff_check.json
           and `reports/phase3/foundry_handoff_audit.json` -- the gate's own
           verdict target -- is no longer in the step's declared set.

`tapeout_checklist_gen` gained `--json` as an alias for `--out`, because the flow's
producer runner names every destination with `--json`.

MEASURED, main 93b2d10fa vs this tip, on a copy of run22's own tree:
  step 38  FAIL/missing_artefact, AUDIT-CREATED OUTPUT REFUSED = True
        -> NOT_MEASURED/upstream_failed, refused = False
  step 36  its declared output is no longer among its gate's `--json` targets, so
           `_declared_self_written` is EMPTY for it and the audit_created
           machinery cannot reach it at all.

THE CLASS IS WIDER THAN TWO STEPS, and this file says so rather than pretending
otherwise: 21 other steps still declare an output that is one of their own gate
clauses' receipt targets. They are pinned below as a measured residual that may
only SHRINK. Fixing each needs its own producer and its own evidence -- the shape
of this branch, 21 more times -- and a bare class-wide assertion would simply ship
21 reds.
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

PLUGIN = Path(__file__).resolve().parents[2]
PROGRAMS = PLUGIN / "programs"
sys.path.insert(0, str(PROGRAMS))

import flow_compliance_check as FCC                          # noqa: E402

#: THE CLASS IS THE AUDIT'S OWN PREDICATE, NOT A REGEX OF MY OWN.
#:
#: This file previously derived membership from `--(?:json|report)`, which is a
#: WIDER set than the rule it is guarding. The audit's self-certified refusal is
#: `_declared_self_written = set(outputs) & _gate_json_targets(step)` -- `--json`
#: ONLY -- and the two disagreed on exactly one entry, which is why defining the
#: class twice is the defect and not a detail: step 31's
#: `reports/phase3/perc_sweep.json` is WRITTEN by its declared producer
#: `perc_corpus_sweep` and only READ by the gate's `sweep_reach_check --report`.
#: The regex called that a self-certified member and the failure text would have
#: demanded "a real producer" for a document that already has one.
#:
#: So membership is taken from `flow_compliance_check._gate_json_targets`, the
#: function the audit itself calls. A `--report` a gate READS is an input, and an
#: input is not a verdict target.


def _flow() -> dict:
    return yaml.safe_load(
        (PLUGIN / "flow" / "phase1_phase2_phase3.yaml").read_text())


def _clause_commands(gate) -> list:
    out: list = []

    def rec(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k.endswith("program_exit_zero"):
                    out.append(v if isinstance(v, str)
                               else str((v or {}).get("command", "")))
                else:
                    rec(v)
        elif isinstance(node, list):
            for item in node:
                rec(item)

    rec(gate)
    return out


def steps_declaring_their_gates_verdict_target(steps) -> dict:
    """``{step id: [declared outputs that are its own gate's --json target]}``.

    Asks `flow_compliance_check._gate_json_targets` — the audit's own function —
    rather than re-deriving the set here. A guard that computes the population a
    second way is a guard that can disagree with the rule it guards, and this one
    did.
    """
    out: dict = {}
    for st in steps:
        if not isinstance(st, dict):
            continue
        declared = set(str(x) for x in (st.get("required_outputs") or []))
        if not declared:
            continue
        own = sorted(declared & FCC._gate_json_targets(st))
        if own:
            out[str(st.get("id"))] = own
    return out


#: MEASURED 2026-09-23 on this tree, AFTER the two steps this branch splits. A
#: RESIDUAL, not a permission: each member needs its own producer and its own
#: evidence, which is this branch's shape 21 more times.
#:
#: RATCHET ON MEMBERSHIP, NOT ON COUNT -- this repo's own rule (#900, quoted in
#: `prose_polarity_consulted_check`): a count cannot say WHICH step moved, and two
#: steps trading places move nothing.
_RESIDUAL = {
    "0.5ic": ["reports/phase1/submission_template.json",
              "reports/phase1/tapeout_declaration.json"],
    # `reports/phase1/gates/stage_phase1_compliance.json` LEFT step 2 by the
    # step-38 half of this ruling (lane ictier1, spm run23): it is the nested
    # stage_phase1 clause's verdict target and has no separate producer, so it
    # left the declared set. See test_step2_does_not_declare_its_nested_gates_
    # verdict_target.py.
    "2": ["reports/crosslayer/rewrite_equivalence_check.json",
          "reports/phase2/lint/rom_init_lint.json",
          "reports/phase2/lint/rtl_hygiene.json"],
    "8": ["reports/phase2/sdc_check.json"],
    "10": ["reports/phase3/sta/pre_pnr_summary.json"],
    "11": ["reports/phase2/dft/bsdl_plan.json"],
    "14": ["reports/analog/stage_analog_compliance.json"],
    "15": ["reports/phase2/gates/stage2_compliance.json"],
    "15.5ic": ["reports/phase3/pad_assignment.json",
               "reports/phase3/padring.json"],
    "21": ["reports/phase3/drc_router.json"],
    "23": ["reports/phase3/sta/post_route_signoff_corner.json",
           "reports/phase3/sta/post_route_summary.json",
           "reports/phase3/sta/sta_corner_record_completeness.json"],
    "24": ["reports/phase3/ir_drop_signoff.json"],
    "25": ["reports/phase3/em_current_authority.json",
           "reports/phase3/em_signoff.json"],
    "26": ["reports/phase3/antenna_signoff.json"],
    "26.5ic": ["reports/phase3/die_finishing.json"],
    "28": ["reports/phase2/gates/perc_signoff.json"],
    "29": ["reports/phase2/gates/post_layout_sim.json"],
    # `reports/phase3/perc_sweep.json` is NOT here, and the reason is the point of
    # this file: `perc_corpus_sweep` -- step 31's declared producer -- WRITES it
    # (producer_command), and the gate's `sweep_reach_check --report` only READS
    # it. It is a producer's document that a gate consults, which is exactly the
    # shape R-0915-141 asks for, not a violation of it.
    "31": ["reports/phase2/gates/erc_density.json",
           "reports/phase3/drc_signoff.json",
           "reports/phase3/lvs.json"],
    "37": ["reports/phase3/gates/stage3_compliance.json"],
    "37.5ic": ["reports/phase3/tapeout_precheck.json"],
    "37.5ip": ["reports/phase3/digital_hardmacro.json"],
    "M1": ["reports/analog/mixed_signal/merge.json"],
}


# ── the ruling's two steps ─────────────────────────────────────────────────

def test_step_36_no_longer_declares_its_gates_verdict_target():
    viol = steps_declaring_their_gates_verdict_target(_flow()["steps"])
    assert "36" not in viol, viol.get("36")


def test_step_38_no_longer_declares_its_gates_verdict_target():
    viol = steps_declaring_their_gates_verdict_target(_flow()["steps"])
    assert "38" not in viol, viol.get("38")


def test_the_two_steps_declare_a_real_producer():
    """The other half of the split: the step's `programs:` must name the program
    that ASSEMBLES the document, not the checker that judges it."""
    by_id = {str(s.get("id")): s for s in _flow()["steps"] if isinstance(s, dict)}
    assert by_id["36"].get("programs") == ["tapeout_checklist_gen"], (
        by_id["36"].get("programs"))
    assert by_id["38"].get("programs") == ["foundry_handoff_pack_gen"], (
        by_id["38"].get("programs"))


def test_the_gates_still_run_and_write_their_verdict_somewhere():
    """A split that silenced the gate would be worse than the defect. Both gates
    are still invoked, and each names its own receipt path."""
    by_id = {str(s.get("id")): s for s in _flow()["steps"] if isinstance(s, dict)}
    c36 = " ".join(_clause_commands(by_id["36"].get("gate")))
    assert "tapeout_signoff_check . --mode tapeout --json " in c36, c36[:200]
    assert "reports/audit/tapeout_signoff.json" in c36
    c38 = " ".join(_clause_commands(by_id["38"].get("gate")))
    assert "foundry_handoff_package_check . --json " in c38, c38[:200]
    assert "reports/phase3/foundry_handoff_check.json" in c38


def test_the_producer_runner_drives_the_producers_not_the_gates():
    import phase3_one_shot_runner as R
    table = {n: (prog, out) for n, prog, out, _a in R._PRE_AUDIT_PRODUCERS}
    assert table["tapeout_checklist"][0] == "tapeout_checklist_gen.py", table
    assert table["foundry_handoff"][0] == "foundry_handoff_pack_gen.py", table


def test_the_generator_accepts_the_runners_flag():
    """`_run_declared_signoff_gate` builds `[prog, project, *argv, "--json", out]`
    for every producer it drives, so a producer that only knows `--out` cannot be
    wired without teaching the runner a second spelling per program."""
    import subprocess
    r = subprocess.run([sys.executable, str(PROGRAMS / "tapeout_checklist_gen.py"),
                        "--help"], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-300:]
    assert "--json OUT" in r.stdout, r.stdout[:400]


# ── the class, as a ratchet that may only shrink ───────────────────────────

def test_the_residual_is_exactly_what_was_measured():
    """MEMBERSHIP, not a count. A new step in this class is a new member and is
    named; a member that gains a real producer leaves and must be removed here in
    the same change."""
    viol = steps_declaring_their_gates_verdict_target(_flow()["steps"])
    joined = {k: v for k, v in viol.items() if k not in _RESIDUAL}
    left = {k: v for k, v in _RESIDUAL.items() if k not in viol}
    assert joined == {}, (
        f"{len(joined)} step(s) newly declare their own gate's verdict target, "
        f"which the audit refuses as self-certified evidence BY CONSTRUCTION: "
        f"{joined}\nGive the step a real producer and point the gate's --json at "
        f"its own path — see steps 36 and 38 in this branch for the shape.")
    assert left == {}, (
        f"{len(left)} step(s) left this class and the residual still lists them; "
        f"remove them in the change that fixed them: {left}")
    for sid, paths in viol.items():
        assert sorted(paths) == sorted(_RESIDUAL[sid]), (sid, paths)


def test_a_new_member_of_the_class_is_named():
    """MUTATION: splice a step that declares its own gate's receipt target into a
    COPY of the flow and require the sweep to name it."""
    doc = _flow()
    doc["steps"].append({
        "id": "zzsplit", "name": "synthetic", "stage": "stage4",
        "required_outputs": ["reports/audit/zzsplit.json"],
        "gate": {"all_of": [
            {"program_exit_zero":
                "zzsplit_check . --json reports/audit/zzsplit.json"}]},
        "blocks_on": [],
    })
    viol = steps_declaring_their_gates_verdict_target(doc["steps"])
    assert viol.get("zzsplit") == ["reports/audit/zzsplit.json"], viol.get("zzsplit")


def test_the_class_is_the_predicate_the_audit_uses():
    """TWO CONSUMERS, AND THEY ARE NOT THE SAME SET — which is why this file now
    names which one it means.

    `_gate_json_targets` (`--json`) decides SELF-CERTIFIED EVIDENCE: is this
    declared output the document the gate writes its verdict to? That is the rule
    R-0915-141 is about and the one this ratchet guards.

    `_GATE_RECEIPT_FLAGS` (`--json` AND `--report`) decides RECEIPT REDIRECTION:
    which paths must be moved to scratch so the audit cannot overwrite the run's
    documents. That set is deliberately wider, because a gate that READS its
    target must not have it overwritten either.

    Conflating them put step 31's `perc_sweep.json` -- a producer's document the
    gate only reads -- into a class that demands it be given a producer.
    """
    assert "--json" in FCC._GATE_RECEIPT_FLAGS
    assert "--report" in FCC._GATE_RECEIPT_FLAGS, (
        "the redirect no longer covers --report; a gate that reads its target "
        "through that flag can now have the run's document overwritten")
    # the narrower set really is narrower on the shipped flow: some step names a
    # --report target it declares, and that step must NOT be a member here.
    by_json = steps_declaring_their_gates_verdict_target(_flow()["steps"])
    assert "reports/phase3/perc_sweep.json" not in by_json.get("31", []), (
        "perc_sweep.json is back in the self-certified class; it is written by "
        "step 31's declared producer and only read by its gate")


def test_the_split_makes_the_audit_machinery_unreachable_for_step_36():
    """THE POINT OF THE SPLIT, at the audit's own predicate:
    `_declared_self_written = set(outputs) & _gate_json_targets(step)`. Empty for
    step 36 now, so no criterion of the self-certified refusal can reach it."""
    by_id = {str(s.get("id")): s for s in _flow()["steps"] if isinstance(s, dict)}
    for sid in ("36", "38"):
        step = by_id[sid]
        outputs = set(str(x) for x in (step.get("required_outputs") or []))
        assert not (outputs & FCC._gate_json_targets(step)), sid


# ── the refusal is structurally unreachable, not merely avoided ─────────────

def _refusal_is_reachable(project: Path, step: dict) -> bool:
    """The audit's own computation, through the audit's own helpers.

    `flow_compliance_check` decides a declared output is the auditor's own with
    `_declared_self_written = set(outputs) & _gate_json_targets(step)`, then keeps
    the members that are absent before the gate, or carried by a prior note, or
    hold a gate verdict document. Reproduced here by CALLING those helpers so this
    arm cannot drift from the module: an empty `_declared_self_written` is the
    whole point of the split -- no later condition can put a path back.
    """
    outputs = set(str(x) for x in (step.get("required_outputs") or []))
    self_written = outputs & FCC._gate_json_targets(step)
    if not self_written:
        return False
    own_gate = frozenset(FCC._gate_name(c)
                         for c in FCC._declared_gate_commands(step.get("gate")))
    own_prod = frozenset(str(p).strip() for p in (step.get("programs") or []))
    for rel in self_written:
        f = project / rel
        if not f.exists():
            return True                      # absent before the gate
        if FCC._is_gate_verdict_document(f, own_gate, own_prod):
            return True                      # holds a gate verdict document
    return False


def test_the_old_shape_refuses_an_absent_declared_output(tmp_path):
    """THE DEFECT'S FIRST MECHANISM, and the one run22 actually took: the declared
    path is absent when the audit begins, the step's own gate clause then writes
    it, and the audit records that it was first to write it. In the old shape
    that is unavoidable, because the path the gate writes IS the declared path."""
    rel = "reports/audit/zzold.json"
    step = {"id": "zzold", "programs": ["zzold_check"],
            "required_outputs": [rel],
            "gate": {"all_of": [
                {"program_exit_zero": f"zzold_check . --json {rel}"}]}}
    assert not (tmp_path / rel).exists()
    assert _refusal_is_reachable(tmp_path, step) is True


def test_the_old_shape_refuses_a_gate_verdict_document_on_any_pass(tmp_path):
    """THE SECOND MECHANISM, which is why the refusal does not depend on pass
    ordinal: a document that IDENTIFIES ITSELF as the gate's verdict is the
    auditor's own whoever wrote it, when the emitter is not a declared producer of
    the step.

    NOT the shape of the arm above, and the difference cost me a red here worth
    recording: when the step's `programs:` names the SAME program as the gate
    clause, `_is_gate_verdict_document` credits the document -- the declared
    producer really is its author. So this branch bites only when the emitter is
    a gate program and NOT a declared producer."""
    rel = "reports/audit/zzold2.json"
    doc = tmp_path / rel
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text('{"gate": "zzold2_check", "verdict": "PASS"}\n')
    step = {"id": "zzold2", "programs": ["zzold2_gen"],
            "required_outputs": [rel],
            "gate": {"all_of": [
                {"program_exit_zero": f"zzold2_check . --json {rel}"}]}}
    assert _refusal_is_reachable(tmp_path, step) is True


def test_the_new_shape_cannot_refuse_whoever_wrote_first(tmp_path):
    """THE SPLIT, the same tree: the producer's document at its own path, the
    gate's verdict at the gate's path. `_declared_self_written` is empty, so the
    refusal is unreachable BY CONSTRUCTION -- not avoided by timing."""
    prod = "reports/audit/zznew_checklist.json"
    receipt = "reports/audit/zznew_signoff.json"
    for rel, body in ((prod, '{"verdict": "READY", "items": []}\n'),
                      (receipt, '{"gate": "zznew_check", "verdict": "PASS"}\n')):
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body)
    step = {"id": "zznew", "programs": ["zznew_gen"],
            "required_outputs": [prod],
            "gate": {"all_of": [
                {"program_exit_zero": f"zznew_check . --json {receipt}"}]}}
    assert _refusal_is_reachable(tmp_path, step) is False
    # and it stays unreachable when the GATE's own document is a verdict
    # document written before anything else -- the condition that trapped 36.
    assert _refusal_is_reachable(tmp_path, step) is False


def test_the_live_steps_36_and_38_are_in_the_new_shape(tmp_path):
    """The same predicate over the LIVE flow, on a tree where every declared
    output is absent -- the harshest arm, since absence is the first of the three
    conditions that makes the refusal reachable."""
    by_id = {str(s.get("id")): s for s in _flow()["steps"] if isinstance(s, dict)}
    for sid in ("36", "38"):
        assert _refusal_is_reachable(tmp_path, by_id[sid]) is False, sid


# ── the producer's document says not-ready, and the step fails ─────────────

def test_the_producer_marks_not_ready_in_the_document_itself(tmp_path):
    """A declared output is only worth declaring if it can say no. MEASURED on an
    empty tree: 10 of 10 blocker rows missing, `verdict` BLOCKER_MISSING."""
    import json
    import subprocess
    out = tmp_path / "reports/audit/tapeout_checklist.json"
    r = subprocess.run(
        [sys.executable, str(PROGRAMS / "tapeout_checklist_gen.py"),
         str(tmp_path), "--json", str(out)],
        capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-400:]
    doc = json.loads(out.read_text())
    assert doc["verdict"] == "BLOCKER_MISSING", doc["verdict"]
    assert doc["summary"]["blockers_missing"] == doc["summary"]["blockers_total"]
    assert doc["summary"]["blockers_total"] >= 10, doc["summary"]


def test_a_not_ready_project_still_fails_step_36(tmp_path):
    """THE NEGATIVE DIRECTION OF THE SPLIT, and it is the arm that matters: moving
    the gate's verdict to its own path must not make the step passable.

    MEASURED, and the mechanism is worth stating exactly rather than flattering
    it: step 36's blocking clause exits 1 on this tree because `signoff_audit`
    independently finds the evidence missing -- NOT because it read the
    checklist's own `BLOCKER_MISSING` token. Nothing in step 36's gate reads that
    token (swept across `programs/`: the only mention of the checklist path in
    `signoff_audit.py` is a default evidence NAME, line ~2149). So the document
    the step declares is produced and can say no, but its verdict field is
    currently judged by nobody -- an empty-promise shape (matrix d8) reported with
    this branch, not closed by it.
    """
    import subprocess
    (tmp_path / "reports").mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        [sys.executable, str(PROGRAMS / "tapeout_signoff_check.py"),
         str(tmp_path), "--mode", "tapeout",
         "--json", str(tmp_path / "reports/audit/tapeout_signoff.json")],
        capture_output=True, text=True, timeout=900)
    assert r.returncode == 1, (
        f"a project with 10/10 tapeout blockers missing must not pass step 36's "
        f"blocking clause; rc={r.returncode}\n{r.stdout[-600:]}")
    # and the gate wrote its verdict to ITS OWN path, which is the split.
    assert (tmp_path / "reports/audit/tapeout_signoff.json").exists()
    assert not (tmp_path / "reports/audit/tapeout_checklist.json").exists(), (
        "the gate wrote the producer's declared path; the split is not real")


# ── the two directions of the membership rule ──────────────────────────────

def test_a_producer_written_document_a_gate_only_reads_is_not_a_member():
    """STEP 31's SHAPE, on a synthetic copy: the producer writes X and the gate
    consults X through `--report`. That is the rule working, not breaking, so X
    must not be a member."""
    doc = _flow()
    doc["steps"].append({
        "id": "zzread", "name": "synthetic", "stage": "stage3",
        "programs": ["zzread_gen"],
        "required_outputs": ["reports/phase3/zzread.json"],
        "gate": {"all_of": [
            {"program_exit_zero":
                "zzread_check . --report reports/phase3/zzread.json "
                "--json reports/phase3/zzread_check.json"}]},
        "blocks_on": [],
    })
    viol = steps_declaring_their_gates_verdict_target(doc["steps"])
    assert "zzread" not in viol, viol.get("zzread")


def test_a_gate_written_json_target_is_a_member():
    """THE OTHER DIRECTION on the same synthetic step: move the declared output to
    the gate's own `--json` path and it IS a member."""
    doc = _flow()
    doc["steps"].append({
        "id": "zzwrite", "name": "synthetic", "stage": "stage3",
        "programs": ["zzwrite_check"],
        "required_outputs": ["reports/phase3/zzwrite_check.json"],
        "gate": {"all_of": [
            {"program_exit_zero":
                "zzwrite_check . --report reports/phase3/zzwrite.json "
                "--json reports/phase3/zzwrite_check.json"}]},
        "blocks_on": [],
    })
    viol = steps_declaring_their_gates_verdict_target(doc["steps"])
    assert viol.get("zzwrite") == ["reports/phase3/zzwrite_check.json"], (
        viol.get("zzwrite"))
