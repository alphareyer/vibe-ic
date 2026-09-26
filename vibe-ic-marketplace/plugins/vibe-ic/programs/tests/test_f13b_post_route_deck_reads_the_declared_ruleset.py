"""F13b -- the post-route SPEF extraction in pnr.tcl reads the OpenRCX ruleset
the PDK DECLARES, through the reader step 22 uses (F13, v1.25.33).

`_post_route_spef_repair_tcl` globbed `rules.openrcx.*.nom.magic` (then `.nom`,
then IHP's `openrcx/*.nom.magic.rules`) under the PDK root and derived the
sign-off max deck by `string map {.nom. .max.}`. Nothing declares the `.magic`
file: on the 0.3.79 image gf180mcuD declares `rules.openrcx.gf180mcuD.<c>`,
sky130A `.<c>.calibre`, ihp-sg13g2 `openrcx/IHP_rcx_patterns.rules` (F13's
measurements). F13 moved step 22 and the ship repair (`_max_captable_c`) onto
the declaration; this deck -- the SPEF the in-flow sign-off DRV repair sizes
against -- still took the undeclared model.

Now: `step_pnr` resolves `_openrcx_ruleset_declaration(pdk, container)`,
records it beside pnr.tcl (`_write_rcx_declaration_record`), and hands it to
the deck. nom for the measurement, max for the DRV repair (nom, disclosed,
when no max is declared). UNREADABLE, or a declaration without nom, REFUSES:
no extraction and no repair on a substituted model.

chip-AGNOSTIC: fixture PDK `fix`.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import phase3_one_shot_runner as R  # noqa: E402

needs_tclsh = pytest.mark.skipif(shutil.which("tclsh") is None,
                                 reason="tclsh not installed")

# Every tool command is a no-op, except the one this file is about.
_STUB = ('proc unknown {args} { return "" }\n'
         'proc extract_parasitics {args} { puts "EXTRACT_WITH: $args" }\n')


def _pdk(tech_lef: str) -> "R.PdkConfig":
    return R.PdkConfig(
        name="fixture_pdk", liberty="/pdk/lib.lib", tech_lef=tech_lef,
        cell_lef="/pdk/cells.lef", cell_gds=None, site="s", drc_deck=None,
        metal_prefix="met", tapcell_master="t", antenna_diode_cell="d",
        pnr_exclude_cell_file="/pdk/x.cells")


def _host_exec(container, cmd, timeout=20, **_kw):
    p = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True,
                       timeout=60)
    return p.returncode, p.stdout, p.stderr


def _stage(root: Path, declare=("nom", "max")) -> str:
    """A PDK tree shipping BOTH the declared `.<c>` ruleset and an undeclared
    `.<c>.magic` beside it -- the gf180mcuD shape -- and a LibreLane
    config.tcl declaring the former."""
    tech = root / "pdk" / "libs.tech" / "librelane"
    tech.mkdir(parents=True)
    for c in ("min", "nom", "max"):
        (tech / f"rules.openrcx.fix.{c}").write_text("# declared\n")
        (tech / f"rules.openrcx.fix.{c}.magic").write_text("# undeclared\n")
    var = {"nom": "RCX_RULES", "min": "RCX_RULES_MIN", "max": "RCX_RULES_MAX"}
    (tech / "config.tcl").write_text("".join(
        f'set ::env({var[c]}) "$::env(PDK_ROOT)/$::env(PDK)/libs.tech/'
        f'librelane/rules.openrcx.fix.{c}"\n' for c in declare))
    ref = root / "pdk" / "libs.ref" / "fix"
    ref.mkdir(parents=True)
    return str(ref / "tech.lef")


def _deck(tmp_path, monkeypatch, declare=("nom", "max"), **kw):
    monkeypatch.setattr(R, "_docker_exec", _host_exec)
    tlef = _stage(tmp_path, declare)
    decl = R._openrcx_ruleset_declaration(_pdk(tlef), container="fake")
    return decl, R._post_route_spef_repair_tcl(
        str(tmp_path / "out"), tlef, rcx_declaration=decl, **kw)


def _eval(tmp_path: Path, block: str) -> subprocess.CompletedProcess:
    script = tmp_path / "prs.tcl"
    script.write_text(_STUB + block)
    return subprocess.run(["tclsh", str(script)], capture_output=True,
                          text=True, timeout=60)


@needs_tclsh
def test_the_deck_extracts_with_the_declared_ruleset_not_the_magic_one(
        tmp_path, monkeypatch):
    decl, tcl = _deck(tmp_path, monkeypatch)
    assert decl["status"] == "DECLARED", decl
    assert "glob -nocomplain" not in tcl
    r = _eval(tmp_path, tcl)
    assert r.returncode == 0, r.stderr
    used = [ln for ln in r.stdout.splitlines() if ln.startswith("EXTRACT_WITH")]
    assert used, r.stdout
    for ln in used:
        model = ln.split("-ext_model_file ")[1].split()[0]
        assert model.endswith(("/rules.openrcx.fix.nom",
                               "/rules.openrcx.fix.max")), ln
    assert "SPEF_REPAIR_CAPTABLE: " in r.stdout
    assert r.stdout.split("SPEF_REPAIR_CAPTABLE: ")[1].split()[0].endswith(
        "/rules.openrcx.fix.nom")
    assert r.stdout.split("SPEF_REPAIR_SIGNOFF_CAPTABLE: ")[1].split()[0] \
        .endswith("/rules.openrcx.fix.max")
    assert "SPEF_MEASURE_COMPLETE" in r.stdout


@needs_tclsh
def test_no_declared_max_uses_the_declared_nom_and_says_so(
        tmp_path, monkeypatch):
    _decl, tcl = _deck(tmp_path, monkeypatch, declare=("nom",))
    r = _eval(tmp_path, tcl)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split("SPEF_REPAIR_SIGNOFF_CAPTABLE: ")[1].split()[0] \
        .endswith("/rules.openrcx.fix.nom")
    assert "SPEF_REPAIR_SIGNOFF_RULESET_NOM:" in r.stdout


@needs_tclsh
@pytest.mark.parametrize("decl,code", [
    ({"status": "UNREADABLE", "declaration": None, "corners": {},
      "detail": 'declaration probe failed: "docker" [gone]'},
     "RCX_RULESET_DECLARATION_UNREADABLE"),
    ({"status": "DECLARED", "declaration": ["config.tcl"],
      "corners": {"max": {"path": "/pdk/libs.tech/librelane/r.max"}},
      "detail": ""},
     "RCX_NOM_RULESET_UNDECLARED"),
])
def test_an_unreadable_or_nomless_declaration_refuses(tmp_path, decl, code):
    tcl = R._post_route_spef_repair_tcl(
        str(tmp_path / "out"), "/pdk/libs.ref/fix/tech.lef",
        fork_repair_capable=True, rcx_declaration=decl)
    r = _eval(tmp_path, tcl)
    assert r.returncode == 0, r.stderr
    assert f"SPEF_REPAIR_REFUSED: {code}" in r.stdout
    assert "EXTRACT_WITH" not in r.stdout
    assert "SPEF_MEASURE_COMPLETE" not in r.stdout
    assert "SPEF_REPAIR_CAPTABLE" not in r.stdout


def test_step_pnr_hands_the_deck_the_recorded_declaration():
    src = (PROGRAMS / "phase3_one_shot_runner.py").read_text()
    i = src.index("def step_pnr(")
    body = src[i:src.index("\ndef ", i + 10)]
    call = body.index("spef_repair_block = _post_route_spef_repair_tcl(")
    pre = body[:call]
    assert "_prs_rcx_decl = _openrcx_ruleset_declaration(pdk, container)" in pre
    assert "_write_rcx_declaration_record(out_dir, _prs_rcx_decl)" in pre
    assert "rcx_declaration=_prs_rcx_decl" in body[call:call + 800]
