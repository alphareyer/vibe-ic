"""vibe-ic#2166 — the PnR/GDS cache-hit decision is a FUNCTION, and it is DRIVEN here.

THE DEFECT, MEASURED (lane cz2157, #2157, 8HD-9).  #2157's sweep of unreadable
0-byte `antenna_iter_*.rpt` lives inside `step_pnr`'s approach loop.  There is a
second route to the same escape that never reaches it: `main()`'s cache-hit
branch (`DEF already present … skipped re-run`) does not call `step_pnr` AT ALL,
so a cached PnR directory carrying `antenna_iter_0.rpt` at 0 bytes ships it to
`eda_report_audit`, which then reports NOT_MEASURED over evidence nobody could
open.  The GDS stage has the same shape.

WHY THE FIX IS AN EXTRACTION AND NOT A LINE.  The decision used to be written
twice INLINE in `main()`, and `main()` is not reachable from a test with a
prepared directory — so the only assertion anyone could write about it was a
grep of the source.  #2157 measured exactly what that is worth: four
source-grep assertions were ALL GREEN on the tree that carried the escape they
existed to prevent.  So the decision moved into `_cached_stage_decision`, whose
whole point is that everything below this line DRIVES it against a directory on
disk and asserts on the ARTEFACT.

MUTATIONS THESE MUST KILL (all run, all caught — see the lane's MEASUREMENTS.md):
  * removing the sweep from the accept path;
  * moving the sweep onto the REJECT path (a rejected directory is about to be
    rebuilt by `step_pnr`, whose own sweep owns it);
  * sweeping unconditionally, i.e. dropping the `accept` test;
  * dropping the `blocked_by` conjunct the GDS site uses for `_pnr_reran`;
  * re-inlining either call site back into `main()`.
"""

import ast
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import phase3_one_shot_runner as R  # noqa: E402
from _delivery_declaration import declare_delivery as _declare  # noqa: E402

SRC = (PROGRAMS / "phase3_one_shot_runner.py").read_text()

DIE = "200x200"
UTIL = 0.4


import pytest  # noqa: E402





class _IdentityArgs:
    """The knobs R-0924-3 r3 folds into a step's identity — `--spare-density`
    and the env vars are inputs the step reads that are not files, so the
    fixtures must name them as a real invocation would."""
    spare_density = 0.02
    container = ""


def _pdk(root):
    """A PDK whose declared files can actually be read — R-0924-3 hashes them,
    and an unreadable PDK is (correctly) never a match."""
    # OUTSIDE the project, which is where a PDK lives. R-0924-3 r3 added a
    # structural guard: a PDK path inside the run directory is this run's own
    # derivation (the VIA-patch legalizer stages one there), not a PDK input,
    # and it refuses. Writing the fixture's PDK inside the project tripped it —
    # correctly.
    d = Path(root).parent / "_pdk_outside"
    d.mkdir(parents=True, exist_ok=True)
    lib, tlef, clef = d / "tt.lib", d / "tech.lef", d / "cells.lef"
    for f, text in ((lib, "library(t){}\n"), (tlef, "VERSION 5.8 ;\n"),
                    (clef, "MACRO unit\n")):
        if not f.is_file():
            f.write_text(text)
    return R.PdkConfig(name="testpdk", liberty=str(lib), tech_lef=str(tlef),
                       cell_lef=str(clef), cell_gds=None, site="unit",
                       drc_deck=None)


def _span_inputs(project, top: str = "top") -> None:
    """Everything the three SPANS declare that they do not produce themselves.

    R-0924-3 r2 CATCH-UP. The review (wcxu446tu) found that keying a kind on
    the ONE step declaring its artefact made pnr hash `post_hold.def` — a file
    `step_pnr` writes itself — so a new netlist, SDC or slot never invalidated
    the routed DEF. Freshness is now keyed on the whole SPAN the runner
    function implements (pnr = 15..21, gds = 26.5ic..37), so "a tree from a
    previous run" means a tree carrying what those spans READ: the slot
    declaration, the RTL, the SDC, the netlist, the routed DEF, the spare-cell
    record and the SPEF. Same catch-up as the ones above, for the same reason.
    """
    from pathlib import Path as _P
    project = _P(project)
    for rel, text in (
        ("phase2/stage1/rtl/%s.v" % top, "module %s(); endmodule\n" % top),
        ("phase2/stage2/constraints/%s.sdc" % top,
         "create_clock -period 10\n"),
        ("phase2/stage2/synth/netlist.v", "module %s(); endmodule\n" % top),
        # r4: the netlist PnR ACTUALLY reads, per `pnr_input_netlist`.
        ("phase2/stage2/synth/%s_synth.v" % top,
         "module %s(); endmodule\n" % top),
        ("phase3/stage3/pnr/routed.def", "VERSION 5.8 ;\nEND DESIGN\n"),
        ("phase3/stage3/pnr/spare_cells.json", "{}\n"),
        ("phase3/stage3/extracted/parasitic.spef", "*SPEF\n"),
    ):
        p = project / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.is_file():
            p.write_text(text)


def _seed_recording(kind: str) -> None:
    """R-0924-3 r5: `code` comes from what the step RAN, and a step with no
    recording gets NO cache — which is the point of r5. A fixture that stamps
    a tree without running the step through `_recorded` must therefore supply
    the recording a real run would have left. The recorder's own behaviour is
    covered by the r5 tests that drive it for real."""
    import _step_recorder as _sr
    # DERIVED, not invented: a hand-written digest would not survive
    # re-derivation (the check side recomputes these keys from CURRENT
    # source), so the stand-in is computed the same way a real recording is.
    _d, _err = _sr.check_digests(R.PROGRAMS_DIR / "_step_identity.py", [])
    assert not _err, _err
    R._STEP_RECORDING[kind] = ({
        "_step_identity.py": _d,
        "__engine_env__": {name: _sr._engine_marker(name)
                           for name in _sr.ENGINE_ENV},
    }, "")


def _valid_cache(tmp_path, kind="pnr", artefact="top.def",
                 deliverable="HARDMACRO"):
    """A directory that is GENUINELY reusable — and stays that way.

    Every key the decision reads is satisfied by the runner's OWN writers, so
    this state does not depend on the defect and cannot become unreachable when
    the defect is fixed: `_write_pnr_args_sidecar` for the geometry key and
    `_write_producer_identity` for the step key. That is what makes the accept
    assertions below non-vacuous.

    R-0924-3 CATCH-UP: the step key is now the STEP's identity, so a reusable
    directory is one whose declared inputs are present, whose tool ledger
    exists and whose PDK can be read. All three are written here — by the
    runner's own writer for the stamp, and as real files for the rest. Nothing
    is stubbed past the decision: the decision still runs for real.
    """
    project = tmp_path / "proj"
    out_dir = project / "phase3" / "stage3" / "pnr"
    out_dir.mkdir(parents=True, exist_ok=True)
    # step 21 reads post_hold.def; step 37 reads filled.def OR metal_fill.done
    (out_dir / "post_hold.def").write_text("VERSION 5.8 ;\nEND DESIGN\n")
    (out_dir / "metal_fill.done").write_text("fill complete\n")
    (project / "provenance.jsonl").write_text("".join(
        __import__("json").dumps(
            {"tool": t, "version": v, "outputs": {o: "sha256:" + "0" * 64}}
        ) + "\n" for t, v, o in (
            ("openroad", "2.0", "phase3/stage3/pnr/top.def"),
            ("klayout", "0.28", "phase3/stage4/gds/top.gds"))))
    # The DEF the stream-out reads. A GDS is always streamed FROM a DEF, so a
    # directory holding a GDS and no DEF is not a state a real run leaves —
    # and R-0924-3 r2 makes that DEF part of the GDS step's identity, because
    # other steps promote it IN PLACE and no `required_inputs` entry names it.
    top_def = out_dir / "top.def"
    if not top_def.is_file():
        top_def.write_text("VERSION 5.8 ;\nEND DESIGN\n")
    _span_inputs(project, "top")
    # The slot declaration is step 0.5ic's output and is UNCONDITIONAL, so the
    # span must be able to hash it. HARDMACRO keeps this fixture's original
    # premise: it is not a chip path, so the pad-ring clause stays out of the
    # way of the sweep these tests are about. The one test that DOES want the
    # chip path declares DIE for itself.
    _declare(project, deliverable)
    R._write_pnr_args_sidecar(out_dir, DIE, UTIL)
    _seed_recording(kind)
    R._write_producer_identity(
        out_dir, kind, project=project, pdk=_pdk(project), container="",
        top="top", args=_IdentityArgs())
    art = out_dir / artefact
    art.write_text("VERSION 5.8 ;\n")
    return project, out_dir, art


def _decide(project, out_dir, art, **kw):
    kw.setdefault("kind", "pnr")
    kw.setdefault("top", "top")
    kw.setdefault("die_um", DIE)
    kw.setdefault("util", UTIL)
    kw.setdefault("pdk", _pdk(project))
    kw.setdefault("container", "")
    kw.setdefault("args", _IdentityArgs())
    return R._cached_stage_decision(project, out_dir, art, **kw)


def _empty_antenna(out_dir, *names):
    for n in names:
        (out_dir / n).write_bytes(b"")


def _antenna_left(out_dir):
    return sorted(p.name for p in out_dir.glob("antenna_iter_*.rpt"))


# ── the branch is reachable at all ─────────────────────────────────────────

def test_a_genuinely_valid_cached_directory_is_accepted(tmp_path):
    """NON-VACUITY. If this cannot be made to ACCEPT, every assertion below is
    about a branch no test ever enters — which was the whole problem."""
    project, out_dir, art = _valid_cache(tmp_path)
    d = _decide(project, out_dir, art)
    assert d.accept is True, d.reason
    assert "geometry unchanged" in d.reason, d.reason
    assert d.dropped == ()


def test_a_directory_with_no_geometry_sidecar_is_refused(tmp_path):
    """THE OTHER DIRECTION. A decision that cannot say no is not a decision."""
    project, out_dir, art = _valid_cache(tmp_path)
    (out_dir / R._PNR_ARGS_SIDECAR).unlink()
    d = _decide(project, out_dir, art)
    assert d.accept is False
    assert "geometry unknown" in d.reason, d.reason


def test_a_missing_artefact_is_refused(tmp_path):
    project, out_dir, art = _valid_cache(tmp_path)
    art.unlink()
    assert _decide(project, out_dir, art).accept is False


# ── THE ARTEFACT ASSERTION — the escape #2166 was filed for ────────────────

def test_accepting_a_cached_run_drops_the_zero_byte_antenna_reports(tmp_path):
    """The published shape of #2157, arriving through the cache-hit door."""
    project, out_dir, art = _valid_cache(tmp_path)
    _empty_antenna(out_dir, "antenna_iter_0.rpt", "antenna_iter_1.rpt")
    d = _decide(project, out_dir, art)
    assert d.accept is True, d.reason
    assert _antenna_left(out_dir) == [], _antenna_left(out_dir)
    assert d.dropped == ("antenna_iter_0.rpt", "antenna_iter_1.rpt"), d.dropped


def test_the_step_detail_names_what_the_reuse_removed(tmp_path):
    """A reused directory may never quietly differ from the one that was cached.
    The disclosure the step PUBLISHES has to carry it, not only a log line."""
    project, out_dir, art = _valid_cache(tmp_path)
    _empty_antenna(out_dir, "antenna_iter_0.rpt")
    d = _decide(project, out_dir, art)
    assert "antenna_iter_0.rpt" in d.reason, d.reason
    assert "antenna_iter_0.rpt" in d.producer_reason, d.producer_reason


def test_an_antenna_report_with_content_survives_the_reuse(tmp_path):
    """THE CONTROL. The sweep is on SIZE. A cached run that HAD antenna
    violations keeps the report that says so."""
    project, out_dir, art = _valid_cache(tmp_path)
    (out_dir / "antenna_iter_0.rpt").write_text("openroad\nNet: n1\n" + "x" * 400)
    d = _decide(project, out_dir, art)
    assert d.accept is True
    assert _antenna_left(out_dir) == ["antenna_iter_0.rpt"]
    assert d.dropped == ()


def test_a_refused_directory_is_left_exactly_as_it_was(tmp_path):
    """THE OTHER CONTROL, and it is the one that pins the sweep to ACCEPT. A
    refused directory is about to be rebuilt by `step_pnr`, whose own approach
    loop owns the sweep (#2157). Sweeping here would delete evidence from a run
    that is still the last completed one, for no gain."""
    project, out_dir, art = _valid_cache(tmp_path)
    (out_dir / R._PNR_ARGS_SIDECAR).unlink()
    _empty_antenna(out_dir, "antenna_iter_0.rpt", "antenna_iter_1.rpt")
    before = sorted(p.name for p in out_dir.iterdir())
    d = _decide(project, out_dir, art)
    assert d.accept is False
    assert sorted(p.name for p in out_dir.iterdir()) == before
    assert d.dropped == ()


def test_only_the_antenna_iteration_reports_are_swept(tmp_path):
    """POPULATION CONTROL. The other reports a PnR directory legitimately
    leaves at 0 bytes must survive — for them, empty IS the answer."""
    project, out_dir, art = _valid_cache(tmp_path)
    siblings = ("sdr_drv.rpt", "routed_router.drc.rpt",
                "pnr_fanout_root_candidates.rpt")
    _empty_antenna(out_dir, "antenna_iter_0.rpt", *siblings)
    d = _decide(project, out_dir, art)
    assert d.accept is True
    left = sorted(p.name for p in out_dir.iterdir())
    for s in siblings:
        assert s in left, (s, left)
    assert "antenna_iter_0.rpt" not in left


# ── the GDS twin, same door ────────────────────────────────────────────────

def test_the_gds_stage_accepts_and_sweeps_the_same_directory(tmp_path):
    project, out_dir, art = _valid_cache(tmp_path, kind="gds",
                                         artefact="top.gds")
    _empty_antenna(out_dir, "antenna_iter_0.rpt")
    d = _decide(project, out_dir, art, kind="gds")
    assert d.accept is True, d.reason
    assert _antenna_left(out_dir) == []
    assert d.dropped == ("antenna_iter_0.rpt",)


def test_the_gds_stage_refuses_when_the_caller_says_pnr_reran(tmp_path):
    """`_pnr_reran` is a fact about THIS run's plan, not about the directory,
    so it is passed IN. #593: a GDS cached from the previous DEF must never be
    shipped after PnR re-ran — and a refusal sweeps nothing."""
    project, out_dir, art = _valid_cache(tmp_path, kind="gds",
                                         artefact="top.gds")
    _empty_antenna(out_dir, "antenna_iter_0.rpt")
    d = _decide(project, out_dir, art, kind="gds",
                blocked_by="PnR re-ran in this session")
    assert d.accept is False
    assert "PnR re-ran in this session" in d.reason
    assert _antenna_left(out_dir) == ["antenna_iter_0.rpt"]


def test_the_pad_ring_clause_belongs_to_the_pnr_stage_only(tmp_path):
    """BEHAVIOUR PRESERVED FROM THE TWO INLINE COPIES, and driven so that it
    can fail. The PnR site carried a chip-path pad-ring conjunct; the GDS site
    never did. Driven on a project that DOES request a chip path (canonical
    step 15.5ic's own condition: `input/submission_template/SELF_TAPEOUT.txt`)
    and has no pad-ring route evidence, so the two stages must DISAGREE about
    the same directory — which a single shared conjunct could not produce."""
    # A pad ring is die furniture (#2112): a HARDMACRO never requests one, so
    # this case declares the DIE it is actually about — and declares it BEFORE
    # the stamp, because the declaration is one of the inputs the stamp hashes.
    project, out_dir, art = _valid_cache(tmp_path, deliverable="DIE")
    tmpl = project / "input" / "submission_template"
    tmpl.mkdir(parents=True, exist_ok=True)
    (tmpl / "SELF_TAPEOUT.txt").write_text("x\n")
    assert R._chip_path_requests_pad_ring(project) is True
    assert R._pad_ring_route_cache_valid(project, "top") is False
    _seed_recording("gds")
    R._write_producer_identity(out_dir, "gds", project=project,
                               pdk=_pdk(project), container="",
                               top="top", args=_IdentityArgs())
    gds = out_dir / "top.gds"
    gds.write_text("HEADER\n")
    _empty_antenna(out_dir, "antenna_iter_0.rpt")

    pnr = _decide(project, out_dir, art, kind="pnr")
    assert pnr.accept is False, pnr.reason
    assert "pad-ring route evidence" in pnr.reason, pnr.reason
    # and a refusal sweeps nothing, so the report is still there for the GDS
    # stage to inherit — which is what makes the next assertion meaningful
    assert _antenna_left(out_dir) == ["antenna_iter_0.rpt"]

    gdsd = _decide(project, out_dir, gds, kind="gds")
    assert gdsd.accept is True, gdsd.reason
    assert "pad-ring" not in gdsd.reason, gdsd.reason
    assert gdsd.dropped == ("antenna_iter_0.rpt",)


# ── the cheap sibling: the decision must stay OUT of main() ────────────────

def test_both_cache_hit_sites_call_the_extracted_decision():
    """A SIBLING, not the guard — the driven tests above are the guard. Read
    with `ast`: if either site re-inlines the decision, `main()` becomes
    undrivable again and every assertion above stops describing the flow."""
    tree = ast.parse(SRC)
    main = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    called = [n for n in ast.walk(main)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    names = [c.func.id for c in called]
    assert names.count("_cached_stage_decision") == 2, (
        "both the PnR and the GDS cache-hit site must go through the one "
        "drivable decision; found %d call(s)"
        % names.count("_cached_stage_decision"))
    assert "_pnr_cache_valid_for" not in names, (
        "the geometry key is being read inline in main() again — that is the "
        "shape #2166 removed")


def test_the_sweep_is_reached_from_the_decision():
    """The decision is the only thing standing between a cached directory and
    the audit, so it must be what calls the sweep."""
    tree = ast.parse(SRC)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef)
              and n.name == "_cached_stage_decision")
    assert any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == "_drop_empty_antenna_reports"
               for n in ast.walk(fn))
