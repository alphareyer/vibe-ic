"""Preserved evidence named through a second mount is not lost evidence.

`project_outputs_in_tree_check` exists to catch one harm: the flow wrote its
outputs somewhere other than the project tree, so the evidence is gone. A
relocated-copy reference is therefore only a finding when the artefact is
actually missing -- and the same tree is routinely named two ways, because a run
bind-mounted into the EDA container is both `/home/.../run20/phase3/...` on the
host and `/foss/designs/run20/phase3/...` inside the image. A recorded command
line naturally carries the container spelling.

MEASURED on spm run20, `reports/phase3/die_finishing.json`, whose recorded
KLayout `argv` carries both of these -- while the SAME record's `gds_in` gives
the host spelling of the first:

    /foss/designs/run20/phase3/stage3/pnr/spm.gds         IS in the tree
    /foss/designs/run20/phase3/stage3/pnr/spm.sealed.gds  is NOT

Both were reported as "an ephemeral location this run used and did not
preserve". For the first that is false. run18L and run19 recorded the HOST
spelling of the identical argv and were counted as in-tree self-references, so
this never showed: the reference set changed because a PRODUCER changed, not
because anything regressed.

`spm.sealed.gds` keeps its finding, and must: that output was never written, in
any of the three runs. The point of this module is that the two are told apart.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import project_outputs_in_tree_check as G


def run_root(tmp_path, *, keep=("phase3/stage3/pnr/spm.gds",)):
    project = tmp_path / "run20"
    (project / "reports/phase3").mkdir(parents=True)
    for rel in keep:
        target = project / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("GDS")
    return project


#: The container spelling of the same tree: another mount, same run directory name.
def other_mount(rel, project):
    return f"/foss/designs/{project.name}/{rel}"


def test_an_artefact_present_in_the_run_root_is_preserved(tmp_path):
    project = run_root(tmp_path)
    kept = G.preserved_in_the_run_root(
        other_mount("phase3/stage3/pnr/spm.gds", project), project)
    assert kept == Path("phase3/stage3/pnr/spm.gds")


def test_an_artefact_absent_from_the_run_root_keeps_its_finding(tmp_path):
    """THE FALSIFIER. The seal-ring output was never written; saying the
    reference is fine because it names this run would hide that."""
    project = run_root(tmp_path)
    assert G.preserved_in_the_run_root(
        other_mount("phase3/stage3/pnr/spm.sealed.gds", project), project) is None


def test_a_path_that_does_not_name_this_run_is_not_this_class(tmp_path):
    project = run_root(tmp_path)
    assert G.preserved_in_the_run_root(
        "/tmp/lane.x/gate_receipt_abc/padring.json", project) is None
    # and the gate's own relocated-copy predicate agrees it is a different class
    assert not G.names_a_relocated_copy(
        "/tmp/lane.x/gate_receipt_abc/padring.json", project)


def test_the_tail_is_taken_after_the_LAST_occurrence_of_the_run_name(tmp_path):
    """A copy staged under a directory that repeats the run's name is still this
    run, and the run-relative path is what follows the final one."""
    project = run_root(tmp_path)
    nested = f"/staging/{project.name}/{project.name}/phase3/stage3/pnr/spm.gds"
    assert G.preserved_in_the_run_root(nested, project) == Path(
        "phase3/stage3/pnr/spm.gds")


def test_a_reference_to_the_mount_root_itself_resolves_to_nothing(tmp_path):
    project = run_root(tmp_path)
    assert G.preserved_in_the_run_root(f"/foss/designs/{project.name}",
                                       project) is None


@pytest.mark.parametrize("rel,blocking", [
    ("phase3/stage3/pnr/spm.gds", False),
    ("phase3/stage3/pnr/spm.sealed.gds", True),
])
def test_the_gate_blocks_on_the_missing_one_only(tmp_path, capsys, rel,
                                                 blocking):
    """End to end through the gate: one declaration file carrying one container
    spelling, and the exit code follows whether the artefact is really there."""
    project = run_root(tmp_path)
    (project / "reports/phase3/fixture_record.json").write_text(json.dumps(
        {"producer": "fixture_gen",
         "argv": ["tool", "--output", other_mount(rel, project)]}))
    import sys
    argv = sys.argv
    sys.argv = ["project_outputs_in_tree_check", str(project)]
    try:
        rc = G.main()
    finally:
        sys.argv = argv
    out = capsys.readouterr().out
    if blocking:
        assert rc == 1, out
        assert "spm.sealed.gds" in out
    else:
        assert rc == 0, out
        assert "another mount of the same tree" in out
        assert "present as phase3/stage3/pnr/spm.gds" in out
