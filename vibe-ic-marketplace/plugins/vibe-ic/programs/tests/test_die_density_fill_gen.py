#!/usr/bin/env python3
"""Die-wide dummy fill: the fill frame must be the DIE, not the layout bbox.

WHAT THIS DEFENDS, and where the bar comes from
-----------------------------------------------
Not this repo's opinion. The shuttle operator's own precheck container
(`ghcr.io/wafer-space/gf180mcu-precheck`, image digest
sha256:f6c0cb88efce8769ec87de5a2035ada731fd8fffb1b3e5e1968078f6dd191c2f), run
2026-08-20 against a sealed gf180mcuD 0.5x0.5-slot die this flow produced,
returned rc=1 with 8 KLayout density errors — DCF.1b, PL.8, M1.4, M2.4, M3.4,
M4.4, M5.4, MT.3 — every one reported against the whole die polygon
(0,0;1936,2531).

The fill was not missing. The flow's own `cmp_fill_emit.json` for that run reads
`metal2 0.0036 -> 0.4330, "reached": true`, and its `metal_density.json` reads
`"die_area_um2": 1732693` — which is the CORE bbox, 35.4 % of the 4900016 um2
die. Measured over the DIE instead, the same layers were COMP 3.04 %, Poly2
0.12 %, Metal1 8.26 %, Metal2 18.21 %, Metal3 17.97 %, Metal4 18.45 %,
Metal5 18.48 %, against floors of 25/14/30/30/30/30/30 in the PDK's own
`density.rb`. The fill was scoped to the wrong rectangle and nothing measured
the right one.

Running the PDK's OWN generator (`libs.tech/klayout/tech/scripts/fill_all.rb`)
over the sealed die took those to 26.38 / 29.35 / 31.97 / 41.92 / 48.10 /
48.28 / 37.46 % and the precheck's density stage to `{"total": 0}`.

Every test below breaks something the change defends and requires the failure:

  * the generator filled a frame that does not cover the die -> FAIL, naming
    the fraction it did cover, NOT a fill reported as die-wide
  * the generator exited 0 having written nothing            -> FAIL (this
    PDK's sibling `sealring.py` was measured doing exactly that)
  * the generator wrote a layout that gained no area         -> FAIL
  * the PDK ships no density filler                          -> DISCLOSED SKIP,
                                                                rc 2, naming
                                                                every location
                                                                searched
  * the step is not wired into both stream-out paths, or runs before the seal
    ring                                                     -> the ordering
                                                                assertions redden
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import die_density_fill_gen as DDF                            # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _progress_run as _pr  # noqa: E402

_RUNNER = _PROGRAMS / "phase3_one_shot_runner.py"
_DRIVER = _PROGRAMS / "density_fill" / "pdk_fill_driver.rb"
_MEASURE = _PROGRAMS / "density_fill" / "die_density_measure.py"


# ── the engines this program ships beside itself ────────────────────────────

def test_the_two_engines_exist_and_are_what_the_program_names():
    assert _DRIVER.is_file() and _MEASURE.is_file()
    assert DDF._DRIVER_REL == "density_fill/pdk_fill_driver.rb"
    assert DDF._MEASURE_REL == "density_fill/die_density_measure.py"


def test_the_driver_loads_the_pdk_script_and_carries_no_fill_geometry():
    """The driver's whole job is to give the PDK script's globals their types.

    If it ever grows a fill cell, a pitch or a keep-out, the flow has started
    reimplementing foundry data instead of calling the foundry's generator —
    and the fill would then be this repo's opinion about a design manual it has
    never read.
    """
    rb = _DRIVER.read_text()
    assert "load script" in rb
    assert "$input" in rb and "$output" in rb and "$threads" in rb
    for reimplementation in ("fill_region", "row_step", "column_step",
                             "DBox::new", "TilingProcessor"):
        assert reimplementation not in rb, reimplementation


def test_the_measurement_applies_no_floor_and_writes_no_layer_number():
    """Which layer must reach what coverage is the PDK density deck's to say.

    A second, independently-written floor here would be a second opinion about
    foundry data — and the one that silently disagreed would be believed.
    """
    src = _MEASURE.read_text()
    assert "over_die" in src and "over_bbox" in src
    body = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    # No bare density threshold and no GDS layer number in the logic.
    for literal in ("0.30", "0.25", "0.14", "34, 0", "81, 0", "22, 4"):
        assert literal not in body, literal


# ── resolution: an absence must NAME where it looked ────────────────────────

def test_absent_generator_is_a_disclosed_skip_that_names_every_location(tmp_path, monkeypatch):
    monkeypatch.delenv("PDK_ROOT", raising=False)
    monkeypatch.delenv("PDK", raising=False)
    monkeypatch.delenv(DDF._ENV_SCRIPT, raising=False)
    script, src, tried = DDF.resolve_script(tmp_path, None, None, None)
    assert script is None and src == ""
    assert any(DDF._BRIDGE_KEY in t for t in tried)
    assert any(DDF._ENV_SCRIPT in t for t in tried)
    assert any(DDF._PDK_SCRIPT_REL in t for t in tried)


def test_resolution_order_is_explicit_then_bridge_then_env_then_convention(tmp_path, monkeypatch):
    assert DDF.resolve_script(tmp_path, "/x/explicit.rb", None, None)[0] == "/x/explicit.rb"
    bridge = tmp_path / DDF._BRIDGE_CFG
    bridge.parent.mkdir(parents=True, exist_ok=True)
    bridge.write_text(json.dumps({DDF._BRIDGE_KEY: {"script": "/x/bridge.rb"}}))
    assert DDF.resolve_script(tmp_path, None, None, None)[0] == "/x/bridge.rb"
    bridge.write_text("{}")
    monkeypatch.setenv(DDF._ENV_SCRIPT, "/x/env.rb")
    assert DDF.resolve_script(tmp_path, None, None, None)[0] == "/x/env.rb"
    monkeypatch.delenv(DDF._ENV_SCRIPT)
    got, src, _ = DDF.resolve_script(tmp_path, None, "/pdks", "somepdk")
    assert got == "/pdks/somepdk/" + DDF._PDK_SCRIPT_REL
    assert src.startswith("$PDK_ROOT/$PDK/")


# ── the fake runner: everything below drives the real control flow ──────────

class _FakeRunner:
    """A KLayout runner whose census, measurement and fill are scripted.

    `measurements` is consumed one per full `die_density_measure` invocation, so
    a test states the BEFORE and AFTER layout as data. `censuses` is consumed
    one per COUNT-ONLY invocation (the one-writer probe). `fill` decides what
    the generator does to the output path, and `pass_layers` — when given —
    makes the fake behave like a multi-pass PDK generator whose passes write
    the layers named there.
    """

    kind = "fake"
    detail = "fake"

    def __init__(self, measurements, fill="write", rc=0, output="",
                 censuses=None, pass_layers=None, siblings=()):
        self._m = list(measurements)
        self._c = list(censuses or [])
        self._fill = fill
        self._rc = rc
        self._out = output
        self._pass_layers = pass_layers or {}
        self._siblings = list(siblings)
        self.fill_calls = []

    def cpath(self, p):
        return str(p)

    def covers(self, p):
        return True

    def exists(self, p):
        return True

    def run(self, script, env, *, path_keys=(), timeout=1800):
        if Path(str(script)).name == "die_density_measure.py":
            if env.get("DENS_COUNT_ONLY") == "1":
                if self._pass_layers:
                    Path(env["DENS_OUT"]).write_text(json.dumps(
                        {"shape_census": self._census_for(env["DENS_GDS"])}))
                else:
                    Path(env["DENS_OUT"]).write_text(json.dumps(
                        {"shape_census": self._c.pop(0) if self._c else {}}))
                return 0, "", ""
            Path(env["DENS_OUT"]).write_text(json.dumps(self._m.pop(0)))
            return 0, "", ""
        # the fill driver
        skip = [x for x in (env.get("VIBEIC_FILL_SKIP") or "").split(",") if x]
        self.fill_calls.append(skip)
        if self._fill == "write":
            Path(env["VIBEIC_FILL_OUT"]).write_text(json.dumps(sorted(skip)))
        return self._rc, self._out, ""

    def _census_for(self, gds):
        """The census of a layout the fake generator produced: the input's own
        layers plus whatever the passes that were NOT skipped write."""
        pth = Path(str(gds))
        try:
            skip = set(json.loads(pth.read_text()))
        except (OSError, ValueError):
            return dict(self._c[0]) if self._c else {}
        cen = dict(self._c[0]) if self._c else {}
        for sib, layers in self._pass_layers.items():
            if sib in skip:
                continue
            for layer in layers:
                key = "%d/4" % layer
                cen[key] = cen.get(key, 0) + 1
        return cen

    def run_argv(self, argv, env, *, timeout=1800):
        if argv and argv[0] == "cat":
            return 0, "\n".join(
                "require_relative '%s'" % s for s in self._siblings), ""
        raise AssertionError("unexpected argv %r" % (argv,))


def _measurement(bbox, die, layers_area):
    die_area = (die[2] - die[0]) * (die[3] - die[1])
    bbox_area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
    eps = 0.001
    return {
        "bbox_um": bbox,
        "bbox_area_um2": bbox_area,
        "die_um": die,
        "die_area_um2": die_area,
        "bbox_covers_die": (bbox[0] <= die[0] + eps and bbox[1] <= die[1] + eps
                            and bbox[2] >= die[2] - eps and bbox[3] >= die[3] - eps),
        "bbox_area_over_die_area": bbox_area / die_area,
        "by_layer": {
            str(l): {"area_um2": a, "datatypes": [0, 4],
                     "over_die": a / die_area, "over_bbox": a / bbox_area}
            for l, a in layers_area.items()},
        "by_layer_datatype": {},
    }


def _project(tmp_path):
    gds = tmp_path / "phase3" / "stage3" / "pnr" / "spm.gds"
    gds.parent.mkdir(parents=True, exist_ok=True)
    gds.write_bytes(b"unfilled")
    return gds


DIE = [0.0, 0.0, 1936.0, 2531.0]
CORE = [441.97, 442.0, 1494.0, 2089.0]


def test_a_fill_whose_frame_does_not_cover_the_die_is_a_named_failure(tmp_path, monkeypatch):
    """THE DEFECT THIS CHANGE EXISTS FOR.

    The PDK generator's fill frame is `$ly.top_cell().dbbox()`. Given a layout
    that spans only the routed core, it fills the core, exits 0, and every
    per-layer number it produces is a true statement about the core and a false
    one about the die. The step must refuse the DIE-WIDE claim while keeping the
    fill it did deposit, and it must say what fraction of the die it reached —
    a reader who is only told "partial" cannot tell 99 % from 35 %.
    """
    gds = _project(tmp_path)
    before = _measurement(CORE, DIE, {34: 100.0})
    after = _measurement(CORE, DIE, {34: 700000.0})
    monkeypatch.setattr(DDF._kl, "find_runner",
                        lambda *a, **k: _FakeRunner([before, after]))
    res = DDF.run(tmp_path, str(gds), "/pdk/fill_all.rb", None, "somepdk",
                  1936, 2531, "spm", 8, False, None, True, None, 60)
    fill = res["fill"]
    assert fill["state"] == "FAIL", fill
    assert fill["bbox_covers_die"] is False
    assert "35.4 %" in fill["reason"], fill["reason"]
    assert "does NOT cover the declared die" in fill["reason"]
    # The fill that WAS deposited is kept — refusing the claim must not throw
    # away legitimate foundry fill.
    assert gds.read_bytes() != b"unfilled"


def test_the_same_fill_on_a_sealed_die_passes(tmp_path, monkeypatch):
    """The other side of the same gate: identical program, identical generator,
    a layout whose bounding box IS the die. Without this the test above would
    be satisfied by a step that always fails."""
    gds = _project(tmp_path)
    before = _measurement(DIE, DIE, {34: 404500.0})
    after = _measurement(DIE, DIE, {34: 1566500.0})
    monkeypatch.setattr(DDF._kl, "find_runner",
                        lambda *a, **k: _FakeRunner([before, after]))
    res = DDF.run(tmp_path, str(gds), "/pdk/fill_all.rb", None, "somepdk",
                  1936, 2531, "spm", 8, False, None, True, None, 60)
    fill = res["fill"]
    assert fill["state"] == "PASS", fill
    assert fill["bbox_covers_die"] is True
    assert fill["layers_gained_fill"] == [34]
    cov = fill["coverage_over_die"]["34"]
    assert cov["over_die_before"] < 0.10 < 0.30 < cov["over_die_after"]


def test_a_generator_that_exits_zero_writing_nothing_is_a_failure(tmp_path, monkeypatch):
    """MEASURED on this PDK's sibling generator: `sealring.py` ends a failed
    import with a bare `sys.exit()`, which exits 0 and writes no file. Reading
    the exit code would have recorded a fill that does not exist."""
    gds = _project(tmp_path)
    before = _measurement(DIE, DIE, {34: 404500.0})
    monkeypatch.setattr(
        DDF._kl, "find_runner",
        lambda *a, **k: _FakeRunner([before], fill="nothing", rc=0,
                                    output="Error: Couldn't load the fill library."))
    res = DDF.run(tmp_path, str(gds), "/pdk/fill_all.rb", None, "somepdk",
                  1936, 2531, "spm", 8, False, None, True, None, 60)
    fill = res["fill"]
    assert fill["state"] == "FAIL"
    assert fill["generator_rc"] == 0
    assert "produced no output layout" in fill["reason"]
    assert "Couldn't load the fill library" in fill["reason"]
    assert gds.read_bytes() == b"unfilled"                    # untouched


def test_a_generator_that_deposits_no_area_is_a_failure(tmp_path, monkeypatch):
    gds = _project(tmp_path)
    same = {34: 404500.0}
    monkeypatch.setattr(
        DDF._kl, "find_runner",
        lambda *a, **k: _FakeRunner([_measurement(DIE, DIE, same),
                                     _measurement(DIE, DIE, same)]))
    res = DDF.run(tmp_path, str(gds), "/pdk/fill_all.rb", None, "somepdk",
                  1936, 2531, "spm", 8, False, None, True, None, 60)
    assert res["fill"]["state"] == "FAIL"
    assert "not one layer gained area" in res["fill"]["reason"]
    assert gds.read_bytes() == b"unfilled"


def test_no_klayout_is_a_disclosed_skip_not_a_pass(tmp_path, monkeypatch):
    gds = _project(tmp_path)
    monkeypatch.setattr(DDF._kl, "find_runner", lambda *a, **k: None)
    res = DDF.run(tmp_path, str(gds), "/pdk/fill_all.rb", None, "somepdk",
                  1936, 2531, "spm", 8, False, None, True, None, 60)
    assert res["fill"]["state"] == "DISCLOSED_SKIP"


def test_the_cli_maps_states_to_exit_codes_and_marks_a_skip_vacuous(tmp_path, monkeypatch, capsys):
    gds = _project(tmp_path)
    monkeypatch.setattr(DDF._kl, "find_runner", lambda *a, **k: None)
    rc = DDF.main([str(tmp_path), "--gds", str(gds), "--in-place",
                   "--script", "/pdk/fill_all.rb", "--pdk", "somepdk",
                   "--die-width", "1936", "--die-height", "2531"])
    assert rc == DDF.SKIP
    assert capsys.readouterr().out.startswith("VACUOUS_PASS: ")


# ── the wiring: the step must run, in both paths, AFTER the seal ring ───────

def test_the_step_is_wired_into_both_streamout_paths_after_the_seal_ring():
    """ORDER IS PART OF THE FIX. The PDK generator's frame is the layout
    bounding box and its scribe keep-out is measured inward from that frame, so
    it can only fill the die once the seal ring has made the bounding box the
    die. Running it before the ring would fill the core and — by the gate above
    — fail, correctly but pointlessly."""
    src = _RUNNER.read_text()
    assert "def _die_density_fill(" in src
    calls = [i for i, l in enumerate(src.splitlines())
             if "_die_density_fill(project" in l and "def " not in l]
    seals = [i for i, l in enumerate(src.splitlines())
             if "_die_finishing(project" in l and "def " not in l]
    assert len(calls) == 2, calls                             # both streamout paths
    assert len(seals) == 2, seals
    fills = [i for i, l in enumerate(src.splitlines())
             if "_density_metal_fill(project" in l and "def " not in l]
    assert len(fills) == 2, fills
    for seal, fill, call in zip(seals, fills, calls):
        # seal -> this flow's own metal fill -> the PDK generator. The last of
        # the three must be last: it is the one that cannot see the others'
        # output, so it is the one that must be told what is already there.
        assert seal < fill < call, (seal, fill, call)


def test_the_step_reports_its_outcome_in_both_paths_extras():
    src = _RUNNER.read_text()
    assert src.count('"die_density_fill": ddfill_ok,') == 2
    assert src.count('"die_density_fill_note": ddfill_note,') == 2


def test_the_report_name_cannot_be_eaten_by_the_metal_density_check():
    """`metal_layer_density_check` rglobs `*metal*density*.json` project-wide
    and would ingest this report as if it were a per-layer density measurement.
    The name is load-bearing, so it is asserted rather than assumed."""
    import fnmatch
    name = Path(DDF._REPORT_REL).name
    assert not fnmatch.fnmatch(name, "*metal*density*.json"), name


def test_the_program_is_pdk_agnostic():
    """No foundry, PDK, vendor, design or layer literal in the producer."""
    body = "\n".join(
        l for l in (_PROGRAMS / "die_density_fill_gen.py").read_text().splitlines()
        if not l.lstrip().startswith("#"))
    head, _, body = body.partition('"""')
    _, _, body = body.partition('"""')                        # drop the docstring
    for literal in ("gf180", "sky130", "ihp-", "Metal1", "spm", "1936"):
        assert literal not in body, literal


def test_the_program_runs_standalone_and_refuses_without_a_project():
    cp = _pr.run([sys.executable,
                         str(_PROGRAMS / "die_density_fill_gen.py")],
                        capture_output=True, text=True)
    assert cp.returncode != 0


# ── ONE WRITER PER DUMMY LAYER ──────────────────────────────────────────────

_SIBLINGS = ("fill_comp.rb", "fill_poly2.rb", "fill_metal.rb")
#: What each of the generator's passes writes. The program must DISCOVER this
#: by running them, never be told it: which layer families a PDK ships, what it
#: calls the scripts and which layers each one writes are all PDK data.
_PASS_LAYERS = {"fill_comp.rb": [22], "fill_poly2.rb": [30],
                "fill_metal.rb": [34, 36, 42, 46, 81]}


def test_the_pass_that_would_double_write_an_owned_layer_is_discovered_and_left_out(
        tmp_path, monkeypatch):
    """THE SECOND DEFECT, and the more dangerous one.

    A layer this flow has already filled must not be filled again by the PDK's
    generator: the PDK computes its keep-out from the DRAWN datatype alone, so
    it cannot see fill that is already there. MEASURED on a real die filled by
    both — 234437 KLayout DRC errors (MT.2a 79226, M3.2a 73590, M4.2a 71714,
    MT.1 4374, M3.1 2862, M4.1 2671) — where EACH filler alone was DRC-clean.

    The program is told only which LAYER NUMBERS this flow owns. Which of the
    generator's passes writes them is read from the PDK's own require_relative
    list and MEASURED by running each one.
    """
    gds = _project(tmp_path)
    before = _measurement(DIE, DIE, {34: 1000.0})
    after = _measurement(DIE, DIE, {22: 1200.0, 30: 1400.0, 34: 1000.0})
    # the input already carries dummy on every metal layer this flow owns
    cen_in = {"%d/4" % layer: 1 for layer in (34, 36, 42, 46, 81)}
    runner = _FakeRunner([before, after], censuses=[cen_in],
                         pass_layers=_PASS_LAYERS, siblings=_SIBLINGS)
    monkeypatch.setattr(DDF._kl, "find_runner", lambda *a, **k: runner)
    res = DDF.run(tmp_path, str(gds), "/pdk/fill_all.rb", None, "somepdk",
                  1936, 2531, "spm", 8, False, None, True, None, 60,
                  owned_layers=[34, 36, 42, 46, 81])
    fill = res["fill"]
    assert fill["state"] == "PASS", fill
    assert fill["contested_layers"] == [34, 36, 42, 46, 81]
    probe = fill["probe"]
    assert probe["sibling_passes"] == list(_SIBLINGS)
    assert probe["layers_per_pass"]["fill_metal.rb"] == [34, 36, 42, 46, 81]
    assert probe["layers_per_pass"]["fill_comp.rb"] == [22]
    assert fill["skipped_passes"] == ["fill_metal.rb"]
    # and the promoted layout is the one produced WITH that pass skipped
    assert json.loads(gds.read_text()) == ["fill_metal.rb"]


def test_no_owned_layer_means_the_whole_generator_runs(tmp_path, monkeypatch):
    """The other side: a flow with no filler of its own must get the PDK's
    complete generator, metal included. Without this the test above would be
    satisfied by a step that always skips."""
    gds = _project(tmp_path)
    before = _measurement(DIE, DIE, {34: 1000.0})
    after = _measurement(DIE, DIE, {22: 1200.0, 34: 900000.0})
    runner = _FakeRunner([before, after], censuses=[{}],
                         pass_layers=_PASS_LAYERS, siblings=_SIBLINGS)
    monkeypatch.setattr(DDF._kl, "find_runner", lambda *a, **k: runner)
    res = DDF.run(tmp_path, str(gds), "/pdk/fill_all.rb", None, "somepdk",
                  1936, 2531, "spm", 8, False, None, True, None, 60,
                  owned_layers=[])
    fill = res["fill"]
    assert fill["state"] == "PASS", fill
    assert fill["skipped_passes"] == []
    assert runner.fill_calls == [[]], runner.fill_calls


def test_an_owned_layer_that_carries_no_fill_yet_is_not_contested(tmp_path, monkeypatch):
    """`--owned-layer` says which layers this flow's filler is CONFIGURED for;
    the census says which it actually WROTE. A configured filler that deposited
    nothing must not cost the die the PDK's fill for that layer."""
    gds = _project(tmp_path)
    before = _measurement(DIE, DIE, {34: 1000.0})
    after = _measurement(DIE, DIE, {22: 1200.0, 34: 900000.0})
    runner = _FakeRunner([before, after], censuses=[{}],
                         pass_layers=_PASS_LAYERS, siblings=_SIBLINGS)
    monkeypatch.setattr(DDF._kl, "find_runner", lambda *a, **k: runner)
    res = DDF.run(tmp_path, str(gds), "/pdk/fill_all.rb", None, "somepdk",
                  1936, 2531, "spm", 8, False, None, True, None, 60,
                  owned_layers=[34, 36, 42, 46, 81])
    assert res["fill"]["contested_layers"] == []
    assert res["fill"]["skipped_passes"] == []


def test_the_driver_skips_a_pass_through_rubys_own_bookkeeping():
    """The skip must not edit, copy or re-implement any PDK file: a path already
    in `$LOADED_FEATURES` is simply not required again."""
    rb = _DRIVER.read_text()
    assert "$LOADED_FEATURES" in rb
    assert "VIBEIC_FILL_SKIP" in rb
    assert "File.expand_path" in rb
    # and a name the PDK does not ship must be refused, not silently ignored —
    # a typo'd skip would otherwise run the pass it was meant to leave out.
    assert "which the PDK does not ship" in rb


def test_the_runner_tells_the_program_which_layers_its_own_filler_owns():
    src = _RUNNER.read_text()
    assert 'metal_fill_density_cfg.json' in src
    assert '"--owned-layer"' in src


# ── vibe-ic#2107: the engine is CHECKED on the host and OPENED in the container ──
#
# The program shipped a host `Path.is_file()` guard over its two engines and
# then handed those same host paths to a KLayout that, on every normal flow
# run, lives inside a container. The plugin tree is not one of the mounts the
# repo's own `tools/vibeic-eda/restart-eda.sh` creates (lines 194-195 bind the
# designs directory and nothing else), so the guard passed, the open failed,
# and the run recorded
#
#   state  = FAIL
#   reason = could not measure the die BEFORE filling: the density measurement
#            wrote no report (rc=1): ERROR: Unable to open file:
#            .../programs/density_fill/die_density_measure.py (errno=2)
#   runner = container:<name>:klayout
#
# — a die-density number that is NOT_MEASURED-by-the-plugin reading as a
# non-PASS of the layout. The two tests below are the two directions: the
# engines must be reachable where KLayout runs, and an engine that genuinely
# cannot be reached there must FAIL by name rather than skip or pass.

class _ContainerFakeRunner(_FakeRunner):
    """`_FakeRunner`, but it can only open what its bind mounts cover.

    Everything the real `ContainerRunner` does that matters here: `covers` is a
    prefix test over the mount list, and a script outside it is not a script
    this runner can execute — KLayout answers `Unable to open file: … (errno=2)`
    and writes nothing, which is exactly what the reported run recorded.
    """

    kind = "container"

    def __init__(self, mount, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._mount = str(mount).rstrip("/")
        self.detail = "fake:klayout"
        self.refused = []

    def covers(self, p):
        q = str(p)
        return q == self._mount or q.startswith(self._mount + "/")

    def exists(self, p):
        # The PDK's own generator lives in the container's tree, not the host's.
        return True

    def run(self, script, env, *, path_keys=(), timeout=1800):
        if not self.covers(script):
            self.refused.append(str(script))
            return 1, "", f"ERROR: Unable to open file: {script} (errno=2)"
        return super().run(script, env, path_keys=path_keys, timeout=timeout)


def test_the_engines_are_made_reachable_where_klayout_actually_runs(tmp_path, monkeypatch):
    """THE DEFECT (#2107). The engines live under the plugin install, which the
    container does not mount; the GDS lives in the project, which it does. The
    step must reach the engines from the side that opens them — and it must not
    hand the runner a single path the runner cannot open."""
    gds = _project(tmp_path)
    before = _measurement(DIE, DIE, {34: 404500.0})
    after = _measurement(DIE, DIE, {34: 1566500.0})
    runner = _ContainerFakeRunner(tmp_path, [before, after])
    monkeypatch.setattr(DDF._kl, "find_runner", lambda *a, **k: runner)
    res = DDF.run(tmp_path, str(gds), "/pdk/fill_all.rb", None, "somepdk",
                  1936, 2531, "spm", 8, False, None, True, None, 60)
    fill = res["fill"]
    assert runner.refused == [], runner.refused
    assert fill["state"] == "PASS", fill
    # …and it says so in the report, because a copy that is never disclosed is
    # indistinguishable from an engine that was reachable all along.
    staged = fill.get("engines_staged") or []
    assert len(staged) == 2, fill
    for q in staged:
        assert runner.covers(q), q
        assert Path(q).is_file(), q
    by_name = {Path(q).name: Path(q).read_bytes() for q in staged}
    assert by_name[_DRIVER.name] == _DRIVER.read_bytes()
    assert by_name[_MEASURE.name] == _MEASURE.read_bytes()


def test_an_engine_the_runner_still_cannot_open_is_a_named_failure(tmp_path, monkeypatch):
    """The other direction, so the test above cannot be satisfied by a step that
    always passes. A runner that reaches the GDS but nothing beside it — docker
    binds single files as readily as directories — leaves the engines
    unreachable even after staging. That is this program failing, and it must
    be recorded as FAIL with the engine named: never a DISCLOSED_SKIP (which
    would read as "this PDK ships no filler") and never a PASS."""
    gds = _project(tmp_path)
    before = _measurement(DIE, DIE, {34: 404500.0})
    after = _measurement(DIE, DIE, {34: 1566500.0})

    class _OnlyTheGds(_ContainerFakeRunner):
        def covers(self, p):
            return str(p) == str(gds)

    runner = _OnlyTheGds(tmp_path, [before, after])
    monkeypatch.setattr(DDF._kl, "find_runner", lambda *a, **k: runner)
    res = DDF.run(tmp_path, str(gds), "/pdk/fill_all.rb", None, "somepdk",
                  1936, 2531, "spm", 8, False, None, True, None, 60)
    fill = res["fill"]
    assert fill["state"] == "FAIL", fill
    assert "cannot be opened where KLayout runs" in fill["reason"], fill
    assert _MEASURE.name in fill["reason"] or _DRIVER.name in fill["reason"], fill
    assert runner.refused == [], "it must refuse BEFORE launching the engine"


def test_a_runner_that_already_reaches_the_engines_stages_nothing(tmp_path):
    """A host KLayout covers every path there is; copying its engines would be
    a second copy of a file that was already the right one."""
    into = tmp_path / "stage"
    got, why = DDF.stage_engine(_FakeRunner([]), _MEASURE, into)
    assert why is None
    assert got == _MEASURE
    assert not into.exists()


def test_an_unreachable_engine_is_staged_into_a_directory_that_is_reachable(tmp_path):
    into = tmp_path / "stage"
    got, why = DDF.stage_engine(_ContainerFakeRunner(tmp_path, []), _MEASURE, into)
    assert why is None
    assert got == into / _MEASURE.name
    assert got.read_bytes() == _MEASURE.read_bytes()


def test_the_probe_layout_is_removed_and_leaves_its_hash(tmp_path, monkeypatch):
    """A PROBE IS NOT AN ARTEFACT (dispatcher 2026-09-18).

    `<top>.probe.gds` answers one question — which sibling pass writes the
    contested layers — and nothing reads it afterwards; the bytes it needs for
    the promotion are already in memory. On the die route it is a FULL copy of
    the sign-off stream: MEASURED on spm x gf180mcuD, 1,136,500,018 bytes, one
    of three identical copies the pnr directory carried (3.4 GB). It is removed
    and its sha256, size and reason are recorded in the probe log.
    """
    gds = _project(tmp_path)
    before = _measurement(DIE, DIE, {34: 1000.0})
    after = _measurement(DIE, DIE, {22: 1200.0, 30: 1400.0, 34: 1000.0})
    cen_in = {"%d/4" % layer: 1 for layer in (34, 36, 42, 46, 81)}
    runner = _FakeRunner([before, after], censuses=[cen_in],
                         pass_layers=_PASS_LAYERS, siblings=_SIBLINGS)
    monkeypatch.setattr(DDF._kl, "find_runner", lambda *a, **k: runner)
    probe = tmp_path / "phase3/stage3/pnr/spm.probe.gds"
    res = DDF.run(tmp_path, str(gds), "/pdk/fill_all.rb", None, "somepdk",
                  1936, 2531, "spm", 8, False, None, True, None, 60,
                  owned_layers=[34, 36, 42, 46, 81])
    rec = res["fill"]["probe"]["probe_layout"]
    assert rec["removed"] is True, rec
    assert rec["path"] == "spm.probe.gds"
    assert rec["bytes"] > 0 and len(rec["sha256"]) == 64
    assert not probe.exists(), "the probe copy is still on disk"
    # the decision the probe existed to make is unchanged
    assert res["fill"]["skipped_passes"] == ["fill_metal.rb"]
    assert json.loads(gds.read_text()) == ["fill_metal.rb"]
