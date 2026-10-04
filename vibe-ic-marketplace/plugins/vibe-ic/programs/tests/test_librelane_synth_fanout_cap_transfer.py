"""The Librelane Step-9 caller transfers a declared cap into ABC's normal API."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import phase3_one_shot_runner as runner  # noqa: E402


class LibrelaneSynthFanoutTransfer(unittest.TestCase):
    def test_derived_cap_is_published_at_strict_canonical_config_identity(self):
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as temp:
            canonical = Path(temp) / "synthesis_resolved.json"
            original_bytes = b'{"MAX_FANOUT_CONSTRAINT":4,"meta":{"step":"Yosys.Synthesis"}}\n'
            canonical.write_bytes(original_bytes)
            result = runner._publish_librelane_synth_fanout_config(
                canonical,
                {"SYNTH_ABC_BUFFER_ONLY": (
                    True, "L19_CONSTRAINTS_PDK.constraint_declarations:L9:54")},
            )
            preserved = Path(temp) / "synthesis_pre_fanout_resolved.json"
            provenance = Path(temp) / "synthesis_resolved.provenance.json"
            self.assertEqual(result, canonical)
            self.assertEqual(preserved.read_bytes(), original_bytes)
            self.assertIs(json.loads(canonical.read_text())["SYNTH_ABC_BUFFER_ONLY"], True)
            derived_from = json.loads(provenance.read_text())["derived_from"]
            self.assertEqual(Path(derived_from), preserved)

    def test_declared_cap_selects_buffer_only_and_keeps_input_provenance(self):
        updates = runner._librelane_synth_fanout_updates(
            {"MAX_FANOUT_CONSTRAINT": 4,
             "SYNTH_ABC_BUFFERING": False,
             "SYNTH_ABC_BUFFER_ONLY": False},
            {"MAX_FANOUT_CONSTRAINT": 4},
            {"MAX_FANOUT_CONSTRAINT":
             "L19_CONSTRAINTS_PDK.constraint_declarations:L9:54"},
        )
        self.assertEqual(updates, {
            "SYNTH_ABC_BUFFER_ONLY": (
                True,
                "L19_CONSTRAINTS_PDK.constraint_declarations:L9:54 -> "
                "LibreLane Yosys.Synthesis ABC buffer -N 4 "
                "(buffer-only; no ABC upsize/dnsize)",
            )
        })

    def test_full_buffering_false_can_coexist_with_buffer_only(self):
        updates = runner._librelane_synth_fanout_updates(
            {"MAX_FANOUT_CONSTRAINT": 4,
             "SYNTH_ABC_BUFFERING": False,
             "SYNTH_ABC_BUFFER_ONLY": False},
            {"MAX_FANOUT_CONSTRAINT": 4,
             "SYNTH_ABC_BUFFERING": False},
            {"MAX_FANOUT_CONSTRAINT": "L9:54",
             "SYNTH_ABC_BUFFERING": "input declaration"},
        )
        self.assertIs(updates["SYNTH_ABC_BUFFER_ONLY"][0], True)

    def test_explicit_buffer_only_false_conflicting_with_cap_refuses(self):
        with self.assertRaisesRegex(ValueError, "LL_SYNTH_FANOUT_MODE_CONFLICT"):
            runner._librelane_synth_fanout_updates(
                {"MAX_FANOUT_CONSTRAINT": 4,
                 "SYNTH_ABC_BUFFER_ONLY": False},
                {"MAX_FANOUT_CONSTRAINT": 4,
                 "SYNTH_ABC_BUFFER_ONLY": False},
                {"MAX_FANOUT_CONSTRAINT": "L9:54",
                 "SYNTH_ABC_BUFFER_ONLY": "input declaration"},
            )

    def test_no_declared_cap_does_not_invent_abc_buffering(self):
        self.assertEqual(runner._librelane_synth_fanout_updates(
            {"SYNTH_ABC_BUFFERING": False, "SYNTH_ABC_BUFFER_ONLY": False},
            {}, {},
        ), {})

    def test_pinned_librelane_api_emits_exact_cap_without_sizing(self):
        from librelane.scripts.pyosys.construct_abc_script import ABCScriptCreator

        config = {
            "CLOCK_PERIOD": 24,
            "SYNTH_ABC_LEGACY_REFACTOR": False,
            "SYNTH_ABC_LEGACY_REWRITE": False,
            "SYNTH_ABC_USE_MFS3": False,
            "SYNTH_ABC_AREA_USE_NF": False,
            "MAX_FANOUT_CONSTRAINT": 4,
            "MAX_TRANSITION_CONSTRAINT": None,
            "SYNTH_ABC_BUFFERING": False,
            "SYNTH_ABC_BUFFER_ONLY": True,
            "SYNTH_SIZING": False,
        }
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as temp:
            path = Path(ABCScriptCreator(config).generate_abc_script(
                temp, "AREA 0"))
            script = path.read_text()
        self.assertEqual(script.count("buffer -N 4"), 1)
        self.assertNotIn("upsize", script)
        self.assertNotIn("dnsize", script)


if __name__ == "__main__":
    unittest.main(verbosity=2)
