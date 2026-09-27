#!/usr/bin/env python3
"""FX_STALE_LDOCS — generated L docs are reused only when they are what the
CURRENT phase-1 producer would write.

MEASURED on subservient (8HD-4, 2026-09-28, lane fxtb FX_P2 arm f4): an exact
fresh copy of a tree whose L1 had been written by the pre-fix producer kept
FAILing `l1_pin_table_aliases_typed_check` after the producer was fixed,
because the front door skipped phase 1 whenever 13 L docs existed. Only
deleting `phase1/generated_docs` let the fix through (arm f5). Both L1s said
`plugin_version 1.25.64`: the change was CODE, and a version cannot see it.

The rule now (orchestrator ruling):
  1. generated docs are reused only when their producer identity matches the
     current producer; a mismatch regenerates, with the reason named;
  2. design INPUT is never regenerated or judged stale;
  3. a generated doc whose bytes differ from what phase 1 recorded is refused
     by name, with the two ways out;
  4. no identity at all regenerates ("no producer identity");
  5. a run that skips phase 1 and reads stale docs discloses it, and a PASS
     over them is NOT_MEASURED -- not FAIL, not PASS.

Identities are real: a tiny producer module in a temporary programs dir is
LOADED and RUN under phase 1's `ProducerRecorder` and stamped through
`_step_identity`'s sidecar, and "the producer changed" is an edit to that
module's source. chip-AGNOSTIC.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import _step_identity as SI  # noqa: E402
import _step_recorder as SR  # noqa: E402
import vibe_ic_one_shot_runner as ORCH  # noqa: E402

try:                                   # absent on the unfixed tree
    import _phase1_producer_identity as PID  # noqa: E402
except ImportError:                    # pragma: no cover - main arm
    PID = None

#: asked through getattr so the unfixed tree ANSWERS instead of raising
_REFUSED = getattr(ORCH, "_P1_MODE_REFUSED", "refused_generated_doc_edited")

PRODUCER_V1 = "def produce(x):\n    return x + 1\n"
PRODUCER_V2 = "def produce(x):\n    return x + 2\n"


def _producer(tmp_path: Path, src: str) -> Path:
    progs = tmp_path / "programs"
    progs.mkdir(exist_ok=True)
    (progs / "fake_l_producer.py").write_text(src)
    return progs


def _run_producer(progs: Path):
    """Load and run the fake producer the way phase 1 loads its modules (into
    `sys.modules`), under phase 1's own recorder when this tree has one."""
    name = "fake_l_producer_%d" % id(progs)
    spec = importlib.util.spec_from_file_location(
        name, progs / "fake_l_producer.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    rec = (PID.ProducerRecorder(progs) if PID is not None
           else SR.Recorder(progs))
    with rec:
        mod.produce(1)
    return rec


def _project(tmp_path: Path, *, design_input=True, n_docs=13) -> Path:
    proj = tmp_path / "proj"
    if design_input:
        (proj / "input" / "docs").mkdir(parents=True)
        (proj / "input" / "docs" / "spec.md").write_text("# spec\n")
    gd = proj / "phase1" / "generated_docs"
    gd.mkdir(parents=True)
    for i in range(1, n_docs + 1):
        (gd / f"L{i}_DOC.json").write_text(json.dumps({"doc": i}))
    return proj


def _stamp(proj: Path, rec) -> None:
    """Phase 1's own stamp; on the unfixed tree, the sidecar written by hand
    in the same format (the unfixed front door never reads it)."""
    if PID is not None:
        PID.stamp(proj, rec)
        return
    recording, _ = rec.recorded()
    SI.write_sidecar(proj / "phase1", "phase1",
                     {"code": SI.code_from_recording(recording)[0],
                      "inputs": "x"}, None,
                     {"recording": recording, "outputs": {}})


def _decide(proj: Path, progs: Path, monkeypatch):
    monkeypatch.setattr(ORCH, "PROGRAMS_DIR", progs)
    return ORCH._phase1_decision(proj, force_skip=False)


def _fresh(proj: Path, progs: Path):
    return PID.assess(proj, progs) if PID is not None else {}


# ---------------------------------------------------------------------------
# 1 + 4 — the measured case, and the old project
# ---------------------------------------------------------------------------
def test_docs_written_by_an_older_producer_are_regenerated(tmp_path,
                                                           monkeypatch):
    """THE f4 SHAPE: the docs exist, the producer changed since -> today the
    front door skipped phase 1 and the fix never reached the project."""
    progs = _producer(tmp_path, PRODUCER_V1)
    proj = _project(tmp_path)
    _stamp(proj, _run_producer(progs))
    (progs / "fake_l_producer.py").write_text(PRODUCER_V2)   # the fix lands
    assert _decide(proj, progs, monkeypatch) == (True, "docs")
    f = _fresh(proj, progs)
    assert f["state"] == "REGENERATE" and f["reason"] == "PRODUCER_CHANGED"
    assert f["old"]["code"] != f["new"]["code"]


def test_docs_with_no_producer_identity_are_regenerated(tmp_path,
                                                        monkeypatch):
    progs = _producer(tmp_path, PRODUCER_V1)
    proj = _project(tmp_path)
    assert _decide(proj, progs, monkeypatch) == (True, "docs")
    f = _fresh(proj, progs)
    assert f["reason"] == "NO_PRODUCER_IDENTITY"
    assert "no producer identity" in f["why"]


def test_a_design_input_change_regenerates(tmp_path, monkeypatch):
    progs = _producer(tmp_path, PRODUCER_V1)
    proj = _project(tmp_path)
    _stamp(proj, _run_producer(progs))
    (proj / "input" / "docs" / "spec.md").write_text("# spec, revised\n")
    assert _decide(proj, progs, monkeypatch) == (True, "docs")
    assert _fresh(proj, progs)["reason"] == "DESIGN_INPUT_CHANGED"


# ---------------------------------------------------------------------------
# the control: current docs are still reused (no wall-clock tax)
# ---------------------------------------------------------------------------
def test_docs_the_current_producer_wrote_are_reused(tmp_path, monkeypatch):
    progs = _producer(tmp_path, PRODUCER_V1)
    proj = _project(tmp_path)
    _stamp(proj, _run_producer(progs))
    assert _decide(proj, progs, monkeypatch) == (False, "")


# ---------------------------------------------------------------------------
# 3 — a hand-edited generated doc
# ---------------------------------------------------------------------------
def test_a_hand_edited_generated_doc_is_refused_not_overwritten(tmp_path,
                                                                monkeypatch):
    progs = _producer(tmp_path, PRODUCER_V1)
    proj = _project(tmp_path)
    _stamp(proj, _run_producer(progs))
    doc = proj / "phase1" / "generated_docs" / "L1_DOC.json"
    doc.write_text(json.dumps({"doc": 1, "edited": True}))
    assert _decide(proj, progs, monkeypatch) == (False, _REFUSED)
    f = _fresh(proj, progs)
    assert f["reason"] == "GENERATED_DOC_EDITED" and "L1_DOC.json" in f["why"]
    assert "input/" in f["why"] and "delete the doc" in f["why"]
    assert json.loads(doc.read_text())["edited"] is True   # untouched


def test_a_doc_the_producer_never_wrote_is_refused(tmp_path, monkeypatch):
    progs = _producer(tmp_path, PRODUCER_V1)
    proj = _project(tmp_path)
    _stamp(proj, _run_producer(progs))
    (proj / "phase1" / "generated_docs" / "L99_HANDMADE.json").write_text("{}")
    assert _decide(proj, progs, monkeypatch) == (False, _REFUSED)


def test_deleting_the_edited_doc_is_a_way_out(tmp_path, monkeypatch):
    progs = _producer(tmp_path, PRODUCER_V1)
    proj = _project(tmp_path, n_docs=14)
    _stamp(proj, _run_producer(progs))
    (proj / "phase1" / "generated_docs" / "L1_DOC.json").unlink()
    assert _decide(proj, progs, monkeypatch) == (True, "docs")
    assert _fresh(proj, progs)["reason"] == "GENERATED_DOC_REMOVED"


def test_the_refusal_halts_the_run_by_name():
    """The front door's handling is on the plan row, not only a mode token."""
    src = Path(ORCH.__file__).read_text()
    assert 'plan.append(("phase1", "NOT_MEASURED", 1))' in src
    assert "p1_mode == _P1_MODE_REFUSED" in src


# ---------------------------------------------------------------------------
# 2 — design input is never regenerated or judged stale
# ---------------------------------------------------------------------------
def test_L_docs_with_no_design_input_are_input_and_kept(tmp_path,
                                                        monkeypatch):
    """A project HANDED its L docs has nothing to regenerate them from: they
    are its input, whatever their identity."""
    progs = _producer(tmp_path, PRODUCER_V1)
    proj = _project(tmp_path, design_input=False)
    assert _decide(proj, progs, monkeypatch) == (False, "")


def test_assessing_never_writes_the_design_input(tmp_path, monkeypatch):
    progs = _producer(tmp_path, PRODUCER_V1)
    proj = _project(tmp_path)
    spec = proj / "input" / "docs" / "spec.md"
    before = (spec.read_bytes(), spec.stat().st_mtime_ns)
    _decide(proj, progs, monkeypatch)
    assert (spec.read_bytes(), spec.stat().st_mtime_ns) == before


# ---------------------------------------------------------------------------
# 5 — a phase-1-skipping run that reads stale docs
# ---------------------------------------------------------------------------
def test_a_window_run_over_stale_docs_discloses_and_is_not_green():
    note_fn = getattr(ORCH, "_stale_generated_docs_note", None)
    demote = getattr(ORCH, "_demote_for_stale_generated_docs", None)
    assert note_fn is not None and demote is not None
    stale = {"state": "REGENERATE", "reason": "PRODUCER_CHANGED",
             "why": "code a -> b"}
    note = note_fn(False, True, stale, "--skip-phase1")
    assert note and note.startswith("STALE_GENERATED_DOCS") \
        and "PRODUCER_CHANGED" in note
    reasons = []
    assert demote("PASS", note, reasons) == "NOT_MEASURED" and reasons == [note]
    assert demote("FAIL", note, []) == "FAIL"         # a real FAIL stays red
    # current docs, or a run that DID regenerate: nothing to disclose
    assert note_fn(False, True, {"state": "REUSE"}, "x") is None
    assert note_fn(True, True, stale, "x") is None


# ---------------------------------------------------------------------------
# the producer side: phase 1 stamps what it ran
# ---------------------------------------------------------------------------
def _p1(monkeypatch, body):
    import phase1_one_shot_runner as P1
    monkeypatch.setattr(P1, "_main", body)
    return P1


def test_phase1_stamps_its_identity_when_it_writes_docs(tmp_path,
                                                        monkeypatch):
    proj = _project(tmp_path, n_docs=0)
    gd = proj / "phase1" / "generated_docs"

    def body():
        P1._RUN.update(project=proj, second_track_only=False, extracting=True)
        for i in range(1, 14):
            (gd / f"L{i}_DOC.json").write_text(json.dumps({"doc": i}))
        return 0

    P1 = _p1(monkeypatch, body)
    assert P1.main() == 0
    rec = SI.read_sidecar(proj / "phase1", "phase1")
    assert rec and rec.get("code") and rec.get("recording"), rec
    assert set(rec["outputs"]) == {f"L{i}_DOC.json" for i in range(1, 14)}
    assert PID.assess(proj, PROGRAMS)["state"] == "REUSE"


def test_phase1_that_wrote_nothing_claims_nothing(tmp_path, monkeypatch):
    proj = _project(tmp_path)

    def body():
        P1._RUN.update(project=proj, second_track_only=False)
        return 3                    # refused / locked out: never extracted

    P1 = _p1(monkeypatch, body)
    P1.main()
    assert SI.read_sidecar(proj / "phase1", "phase1") is None


def test_an_extraction_that_rewrites_identical_bytes_still_stamps(
        tmp_path, monkeypatch):
    """A changed producer that happens to write the same bytes is still the
    producer of record; otherwise every later run would regenerate again."""
    proj = _project(tmp_path)                      # 13 docs already present

    def body():
        P1._RUN.update(project=proj, second_track_only=False, extracting=True)
        return 0                                   # extraction ran, same bytes

    P1 = _p1(monkeypatch, body)
    P1.main()
    assert PID.assess(proj, PROGRAMS)["state"] == "REUSE"


def test_a_second_track_pass_that_rewrites_a_doc_refreshes_its_digest(
        tmp_path, monkeypatch):
    """The expert second pass is phase 1 too: a doc it legitimately rewrites
    is not a hand edit, and must not be refused on the next run."""
    proj = _project(tmp_path)
    import _p1_identity_fixture as FIX
    FIX.stamp_current(proj)
    doc = proj / "phase1" / "generated_docs" / "L1_DOC.json"

    def body():
        P1._RUN.update(project=proj, second_track_only=True)
        doc.write_text(json.dumps({"doc": 1, "expert": "answered"}))
        return 0

    P1 = _p1(monkeypatch, body)
    P1.main()
    assert PID.assess(proj, PROGRAMS)["state"] == "REUSE"


# ---------------------------------------------------------------------------
# phase 1's recorder: what it LOADED and LAUNCHED, with no profiler
# ---------------------------------------------------------------------------
def test_the_recorder_sees_a_dynamic_import_and_installs_no_profiler(tmp_path):
    """MEASURED: the per-call profiler made phase 1 ~10x slower (~200 s vs
    ~20 s). This one records sys.modules, so an importlib import is seen."""
    progs = _producer(tmp_path, PRODUCER_V1)
    rec = PID.ProducerRecorder(progs)
    with rec:
        assert sys.getprofile() is None
        spec = importlib.util.spec_from_file_location(
            "dyn_%d" % id(progs), progs / "fake_l_producer.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
    record, why = rec.recorded()
    assert record is not None, why
    assert "launched:fake_l_producer.py" in record


def test_the_recorder_sees_a_launched_plugin_script(tmp_path):
    progs = _producer(tmp_path, PRODUCER_V1)
    (progs / "tool_script.py").write_text("print('ran')\n")
    rec = PID.ProducerRecorder(progs)
    import subprocess
    with rec:
        subprocess.run([sys.executable, str(progs / "tool_script.py")],
                       capture_output=True, timeout=60)
    record, _ = rec.recorded()
    assert "launched:tool_script.py" in record


def test_a_recorder_that_saw_no_plugin_code_records_nothing(tmp_path):
    empty = tmp_path / "empty_root"
    empty.mkdir()
    rec = PID.ProducerRecorder(empty)
    with rec:
        pass
    record, why = rec.recorded()
    assert record is None and "no plugin module" in why
