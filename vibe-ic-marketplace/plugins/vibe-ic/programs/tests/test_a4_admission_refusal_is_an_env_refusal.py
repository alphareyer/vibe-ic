"""A4's corner admission refusing BEFORE any simulator launch is an
environment refusal, not a skill deferral (lane mig94, T94 A4 harvest #1).

MEASURED 2026-09-26 on 8HD-4 with vibeic-eda 0.3.77 (by digest) on
u_hawaii_adc/ldo: the A4 container was started without a memory ceiling, so
`_corner_reservation` found `HostConfig.Memory == 0` and raised
`AdmissionRefused` from the first single-deck launch. Nothing caught it:
`analog_real_corner_sweep` died with a traceback (exit 1), wrote no
`corner_results.json`, and `analog_one_shot_runner` reported A4 as
`WAIVED ... invoke skill ams-sim` — a skill that cannot supply a container or
a memory ceiling.

The rule now: a producer that could not reach its tool says so on a line that
STARTS with `ENV_REFUSED:` and exits `EX_ENV_REFUSED`; the runner reads either
signal as the ENV_UNAVAILABLE tier.

WHAT IS REAL HERE. The exception comes from the shipped
`_corner_reservation` -> `analog_corner_admission.declared_reservation` chain,
reading `HostConfig.Memory` from a stub `docker` on PATH (the only faked
tool). The deck preparation `_run_block` does before its first launch is
replaced by that one call: preparing a deck needs a staged PDK and is not what
decides this tier. The paired arm declares a ceiling through the same stub and
must NOT be refused.
"""
from __future__ import annotations

import os
from pathlib import Path

import _plugin_tree  # noqa: F401 — puts programs/ on sys.path
import _analog_producer_common as PC


def _stub_docker(tmp_path: Path, memory: str) -> Path:
    d = tmp_path / "stub_bin"
    d.mkdir(parents=True, exist_ok=True)
    (d / "docker").write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "a = sys.argv[1:]\n"
        "if a and a[0] == 'inspect' and 'HostConfig.Memory' in ' '.join(a):\n"
        f"    print({memory!r}); sys.exit(0)\n"
        "sys.exit(1)\n", encoding="utf-8")
    (d / "docker").chmod(0o755)
    return d


def _run_block_reaching_admission(tmp_path, monkeypatch, memory):
    import analog_real_corner_sweep as ARS
    monkeypatch.delenv("VIBEIC_ANALOG_CORNER_MEMORY", raising=False)
    monkeypatch.delenv("VIBEIC_DOCKER_MEMORY", raising=False)
    monkeypatch.setenv("PATH", f"{_stub_docker(tmp_path, memory)}"
                               f"{os.pathsep}{os.environ['PATH']}")
    reached = {}

    def body(project, block, container, pdk, topology_override):
        reached["reservation"] = ARS._corner_reservation(container)
        return 0

    monkeypatch.setattr(ARS, "_run_block", body)
    rc = ARS.run_block(tmp_path, "blk", "vibeic-eda", "", "auto")
    return rc, reached


def test_an_undeclared_memory_ceiling_is_an_env_refusal_not_a_traceback(
        tmp_path, monkeypatch, capsys):
    rc, reached = _run_block_reaching_admission(tmp_path, monkeypatch, "0")
    err = capsys.readouterr().err
    assert rc == PC.EX_ENV_REFUSED, err
    assert "reservation" not in reached
    lines = [ln for ln in err.splitlines() if ln.startswith(PC.ENV_REFUSED_TOKEN)]
    assert lines and "admission" in lines[0], err


def test_a_declared_ceiling_takes_the_ordinary_path(tmp_path, monkeypatch,
                                                    capsys):
    """PAIRED ARM: the same stub with a declared ceiling is admitted."""
    rc, reached = _run_block_reaching_admission(
        tmp_path, monkeypatch, str(8 * 1024 ** 3))
    assert rc == 0, capsys.readouterr().err
    assert reached["reservation"][1] == 8 * 1024 ** 3


def test_the_runner_reads_the_refusal_line_as_the_env_tier():
    """The runner's reader over the producer's own output: the line the sweep
    now prints is recognised; the old mid-line spelling was not."""
    import analog_one_shot_runner as R

    class _CP:
        def __init__(self, err):
            self.stderr, self.stdout, self.returncode = err, "", 69

    new = _CP("ENV_REFUSED: analog_real_corner_sweep block=b: corner "
              "admission refused before any simulator launch (x)\n")
    assert R._producer_env_gap(new)
    old = _CP("[real_sim] block=b ENV_REFUSED: nothing was simulated\n")
    assert R._producer_env_gap(old) is None


def test_the_a4_step_reports_env_unavailable_not_a_skill_waiver(
        tmp_path, monkeypatch):
    """The runner's A4 branch, handed the sweep's EXIT CODE with no token
    line: the row is the env tier, never `WAIVED ... invoke skill`."""
    import analog_one_shot_runner as R
    project = tmp_path / "proj"
    b = project / "phase3" / "analog" / "blk"
    b.mkdir(parents=True)
    (b / "blk.sp").write_text(".subckt blk a b\nR1 a b 1k\n.ends\n")
    (project / "phase3" / "analog" / "analog_block_list.json").write_text(
        '{"blocks": [{"name": "blk", "type": "ldo"}]}')
    real_run = R._pr.run

    class _CP:
        def __init__(self, rc, out="", err=""):
            self.returncode, self.stdout, self.stderr = rc, out, err

    def fake_run(cmd, *a, **k):
        if any(str(x).endswith("analog_real_corner_sweep.py") for x in cmd):
            return _CP(PC.EX_ENV_REFUSED, err="corner launch refused\n")
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(R._pr, "run", fake_run)
    res = R.step_for_block(project, {"name": "blk", "type": "ldo"},
                           "A4_corner_sweep", None)
    assert "invoke skill" not in res.detail, res.detail
    assert res.extras.get("verdict_tier") == "ENV_UNAVAILABLE", (res.status,
                                                                res.detail)
