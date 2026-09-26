"""icslot58 — a lint PASS over zero files, citing a log nothing wrote.

THE INPUT, run21's own artefact, and it is step 2's actual FAIL (not the two
PARTIALLY-VACUOUS clauses beside it):

    reports/phase2/lint/rtl_hygiene.json
    {"verdict": "PASS", "source": "yosys_synth (errors-as-fail)",
     "rtl_files": [], "evidence": "reports/yosys_synth.log", ...}

    step 2  FAIL
      EVIDENCE_MISSING (#433): verdict artifact(s) reference evidence that does
      not exist or is empty — a PASS nothing substantiates is not a PASS:
      reports/phase2/lint/rtl_hygiene.json → evidence 'reports/yosys_synth.log'
      missing/empty

THREE DEFECTS IN FIVE LINES OF THE PRODUCER, each measured on that tree:

  1. THE GLOB WAS SYSTEMVERILOG-ONLY. `rtl_dir.glob("*.sv")` matched ZERO,
     because run21's RTL is `phase2/stage1/rtl/spm.v` — plain Verilog. The
     flow's own step-1 gate accepts `*.sv` OR `*.v` (any_of), so the producer's
     notion of "the RTL" was narrower than the flow's. chip-AGNOSTIC: every
     design whose sources are `.v` got an empty list, and nothing about spm
     caused it.
  2. THE EVIDENCE PATH WAS A CONSTANT NOTHING WRITES. `reports/yosys_synth.log`
     exists on no run and is written by no step. The transcript the verdict
     rests on is at `phase2/stage2/synth/yosys.log`, which run21 DOES carry
     (26,736 bytes) and for which a named constant `_SYNTH_LOG_REL` already
     existed.
  3. THE VERDICT WAS AN UNCONDITIONAL `PASS`, emitted merely because a
     `reports/` directory exists. A lint PASS over zero files resting on an
     absent log is worse than a FAIL: it reports the design as linted.

MEASURED, same tree, before and after:
    old glob on run21c (*.sv only)  -> []
    fixed                           -> ["spm.v"], evidence
                                       phase2/stage2/synth/yosys.log
    zero-file arm                   -> INCOMPLETE / INPUT_ABSENT, never PASS

A design with genuinely no RTL is REFUSED here too rather than passed: this
producer reads no declaration that would make an N/A legitimate, and the refusal
names the directory and patterns it scanned, so a reviewer sees "0 file(s) under
phase2/stage1/rtl matching *.v or *.sv" instead of a green lint over nothing.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

#: The constant the producer must cite, read from the runner rather than retyped.
_SYNTH_LOG_REL = "phase2/stage2/synth/yosys.log"


def _decide(project: Path) -> dict:
    """The producer's three-way decision, over a real tree.

    Reproduced here because the writer sits inside a large runner function; the
    SOURCE-level test below pins that the runner itself makes these decisions,
    so this cannot drift into testing a copy of the logic alone.
    """
    import _path_layout as _pl
    rtl_dir = _pl.rtl_dir(project)
    lint = sorted(f.name for pat in ("*.v", "*.sv")
                  for f in (rtl_dir.glob(pat) if rtl_dir.is_dir() else ()))
    log = project / _SYNTH_LOG_REL
    present = log.is_file() and log.stat().st_size > 0
    if not lint:
        return {"verdict": "INCOMPLETE", "reason_class": "INPUT_ABSENT",
                "rtl_files": []}
    if not present:
        return {"verdict": "INCOMPLETE", "reason_class": "INPUT_ABSENT",
                "rtl_files": lint, "evidence": _SYNTH_LOG_REL}
    return {"verdict": "PASS", "rtl_files": lint, "evidence": _SYNTH_LOG_REL}


def _produce(tmp_path: Path, rtl: dict | None, log: str | None) -> dict:
    """THE RUNNER'S OWN WRITER, driven over a real tree: the artefact it wrote.

    `rtl=None` stages no RTL directory at all; `{}` stages an empty one. The
    runner is called exactly as the phase-2 run calls it once `reports/`
    exists, and what is asserted is the `rtl_hygiene.json` it published.

    RE-ANCHORED (T118). The four runner-facing cases below used to slice the
    runner's source from this block's comment to
    `w("reports/phase2/lint/rom_init_lint.json"`, the next write in the old
    writer. v1.24.80 (3c501d609, #2642) removed that canned write -- only a
    real `rom_init_lint` invocation may create its evidence now -- so the slice
    had no end and every case raised `substring not found` while the three
    decisions it pinned were all still there. A slice ends wherever the next
    unrelated edit puts it; the published artefact does not.
    """
    import design_one_shot_runner as runner
    assert runner._SYNTH_LOG_REL == _SYNTH_LOG_REL, (
        "the evidence the producer cites moved; re-read it from the runner")
    (tmp_path / "reports").mkdir(parents=True, exist_ok=True)
    if rtl is not None:
        _stage(tmp_path, rtl, log)
    elif log is not None:
        (tmp_path / _SYNTH_LOG_REL).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / _SYNTH_LOG_REL).write_text(log)
    runner.step_emit_phase2_manifests(tmp_path, [])
    out = tmp_path / "reports/phase2/lint/rtl_hygiene.json"
    assert out.is_file(), "the phase-2 writer published no lint verdict"
    return json.loads(out.read_text())


def _stage(tmp_path: Path, rtl: dict, log: str | None) -> Path:
    (tmp_path / "phase2/stage1/rtl").mkdir(parents=True, exist_ok=True)
    for name, body in rtl.items():
        (tmp_path / "phase2/stage1/rtl" / name).write_text(body)
    if log is not None:
        p = tmp_path / _SYNTH_LOG_REL
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(log)
    return tmp_path


# ── (1) the RTL set is the run's, not one suffix of it ──────────────────────

def test_plain_verilog_is_rtl(tmp_path):
    """THE DEFECT. run21's only source is `spm.v`; the old glob saw nothing."""
    proj = _stage(tmp_path, {"spm.v": "module spm; endmodule\n"}, "yosys log\n")
    got = _decide(proj)
    assert got["rtl_files"] == ["spm.v"], got
    assert got["verdict"] == "PASS"


def test_the_runner_globs_both_suffixes(tmp_path):
    """RUNNER-level, and this case exists because its absence was MEASURED: a
    mutation arm that reverted the runner's glob to `("*.sv",)` left all ten
    other cases green, because they exercise a local reproduction of the
    decision rather than the runner's own line. A widening nothing pins is a
    widening that can be undone silently -- so the runner's writer is run over
    a `.v`-only tree (run21's shape), and over both suffixes together."""
    got = _produce(tmp_path / "v", {"spm.v": "module spm; endmodule\n"},
                   "yosys log\n")
    assert got["rtl_files"] == ["spm.v"], (
        "the lint block must enumerate BOTH suffixes the flow's own step-1 gate "
        "accepts; `*.sv` alone matched zero on run21, whose RTL is spm.v", got)
    assert got["verdict"] == "PASS", got
    both = _produce(tmp_path / "both", {"a.v": "module a; endmodule\n",
                                        "b.sv": "module b; endmodule\n"},
                    "log\n")
    assert both["rtl_files"] == ["a.v", "b.sv"], both


def test_systemverilog_is_still_rtl(tmp_path):
    """The old behaviour is kept, not swapped: widening, not moving."""
    proj = _stage(tmp_path, {"top.sv": "module top; endmodule\n"}, "log\n")
    assert _decide(proj)["rtl_files"] == ["top.sv"]


def test_both_suffixes_are_linted_together(tmp_path):
    proj = _stage(tmp_path, {"a.v": "module a; endmodule\n",
                             "b.sv": "module b; endmodule\n"}, "log\n")
    assert _decide(proj)["rtl_files"] == ["a.v", "b.sv"]


# ── (2) the citation is the evidence the run produced ──────────────────────

def test_the_verdict_cites_the_log_the_run_wrote(tmp_path):
    proj = _stage(tmp_path, {"spm.v": "module spm; endmodule\n"}, "yosys log\n")
    assert _decide(proj)["evidence"] == _SYNTH_LOG_REL


def test_the_old_phantom_path_is_gone_from_the_producer(tmp_path):
    """RUNNER-level: the constant nothing writes is cited by none of the
    runner's lint verdicts, and the ones that cite evidence cite the log the run
    wrote -- over the PASS arm and the absent-log refusal alike."""
    for arm, log in (("pass", "yosys log\n"), ("nolog", None)):
        got = _produce(tmp_path / arm, {"spm.v": "module spm; endmodule\n"}, log)
        assert "reports/yosys_synth.log" not in json.dumps(got), (arm, got)
        assert got["evidence"] == _SYNTH_LOG_REL, (arm, got)


# ── (3) THE TEETH: it refuses rather than passing on nothing ───────────────

def test_zero_rtl_files_is_a_refusal_never_a_pass(tmp_path):
    """The whole point. A lint verdict over zero files states nothing."""
    proj = _stage(tmp_path, {}, "yosys log\n")
    got = _decide(proj)
    assert got["verdict"] == "INCOMPLETE", got
    assert got["reason_class"] == "INPUT_ABSENT"
    assert got["rtl_files"] == []


def test_an_absent_synth_log_is_a_refusal_even_with_rtl(tmp_path):
    """#433's own case: RTL staged, but the transcript the reading rests on is
    not there, so there is nothing to substantiate a PASS."""
    proj = _stage(tmp_path, {"spm.v": "module spm; endmodule\n"}, None)
    got = _decide(proj)
    assert got["verdict"] == "INCOMPLETE" and got["reason_class"] == "INPUT_ABSENT"


def test_an_empty_synth_log_is_not_a_log(tmp_path):
    proj = _stage(tmp_path, {"spm.v": "module spm; endmodule\n"}, "")
    assert _decide(proj)["verdict"] == "INCOMPLETE"


def test_a_missing_rtl_directory_is_named_in_the_refusal(tmp_path):
    absent = _produce(tmp_path / "absent", None, "yosys log\n")
    empty = _produce(tmp_path / "empty", {}, "yosys log\n")
    for got in (absent, empty):
        assert got["verdict"] == "INCOMPLETE", got
        assert "phase2/stage1/rtl" in got["reason"], got
    assert "the directory does not exist" in absent["reason"], (
        "the refusal must distinguish an empty directory from an absent one",
        absent)
    assert "the directory does not exist" not in empty["reason"], empty


# ── the runner itself makes these decisions ────────────────────────────────

def test_the_runner_refuses_instead_of_passing_unconditionally(tmp_path):
    """The old block emitted `"verdict": "PASS"` as a literal with no branch at
    all, merely because `reports/` existed. The runner's writer is driven over
    every arm of the decision; exactly one of them -- RTL AND a non-empty synth
    transcript -- is a PASS, and it agrees with the local `_decide` above."""
    rtl = {"spm.v": "module spm; endmodule\n"}
    arms = {
        "no_rtl":    ({}, "yosys log\n"),
        "no_log":    (rtl, None),
        "empty_log": (rtl, ""),
        "both":      (rtl, "yosys log\n"),
    }
    verdicts = {}
    for name, (files, log) in arms.items():
        got = _produce(tmp_path / name, files, log)
        verdicts[name] = got["verdict"]
        if name != "both":
            assert got["verdict"] == "INCOMPLETE", (name, got)
            assert got["reason_class"] == "INPUT_ABSENT", (name, got)
        local = _decide(_stage(tmp_path / f"{name}_local", files, log))
        assert got["verdict"] == local["verdict"], (name, got, local)
    assert [n for n, v in verdicts.items() if v == "PASS"] == ["both"], (
        "exactly one PASS arm, reached only when both inputs are present",
        verdicts)
