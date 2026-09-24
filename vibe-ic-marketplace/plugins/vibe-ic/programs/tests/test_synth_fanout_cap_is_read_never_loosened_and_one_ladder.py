#!/usr/bin/env python3
"""The synthesis fanout cap: a design-declared value is READ and never
loosened, and synthesis and BOTH sign-off SDC paths get the same cap from ONE
ladder (review of next/icsub5-synthfo, findings 3 and 4).

FINDING 3, MEASURED on spm (gf180mcuD). Its L9 §9.1B declares the cap as a
per-library table:

    | library | `MAX_FANOUT_CONSTRAINT` |
    | `sky130_fd_sc_ls` | 5 |
    | `gf180mcu_*` | 4 |
    | 其他 | 工具預設 |

`_L9_SYNTH_MAX_FANOUT_RE` only reads a single `SYNTH_MAX_FANOUT | N |` row, so
the design's 4 read as "no cap" and the PDK-family default (10) LOOSENED it.

FINDING 4. The staged-SDC (augment) path stopped at the design-declared tiers
and the liberty, skipping the PDK-family default synthesis consults, so a
design staging its own SDC could be bounded at 10 in synthesis and carry no
`set_max_fanout` at sign-off. And a design SDC's own `set_max_fanout` (kept
byte-identical at sign-off) was invisible to synthesis.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import phase3_one_shot_runner as R  # noqa: E402

SPM_L9 = """## 9.1B Synthesis Fanout Limit(非 SDC,合成階段約束)

| library | `MAX_FANOUT_CONSTRAINT` |
|---|---|
| `sky130_fd_sc_ls` | 5 |
| `gf180mcu_*` | 4 |
| 其他 | 工具預設 |
"""


def _proj(tmp_path: Path, l9: str = "", sdc: str = "") -> Path:
    docs = tmp_path / "input" / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    if l9:
        (docs / "L9_constraints_floorplan.md").write_text(l9)
    if sdc:
        c = tmp_path / "input" / "constraints"
        c.mkdir(parents=True, exist_ok=True)
        (c / "design.sdc").write_text(sdc)
    return tmp_path


def _lib(monkeypatch, name="gf180mcu_fd_sc_mcu7t5v0"):
    monkeypatch.setattr(R, "_active_std_cell_library", lambda *a, **k: name)


def _pdk_default(monkeypatch, cap=10):
    monkeypatch.setattr(R, "_flow_default_max_fanout_read",
                        lambda *a, **k: (cap, "pdk_compat.py:322", ""))
    monkeypatch.setattr(R, "_flow_default_max_fanout",
                        lambda *a, **k: (cap, "pdk_compat.py:322"))
    monkeypatch.setattr(R, "_liberty_drv_limits", lambda *a, **k: {})


# --- finding 3: a per-library L9 table is a declaration ---------------------
def test_the_measured_spm_table_declares_4_on_gf180(tmp_path, monkeypatch):
    """RED ON THE REVIEWED BRANCH (None there -> the PDK default 10)."""
    _lib(monkeypatch)
    assert R._l9_declared_max_fanout(_proj(tmp_path, SPM_L9), "gf180mcuD") == 4


def test_the_declared_4_is_never_loosened_by_the_pdk_default(tmp_path,
                                                            monkeypatch):
    _lib(monkeypatch)
    _pdk_default(monkeypatch, 10)
    cap, why, _ = R._synth_max_fanout(_proj(tmp_path, SPM_L9), "gf180mcuD")
    assert cap == 4, (cap, why)
    assert "gf180mcu_*" in why, why


def test_staged_sdc_does_not_loosen_the_design_table_to_liberty_default(
        tmp_path, monkeypatch):
    """Exercise the existing sign-off consumer on main as a value control."""
    _lib(monkeypatch)
    monkeypatch.setattr(R, "_liberty_drv_limits", lambda *a, **k: {
        "max_fanout": 10, "fanout_source": "active.lib:default_max_fanout"})
    project = _proj(tmp_path, SPM_L9)
    _, info = R._ensure_staged_sdc_drv(
        "create_clock -period 10 [get_ports clk]\n", "active.lib", "",
        project, pdk_name="gf180mcuD")
    assert info["added_max_fanout"] == 4, info["added_max_fanout"]


def test_a_foreign_librarys_row_never_reaches_this_run():
    assert R._l9_library_scoped_fanout(SPM_L9, "sky130_fd_sc_hd",
                                       "sky130A") is None
    assert R._l9_library_scoped_fanout(SPM_L9, "sky130_fd_sc_ls",
                                       "sky130A") == (5, "sky130_fd_sc_ls")


def test_a_row_without_a_number_declares_nothing_and_unknown_library_is_none():
    assert R._l9_library_scoped_fanout(SPM_L9, "", "") is None
    table = "| library | MAX_FANOUT_CONSTRAINT |\n|---|---|\n| gf180mcu_* | 工具預設 |\n"
    assert R._l9_library_scoped_fanout(table, "gf180mcu_x", "gf180mcuD") is None


def test_an_exact_row_beats_a_glob_and_emphasis_is_not_a_glob():
    table = ("| lib | **MAX_FANOUT_CONSTRAINT** |\n|---|---|\n"
             "| **gf180mcu_*** | **6** |\n| gf180mcu_fd_sc_mcu7t5v0 | 3 |\n")
    assert R._l9_library_scoped_fanout(
        table, "gf180mcu_fd_sc_mcu7t5v0", "") == (3, "gf180mcu_fd_sc_mcu7t5v0")
    assert R._l9_library_scoped_fanout(
        table, "gf180mcu_fd_sc_mcu9t5v0", "") == (6, "gf180mcu_*")


# --- finding 4: one ladder for synthesis and BOTH SDC paths ----------------
def test_a_design_sdcs_own_cap_bounds_synthesis(tmp_path, monkeypatch):
    _lib(monkeypatch)
    _pdk_default(monkeypatch, 10)
    proj = _proj(tmp_path, sdc="create_clock -period 10 [get_ports clk]\n"
                               "set_max_fanout 6 [current_design]\n")
    cap, why, _ = R._synth_max_fanout(proj, "gf180mcuD")
    assert cap == 6 and "staged SDC" in why, (cap, why)


def test_two_design_caps_the_tighter_wins_and_both_are_named(tmp_path,
                                                             monkeypatch):
    _lib(monkeypatch)
    _pdk_default(monkeypatch, 10)
    proj = _proj(tmp_path, SPM_L9,
                 "create_clock -period 10 [get_ports clk]\n"
                 "set_max_fanout 9 [current_design]\n")
    cap, why, _ = R._synth_max_fanout(proj, "gf180mcuD")
    assert cap == 4, (cap, why)
    assert "two caps" in why and "9" in why, why


def test_augment_path_gets_the_same_cap_synthesis_gets(tmp_path, monkeypatch):
    """RED ON THE REVIEWED BRANCH: the augment path skipped the PDK-family
    tier, so with a liberty declaring no default it supplied NO cap while
    synthesis bounded at 10."""
    _lib(monkeypatch)
    _pdk_default(monkeypatch, 10)
    staged = "create_clock -period 10 [get_ports clk]\n"
    proj = _proj(tmp_path, sdc=staged)
    synth_cap, _why, _u = R._synth_max_fanout(proj, "gf180mcuD", "x.lib")
    text, info = R._ensure_staged_sdc_drv(staged, "x.lib", "", proj,
                                          pdk_name="gf180mcuD")
    assert info["added_max_fanout"] == synth_cap == 10, (info, synth_cap)
    fo = [int(float(m.group(2))) for m in R._SDC_MAX_FANOUT_RE.finditer(text)]
    assert fo and set(fo) == {synth_cap}, (fo, synth_cap)


def test_augment_path_keeps_a_design_declared_cap_and_synthesis_matches(
        tmp_path, monkeypatch):
    _lib(monkeypatch)
    _pdk_default(monkeypatch, 10)
    staged = ("create_clock -period 10 [get_ports clk]\n"
              "set_max_fanout 6 [current_design]\n")
    proj = _proj(tmp_path, sdc=staged)
    text, info = R._ensure_staged_sdc_drv(staged, "x.lib", "", proj,
                                          pdk_name="gf180mcuD")
    fo = {int(float(m.group(2))) for m in R._SDC_MAX_FANOUT_RE.finditer(text)}
    synth_cap, _w, _u = R._synth_max_fanout(proj, "gf180mcuD", "x.lib")
    assert fo == {6} == {synth_cap}, (fo, synth_cap)
    assert "set_max_fanout" in info["design_declared"]


def test_auto_sdc_path_gets_the_same_cap_synthesis_gets(tmp_path, monkeypatch):
    _lib(monkeypatch)
    _pdk_default(monkeypatch, 10)
    proj = _proj(tmp_path, SPM_L9)
    synth_cap, _w, _u = R._synth_max_fanout(proj, "gf180mcuD", "x.lib")
    captured = {}
    real = R._drv_constraints_sdc_block

    def spy(*a, **k):
        captured["max_fanout"] = k.get("max_fanout")
        return real(*a, **k)
    monkeypatch.setattr(R, "_drv_constraints_sdc_block", spy)
    try:
        R._build_auto_silicon_sdc(proj, "top", liberty_path="x.lib",
                                  pdk_name="gf180mcuD")
    except Exception:          # the SDC builder may need more than a fixture
        pass                   # once past the DRV block; the cap is captured
    assert captured.get("max_fanout") == synth_cap == 4, (captured, synth_cap)


def test_every_sdc_path_resolves_through_the_one_ladder():
    """Source pin: no SDC path resolves the cap on its own again."""
    src = (PROGRAMS / "phase3_one_shot_runner.py").read_text()
    for fn in ("def _ensure_staged_sdc_drv", "def _build_auto_silicon_sdc"):
        i = src.index(fn)
        j = src.index("\ndef ", i + 10)
        body = src[i:j]
        assert "_synth_max_fanout(" in body, fn
        assert "_l9_declared_max_fanout(project" not in body, (
            f"{fn} resolves the cap on its own again")


def test_an_unread_tier_is_disclosed_in_the_sdc_without_a_drv_block(tmp_path,
                                                                    monkeypatch):
    """UNREAD IS NOT EMPTY, and a disclosure is not a constraint: an auto SDC
    whose cap could not be resolved because a tier was unreadable SAYS so, and
    emits no `TAPEOUT-SIGNOFF (DRV)` block for limits nobody declared."""
    monkeypatch.setattr(R, "_active_std_cell_library", lambda *a, **k: "")
    monkeypatch.setattr(R, "_flow_default_max_fanout_read", lambda *a, **k: (
        None, "", "pdk_compat.py NOT READ: this run records no container image"))
    monkeypatch.setattr(R, "_liberty_drv_limits", lambda *a, **k: {})
    sdc = R._build_auto_silicon_sdc(_proj(tmp_path), top="chip_top")
    assert "NOT READ" in sdc and "max_fanout UNRESOLVED" in sdc, sdc[-800:]
    assert "TAPEOUT-SIGNOFF (DRV)" not in sdc
    assert "set_max_fanout" not in sdc


def test_a_flow_written_sdc_is_read_but_not_called_a_design_declaration(
        tmp_path, monkeypatch):
    """MEASURED on arm B (subservient): the resolver returned the FLOW-written
    phase-2 SDC, and the ledger called it "the design's own staged SDC".
    Synthesis still reads it (PnR keeps its cap, so the two must agree), but
    the provenance says who wrote it."""
    _lib(monkeypatch)
    _pdk_default(monkeypatch, 10)
    proj = _proj(tmp_path)
    flow = proj / "phase2" / "stage2" / "constraints"
    flow.mkdir(parents=True)
    (flow / "top.sdc").write_text("set_max_fanout 10 [current_design]\n")
    monkeypatch.setattr(R, "_resolve_staged_silicon_sdc",
                        lambda p: flow / "top.sdc")
    cap, why, _ = R._synth_max_fanout(proj, "gf180mcuD")
    assert cap == 10, (cap, why)
    assert "flow-written" in why and "design's own" not in why, why
