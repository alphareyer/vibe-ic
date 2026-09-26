"""F13 — step 22's OpenRCX ruleset is a DECLARED input, never a glob (§4.05).

THE DEFECT
    The direct extraction path globbed the PDK tree for
    `rules.openrcx.*.<corner>[.magic]` (and IHP's `openrcx/*.<corner>.magic.rules`)
    and PREFERRED the `.magic` file.  Nothing declares that file.  On the 0.3.79
    image the PDK's own LibreLane config declares a different one on every open
    PDK (gf180mcuD: `rules.openrcx.gf180mcuD.<c>`; sky130A: `.<c>.calibre`;
    ihp-sg13g2: `openrcx/IHP_rcx_patterns.rules`).  Measured on spm x gf180mcuD
    (same DEF, same OpenROAD): 9.367 pF total C with the globbed `.magic`
    ruleset vs 6.084 pF with the declared one -- the whole step-22 gap between
    this path and LibreLane OpenROAD.RCX.

THE RULE THE CODE FOLLOWS NOW
    The ruleset per corner is what the PDK's LibreLane config (or, with no such
    config, `pdk_registry.json`) declares, sourced by Tcl the way LibreLane
    sources it, and recorded with where it was declared.  No declaration -> no
    ruleset is guessed.  A declaration that cannot be read -> step 22 refuses.

These tests drive the real resolver: `_docker_exec` is replaced only by a
host shell that runs the exact command the runner builds (base64 -> tclsh),
over a PDK tree staged under tmp_path.  chip/PDK-AGNOSTIC: synthetic names.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import phase3_one_shot_runner as R  # noqa: E402

_PDK = "xpdk"
_SCL = "xpdk_sc"
_BANNER = "[INFO] Final PATH variable: /foss/tools/bin:/usr/bin\n"


def _host_exec(calls=None):
    """The container boundary, faked: run the runner's command on the host."""
    def run(container, cmd, timeout=1800, **_kw):
        if calls is not None:
            calls.append(cmd)
        p = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True,
                           timeout=60)
        return p.returncode, _BANNER + p.stdout, p.stderr
    return run


def _stage(root: Path, config: str | None, files=(), flow="librelane",
           scl_config: str | None = None) -> SimpleNamespace:
    pdk = root / "pdks" / _PDK
    tech = pdk / "libs.tech" / flow
    tech.mkdir(parents=True, exist_ok=True)
    for rel in files:
        f = pdk / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("Extraction Rules for OpenRCX\n")
    if config is not None:
        (tech / "config.tcl").write_text(config)
    if scl_config is not None:
        (tech / _SCL).mkdir(parents=True, exist_ok=True)
        (tech / _SCL / "config.tcl").write_text(scl_config)
    ref = pdk / "libs.ref" / _SCL / "lef"
    ref.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        name=_PDK, tech_lef=str(root / "project/active_via_legalized.tlef"),
        tech_lef_source=None, cell_lef=str(ref / "cells.lef"),
        liberty=str(pdk / "libs.ref" / _SCL / "lib/cells.lib"), cell_gds=None,
        macro_lefs=[], metal_prefix="M")


_ROOT_VAR = "$::env(PDK_ROOT)/$::env(PDK)"
_LEGACY = f'''
set ::env(RCX_RULES) "{_ROOT_VAR}/libs.tech/librelane/rcx_rules.info"
# the PDK re-declares it below; the LAST value is the declaration
set ::env(RCX_RULES) "{_ROOT_VAR}/libs.tech/librelane/rules.openrcx.$::env(PDK).nom"
set ::env(RCX_RULES_MIN) "{_ROOT_VAR}/libs.tech/librelane/rules.openrcx.$::env(PDK).min"
set ::env(RCX_RULES_MAX) "{_ROOT_VAR}/libs.tech/librelane/rules.openrcx.$::env(PDK).max"
# a config that needs the standard-cell library, as the real ones do
set ::env(TECH_LEF) [glob -nocomplain "{_ROOT_VAR}/libs.ref/$::env(STD_CELL_LIBRARY)/lef/*"]
'''
_BOTH_MODELS = [f"libs.tech/librelane/rules.openrcx.{_PDK}.{c}{sfx}"
                for c in ("min", "nom", "max") for sfx in ("", ".magic")]


def test_legacy_declaration_wins_over_a_magic_sibling(tmp_path, monkeypatch):
    """The regression: with both files on disk the glob took `.magic`; the
    declaration names the plain file, per corner, and says where."""
    monkeypatch.setattr(R, "_docker_exec", _host_exec())
    pdk = _stage(tmp_path, _LEGACY, _BOTH_MODELS, scl_config="# scl\n")
    decl = R._openrcx_ruleset_declaration(pdk, "c")
    assert decl["status"] == "DECLARED", decl
    for c in ("min", "nom", "max"):
        v = decl["corners"][c]
        assert v["path"].endswith(f"rules.openrcx.{_PDK}.{c}"), v
        want = {"nom": "RCX_RULES", "min": "RCX_RULES_MIN", "max": "RCX_RULES_MAX"}[c]
        assert v["declared_by"].endswith(f"/libs.tech/librelane/config.tcl:{want}"), v
        assert v["pattern"] == f"{c}_*"
    assert R._discover_openrcx_captables(pdk, "c") == {
        c: decl["corners"][c]["path"] for c in ("min", "nom", "max")}
    assert R._max_captable_c(pdk, "c").endswith(f"rules.openrcx.{_PDK}.max")


def test_existing_entry_points_return_the_declared_file(tmp_path, monkeypatch):
    """Through the two entry points the step-22 corners and the repair decks
    already call -- no new API: the declared plain file, never `.magic`."""
    monkeypatch.setattr(R, "_docker_exec", _host_exec())
    monkeypatch.setattr(R, "_container_ls_paths", lambda c, e, m, timeout=20: [
        ln for ln in subprocess.run(["bash", "-c", f"ls {e} 2>/dev/null"],
                                    capture_output=True, text=True).stdout.split()
        if m in ln])
    pdk = _stage(tmp_path, _LEGACY, _BOTH_MODELS)
    caps = R._discover_openrcx_captables(pdk, "c")
    assert {c: Path(v).name for c, v in caps.items()} == {
        c: f"rules.openrcx.{_PDK}.{c}" for c in ("min", "nom", "max")}, caps
    assert Path(R._max_captable_c(pdk, "c")).name == f"rules.openrcx.{_PDK}.max"


def test_rulesets_dict_is_read_like_librelane(tmp_path, monkeypatch):
    """RCX_RULESETS (a Tcl dict, set conditionally) is the declaration."""
    monkeypatch.setattr(R, "_docker_exec", _host_exec())
    cfg = f'''
set ::env(RCX_RULESETS) [dict create]
dict set ::env(RCX_RULESETS) "nom_*" "{_ROOT_VAR}/libs.tech/librelane/rules.openrcx.$::env(PDK).nom.spef_extractor"
dict set ::env(RCX_RULESETS) "max_*" "{_ROOT_VAR}/libs.tech/librelane/rules.openrcx.$::env(PDK).max.spef_extractor"
if {{ [file exists "{_ROOT_VAR}/libs.tech/librelane/rules.openrcx.$::env(PDK).nom.calibre"] }} {{
  dict set ::env(RCX_RULESETS) "nom_*" "{_ROOT_VAR}/libs.tech/librelane/rules.openrcx.$::env(PDK).nom.calibre"
}}
'''
    files = _BOTH_MODELS + [f"libs.tech/librelane/rules.openrcx.{_PDK}.nom.calibre",
                            f"libs.tech/librelane/rules.openrcx.{_PDK}.nom.spef_extractor",
                            f"libs.tech/librelane/rules.openrcx.{_PDK}.max.spef_extractor"]
    pdk = _stage(tmp_path, cfg, files)
    decl = R._openrcx_ruleset_declaration(pdk, "c")
    assert decl["status"] == "DECLARED", decl
    assert set(decl["corners"]) == {"nom", "max"}, "min is not declared: not guessed"
    assert decl["corners"]["nom"]["path"].endswith(".nom.calibre")
    assert decl["corners"]["max"]["path"].endswith(".max.spef_extractor")
    assert decl["corners"]["nom"]["declared_by"].endswith(":RCX_RULESETS[nom_*]")


def test_a_subdirectory_ruleset_is_found_because_it_is_declared(tmp_path, monkeypatch):
    """IHP-style layout: the declared file lives under `openrcx/`; the
    `<pdk>.nom.magic.rules` sibling the config comments out is not used."""
    monkeypatch.setattr(R, "_docker_exec", _host_exec())
    cfg = f'''
set ::env(RCX_RULESETS) [dict create]
dict set ::env(RCX_RULESETS) "nom_*" "{_ROOT_VAR}/libs.tech/librelane/openrcx/patterns.rules"
#dict set ::env(RCX_RULESETS) "nom_*" "{_ROOT_VAR}/libs.tech/librelane/openrcx/$::env(PDK).nom.magic.rules"
'''
    files = ["libs.tech/librelane/openrcx/patterns.rules"] + [
        f"libs.tech/librelane/openrcx/{_PDK}.{c}.magic.rules" for c in ("min", "nom", "max")]
    pdk = _stage(tmp_path, cfg, files)
    caps = R._discover_openrcx_captables(pdk, "c")
    assert caps == {"nom": str(tmp_path / "pdks" / _PDK /
                               "libs.tech/librelane/openrcx/patterns.rules")}
    assert R._max_captable_c(pdk, "c") == ""


def test_the_scl_config_can_override_and_is_named(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "_docker_exec", _host_exec())
    scl = f'set ::env(RCX_RULES) "{_ROOT_VAR}/libs.tech/librelane/scl.nom"\n'
    pdk = _stage(tmp_path, _LEGACY, _BOTH_MODELS + ["libs.tech/librelane/scl.nom"],
                 scl_config=scl)
    decl = R._openrcx_ruleset_declaration(pdk, "c")
    assert decl["corners"]["nom"]["path"].endswith("/scl.nom")
    assert decl["corners"]["nom"]["declared_by"].endswith(
        f"/librelane/{_SCL}/config.tcl:RCX_RULES")
    assert decl["corners"]["max"]["declared_by"].endswith(
        "/librelane/config.tcl:RCX_RULES_MAX")


def test_an_older_image_declares_it_under_openlane(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "_docker_exec", _host_exec())
    cfg = f'set ::env(RCX_RULES) "{_ROOT_VAR}/libs.tech/openlane/rules.openrcx.$::env(PDK).nom"\n'
    pdk = _stage(tmp_path, cfg, [f"libs.tech/openlane/rules.openrcx.{_PDK}.nom",
                                 f"libs.tech/openlane/rules.openrcx.{_PDK}.nom.magic"],
                 flow="openlane")
    caps = R._discover_openrcx_captables(pdk, "c")
    assert list(caps) == ["nom"] and caps["nom"].endswith(
        f"/libs.tech/openlane/rules.openrcx.{_PDK}.nom")


def test_rule_files_without_a_declaration_are_not_used(tmp_path, monkeypatch):
    """A config that declares no ruleset: the files on disk are not a
    declaration.  (Pre-fix: the glob picked every `.magic` it found.)"""
    monkeypatch.setattr(R, "_docker_exec", _host_exec())
    pdk = _stage(tmp_path, "set ::env(SOMETHING_ELSE) 1\n", _BOTH_MODELS)
    decl = R._openrcx_ruleset_declaration(pdk, "c")
    assert decl["status"] == "DECLARES_NONE", decl
    assert R._discover_openrcx_captables(pdk, "c") == {}
    assert R._max_captable_c(pdk, "c") == ""


def test_no_config_falls_back_to_the_registry_declaration(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "_docker_exec", _host_exec())
    pdk = _stage(tmp_path, None, [f"libs.tech/librelane/rules.openrcx.{_PDK}.nom",
                                  f"libs.tech/librelane/rules.openrcx.{_PDK}.nom.magic"])
    monkeypatch.setattr(R, "_pdk_registry_entry", lambda n: {
        "name": n, "rcx_rules": f"libs.tech/librelane/rules.openrcx.{_PDK}.nom"})
    decl = R._openrcx_ruleset_declaration(pdk, "c")
    assert decl["status"] == "DECLARED", decl
    assert decl["corners"]["nom"]["declared_by"] == f"pdk_registry.json:{_PDK}.rcx_rules"
    assert decl["corners"]["nom"]["path"].endswith(f"rules.openrcx.{_PDK}.nom")


def test_nothing_declared_anywhere_is_undeclared(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "_docker_exec", _host_exec())
    pdk = _stage(tmp_path, None, _BOTH_MODELS)
    monkeypatch.setattr(R, "_pdk_registry_entry", lambda n: None)
    decl = R._openrcx_ruleset_declaration(pdk, "c")
    assert decl["status"] == "UNDECLARED", decl
    assert R._discover_openrcx_captables(pdk, "c") == {}


@pytest.mark.parametrize("cfg,files,why", [
    ("error {broken declaration}\n", _BOTH_MODELS, "broken declaration"),
    (f'set ::env(RCX_RULES) "{_ROOT_VAR}/libs.tech/librelane/absent.nom"\n',
     _BOTH_MODELS, "absent.nom"),
])
def test_an_unreadable_declaration_is_refused(tmp_path, monkeypatch, cfg, files, why):
    monkeypatch.setattr(R, "_docker_exec", _host_exec())
    pdk = _stage(tmp_path, cfg, files)
    decl = R._openrcx_ruleset_declaration(pdk, "c")
    assert decl["status"] == "UNREADABLE" and why in decl["detail"], decl
    assert R._discover_openrcx_captables(pdk, "c") == {}


# ── _emit_spef: the declared file reaches the deck, and is recorded ─────────
def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    pnr = R._pl.pnr_dir(project)
    pnr.mkdir(parents=True)
    (pnr / "top.def").write_text("VERSION 5.8 ;\nDESIGN top ;\nEND DESIGN\n")
    return project


def _emit(tmp_path, monkeypatch, pdk):
    project = _project(tmp_path)
    calls = []
    host = _host_exec(calls)

    def exec_(container, cmd, timeout=1800, **kw):
        if "openroad" in cmd:           # the extraction itself: not executed
            return 1, "control: execution not requested", ""
        return host(container, cmd, timeout, **kw)
    monkeypatch.setattr(R, "_docker_exec", exec_)
    monkeypatch.setattr(R, "_to_container_path", lambda p, c: str(p))
    monkeypatch.setattr(R, "_def_reopen_extra_lefs_c", lambda d, p, c: [])
    ex = R._pl.extracted_dir(project)
    ex.mkdir(parents=True, exist_ok=True)
    notes: list = []
    ok = R._emit_spef(project, "top", pdk, "c", ex / "top.spef", notes)
    return ok, ex, notes


def test_emit_spef_deck_reads_the_declared_ruleset_and_records_it(tmp_path, monkeypatch):
    pdk = _stage(tmp_path, _LEGACY, _BOTH_MODELS)
    _ok, ex, _notes = _emit(tmp_path, monkeypatch, pdk)
    tcl = (ex / "extract_top.tcl").read_text()
    code = [ln for ln in tcl.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    declared = str(tmp_path / "pdks" / _PDK / f"libs.tech/librelane/rules.openrcx.{_PDK}.nom")
    assert f"set _rules {{{declared}}}" in code, "the deck must read the DECLARED file"
    assert not any("glob" in ln for ln in code), "no ruleset may be globbed"
    assert any("extract_parasitics -ext_model_file $_rules" in ln for ln in code)
    rec = json.loads((ex / "rcx_ruleset_declaration.json").read_text())
    assert rec["status"] == "DECLARED" and rec["step"] == "22"
    assert rec["corners"]["nom"]["path"] == declared
    assert rec["corners"]["nom"]["declared_by"].endswith("config.tcl:RCX_RULES")


def test_emit_spef_refuses_an_unreadable_declaration(tmp_path, monkeypatch):
    pdk = _stage(tmp_path, "error {broken}\n", _BOTH_MODELS)
    ok, ex, notes = _emit(tmp_path, monkeypatch, pdk)
    assert ok is False
    assert not (ex / "extract_top.tcl").exists(), "no extraction on a refused input"
    assert any("RCX_RULESET_DECLARATION_UNREADABLE" in n for n in notes), notes
    assert json.loads((ex / "rcx_ruleset_declaration.json").read_text())["status"] == "UNREADABLE"


def test_emit_spef_refuses_when_only_other_corners_are_declared(tmp_path, monkeypatch):
    cfg = f'''
set ::env(RCX_RULESETS) [dict create]
dict set ::env(RCX_RULESETS) "max_*" "{_ROOT_VAR}/libs.tech/librelane/rules.openrcx.$::env(PDK).max"
'''
    pdk = _stage(tmp_path, cfg, _BOTH_MODELS)
    ok, ex, notes = _emit(tmp_path, monkeypatch, pdk)
    assert ok is False and any("RCX_NOM_RULESET_UNDECLARED" in n for n in notes), notes


def test_emit_spef_without_a_declaration_takes_the_disclosed_rule_less_tier(tmp_path, monkeypatch):
    pdk = _stage(tmp_path, None, _BOTH_MODELS)
    monkeypatch.setattr(R, "_pdk_registry_entry", lambda n: None)
    _ok, ex, notes = _emit(tmp_path, monkeypatch, pdk)
    tcl = (ex / "extract_top.tcl").read_text()
    assert "set _rules {}" in tcl, "no declaration -> no ruleset (LEF-RC tier)"
    assert "extract_parasitics -lef_rc -version 2.0" in tcl
    assert any("RCX_RULESET_UNDECLARED" in n for n in notes), notes


def test_the_spef_provenance_carries_the_declaration(tmp_path, monkeypatch):
    """§4.05: the SPEF's provenance names the ruleset it was extracted with and
    where the PDK declared it -- the record `_emit_spef` wrote beside it."""
    pdk = _stage(tmp_path, _LEGACY, _BOTH_MODELS)
    _ok, ex, _notes = _emit(tmp_path, monkeypatch, pdk)
    inputs = R._rcx_provenance_inputs(ex / "top.spef")
    nom = inputs["openrcx_ruleset"]["corners"]["nom"]
    assert nom["path"].endswith(f"rules.openrcx.{_PDK}.nom")
    assert nom["declared_by"].endswith("config.tcl:RCX_RULES")
    # the step-22 provenance entry is built from exactly this helper
    src = (PROG / "phase3_one_shot_runner.py").read_text()
    block = src[src.index("# --- SPEF provenance (Step 22 parasitic extraction)"):]
    block = block[:block.index("_rmeas.attach(project, spef_entry)")]
    assert '_rcx_inputs = _rcx_provenance_inputs(spef_out)' in block
    assert 'spef_entry["inputs"] = _rcx_inputs' in block
    assert R._rcx_provenance_inputs(tmp_path / "elsewhere/x.spef") == {}


def test_a_failed_re_extraction_does_not_keep_an_earlier_spef(tmp_path, monkeypatch):
    """Measured in the chained spm run: read_def aborted, `_emit_spef` saw the
    earlier `.magic`-ruleset SPEF still on disk and reported success, and the
    declaration beside it would have vouched for it.  A failed extraction now
    leaves no SPEF, nominal or per corner."""
    pdk = _stage(tmp_path, _LEGACY, _BOTH_MODELS)
    project = _project(tmp_path)
    ex = R._pl.extracted_dir(project)
    (ex / "corners").mkdir(parents=True)
    (ex / "top.spef").write_text("*SPEF earlier extraction\n")
    for c in ("min", "nom", "max"):
        (ex / f"corners/top.{c}.spef").write_text("*SPEF earlier corner\n")
    host = _host_exec()
    monkeypatch.setattr(R, "_docker_exec", lambda c, cmd, timeout=1800, **kw: (
        (1, "[ERROR ODB-0421] DEF parser returns an error!", "") if "openroad" in cmd
        else host(c, cmd, timeout, **kw)))
    monkeypatch.setattr(R, "_to_container_path", lambda p, c: str(p))
    monkeypatch.setattr(R, "_def_reopen_extra_lefs_c", lambda d, p, c: [])
    notes: list = []
    assert R._emit_spef(project, "top", pdk, "c", ex / "top.spef", notes) is False
    assert not (ex / "top.spef").exists()
    assert R._emit_spef_corners(project, "top", pdk, "c", ex / "corners", notes) == {}
    assert not list((ex / "corners").glob("top.*.spef"))
