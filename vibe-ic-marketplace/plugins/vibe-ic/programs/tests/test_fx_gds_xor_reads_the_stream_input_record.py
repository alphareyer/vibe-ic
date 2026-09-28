"""Step 37.3 re-streams from the stream-out's OWN record of what it read.

MEASURED on lane fxspm1's spm integration run (gf180mcuD, 2026-09-28): step 37.3
answered NOT_MEASURED, rc 127, "the run records no LEF set for its stream-out".
`gds_xor_check` assembled the set from `technology_units.json`, the pad-ring IO
record and a registry glob. A core design has no IO ring, so that assembly was
empty on EVERY such design, for a GDS the flow had just streamed itself (with
Magic, which the re-stream would not have used either).

The rule now:
  * the stream-out step (both engines) writes `<gds stem>.stream_inputs.json`
    beside its output: engine, recipe, DEF, and every LEF / cell GDS / macro GDS /
    layer map / rcfile it handed the tool, each with its sha256;
  * `gds_xor_check` re-streams from that record and nothing else, with the
    recorded engine, after proving each input still holds the recorded bytes
    where the tool runs; an absent or incomplete record is refused BY NAME.

The tool itself is faked (its file writes are not what is under test); the
digest check is real `sha256sum -c` on the host, through `_klayout_launch`'s own
HostRunner.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import sys
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import _klayout_launch as KL  # noqa: E402
import _stream_input_record as SIR  # noqa: E402
import gds_xor_check as G  # noqa: E402
import phase3_one_shot_runner as R  # noqa: E402

PNR = "phase3/stage3/pnr"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class _Tool(KL.HostRunner):
    """The real host runner for everything (`sha256sum -c` included) except the
    EDA tool, whose invocation is recorded instead of executed."""

    def __init__(self):
        super().__init__("klayout", ("-zz", "-b", "-r"))
        self.calls = []

    def run(self, script, env, *, path_keys=(), timeout=1800):
        self.calls.append(("klayout", str(script),
                           {k: str(v) for k, v in env.items()}))
        return 0, "", ""

    def run_argv(self, argv, env, *, timeout=1800):
        if argv and argv[0] == "magic":
            self.calls.append(("magic", [str(a) for a in argv],
                               {k: str(v) for k, v in env.items()}))
            return 0, "", ""
        return super().run_argv(argv, env, timeout=timeout)


def _core_run(root: Path, *, engine: str):
    """A core design's run as the stream-out leaves it: DEF, recipe, a legalized
    tech LEF in the project, library files outside it, NO IO-ring record."""
    project = root / "spm"
    pnr = project / PNR
    pnr.mkdir(parents=True)
    dfile = pnr / "spm.def"
    dfile.write_text("VERSION 5.8 ;\nDESIGN spm ;\nCOMPONENTS 0 ;\nEND DESIGN\n")
    (pnr / "routed.def").write_bytes(dfile.read_bytes())
    tlef = pnr / "active_via_legalized.tlef"
    tlef.write_text("VERSION 5.8 ;\n")
    lib = root / "pdk"
    lib.mkdir()
    cell_gds, mapf, rc = lib / "cells.gds", lib / "tech.map", lib / "tech.magicrc"
    cell_gds.write_bytes(b"GDS cells")
    mapf.write_text("metal1 NET 34 0\n")
    rc.write_text("tech load x\n")
    recipe = pnr / ("magic_stream_out.tcl" if engine == "magic" else "stream_out.py")
    recipe.write_text("# the run's own recipe\n")
    files = {"LEFS": str(tlef), "CELL_GDS": str(cell_gds), "MACRO_GDS": "",
             "LEFDEF_MAP": str(mapf) if engine == "klayout" else ""}
    return project, dfile, recipe, files, (str(rc) if engine == "magic" else None)


def _produce(monkeypatch, project, dfile, recipe, files, rcfile, engine):
    """The PRODUCER, as step_gds calls it right after the stream-out exec."""
    monkeypatch.setattr(R, "_container_mounts", lambda c: [])
    gds_out = project / PNR / "spm.gds"
    R._record_stream_inputs(project, "", gds_out, engine, recipe, dfile, "spm",
                            files, {"STDCELL_MARKER_LAYER": ""} if engine == "klayout" else {},
                            rcfile=rcfile)
    return SIR.record_path(gds_out)


# ── the producer ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("engine", ["magic", "klayout"])
def test_the_stream_out_records_every_input_with_its_digest(tmp_path, monkeypatch, engine):
    """RED ON MAIN: there is no record (and no `_record_stream_inputs`)."""
    project, dfile, recipe, files, rcfile = _core_run(tmp_path, engine=engine)
    rec_path = _produce(monkeypatch, project, dfile, recipe, files, rcfile, engine)
    rec, why = SIR.read(rec_path)
    assert rec is not None, why
    assert rec["engine"] == engine and rec["output"] == "spm.gds"
    assert rec["recipe"] == {"project_rel": f"{PNR}/{recipe.name}", "sha256": _sha(recipe)}
    assert rec["def"] == {"project_rel": f"{PNR}/spm.def", "sha256": _sha(dfile)}
    lefs = rec["inputs"]["LEFS"]
    assert [e["project_rel"] for e in lefs] == [f"{PNR}/active_via_legalized.tlef"]
    assert rec["inputs"]["CELL_GDS"][0]["sha256"] == _sha(Path(files["CELL_GDS"]))
    assert rec["inputs"]["CELL_GDS"][0]["project_rel"] is None
    if engine == "magic":
        assert rec["rcfile"]["sha256"] == _sha(Path(rcfile))
    else:
        assert rec["inputs"]["LEFDEF_MAP"][0]["sha256"] == _sha(Path(files["LEFDEF_MAP"]))
    assert rec["unhashed"] == []


def test_both_engines_record_right_after_their_stream():
    """Structural, host-runnable: each engine's stream-out calls the recorder
    with its own engine name, after its tool exec."""
    for fn, engine in ((R._magic_def_to_gds, "magic"), (R._step_gds_direct, "klayout")):
        src = inspect.getsource(fn)
        call = src.find(f'_record_stream_inputs(project, container, gds_out, "{engine}"')
        assert call > 0, f"{fn.__name__} records no stream-out input set"
        assert src.rfind("_docker_exec(container, cmd", 0, call) > 0, fn.__name__


# ── the consumer ─────────────────────────────────────────────────────────────

def test_a_run_without_the_record_is_refused_by_naming_it(tmp_path):
    """Never assembled from other records. RED ON MAIN: main assembles the LEF
    set from technology_units.json / the IO record / a registry glob and names
    no record."""
    project, dfile, recipe, _files, _rc = _core_run(tmp_path, engine="klayout")
    rc, _so, se = G.stream_reference(_Tool(), project / "scratch", dfile,
                                     project / "out.gds", 60)
    assert rc == 127
    assert f"{PNR}/spm.stream_inputs.json" in se and "records no stream-out input set" in se


def test_the_fxspm1_core_run_is_re_streamed_not_refused(tmp_path):
    """RED ON MAIN, FOR THE MEASURED REASON: a core run whose stream-out left its
    record (written here exactly as the producer writes it) is refused on main
    with rc 127 "the run records no LEF set for its stream-out", because main
    never reads the record and its own assembly needs an IO-ring record."""
    project, dfile, recipe, files, rcfile = _core_run(tmp_path, engine="magic")
    # As on the integration run: step_gds also leaves the KLayout recipe in the
    # pnr dir, so main gets past "recipe absent" to the LEF-set refusal.
    (project / PNR / "stream_out.py").write_text("# the KLayout recipe\n")
    tlef = Path(files["LEFS"])
    entry = lambda p, rel=None: {"path": str(p), "project_rel": rel, "sha256": _sha(Path(p))}
    rec = {"schema": "vibeic.stream_inputs/1", "program": "phase3_one_shot_runner",
           "engine": "magic", "output": "spm.gds", "top": "spm",
           "recipe": {"project_rel": f"{PNR}/{recipe.name}", "sha256": _sha(recipe)},
           "def": {"project_rel": f"{PNR}/spm.def", "sha256": _sha(dfile)},
           "inputs": {"LEFS": [entry(tlef, f"{PNR}/{tlef.name}")],
                      "CELL_GDS": [entry(files["CELL_GDS"])],
                      "MACRO_GDS": [], "LEFDEF_MAP": []},
           "scalars": {}, "rcfile": entry(rcfile), "unhashed": []}
    (project / PNR / "spm.stream_inputs.json").write_text(json.dumps(rec))
    tool = _Tool()
    rc, _so, se = G.stream_reference(tool, project / "scratch", dfile,
                                     project / "scratch" / "ref.gds", 60)
    assert rc == 0, f"a recorded core-design stream-out was refused: {se}"
    assert [c[0] for c in tool.calls] == ["magic"], tool.calls


@pytest.mark.parametrize("engine", ["magic", "klayout"])
def test_a_core_run_re_streams_exactly_what_its_stream_out_read(tmp_path, monkeypatch, engine):
    """THE fxspm1 SHAPE: a core design, no IO-ring record. RED ON MAIN (rc 127,
    "the run records no LEF set"). The re-stream uses the recorded engine and
    hands the tool the recorded set, every path verified by digest."""
    project, dfile, recipe, files, rcfile = _core_run(tmp_path, engine=engine)
    _produce(monkeypatch, project, dfile, recipe, files, rcfile, engine)
    tool = _Tool()
    out = project / "scratch" / "pre_finishing.gds"
    rc, _so, se = G.stream_reference(tool, project / "scratch", dfile, out, 60,
                                     stem="spm")
    assert rc == 0, se
    assert len(tool.calls) == 1, tool.calls
    kind, what, env = tool.calls[0]
    assert kind == engine
    assert env["LEFS"] == files["LEFS"] and env["CELL_GDS"] == files["CELL_GDS"]
    assert env["TOP"] == "spm" and env["DEF"] == str(dfile) and env["GDS_OUT"] == str(out)
    if engine == "magic":
        assert what == ["magic", "-dnull", "-noconsole", "-rcfile", rcfile, str(recipe)]
    else:
        assert what == str(recipe) and env["LEFDEF_MAP"] == files["LEFDEF_MAP"]


def test_an_input_that_moved_since_the_stream_is_named(tmp_path, monkeypatch):
    project, dfile, recipe, files, rcfile = _core_run(tmp_path, engine="magic")
    _produce(monkeypatch, project, dfile, recipe, files, rcfile, "magic")
    Path(files["CELL_GDS"]).write_bytes(b"GDS cells, a different release")
    tool = _Tool()
    rc, _so, se = G.stream_reference(tool, project / "scratch", dfile,
                                     project / "o.gds", 60, stem="spm")
    assert rc == 127 and files["CELL_GDS"] in se, se
    assert tool.calls == [], "the tool must not run on inputs that moved"


def test_a_recipe_or_def_that_changed_is_refused(tmp_path, monkeypatch):
    project, dfile, recipe, files, rcfile = _core_run(tmp_path, engine="klayout")
    _produce(monkeypatch, project, dfile, recipe, files, rcfile, "klayout")
    recipe.write_text("# edited after the stream\n")
    rc, _so, se = G.stream_reference(_Tool(), project / "scratch", dfile,
                                     project / "o.gds", 60, stem="spm")
    assert rc == 127 and "changed since the stream-out ran" in se
    other = project / PNR / "routed.def"
    other.write_text("VERSION 5.8 ;\nDESIGN spm ;\nCOMPONENTS 1 ;\nEND DESIGN\n")
    recipe.write_text("# the run's own recipe\n")
    rc, _so, se = G.stream_reference(_Tool(), project / "scratch", other,
                                     project / "o.gds", 60, stem="spm")
    assert rc == 127 and "not the DEF this check compares" in se


def test_an_input_the_stream_out_could_not_hash_is_refused(tmp_path, monkeypatch):
    """A tool-side path the producer could not read (a container-only path with
    no container here) is recorded with sha256 null, and the consumer will not
    re-stream on a promise it cannot check."""
    project, dfile, recipe, files, rcfile = _core_run(tmp_path, engine="magic")
    files = dict(files, MACRO_GDS="/nowhere/io.gds")
    rec_path = _produce(monkeypatch, project, dfile, recipe, files, rcfile, "magic")
    assert json.loads(rec_path.read_text())["unhashed"] == ["/nowhere/io.gds"]
    rc, _so, se = G.stream_reference(_Tool(), project / "scratch", dfile,
                                     project / "o.gds", 60, stem="spm")
    assert rc == 127 and "/nowhere/io.gds" in se and "no sha256" in se


def test_a_copy_of_the_run_re_anchors_its_own_inputs(tmp_path, monkeypatch):
    """The record is read on a COPY (lane fxspm1's E2E shape): a project input is
    recorded project-relative and resolves inside the copy, never back into the
    original run."""
    import shutil
    project, dfile, recipe, files, rcfile = _core_run(tmp_path, engine="magic")
    _produce(monkeypatch, project, dfile, recipe, files, rcfile, "magic")
    copy = tmp_path / "copy" / "spm"
    shutil.copytree(project, copy)
    shutil.rmtree(project)
    tool = _Tool()
    rc, _so, se = G.stream_reference(tool, copy / "scratch", copy / PNR / "spm.def",
                                     copy / "o.gds", 60, stem="spm")
    assert rc == 0, se
    assert tool.calls[0][2]["LEFS"] == str(copy / PNR / "active_via_legalized.tlef")
