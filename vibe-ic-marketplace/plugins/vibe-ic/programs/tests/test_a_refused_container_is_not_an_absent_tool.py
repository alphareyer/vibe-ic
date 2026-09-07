"""A container refused for its IMAGE is not a tool that is absent
(vibe-ic#2173, the residual siblings).

MEASURED 2026-09-07 on 8HD-6, lane cz2173, against a pristine full clone of
live main `a1f3685837ca` (tree `b16cf5cde018`, 8122 tracked files, no
alternates), pin `sha256:89a8fd7295208ee6d06e216ade9edc6161d26db52099e9f22ceb
77a2d76e3f49` as `_eda_pin.IMAGE_DIGEST` states it.

#2173 is filed against A3, where the refusal is EMITTED OVER: the deck is
published anyway. Its siblings carry the SAME fact in the other direction --
the refusal is read as the TOOL BEING ABSENT, which is a capability gap this
flow deliberately carries and a completely different sentence. Measured on that
clone, driving the SHIPPED chain (`_eda_pin.container_image_digest` ->
`container_pin_state` -> `container_attach_refusal` -> `_container_exec`) with
a stub `docker` reporting a digest that is not the pin:

    _container_exec.run_in_container(...)  rc=125 = IMAGE_MISMATCH_RC
      and NOTHING outside _container_exec and its own tests read that rc.

    analog_real_corner_sweep._resolve_ngspice   -> None      "ngspice absent"
    analog_real_corner_sweep._ngspice_available -> False
      => A4                            "[real_sim] ngspice not in container"
      => analog_mc_yield_run           verdict SKIP
      => analog_loop_liveness_samples  "ngspice is not reachable"
    _area_unit.ContainerReader.exists()         -> False     "not there"
    digital_hardmacro_gen                       "magic exited 125, no LEF"

    lec_run._container_available / _docker / _docker_exec_raw
                                                -> RAISED ContainerImageMismatch
      => `lec_run <p> --top top --container vibeic-eda`: rc 1, a TRACEBACK,
         no reports/lec.json, and reports/lec.live.*.rpt +
         reports/lec.telemetry.*.json left behind -- A3's own pre-#2156 state.

THE PAIRED ARM IS NOT OPTIONAL. Every MISMATCH case below has a MATCH twin
driving the SAME stub with the PINNED digest, which must reach the ordinary
path. Without it, a refusal that fired on every container would pass every
mismatch assertion here.

THE REFUSAL IS DRIVEN, NEVER SIMULATED: no test monkeypatches
`container_attach_refusal`, `image_refusal` or `_ngspice_available`. A test that
did would agree with the implementation by construction and could not have
caught any of the above.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import _plugin_tree  # noqa: F401 — puts programs/ on sys.path
import _area_unit as AU
import _container_exec as CE
import _eda_pin as PIN

PROGRAMS = Path(_plugin_tree.plugin_path("programs"))

#: A well-formed sha256 that is NOT the pin, DERIVED from the pin so it can
#: never accidentally become equal to it.
OTHER_DIGEST = "sha256:" + "".join(
    ("0" if c != "0" else "1") for c in PIN.IMAGE_DIGEST.split(":", 1)[1])

CONTAINER = "vibeic-eda"

#: A deck that is RUNNABLE and SCOREABLE by `analog_mc_yield_run._deck_rank`,
#: so the run reaches the simulator probe rather than bailing before it.
RUNNABLE_DECK = (
    "* tb\n"
    ".subckt blk vin vout\n"
    "R1 vin vout 1k\n"
    ".ends\n"
    "X1 vin vout blk\n"
    "Vdd vin 0 3.0\n"
    ".tran 1n 100n\n"
    ".meas tran vout_v AVG v(vout)\n"
    ".end\n")


def _stub_docker(tmp_path: Path, digest: str) -> Path:
    """A `docker` on PATH reporting `digest` as the container's image.

    `inspect` is how the pin state is measured; every other verb exits 0 with
    no output, so a MATCH arm walks the ordinary path and lands on the ordinary
    "the simulator is not in this container" — the capability gap that IS
    honest here, and the thing a mismatch must never be reported as.
    """
    d = tmp_path / "stub_bin"
    d.mkdir(parents=True, exist_ok=True)
    (d / "docker").write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "a = sys.argv[1:]\n"
        "if a and a[0] == 'inspect':\n"
        f"    print('sha256:aaaa\\trepo@{digest}')\n"
        "    sys.exit(0)\n"
        "sys.exit(0)\n",
        encoding="utf-8")
    (d / "docker").chmod(0o755)
    return d


def _env_with(stub_bin: Path) -> dict:
    env = dict(os.environ)
    env["PATH"] = f"{stub_bin}{os.pathsep}{env.get('PATH', '')}"
    return env


def _run(prog: str, *args: str, env: dict) -> subprocess.CompletedProcess:
    """A program as a SUBPROCESS, because the defect is an exception reaching
    an EXIT CODE: an in-process call sees the exception and never learns what
    rc a caller was handed."""
    return subprocess.run([sys.executable, str(PROGRAMS / prog), *args],
                          capture_output=True, text=True, env=env, check=False)


# ═══ the primitive: the returned refusal has a reader ═════════════════════

def test_the_returned_refusal_is_identified_by_its_mark_not_only_its_rc():
    """`IMAGE_MISMATCH_RC` is 125 because that is docker's own "could not
    start" code — so the rc ALONE is not the measurement. MEASURED against
    `a1f3685837ca`: `describe_result(CompletedProcess(rc=125, stderr=""), 5)`
    answered "the container does not hold the pinned image ...; nothing was
    run" for a TOOL that ran and chose 125 itself."""
    tool_exited_125 = subprocess.CompletedProcess(
        ["docker"], CE.IMAGE_MISMATCH_RC, "", "")
    assert CE.image_refusal(tool_exited_125) == ""
    assert PIN.CONTAINER_IMAGE_MISMATCH not in (
        CE.describe_result(tool_exited_125, 5) or "")

    refused = subprocess.CompletedProcess(
        ["docker"], CE.IMAGE_MISMATCH_RC, "",
        f"{CE.IMAGE_REFUSAL_MARK}{PIN.CONTAINER_IMAGE_MISMATCH}: c runs x\n")
    assert PIN.CONTAINER_IMAGE_MISMATCH in CE.image_refusal(refused)


def test_a_run_that_completed_is_never_read_as_a_refusal():
    """The other direction: every rc a TOOL can return must pass through."""
    for rc in (0, 1, 2, 124, 127):
        cp = subprocess.CompletedProcess(["docker"], rc, "", "")
        assert CE.image_refusal(cp) == "", rc


def test_raise_on_image_refusal_passes_a_real_result_through_unchanged():
    ok = subprocess.CompletedProcess(["docker"], 0, "out", "err")
    assert CE.raise_on_image_refusal(ok) is ok


def test_the_refused_tier_is_distinct_from_every_other_rc_this_repo_returns():
    """Folding it into an existing rc is what let a HOST condition be read as a
    statement about the design. The codes it must differ from are named."""
    tiers = {"EX_ENV_REFUSED": CE.EX_ENV_REFUSED,
             "IMAGE_MISMATCH_RC": CE.IMAGE_MISMATCH_RC,
             "TIMEOUT_EXPIRED_RC": CE.TIMEOUT_EXPIRED_RC,
             "TIMEOUT_UNAVAILABLE_RC": CE.TIMEOUT_UNAVAILABLE_RC,
             "STALLED_RC": CE.STALLED_RC}
    assert len(set(tiers.values())) == len(tiers), tiers
    assert CE.EX_ENV_REFUSED not in (0, 1, 2), (
        "0/1/2 are ok / tool-failed / honest-gap-or-defer across this repo; a "
        "refusal that shares one of them is not routable")


def test_the_refusal_is_driven_through_the_shipped_chain(tmp_path,
                                                         monkeypatch):
    """THE INSTRUMENT'S OWN CONTROL. If the stub did not really produce a
    MISMATCH, every mismatch assertion below would be vacuous."""
    monkeypatch.setenv(
        "PATH", f"{_stub_docker(tmp_path, OTHER_DIGEST)}"
                f"{os.pathsep}{os.environ['PATH']}")
    state, detail = PIN.container_pin_state(CONTAINER)
    assert state == "MISMATCH", (state, detail)
    assert PIN.IMAGE_DIGEST in detail and OTHER_DIGEST in detail
    cp = CE.run_in_container(CONTAINER, "echo hi", deadline_s=5)
    assert cp.returncode == CE.IMAGE_MISMATCH_RC
    assert PIN.IMAGE_DIGEST in CE.image_refusal(cp)


def test_a_matching_container_is_not_refused_by_the_primitive(tmp_path,
                                                              monkeypatch):
    """THE PAIRED ARM for the instrument itself."""
    monkeypatch.setenv(
        "PATH", f"{_stub_docker(tmp_path, PIN.IMAGE_DIGEST)}"
                f"{os.pathsep}{os.environ['PATH']}")
    assert PIN.container_pin_state(CONTAINER)[0] == "MATCH"
    cp = CE.run_in_container(CONTAINER, "echo hi", deadline_s=5)
    assert CE.image_refusal(cp) == ""


def _stub_docker_unreadable(tmp_path: Path) -> Path:
    """A `docker` whose `inspect` FAILS, so the container's digest cannot be
    read at all."""
    d = tmp_path / "stub_unreadable"
    d.mkdir(parents=True, exist_ok=True)
    (d / "docker").write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "if sys.argv[1:2] == ['inspect']:\n"
        "    sys.exit(1)\n"
        "sys.exit(0)\n", encoding="utf-8")
    (d / "docker").chmod(0o755)
    return d


def test_an_unreadable_digest_refuses_nothing_anywhere(tmp_path, monkeypatch):
    """THE THIRD ARM, and without it this whole change is a trap.

    "I could not read the digest" is NOT "I read it and it is wrong"
    (`_eda_pin.container_pin_state` -> `UNREADABLE`). A refusal that fired on
    an unreadable digest would make every locally-built container unusable
    while claiming to have JUDGED its image — the exact inversion of the defect
    being fixed. Every site taught to route on a refusal is driven here against
    a container whose digest cannot be read, and every one must take its
    ordinary path."""
    import analog_real_corner_sweep as ARS
    import digital_hardmacro_gen as HM
    monkeypatch.setenv(
        "PATH", f"{_stub_docker_unreadable(tmp_path)}"
                f"{os.pathsep}{os.environ['PATH']}")
    ARS._NGSPICE_CACHE.clear()

    assert PIN.container_pin_state(CONTAINER)[0] == "UNREADABLE"
    assert PIN.container_attach_refusal(CONTAINER) == ""
    assert CE.image_refusal(
        CE.run_in_container(CONTAINER, "echo hi", deadline_s=5)) == ""
    assert ARS._ngspice_available(CONTAINER) is False   # the ordinary answer
    assert PIN.CONTAINER_IMAGE_MISMATCH not in HM.magic_absent_reason(CONTAINER)
    assert AU.ContainerReader(CONTAINER).exists("/foss/pdks") is True


# ═══ analog_real_corner_sweep: "ngspice absent" was the refusal ═══════════

def _analog_block(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    b = project / "phase3" / "analog" / "blk"
    b.mkdir(parents=True)
    (b / "spec.json").write_text(
        json.dumps({"specs": [{"name": "vout_v", "min": 1.7, "max": 1.9}]}),
        encoding="utf-8")
    (b / "tb_blk.sp").write_text(RUNNABLE_DECK, encoding="utf-8")
    # A4's A3-precondition is checked BEFORE the simulator probe, and rightly:
    # a block with no A3 netlist has nothing of this design to measure. So the
    # deck is here, and the run reaches the probe the refusal fires from.
    (b / "blk.sp").write_text(
        "* _provenance: producer=analog_a3_netlist_emit schema=1\n"
        ".subckt blk vin vout\n"
        "R1 vin vout 1k\n"
        ".ends\n", encoding="utf-8")
    return project


def test_the_ngspice_probe_does_not_answer_absent_on_a_refusal(tmp_path,
                                                               monkeypatch):
    """`_resolve_ngspice` walked its candidate list, saw `rc != 0` on each and
    answered `None`. `None` is this file's word for "ngspice is not installed",
    which sends an operator to install a simulator that IS installed."""
    import analog_real_corner_sweep as ARS
    ARS._NGSPICE_CACHE.clear()
    monkeypatch.setenv(
        "PATH", f"{_stub_docker(tmp_path, OTHER_DIGEST)}"
                f"{os.pathsep}{os.environ['PATH']}")
    with pytest.raises(CE.ContainerImageMismatch) as caught:
        ARS._ngspice_available(CONTAINER)
    assert PIN.IMAGE_DIGEST in str(caught.value)
    assert OTHER_DIGEST in str(caught.value)


def test_a_matching_container_still_answers_the_ordinary_capability_gap(
        tmp_path, monkeypatch):
    """THE PAIRED ARM. The stub has no ngspice, so the honest answer on a
    container AT the pin is `False` — and that answer must still be reachable,
    or this fix has simply made every container refuse."""
    import analog_real_corner_sweep as ARS
    ARS._NGSPICE_CACHE.clear()
    monkeypatch.setenv(
        "PATH", f"{_stub_docker(tmp_path, PIN.IMAGE_DIGEST)}"
                f"{os.pathsep}{os.environ['PATH']}")
    assert ARS._ngspice_available(CONTAINER) is False


def test_a4_reports_a_refusal_tier_and_writes_no_artefact(tmp_path):
    """A4's own entry point. `2` is its defer/blocked rc and is NOT this: a
    defer says the block was examined; this run examined nothing."""
    project = _analog_block(tmp_path)
    b = project / "phase3" / "analog" / "blk"
    before = sorted(p.name for p in b.iterdir())
    cp = _run("analog_real_corner_sweep.py", str(project), "--block", "blk",
              "--container", CONTAINER,
              env=_env_with(_stub_docker(tmp_path, OTHER_DIGEST)))
    assert "Traceback" not in cp.stderr, cp.stderr
    assert cp.returncode == CE.EX_ENV_REFUSED, (cp.returncode, cp.stderr)
    assert PIN.CONTAINER_IMAGE_MISMATCH in cp.stderr
    assert PIN.IMAGE_DIGEST in cp.stderr and OTHER_DIGEST in cp.stderr
    assert sorted(p.name for p in b.iterdir()) == before, (
        "a run the environment refused left an artefact behind")


def test_a4_on_a_matching_container_takes_the_ordinary_path(tmp_path):
    """THE PAIRED ARM: the stub holds the pin and has no ngspice, so A4 must
    reach its ordinary "ngspice not in container" and its ordinary rc."""
    project = _analog_block(tmp_path)
    cp = _run("analog_real_corner_sweep.py", str(project), "--block", "blk",
              "--container", CONTAINER,
              env=_env_with(_stub_docker(tmp_path, PIN.IMAGE_DIGEST)))
    assert cp.returncode != CE.EX_ENV_REFUSED, cp.stderr
    assert PIN.CONTAINER_IMAGE_MISMATCH not in cp.stderr, cp.stderr
    assert "ngspice not in container" in cp.stderr, cp.stderr


# ═══ analog_mc_yield_run: the refusal was a SKIP ══════════════════════════

def test_mc_yield_does_not_report_a_refusal_as_a_skip(tmp_path):
    """A SKIP says the SUBJECT was examined and did not need this step. This
    run was never allowed to examine anything."""
    project = _analog_block(tmp_path)
    cp = _run("analog_mc_yield_run.py", str(project), "--block", "blk",
              "--n", "2", "--container", CONTAINER,
              env=_env_with(_stub_docker(tmp_path, OTHER_DIGEST)))
    assert "Traceback" not in cp.stderr, cp.stderr
    assert cp.returncode == CE.EX_ENV_REFUSED, (cp.returncode, cp.stderr)
    rep = json.loads(cp.stdout)
    assert rep["verdict"] == "ENV_REFUSED", rep
    assert rep["verdict"] != "SKIP"
    assert rep["mc_runs"] == 0
    assert PIN.IMAGE_DIGEST in rep["reason"]
    assert OTHER_DIGEST in rep["reason"]


def test_mc_yield_on_a_matching_container_still_skips_for_an_absent_ngspice(
        tmp_path):
    """THE PAIRED ARM. SKIP for a genuinely absent simulator is the CORRECT
    answer and must survive this change."""
    project = _analog_block(tmp_path)
    cp = _run("analog_mc_yield_run.py", str(project), "--block", "blk",
              "--n", "2", "--container", CONTAINER,
              env=_env_with(_stub_docker(tmp_path, PIN.IMAGE_DIGEST)))
    rep = json.loads(cp.stdout)
    assert rep["verdict"] == "SKIP", rep
    assert cp.returncode != CE.EX_ENV_REFUSED


# ═══ lec_run: the RAISE form reached the exit code as a bare 1 ════════════

def _lec_project(tmp_path: Path) -> Path:
    project = tmp_path / "lecproj"
    (project / "phase2/stage1/rtl").mkdir(parents=True)
    (project / "phase2/stage2/synth").mkdir(parents=True)
    src = "module top(input a, output y); assign y = ~a; endmodule\n"
    (project / "phase2/stage1/rtl/top.v").write_text(src, encoding="utf-8")
    (project / "phase2/stage2/synth/netlist.v").write_text(src,
                                                           encoding="utf-8")
    return project


def test_lec_run_does_not_exit_1_with_a_traceback_on_a_refusal(tmp_path):
    """THE REPRODUCTION. On `a1f3685837ca` this exits 1 with
    `_container_exec.ContainerImageMismatch` on stderr."""
    project = _lec_project(tmp_path)
    cp = _run("lec_run.py", str(project), "--top", "top",
              "--container", CONTAINER,
              env=_env_with(_stub_docker(tmp_path, OTHER_DIGEST)))
    assert "Traceback" not in cp.stderr, cp.stderr
    assert "ContainerImageMismatch" not in cp.stderr, cp.stderr
    assert cp.returncode == CE.EX_ENV_REFUSED, (cp.returncode, cp.stderr)
    assert cp.returncode != 1, (
        "1 is this producer's `Yosys ran and produced no parseable evidence`; "
        "a run in which yosys never started must not share it")


def test_lec_run_names_both_digests_and_is_not_the_disclosed_skip(tmp_path):
    """"container not available — runner should disclosed-skip" is a CAPABILITY
    gap. yosys is there and is usable; the RIGHT to use it is what was
    refused."""
    project = _lec_project(tmp_path)
    cp = _run("lec_run.py", str(project), "--top", "top",
              "--container", CONTAINER,
              env=_env_with(_stub_docker(tmp_path, OTHER_DIGEST)))
    assert PIN.CONTAINER_IMAGE_MISMATCH in cp.stderr, cp.stderr
    assert PIN.IMAGE_DIGEST in cp.stderr and OTHER_DIGEST in cp.stderr
    assert "not available" not in cp.stderr, cp.stderr


def test_lec_run_leaves_no_artefact_of_a_run_that_never_happened(tmp_path):
    """MEASURED on `a1f3685837ca`: the traceback left `reports/lec.live.*.rpt`
    and `reports/lec.telemetry.*.json` — the opening of a run that never
    happened, for a reader who cannot tell that from a run that was killed."""
    project = _lec_project(tmp_path)
    _run("lec_run.py", str(project), "--top", "top", "--container", CONTAINER,
         env=_env_with(_stub_docker(tmp_path, OTHER_DIGEST)))
    reports = project / "reports"
    left = sorted(p.name for p in reports.iterdir()) if reports.is_dir() else []
    assert left == [], left
    assert not (reports / "lec.json").exists()


def test_a_refusal_from_a_later_container_touch_is_still_a_refusal(
        tmp_path, monkeypatch):
    """THE BACKSTOP, and it needs an INJECTED later refusal to be reachable.

    On a real run the door guard above fires first — `_container_available` is
    this producer's first container touch — so the wrapper around `main` can
    only be exercised by a refusal from one of the touches BELOW it. Without
    this the wrapper is a branch no arm reaches, and a mutation that deletes it
    changes nothing: MEASURED here as mutation M3, `0 red`.

    `_container_file_exists` is the next touch (the Liberty probe) and it
    catches only `SubprocessError`/`OSError`, so a refusal from it goes
    straight past — exactly as `_docker`'s other callers would.

    `entry` is the wrapper and `main` keeps the body, because three shipped
    guards read `inspect.getsource(lec_run.main)` and a rename is a move they
    cannot follow.
    """
    import lec_run as LEC
    project = _lec_project(tmp_path)
    monkeypatch.setattr(LEC, "_container_available", lambda _c: True)

    def _refuse(*_a, **_k):
        raise CE.ContainerImageMismatch(
            f"{PIN.CONTAINER_IMAGE_MISMATCH}: container {CONTAINER} runs "
            f"{OTHER_DIGEST}, but the pinned runtime is r@{PIN.IMAGE_DIGEST}")

    monkeypatch.setattr(LEC, "_container_file_exists", _refuse)
    rc = LEC.entry([str(project), "--top", "top", "--container", CONTAINER])
    assert rc == CE.EX_ENV_REFUSED, rc
    assert not (project / "reports" / "lec.json").exists()


def test_lec_run_on_a_matching_container_takes_the_ordinary_path(tmp_path):
    """THE PAIRED ARM. The stub holds the pin and has no yosys, so lec_run must
    reach its ordinary disclosed-skip and write its ordinary report."""
    project = _lec_project(tmp_path)
    cp = _run("lec_run.py", str(project), "--top", "top",
              "--container", CONTAINER,
              env=_env_with(_stub_docker(tmp_path, PIN.IMAGE_DIGEST)))
    assert cp.returncode != CE.EX_ENV_REFUSED, cp.stderr
    assert PIN.CONTAINER_IMAGE_MISMATCH not in cp.stderr, cp.stderr
    assert (project / "reports" / "lec.json").is_file(), cp.stderr


# ═══ analog_loop_liveness_samples_emit ════════════════════════════════════

#: A deck `runner_transients` accepts: a transient the runner really executed
#: (the sibling `.ngspice.log` is that file's own proof), whose `tran` card is
#: the in-`.control` form `analog_real_corner_sweep._TRAN_RE` reads.
LIVENESS_DECK = ("* runner transient\n"
                 "Vdd vdd 0 1.8\nVss vss 0 0\n"
                 "R1 vdd nall 1k\nR2 nall ndac 1k\nR3 ndac nq_n 1k\n"
                 ".control\ntran 1n 100n\n.endc\n.end\n")


def _liveness_block(tmp_path: Path) -> Path:
    """A block whose type DECLARES liveness nodes, so the run reaches the
    simulator probe rather than the honest `NOT_DECLARED` gap before it."""
    import analog_a2_topology_emit as A2
    project = tmp_path / "livproj"
    b = project / "phase3" / "analog" / "blk" / "sizing_loop"
    b.mkdir(parents=True)
    entry = A2.LIBRARY["delta_sigma"]
    (b.parent / "topology.json").write_text(json.dumps({
        "block_type": "delta_sigma",
        "rails": entry["rails"],
        A2.LIVENESS_NODES_KEY: entry[A2.LIVENESS_NODES_KEY]}), encoding="utf-8")
    (b / "pt0.sp").write_text(LIVENESS_DECK, encoding="utf-8")
    (b / "pt0.ngspice.log").write_text("* the runner ran it\n", encoding="utf-8")
    return project


def test_the_liveness_export_does_not_report_a_refusal_as_an_honest_gap(
        tmp_path):
    """`Refusal` in that file is a statement about the DESIGN — a node this
    block does not declare, a deck with no transient — and it exits
    `RC_HONEST_GAP`. A container running the wrong bytes says nothing about the
    design at all."""
    project = _liveness_block(tmp_path)
    b = project / "phase3" / "analog" / "blk"
    before = sorted(p.name for p in b.iterdir())
    cp = _run("analog_loop_liveness_samples_emit.py", str(project),
              "--block", "blk", "--container", CONTAINER,
              env=_env_with(_stub_docker(tmp_path, OTHER_DIGEST)))
    assert "Traceback" not in cp.stderr, cp.stderr
    assert cp.returncode == CE.EX_ENV_REFUSED, (cp.returncode, cp.stderr)
    rec = json.loads(cp.stdout)
    assert rec["verdict"] == "ENV_REFUSED", rec
    assert rec["env_refused"] is True
    assert PIN.IMAGE_DIGEST in rec["reason"] and OTHER_DIGEST in rec["reason"]
    assert "HONEST_GAP" not in cp.stderr, cp.stderr
    assert sorted(p.name for p in b.iterdir()) == before


def test_the_liveness_export_still_refuses_honestly_for_an_absent_ngspice(
        tmp_path):
    """THE PAIRED ARM. "ngspice is not reachable" for a container AT the pin
    that really has no ngspice is the CORRECT answer and must survive."""
    project = _liveness_block(tmp_path)
    cp = _run("analog_loop_liveness_samples_emit.py", str(project),
              "--block", "blk", "--container", CONTAINER,
              env=_env_with(_stub_docker(tmp_path, PIN.IMAGE_DIGEST)))
    assert cp.returncode != CE.EX_ENV_REFUSED, cp.stderr
    rec = json.loads(cp.stdout)
    assert rec["verdict"] == "REFUSED", rec
    assert "ngspice is not reachable" in rec["reason"], rec


# ═══ digital_hardmacro_gen: "magic is not on PATH in that container" ══════

def test_the_magic_reason_does_not_call_a_refusal_an_absent_tool(
        tmp_path, monkeypatch):
    """MEASURED on `a1f3685837ca`: against a refused container `MagicSite.sh`
    came back rc 125 with nothing run, `has_magic()` said False,
    `find_magic_site` said None, and the reason published was "magic is ... not
    on PATH inside container 'vibeic-eda' either" — about a container that has
    magic. This is the probe BEFORE the LEF launch and it is the one a real run
    reaches first."""
    import digital_hardmacro_gen as HM
    monkeypatch.setenv(
        "PATH", f"{_stub_docker(tmp_path, OTHER_DIGEST)}"
                f"{os.pathsep}{os.environ['PATH']}")
    why = HM.magic_absent_reason(CONTAINER)
    assert PIN.CONTAINER_IMAGE_MISMATCH in why, why
    assert PIN.IMAGE_DIGEST in why and OTHER_DIGEST in why
    assert "not on PATH inside container" not in why, why


def test_the_magic_reason_still_names_an_absent_tool_on_a_matching_container(
        tmp_path, monkeypatch):
    """THE PAIRED ARM. The capability gap is the CORRECT sentence when the
    container really is the pinned one and really has no magic."""
    import digital_hardmacro_gen as HM
    monkeypatch.setenv(
        "PATH", f"{_stub_docker(tmp_path, PIN.IMAGE_DIGEST)}"
                f"{os.pathsep}{os.environ['PATH']}")
    why = HM.magic_absent_reason(CONTAINER)
    assert "not on PATH inside container" in why, why
    assert PIN.CONTAINER_IMAGE_MISMATCH not in why, why


def test_the_lef_launch_does_not_call_a_refusal_a_magic_failure(tmp_path,
                                                                monkeypatch):
    """THE SECOND WINDOW, and it is a real one: `write_lef_with_magic` accepts
    a `site` its caller resolved EARLIER, so the container can be the pinned
    one when magic is probed and a different one by the time magic is launched
    — "a name is not a container identity" (vibe-ic#2076).

    The refusal handed to the reader is composed by the SHIPPED composer under
    a real MISMATCH; only the transport is stood in for, because reaching this
    line through the container needs the earlier probes to have succeeded on
    the same container this one must refuse."""
    import digital_hardmacro_gen as HM
    monkeypatch.setenv(
        "PATH", f"{_stub_docker(tmp_path, OTHER_DIGEST)}"
                f"{os.pathsep}{os.environ['PATH']}")
    refused = CE.run_in_container_supervised(CONTAINER, "magic ...",
                                             ceiling_s=5.0)
    assert refused.returncode == CE.IMAGE_MISMATCH_RC, refused

    work = tmp_path / "work"
    work.mkdir()

    class _ResolvedThenSwapped:
        """The site the caller resolved while the container WAS the pin."""
        path = str(work)
        where = f"container {CONTAINER!r}"

        def open(self, _host_tmp): return True, ""

        def put(self, _src, _name): return True, ""

        def put_text(self, _text, _name, _host_tmp): return True, ""

        def sh(self, _cmd, timeout=0):
            return (refused.returncode, refused.stdout or "",
                    refused.stderr or "")

        def close(self): return None

    gds = tmp_path / "top.gds"; gds.write_bytes(b"\x00")
    dfl = tmp_path / "top.def"; dfl.write_text("DESIGN top ;\n")
    ok, why = HM._write_lef_in_container(
        _ResolvedThenSwapped(), "top", gds, dfl, tmp_path / "out.lef",
        "/foss/pdks", "/foss/pdks/x.magicrc", False, False, 60)
    assert ok is False
    assert PIN.CONTAINER_IMAGE_MISMATCH in why, why
    assert "magic exited 125" not in why, why


# ═══ _area_unit: "the file is not there" was the refusal ══════════════════

def test_the_container_reader_does_not_answer_false_for_a_refusal(
        tmp_path, monkeypatch):
    """`exists()` returning False is this class's word for "the path is not in
    the container". Its own docstring already says a reader that cannot run
    must say so by raising; only the image refusal did not."""
    monkeypatch.setenv(
        "PATH", f"{_stub_docker(tmp_path, OTHER_DIGEST)}"
                f"{os.pathsep}{os.environ['PATH']}")
    rd = AU.ContainerReader(CONTAINER)
    with pytest.raises(CE.ContainerImageMismatch):
        rd.exists("/foss/pdks")
    with pytest.raises(CE.ContainerImageMismatch):
        rd.read("/foss/pdks/anything.lef")


def test_the_container_reader_still_answers_on_a_matching_container(
        tmp_path, monkeypatch):
    """THE PAIRED ARM. The stub exits 0 for `test -e`, so a container AT the
    pin must still get a plain boolean."""
    monkeypatch.setenv(
        "PATH", f"{_stub_docker(tmp_path, PIN.IMAGE_DIGEST)}"
                f"{os.pathsep}{os.environ['PATH']}")
    assert AU.ContainerReader(CONTAINER).exists("/foss/pdks") is True
