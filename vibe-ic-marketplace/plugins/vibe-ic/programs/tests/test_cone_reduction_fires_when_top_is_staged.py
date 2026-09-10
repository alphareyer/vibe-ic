"""Cone reduction must fire when the authored top is ALREADY staged.

DEFECT (measured — opentitan_aes x sky130A, plugin 1.20.60, 2026-09-10):
  `step_reused_ip_consume` resolved the cone root like this::

      _staged_mods = set(_v661_rtl_module_names(project))
      if top_name not in _staged_mods:              # <-- only when ABSENT
          _root = _v661_resolve_dut_module(project, top_name, l9_top_module)
          ...
      if synth_top_resolved is None:
          _autoemit_chip_top_wrapper(...)           # returns None if top exists

  So when the requested top IS staged -- the NORMAL catalog-glue outcome, where
  the authored wrapper sits in `rtl/` beside the reused IP -- neither branch set
  `synth_top_resolved`, it stayed None, and the transitive-cone reduction below
  it skipped with `cone_skipped = "no top resolved"`. The whole vendor package
  then stayed staged and the orphan tail broke elaboration.

  MEASURED, both arms, same tree (the authored 100-file closure pre-staged):
      pre-fix : 100 -> 286 files, synth_top_resolved=None, cone_skipped set
      post-fix: 100 -> 133 files, synth_top_resolved='chip_top', cone ran
  Pre-fix the two orphans that abort yosys-slang (`prim_ascon_duplex.sv`,
  `prim_flash.sv`) were staged; post-fix neither is.

WHY NO EXISTING TEST CAUGHT IT: every fixture in `test_rtl_transitive_cone.py`
  and `test_reused_ip_rtl_consume.py` builds the project with `input/vendor_rtl/`
  populated and `phase2/stage1/rtl/` EMPTY, so `top_name` is never among the
  staged modules and the guard above is always TRUE. The reduction was written
  for exactly this design and never fired on it.

DECLARATION: this test is ADVISORY-neutral -- it asserts the reduction RUNS and
  that an out-of-cone orphan is not staged. It does not add a blocking gate.

Chip-AGNOSTIC: synthetic modules only; no design, PDK or vendor literals.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import design_one_shot_runner as R  # noqa: E402

# The top instantiates `core`; `core` instantiates `leaf_absent`, which NO file
# defines (the parameter-gated, dataset-excluded variant shape). That unresolved
# reference is what makes `reused_ip_consume` decide the staged tree is not
# closed and stage the vendor package -- the real trigger, reproduced.
_TOP = ("module glue_top(input a, output y);\n"
        "  core u(.a(a), .y(y));\n"
        "endmodule\n")
_CORE = ("module core(input a, output y);\n"
         "  leaf_absent u(.a(a), .y(y));\n"
         "endmodule\n")
# An ORPHAN nothing reaches, using a macro no staged file defines -- the shape
# that aborts a single-unit frontend when it is staged for no reason.
_ORPHAN = ("module orphan_macro_user(input a, output y);\n"
           "  `SOME_MACRO_NO_FILE_DEFINES(u, a, y)\n"
           "endmodule\n")


def _mk(root: Path) -> Path:
    vd = root / "input" / "vendor_rtl"
    vd.mkdir(parents=True, exist_ok=True)
    for n, t in (("glue_top.sv", _TOP), ("core.sv", _CORE),
                 ("orphan_macro_user.sv", _ORPHAN)):
        (vd / n).write_text(t)
    gd = root / "phase1" / "generated_docs"
    gd.mkdir(parents=True, exist_ok=True)
    (gd / "L9_INTEGRATION_SPEC.json").write_text(json.dumps({}))
    # THE POINT: the authored top is ALREADY staged, as catalog-glue leaves it.
    rtl = root / "phase2" / "stage1" / "rtl"
    rtl.mkdir(parents=True, exist_ok=True)
    (rtl / "glue_top.sv").write_text(_TOP)
    (rtl / "core.sv").write_text(_CORE)
    return rtl


def test_cone_reduction_fires_when_the_requested_top_is_already_staged(tmp_path):
    rtl = _mk(tmp_path)
    assert "glue_top" in set(R._v661_rtl_module_names(tmp_path))

    sr = R.step_reused_ip_consume(tmp_path, "glue_top")
    ex = sr.extras or {}

    # 1. the root resolved -- this is what was None pre-fix
    assert ex.get("synth_top_resolved") == "glue_top", (
        f"top not resolved though it is staged; cone_skipped="
        f"{ex.get('cone_skipped')!r}")
    # 2. therefore the reduction actually RAN
    assert not ex.get("cone_skipped"), ex.get("cone_skipped")
    assert ex.get("cone_root") == "glue_top", ex.get("cone_root")
    # 3. and the out-of-cone orphan is not in the build set
    assert not (rtl / "orphan_macro_user.sv").is_file(), \
        sorted(p.name for p in rtl.glob("*.sv"))
    # 4. never worse than staging everything: cone files all survive
    for keep in ("glue_top.sv", "core.sv"):
        assert (rtl / keep).is_file(), sorted(p.name for p in rtl.glob("*.sv"))
    assert sr.status == "PASS", sr.detail[-300:]
