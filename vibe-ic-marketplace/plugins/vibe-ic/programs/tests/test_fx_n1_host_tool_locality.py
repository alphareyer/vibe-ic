"""FX-N1 — a host EDA tool must not shadow the pinned image.

MEASURED 2026-09-28 on 8HD-9 (192.168.1.105), spm through the canonical front
door with `--require-image` satisfied: Step 1 FAILed "Yosys elaboration failed"
because `p0_tool_frontend_check` ran the HOST's /usr/bin/yosys 0.9
("ERROR: No such command: read_slang"); the same script in the pinned image
returns 0. `step_yosys_synth` also ran the host yosys first and reached the
container only after it died on `dffunmap`.

THE RULE UNDER TEST (`_eda_tool_route`): when a container route exists (a
docker client on PATH) the tool runs in the resolved image and the host PATH is
never asked; the LOCAL route is taken only when there is no container route,
and there the tool's version is recorded and checked against what the step
needs (for yosys: every command its script runs), refusing with the reason.

HOW THE MACHINE IS KEPT OUT OF IT. PATH is replaced by ONE directory holding a
fake yosys that says it is 0.9 and does not list `read_slang`/`dffunmap`, and,
when the test is about the container route, a fake docker that records its
argv. Nothing else is on PATH, so the real /usr/bin/yosys and /usr/bin/docker
of the host running the suite cannot answer (a PATH that merely starts with
the fixture would still reach them; each test asserts `which()` hits the farm).
Only the tools' behaviour is faked; the resolver, the route predicate and every
program under test run for real.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest


#: The subject of every test here IS where an EDA tool runs and what is said
#: when it cannot, so a failure naming a tool this host lacks is still a red
#: about the route, never a NOT_VERIFIED about the host (`_outcome_states`).
pytestmark = pytest.mark.outcome_state_exempt(
    "subject: the EDA tool route itself (where a tool runs, and the refusal)")


_PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROGRAMS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _stated_eda_image import state_the_image  # noqa: E402

# The help listing of the fake yosys: real `yosys -Q -p help` shape (four
# spaces, name, description), WITHOUT read_slang and dffunmap — as 0.9's.
_OLD_HELP = [
    "abc", "clean", "flatten", "hierarchy", "opt", "proc", "read_verilog",
    "stat", "synth", "techmap", "write_json", "write_verilog",
]


def _fake_yosys(bindir: Path, marker: Path, commands=_OLD_HELP) -> Path:
    listing = "".join(f"    {c:<20} fake description\\n" for c in commands)
    y = bindir / "yosys"
    y.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = -V ]; then echo 'Yosys 0.9 (git sha1 1979e0b)'; exit 0; fi\n"
        "if [ \"$1\" = -Q ] && [ \"$2\" = -p ] && [ \"$3\" = help ]; then\n"
        f"  printf '{listing}'; exit 0; fi\n"
        f"echo \"$@\" >> {marker}\n"
        "echo 'ERROR: No such command: read_slang' >&2\n"
        "exit 1\n")
    y.chmod(0o755)
    return y


def _fake_docker(bindir: Path, log: Path) -> Path:
    """A docker CLI stand-in that logs every argv and speaks just enough of the
    protocol the resolver uses: `run -d` prints a container id; `exec` exits
    0, unless `<log>.die` exists (then it answers with docker's own daemon
    error for a vanished container, once per line in that file); `ps` prints
    `<log>.ps` when present; `inspect` / `image` exit 1 (nothing readable);
    everything else exits 0."""
    d = bindir / "docker"
    # Shell BUILTINS only: PATH is this one directory, so no sed/cat exists.
    d.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> {log}\n"
        "case \"$1\" in\n"
        "  run) case \" $* \" in *\" -d \"*) echo fakecontainerid0123;; esac; exit 0;;\n"
        f"  exec) if [ -s {log}.die ]; then rest=''; first=1; "
        f"while IFS= read -r l; do if [ $first = 1 ]; then first=0; else rest=\"$rest$l\\n\"; fi; done < {log}.die; "
        f"printf '%b' \"$rest\" > {log}.die; "
        "echo 'Error response from daemon: No such container: gone' >&2; exit 1; fi; exit 0;;\n"
        f"  ps) if [ -f {log}.ps ]; then while IFS= read -r l; do printf '%s\\n' \"$l\"; done < {log}.ps; fi; exit 0;;\n"
        "  inspect|image) exit 1;;\n"
        "esac\n"
        "exit 0\n")
    d.chmod(0o755)
    return d


def _session_runs(F):
    """The `docker run -d` calls that started a session container."""
    return [c for c in F.docker_calls() if c.startswith("run ") and " -d " in f" {c} "]


def _execs(F, needle=""):
    return [c for c in F.docker_calls() if c.startswith("exec ") and needle in c]


@pytest.fixture
def farm(tmp_path, monkeypatch):
    """PATH = one directory. Returns a namespace the tests fill in."""
    bindir = tmp_path / "farm"
    bindir.mkdir()

    class F:
        bin = bindir
        marker = tmp_path / "host_yosys_ran.txt"
        docker_log = tmp_path / "docker_argv.txt"

        @staticmethod
        def docker_calls():
            if not F.docker_log.is_file():
                return []
            return [ln for ln in F.docker_log.read_text().splitlines() if ln]

    monkeypatch.setenv("PATH", str(bindir))
    for var in ("VIBEIC_EDA_CONTAINER", "EDA_CONTAINER", "VIBEIC_EDA_IMAGE",
                "IIC_EDA_IMAGE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("VIBEIC_DOCKER_MEMORY", "2g")
    try:
        import _eda_tool_route
        _eda_tool_route.reset_caches()
    except ImportError:          # the tree before the fix has no resolver
        pass
    import _container_exec as _ce
    monkeypatch.setattr(_ce, "_ANNOUNCED", set())
    yield F
    try:
        import _eda_tool_route
        _eda_tool_route.reset_caches()
    except ImportError:
        pass


def _with_docker(F, monkeypatch):
    _fake_docker(F.bin, F.docker_log)
    assert shutil.which("docker") == str(F.bin / "docker")
    return state_the_image(monkeypatch)


def _rtl_project(root: Path) -> Path:
    rtl = root / "phase2/stage1/rtl"
    rtl.mkdir(parents=True)
    (rtl / "top.v").write_text("module top(input a, output y); assign y = a; endmodule\n")
    return root


# ── the reported site: p0_tool_frontend_check (Step 1's content check) ──────

def test_p0_frontend_runs_yosys_in_the_image_when_the_host_has_one(farm, tmp_path, monkeypatch):
    """RED before the fix: the host yosys 0.9 ran and `execution` said host."""
    _fake_yosys(farm.bin, farm.marker)
    image = _with_docker(farm, monkeypatch)
    assert shutil.which("yosys") == str(farm.bin / "yosys")
    import p0_tool_frontend_check as P
    result = P.check(_rtl_project(tmp_path / "proj"))
    assert not farm.marker.exists(), (
        "the HOST yosys elaborated the design although a container route "
        "exists: " + farm.marker.read_text())
    runs = _session_runs(farm)
    assert len(runs) == 1 and image in runs[0], farm.docker_calls()
    yosys = _execs(farm, " yosys ")
    assert yosys and "read_slang" in yosys[0], farm.docker_calls()
    assert result["tools"]["Yosys.JsonHeader"]["execution"] == image, result


def test_p0_frontend_local_route_refuses_a_yosys_without_read_slang(farm, tmp_path):
    """No docker client: the LOCAL route. The host yosys lacks `read_slang`,
    which the elaboration script runs, so it is REFUSED with that reason
    instead of reporting the design as failing elaboration.
    RED before the fix: the 0.9 binary ran the script ("Yosys elaboration
    failed", no reason)."""
    _fake_yosys(farm.bin, farm.marker)
    assert shutil.which("docker") is None
    import p0_tool_frontend_check as P
    result = P.check(_rtl_project(tmp_path / "proj"))
    assert not farm.marker.exists(), farm.marker.read_text()
    assert not result["passed"]
    text = " ".join(result["findings"])
    assert "TOOL_BELOW_MINIMUM" in text and "read_slang" in text, result


def test_the_step1_content_check_does_not_fail_the_design_on_a_host_yosys(farm, tmp_path, monkeypatch):
    """The flow-level symptom: `flow_step_output_content_check --mode rtl`
    printed "Yosys elaboration failed" for a design the image elaborates.
    With a container route it must not come from the host binary."""
    _fake_yosys(farm.bin, farm.marker)
    _with_docker(farm, monkeypatch)
    import flow_step_output_content_check as C
    errors = C.check(_rtl_project(tmp_path / "proj"), "rtl")
    assert not farm.marker.exists()
    assert "Yosys elaboration failed" not in errors, errors


# ── the reported site: step_yosys_synth ─────────────────────────────────────

def _synth_project(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    rtl = proj / "phase2" / "stage1" / "rtl"
    rtl.mkdir(parents=True)
    (rtl / "counter.v").write_text(
        "module counter (input clk, input resetn, output reg [7:0] cnt);\n"
        "  always @(posedge clk) if (!resetn) cnt <= 8'd0; else cnt <= cnt + 8'd1;\n"
        "endmodule\n")
    return proj


_NETLIST = ("module counter(clk, resetn, cnt);\n input clk, resetn; output [7:0] cnt;\n"
            + "".join(f"  \\$_DFF_P_ q{i} (.C(clk), .D(d{i}), .Q(cnt[{i % 8}]));\n"
                      for i in range(12)) + "endmodule\n")


def test_synth_never_asks_the_host_when_a_container_route_exists(tmp_path, monkeypatch):
    """RED before the fix: `_run(["yosys", ...])` on the host came FIRST and,
    because that host yosys "succeeded", the container was never reached."""
    import design_one_shot_runner as R
    import _container_route as _route
    proj = _synth_project(tmp_path)
    synth_dir = R._pl.synth_dir(proj)
    _route.pin_container_route(monkeypatch)
    calls = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        if cmd and os.path.basename(str(cmd[0])) == "yosys":
            (synth_dir / "netlist_yosys.v").write_text(_NETLIST)
            return 0, "Number of cells: 12   (HOST yosys)", ""
        if cmd[:2] == ["docker", "exec"] and "yosys -p" in " ".join(cmd):
            (synth_dir / "netlist_yosys.v").write_text(_NETLIST)
            return 0, "Number of cells: 12", ""
        return 0, "", ""
    monkeypatch.setattr(R, "_run", fake_run)
    monkeypatch.setattr(R, "_path_in_container", lambda p, c: True)
    res = R.step_yosys_synth(proj, "counter", container="test-eda")
    host = [c for c in calls if c and os.path.basename(str(c[0])) == "yosys"]
    assert host == [], f"the host yosys was run: {host}"
    assert any(c[:2] == ["docker", "exec"] and "test-eda" in c
               and "yosys -p" in " ".join(c) for c in calls), calls
    assert res.status == "PASS", (res.status, res.detail)


def test_synth_local_route_refuses_a_yosys_without_the_scripts_commands(farm, tmp_path):
    """No docker client: the LOCAL route. The script runs `dffunmap`, which the
    0.9 binary does not have, so the step is NOT_MEASURED (TOOL_ABSENT) with
    that reason and the binary never synthesizes anything.
    RED before the fix: the host binary ran the script and the step FAILed."""
    _fake_yosys(farm.bin, farm.marker)
    assert shutil.which("docker") is None
    import design_one_shot_runner as R
    proj = _synth_project(tmp_path)
    res = R.step_yosys_synth(proj, "counter", container="test-eda")
    assert not farm.marker.exists(), farm.marker.read_text()
    assert res.status == "NOT_MEASURED", (res.status, res.detail)
    assert "dffunmap" in res.detail and "TOOL_BELOW_MINIMUM" in (
        (R._pl.synth_dir(proj) / "yosys.log").read_text()), res.detail


# ── the same host-first shape at its other sites ────────────────────────────

def test_cdc_netlist_runs_yosys_in_the_image_when_the_host_has_one(farm, tmp_path, monkeypatch):
    """RED before the fix: `_cdc_netlist.build` took the host yosys first."""
    _fake_yosys(farm.bin, farm.marker)
    _with_docker(farm, monkeypatch)
    import _cdc_netlist as cdc
    rtl = tmp_path / "top.v"
    rtl.write_text("module top; endmodule\n")
    with pytest.raises(cdc.Refusal, match="CDC_NETLIST_BUILD_FAILED"):
        cdc.build(tmp_path, [rtl], "top", image="img:1")   # fake docker writes no netlist
    assert not farm.marker.exists(), farm.marker.read_text()
    assert any("img:1" in c for c in _session_runs(farm)), farm.docker_calls()
    assert _execs(farm, " yosys "), farm.docker_calls()


def test_catalog_synth_check_runs_yosys_in_the_image_when_the_host_has_one(farm, tmp_path, monkeypatch):
    """RED before the fix: `catalog_synth_safe_params_check._yosys` took the
    host yosys first."""
    _fake_yosys(farm.bin, farm.marker)
    _with_docker(farm, monkeypatch)
    import catalog_synth_safe_params_check as C
    out = tmp_path / "out"
    out.mkdir()
    C._yosys(tmp_path, "img:1", f"read_verilog -sv {tmp_path}/a.v; write_json {out}/n.json", out)
    assert not farm.marker.exists(), farm.marker.read_text()
    assert any("img:1" in c for c in _session_runs(farm)), farm.docker_calls()
    assert _execs(farm, " yosys "), farm.docker_calls()


def test_hardmacro_etm_runs_sta_in_the_named_container_when_the_host_has_one(tmp_path, monkeypatch):
    """RED before the fix: `characterise_liberty` took `bash -lc sta` on the
    host as soon as `shutil.which("sta")` found one, container named or not."""
    import digital_hardmacro_gen as mod
    import _container_route as _route
    import _eda_pin
    pnr = tmp_path / "phase3/stage3/pnr"
    pnr.mkdir(parents=True)
    sp = tmp_path / "phase3/stage3/extracted/spef_corners"
    sp.mkdir(parents=True)
    reports = tmp_path / "reports/phase3"
    reports.mkdir(parents=True)
    (pnr / "macro_a_pnr.v").write_text("module macro_a; endmodule\n")
    (pnr / "constraint.sdc").write_text("create_clock -period 10 clk\n")
    (sp / "macro_a.max.spef").write_text('*SPEF "IEEE 1481-1998"\n*DESIGN "macro_a"\n')
    (reports / "sta_mcorner_ocv.rpt").write_text(
        "=== SETUP corner: process=SS liberty=/pdk/ss.lib, SPEF=macro_a.max.spef ===\n"
        "STA_BASIS_CORNER: max\n")
    _route.pin_container_route(monkeypatch)
    monkeypatch.setattr(_eda_pin, "container_attach_refusal", lambda c, env=None: "")
    monkeypatch.setattr(mod.shutil, "which",
                        lambda n, *a, **k: "/usr/bin/docker" if n == "docker" else f"/host/{n}")
    seen = []

    def fake_sh(argv, *a, **k):
        seen.append(list(argv))
        tcl = Path(argv[-1].split()[-1])
        Path(tcl.read_text().split()[-1]).write_text(
            "library (x) { cell (macro_a) { pin (q) { timing () {} } } }")
        return 0, "", ""
    monkeypatch.setattr(mod, "_sh", fake_sh)
    text, rec = mod.characterise_liberty(tmp_path, "macro_a", "test-eda", tmp_path / "hm")
    assert seen and seen[0][:2] == ["docker", "exec"] and "test-eda" in seen[0], seen
    assert rec["characterised"] is True, rec


# ── the resolver itself ─────────────────────────────────────────────────────

def test_local_route_records_the_version_and_runs_a_sufficient_tool(farm, tmp_path, capsys):
    """The local route is not a refusal machine: a yosys that HAS every command
    the script runs is used, and its version is recorded."""
    import _eda_tool_route as T
    _fake_yosys(farm.bin, farm.marker, commands=_OLD_HELP + ["read_slang"])
    cp = T.run(["yosys", "-p", "read_slang x.v; hierarchy -check"],
               cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert farm.marker.exists(), "a sufficient local tool must run"
    assert cp.eda_route["route"] == T.ROUTE_LOCAL
    assert cp.eda_route["version"] == "Yosys 0.9 (git sha1 1979e0b)"
    assert T.records()[-1]["version"] == "Yosys 0.9 (git sha1 1979e0b)"
    assert "LOCAL route" in capsys.readouterr().err


def test_local_route_refuses_a_version_below_the_declared_minimum(farm, tmp_path):
    import _eda_tool_route as T
    _fake_yosys(farm.bin, farm.marker)
    with pytest.raises(T.ToolRouteRefused) as exc:
        T.run(["yosys", "-p", "read_verilog x.v"], cwd=tmp_path,
              requirement=T.Requirement(version=(0, 40), why="the step's own floor"))
    assert exc.value.code == T.TOOL_BELOW_MINIMUM
    assert "0.40" in exc.value.reason and "Yosys 0.9" in exc.value.reason
    assert isinstance(exc.value, FileNotFoundError), "a refusal must reach the sites' absent-tool branch"
    assert not farm.marker.exists()


def test_a_container_route_with_no_pinned_image_is_refused_not_hosted(farm, tmp_path, monkeypatch):
    """docker client present, no usable container, no pinned image: REFUSED.
    Falling back to the host binary would be the defect again."""
    import _eda_tool_route as T
    import _eda_pin
    _fake_yosys(farm.bin, farm.marker)
    _fake_docker(farm.bin, farm.docker_log)
    monkeypatch.setattr(_eda_pin, "pinned_image_present",
                        lambda env=None: (None, "IMAGE_NOT_PRESENT: stated for the test"))
    with pytest.raises(T.ToolRouteRefused) as exc:
        T.run(["yosys", "-V"], cwd=tmp_path)
    assert exc.value.code == T.NO_IMAGE_ROUTE and "IMAGE_NOT_PRESENT" in exc.value.reason
    assert not farm.marker.exists()


def test_image_route_mounts_every_named_directory_at_the_same_path(farm, tmp_path, monkeypatch):
    """Paths inside a yosys SCRIPT (not separate argv tokens) must resolve in
    the image too: the session container mounts their ROOTS at the same path,
    the exec names them unchanged, and a path the image owns (/foss, /usr, ...)
    is never mounted over."""
    import _eda_tool_route as T
    image = _with_docker(farm, monkeypatch)
    src = tmp_path / "rtl" / "a.v"
    src.parent.mkdir()
    src.write_text("module a; endmodule\n")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    full, route = T.argv_for(
        ["yosys", "-p", f"read_verilog {src}; read_liberty /foss/pdks/x.lib; "
                        f"write_json {out_dir}/n.json"], cwd=str(tmp_path / "rtl"))
    assert route.kind == T.ROUTE_IMAGE and full[:2] == ["docker", "exec"]
    runs = _session_runs(farm)
    assert len(runs) == 1 and image in runs[0], farm.docker_calls()
    vols = runs[0].split(" -v ")[1:]
    mounted = {v.split()[0] for v in vols}
    root = T.session_root(str(src.parent))
    assert f"{root}:{root}" in mounted, mounted
    assert not any(m.startswith("/foss") for m in mounted), mounted
    assert full[full.index("-w") + 1] == str(tmp_path / "rtl")
    assert f"read_verilog {src}" in " ".join(full)
    assert route.container in full and "yosys" in full


def test_a_named_container_holding_the_pin_and_seeing_the_paths_is_used(farm, tmp_path, monkeypatch):
    import _eda_tool_route as T
    import _eda_pin
    _with_docker(farm, monkeypatch)
    monkeypatch.setattr(_eda_pin, "container_attach_refusal", lambda c, env=None: "")
    monkeypatch.setattr(T, "_container_state",
                        lambda c: (True, "", [(str(tmp_path), str(tmp_path))]))
    full, route = T.argv_for(["iverilog", "-o", f"{tmp_path}/a.vvp", f"{tmp_path}/a.v"],
                             cwd=str(tmp_path), container="lane-eda", deadline_s=30)
    assert route.kind == T.ROUTE_CONTAINER and route.container == "lane-eda"
    assert full[:2] == ["docker", "exec"] and "lane-eda" in full
    assert full[full.index("-w") + 1] == str(tmp_path)
    assert f"{tmp_path}/a.vvp" in full and f"{tmp_path}/a.v" in full
    assert full[full.index("lane-eda") + 1:full.index("lane-eda") + 5] == \
        ["timeout", "-k", "5", "30"], full


def test_a_container_that_sees_the_paths_under_other_names_is_not_used(farm, tmp_path, monkeypatch):
    """A tool's diagnostics name the paths it was given and callers attribute
    failures by them, so a container that would see /work/a.v instead of
    <host>/a.v is passed over for the image route (same paths, same text)."""
    import _eda_tool_route as T
    import _eda_pin
    image = _with_docker(farm, monkeypatch)
    monkeypatch.setattr(_eda_pin, "container_attach_refusal", lambda c, env=None: "")
    monkeypatch.setattr(T, "_container_state",
                        lambda c: (True, "", [(str(tmp_path), "/work")]))
    full, route = T.argv_for(["iverilog", "-o", f"{tmp_path}/a.vvp", f"{tmp_path}/a.v"],
                             cwd=str(tmp_path), container="lane-eda")
    assert route.kind == T.ROUTE_IMAGE and route.image == image, route.record()
    assert f"{tmp_path}/a.v" in full and "/work/a.v" not in full


# ── the runner's simulation stages: container-first, and no host fallback ───

def _runner_stage_log(monkeypatch, R):
    """Record where `_run_iverilog_stage` sends the work (identity probes of
    the sim-toolchain recorder are attribution traffic and are dropped)."""
    log = []

    def keep(where, argv):
        text = argv if isinstance(argv, str) else " ".join(map(str, argv))
        if "__VIBEIC_TOOL_PATH__" not in text:
            log.append(where)

    monkeypatch.setattr(R, "_run", lambda argv, cwd=None, timeout=600, env=None:
                        (keep("host", argv), (0, "HOST", ""))[1])
    monkeypatch.setattr(R, "_docker_exec", lambda c, cmd, timeout=600, **k:
                        (keep("container", cmd), (0, "CONTAINER", ""))[1])
    monkeypatch.setattr(R, "_routed_tool_stage",
                        lambda argv, run_dir, timeout, as_tool=None:
                        (keep("image", argv), (0, "IMAGE", "", {"route": "image"}))[1],
                        raising=False)
    monkeypatch.setattr(R, "_run_stage_in_mounted_image",
                        lambda argv, run_dir, container, timeout=120:
                        (keep("mounted", argv), (0, "MOUNTED", ""))[1])
    monkeypatch.setattr(R, "_declared_container_image",
                        lambda p, c: {"declared_image_ref": None,
                                      "declared_image_id": None,
                                      "require_image": None,
                                      "declared_image_source": None},
                        raising=False)
    getattr(R, "_SIM_TOOLCHAIN_SEEN", {}).clear()
    return log


def test_runner_stage_goes_to_the_pinned_image_not_the_host_when_the_container_lacks_the_tool(
        monkeypatch, tmp_path):
    """RED before the fix: the declared container had no iverilog, the host
    had one, and the stage ran on the host."""
    import design_one_shot_runner as R
    import _container_route as _route
    monkeypatch.setattr("shutil.which", lambda t, *a, **k: f"/host/{t}")
    _route.pin_container_route(monkeypatch)
    monkeypatch.setattr(R, "_tool_in_container", lambda c, t: False)
    log = _runner_stage_log(monkeypatch, R)
    rc, out, _ = R._run_iverilog_stage(
        ["iverilog", "-o", str(tmp_path / "x.vvp")], tmp_path, "declared",
        timeout=60)
    assert log == ["image"], log
    assert (rc, out) == (0, "IMAGE")


def test_runner_stage_uses_the_containers_own_image_when_it_cannot_see_the_tree(
        monkeypatch, tmp_path):
    """RED before the fix: a container WITH iverilog that could not see the
    run tree handed the stage to the host whenever the host had iverilog."""
    import design_one_shot_runner as R
    import _container_route as _route
    monkeypatch.setattr("shutil.which", lambda t, *a, **k: f"/host/{t}")
    _route.pin_container_route(monkeypatch)
    monkeypatch.setattr(R, "_tool_in_container", lambda c, t: t == "iverilog")
    monkeypatch.setattr(R, "_path_in_container", lambda p, c: False)
    log = _runner_stage_log(monkeypatch, R)
    rc, out, _ = R._run_iverilog_stage(
        ["iverilog", "-o", str(tmp_path / "x.vvp")], tmp_path, "declared",
        timeout=60)
    assert log == ["mounted"], log
    assert (rc, out) == (0, "MOUNTED")


# ── the remaining host-first selectors ──────────────────────────────────────

def test_klayout_runner_is_the_container_not_a_host_build_when_a_container_route_exists(monkeypatch):
    """RED before the fix: `find_runner` was "host first then container", so a
    host KLayout build ran the sign-off decks of a run pinned to the image."""
    import _klayout_launch as K
    import _container_route as _route
    monkeypatch.setattr("shutil.which", lambda n, *a, **k: f"/host/{n}")
    _route.pin_container_route(monkeypatch)
    monkeypatch.delenv("VIBEIC_KLAYOUT_FORCE_ABSENT", raising=False)
    monkeypatch.setattr(K, "_container_has_klayout", lambda name: name == "lane-eda")
    runner = K.find_runner(container="lane-eda")
    assert isinstance(runner, K.ContainerRunner), type(runner).__name__


def test_klayout_local_route_still_uses_this_filesystems_klayout(monkeypatch):
    """The other branch: no docker client (inside the image) -> this PATH's
    KLayout is the runner."""
    import _klayout_launch as K
    import _container_route as _route
    monkeypatch.setattr("shutil.which", lambda n, *a, **k: f"/host/{n}")
    _route.pin_local_route(monkeypatch)
    monkeypatch.delenv("VIBEIC_KLAYOUT_FORCE_ABSENT", raising=False)
    assert isinstance(K.find_runner(container="lane-eda"), K.HostRunner)


def test_fmeda_host_leg_is_closed_while_a_container_route_exists(monkeypatch):
    """RED before the fix: docker client present, no image held, host
    iverilog on PATH -> the injection ran on the host binary."""
    import fmeda_fault_injection_coverage as fi
    import _container_route as _route
    monkeypatch.setattr("shutil.which", lambda n, *a, **k: f"/host/{n}")
    _route.pin_container_route(monkeypatch)
    monkeypatch.setattr(fi, "_local_docker_image", lambda: None)
    backend, img, _reason = fi.resolve_injection_backend()
    assert (backend, img) == (fi.BACKEND_NONE, None)


def test_formal_structural_yosys_with_no_container_named_runs_in_the_image(farm, tmp_path, monkeypatch):
    """RED before the fix: `container=None` meant `bash -lc yosys` on the
    host, whatever the host's yosys was, although a container route existed."""
    _fake_yosys(farm.bin, farm.marker)
    _with_docker(farm, monkeypatch)
    (farm.bin / "bash").symlink_to(shutil.which("bash", path="/bin:/usr/bin"))
    import formal_structural_check as F
    rtl = tmp_path / "a.v"
    rtl.write_text("module a; endmodule\n")
    F.run_yosys([rtl], "a", tmp_path / "work", None)
    assert not farm.marker.exists(), farm.marker.read_text()
    runs = _execs(farm, " yosys ")
    assert runs and _session_runs(farm), farm.docker_calls()


def test_the_not_prose_claim_for_the_banner_and_help_readers_is_falsifiable(farm, tmp_path):
    """The two readers `prose_polarity_consulted_check._NOT_PROSE` exempts read
    a fixed grammar: the FIRST dotted number of a banner, and the four-space
    indented NAME column of `yosys -Q -p help`. Falsify both: a description that
    mentions a command must not supply it, and a banner is read for its version
    and nothing else."""
    import _eda_tool_route as T
    assert T._parse_version("Yosys 0.9 (git sha1 1979e0b)") == (0, 9)
    assert T._parse_version("Icarus Verilog version 12.0 (stable) (v12_0)") == (12, 0)
    assert T._parse_version("no version here") is None
    # a help listing whose DESCRIPTION column names read_slang, but whose name
    # column does not carry it: read_slang is still absent
    y = farm.bin / "yosys"
    y.write_text("#!/bin/sh\n"
                 "if [ \"$1\" = -V ]; then echo 'Yosys 0.9 (git sha1 1979e0b)'; exit 0; fi\n"
                 "printf '    read_verilog         unlike read_slang, reads Verilog\\n'\n"
                 "printf '    hierarchy            check hierarchy\\n'\n")
    y.chmod(0o755)
    have = T._yosys_commands_available(str(y))
    assert have == frozenset({"read_verilog", "hierarchy"}), have
    with pytest.raises(T.ToolRouteRefused) as exc:
        T.resolve("yosys", ["yosys", "-p", "read_slang a.sv"], local=True)
    assert "read_slang" in exc.value.reason
