#!/usr/bin/env python3
"""Tests for gate_evidence_completeness_check.py

THE THREE BRANCHES ARE TESTED TOGETHER ON PURPOSE. The change this file was
rewritten for moves ONE of them (absent report: rc 1 -> rc 2) and the value of
that change depends entirely on the other two NOT moving with it. A test file
that only pinned the branch under edit would pass just as happily against a
program that had stopped refusing altogether, which is the failure this gate
exists to prevent in other programs.

    absent report     rc 2  NOT CHECKED   -- changed here
    read, 0 PASS      rc 0  real result   -- control, must not move
    read, PASS w/o    rc 1  real gap      -- control, must not move: this is
      evidence                               the refusal the gate is FOR
"""
from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path

PROG = Path(__file__).resolve().parent.parent / "gate_evidence_completeness_check.py"


def _run(args: list, **kw) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(PROG)] + args,
                          capture_output=True, text=True, **kw)


def test_help():
    r = _run(["--help"])
    assert r.returncode == 0


def test_absent_report_is_not_checked_not_a_failure(tmp_path):
    """An empty project has no report, so no question was ever put to it.

    This asserted `returncode == 1` until 2026-08-31 and so pinned the defect:
    the program printed `FAIL: nothing to audit` over a design it had not read
    one byte of, and the flow's advisory slot recorded that as a FINDING. rc 2
    is this repo's NOT-CHECKED tier and the program's own docstring already
    assigned the two neighbouring I/O conditions to it.
    """
    r = _run([str(tmp_path)])
    assert r.returncode == 2, r.stdout + r.stderr
    combined = r.stdout + r.stderr
    assert "VACUOUS_PASS:" in combined, (
        "the sentinel `flow_compliance_check._stdout_signals_vacuous` matches "
        "must be present, or the advisory slot records this as a FINDING "
        "rather than as `n/a (input not present)`")
    assert "FAIL" not in r.stdout, (
        "a run that read nothing must not print FAIL: it has no design to "
        "return a verdict about")


def test_report_read_with_no_pass_claims_is_a_real_result(tmp_path):
    """CONTROL. An empty artefact is not a missing one.

    The report EXISTS and was parsed; it simply claims no PASS gate. That is a
    real result over a real artefact and stays rc 0 — the line
    `gate_zero_denominator_refuses_check` draws in its own words. If this moved
    with the change above, the change would have swept up the wrong branch.
    """
    (tmp_path / "FINAL_REPORT.md").write_text(
        "# Report\n\nNo gate reached a verdict in this run.\n", encoding="utf-8")
    r = _run([str(tmp_path)])
    assert r.returncode == 0, r.stdout + r.stderr


def test_pass_claim_without_evidence_still_refuses(tmp_path):
    """CONTROL, and the load-bearing one.

    A report that CLAIMS a PASS with no backing artefact is the exact defect
    this program exists to catch. It must still return 1 after the change. A
    fix that silenced the absent-report branch by weakening the gate would show
    up here and nowhere else.
    """
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "flow_compliance.json").write_text(json.dumps({
        "steps": [
            {"id": "1", "name": "a step that claims a pass",
             "gate": "no_such_evidence_gate", "status": "PASS"},
        ]
    }), encoding="utf-8")
    r = _run([str(tmp_path)])
    assert r.returncode == 1, (
        "a PASS claimed with no evidence file must still be a GAP: " +
        r.stdout + r.stderr)


# ---------------------------------------------------------------------------
# A LEDGER `cmd` IS ARGV, AND IS READ AS ARGV — vibe-ic, lane mainred2.
#
# `declared_json_outputs` used to recover the declared artefact with
# `re.compile(r"--json[=\s]+(\S+)").search(cmd)`. Two consequences, and the
# tests below are one for each, so restoring the grep reddens this file:
#
#   * FUNCTIONAL. `--json` is a flag: it is a whole argv token or it is
#     nothing. An unanchored substring search cannot tell the flag from the
#     same eight characters inside a QUOTED argument, and the gate then demands
#     a file for an artefact the gate never declared — a false FAIL against a
#     PASS gate that did nothing wrong.
#   * STRUCTURAL. `prose_polarity_consulted_check --ratchet` flags any
#     `re.search` whose matched group is written into a record as a
#     polarity-blind prose extractor, and it was RIGHT to: that is the
#     instrument for reading a sentence, pointed at a command line. Parsing
#     argv with `shlex` — the same split `flow_compliance_check.
#     _resolve_program_cmd` uses before it RUNS the string — is reading the
#     field in its own grammar, so the finding closes with no exemption and no
#     register entry.
# ---------------------------------------------------------------------------
import importlib.util as _ilu

_spec = _ilu.spec_from_file_location("_gecc_under_test", PROG)
_GECC = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_GECC)


def test_the_declared_artefact_is_an_argv_token_not_a_substring():
    """Both spellings the flow writes are read, and nothing else is."""
    f = _GECC._declared_json_argument
    assert f("check.py /p --json reports/x.json") == "reports/x.json"
    assert f("check.py /p --json=reports/x.json") == "reports/x.json"
    assert f('check.py /p --json "a b.json"') == "a b.json"
    # no declaration at all
    assert f("check.py /p") is None
    assert f("") is None
    # `--jsonl` is a DIFFERENT flag; a prefix is not a token
    assert f("check.py /p --jsonl reports/x.json") is None
    # a flag with no argument declares nothing rather than swallowing the next
    assert f("check.py /p --json") is None
    assert f("check.py /p --json --strict") is None


def test_json_inside_a_quoted_argument_is_not_a_declaration():
    """THE REGRESSION, in the shape that made the substring search wrong.

    The command declares NO artefact — the eight characters live inside one
    quoted argument. The old grep answered `out.json`; the gate would then
    require `out.json` on disk and report a gap against a gate that declared
    nothing. Read as argv there is no `--json` token at all.
    """
    cmd = 'runner.py /p --note "pass --json out.json to the next stage"'
    assert _GECC._declared_json_argument(cmd) is None


def test_an_unrunnable_command_still_yields_tokens_never_a_substring():
    """An unbalanced quote makes `shlex` refuse. Such a string was never a
    runnable command either, so the fallback is whitespace TOKENS — which is
    still a token grammar, and still cannot match inside another argument."""
    f = _GECC._declared_json_argument
    assert f('check.py --json out.json --note "unbalanced') == "out.json"
    assert f('check.py --note "unbalanced --json out.json') == "out.json", (
        "whitespace tokens cannot see the quoting, so this one IS reported — "
        "recorded honestly: the fallback is a token split, not a parser")


def test_the_ledger_reader_uses_the_argv_parser(tmp_path):
    """End to end through `declared_json_outputs`, so the wiring is pinned too
    and not just the helper."""
    report = tmp_path / "flow_compliance.json"
    report.write_text(json.dumps({"gate_execution_ledger": [
        {"gate": "declares_one", "cmd": "a.py /p --json reports/a.json"},
        {"gate": "declares_none", "cmd": "b.py /p"},
        {"gate": "quotes_one", "cmd": 'c.py /p --note "--json nope.json"'},
    ]}), encoding="utf-8")
    assert _GECC.declared_json_outputs(report) == {
        "declares_one": "reports/a.json",
        "declares_none": None,
        "quotes_one": None,
    }


def test_the_reader_is_not_a_prose_extractor():
    """STRUCTURAL REVERT-PROOF. The named reader must contain no regex search
    at all — restoring `_JSON_FLAG_RE.search` puts the offender back into
    `prose_polarity_consulted_check --ratchet`, which is a landing gate."""
    import ast
    tree = ast.parse(PROG.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef)
              and n.name == "_declared_json_argument")
    searches = [n for n in ast.walk(fn)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr in {"search", "findall", "finditer",
                                    "match", "fullmatch"}]
    assert not searches, (
        f"the declared-artefact reader greps again (line "
        f"{[n.lineno for n in searches]}); a command line is argv, and reading "
        f"it with a sentence-reader is the finding this closed")
