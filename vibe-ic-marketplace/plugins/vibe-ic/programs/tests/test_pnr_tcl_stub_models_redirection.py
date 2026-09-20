"""The deck evaluator's stand-in models OpenROAD's `>` redirection — and only
that.

WHY THIS FILE EXISTS. `_pnr_tcl_stub` is the interpreter six deck-evaluability
tests run the WHOLE `pnr.tcl` under, and its contract is that it grows only
where the deck grows. v1.22.13 (#2376) emitted a post-route block that RE-READS
the `sta.rpt` the line above it wrote; under the one-line `unknown` stub nothing
had created that file and 16 cases across six files died at `couldn't open`.
The repair was to teach `unknown` the redirect OpenROAD's command wrappers
already perform — and a stand-in that grew a capability needs the capability
pinned in BOTH directions, or the next reader cannot tell a faithful model from
a blanket "every open succeeds".

So: the redirect must WORK (a redirected path is readable afterwards, `>`
truncates, `>>` appends), and it must NOT over-reach (a path nothing redirected
into is still unopenable, and a command whose trailing arguments merely look
like operands is left alone).
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _pnr_tcl_stub import STUB  # noqa: E402

tclsh = shutil.which("tclsh")
needs_tclsh = pytest.mark.skipif(tclsh is None, reason="tclsh not installed")


def _run(tmp_path: Path, body: str):
    script = tmp_path / "probe.tcl"
    script.write_text(STUB + body)
    return subprocess.run([tclsh, str(script)], capture_output=True, text=True)


@needs_tclsh
def test_a_redirected_command_creates_the_file_the_deck_reads_back(tmp_path):
    """The exact shape the deck uses: write with `>`, then `open ... r`."""
    rpt = tmp_path / "sta.rpt"
    res = _run(tmp_path, f'''
report_checks -path_delay max > {rpt}
set f [open {rpt} r]
set text [read $f]
close $f
puts "READ_BACK:[string length $text]"
''')
    assert res.returncode == 0, res.stderr
    assert "READ_BACK:0" in res.stdout, res.stdout


@needs_tclsh
def test_a_stubbed_command_writes_nothing_into_its_redirect(tmp_path):
    """EMPTY, not absent, and not invented content.

    A stubbed command produces no output, so its redirect target is created
    empty — the honest stand-in for `report_checks` on a design the stub never
    linked. Inventing a report body would make every deck block that parses
    one read a fixture this file wrote.
    """
    rpt = tmp_path / "area.rpt"
    res = _run(tmp_path, f'report_design_area > {rpt}\nputs DONE\n')
    assert res.returncode == 0, res.stderr
    assert rpt.is_file()
    assert rpt.read_text() == ""


@needs_tclsh
def test_truncating_and_appending_are_distinguished(tmp_path):
    rpt = tmp_path / "hold.rpt"
    rpt.write_text("PRIOR\n")
    res = _run(tmp_path, f'''
some_command >> {rpt}
set f [open {rpt} r]
puts "AFTER_APPEND:[string trim [read $f]]"
close $f
other_command > {rpt}
set f [open {rpt} r]
puts "AFTER_TRUNCATE:[string length [read $f]]"
close $f
''')
    assert res.returncode == 0, res.stderr
    assert "AFTER_APPEND:PRIOR" in res.stdout, res.stdout
    assert "AFTER_TRUNCATE:0" in res.stdout, res.stdout


@needs_tclsh
def test_a_path_nothing_redirected_into_is_still_unopenable(tmp_path):
    """THE NEGATIVE CONTROL. The stub did not make `open` always succeed.

    This is the failure the 16 reds actually were, reproduced deliberately: a
    deck that reads a report no line of it ever wrote must still die, or the
    evaluators stop being able to catch that defect at all.
    """
    missing = tmp_path / "never_written.rpt"
    res = _run(tmp_path, f'set f [open {missing} r]\nputs UNREACHED\n')
    assert res.returncode != 0
    assert "couldn't open" in res.stderr, res.stderr
    assert "UNREACHED" not in res.stdout


@needs_tclsh
def test_a_trailing_operand_that_is_not_a_redirect_is_left_alone(tmp_path):
    """`>` is recognised as its own WORD in the last-but-one position only.

    A command ending in two ordinary operands must not have the second one
    created as a file, or the stub would litter the deck's own arguments
    across the filesystem and mask a missing redirect.
    """
    victim = tmp_path / "not_a_redirect_target"
    res = _run(tmp_path, f'set_placement_padding -global {victim}\nputs DONE\n')
    assert res.returncode == 0, res.stderr
    assert "DONE" in res.stdout
    assert not victim.exists()
