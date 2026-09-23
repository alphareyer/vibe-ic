"""A second producer recreated #2158's condition, by a different mechanism.

#2158 repaired a producer that embedded the run's own ephemeral scratch path in
a persistent report, and its landing message said "the producer fix means no
new run can create one". MEASURED false: a front-door run four commits later
created the condition again from a DIFFERENT file, and the failure is not a
boundary-buggy remap but NO REMAP AT ALL.

`verilator_coverage_measure` composes `coverage_verilator.json` on the host.
`coverage_dat`, `testbench` and `rtl_sources` are composed here, so they are
host paths. `per_file`'s keys are the source files VERILATOR named, in the
namespace VERILATOR ran in — and when the flow dispatches the instrumented
build into the pinned image, that is the CONTAINER's. `scope_files` is derived
from those keys, so it inherits the spelling. One document, two namespaces,
nothing in it saying which field is which:

    rtl_sources  ["<project>/phase2/stage1/rtl/<top>.v"]               host
    scope_files  ["/foss/designs/<project>/phase2/stage1/rtl/<top>.v"] container

REPRODUCED on 8HD-9 with the pinned image
`sha256:89a8fd7295208ee6d06e216ade9edc6161d26db52099e9f22ceb77a2d76e3f49`
(label 0.3.49): two runs of the SAME RTL and the SAME testbench differing only
in the container mount DESTINATION. With `/foss/designs/<name>` the gate
`project_outputs_in_tree_check` exits 1 on two dangling references, both cited
by `coverage_verilator.json`; with the project's own host path it exits 0.
Nothing was lost and nothing was left outside the tree — the bytes were the
ones the run had just written, seen through the container's own bind mount.

Every check below drives BOTH directions. The end-to-end one runs the real
gate over a real tree and asserts rc 1 before and rc 0 after, so a normalisation
that stopped normalising would be caught by a test that can fail.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import _designs_root as DR                    # noqa: E402
import verilator_coverage_measure as V        # noqa: E402

_GATE = _PROGRAMS / "project_outputs_in_tree_check.py"

#: One Verilator 5.x coverage record, whose blob is `\x01`-separated
#: `key\x02value` pairs. `f` is the filename the tool reported.
def _rec(src: str, page: str, hits: int) -> str:
    blob = "\x01".join([f"f\x02{src}", f"page\x02{page}", "n\x021"])
    return f"C '{blob}' {hits}\n"


def _dat(tmp_path: Path, *sources: str) -> Path:
    """A coverage.dat naming `sources` exactly as a tool would."""
    text = "# SystemC::Coverage-3\n"
    for i, src in enumerate(sources):
        text += _rec(src, "v_line/x", 1 + i)
        text += _rec(src, "v_toggle/x", 0)
        text += _rec(src, "v_branch/x", 1)
    p = tmp_path / "coverage.dat"
    p.write_text(text)
    return p


CONT = "/foss/designs/proj"


def _mounts(host_root: Path):
    return [(host_root, CONT)]


# ── the shared layer: the inverse of the translation the repo already owns ──

def test_host_spelling_inverts_translate(tmp_path):
    """Round trip through the ONE module that owns host<->container paths."""
    host = tmp_path / "proj"
    (host / "a").mkdir(parents=True)
    f = host / "a" / "top.v"
    f.write_text("module top; endmodule\n")
    mounts = _mounts(host)

    fwd = DR.translate(f, mounts=mounts)
    assert fwd.path == f"{CONT}/a/top.v", fwd

    back = DR.host_spelling(fwd.path, mounts=mounts)
    assert back.basis == DR.NS_TRANSLATED
    assert Path(back.path) == f.resolve()


def test_a_path_already_on_the_host_side_is_not_rewritten(tmp_path):
    host = tmp_path / "proj"
    host.mkdir()
    got = DR.host_spelling(str(host / "a" / "top.v"), mounts=_mounts(host))
    assert got.basis == DR.NS_ALREADY_HOST
    assert got.path == str(host / "a" / "top.v")


def test_a_path_no_mount_covers_is_refused_not_handed_back(tmp_path):
    """'I could not translate it' must never render as 'it is a host path'."""
    got = DR.host_spelling("/foss/pdks/openpdk/lib/cell.lib",
                           mounts=_mounts(tmp_path / "proj"))
    assert got.basis == DR.NS_NO_MOUNT
    assert got.path is None
    assert "no bind mount covers" in got.detail


def test_the_longest_prefix_wins_like_the_kernel_does(tmp_path):
    """Nested mounts: the more specific destination decides, as in the
    namespace the tool actually saw."""
    outer, inner = tmp_path / "outer", tmp_path / "inner"
    mounts = [(outer, "/foss/designs"), (inner, "/foss/designs/proj")]
    got = DR.host_spelling("/foss/designs/proj/a.v", mounts=mounts)
    assert got.basis == DR.NS_TRANSLATED
    assert got.path == str(inner / "a.v")


# ── ingest: the tool's answer is normalised before anything persists it ──

def test_container_reported_paths_become_the_host_spelling(tmp_path):
    host = tmp_path / "proj"
    (host / "rtl").mkdir(parents=True)
    dat = _dat(tmp_path, f"{CONT}/rtl/top.v", f"{CONT}/tb/tb_top.v")

    cov = V.parse_coverage_dat(str(dat), mounts=_mounts(host))

    assert sorted(cov["per_file"]) == sorted(
        [str(host / "rtl" / "top.v"), str(host / "tb" / "tb_top.v")])
    assert not [k for k in cov["per_file"] if k.startswith("/foss/")]
    ns = cov["path_namespace"]
    assert ns["examined"] is True
    assert ns["translated_from_container"] == 2
    assert ns["untranslated"] == []


def test_the_measurement_itself_does_not_move(tmp_path):
    """Only the SPELLING changes. Compared by MEMBERSHIP — basenames and the
    per-file bodies — because a count is the one summary a substitution
    cannot disturb."""
    host = tmp_path / "proj"
    host.mkdir()
    dat = _dat(tmp_path, f"{CONT}/rtl/top.v", f"{CONT}/tb/tb_top.v")

    raw = V.parse_coverage_dat(str(dat))
    fixed = V.parse_coverage_dat(str(dat), mounts=_mounts(host))

    assert raw["totals"] == fixed["totals"]
    assert raw["format_detected"] == fixed["format_detected"]
    assert ([Path(k).name for k in sorted(raw["per_file"])]
            == [Path(k).name for k in sorted(fixed["per_file"])])
    assert (sorted(map(json.dumps, raw["per_file"].values()))
            == sorted(map(json.dumps, fixed["per_file"].values())))


def test_scope_files_inherits_the_normalised_spelling(tmp_path):
    """`scope_files` is derived from the per-file keys, so fixing the keys is
    what fixes the field #2180 was reported on."""
    host = tmp_path / "proj"
    host.mkdir()
    dat = _dat(tmp_path, f"{CONT}/rtl/top.v", f"{CONT}/tb/tb_top.v")
    cov = V.parse_coverage_dat(str(dat), mounts=_mounts(host))
    scoped = V.scope_totals(cov, [str(host / "rtl" / "top.v")])
    assert scoped["scope_files"] == [str(host / "rtl" / "top.v")]


def test_a_source_with_no_host_spelling_is_named_not_silently_kept(tmp_path):
    """An image-internal source legitimately has no host spelling. Keeping it
    is right; keeping it SILENTLY is what made the document unreadable."""
    host = tmp_path / "proj"
    host.mkdir()
    dat = _dat(tmp_path, f"{CONT}/rtl/top.v", "/foss/tools/share/prim.v")
    cov = V.parse_coverage_dat(str(dat), mounts=_mounts(host))

    assert "/foss/tools/share/prim.v" in cov["per_file"]
    assert cov["path_namespace"]["untranslated"] == ["/foss/tools/share/prim.v"]
    assert cov["path_namespace"]["translated_from_container"] == 1


def test_no_container_declared_is_recorded_as_not_examined(tmp_path):
    """The negative direction. Nothing is rewritten, and the payload says the
    paths were not examined instead of claiming they are host paths."""
    dat = _dat(tmp_path, f"{CONT}/rtl/top.v")
    cov = V.parse_coverage_dat(str(dat))
    assert list(cov["per_file"]) == [f"{CONT}/rtl/top.v"]
    ns = cov["path_namespace"]
    assert ns["examined"] is False
    assert ns["already_host"] == 0 and ns["translated_from_container"] == 0
    assert "no container was declared" in ns["note"]


def test_an_unreadable_mount_table_is_not_a_host_path(tmp_path):
    """`None` and `[]` are different answers. A container WAS declared and its
    mount table could not be read: every path is untranslatable and is NAMED,
    never passed off as a host path."""
    dat = _dat(tmp_path, f"{CONT}/rtl/top.v")
    cov = V.parse_coverage_dat(str(dat), mounts=[])
    ns = cov["path_namespace"]
    assert ns["examined"] is True
    assert ns["mounts_consulted"] == 0
    assert ns["untranslated"] == [f"{CONT}/rtl/top.v"]
    assert list(cov["per_file"]) == [f"{CONT}/rtl/top.v"]


def test_a_collision_leaves_both_rows_rather_than_deleting_one(tmp_path):
    """Two mounts of the same host tree make two container paths collapse onto
    one host path. Merging would delete a measured file's row, so both stay in
    the tool's namespace and the collision is named."""
    host = tmp_path / "proj"
    host.mkdir()
    mounts = [(host, "/foss/designs/proj"), (host, "/work/proj")]
    dat = _dat(tmp_path, "/foss/designs/proj/rtl/top.v", "/work/proj/rtl/top.v")
    cov = V.parse_coverage_dat(str(dat), mounts=mounts)
    assert len(cov["per_file"]) == 2, cov["per_file"]
    ns = cov["path_namespace"]
    assert sorted(ns["untranslated"]) == [
        "/foss/designs/proj/rtl/top.v", "/work/proj/rtl/top.v"]
    assert ns["collisions"]
    # The disclosure must ADD UP. Withdrawing the first of a colliding pair has
    # to withdraw its basis count with it — a disclosure whose numbers do not
    # account for every path examined is this same defect one level down.
    assert (ns["already_host"] + ns["translated_from_container"]
            + len(ns["untranslated"])) == 2


# ── end to end: the real gate, both directions, over a real tree ──

def _project_with(tmp_path: Path, scope_files, per_file_keys):
    proj = tmp_path / "proj"
    rtl = proj / "phase2" / "stage1" / "rtl"
    rtl.mkdir(parents=True)
    (rtl / "top.v").write_text("module top; endmodule\n")
    out = proj / "reports" / "phase2" / "coverage"
    out.mkdir(parents=True)
    (out / "coverage_verilator.json").write_text(json.dumps({
        "tool": "verilator",
        "measurement_mode": "measure-tb",
        "rtl_sources": [str(rtl / "top.v")],
        "totals": {"line": {"covered": 1, "total": 1, "pct": 100.0}},
        "scope_files": list(scope_files),
        "per_file": {k: {"line": {"covered": 1, "total": 1, "pct": 100.0}}
                     for k in per_file_keys},
    }, indent=2))
    return proj


def _gate(proj: Path):
    return subprocess.run([sys.executable, str(_GATE), str(proj)],
                          capture_output=True, text=True, timeout=300)


def test_the_gate_blocks_a_container_spelled_citation(tmp_path):
    """The causal half, stated on its own: the ONLY thing that moves the gate
    is the spelling of the same two files. A fixture pair — it says nothing
    about which spelling the producer emits, which is what the next test is
    for."""
    before = _project_with(
        tmp_path / "before",
        ["/foss/designs/proj/phase2/stage1/rtl/top.v"],
        ["/foss/designs/proj/phase2/stage1/rtl/top.v",
         "/foss/designs/proj/phase2/stage1/sim_full_stack/tb_top.v"])
    r = _gate(before)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "coverage_verilator.json" in r.stdout

    proj = tmp_path / "after" / "proj"
    after = _project_with(
        tmp_path / "after",
        [str(proj / "phase2" / "stage1" / "rtl" / "top.v")],
        [str(proj / "phase2" / "stage1" / "rtl" / "top.v")])
    r = _gate(after)
    assert r.returncode == 0, r.stdout + r.stderr


# ── the wiring: a fix nothing calls is not a fix ──

def _runnable_project(tmp_path: Path) -> Path:
    """A project the real discovery finds RTL and a testbench in."""
    proj = tmp_path / "proj"
    rtl = proj / "phase2" / "stage1" / "rtl"
    tbd = proj / "phase2" / "stage1" / "sim_full_stack"
    rtl.mkdir(parents=True)
    tbd.mkdir(parents=True)
    (rtl / "top.v").write_text(
        "module top(input clk, input d, output reg q);\n"
        "  always @(posedge clk) q <= d;\nendmodule\n")
    (tbd / "tb_top_full.v").write_text(
        "module tb_top_full;\n reg clk=0; reg d=0; wire q;\n"
        " top dut(.clk(clk), .d(d), .q(q));\n"
        " initial begin d=1; #5 clk=1; #5 $finish; end\nendmodule\n")
    return proj


def test_the_runner_declares_the_mount_table_to_the_parser(tmp_path,
                                                           monkeypatch):
    """DRIVEN, not grepped. `design_one_shot_runner.step_verilator_coverage`
    is the producer the front door actually runs; without this line the repair
    would ship in a function the flow never reaches with a container."""
    import shutil
    import design_one_shot_runner as R

    proj = _runnable_project(tmp_path)
    rtl = proj / "phase2" / "stage1" / "rtl"

    dat = _dat(tmp_path, f"{CONT}/phase2/stage1/rtl/top.v")
    monkeypatch.setattr(V, "verilate_tb_and_run",
                        lambda *a, **k: str(dat))
    monkeypatch.setattr(R, "_container_mounts",
                        lambda c: [(str(proj), CONT)])
    monkeypatch.setattr(R, "_local_exec_mode", lambda: False)
    monkeypatch.setattr(R, "_tool_in_container", lambda c, t: True)
    monkeypatch.setattr(shutil, "which", lambda *a, **k: "/usr/bin/verilator")

    res = R.step_verilator_coverage(proj, container="vibeic-eda-test")
    assert res.status == "PASS", res.detail

    payload = json.loads(
        (proj / "reports" / "phase2" / "coverage"
         / "coverage_verilator.json").read_text())
    assert payload["scope_files"] == [str(rtl / "top.v")]
    assert list(payload["per_file"]) == [str(rtl / "top.v")]
    assert payload["path_namespace"]["translated_from_container"] == 1


def test_the_runner_does_not_claim_a_translation_it_did_not_make(
        tmp_path, monkeypatch):
    """The other direction of the wiring: running the tool on this filesystem
    must leave the paths alone AND say it did not examine them."""
    import shutil
    import design_one_shot_runner as R

    proj = _runnable_project(tmp_path)
    rtl = proj / "phase2" / "stage1" / "rtl"

    dat = _dat(tmp_path, str(rtl / "top.v"))
    monkeypatch.setattr(V, "verilate_tb_and_run", lambda *a, **k: str(dat))
    monkeypatch.setattr(R, "_local_exec_mode", lambda: True)
    monkeypatch.setattr(shutil, "which", lambda *a, **k: "/usr/bin/verilator")

    res = R.step_verilator_coverage(proj, container="vibeic-eda-test")
    assert res.status == "PASS", res.detail
    payload = json.loads(
        (proj / "reports" / "phase2" / "coverage"
         / "coverage_verilator.json").read_text())
    assert payload["path_namespace"]["examined"] is False
    assert list(payload["per_file"]) == [str(rtl / "top.v")]


def test_the_report_the_producer_writes_passes_the_real_gate(tmp_path,
                                                             monkeypatch):
    """The two halves joined, and the one arm that cannot be satisfied by a
    fixture. The producer runs with a container declared and a tool that
    answered in the container's namespace; the file it writes is handed to the
    real `project_outputs_in_tree_check`. Before the fix this is rc 1, naming
    `coverage_verilator.json` — that is the run reported in #2180.
    """
    import shutil
    import design_one_shot_runner as R

    proj = _runnable_project(tmp_path)
    rtl = proj / "phase2" / "stage1" / "rtl"
    # The coverage.dat sits where the producer builds it (`sim/cov_build`,
    # INSIDE the project), so the gate below judges the WHOLE report the
    # producer wrote. It used to sit at `tmp_path` and the test popped
    # `coverage_dat` to hide it — which stopped covering the file the day the
    # suite union (6307e236d) began citing each member's dat again under
    # `per_testbench.<tb>.coverage_dat`: the gate then blocked on the
    # FIXTURE's own input, not on a path the tool named. Stripping one more
    # key would have chased the payload's shape; an in-tree input needs no
    # stripping at all.
    cov_build = proj / "phase2" / "stage1" / "sim" / "cov_build"
    cov_build.mkdir(parents=True)
    dat = _dat(cov_build, f"{CONT}/phase2/stage1/rtl/top.v",
               f"{CONT}/phase2/stage1/sim_full_stack/tb_top_full.v")
    monkeypatch.setattr(V, "verilate_tb_and_run", lambda *a, **k: str(dat))
    monkeypatch.setattr(R, "_container_mounts", lambda c: [(str(proj), CONT)])
    monkeypatch.setattr(R, "_local_exec_mode", lambda: False)
    monkeypatch.setattr(R, "_tool_in_container", lambda c, t: True)
    monkeypatch.setattr(shutil, "which", lambda *a, **k: "/usr/bin/verilator")

    assert R.step_verilator_coverage(proj,
                                     container="vibeic-eda-test").status == "PASS"

    cov_json = (proj / "reports" / "phase2" / "coverage"
                / "coverage_verilator.json")
    payload = json.loads(cov_json.read_text())

    r = _gate(proj)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "/foss/" not in json.dumps(payload["scope_files"])
    assert "/foss/" not in json.dumps(sorted(payload["per_file"]))
