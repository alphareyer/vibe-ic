"""SPM-SI-1 — the OpenRCX captable must be found under BOTH shipped layouts.

THE DEFECT
    `phase3_one_shot_runner` discovered the OpenRCX extraction model with a
    SINGLE naming convention, the open_pdks one:

        libs.tech/{librelane,openlane}/rules.openrcx.<pdk>.<corner>.magic

    IHP-Open-PDK ships the same kind of file one level DEEPER and with the
    tokens REVERSED:

        libs.tech/librelane/openrcx/<pdk>.<corner>.magic.rules

    So on ihp-sg13g2 the glob returned nothing, the emitted deck fell through to
    the `-lef_rc` branch (per-layer R + area/fringe C, all lumped TO GROUND) and
    the SPEF came out with ZERO coupling capacitors. Everything downstream that
    reasons about inter-net coupling then became VACUOUS-BY-CONSTRUCTION: the
    signal-integrity screen reported "487 nets, 0 coupling pairs" and could
    never report anything else, on a PDK that does ship a full coupling model
    (`Metal 1 OVER 0` carries a distance-indexed coupling column).

    A check that cannot fail is worse than no check, so this is pinned here.

WHAT IS ASSERTED
    * both conventions are globbed, at every discovery site;
    * a PDK using ONLY the IHP layout resolves (the regression);
    * a PDK using ONLY the open_pdks layout still resolves (no behaviour change);
    * when both exist the pre-existing open_pdks choice still wins, so no PDK
      that resolves today silently starts resolving somewhere else.

chip/PDK-AGNOSTIC: no chip, vendor or corner literal drives the resolution.
"""
import glob as _glob
import shlex as _shlex
import sys
from pathlib import Path

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import phase3_one_shot_runner as R  # noqa: E402

_CORNERS = ("min", "nom", "max")


def _fake_ls(_container, ls_expr, must_contain, timeout=20):
    """Host stand-in for `_container_ls_paths` (test container path == host)."""
    hits = []
    for pat in _shlex.split(ls_expr):
        for p in sorted(_glob.glob(pat)):
            if p.startswith("/") and must_contain in p and p not in hits:
                hits.append(p)
    return hits


def _stage_openpdks(root: Path, subdir: str = "librelane") -> str:
    """open_pdks / asap7 layout: rules.openrcx.<pdk>.<corner>.magic"""
    d = root / "pdk" / "libs.tech" / subdir
    d.mkdir(parents=True, exist_ok=True)
    for c in _CORNERS:
        (d / f"rules.openrcx.sky130A.{c}.magic").write_text("# captable\n")
    ref = root / "pdk" / "libs.ref" / "fix"
    ref.mkdir(parents=True, exist_ok=True)
    return str(ref / "tech.lef")


def _stage_ihp(root: Path, subdir: str = "librelane") -> str:
    """IHP-Open-PDK layout: openrcx/<pdk>.<corner>.magic.rules"""
    d = root / "pdk" / "libs.tech" / subdir / "openrcx"
    d.mkdir(parents=True, exist_ok=True)
    for c in _CORNERS:
        (d / f"ihp-sg13g2.{c}.magic.rules").write_text("# captable\n")
    ref = root / "pdk" / "libs.ref" / "fix"
    ref.mkdir(parents=True, exist_ok=True)
    return str(ref / "tech.lef")


def _declare(root: Path, rel_by_corner: dict, subdir: str = "librelane") -> None:
    """F13: the PDK's `libs.tech/<subdir>/config.tcl` DECLARES these files
    (RCX_RULESETS, the form both open_pdks and IHP-Open-PDK use)."""
    d = root / "pdk" / "libs.tech" / subdir
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.tcl").write_text("set ::env(RCX_RULESETS) [dict create]\n" + "".join(
        f'dict set ::env(RCX_RULESETS) "{c}_*" '
        f'"$::env(PDK_ROOT)/$::env(PDK)/libs.tech/{subdir}/{rel}"\n'
        for c, rel in rel_by_corner.items()))


_IHP = {c: f"openrcx/ihp-sg13g2.{c}.magic.rules" for c in _CORNERS}
_OPENPDKS = {c: f"rules.openrcx.sky130A.{c}.magic" for c in _CORNERS}


def _host_exec(container, cmd, timeout=20, **_kw):
    """The container boundary, faked: the runner's command runs on the host."""
    import subprocess
    p = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True,
                       timeout=60)
    return p.returncode, p.stdout, p.stderr


def _pdk_with(tech_lef: str):
    class _P:
        name = "fixture_pdk"
    p = _P()
    p.tech_lef = tech_lef
    return p


# F13 (§4.05): the ruleset is the one the PDK DECLARES, so each layout below
# is also declared by its config; the properties are unchanged -- an IHP
# layout resolves (never the coupling-free -lef_rc fall-through), an older
# image's openlane tree resolves, open_pdks still resolves, and a second file
# convention on disk never re-points a PDK (the declaration decides).

# ── the regression: IHP-only layout must resolve ─────────────────────────────
def test_discover_captables_finds_ihp_openrcx_subdir_layout(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "_docker_exec", _host_exec)
    tlef = _stage_ihp(tmp_path)
    _declare(tmp_path, _IHP)
    out = R._discover_openrcx_captables(_pdk_with(tlef), container="fake")
    assert set(out) == set(_CORNERS), (
        f"IHP openrcx/<pdk>.<corner>.magic.rules layout not discovered: {out}")
    for c in _CORNERS:
        assert out[c].endswith(f".{c}.magic.rules"), out[c]
        assert "/libs.tech/librelane/openrcx/" in out[c], out[c]


def test_ihp_layout_also_found_under_openlane_subdir(tmp_path, monkeypatch):
    """Backward-compat: an older image puts the same tree under openlane/."""
    monkeypatch.setattr(R, "_docker_exec", _host_exec)
    tlef = _stage_ihp(tmp_path, subdir="openlane")
    _declare(tmp_path, _IHP, subdir="openlane")
    out = R._discover_openrcx_captables(_pdk_with(tlef), container="fake")
    assert set(out) == set(_CORNERS), out
    for c in _CORNERS:
        assert "/libs.tech/openlane/openrcx/" in out[c], out[c]


# ── no behaviour change for PDKs that already resolved ───────────────────────
def test_openpdks_layout_still_resolves(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "_docker_exec", _host_exec)
    tlef = _stage_openpdks(tmp_path)
    _declare(tmp_path, _OPENPDKS)
    out = R._discover_openrcx_captables(_pdk_with(tlef), container="fake")
    assert set(out) == set(_CORNERS), out
    for c in _CORNERS:
        assert out[c].endswith(f".{c}.magic"), out[c]


def test_openpdks_layout_wins_when_both_present(tmp_path, monkeypatch):
    """A second convention on disk must not re-point a PDK that already
    resolved — the declared open_pdks model stays the one used."""
    monkeypatch.setattr(R, "_docker_exec", _host_exec)
    tlef = _stage_openpdks(tmp_path)
    _stage_ihp(tmp_path)
    _declare(tmp_path, _OPENPDKS)
    out = R._discover_openrcx_captables(_pdk_with(tlef), container="fake")
    assert set(out) == set(_CORNERS), out
    for c in _CORNERS:
        assert out[c].endswith(f".{c}.magic"), (
            f"IHP layout hijacked a PDK that already resolved: {out[c]}")


# ── every discovery site resolves both conventions ───────────────────────────
def test_max_captable_helper_globs_both_conventions(tmp_path, monkeypatch):
    """_max_captable_c must not lose an IHP-layout PDK's max-corner captable.
    (F13: it returns the DECLARED max ruleset; it no longer globs.)"""
    monkeypatch.setattr(R, "_docker_exec", _host_exec)
    tlef = _stage_ihp(tmp_path)
    _declare(tmp_path, _IHP)
    got = R._max_captable_c(_pdk_with(tlef), "fake")
    assert got.endswith("/libs.tech/librelane/openrcx/ihp-sg13g2.max.magic.rules"), (
        "_max_captable_c lost the IHP-layout max-corner captable: " + repr(got))


def test_emitted_spef_decks_glob_both_conventions():
    """Every emitted TCL that still discovers a captable by glob must try both
    layouts; the step-22 deck resolves it by declaration and globs nothing.

    Guards the exact failure mode: a deck that globs only `rules.openrcx.*`
    falls through to `-lef_rc` and produces a coupling-free SPEF."""
    src = (PROG / "phase3_one_shot_runner.py").read_text()
    # the remaining glob deck (post-route measure-only extraction)
    i = src.find("def _post_route_spef_repair_tcl(")
    body = src[i:src.find("\ndef ", i + 10)]
    assert "rules.openrcx.*.nom.magic" in body, body[:400]
    assert "openrcx/*.nom.magic.rules" in body, (
        "the post-route deck globs one convention only — an IHP-layout PDK "
        "would still degrade to a coupling-free SPEF there")
    # the step-22 deck: no glob; the declared ruleset (either layout) is read
    j = src.find("def _emit_spef(")
    spef = src[j:src.find("\ndef ", j + 10)]
    assert "glob -nocomplain" not in spef, "step 22 must not glob its ruleset"
    assert "set _rules {{{rules_nom}}}" in spef
    assert "_openrcx_ruleset_declaration(pdk, container)" in spef
