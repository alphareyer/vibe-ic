"""vibe-ic#2081 — a blocking sign-off gate's step line must carry the numbers
the gate measured, not the verdict word it already carries in its own column.

MEASURED, sha256 x sky130A (lane rbsha2, 2026-09-06, image 0.3.46):
`run_waive_served.log:145` reads, in full,

    FAIL   drv_promotion_corroboration FAIL

while the two rows beside it carry their slack and their corner:

    FAIL   sta_corner  this gate's own rc-axis corners are MET (governing ...
    FAIL   sta_record  R3 SIGN-OFF corner 'SS' ... setup -2.530 ns, TNS -66.25

The gate's OWN artefact (`reports/phase3/sta/drv_promotion_corroboration.json`)
said why -- "the promotion claimed it ended at 136 DRV violation(s) from its own
session, but the sign-off report the acceptance gate reads shows 287" -- under
the key `reason` (SINGULAR). `_gate_detail` read `findings` and `reasons`
(PLURAL) only and fell through to echoing the verdict word.

THE POPULATION IS NOT ONE GATE. Of the six programs behind the declared
sign-off table, three write `reason` singular: `drv_promotion_corroboration_
check`, `tapeout_precheck`, and one branch of `sta_corner_record_completeness_
check`. `test_declared_signoff_gate_reason_key_population` below asserts that
membership from the SOURCE, so this test fails if a gate is added whose
explanation this reader would drop.
"""
import json
import sys
import unittest
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import phase3_one_shot_runner as R          # noqa: E402


#: The gate's own artefact, transcribed from the measured run.
MEASURED_DRV_JSON = {
    "promoted": True,
    "signoff_report": "phase3/stage3/sta/sta_mcorner_ocv.rpt",
    "signoff_drv_violations": 287,
    "claimed_drv_after": 136,
    "verdict": "FAIL",
    "rc": 1,
    "reason": ("the promotion claimed it ended at 136 DRV violation(s) from "
               "its own session, but the sign-off report the acceptance gate "
               "reads shows 287. Passing your own re-measurement is not "
               "passing the downstream gate - do not ship this route."),
}


class GateDetailReadsSingularReason(unittest.TestCase):

    def setUp(self):
        self.mod = R
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _detail(self, doc):
        p = self.tmp / "gate.json"
        p.write_text(json.dumps(doc))
        return self.mod._gate_detail(p, "", "")

    def test_singular_reason_reaches_the_step_line(self):
        detail = self._detail(MEASURED_DRV_JSON)
        self.assertIn("136", detail,
                      "the gate's own claimed count never reached the reader")
        self.assertIn("287", detail,
                      "the sign-off count that contradicted it never reached "
                      "the reader")
        self.assertNotEqual(detail.strip(), "FAIL")

    def test_negative_control_a_gate_with_no_explanation_still_says_FAIL(self):
        """The verdict-word fallback is the floor, not the defect: a gate that
        offers NO explanation must still produce a non-empty line."""
        detail = self._detail({"verdict": "FAIL", "rc": 1})
        self.assertEqual(detail.strip(), "FAIL")

    def test_negative_control_plural_reasons_are_unchanged(self):
        """Whatever this change does, it must not disturb the spelling that
        already worked -- `sta_record`'s line is the one that was correct."""
        detail = self._detail({
            "verdict": "FAIL",
            "reasons": ["R3 SIGN-OFF corner 'SS' (process axis, role setup) "
                        "is VIOLATED: setup -2.530 ns, TNS -66.25"],
        })
        self.assertIn("-2.530 ns", detail)
        self.assertIn("TNS -66.25", detail)

    def test_negative_control_findings_still_win_their_place(self):
        detail = self._detail({
            "verdict": "FAIL",
            "findings": [{"rule": "STA_REAL_VIOLATION_FOUND",
                          "message": "a real timing violation was found"}],
        })
        self.assertIn("STA_REAL_VIOLATION_FOUND", detail)

    def test_a_string_reason_is_not_iterated_character_by_character(self):
        """A bare string is the value. Iterating it would emit `t; h; e; ...`,
        which is the failure mode a `for r in doc.get(key)` would produce."""
        detail = self._detail({"verdict": "FAIL", "reason": "abcd"})
        self.assertEqual(detail.strip(), "abcd")

    def test_declared_signoff_gate_reason_key_population(self):
        """MEMBERSHIP, not a count: every program behind the declared sign-off
        table that says WHY must say it in a key this reader knows."""
        src = (PROGRAMS / "phase3_one_shot_runner.py").read_text()
        self.assertIn("_GATE_REASON_KEYS", src)
        known = set(self.mod._GATE_REASON_KEYS) | {"findings"}
        programs = [p for _n, p, _o, _a in self.mod._DECLARED_SIGNOFF_GATES]
        programs.append(self.mod._DRV_PROMOTION_GATE[1])
        speaking = {}
        for prog in programs:
            path = PROGRAMS / prog
            if not path.is_file():
                continue
            text = path.read_text()
            speaking[prog] = {k for k in ("reason", "reasons", "findings")
                              if f'"{k}":' in text}
        # every gate that writes an explanation writes it under a known key
        for prog, keys in speaking.items():
            if not keys:
                continue
            self.assertTrue(
                keys & known,
                f"{prog} explains itself under {sorted(keys)}, none of which "
                f"_gate_detail reads")
        # and the singular spelling really is in live use -- if this ever goes
        # empty the fix above has become dead code and should be revisited
        singular = sorted(p for p, k in speaking.items() if "reason" in k)
        self.assertTrue(
            singular,
            "no declared sign-off gate writes `reason` (singular) any more")


if __name__ == "__main__":
    unittest.main()
