#!/usr/bin/env python3
"""Focused controls for persistent P2/P3 canonical-run admission."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


PROGRAMS = Path(__file__).resolve().parent.parent
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import canonical_run_admission as cra  # noqa: E402
import design_input_digest as did  # noqa: E402


def test_standalone_isolated_path_load_resolves_required_siblings(tmp_path):
    """The inventory may load this file outside ``programs/`` and under ``-I``.

    That path must retain the ordinary, fail-loud sibling imports; this is not
    an import fallback or a test-only ``PYTHONPATH`` workaround.
    """
    target = PROGRAMS / "canonical_run_admission.py"
    loader = (
        "import importlib.util, sys; "
        "p = sys.argv[1]; "
        "s = importlib.util.spec_from_file_location('isolated_canonical_admission', p); "
        "m = importlib.util.module_from_spec(s); "
        "sys.modules[s.name] = m; "
        "s.loader.exec_module(m); "
        "assert m.emit_attestation.__file__ and m._eda_pin.__file__"
    )
    got = subprocess.run(
        [sys.executable, "-I", "-c", loader, str(target)],
        cwd=tmp_path, text=True, capture_output=True)
    assert got.returncode == 0, got.stderr


def _project(root: Path) -> Path:
    (root / "input").mkdir(parents=True)
    (root / "input" / "spec.md").write_text("counter specification\n")
    (root / "phase1" / "input_doc").mkdir(parents=True)
    (root / "phase1" / "input_doc" / "brief.md").write_text("brief\n")
    (root / "phase1" / "generated_docs").mkdir(parents=True)
    (root / "phase1" / "generated_docs" / "L1.json").write_text('{"layer": 1}\n')
    (root / "phase2" / "stage1" / "rtl").mkdir(parents=True)
    (root / "phase2" / "stage1" / "rtl" / "core.v").write_text(
        "module core(input a, output y); assign y = a; endmodule\n")
    (root / "phase2" / "stage2" / "constraints").mkdir(parents=True)
    (root / "phase2" / "stage2" / "constraints" / "core.sdc").write_text(
        "create_clock -period 10 [get_ports a]\n")
    return root


def _admit(root: Path, **changes):
    cfg = {"top_name": "chip_top", "util": 0.3}
    cfg.update(changes.pop("config", {}))
    return cra.admit(root, "phase2", container_image=changes.pop(
        "image", "sha256:" + "a" * 64), config=cfg,
        program_paths=changes.pop("programs", [PROGRAMS / "design_one_shot_runner.py"]))


def test_same_identity_is_refused_after_persistent_reload(tmp_path):
    project = _project(tmp_path / "proj")
    first = _admit(project)
    assert first.admitted and first.reason == "ADMITTED"
    # A new Python-level caller has no in-memory state to consult.  The JSONL
    # ledger must still refuse the exact same P2 work.
    second = _admit(project)
    assert not second.admitted
    assert second.reason == "DUPLICATE_NO_NEW_EVIDENCE"


def test_canonical_authored_rtl_change_reopens_phase2_admission(tmp_path):
    project = _project(tmp_path / "proj")
    assert _admit(project).admitted
    assert not _admit(project).admitted
    rtl = project / "phase2" / "stage1" / "rtl" / "core.v"
    rtl.write_text("module core(input a, output y); assign y = ~a; endmodule\n")
    reopened = _admit(project)
    assert reopened.admitted
    assert reopened.reason == "ADMITTED"


def test_substantive_identity_changes_reopen_but_runtime_ledger_does_not(tmp_path):
    project = _project(tmp_path / "proj")
    assert _admit(project).admitted

    # The admission ledger changes only runtime bookkeeping; it is intentionally
    # not part of the design-input digest and cannot buy a fresh run.
    before = did.scan_inputs(project)
    (project / cra.STATE_DIR / "runtime.json").write_text(
        json.dumps({"last_poll": "changed"}) + "\n")
    after = did.scan_inputs(project)
    assert before.stats == after.stats

    # A real source edit changes the source identity and reopens admission.
    (project / "input" / "spec.md").write_text("counter specification v2\n")
    assert _admit(project).admitted

    # The generated L-doc receipt is not an output loophole: changing it is a
    # different Phase-1 contract and must reopen the downstream span.
    (project / "phase1" / "generated_docs" / "L1.json").write_text('{"layer": 1, "v": 2}\n')
    assert _admit(project).admitted

    # A tool-image or dispatch-contract change is also substantive evidence.
    assert _admit(project, image="sha256:" + "b" * 64).admitted
    assert _admit(project, config={"util": 0.4}).admitted


def test_corrupt_ledger_fails_closed(tmp_path):
    project = _project(tmp_path / "proj")
    path = cra.ledger_path(project)
    path.parent.mkdir(parents=True)
    path.write_text("not json\n")
    got = _admit(project)
    assert not got.admitted
    assert got.reason == "ADMISSION_LEDGER_CORRUPT"


def test_unavailable_ledger_lock_fails_closed(tmp_path, monkeypatch):
    project = _project(tmp_path / "proj")
    monkeypatch.setattr(cra.fcntl, "flock", lambda *_: (_ for _ in ()).throw(OSError("busy")))
    got = _admit(project)
    assert not got.admitted
    assert got.reason == "ADMISSION_LEDGER_LOCK_UNAVAILABLE"


def test_delegated_child_only_accepts_exact_parent_identity(tmp_path, monkeypatch):
    project = _project(tmp_path / "proj")
    first = _admit(project)
    assert first.admitted
    monkeypatch.setenv(cra.DELEGATION_ENV, first.identity_sha256)
    delegated = _admit(project)
    assert delegated.admitted
    assert delegated.reason == "DELEGATED_PARENT_ADMISSION"
    monkeypatch.setenv(cra.DELEGATION_ENV, "different-run")
    refused = _admit(project)
    assert not refused.admitted
    assert refused.reason == "DUPLICATE_NO_NEW_EVIDENCE"


def test_phase3_reopens_only_when_its_consumed_phase2_input_moves(tmp_path):
    project = _project(tmp_path / "proj")
    netlist = project / "phase2" / "stage2" / "synth" / "netlist.v"
    netlist.parent.mkdir(parents=True)
    netlist.write_text("module chip_top; endmodule\n")
    kw = {"container_image": "sha256:" + "c" * 64,
          "config": {"top_name": "chip_top", "util": 0.3},
          "program_paths": [PROGRAMS / "phase3_one_shot_runner.py"]}
    assert cra.admit(project, "phase3", **kw).admitted
    assert not cra.admit(project, "phase3", **kw).admitted
    netlist.write_text("module chip_top; wire changed; endmodule\n")
    assert cra.admit(project, "phase3", **kw).admitted


def _load_runner(filename: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, PROGRAMS / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Dataclasses resolve postponed annotations through sys.modules during
    # import, exactly as normal import machinery would.
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_direct_phase_runners_refuse_before_expensive_dispatch(tmp_path, monkeypatch,
                                                                capsys):
    """Integration seam: both bypassable CLIs obey a negative admission."""
    project = _project(tmp_path / "proj")
    refused = cra.Admission(False, "DUPLICATE_NO_NEW_EVIDENCE", "x", {}, "control")

    design = _load_runner("design_one_shot_runner.py", "_test_direct_phase2")
    monkeypatch.setattr(design._runner_lock, "acquire_or_reenter", lambda *_: object())
    monkeypatch.setattr(design._canonical_admission, "admit_span", lambda *a, **k: refused)
    monkeypatch.setattr(sys, "argv", ["design_one_shot_runner.py", str(project)])
    assert design.main() == 2
    assert "canonical Phase-2 admission" in capsys.readouterr().err

    phase3 = _load_runner("phase3_one_shot_runner.py", "_test_direct_phase3")
    monkeypatch.setattr(phase3._runner_lock, "acquire_or_reenter", lambda *_: object())
    monkeypatch.setattr(phase3._canonical_admission, "admit_span", lambda *a, **k: refused)
    monkeypatch.setattr(sys, "argv", ["phase3_one_shot_runner.py", str(project)])
    assert phase3.main() == 2
    assert "canonical Phase-3 admission" in capsys.readouterr().err
