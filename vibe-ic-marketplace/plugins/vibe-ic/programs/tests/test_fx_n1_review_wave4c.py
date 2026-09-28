"""FX-N1 re-review (review wave 4c, branch N1): the findings, each driven.

Both lenses returned LAND_AFTER_FIX. Every MAJOR and MINOR has a test here that
fails on the reviewed tip (the rebased N1 commit) for the reason the finding
states, and passes after the fix. The stall-kill MAJOR lives with the session
container it concerns (`test_fx_n1_cost_session_container`).

No test here reaches this host's docker or its PATH: the route is DECLARED
(`tests/_container_route`), the resolver's docker calls go to stand-ins, and
the host launcher of the runner is a recorder.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


#: The subject of every test here IS where an EDA tool runs and what is said
#: when it cannot, so a failure naming a tool this host lacks is still a red
#: about the route, never a NOT_VERIFIED about the host (`_outcome_states`).
pytestmark = pytest.mark.outcome_state_exempt(
    "subject: the EDA tool route itself (where a tool runs, and the refusal)")


PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _container_route as _route  # noqa: E402


# ── MAJOR 1: the #902 sim-toolchain record for the pinned-image leg ─────────

@pytest.fixture
def dosr(monkeypatch):
    import design_one_shot_runner as R
    getattr(R, "_SIM_TOOLCHAIN_SEEN", {}).clear()
    yield R
    getattr(R, "_SIM_TOOLCHAIN_SEEN", {}).clear()


def _launchers(monkeypatch, R):
    """Recorders for the runner's host launcher and container exec."""
    log = []
    monkeypatch.setattr(R, "_run", lambda argv, cwd=None, timeout=600, env=None:
                        log.append(("host", list(map(str, argv)))) or (0, "", ""))
    monkeypatch.setattr(R, "_docker_exec", lambda c, cmd, timeout=600, **_k:
                        log.append(("docker", cmd)) or (0, "", ""))
    return log


def _declared(monkeypatch, R, **fields):
    rec = {"declared_image_ref": None, "declared_image_id": None,
           "require_image": None, "declared_image_source": None}
    rec.update(fields)
    monkeypatch.setattr(R, "_declared_container_image",
                        lambda _p, _c: dict(rec))


def test_a_refused_pinned_image_route_is_recorded_not_run_and_the_host_is_never_probed(
        dosr, monkeypatch, tmp_path):
    """RED on the reviewed tip: the refusal was recorded as `execution_locality:
    host`, verdict DIVERGED, "ran on the HOST (<host version>)", after probing
    the host with `bash -lc`."""
    import _eda_tool_route as T
    _route.pin_container_route(monkeypatch)
    monkeypatch.setattr(dosr, "_tool_in_container", lambda c, t: False)
    _declared(monkeypatch, dosr, declared_image_ref="declared:ref")
    log = _launchers(monkeypatch, dosr)

    def refuse(argv, **_k):
        raise T.ToolRouteRefused("iverilog", T.NO_IMAGE_ROUTE,
                                 "stated: no pinned image on this host",
                                 {"tool": "iverilog", "route": None})
    monkeypatch.setattr(dosr._tool_route, "supervised_run", refuse)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    rc, _out, err = dosr._run_iverilog_stage(
        ["iverilog", "-o", str(run_dir / "x.vvp")], run_dir,
        "declared_container", timeout=60)
    assert rc == 127 and "NO_IMAGE_ROUTE" in err
    rec = json.loads((run_dir / dosr.SIM_TOOLCHAIN_RECORD).read_text())["records"][0]
    assert rec["verdict"] == "NOT_RUN", rec
    assert rec["execution_locality"] == "refused"
    assert "NO_IMAGE_ROUTE" in rec["refusal"] and "no pinned image" in rec["reason"]
    assert "HOST" not in rec["reason"].replace("host was not probed", "")
    assert rec["tool_version"] is None and rec["tool_path"] is None
    assert log == [], f"something ran or was probed: {log}"


def test_a_pinned_image_run_records_the_image_and_version_measured_on_that_route(
        dosr, monkeypatch, tmp_path):
    """RED on the reviewed tip: the record said "ran INSIDE container
    'declared_container'" with the declared image, and its version came from a
    probe of the DECLARED container, not of the image the stage ran on."""
    _route.pin_container_route(monkeypatch)
    monkeypatch.setattr(dosr, "_tool_in_container", lambda c, t: False)
    _declared(monkeypatch, dosr, declared_image_ref="declared:ref",
              declared_image_id="sha256:" + "d" * 64)
    log = _launchers(monkeypatch, dosr)
    cp = subprocess.CompletedProcess(["iverilog"], 0, "", "")
    cp.eda_route = {"tool": "iverilog", "route": "image",
                    "image": "ghcr.io/stated/image@sha256:" + "a" * 64,
                    "container": "vibeic-route-1-abcd"}
    monkeypatch.setattr(dosr._tool_route, "supervised_run", lambda argv, **_k: cp)
    asked = []

    def identify(tool, **kw):
        asked.append((tool, kw))
        return {"tool": tool, "route": "image", "image": kw.get("image"),
                "container": "vibeic-route-1-abcd",
                "image_digest": "sha256:" + "a" * 64,
                "image_id": "sha256:" + "d" * 64,
                "path": "/foss/tools/bin/iverilog",
                "version": "Icarus Verilog version 13.0 (IMAGE BANNER)", "why": ""}
    monkeypatch.setattr(dosr._tool_route, "identify", identify, raising=False)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    dosr._run_iverilog_stage(["iverilog", "-o", str(run_dir / "x.vvp")],
                             run_dir, "declared_container", timeout=60)
    rec = json.loads((run_dir / dosr.SIM_TOOLCHAIN_RECORD).read_text())["records"][0]
    assert rec["execution_locality"] == "pinned_image"
    assert asked == [("iverilog", {"image": cp.eda_route["image"]})], asked
    assert rec["tool_version"] == "Icarus Verilog version 13.0 (IMAGE BANNER)"
    assert rec["routed_image_ref"] == cp.eda_route["image"]
    assert rec["routed_image_id"] == "sha256:" + "d" * 64
    assert rec["verdict"] == "MATCH"
    assert "INSIDE container" not in rec["reason"]
    assert "pinned image" in rec["reason"] and "IMAGE BANNER" in rec["reason"]
    assert not [c for c in log if "__VIBEIC_TOOL_PATH__" in str(c)], \
        f"the declared container / host was probed for the identity: {log}"


def test_a_pinned_image_that_is_not_the_declared_image_is_diverged(
        dosr, monkeypatch, tmp_path):
    _route.pin_container_route(monkeypatch)
    monkeypatch.setattr(dosr, "_tool_in_container", lambda c, t: False)
    _declared(monkeypatch, dosr, declared_image_ref="declared:ref",
              declared_image_id="sha256:" + "d" * 64)
    _launchers(monkeypatch, dosr)
    cp = subprocess.CompletedProcess(["iverilog"], 0, "", "")
    cp.eda_route = {"tool": "iverilog", "route": "image", "image": "other:ref"}
    monkeypatch.setattr(dosr._tool_route, "supervised_run", lambda argv, **_k: cp)
    monkeypatch.setattr(dosr._tool_route, "identify", lambda tool, **kw: {
        "tool": tool, "route": "image", "image": "other:ref", "container": "c",
        "image_digest": None, "image_id": "sha256:" + "e" * 64,
        "path": "/x/iverilog", "version": "v", "why": ""}, raising=False)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    dosr._run_iverilog_stage(["iverilog", "-o", str(run_dir / "x.vvp")],
                             run_dir, "declared_container", timeout=60)
    rec = json.loads((run_dir / dosr.SIM_TOOLCHAIN_RECORD).read_text())["records"][0]
    assert rec["verdict"] == "DIVERGED" and "USED another" in rec["reason"]


def test_identify_asks_the_route_itself_and_reads_the_running_image(monkeypatch):
    """The measurement MAJOR 1 needed: path and banner asked INSIDE the image
    route's container, and the image read from what that container runs."""
    import _eda_tool_route as T
    import _eda_pin
    monkeypatch.setattr(T, "resolve", lambda tool, argv, **kw: T.Route(
        tool=tool, kind=T.ROUTE_IMAGE, image="stated:ref"))
    monkeypatch.setattr(T, "session_container", lambda image, dirs, tool: "sess-1")
    monkeypatch.setattr(T._ce, "docker_exec_argv",
                        lambda c, *cmd, opts=(): ["docker", "exec", c, *cmd])
    ran = []

    def fake_run(argv, **kw):
        ran.append(argv)
        return subprocess.CompletedProcess(argv, 0, (
            "[INFO] login noise\n"
            f"{T._ID_PATH_MARK}/foss/tools/bin/vvp\n"
            f"{T._ID_VER_MARK}Icarus Verilog runtime version 13.0\n"), "")
    monkeypatch.setattr(T.subprocess, "run", fake_run)
    monkeypatch.setattr(_eda_pin, "container_image_digest",
                        lambda c: ("sha256:" + "a" * 64, ""))
    monkeypatch.setattr(_eda_pin, "container_image_id",
                        lambda c: ("sha256:" + "b" * 64, ""))
    got = T.identify("vvp")
    assert ran and ran[0][:4] == ["docker", "exec", "sess-1", "sh"]
    assert got["path"] == "/foss/tools/bin/vvp"
    assert got["version"] == "Icarus Verilog runtime version 13.0"
    assert got["image"] == "stated:ref" and got["container"] == "sess-1"
    assert got["image_digest"] == "sha256:" + "a" * 64
    assert got["image_id"] == "sha256:" + "b" * 64 and got["why"] == ""


def test_identify_on_a_refused_route_runs_nothing(monkeypatch):
    import _eda_tool_route as T

    def refuse(tool, argv, **kw):
        raise T.ToolRouteRefused(tool, T.NO_IMAGE_ROUTE, "stated")
    monkeypatch.setattr(T, "resolve", refuse)
    monkeypatch.setattr(T.subprocess, "run",
                        lambda *a, **k: pytest.fail("something was run"))
    got = T.identify("iverilog")
    assert got["route"] is None and "NO_IMAGE_ROUTE" in got["why"]
    assert got["path"] is None and got["version"] is None


# ── MAJOR 2: magic is routed like every other tool ──────────────────────────

def test_a_named_container_is_asked_for_magic_before_and_instead_of_the_host(monkeypatch):
    """RED on the reviewed tip: `find_magic_site` tried `MagicSite("")` FIRST,
    whose `has_magic` asked `_eda_tool_route.available("magic", local=True)`,
    so a host magic on PATH was preferred over the named container."""
    import digital_hardmacro_gen as H
    _route.pin_container_route(monkeypatch)
    asked_local = []
    monkeypatch.setattr(H._tool_route, "available",
                        lambda tool, **kw: asked_local.append((tool, kw)) or True)
    asked = []

    def in_container(container, cmd, ceiling_s=120.0, **_k):
        asked.append((container, cmd))
        return subprocess.CompletedProcess([], 0, "", "")
    monkeypatch.setattr(H._container_exec, "run_in_container_supervised", in_container)
    site = H.find_magic_site("eda_ctr")
    assert site is not None and site.in_container and site.container == "eda_ctr"
    assert asked and asked[0][0] == "eda_ctr" and "command -v magic" in asked[0][1]
    assert asked_local == [], f"the host was asked first: {asked_local}"


def test_this_environment_is_asked_only_when_it_is_the_route(monkeypatch):
    """The caller's deliberate choice ('' / 'host') and no docker client keep
    the local site; that half must not be lost in the fix."""
    import digital_hardmacro_gen as H
    monkeypatch.setattr(H._tool_route, "available", lambda tool, **kw: True)
    _route.pin_container_route(monkeypatch)
    assert H.find_magic_site("host").in_container is False
    assert H.find_magic_site("").in_container is False
    _route.pin_local_route(monkeypatch)
    assert H.find_magic_site("eda_ctr").in_container is False


def test_the_absent_reason_does_not_claim_a_place_that_was_not_searched(monkeypatch):
    import digital_hardmacro_gen as H
    import _eda_pin
    _route.pin_container_route(monkeypatch)
    monkeypatch.setattr(_eda_pin, "container_attach_refusal", lambda c, env=None: "")
    why = H.magic_absent_reason("eda_ctr")
    assert "inside container 'eda_ctr'" in why
    assert "not on PATH in this environment" not in why, why


# ── MINORs in the resolver ──────────────────────────────────────────────────

from test_fx_n1_host_tool_locality import _execs, farm  # noqa: E402,F401
from test_fx_n1_cost_session_container import docker_farm  # noqa: E402,F401


def test_include_flags_and_plusarg_dirs_are_mounted(tmp_path):
    """RED on the reviewed tip: `-I/abs` and `+incdir+/abs` were not paths."""
    import _eda_tool_route as T
    inc, inc2, inc3 = (tmp_path / n for n in ("inc", "inc2", "inc3"))
    for d in (inc, inc2, inc3):
        d.mkdir()
    run = tmp_path / "run"
    run.mkdir()
    dirs = T.bind_dirs(["iverilog", f"-I{inc}", f"+incdir+{inc2}+{inc3}", "a.v"],
                       cwd=str(run))
    for d in (inc, inc2, inc3):
        assert str(d) in dirs, dirs


def test_a_symlinked_directory_is_mounted_as_named_and_as_resolved(tmp_path):
    """RED on the reviewed tip: only the resolved directory was mounted, so
    the name argv keeps did not resolve in the container."""
    import _eda_tool_route as T
    real = tmp_path / "real" / "proj"
    real.mkdir(parents=True)
    (real / "a.v").write_text("module a; endmodule\n")
    link = tmp_path / "linkproj"
    link.symlink_to(real, target_is_directory=True)
    elsewhere = tmp_path / "cwd"
    elsewhere.mkdir()
    dirs = T.bind_dirs(["iverilog", f"{link}/a.v"], cwd=str(elsewhere))
    assert str(link) in dirs and str(real) in dirs, dirs


def test_a_built_program_run_as_a_tool_has_its_own_directory_mounted(
        docker_farm, tmp_path):
    """RED on the reviewed tip: `_paths_of` skipped argv[0] even when
    `as_tool` executes it in the container."""
    import _eda_tool_route as T
    build, run = tmp_path / "build", tmp_path / "run"
    build.mkdir()
    run.mkdir()
    (build / "Vtop").write_text("")
    _full, route = T.argv_for([str(build / "Vtop"), "+verilator+seed+1"],
                              cwd=str(run), as_tool="verilator")
    assert str(build) in route.mounts, route.mounts


def test_a_path_inside_an_image_owned_root_is_refused_naming_it(docker_farm, tmp_path):
    """RED on the reviewed tip: the path was silently not mounted and the tool
    ran without its input ('No such file'), which callers read as a design
    error. `/usr/share` exists on every host this runs on."""
    import _eda_tool_route as T
    assert Path("/usr/share").is_dir()
    with pytest.raises(T.ToolRouteRefused) as exc:
        T.argv_for(["iverilog", "/usr/share/fx_n1_probe_dir/a.v"], cwd=str(tmp_path))
    assert exc.value.code == T.NO_IMAGE_ROUTE
    assert "/usr/share/fx_n1_probe_dir/a.v" in exc.value.reason
    assert not [c for c in docker_farm.docker_calls() if c.startswith("exec ")]


def test_image_paths_and_kernel_paths_are_neither_mounted_nor_refused(docker_farm, tmp_path):
    """The guard for the refusal: `/foss/...` names the image's own PDK or
    toolchain (whatever the host has there) and `/dev/null` means the same in
    the container."""
    import _eda_tool_route as T
    _full, route = T.argv_for(["yosys", "-p", "read_liberty /foss/pdks/x.lib",
                               "-o", "/dev/null"], cwd=str(tmp_path))
    assert route.kind == T.ROUTE_IMAGE
    assert not [m for m in route.mounts if m.startswith(("/foss", "/dev"))], route.mounts


def _named_container_holding(monkeypatch, T, tmp_path, digest):
    import _eda_pin
    monkeypatch.setattr(_eda_pin, "container_attach_refusal", lambda c, env=None: "")
    monkeypatch.setattr(T, "_container_state",
                        lambda c: (True, "", [(str(tmp_path), str(tmp_path))]))
    monkeypatch.setattr(_eda_pin, "container_image_digest", lambda c: (digest, ""))
    monkeypatch.setattr(_eda_pin, "container_image_reference",
                        lambda c: (f"ghcr.io/stated/in-container@{digest}", ""))


def test_an_explicit_image_is_not_overridden_by_a_container_running_other_bytes(
        docker_farm, tmp_path, monkeypatch):
    """RED on the reviewed tip: `argv_for(..., image=X)` ran in the named
    container whatever it held, and recorded X as the image."""
    import _eda_tool_route as T
    _named_container_holding(monkeypatch, T, tmp_path, "sha256:" + "b" * 64)
    explicit = "ghcr.io/stated/declared@sha256:" + "a" * 64
    _full, route = T.argv_for(["yosys", "-V"], cwd=str(tmp_path),
                              container="lane-eda", image=explicit)
    assert route.kind == T.ROUTE_IMAGE and route.image == explicit, route.record()
    assert any("not the explicit image" in n for n in route.notes), route.notes


def test_a_container_running_the_explicit_image_is_used_and_its_actual_image_recorded(
        docker_farm, tmp_path, monkeypatch):
    import _eda_tool_route as T
    digest = "sha256:" + "a" * 64
    _named_container_holding(monkeypatch, T, tmp_path, digest)
    _full, route = T.argv_for(["yosys", "-V"], cwd=str(tmp_path),
                              container="lane-eda",
                              image=f"ghcr.io/stated/declared@{digest}")
    assert route.kind == T.ROUTE_CONTAINER and route.container == "lane-eda"
    assert route.image == f"ghcr.io/stated/in-container@{digest}", route.record()


# ── MINOR: step_yosys_synth with its container missing ──────────────────────

from test_fx_n1_host_tool_locality import _NETLIST, _synth_project  # noqa: E402


def _container_gone(monkeypatch, R):
    """The step's own container machinery reports the container absent: no
    mount covers the tree and no staging dir can be created in it."""
    monkeypatch.setattr(R, "_path_in_container", lambda p, c: False)
    monkeypatch.setattr(R, "_phase2_container_workdir", lambda c, p, s: (None, False))
    execs = []
    monkeypatch.setattr(R, "_run", lambda cmd, **kw: execs.append(list(cmd)) or (0, "", ""))
    return execs


def test_synth_with_its_container_missing_is_not_measured_when_the_image_is_refused(
        tmp_path, monkeypatch):
    """RED on the reviewed tip: rc 127 "container down?" and the step FAILED,
    a fact about the host booked against the design."""
    import design_one_shot_runner as R
    import _eda_tool_route as T
    _route.pin_container_route(monkeypatch)
    proj = _synth_project(tmp_path)
    execs = _container_gone(monkeypatch, R)

    def refuse(argv, **kw):
        raise T.ToolRouteRefused("yosys", T.NO_IMAGE_ROUTE, "stated: no pinned image")
    monkeypatch.setattr(R._tool_route, "supervised_run", refuse)
    res = R.step_yosys_synth(proj, "counter", container="gone-eda")
    assert res.status == "NOT_MEASURED", (res.status, res.detail)
    assert "no pinned image" in res.detail and "gone-eda" in res.detail
    assert not [c for c in execs if "yosys" in " ".join(c)], execs


def test_synth_with_its_container_missing_runs_on_the_pinned_image(tmp_path, monkeypatch):
    """RED on the reviewed tip: the step never reached the image."""
    import design_one_shot_runner as R
    _route.pin_container_route(monkeypatch)
    proj = _synth_project(tmp_path)
    synth_dir = R._pl.synth_dir(proj)
    _container_gone(monkeypatch, R)
    asked = []

    def routed(argv, **kw):
        asked.append((list(argv), kw.get("container")))
        (synth_dir / "netlist_yosys.v").write_text(_NETLIST)
        cp = subprocess.CompletedProcess(argv, 0, "Number of cells: 12", "")
        cp.eda_route = {"route": "image", "image": "stated:image",
                        "container": "vibeic-route-1-beef"}
        return cp
    monkeypatch.setattr(R._tool_route, "supervised_run", routed)
    res = R.step_yosys_synth(proj, "counter", container="gone-eda")
    assert asked and asked[0][0][:2] == ["yosys", "-p"] and asked[0][1] == "gone-eda"
    assert res.status == "PASS", (res.status, res.detail)
    log = (synth_dir / "yosys.log").read_text()
    assert "could not be used" in log and "vibeic-route-1-beef" in log, log


def test_synth_image_route_keeps_a_progressing_job_past_its_idle_tolerance(
        tmp_path, monkeypatch):
    """A synthesis that still advances after the old deadline must finish."""
    import design_one_shot_runner as R
    _route.pin_container_route(monkeypatch)
    proj = _synth_project(tmp_path)
    synth_dir = R._pl.synth_dir(proj)
    _container_gone(monkeypatch, R)
    monkeypatch.setattr(R, "_phase2_synth_timeout_s", lambda: 1)
    calls = []

    def wall_run(argv, **kw):
        calls.append(("wall", kw))
        raise subprocess.TimeoutExpired(argv, kw["timeout"], output="working")

    def progress_run(argv, **kw):
        calls.append(("progress", kw))
        (synth_dir / "netlist_yosys.v").write_text(_NETLIST)
        cp = subprocess.CompletedProcess(argv, 0, "Number of cells: 12", "")
        cp.eda_route = {"route": "image", "image": "stated:image",
                        "container": "vibeic-route-progress"}
        return cp

    monkeypatch.setattr(R._tool_route, "run", wall_run)
    monkeypatch.setattr(R._tool_route, "supervised_run", progress_run)
    res = R.step_yosys_synth(proj, "counter", container="gone-eda")
    assert res.status == "PASS", (res.status, res.detail, calls)
    assert [kind for kind, _ in calls] == ["progress"]
    assert calls[0][1]["stall_looks"] * calls[0][1]["poll_s"] == 1
    assert "timeout" not in calls[0][1]


def test_routed_sim_stage_uses_progress_supervision(tmp_path, monkeypatch):
    """The common Icarus/Verilator route also treats its limit as idle time."""
    import design_one_shot_runner as R
    seen = []

    def wall_run(argv, **kw):
        seen.append(("wall", kw))
        return subprocess.CompletedProcess(argv, 124, "working",
                                           "TIMEOUT: still working")

    def progress_run(argv, **kw):
        seen.append(("progress", kw))
        cp = subprocess.CompletedProcess(argv, 0, "finished", "")
        cp.eda_route = {"route": "image", "image": "stated:image"}
        return cp

    monkeypatch.setattr(R._tool_route, "run", wall_run)
    monkeypatch.setattr(R._tool_route, "supervised_run", progress_run)
    rc, out, err, route = R._routed_tool_stage(
        ["iverilog", "-V"], tmp_path, timeout=1)
    assert (rc, out, err) == (0, "finished", "")
    assert route["route"] == "image"
    assert [kind for kind, _ in seen] == ["progress"]
    assert seen[0][1]["stall_looks"] * seen[0][1]["poll_s"] == 1


def test_routed_stage_reports_an_actual_stall(tmp_path, monkeypatch):
    import design_one_shot_runner as R
    import _progress_run as P

    def stalled(argv, **_kw):
        raise P.Stalled(argv, 4, 0.25, 1.0,
                        {"output": True, "cpu": True}, out="tick")

    monkeypatch.setattr(R._tool_route, "supervised_run", stalled)
    monkeypatch.setattr(R._tool_route, "run", lambda argv, **kw:
                        subprocess.CompletedProcess(argv, 0, "done", ""))
    rc, out, err, _route_record = R._routed_tool_stage(
        ["iverilog", "-V"], tmp_path, timeout=1)
    assert rc == 124 and out == "tick" and "STALLED" in err
    assert "TIMEOUT" not in err


# ── MINOR: tool names held in variables and constants ───────────────────────

def _fake_host_tool(bindir: Path, name: str, marker: Path) -> None:
    t = bindir / name
    t.write_text(f"#!/bin/sh\necho \"$@\" >> {marker}\necho 'HOST {name}'\nexit 0\n")
    t.chmod(0o755)


def _image_carries(F, *tools):
    """The farm's docker answers the resolver's image-contents probe (`command
    -v ... && echo <tool>`) with `tools`."""
    d = F.bin / "docker"
    d.write_text(d.read_text().replace(
        "  exec) ", "  exec) case \"$*\" in *\"command -v\"*) echo "
        + " ".join(tools) + "; exit 0;; esac; "))


def test_selfcheck_lint_runs_verilator_on_the_route_not_the_host(docker_farm, tmp_path):
    """RED on the reviewed tip: `cand = override or env or "verilator"` then
    `shutil.which(cand)` ran the HOST verilator."""
    import verilog_selfcheck_lint as V
    _fake_host_tool(docker_farm.bin, "verilator", docker_farm.marker)
    _image_carries(docker_farm, "verilator")
    r = V.selfcheck_lint("module m; endmodule\n", top="m")
    assert not docker_farm.marker.exists(), "the host verilator was run"
    assert _execs(docker_farm, "verilator --lint-only"), docker_farm.docker_calls()
    assert r["status"] in ("PASS", "FAIL"), r


def test_selfcheck_lint_skips_with_the_routes_reason(docker_farm, monkeypatch):
    import verilog_selfcheck_lint as V
    monkeypatch.setattr(V._tool_route, "why_unavailable",
                        lambda tool, **kw: "NO_IMAGE_ROUTE: verilator: stated")
    r = V.selfcheck_lint("module m; endmodule\n", top="m")
    assert r["status"] == "SKIP" and r["skip_reason"].startswith("NO_IMAGE_ROUTE")


def test_testbench_dispatch_with_no_container_runs_on_the_route_not_the_host(
        docker_farm, tmp_path):
    """RED on the reviewed tip: with no container the argv ran on the host."""
    import testbench_gen as TB
    _fake_host_tool(docker_farm.bin, "verilator", docker_farm.marker)
    rc, _log = TB.default_dispatch(["verilator", "--version"], tmp_path, None,
                                   "verilator", 60)
    assert not docker_farm.marker.exists(), "the host verilator was run"
    assert _execs(docker_farm, "verilator --version"), docker_farm.docker_calls()
    assert rc == 0


def test_a_program_the_simulator_built_runs_where_the_simulator_runs(docker_farm, tmp_path):
    import testbench_gen as TB
    sim = tmp_path / "obj_dir" / "Vtop"
    sim.parent.mkdir()
    sim.write_text("")
    rc, _log = TB.default_dispatch([str(sim)], tmp_path, None, "verilator", 60)
    assert _execs(docker_farm, str(sim)), docker_farm.docker_calls()


# ── MINOR: a gate's "cannot run" text carries the ROUTE's reason ────────────
#
# `available()` is a bool. Gates that turned False into "iverilog/vvp absent",
# "not on PATH" or "install iverilog" told an operator on a host whose pinned
# image was merely not pulled to install a HOST tool -- the substitution the
# route removes. The scan below finds every `if not <availability>:` body in
# the shipped programs (an availability is `_tool_route.available(...)` or a
# module function whose return value is one) and forbids that wording in it.

import ast as _ast  # noqa: E402
import re as _re  # noqa: E402

#: "absent" alone is the wording the review found; "absent from this run's
#: tool route (<the route's reason>)" is the accurate form (it names the route
#: and carries the reason), and keeps the disclosure landed tests assert on.
_WRONG_ABSENCE = _re.compile(
    r"not on PATH|\babsent\b(?! from this run's tool)|apt install"
    r"|install (iverilog|verilator|klayout|yosys)|strmrun/klayout on PATH", _re.I)
_SKIP_DIRS = {"tests", "librelane_plugins", "calibration"}


def _reason_names(tree) -> set:
    """Names bound to a route REASON (`_tool_route.unavailable(...)` /
    `why_unavailable(...)`): `if why:` guards a cannot-run branch too, so a
    reverted message under the converted guard is still found."""
    out = set()
    for n in _ast.walk(tree):
        if isinstance(n, _ast.Assign) and isinstance(n.value, _ast.Call) \
                and isinstance(n.value.func, _ast.Attribute) \
                and n.value.func.attr in ("unavailable", "why_unavailable") \
                and isinstance(n.value.func.value, _ast.Name) \
                and n.value.func.value.id == "_tool_route":
            out |= {t.id for t in n.targets if isinstance(t, _ast.Name)}
    return out


def _availability_helpers(tree) -> set:
    out = set()
    for fn in _ast.walk(tree):
        if isinstance(fn, _ast.FunctionDef):
            for r in _ast.walk(fn):
                if isinstance(r, _ast.Return) and r.value is not None and any(
                        _is_available_call(c) for c in _ast.walk(r.value)):
                    out.add(fn.name)
    return out


def _is_available_call(n, helpers=frozenset()) -> bool:
    if not isinstance(n, _ast.Call):
        return False
    f = n.func
    if isinstance(f, _ast.Attribute) and f.attr == "available" \
            and isinstance(f.value, _ast.Name) and f.value.id == "_tool_route":
        return True
    return isinstance(f, _ast.Name) and f.id in helpers


def scan_absence_wording(stem: str, src: str):
    tree = _ast.parse(src)
    helpers = _availability_helpers(tree)
    reasons = _reason_names(tree)
    found = []
    for n in _ast.walk(tree):
        if isinstance(n, _ast.If) and any(
                _is_available_call(c, helpers)
                or (isinstance(c, _ast.Name) and c.id in reasons)
                for c in _ast.walk(n.test)):
            for c in _ast.walk(_ast.Module(body=n.body, type_ignores=[])):
                if isinstance(c, _ast.Constant) and isinstance(c.value, str) \
                        and _WRONG_ABSENCE.search(c.value):
                    found.append(f"{stem}:{c.lineno} {c.value[:70]!r}")
        if isinstance(n, _ast.Constant) and isinstance(n.value, str) \
                and "strmrun/klayout on PATH" in n.value:
            found.append(f"{stem}:{n.lineno} {n.value[:70]!r}")
    return found


def test_no_gate_reports_an_unavailable_route_as_a_missing_host_tool():
    """RED on the reviewed tip: 15+ gates (diff_verify_harness, latency,
    clause/spec/prompt smoke TBs, regmap, harness_exact, waveform table,
    protocol reference TB, the KLayout fill/seal/antenna callers ...)."""
    offenders = []
    for p in sorted(PROGRAMS.rglob("*.py")):
        rel = p.relative_to(PROGRAMS)
        if any(part in _SKIP_DIRS for part in rel.parts[:-1]) or p.stem == "_eda_tool_route":
            continue
        try:
            offenders += scan_absence_wording(p.stem, p.read_text(errors="replace"))
        except SyntaxError:
            continue
    assert offenders == [], "\n  ".join([f"{len(offenders)} site(s):"] + offenders)


def test_the_absence_wording_scan_is_not_vacuous():
    planted = '''
def have():
    return _tool_route.available("iverilog")
def a():
    if not _tool_route.available("vvp"):
        return "iverilog/vvp absent"
def b():
    if not have():
        return "not on PATH -- install iverilog"
def c():
    why = _tool_route.unavailable("iverilog")
    if why:
        return f"cannot run on this route ({why})"
MSG = "no strmrun/klayout on PATH"
def d():
    why_not = _tool_route.unavailable("vvp")
    if why_not:
        return "vvp absent"
'''
    got = scan_absence_wording("planted", planted)
    assert {int(g.split(":")[1].split()[0]) for g in got} == {6, 9, 14, 18}, got


def test_a_gate_names_the_route_refusal(monkeypatch, tmp_path):
    """Driven, not scanned: with the route refused for a stated reason, the
    gate's reason carries THAT reason and no host-tool advice. RED on the
    reviewed tip: 'iverilog absent' / 'verilator absent'."""
    import harness_exact_selfverify as H
    monkeypatch.setattr(H._tool_route, "why_unavailable",
                        lambda tool, **kw: f"NO_IMAGE_ROUTE: {tool}: stated reason")
    rtl = tmp_path / "top.sv"
    rtl.write_text("module top; endmodule\n")
    for gate in (H.gate_a_standalone_compile, H.gate_b_verilator_lint):
        g = gate(rtl, "top", tmp_path, False)
        assert g["verdict"] == "SKIP", g
        assert "NO_IMAGE_ROUTE" in g["reason"] and "stated reason" in g["reason"], g
        assert not _WRONG_ABSENCE.search(g["reason"]), g["reason"]


def test_the_klayout_reason_names_only_where_it_looked(monkeypatch):
    import _klayout_launch as K
    import _eda_pin
    monkeypatch.delenv("VIBEIC_KLAYOUT_FORCE_ABSENT", raising=False)
    _route.pin_container_route(monkeypatch)
    monkeypatch.setattr(_eda_pin, "container_attach_refusal", lambda c, env=None: "")
    why = K.why_no_runner("lane-eda")
    assert "host PATH is not asked" in why and "'lane-eda'" in why
    # the places it does NOT look are not blamed
    assert "strmrun" not in why and "process's PATH" not in why, why
    _route.pin_local_route(monkeypatch)
    assert "no docker client" in K.why_no_runner("lane-eda")


def test_fmeda_does_not_blame_the_host_for_a_missing_image(monkeypatch):
    import fmeda_fault_injection_coverage as F
    _route.pin_container_route(monkeypatch)
    monkeypatch.setattr(F, "_local_docker_image", lambda: None)
    backend, _img, reason = F.resolve_injection_backend()
    assert backend == F.BACKEND_NONE
    assert "host has no iverilog" not in reason and "pinned image" in reason, reason


def test_the_professional_tb_gap_on_a_container_route_does_not_blame_the_local_path(
        tmp_path, monkeypatch):
    """The sibling of test_sha256_capture...::test_step_still_reports_unreachable
    (which now declares the LOCAL route). RED on the reviewed tip: "nor on the
    local PATH" on a host where a container route exists and the local PATH
    is never asked."""
    import sys as _sys
    import design_one_shot_runner as R
    out = tmp_path / "phase2" / "stage1" / "sim_professional" / "dut"
    out.mkdir(parents=True)
    generated = {"status": "PASS", "dut_kind": "expert_reference",
                 "out_dir": str(out), "reference_model_tier": "expert_filled",
                 "files": []}
    monkeypatch.setitem(_sys.modules, "professional_tb_gen",
                        type("ptb", (), {"generate": staticmethod(lambda p: generated)}))
    monkeypatch.setattr(R, "_tool_in_container", lambda c, t: False)
    monkeypatch.setattr(R, "_local_cocotb_toolchain_present", lambda: True)
    _route.pin_container_route(monkeypatch)
    res = R.step_professional_tb_gen(tmp_path, "dut", "eda")
    assert res.status == "NOT_MEASURED"
    assert "nor on the local PATH" not in res.detail, res.detail
    assert "'eda'" in res.detail and "local PATH was not asked" in res.detail
