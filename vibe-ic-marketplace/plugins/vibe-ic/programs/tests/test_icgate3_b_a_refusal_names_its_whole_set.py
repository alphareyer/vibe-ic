"""R-icgate3 — a refusing producer's step row carries the SET it refused on.

MEASURED on spm run15 (8HD-4, `_lane_icspm5/run15`),
`reports/orchestrator/phase3_one_shot.json`:

    steps[28] tapeout_docs_gen     NOT_MEASURED
      "NOT RELEASABLE — no documents written. 8 propert(ies) are not clean:"
    steps[29] ic_release_docs_gen  NOT_MEASURED
      "NOT RELEASABLE — no product documents written. 1 release(s) examined."

A colon and nothing after it. Nobody could name the eight.

THE PRODUCERS ALREADY ENUMERATE. `tapeout_docs_gen` prints `f"  - {b}"` for
every blocker (tapeout_docs_gen.py, in `main` under `release_blockers`), and
`ic_release_docs_gen._refuse` prints every artefact refusal and every unclean
property. Re-running `tapeout_docs_gen` on run15's own bytes (its
`phase3/final/metrics.json`, sha256 998c2d90b99b...) prints all eight:

    - Magic DRC: NOT_MEASURED (magic__drc_error__count)
    - KLayout DRC: 5 (klayout__drc_error__count)
    - Density: NOT_MEASURED (klayout__density_error__count)
    - Antenna — violating nets: 1 (antenna__violating__nets)
    - Antenna — violating pins: 1 (antenna__violating__pins)
    - GDS vs layout XOR: NOT_MEASURED (design__xor_difference__count)
    - Max-slew violations: NOT_MEASURED (design__max_slew_violation__count)
    - Max-cap violations: NOT_MEASURED (design__max_cap_violation__count)

so the defect is in the CONSUMER, and it is one expression, repeated at all
three producer-dispatch rows in `phase3_one_shot_runner`:

    detail_lines = (cp.stdout or cp.stderr or "").strip().splitlines()
    detail = detail_lines[0] if detail_lines else f"rc={cp.returncode}"

  * `detail_lines[0]` keeps the header and drops the set;
  * `stdout or stderr` drops the WHOLE refusal the moment the producer also
    prints one progress line — both refusals above go to stderr.

Grepping the whole of run15 for `propert(ies) are not clean` returns ONE hit,
the header itself, so the enumeration was not merely unrendered — it was never
kept anywhere. `_producer_detail` therefore also WRITES it, to
`reports/orchestrator/producers/<step>.log`.

chip-AGNOSTIC: this reasons about a subprocess's two streams, nothing else.
"""
import ast
import sys
import types
import unittest
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import phase3_one_shot_runner as R          # noqa: E402
import tapeout_docs_gen as T                # noqa: E402


#: `tapeout_docs_gen`'s stderr on run15's own metrics, verbatim.
MEASURED_TAPEOUT_STDERR = """NOT RELEASABLE — no documents written. 8 propert(ies) are not clean:
  - Magic DRC: NOT_MEASURED (magic__drc_error__count)
  - KLayout DRC: 5 (klayout__drc_error__count)
  - Density: NOT_MEASURED (klayout__density_error__count)
  - Antenna — violating nets: 1 (antenna__violating__nets)
  - Antenna — violating pins: 1 (antenna__violating__pins)
  - GDS vs layout XOR: NOT_MEASURED (design__xor_difference__count)
  - Max-slew violations: NOT_MEASURED (design__max_slew_violation__count)
  - Max-cap violations: NOT_MEASURED (design__max_cap_violation__count)

A release document for a run that did not pass is worse than no document: it becomes a file that outlives the run it came from. Fix the run, or pass --allow-incomplete to write a DRAFT.
"""

#: The eight, by the metric key each names -- MEMBERSHIP, never a count.
MEASURED_EIGHT = (
    "magic__drc_error__count", "klayout__drc_error__count",
    "klayout__density_error__count", "antenna__violating__nets",
    "antenna__violating__pins", "design__xor_difference__count",
    "design__max_slew_violation__count", "design__max_cap_violation__count",
)


def _cp(stdout="", stderr="", rc=1):
    return types.SimpleNamespace(stdout=stdout, stderr=stderr, returncode=rc)


class _Tmp(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._t = tempfile.TemporaryDirectory()
        self.tmp = Path(self._t.name)

    def tearDown(self):
        self._t.cleanup()


class ARefusalNamesItsWholeSet(_Tmp):

    def test_every_one_of_the_eight_reaches_the_step_row(self):
        detail = R._producer_detail("tapeout_docs_gen",
                                    _cp(stderr=MEASURED_TAPEOUT_STDERR),
                                    self.tmp)
        missing = [k for k in MEASURED_EIGHT if k not in detail]
        self.assertFalse(missing, f"the row still cannot name: {missing}")

    def test_the_row_stays_one_line(self):
        detail = R._producer_detail("tapeout_docs_gen",
                                    _cp(stderr=MEASURED_TAPEOUT_STDERR),
                                    self.tmp)
        self.assertNotIn("\n", detail)

    def test_a_progress_line_on_stdout_does_not_erase_the_refusal(self):
        """`stdout or stderr` published the progress line and nothing else."""
        detail = R._producer_detail(
            "tapeout_docs_gen",
            _cp(stdout="phase3: assembling release documents\n",
                stderr=MEASURED_TAPEOUT_STDERR), self.tmp)
        missing = [k for k in MEASURED_EIGHT if k not in detail]
        self.assertFalse(missing, f"stdout erased the refusal; missing {missing}")

    def test_the_whole_account_is_written_to_a_producer_log(self):
        R._producer_detail("tapeout_docs_gen",
                           _cp(stderr=MEASURED_TAPEOUT_STDERR), self.tmp)
        log = self.tmp / R._PRODUCER_LOG_DIR_REL / "tapeout_docs_gen.log"
        self.assertTrue(log.is_file(), "no producer log was written")
        text = log.read_text()
        for k in MEASURED_EIGHT:
            self.assertIn(k, text)

    def test_an_over_budget_account_drops_whole_lines_and_names_the_log(self):
        big = "header:\n" + "\n".join(f"  - item {i} " + "x" * 120
                                      for i in range(60))
        detail = R._producer_detail("p", _cp(stderr=big), self.tmp)
        self.assertIn("more line(s) not shown here", detail)
        self.assertIn(R._PRODUCER_LOG_DIR_REL, detail)
        # WHOLE items: every rendered item ends where its own text ends.
        # The trailing disclosure is not an item -- strip it before splitting.
        body = detail.split(" [+", 1)[0]
        for part in body.split("; ")[1:]:
            self.assertTrue(part.rstrip().endswith("x"),
                            f"an item was cut mid-token: {part[-30:]!r}")

    # ---- the other direction --------------------------------------------- #
    def test_negative_control_zero_unclean_properties_still_says_zero(self):
        """A producer with nothing to refuse on refuses nothing. Driven through
        `release_blockers` itself, over the FULL declared key set."""
        clean = {key: 0 for _l, key, _p in T.MANUFACTURABILITY + T.ELECTRICAL}
        self.assertEqual(T.release_blockers(clean), [],
                         "a clean run acquired a blocker")

    def test_negative_control_a_passing_producer_row_is_its_own_output(self):
        detail = R._producer_detail("tapeout_docs_gen",
                                    _cp(stdout="wrote 2 document(s)\n", rc=0),
                                    self.tmp)
        self.assertIn("wrote 2 document(s)", detail)
        self.assertNotIn("not shown here", detail)

    def test_negative_control_no_output_at_all_names_the_rc(self):
        self.assertEqual(
            R._producer_detail("p", _cp(stdout="", stderr="", rc=3), self.tmp),
            "rc=3")

    def test_negative_control_no_project_claims_no_log(self):
        detail = R._producer_detail("p", _cp(stderr=MEASURED_TAPEOUT_STDERR),
                                    None)
        self.assertNotIn(R._PRODUCER_LOG_DIR_REL, detail,
                         "a log was named that nothing wrote")
        for k in MEASURED_EIGHT:
            self.assertIn(k, detail, "the row lost the set it had no log for")

    def test_negative_control_an_unwritable_project_says_so_and_never_lies(self):
        unwritable = self.tmp / "ro"
        unwritable.mkdir()
        unwritable.chmod(0o500)
        try:
            big = "header:\n" + "\n".join(f"  - item {i} " + "x" * 120
                                          for i in range(60))
            detail = R._producer_detail("p", _cp(stderr=big), unwritable)
            self.assertIn("not recorded anywhere", detail)
            self.assertNotIn(R._PRODUCER_LOG_DIR_REL, detail)
        finally:
            unwritable.chmod(0o700)

    def test_negative_control_an_unwritable_LOG_DIR_says_so_and_never_lies(self):
        """The sibling of the above, one level deeper: the directory EXISTS and
        cannot be written into, so the failure is in the write, not the mkdir."""
        log_dir = self.tmp / R._PRODUCER_LOG_DIR_REL
        log_dir.mkdir(parents=True)
        log_dir.chmod(0o500)
        try:
            big = "header:\n" + "\n".join(f"  - item {i} " + "x" * 120
                                          for i in range(60))
            detail = R._producer_detail("p", _cp(stderr=big), self.tmp)
            self.assertIn("not recorded anywhere", detail)
            self.assertNotIn(R._PRODUCER_LOG_DIR_REL, detail)
        finally:
            log_dir.chmod(0o700)


class NoDispatchRowStillTakesLineZero(unittest.TestCase):
    """MEMBERSHIP over the runner's own source: a fourth dispatch row added
    later must not quietly reintroduce the expression. `test_...population`
    below is the shape `test_issue2081_...` uses one layer up."""

    def test_the_line_zero_expression_is_gone_from_the_runner(self):
        """Asserted over the AST, NOT over the text.

        The defective expression is QUOTED in `_producer_detail`'s own
        docstring, as the measurement that motivated it. A grep-shaped check
        would be satisfied by deleting that prose and unsatisfied by keeping
        it -- a guard keyed on a comment is not a guard.
        """
        src = (PROGRAMS / "phase3_one_shot_runner.py").read_text()
        offenders = []
        for node in ast.walk(ast.parse(src)):
            if (isinstance(node, ast.Subscript)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "detail_lines"
                    and isinstance(node.slice, ast.Constant)
                    and node.slice.value == 0):
                offenders.append(node.lineno)
        self.assertFalse(offenders,
                         f"a producer-dispatch row still takes only line 0, "
                         f"at line(s) {offenders}")

    def test_every_producer_dispatch_row_uses_the_shared_reader(self):
        src = (PROGRAMS / "phase3_one_shot_runner.py").read_text()
        tree = ast.parse(src)
        dispatchers = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            seg = ast.get_source_segment(src, node) or ""
            if "_run_producer(" not in seg and "docs_gen.py" not in seg:
                continue
            if not node.name.startswith("step_"):
                continue
            if "_docs_gen" not in node.name:
                continue
            dispatchers[node.name] = "_producer_detail(" in seg
        self.assertTrue(dispatchers, "no docs-gen dispatch row was found at all")
        silent = sorted(n for n, ok in dispatchers.items() if not ok)
        self.assertFalse(silent,
                         f"these rows do not use the shared reader: {silent}")

    def test_the_step_name_each_row_passes_is_its_own(self):
        """A copy-paste that names another row's log would file one producer's
        account under another's name."""
        src = (PROGRAMS / "phase3_one_shot_runner.py").read_text()
        tree = ast.parse(src)
        seen = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if not node.name.startswith("step_"):
                continue
            for call in ast.walk(node):
                if (isinstance(call, ast.Call)
                        and getattr(call.func, "id", "") == "_producer_detail"
                        and call.args
                        and isinstance(call.args[0], ast.Constant)):
                    seen += 1
                    self.assertEqual(call.args[0].value,
                                     node.name[len("step_"):],
                                     f"{node.name} files its log under "
                                     f"{call.args[0].value!r}")
        self.assertGreaterEqual(seen, 3,
                                "fewer dispatch rows than the three measured")


if __name__ == "__main__":
    unittest.main()
