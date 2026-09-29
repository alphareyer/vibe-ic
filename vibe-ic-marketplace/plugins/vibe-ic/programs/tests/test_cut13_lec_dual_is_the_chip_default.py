"""CUT_W4 step 13 (R-0929-TOOL-DEFAULT): LEC runs its tool arm by default.

Audit 3.14 (step 13): the EQY arm existed but was opt-in, and it could not
run without `phase3/librelane_switch.json` naming a `pdk`
(LL_SWITCH_INCOMPLETE), so on every real run step 13 was lec_run alone. The
chip path now runs step 13 `dual`: lec_run (arm A) plus Yosys.EQY (arm B),
with the PDK the run resolved. What stays is vibe-ic's gate over the tool's
output, and the combine rule (EQY treats gold-side x as don't-care, so its
PASS never stands alone past a gold-x partition, and a counterexample in
either arm is never outvoted).

Only the EDA tool's file writes are faked: `run_eqy` returns a Yosys.EQY
directory holding a REAL EQY run's status files (programs/calibration).
"""
import importlib
import hashlib
import json
import tarfile
from pathlib import Path

import pytest

import _plugin_tree  # noqa: F401 — puts programs/ on sys.path
import _owner_declared as _OD  # tests/, the one attestation fixture
import librelane_contract as LC

PROGRAMS = Path(LC.__file__).resolve().parent
CAL = PROGRAMS / "calibration"
SHA = "sha256:" + hashlib.sha256((CAL / "cal_eqy_gate.v").read_bytes()).hexdigest()


def put(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(obj if isinstance(obj, str) else json.dumps(obj))
    return path


def _chip(root):
    """A project on the chip path (step 15.5ic's condition), no switch file."""
    st = root / "input" / "submission_template"
    st.mkdir(parents=True, exist_ok=True)
    (st / "SELF_TAPEOUT.txt").write_text("# self tape-out\n")
    (st / "tapeout_declaration.json").write_text(json.dumps(_OD.attest(
        {"schema": "vibe-ic/tapeout_declaration/1",
         "answers": {"deliverable": "DIE"}})))
    return root


def _eqy_folder(root, sample):
    folder = root / "01-yosys-eqy"
    (folder / "scratch").mkdir(parents=True)
    with tarfile.open(CAL / f"{sample}.tar") as tar:
        tar.extractall(folder / "scratch")
    return folder


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Image and PDK root declared by env, so no docker is asked."""
    (tmp_path / "pdkroot").mkdir()
    monkeypatch.setenv("VIBEIC_LIBRELANE_IMAGE", "img:1")
    monkeypatch.setenv("VIBEIC_LIBRELANE_PDK_ROOT", str(tmp_path / "pdkroot"))
    project = _chip(tmp_path / "proj")
    put(project / "phase2/stage1/rtl/cal_chain.v", (CAL / "cal_chain_rtl.v").read_text())
    put(project / "phase2/stage2/synth/cal_chain_synth.v",
        (CAL / "cal_eqy_gate_noneq.v").read_text())
    return project


def _runner():
    return importlib.import_module("design_one_shot_runner")


def _fake_eqy(monkeypatch, sample, seen=None):
    import librelane_eqy as eqy

    def run_eqy(project, image, pdk, top, rtl, netlist, *, mounts=None,
                pdk_root=None, std_cell_library=None, namespace="lec_eqy"):
        if seen is not None:
            seen.append({"pdk": pdk, "scl": std_cell_library, "mounts": mounts})
        return _eqy_folder(project / "phase3/librelane" / namespace, sample)
    monkeypatch.setattr(eqy, "run_eqy", run_eqy)


def _arm_a(runner, verdict="INCONCLUSIVE"):
    status = {"PASS": "PASS", "FAIL": "FAIL"}.get(verdict, "NOT_MEASURED")
    kw = ({} if status != "NOT_MEASURED"
          else {"reason_class": runner._V.ReasonClass.INCONCLUSIVE})
    return runner.StepResult("lec_equivalence", status, 1.0, "arm A", **kw)


GATE_REL = "phase2/stage2/synth/cal_chain_synth.v"


# ── the default ─────────────────────────────────────────────────────────────
def test_the_chip_path_runs_step13_dual_with_no_switch(tmp_path):
    project = _chip(tmp_path)
    assert LC.selected_mode(project, "13") == "dual"
    # a design with no pad ring keeps lec_run alone
    assert LC.selected_mode(tmp_path / "core_only", "13") == "direct"
    # the project's own opt-out still wins
    put(project / "phase3/librelane_switch.json", {"steps": {"13": "direct"}})
    assert LC.selected_mode(project, "13") == "direct"


def test_the_arm_runs_on_the_callers_resolved_pdk_without_a_switch(env, monkeypatch):
    """Main: no switch -> the arm read a missing file and never ran EQY."""
    runner = _runner()
    seen = []
    _fake_eqy(monkeypatch, "eqy_not_equivalent_positive", seen)
    put(env / "reports/lec.json", {"verdict": "INCONCLUSIVE", "proof_identity": {
        "gate_netlist": {"path": GATE_REL, "sha256": SHA}}})
    rows = runner._lec_eqy_arm(env, "cal_chain", GATE_REL, [_arm_a(runner)],
                               pdk="pdkA", std_cell_library="libA")
    assert seen and seen[0]["pdk"] == "pdkA" and seen[0]["scl"] == "libA"
    assert [r.status for r in rows] == ["FAIL"]
    arms = json.loads((env / "reports/lec_arms.json").read_text())
    assert (arms["mode"], arms["selected"]) == ("dual", "eqy")
    assert arms["subjects"]["lec_run"] == SHA
    assert arms["subjects"]["eqy"] == "sha256:" + LC.digest(env / GATE_REL)


def test_no_pdk_anywhere_is_named_not_measured(env, monkeypatch):
    """No caller PDK and no switch: arm B says which two sources were absent."""
    runner = _runner()
    _fake_eqy(monkeypatch, "eqy_defined_init_negative")
    put(env / "reports/lec.json", {"verdict": "PASS"})
    rows = runner._lec_eqy_arm(env, "cal_chain", GATE_REL, [_arm_a(runner, "PASS")])
    eqy_doc = json.loads((env / "reports/lec_eqy.json").read_text())
    assert eqy_doc["verdict"] == "NOT_MEASURED"
    assert "LL_PDK_UNDECLARED" in eqy_doc["explanation"]
    assert "librelane_switch.json" in eqy_doc["explanation"]
    # arm A's proof stands; the absent arm is disclosed, never a PASS of its own
    assert rows[-1].status == "PASS"
    assert "eqy=NOT_MEASURED" in rows[-1].detail


def test_phase3_hands_its_resolved_pdk_to_step13(tmp_path, monkeypatch):
    """The re-proof on step 15's input carries phase 3's PdkConfig."""
    p3 = importlib.import_module("phase3_one_shot_runner")
    d2 = _runner()
    import lec_gate_netlist_select as gns
    monkeypatch.setattr(gns, "proof_subject_binding",
                        lambda *a, **k: {"state": gns.BINDING_STALE})
    got = {}

    def fake(project, top, container, results=None, **kw):
        got.update(kw)
        return []
    monkeypatch.setattr(d2, "step_lec_equivalence", fake)
    pdk = p3.PdkConfig(
        name="pdkA", site="s", drc_deck=None, cell_gds=None, tech_lef="t.lef",
        cell_lef="/r/pdkA/libs.ref/libA/lef/libA.lef",
        liberty="/r/pdkA/libs.ref/libA/lib/libA__tt.lib")
    p3.run_step13_lec_on_pnr_input(tmp_path, "top", "", pdk)
    assert got == {"pdk": "pdkA", "std_cell_library": "libA"}


# ── the gate audits the tool arm ────────────────────────────────────────────
def _gate_tree(root, arms, *, lec=None):
    put(root / GATE_REL, (CAL / "cal_eqy_gate.v").read_text())
    rtl = put(root / "phase2/stage1/rtl/cal_chain.v", (CAL / "cal_chain_rtl.v").read_text())
    script = put(root / "phase3/librelane/lec_eqy/cal_chain.eqy", "[script]\nprep -top cal_chain\n")
    def fingerprint(path):
        return {"path": str(path.relative_to(root)),
                "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()}
    identity = {"top": "cal_chain", "gate_netlist": fingerprint(root / GATE_REL),
                "gold_rtl": [fingerprint(rtl)], "equivalence_script": fingerprint(script)}
    put(root / "reports/lec.json", lec or {
        "verdict": "INCONCLUSIVE", "equivalent": False, "inconclusive": True,
        "compared_points": 60, "unproven_points": 5, "miter_points": 65,
        "non_equivalent_points": None, "non_convergence": True,
        "proof_identity": identity})
    put(root / "reports/lec.rpt", "Found 65 $equiv cells\n")
    if arms is not None:
        arms = dict(arms, subjects=dict(arms["subjects"], eqy_path=str(root / GATE_REL)))
        put(root / "reports/lec_arms.json", arms)
        put(root / "reports/lec_eqy.json", {"verdict": arms["arms"]["eqy"],
            "proof_identity": identity, "compared_points": 2, "proven_points": 2,
            "xbits_partitions": list(arms.get("eqy_xbits_partitions") or [])})
    return root


def _gate(root):
    import lec_equivalence_check as G
    return G.main([str(root)])


def _arms(verdict, selected, eqy, *, eqy_sha=SHA, lec_sha=SHA, xbits=()):
    return {"verdict": verdict, "selected": selected,
            "arms": {"lec_run": "INCONCLUSIVE", "eqy": eqy},
            "subjects": {"lec_run": lec_sha, "eqy": eqy_sha},
            "eqy_xbits_partitions": list(xbits)}


def test_an_eqy_counterexample_fails_the_gate(tmp_path, capsys):
    """Main: the gate read lec.json only, so EQY's counterexample was
    WAIVED-DEFERRED (rc 3) in the audit while the step row said FAIL."""
    root = _gate_tree(tmp_path, dict(_arms("FAIL", "eqy", "FAIL"), reason="NOT_EQUIVALENT"))
    assert _gate(root) == 1
    assert "LEC_TOOL_ARM_NOT_EQUIVALENT" in capsys.readouterr().out


def test_a_bound_eqy_proof_closes_an_undecided_arm_a(tmp_path):
    assert _gate(_gate_tree(tmp_path, _arms("PASS", "eqy", "PASS"))) == 0


@pytest.mark.parametrize("changed", ["netlist", "rtl", "script", "missing_identity",
                                    "added_rtl", "removed_rtl"])
def test_tool_credit_requires_current_proof_inputs(tmp_path, changed):
    """The same captured proof cannot stand for inputs changed after step 13."""
    import lec_equivalence_check as G
    root = _gate_tree(tmp_path, _arms("PASS", "eqy", "PASS"))
    paths = {"netlist": GATE_REL, "rtl": "phase2/stage1/rtl/cal_chain.v",
             "script": "phase3/librelane/lec_eqy/cal_chain.eqy"}
    if changed == "added_rtl":
        put(root / "phase2/stage1/rtl/extra.v", (CAL / "cal_chain_rtl.v").read_text())
    elif changed == "removed_rtl":
        (root / paths["rtl"]).unlink()
    elif changed == "missing_identity":
        doc = json.loads((root / "reports/lec_eqy.json").read_text())
        del doc["proof_identity"]
        put(root / "reports/lec_eqy.json", doc)
    else:
        path = root / paths[changed]
        path.write_text(path.read_text() + "\n// changed after proof\n")
    res = G.audit(root)
    assert (res.passed, _gate(root)) == (False, 1 if changed == "netlist" else 3)
    if changed == "netlist":
        assert [f.rule for f in res.findings] == ["LEC_STALE_PROOF"]


def test_tool_credit_cannot_erase_a_scan_mode_refusal(tmp_path, monkeypatch):
    import lec_equivalence_check as G
    import lec_gate_netlist_select as S
    root = _gate_tree(tmp_path, _arms("PASS", "eqy", "PASS"))
    binding = S.proof_subject_binding(root, json.loads((root / "reports/lec.json").read_text()))
    monkeypatch.setattr(G, "_proof_subject_binding", lambda *a: dict(
        binding, state=S.BINDING_SCAN_UNCONSTRAINED, consumer_scan_inserted=True))
    res = G.audit(root)
    assert (res.passed, _gate(root)) == (False, 1)
    assert [f.rule for f in res.findings] == ["LEC_SCAN_MODE_UNCONSTRAINED"]


def test_tool_credit_cannot_erase_an_unbound_subject(tmp_path):
    import lec_equivalence_check as G
    root = _gate_tree(tmp_path, _arms("PASS", "eqy", "PASS"))
    doc = json.loads((root / "reports/lec.json").read_text())
    del doc["proof_identity"]["gate_netlist"]["sha256"]
    put(root / "reports/lec.json", doc)
    res = G.audit(root)
    assert (res.passed, _gate(root)) == (False, 1)
    assert [f.rule for f in res.findings] == ["LEC_SUBJECT_UNBOUND"]


@pytest.mark.parametrize("mutate_during_run", [False, True])
def test_eqy_proof_identity_is_captured_before_the_tool_runs(tmp_path, monkeypatch,
                                                          mutate_during_run):
    """Replay real status receipts through run_eqy, judge and the step-13 audit."""
    import librelane_eqy as E
    import lec_equivalence_check as G
    root = _gate_tree(tmp_path, _arms("PASS", "eqy", "PASS"))
    gate = root / GATE_REL
    rtl = root / "phase2/stage1/rtl/cal_chain.v"
    def resolve(project, image, raw, output, **kw):
        put(output, {"CELL_LIBS": {"*": ["/pdk/cells.lib"]}, "DEFAULT_CORNER": "tt"})
        return output
    def run(project, image, steps, **kw):
        folder = _eqy_folder(project / "phase3/librelane" / kw["namespace"],
                             "eqy_defined_init_negative")
        if mutate_during_run:
            gate.write_text(gate.read_text() + "\n// changed while EQY was running\n")
        return [folder]
    monkeypatch.setattr(E, "resolve_step_config", resolve)
    monkeypatch.setattr(E, "run_chain", run)
    folder = E.run_eqy(root, "img:1", "pdkA", "cal_chain", [rtl], gate, pdk_root="/pdk")
    E.judge_eqy(folder, root / "reports/lec_eqy.json")
    # Reproduce the existing caller's AFTER-run digest, and a current arm-A
    # identity. Only EQY's own before-run snapshot can detect this difference.
    sha = "sha256:" + hashlib.sha256(gate.read_bytes()).hexdigest()
    lec = json.loads((root / "reports/lec.json").read_text())
    lec["proof_identity"]["gate_netlist"]["sha256"] = sha
    put(root / "reports/lec.json", lec)
    put(root / "reports/lec_arms.json", dict(_arms("PASS", "eqy", "PASS", eqy_sha=sha,
        lec_sha=sha), subjects={"lec_run": sha, "eqy": sha, "eqy_path": str(gate)}))
    res = G.audit(root)
    assert (res.passed, _gate(root)) == ((False, 3) if mutate_during_run else (True, 0))


@pytest.mark.parametrize("arms", [
    _arms("PASS", "eqy", "PASS", xbits=["cal_chain.y"]),          # gold-x vacuous
    _arms("PASS", "eqy", "PASS", eqy_sha="sha256:" + "b" * 64),   # another netlist
    _arms("PASS", "eqy", "PASS", lec_sha="sha256:" + "c" * 64),   # stale record
    None,                                                         # direct mode
], ids=["xbits", "other_subject", "stale", "no_record"])
def test_an_eqy_pass_that_proves_nothing_here_is_not_credited(tmp_path, arms):
    assert _gate(_gate_tree(tmp_path, arms)) == 3


def test_a_stale_counterexample_record_is_not_this_proofs(tmp_path):
    arms = dict(_arms("FAIL", "eqy", "FAIL", lec_sha="sha256:" + "c" * 64))
    assert _gate(_gate_tree(tmp_path, arms)) == 3


# ── arm A never claims a count it did not measure ───────────────────────────
def test_lec_run_reports_no_counterexample_count_it_never_measured():
    import lec_run
    parsed = {"proven": 65, "unproven": 0, "total": 65, "equivalent": True,
              "verdict": "PASS", "sat_model_unsupported_cells": [],
              "unproven_cells": [], "success_line": True, "parse_error": False,
              "verdict_explanation": "proven", "undefined_macro_modules": []}
    rep = lec_run.build_report(parsed, "spm", "n.v", None)
    assert rep["non_equivalent_points"] is None
    full = {"verdict": "PASS", "equivalent": True, "compared_points": 65,
            "unproven_points": 0, "non_equivalent_points": None}
    assert lec_run.pass_cache_eligible(full)
    assert not lec_run.pass_cache_eligible(dict(full, non_equivalent_points=1))


# ── the EQY arm's own script (measured false counterexample) ───────────────
def test_the_eqy_gate_is_flattened_and_purged_before_partitioning():
    """CUT_W4 proof on spm x gf180mcuD (vibeic-eda 0.3.86, EQY v0.69): the
    unflattened gate let EQY cut gold `pr` but drive the gate's `p` from the
    liberty flop's private IQ state, so `spm.p`/`spm.c` read NOT_EQUIVALENT on
    an unreachable state while lec_run proved 65/65. Flatten + purge before
    EQY partitions: the real netlist PASSes, and three one-cell mutants
    (p inverted, xor2->xnor2, nand2->and2) each FAIL (report cut_13)."""
    import librelane_eqy as eqy
    text = eqy.eqy_script("spm", [Path("/r/spm.v")], Path("/n/spm_synth.v"), "/l.lib")
    gate = text.split("[gate]", 1)[1].split("[script]", 1)[0].split()
    assert "flatten" in gate and "-purge" in gate
    i = gate.index("read_verilog")
    assert gate.index("flatten") > i and gate.index("-purge") > gate.index("flatten")


def test_two_bound_proofs_are_recorded_as_corroborated(tmp_path):
    import lec_equivalence_check as G
    root = _gate_tree(tmp_path, dict(_arms("PASS", "lec_run", "PASS"),
                                     arms={"lec_run": "PASS", "eqy": "PASS"}),
                      lec={"verdict": "PASS", "equivalent": True, "compared_points": 65,
                           "unproven_points": 0, "non_equivalent_points": None,
                           "proof_identity": {"top": "cal_chain", "gate_netlist": {"path": GATE_REL,
                                                               "sha256": SHA}}})
    res = G.audit(root)
    assert res.passed and res.summary["tool_arm"]["state"] == "CORROBORATED"
    assert res.summary["subject_binding"]["state"] == "MATCH"
