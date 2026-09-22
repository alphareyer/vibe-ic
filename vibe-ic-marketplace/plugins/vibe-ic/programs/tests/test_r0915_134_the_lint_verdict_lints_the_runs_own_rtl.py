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
RUNNER = PROGRAMS / "design_one_shot_runner.py"
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


def test_the_runner_globs_both_suffixes():
    """SOURCE-level, and this case exists because its absence was MEASURED: a
    mutation arm that reverted the runner's glob to `("*.sv",)` left all ten
    other cases green, because they exercise a local reproduction of the
    decision rather than the runner's own line. A widening nothing pins is a
    widening that can be undone silently."""
    src = RUNNER.read_text()
    i = src.index("THREE DEFECTS IN FIVE LINES")
    j = src.index('w("reports/phase2/lint/rom_init_lint.json"', i)
    block = src[i:j]
    assert 'for pat in ("*.v", "*.sv")' in block, (
        "the lint block must enumerate BOTH suffixes the flow's own step-1 gate "
        "accepts; `*.sv` alone matched zero on run21, whose RTL is spm.v")


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


def test_the_old_phantom_path_is_gone_from_the_producer():
    """SOURCE-level: the constant nothing writes must not be cited anywhere in
    the runner's lint block."""
    src = RUNNER.read_text()
    i = src.index("THREE DEFECTS IN FIVE LINES")
    j = src.index('w("reports/phase2/lint/rom_init_lint.json"', i)
    block = src[i:j]
    assert '"reports/yosys_synth.log"' not in block
    assert "_SYNTH_LOG_REL" in block


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


def test_a_missing_rtl_directory_is_named_in_the_refusal():
    src = RUNNER.read_text()
    i = src.index("THREE DEFECTS IN FIVE LINES")
    j = src.index('w("reports/phase2/lint/rom_init_lint.json"', i)
    block = src[i:j]
    assert "the directory does not exist" in block, (
        "the refusal must distinguish an empty directory from an absent one")


# ── the runner itself makes these decisions ────────────────────────────────

def test_the_runner_refuses_instead_of_passing_unconditionally():
    """Pinned at source because this writer runs only inside a phase-2 run: the
    old block emitted `"verdict": "PASS"` as a literal with no branch at all."""
    src = RUNNER.read_text()
    i = src.index("THREE DEFECTS IN FIVE LINES")
    j = src.index('w("reports/phase2/lint/rom_init_lint.json"', i)
    block = src[i:j]
    assert 'if not _lint_rtl:' in block
    assert '"verdict": "INCOMPLETE"' in block
    assert '"reason_class": "INPUT_ABSENT"' in block
    assert block.count('"verdict": "PASS"') == 1, (
        "exactly one PASS arm, reached only when both inputs are present")
