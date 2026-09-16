"""An INDEPENDENT AUDIT copies the run tree, and the copy read as laundering.

THE DEFECT
==========
`PUBLISHING.md` will not publish an evidence cell until its Overall verdict has
been "independently re-derived", and the plugin ships the way to do that:
`flow_compliance_check.py --read-only`, documented "Use it whenever the tree is
EVIDENCE (a published corpus, another agent's run)". It audits a copy —
`mkdtemp()/<project name>` — so every runner report in it records the ORIGINAL
project path while sitting in the new one.

`report_belongs_to_project_check` reads exactly that as #587 laundering.

MEASURED on a converged spm run (2026-09-16), the SAME bytes (`cp -a`), the
absolute path the only variable — the copy arm bind-mounted back onto the
original path for arm B::

    report_belongs_to_project_check <copy at a new path>    rc=1   4 foreign
    report_belongs_to_project_check <same bytes, own path>  rc=0   0 foreign

and through the umbrella::

    flow_compliance_check --strict  <copy at a new path>    Overall: FAIL              rc=1  PASS=29 FAIL=1
    flow_compliance_check --strict  <same bytes, own path>  Overall: PASS_WITH_WAIVERS rc=0  PASS=34 FAIL=0

Step 36's refusal also voided four downstream PASSes (37, 37.4, 37.5ip, 38),
which is the 34 - 29 = 5 that moved. So the documented independent-audit path
turned a converged run red, and would have done so for every auditor.

WHAT THE FIX MAY NOT DO
=======================
It may not make the gate blind to #587, whose measured shape is a MIXTURE — 61
of 303 attributed reports foreign, a report carried in from another run sitting
among the tree's own. So `--relocated-from` excuses a WHOLESALE relocation only:
every foreign report must name the SAME root and it must be the declared one.
The tests below assert both directions, and the mixture cases are the ones that
keep this from being a waiver.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _progress_run as _pr  # noqa: E402

_PROGRAMS = pathlib.Path(__file__).resolve().parents[1]
PROG = _PROGRAMS / "report_belongs_to_project_check.py"


def _load():
    spec = importlib.util.spec_from_file_location(
        "report_belongs_to_project_check_reloc_probe", PROG)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["report_belongs_to_project_check_reloc_probe"] = mod
    spec.loader.exec_module(mod)
    return mod


M = _load()


def _report(project: pathlib.Path, claims: str, verdict: str = "PASS",
            rel: str = "reports/phase3/analog_one_shot.json"):
    p = project / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"project": claims, "verdict": verdict}),
                 encoding="utf-8")
    return p


def _run(project, *args, env=None):
    return _pr.run([sys.executable, str(PROG), str(project), *args],
                   capture_output=True, text=True, env=env)


def _relocated_tree(tmp_path):
    """A faithful copy of a run: every report names the ORIGINAL path."""
    original = tmp_path / "original_run"
    copy = tmp_path / "audit_copy"
    for rel in ("reports/orchestrator/vibe_ic_one_shot.json",
                "reports/orchestrator/phase3_one_shot.json",
                "reports/phase1_one_shot.json"):
        _report(copy, claims=str(original), verdict="PASS_WITH_WAIVERS",
                rel=rel)
    return original, copy


# ── the defect, stated as a test ─────────────────────────────────────────────
def test_an_audit_copy_reads_as_foreign_without_the_declaration(tmp_path):
    """Unchanged behaviour, and the reason the flag is needed at all."""
    original, copy = _relocated_tree(tmp_path)
    findings, stats = M.audit(copy)
    assert stats["foreign"] == 3, stats
    assert stats["foreign_roots"] == 1, stats
    assert _run(copy).returncode == 1


def test_a_declared_wholesale_relocation_is_not_a_finding(tmp_path):
    original, copy = _relocated_tree(tmp_path)
    findings, stats = M.audit(copy, relocated_from=original)
    assert findings == [], findings
    assert stats["foreign"] == 0 and stats["relocated"] == 3, stats

    r = _run(copy, "--relocated-from", str(original))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RELOCATED" in r.stdout, r.stdout


def test_the_relocation_is_disclosed_in_stdout_and_json(tmp_path):
    """A flag whose effect is invisible is a waiver. It has to SAY so."""
    original, copy = _relocated_tree(tmp_path)
    out = tmp_path / "rep.json"
    r = _run(copy, "--relocated-from", str(original), "--json", str(out))
    assert r.returncode == 0
    assert "RELOCATED" in r.stdout and "WHOLESALE" in r.stdout, r.stdout
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["verdict"] == "PASS"
    assert doc["relocated_from"] == str(original)
    assert doc["stats"]["relocated"] == 3, doc["stats"]


# ── the mutation arms: the flag must NOT excuse #587 ─────────────────────────
def test_a_mixture_is_not_excused_even_when_one_root_matches(tmp_path):
    """THE load-bearing case. A relocated tree PLUS one carried-forward report
    from a third project. If the flag excused the matching majority, #587 would
    be laundered behind a real relocation."""
    original, copy = _relocated_tree(tmp_path)
    _report(copy, claims="/somewhere/else/theirs", verdict="PASS",
            rel="reports/phase2/design_one_shot.json")

    findings, stats = M.audit(copy, relocated_from=original)
    assert stats["relocated"] == 0, "a mixture must excuse NOTHING"
    assert stats["foreign"] == 4, stats
    assert stats["foreign_roots"] == 2, stats
    claimed = {f["claims_project"] for f in findings}
    assert "/somewhere/else/theirs" in claimed
    assert str(original) in claimed, (
        "the reports that WOULD have matched must still be named, so the "
        "reader sees the whole population the refusal is about")

    r = _run(copy, "--relocated-from", str(original))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "NOT RELOCATED" in r.stdout, r.stdout


def test_declaring_the_wrong_root_excuses_nothing(tmp_path):
    original, copy = _relocated_tree(tmp_path)
    findings, stats = M.audit(copy, relocated_from=tmp_path / "some_other_run")
    assert stats["relocated"] == 0 and stats["foreign"] == 3, stats
    assert _run(copy, "--relocated-from",
                str(tmp_path / "some_other_run")).returncode == 1


def test_the_original_587_shape_still_fails_with_the_flag(tmp_path):
    """#587 as measured: the tree's own reports name it, one names another."""
    proj = tmp_path / "mine"
    _report(proj, claims=str(proj), verdict="PASS",
            rel="reports/orchestrator/vibe_ic_one_shot.json")
    _report(proj, claims="/another/operator/AI_IC_design/_c2_adc_run/proj",
            verdict="FAIL", rel="reports/phase3/analog_one_shot.json")

    findings, stats = M.audit(
        proj, relocated_from="/another/operator/AI_IC_design/_c2_adc_run/proj")
    assert stats["foreign"] == 1 and stats["relocated"] == 0, stats
    assert _run(proj, "--relocated-from",
                "/another/operator/AI_IC_design/_c2_adc_run/proj"
                ).returncode == 1


# ── the env-var seam the umbrella uses ───────────────────────────────────────
def test_the_env_var_declares_the_relocation(tmp_path):
    original, copy = _relocated_tree(tmp_path)
    env = dict(os.environ, VIBEIC_AUDIT_RELOCATED_FROM=str(original))
    assert _run(copy, env=env).returncode == 0
    env_wrong = dict(os.environ,
                     VIBEIC_AUDIT_RELOCATED_FROM=str(tmp_path / "nope"))
    assert _run(copy, env=env_wrong).returncode == 1


def test_an_explicit_flag_beats_the_env_var(tmp_path):
    original, copy = _relocated_tree(tmp_path)
    env = dict(os.environ, VIBEIC_AUDIT_RELOCATED_FROM=str(tmp_path / "nope"))
    assert _run(copy, "--relocated-from", str(original),
                env=env).returncode == 0


# ── nothing changes when the flag is absent ──────────────────────────────────
def test_without_the_flag_behaviour_is_unchanged(tmp_path):
    proj = tmp_path / "mine"
    _report(proj, claims=str(proj))
    findings, stats = M.audit(proj)
    assert findings == [] and stats["foreign"] == 0 and stats["relocated"] == 0
    assert _run(proj).returncode == 0

    other = tmp_path / "mine2"
    _report(other, claims="/somewhere/else/theirs")
    assert _run(other).returncode == 1


def test_an_empty_tree_is_still_never_a_pass(tmp_path):
    """The zero-denominator rule the checker already had must survive."""
    proj = tmp_path / "empty"
    proj.mkdir(parents=True)
    r = _run(proj, "--relocated-from", str(tmp_path / "original"))
    assert r.returncode == 2, r.stdout + r.stderr
