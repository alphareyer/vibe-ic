"""A gate's `--json` is often its INPUT too, so the redirect must carry the bytes.

R-0915-126, the third correction in this family. v1.23.23 stopped the audit
overwriting a document the run had produced by sending the clause's receipt to a
scratch path; v1.23.26 made every reader follow that redirect. Both were right and
both were incomplete, because for several gates the flag names an INPUT as well as
an output. `pad_ring_check`'s own help says it: the path is "READ as the
producer's claim, then written back with this gate's verdict beside it".

MEASURED on spm run20 -- design PASS, the reader fix already in the tree, the
audit run on a host where docker and the image both exist -- step 15.5ic still
FAILed, and said exactly why:

    no pad-ring report at /tmp/.../gate_receipt_u1o69wed/padring.json
      -- `pad_ring_gen` did not run. An absent report is not a disclosed skip

`pad_ring_gen` HAD run. The auditor had hidden its output from the gate that was
meant to audit it, and the gate reported the absence honestly. None of the
candidate explanations held: the receipt existed, `image_match` was true, docker
was usable, and the recorded-image reader answers on that tree (`pdk_read_where`
names the image, PASS rc=0). The gate never reached the PDK read.

So the scratch path is SEEDED with a copy of the document. The run's own file is
still never written, the verdict is still read from the receipt, and a gate that
reads its target finds the producer's claim where it looked.
"""
from __future__ import annotations

import json
import stat
import pytest
import flow_compliance_check as F

#: A gate shaped like `pad_ring_check`: it REQUIRES its --json target to exist,
#: reads the producer's claim out of it, and writes its verdict back beside it.
READING_GATE = '''#!/usr/bin/env python3
import json, sys
from pathlib import Path
argv = sys.argv[1:]
out = None
for i, tok in enumerate(argv[:-1]):
    if tok in ("--json", "--report"):
        out = Path(argv[i + 1])
if out is None or not out.is_file():
    print("no report at %s - the producer did not run" % out)
    sys.exit(1)
claim = json.loads(out.read_text())
if claim.get("schema") != "fixture/producer":
    print("unrecognised payload: %r" % claim.get("schema"))
    sys.exit(1)
out.write_text(json.dumps({"program": "fixture_reading_gate", "verdict": "PASS",
                           "producer": claim}))
sys.exit(0)
'''

#: A gate whose --json is write-only: it must be unaffected by finding a seeded
#: file where it was about to write one.
WRITING_GATE = '''#!/usr/bin/env python3
import json, sys
from pathlib import Path
argv = sys.argv[1:]
out = None
for i, tok in enumerate(argv[:-1]):
    if tok in ("--json", "--report"):
        out = Path(argv[i + 1])
out.write_text(json.dumps({"program": "fixture_writing_gate", "verdict": "PASS"}))
sys.exit(0)
'''

PRODUCER_DOC = {"schema": "fixture/producer", "program": "fixture_producer",
                "placed": 771}


def stage(tmp_path, monkeypatch, *, gate=READING_GATE, name="fixture_reading_gate",
          target_exists=True):
    programs = tmp_path / "programs"
    programs.mkdir()
    g = programs / f"{name}.py"
    g.write_text(gate)
    g.chmod(g.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(F, "PROGRAMS_DIR", programs)
    project = tmp_path / "proj"
    (project / "reports").mkdir(parents=True)
    rel = "reports/fixture_document.json"
    if target_exists:
        (project / rel).write_text(json.dumps(PRODUCER_DOC))
    return project, rel, name


def test_a_gate_that_READS_its_target_still_finds_the_document(tmp_path,
                                                              monkeypatch):
    """THE RUN20 CASE. Without the seed the gate refuses PADRING_REPORT_ABSENT
    over a producer that ran."""
    project, rel, name = stage(tmp_path, monkeypatch)
    was = (project / rel).read_bytes()
    res = F._check_program_exit_zero(project, f"{name} . --json {rel}")
    assert res.exit_code == 0, res[1]
    assert res.structured_verdict == "PASS", res
    # and the run's own document is STILL never written
    assert (project / rel).read_bytes() == was


def test_the_seed_is_the_document_itself_not_an_empty_file(tmp_path,
                                                          monkeypatch):
    """The gate refuses an unrecognised payload, so a blank seed would fail it --
    this pins that the bytes carried are the producer's."""
    project, rel, name = stage(tmp_path, monkeypatch)
    seen = {}
    original = F._receipt_off_a_produced_document

    def spy(argv, prj):
        moved, note, keep = original(argv, prj)
        idx = moved.index("--json") + 1
        import pathlib
        seen["seeded"] = json.loads(pathlib.Path(moved[idx]).read_text())
        return moved, note, keep

    monkeypatch.setattr(F, "_receipt_off_a_produced_document", spy)
    F._check_program_exit_zero(project, f"{name} . --json {rel}")
    assert seen["seeded"] == PRODUCER_DOC


def test_a_write_only_gate_is_unaffected_by_the_seed(tmp_path, monkeypatch):
    project, rel, name = stage(tmp_path, monkeypatch, gate=WRITING_GATE,
                               name="fixture_writing_gate")
    was = (project / rel).read_bytes()
    res = F._check_program_exit_zero(project, f"{name} . --json {rel}")
    assert res.exit_code == 0 and res.structured_verdict == "PASS"
    assert (project / rel).read_bytes() == was


def test_the_verdict_still_comes_from_the_receipt_not_the_document(tmp_path,
                                                                  monkeypatch):
    """v1.23.26's property, preserved: the receipt the gate wrote into the seeded
    copy is what the verdict is read from, never the run's document -- which here
    carries no verdict at all."""
    project, rel, name = stage(tmp_path, monkeypatch)
    res = F._check_program_exit_zero(project, f"{name} . --json {rel}")
    assert res.structured_verdict == "PASS"
    assert "verdict" not in json.loads((project / rel).read_text())


def test_with_no_document_to_carry_nothing_is_redirected(tmp_path, monkeypatch):
    """The control: no target, no redirect, and the gate is judged on the absence
    exactly as before -- the honest answer to a producer that really did not run."""
    project, rel, name = stage(tmp_path, monkeypatch, target_exists=False)
    argv = F._resolve_program_cmd(f"{name} . --json {rel}", cwd=project)
    moved, note, _keep = F._receipt_off_a_produced_document(argv, project)
    assert note is None and moved == argv


def test_an_uncopyable_document_leaves_the_gate_the_scratch_path(tmp_path,
                                                                monkeypatch):
    """Fail-safe direction. If the bytes cannot be carried, the gate still gets
    the SCRATCH path -- never the real one, which would put the overwrite back."""
    project, rel, name = stage(tmp_path, monkeypatch)
    import shutil as real_shutil

    def boom(src, dst):
        raise OSError("cannot copy")

    monkeypatch.setattr(F.shutil, "copy2", boom)
    argv = F._resolve_program_cmd(f"{name} . --json {rel}", cwd=project)
    moved, note, keep = F._receipt_off_a_produced_document(argv, project)
    assert note and "RECEIPT REDIRECTED" in note
    assert moved[moved.index("--json") + 1] != str(project / rel)
    del keep, real_shutil
