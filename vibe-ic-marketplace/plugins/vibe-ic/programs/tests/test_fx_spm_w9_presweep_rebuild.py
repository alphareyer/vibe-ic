"""A pitch-changing presweep rebuild must have the pins of a fresh grid."""

from __future__ import annotations

import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(PROGRAMS / "tests"))

from _ppa import pdn_em_presweep as PES  # noqa: E402
from _tcl_walk import walk as _walk  # noqa: E402
from test_fx_spm_psm_source_model import _DB  # noqa: E402


_PIN_DB = r"""
set ::boxes [dict create]
rename bt_v original_bt_v
rename bt_g original_bt_g
proc bt_v {m args} {
  switch -- $m {
    isSpecial { return 1 }
    getBPins { return [lsearch -all -inline [dict keys $::boxes] bp_v_*] }
    default { return "" }
  }
}
proc bt_g {m args} {
  switch -- $m {
    isSpecial { return 1 }
    getBPins { return [lsearch -all -inline [dict keys $::boxes] bp_g_*] }
    default { return "" }
  }
}
proc odb::dbBPin_destroy {p} { dict unset ::boxes $p }
proc mock_pdngen {candidate} {
  # Model the fork's FIRM-BPin rule: a candidate's fixed pin boxes obstruct
  # new straps at another pitch. A fresh grid has no such obstruction.
  if {[dict size $::boxes]} { return }
  if {$candidate == 0} {
    foreach x {5 25} {
      dict set ::boxes bp_v_$x "Metal5:$x"
      dict set ::boxes bp_g_$x "Metal4:$x"
    }
  } else {
    foreach x {2.5 12.5 22.5 32.5} {
      dict set ::boxes bp_v_$x "Metal5:$x"
      dict set ::boxes bp_g_$x "Metal4:$x"
    }
  }
}
"""


def _boxes(tmp_path: Path, builds: tuple[int, ...], *, pads: bool = False) -> list[str]:
    sweep = tmp_path / "sweep"
    sweep.mkdir(parents=True)
    (sweep / "cand_0.tcl").write_text("mock_pdngen 0\n")
    (sweep / "cand_1.tcl").write_text("mock_pdngen 1\n")
    tcl = PES.session_tcl(sweep_dir=str(sweep), python="python3",
                          nets={"VDD": 5.0, "VSS": 0.0}, corner=None,
                          declared_pads=[], stage_marker="PNR_STAGE:")
    procs = tcl[:tcl.index("set _pes_k 0")]
    calls = "\n".join(f"_vibeic_pes_build {k}" for k in builds)
    script = (_DB + f"set ::insts {{{'inst_pad' if pads else 'inst_core'}}}\n"
              + _PIN_DB + procs + calls
              + '\nputs "PIN_BOXES [join [lsort [dict values $::boxes]] ,]"\n')
    out, err, route = _walk(script, "", tmp_path)
    rows = [line.removeprefix("PIN_BOXES ") for line in out.splitlines()
            if line.startswith("PIN_BOXES ")]
    assert len(rows) == 1, f"Tcl did not finish via {route}: {out}\n{err}"
    return rows[0].split(",") if rows[0] else []


def test_pitch_changing_rebuild_has_exactly_the_fresh_candidates_pin_boxes(tmp_path):
    fresh = _boxes(tmp_path / "fresh", (1,))
    rebuilt = _boxes(tmp_path / "rebuilt", (0, 1))
    assert len(fresh) == 8
    assert rebuilt == fresh, (rebuilt, fresh)


def test_a_die_keeps_its_existing_pad_pin_boxes(tmp_path):
    # The same predicate as promoted-pin exclusion: a placed pad master means
    # this is a DIE's supply source, so candidate rebuilding cannot delete it.
    after = _boxes(tmp_path / "die", (0, 1), pads=True)
    original = _boxes(tmp_path / "original", (0,), pads=True)
    assert after == original and len(after) == 4
