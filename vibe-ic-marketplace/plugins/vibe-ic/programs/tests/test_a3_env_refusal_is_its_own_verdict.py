"""An ENVIRONMENT refusal at A3 is its own verdict, never a crash and never a
statement about the design (vibe-ic#2076, vibe-ic#2088).

MEASURED 2026-09-07 on 8HD-9, lane cza3pin, against `94617408759e` (v1.18.75),
with the host's shared container `vibeic-eda` holding
`sha256:06537f7e…` (0.3.46) while the repo pin names `sha256:8c5694ab…`
(0.3.48):

    $ analog_a3_netlist_emit <project> --block ldo \
          --container vibeic-eda --verify-sim
    Traceback (most recent call last):
      ... analog_a3_netlist_emit.py, line 1407, in verify_with_ngspice
        if shutil.which("docker") is None or not _docker_ok(container):
      ... _container_exec.py, line 156, in docker_exec_argv
        raise ContainerImageMismatch(why)
    _container_exec.ContainerImageMismatch: CONTAINER_IMAGE_MISMATCH: ...
    rc=1     netlist_gap.json: absent     --json report: absent

`ContainerImageMismatch` is a `RuntimeError`, and MEASURED across the shipped
tree NOTHING outside `programs/tests/` caught it. So the one line that names
BOTH digests — the only line a reader can act on — reached the producer's exit
code as a bare 1 and reached its run record nowhere at all. The analog runner
then recorded `WAIVED A3_netlist_gen … deterministic producer ERRORED rc=1 and
wrote NO gap file`, and A4..A7 reported BLOCKED on a missing `.sp`: a verdict
about an absence three steps downstream of the cause.

THE REFUSAL IS DRIVEN, NOT SIMULATED. Every test below puts a stub `docker` on
the child's PATH and lets the SHIPPED chain run for real —
`_eda_pin.container_image_digest` -> `container_pin_state` ->
`container_attach_refusal` -> `_container_exec.docker_exec_argv`. A test that
monkeypatched `container_attach_refusal` would agree with the implementation by
construction and could not have caught this.

THE PAIRED ARM IS NOT OPTIONAL. `test_a_matching_container_is_not_refused`
drives the SAME stub with the pinned digest and requires the producer to reach
PAST the refusal into its ordinary simulator probe. Without it, a refusal that
fired on every container at all would pass every other test here.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import _plugin_tree  # noqa: F401 — puts programs/ on sys.path
import _analog_producer_common as PC
import _container_exec as CE
import _eda_pin as PIN
from _stated_eda_image import STATED_DIGEST, state_the_image_for_children  # noqa: E402
import analog_a3_netlist_emit as A3

from _analog_producer_fixture import (
    A1, A2, A3 as A3_PROG, bdir, block, make_project, read_json, run_prog)

LDO_SPEC = [{"name": "Vout", "target": 1.8, "unit": "V"},
            {"name": "Vin", "target": 3.0, "unit": "V"}]

#: A digest that is a well-formed sha256 and is NOT the pin. Derived from the
#: pin by construction so it can never accidentally become equal to it.
OTHER_DIGEST = "sha256:" + "".join(
    ("0" if c != "0" else "1") if c.isdigit() or c in "abcdef" else c
    for c in STATED_DIGEST.split(":", 1)[1])


@pytest.fixture(autouse=True)
def _the_stated_image(monkeypatch):
    """The pin is STATED, in this process and in every program it spawns
    (lane rfimg2). It used to be read from THIS HOST's docker at collection
    (`PIN.IMAGE_DIGEST` in a module constant), so inside the image (no docker)
    the whole file was a collection error, and on a host the arms compared the
    stub container against whatever that host happened to hold."""
    state_the_image_for_children(monkeypatch)


def _stub_docker(tmp_path: Path, digest: str) -> Path:
    """A `docker` on PATH that reports `digest` as the container's image.

    `exec` answers rc 0 for the reachability probe and "no" for the simulator
    probe, so the MATCH arm walks the whole ordinary path and lands on the
    ordinary `NOT_VERIFIED_NO_SIMULATOR`. The MISMATCH arm must never reach
    `exec` at all — that is the guarantee under test — and the stub records
    every invocation so the test can assert it.
    """
    d = tmp_path / "stub_bin"
    d.mkdir(parents=True, exist_ok=True)
    log = tmp_path / "docker_calls.log"
    (d / "docker").write_text(
        "#!/usr/bin/env python3\n"
        "import sys, pathlib\n"
        f"pathlib.Path({str(log)!r}).open('a').write(' '.join(sys.argv[1:])+chr(10))\n"
        "a = sys.argv[1:]\n"
        "if a and a[0] == 'inspect':\n"
        f"    print('sha256:aaaa\\trepo@{digest}')\n"
        "    sys.exit(0)\n"
        "if a and a[0] == 'exec':\n"
        "    if 'command -v' in ' '.join(a):\n"
        "        print('no')\n"
        "    sys.exit(0)\n"
        "sys.exit(0)\n",
        encoding="utf-8")
    (d / "docker").chmod(0o755)
    return d


def _env_with(stub_bin: Path) -> dict:
    env = dict(os.environ)
    env["PATH"] = f"{stub_bin}{os.pathsep}{env.get('PATH', '')}"
    return env


def _run_a3(project: Path, stub_bin: Path, *args: str
            ) -> subprocess.CompletedProcess:
    """A3 as a SUBPROCESS with the stub docker in front of PATH.

    A subprocess, not an in-process call, because the defect this file is about
    was an exception reaching an EXIT CODE: an in-process test would see the
    exception and never learn what rc a caller was handed.
    """
    cmd = [sys.executable, str(A3_PROG), str(project), *args]
    return subprocess.run(cmd, capture_output=True, text=True,
                          env=_env_with(stub_bin), check=False)


@pytest.fixture()
def project(tmp_path):
    p = make_project(tmp_path / "proj", [block("vreg_alpha", "ldo", LDO_SPEC)])
    assert run_prog(A1, p).returncode == 0
    assert run_prog(A2, p).returncode == 0
    return p


# ═══ the tier itself ══════════════════════════════════════════════════════

def test_the_env_refused_tier_is_distinct_from_every_other_producer_tier():
    """Folding it into any existing tier is what let a host condition be read
    as a producer defect. The four it must differ from are named."""
    tiers = {"RC_OK": PC.RC_OK, "RC_NO_INPUT": PC.RC_NO_INPUT,
             "RC_HONEST_GAP": PC.RC_HONEST_GAP, "EX_USAGE": PC.EX_USAGE,
             "EX_ENV_REFUSED": PC.EX_ENV_REFUSED}
    assert len(set(tiers.values())) == len(tiers), tiers


def test_the_refusal_line_is_carried_through_unchanged():
    """`env_refused_line` may format around the detail and may not summarise
    it: the line names both digests, which is what makes it actionable."""
    detail = ("CONTAINER_IMAGE_MISMATCH: container c runs "
              f"{OTHER_DIGEST}, but the pinned runtime is x@"
              f"{PIN.IMAGE_DIGEST}")
    assert detail in PC.env_refused_line("p", detail)


# ═══ MISMATCH — the refusal is recorded, at the step that hit it ═══════════

def test_a_mismatched_container_does_not_exit_1_with_a_traceback(project,
                                                                 tmp_path):
    """THE REPRODUCTION. On the tree that carried the defect this exits 1 with
    `ContainerImageMismatch` on stderr and writes no report at all."""
    stub = _stub_docker(tmp_path, OTHER_DIGEST)
    cp = _run_a3(project, stub, "--verify-sim",
                 "--json", str(tmp_path / "report.json"))
    assert "Traceback" not in cp.stderr, cp.stderr
    assert "ContainerImageMismatch" not in cp.stderr, cp.stderr
    assert cp.returncode == PC.EX_ENV_REFUSED, (cp.returncode, cp.stderr)


def test_the_refusal_names_both_digests_on_stderr(project, tmp_path):
    stub = _stub_docker(tmp_path, OTHER_DIGEST)
    cp = _run_a3(project, stub, "--verify-sim")
    assert PC.ENV_REFUSED_TOKEN in cp.stderr, cp.stderr
    assert PIN.CONTAINER_IMAGE_MISMATCH in cp.stderr, cp.stderr
    assert PIN.IMAGE_DIGEST in cp.stderr, cp.stderr
    assert OTHER_DIGEST in cp.stderr, cp.stderr


def test_the_refusal_reaches_the_json_report(project, tmp_path):
    """The report is the machine-readable half. Before the fix the producer
    exited before writing one, so a caller reading `--json` got a MISSING FILE
    for an outcome the producer knew exactly."""
    stub = _stub_docker(tmp_path, OTHER_DIGEST)
    out = tmp_path / "report.json"
    _run_a3(project, stub, "--verify-sim", "--json", str(out))
    rep = read_json(out)
    assert rep["verdict"] == A3.CONTAINER_IMAGE_MISMATCH
    assert PIN.IMAGE_DIGEST in rep["reason"]
    assert OTHER_DIGEST in rep["reason"]
    assert rep["blocks_env_refused"] == ["vreg_alpha"]
    assert [r["action"] for r in rep["records"]] == [A3.ACTION_ENV_REFUSED]


def test_a_refusal_emits_no_netlist_and_writes_no_gap_file(project, tmp_path):
    """NEITHER artefact, and they fail in opposite directions. A `.sp` would be
    a deck published off a run the environment refused; a `netlist_gap.json`
    would say the DESIGN has a gap, which is a claim about a project the
    producer was never allowed to examine."""
    stub = _stub_docker(tmp_path, OTHER_DIGEST)
    _run_a3(project, stub, "--verify-sim")
    b = bdir(project, "vreg_alpha")
    assert not (b / "vreg_alpha.sp").exists(), sorted(p.name for p in b.iterdir())
    assert not (b / "netlist_gap.json").exists()


def test_nothing_is_run_in_a_mismatched_container(project, tmp_path):
    """The refusal is BEFORE the exec, not after it. The stub logs every
    invocation; `inspect` is how the mismatch is discovered, and `exec` on the
    refused container is the thing that must never appear."""
    stub = _stub_docker(tmp_path, OTHER_DIGEST)
    _run_a3(project, stub, "--verify-sim")
    calls = (tmp_path / "docker_calls.log").read_text().splitlines()
    assert any(c.startswith("inspect") for c in calls), calls
    assert not [c for c in calls if c.startswith("exec")], calls


def test_the_status_is_not_the_absent_simulator_status(tmp_path,
                                                       monkeypatch):
    """A mismatched container is REACHABLE and HAS the simulator. Reporting it
    as `NOT_VERIFIED_NO_SIMULATOR` — which is what
    `_docker_ok` returning False would have done — tells a reader to install a
    tool that is already installed."""
    stub = _stub_docker(tmp_path, OTHER_DIGEST)
    monkeypatch.setenv("PATH", f"{stub}{os.pathsep}{os.environ['PATH']}")
    got = A3.verify_with_ngspice("c_alpha", "vreg_alpha", "* sp", "* tb")
    assert got["simulation_status"] == A3.CONTAINER_IMAGE_MISMATCH_STATUS
    assert got["simulation_status"] != "NOT_VERIFIED_NO_SIMULATOR"
    assert got["env_refused"] is True
    assert got["simulation_verified"] is False
    assert PIN.IMAGE_DIGEST in got["detail"]


def test_a_refusal_returned_as_an_rc_is_the_same_fact_as_a_raised_one(
        tmp_path, monkeypatch):
    """`docker_exec_argv` RAISES; the run wrappers RETURN `IMAGE_MISMATCH_RC`.
    Both mean nothing was run. Without this branch the returned form falls
    through to the log reader, which parses an empty log and answers
    DID_NOT_CONVERGE — charging the deck for a container it never entered."""
    stub = _stub_docker(tmp_path, PIN.IMAGE_DIGEST)   # reachable, MATCHES
    monkeypatch.setenv("PATH", f"{stub}{os.pathsep}{os.environ['PATH']}")
    # ... and the simulator IS found, so the launch is reached.
    monkeypatch.setattr(A3.shutil, "which", lambda _n: "/usr/bin/docker")

    refusal = (f"_container_exec: refused, nothing was run: "
               f"{PIN.CONTAINER_IMAGE_MISMATCH}: container c_alpha runs "
               f"{OTHER_DIGEST}, but the pinned runtime is "
               f"repo@{PIN.IMAGE_DIGEST}")

    def _launch(*a, **k):
        return subprocess.CompletedProcess(
            ["docker"], CE.IMAGE_MISMATCH_RC, "", refusal)

    seen = {}

    def _probe(argv, *a, **k):
        seen["probed"] = True
        return subprocess.CompletedProcess(argv, 0, "yes", "")

    monkeypatch.setattr(A3._pr, "run_best_effort", _probe)
    monkeypatch.setattr(A3._pr, "run", _launch)
    got = A3.verify_with_ngspice("c_alpha", "vreg_alpha", "* sp", "* tb")
    assert seen.get("probed") is True, "the launch was never reached"
    assert got["simulation_status"] == A3.CONTAINER_IMAGE_MISMATCH_STATUS
    assert got["simulation_status"] != "DID_NOT_CONVERGE"
    assert OTHER_DIGEST in got["detail"]


def test_the_pdk_resolver_does_not_launder_the_refusal(tmp_path, monkeypatch):
    """`resolve_pdk_context` wraps its whole body in `except Exception`, whose
    handler records `NEEDS_NATIVE_TEMPLATE` — a statement about the PDK. An
    image mismatch means the resolver was never allowed to look, and must
    leave that function as itself."""
    stub = _stub_docker(tmp_path, OTHER_DIGEST)
    monkeypatch.setenv("PATH", f"{stub}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(
        A3, "_declared_pdk_target", lambda _p: "some_family")

    import analog_pdk_availability as APA

    def _resolve(*a, **k):
        # The real resolver's container entry, reached through the SHIPPED
        # guard rather than an invented raise.
        CE.docker_exec_argv(k.get("container") or "c_alpha", "true")
        raise AssertionError("unreachable: the guard must refuse first")

    monkeypatch.setattr(APA, "resolve_pdk", _resolve)
    with pytest.raises(CE.ContainerImageMismatch):
        A3.resolve_pdk_context(tmp_path, "some_family", "c_alpha", ["nmos"])


# ═══ MATCH — the paired arm ═══════════════════════════════════════════════

def test_a_matching_container_is_not_refused(project, tmp_path):
    """THE CONTROL. Same stub, pinned digest. The producer must walk PAST the
    refusal into its ordinary path — here the simulator probe answers "no", so
    the ordinary capability gap is reported and the deck is still emitted.
    A refusal that fired on every container would pass every test above."""
    stub = _stub_docker(tmp_path, PIN.IMAGE_DIGEST)
    out = tmp_path / "report.json"
    cp = _run_a3(project, stub, "--verify-sim", "--json", str(out))
    assert cp.returncode == PC.RC_OK, cp.stdout + cp.stderr
    assert PC.ENV_REFUSED_TOKEN not in cp.stderr, cp.stderr
    rep = read_json(out)
    assert rep["verdict"] == "EMITTED"
    assert "blocks_env_refused" not in rep, rep.get("blocks_env_refused")
    assert (bdir(project, "vreg_alpha") / "vreg_alpha.sp").is_file()
    calls = (tmp_path / "docker_calls.log").read_text().splitlines()
    assert [c for c in calls if c.startswith("exec")], (
        "the matching arm never entered the container, so it is not the "
        "control it claims to be")

# ═══ the live-ngspice guard — ALREADY FIXED UPSTREAM, and better ══════════
#
# This file carried a test requiring `test_analog_a3_netlist_emit`'s skip guard
# to take its container name from the producer and to consult
# `container_matches_pin`. MEASURED at 94617408 on 8HD-9 the guard did neither
# and the shipped test was RED on pristine main for it. It was fixed on main by
# `1403aa78e` (vibe-ic#2130) while this lane was measuring, and the upstream
# fix is STRICTLY STRONGER than the one here was:
#
#   * it reads `A3_PRODUCER.DEFAULT_CONTAINER`, as this one did;
#   * it routes the probe through `_container_exec.docker_exec_argv`, so a
#     mismatched image is `PROBE_ABSENT` with the refusal's own text — the same
#     outcome this one got from `container_matches_pin`, taken from the one
#     guarded builder rather than from a second reader;
#   * it also closes the EMPTY-VALUE divergence this one did not see
#     (`get(k, default)` hands the guard `""` where `get(k) or default` hands
#     the producer the derived name);
#   * and it asserts against the argv actually PROBED, so putting the literal
#     back is red rather than merely inconsistent.
#
# So the assertion is NOT restated here. `test_the_skip_guard_probes_the_
# container_the_producer_enters` in `test_analog_a3_netlist_emit.py` owns it,
# and a second copy pinned to `container_matches_pin` would go red against the
# better implementation.
