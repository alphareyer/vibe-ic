"""Step14 writer publication through real CLIs and declared-producer consumers.

Neutral recipe/netlist and producer-shaped LibreLane metadata exercise source
contracts. No synthesis or EDA tool runs here; three completed reports do not
certify the full Step14 implementation or an IC flow.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import _gate_authorship as ga
import flow_declared_producer_run as consumer
import synth_handoff_netlist_check as handoff

WRITERS = ("yosys_hilomap_required_check", "yosys_script_template_check",
           "yosys_tiecell_recipe_order_check")
GOOD = ("read_verilog -sv top.v\nsynth -top core -flatten\ntechmap\n"
        "setundef -zero\nhilomap -hicell TIE_H Y -locell TIE_L Y\n"
        "clean\nwrite_verilog netlist.v\n")
NETLIST = "module core(input a, output y);\nBUF_X1 u_buf (.A(a), .Y(y));\nendmodule\n"


def put(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) + "\n")
    return path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def project_fixture(tmp_path, mode="native", *, recipe=GOOD):
    """Current digest-bound transport fixture, explicitly not synthesis evidence."""
    project = (tmp_path / "project").resolve()
    rtl = project / "phase2/stage1/rtl/core.v"
    rtl.parent.mkdir(parents=True)
    rtl.write_text("module core(input a, output y);\nassign y = a;\nendmodule\n")
    mapped = project / "phase2/stage2/synth/core_synth.v"
    mapped.parent.mkdir(parents=True)
    mapped.write_text(NETLIST)
    (mapped.parent / "netlist.v").write_text(NETLIST)
    if mode in ("script", "project"):
        ys = project / "scripts/synth.ys"
        ys.parent.mkdir()
        ys.write_text(recipe)
        return project, ys
    if mode == "inline":
        inline = recipe.replace("techmap\n", "abc -liberty neutral.lib\ntechmap\n")
        inline = inline.replace("write_verilog netlist.v", "write_verilog core_synth.v")
        (mapped.parent / "synth.log").write_text(
            "-- Running command `" + "; ".join(inline.splitlines()) + "' --\n")
        return project, None
    put(project / "input/project.json", {"pdk": "neutral"})
    put(project / "phase1/generated_docs/L9_INTEGRATION_SPEC.json", {"top_module": "core"})
    folder = project / "phase3/librelane/02-yosys-synthesis"
    folder.mkdir(parents=True)
    raw = folder / "core.nl.v"
    raw.write_text(NETLIST)
    (folder / "COMMANDS").write_text("yosys -p '" + "; ".join(recipe.splitlines()) + "'\n")
    prior = put(project / "phase3/librelane/01-yosys-jsonheader/core.h.json", {"module": "core"})
    cfg = {"meta": {"step": "Yosys.Synthesis"}, "PDK": "neutral",
           "DESIGN_NAME": "core", "VERILOG_FILES": [str(rtl)],
           "SYNTH_TIEHI_CELL": "TIE_H/Y", "SYNTH_TIELO_CELL": "TIE_L/Y"}
    resolved = put(project / "phase3/librelane/synthesis_resolved.json", cfg)
    put(folder / "config.json", cfg)
    put(folder / "state_out.json", {"nl": str(raw), "metrics": {
        "design__instance_unmapped__count": 0, "synthesis__check_error__count": 0}})
    put(folder / "reports/stat.json", {"modules": {"\\core": {"num_submodules": 0}}})
    put(folder / "pdk_root.json", {"cli_pdk_root": "/pdk",
        "stated_by": "run_chain(pdk_root=...)", "mounts_under_it": []})
    inp = {"step": "Yosys.Synthesis", "config": digest(resolved),
           "config_files": {str(rtl): digest(rtl)}, "state_files": {str(prior): digest(prior)}}
    put(folder / "input_fingerprint.json", inp)
    put(folder / "vibeic_receipt.json", {"input": inp, "sha256": {
        str(p.relative_to(folder)): digest(p) for p in folder.rglob("*") if p.is_file()}})
    put(mapped.parent / "synth_inputs.json", {"netlist": mapped.name})
    handoff.publish_handoff(project, folder, mapped, "core")
    assert handoff.bound_handoff(project)["verdict"] == "PASS"
    return project, None


def actual_cli(project, writer, target, role, *, ys=None):
    env = dict(os.environ)
    env.pop(ga.ROLE_ENV, None)
    if role == ga.ROLE_AUDIT:
        env.update(ga.child_env_additions("NEUTRAL_AUDIT_INVOCATION", "neutral-audit"))
    arguments = ["--ys-file", str(ys)] if ys is not None else [str(project)]
    cp = subprocess.run([sys.executable, str(PROGRAMS / (writer + ".py")),
                         *arguments, "--json", str(target)],
                        env=env, capture_output=True, text=True, cwd=project)
    assert target.is_file(), cp.stdout + cp.stderr
    return cp, json.loads(target.read_text())


def publication_identity(target, doc, writer, role, *, delegated=False):
    assert doc.get("invoked_as") == role, doc
    assert doc.get("writer_program") == writer, doc
    assert doc.get("program") == ("synth_handoff_netlist_check" if delegated else writer), doc
    assert consumer._document_role(target) == role
    assert consumer._producer_target_has_content(target) == (role == ga.ROLE_PRODUCER)


@pytest.mark.parametrize("mode", ["script", "project", "inline", "native"])
@pytest.mark.parametrize("writer", WRITERS)
@pytest.mark.parametrize("role", [ga.ROLE_PRODUCER, ga.ROLE_AUDIT])
def test_actual_cli_publication_honors_caller_role(tmp_path, mode, writer, role):
    project, ys = project_fixture(tmp_path, mode)
    delegated = mode == "native" and writer != WRITERS[2]
    expected = handoff.bound_handoff(project) if delegated else None
    target = project / "report.json"
    cp, doc = actual_cli(project, writer, target, role, ys=ys if mode == "script" else None)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert doc["verdict"] in ("PASS", "CLEAN", "VACUOUS_PASS")
    if expected is not None:
        # The delegate's program identity and every substantive field survive.
        assert {k: v for k, v in doc.items() if k not in ("invoked_as", "writer_program")} == expected
    publication_identity(target, doc, writer, role, delegated=delegated)
    result = {"rc": cp.returncode, "executed": True, "target_exists_after": True,
              "target_content_valid": consumer._producer_target_has_content(target),
              "authorship": consumer._document_role(target)}
    assert consumer._result_is_producer_complete(result) == (role == ga.ROLE_PRODUCER)


@pytest.mark.parametrize("writer", WRITERS)
def test_missing_input_keeps_nonzero_and_no_completion(tmp_path, writer):
    project = tmp_path / "project"
    project.mkdir()
    target = project / "missing.json"
    cp, doc = actual_cli(project, writer, target, ga.ROLE_PRODUCER, ys=project / "absent.ys")
    assert cp.returncode == 2, cp.stdout + cp.stderr
    assert doc["verdict"] == ("NOT_CHECKED" if writer == WRITERS[2] else "FAIL")
    publication_identity(target, doc, writer, ga.ROLE_PRODUCER)
    result = {"rc": cp.returncode, "executed": True, "target_exists_after": True,
              "target_content_valid": True, "authorship": doc["invoked_as"]}
    assert not consumer._result_is_producer_complete(result)


@pytest.mark.parametrize("writer", WRITERS)
def test_real_recipe_finding_is_not_rewritten_by_publication(tmp_path, writer):
    bad = ("read_verilog top.v\nsynth -top core\n"
           "hilomap -hicell TIE_H Y -locell TIE_L Y\nopt_clean\nwrite_verilog netlist.v\n")
    project, ys = project_fixture(tmp_path, "script", recipe=bad)
    cp, doc = actual_cli(project, writer, project / "finding.json", ga.ROLE_PRODUCER, ys=ys)
    assert cp.returncode == 1, cp.stdout + cp.stderr
    assert doc["verdict"] == ("VIOLATION" if writer == WRITERS[2] else "FAIL")
    assert doc.get("violations") or doc.get("messages")
    publication_identity(project / "finding.json", doc, writer, ga.ROLE_PRODUCER)
    assert not consumer._result_is_producer_complete({"rc": 1, "executed": True,
        "target_exists_after": True, "target_content_valid": True, "authorship": ga.ROLE_PRODUCER})


def clauses():
    selected = [c for c in consumer.declared_producer_clauses()
                if c["step"] == "14" and c["program"] in WRITERS]
    assert len(selected) == 3 and {c["program"] for c in selected} == set(WRITERS)
    return selected


@pytest.mark.parametrize("role", [ga.ROLE_PRODUCER, ga.ROLE_AUDIT])
def test_actual_declared_consumer_counts_only_three_producer_owned_reports(tmp_path, monkeypatch, role):
    project, _ = project_fixture(tmp_path)
    monkeypatch.setenv(ga.ROLE_ENV, role) if role == ga.ROLE_AUDIT else monkeypatch.delenv(ga.ROLE_ENV, raising=False)
    record = consumer._run_clauses(project, clauses(), timeout=30, prior_targets={})
    assert record["executed"] == 3 and record["owed"] == 3
    assert record["unexecutable"] == []
    assert all(row["rc"] == 0 for row in record["results"])
    assert all(row["authorship"] == role for row in record["results"])
    assert record["documents_now_present"] == (3 if role == ga.ROLE_PRODUCER else 0)
    assert record["verdict"] == ("PRODUCED" if role == ga.ROLE_PRODUCER else "INCOMPLETE")
    # Existing explicit current-step request, only three report clauses:
    # no fabricated sibling report and no full Step14/flow conclusion.
    assert record["declared_clauses"] == 3


def test_native_advisory_finding_stays_visible_while_report_completes(tmp_path, monkeypatch):
    project, _ = project_fixture(tmp_path, recipe=GOOD.replace("setundef -zero\n", ""))
    monkeypatch.delenv(ga.ROLE_ENV, raising=False)
    record = consumer._run_clauses(project, clauses(), timeout=30, prior_targets={})
    tie = next(row for row in record["results"] if row["program"] == WRITERS[2])
    assert tie["rc"] == 1 and tie["enforcement"] == "advisory"
    doc = json.loads((project / tie["target"]).read_text())
    assert doc["verdict"] == "VIOLATION" and doc["violations"]
    assert record["documents_now_present"] == 3 and record["verdict"] == "PRODUCED"


def test_invalid_current_handoff_is_still_a_blocking_finding(tmp_path, monkeypatch):
    project, _ = project_fixture(tmp_path)
    raw = project / "phase3/librelane/02-yosys-synthesis/core.nl.v"
    raw.write_text(raw.read_text() + "// changed producer output\n")
    monkeypatch.delenv(ga.ROLE_ENV, raising=False)
    record = consumer._run_clauses(project, clauses(), timeout=30, prior_targets={})
    assert record["verdict"] == "INCOMPLETE" and record["documents_now_present"] == 0
    for row in record["results"]:
        doc = json.loads((project / row["target"]).read_text())
        assert row["authorship"] == ga.ROLE_PRODUCER
        assert row["target_content_valid"] is True
        assert not consumer._result_is_producer_complete(row)
        assert row["rc"] == (2 if row["program"] == WRITERS[2] else 1)
        if row["program"] != WRITERS[2]:
            assert doc["verdict"] == "FAIL" and doc["reason_class"] == "tool_handoff_invalid"
            assert doc["program"] == "synth_handoff_netlist_check"
