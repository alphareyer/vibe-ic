"""An in-tree path that is absent is a different question, so disclose it.

`project_outputs_in_tree_check` counted in-tree self-references WITHOUT asking
whether the file was there, which is how run18L's and run19's
`/foss/designs/run20/...spm.sealed.gds` analogue stayed invisible for three runs
(icslot43's page). The obvious next move is to make an absent in-tree reference
BLOCKING. The denominator says no, and this module pins both the denominator and
the decision.

MEASURED over three completed spm runs before deciding:
    run18L  200 in-tree self-references, 11 absent
    run19   200                        , 11 absent
    run20   210                        , 10 absent
and every one of run20's ten is classified -- FIVE are directory-shaped
DESTINATIONS a record names for a review that did not run
(`reports/{analog,phase1,phase2,phase3}/gates/on_pass_review`,
`reports/crosslayer/baseline_rtl`) and FIVE are optional or prospective artefacts
(`RESULT.md`, `phase1/ai_deep_review_patches.json`,
`phase2/stage1/lessons{.md,_ack.json}`, `reports/crosslayer/rewrite_equivalence.json`).
NOT ONE is a declared `required_output` that went missing. Blocking on them would
manufacture ten findings per run and close none.

"A declared artefact is missing" is already owned by the per-step
`required_outputs` resolution -- the machinery that caught step 23's
`sta_spef_based.rpt` and steps 36/38. So this gate DISCLOSES the fact and changes
no verdict.
"""
from __future__ import annotations

import json
import sys
import pytest
import project_outputs_in_tree_check as G


def run_root(tmp_path, *, absent_ref, present_ref=True):
    project = tmp_path / "run"
    (project / "reports/phase3").mkdir(parents=True)
    here = project / "reports/phase3/real_artefact.json"
    if present_ref:
        here.write_text("{}")
    (project / "reports/phase3/record.json").write_text(json.dumps({
        "producer": "fixture_gen",
        "wrote": str(here),
        "will_write": str(project / absent_ref),
    }))
    return project


def gate(project, capsys):
    argv = sys.argv
    sys.argv = ["project_outputs_in_tree_check", str(project)]
    try:
        rc = G.main()
    finally:
        sys.argv = argv
    return rc, capsys.readouterr().out


def test_an_absent_in_tree_reference_is_disclosed_and_does_not_block(tmp_path,
                                                                    capsys):
    project = run_root(tmp_path, absent_ref="reports/phase3/never_written.json")
    rc, out = gate(project, capsys)
    assert rc == 0, out
    assert "in-tree self-reference(s) whose file is not on disk" in out
    assert "never_written.json (not on disk)" in out
    assert "[FAIL]" not in out


def test_a_present_in_tree_reference_is_not_disclosed_as_absent(tmp_path,
                                                               capsys):
    """The control: only the absent ones are listed, so the disclosure is a
    reading and not a restatement of the whole self-reference count."""
    project = run_root(tmp_path, absent_ref="reports/phase3/never_written.json")
    (project / "reports/phase3/never_written.json").write_text("{}")
    rc, out = gate(project, capsys)
    assert rc == 0, out
    assert "whose file is not on disk" not in out


def test_the_disclosure_names_the_file_that_referenced_it(tmp_path, capsys):
    project = run_root(tmp_path, absent_ref="reports/phase3/gone.json")
    _rc, out = gate(project, capsys)
    assert "reports/phase3/record.json" in out


def test_a_directory_shaped_destination_is_disclosed_the_same_way(tmp_path,
                                                                 capsys):
    """Five of run20's ten have no extension at all -- they name a directory a
    review would write into. Disclosed, never blocking."""
    project = run_root(tmp_path, absent_ref="reports/phase3/gates/on_pass_review")
    rc, out = gate(project, capsys)
    assert rc == 0
    assert "on_pass_review (not on disk)" in out


def test_the_blocking_population_is_untouched(tmp_path, capsys):
    """THE LOAD-BEARING CONTROL. An absent in-tree reference must not change the
    verdict, and a real external-storage finding must still block beside it."""
    project = run_root(tmp_path, absent_ref="reports/phase3/never_written.json")
    (project / "reports/phase3/external.json").write_text(json.dumps(
        {"artifact": "/tmp/some-other-place/swept_away.json"}))
    rc, out = gate(project, capsys)
    assert rc == 1, out                      # the external one still blocks
    assert "whose file is not on disk" in out   # and the in-tree one is disclosed
    assert "never_written.json" not in out.split("this is what the gate exits 1 on:")[1]
