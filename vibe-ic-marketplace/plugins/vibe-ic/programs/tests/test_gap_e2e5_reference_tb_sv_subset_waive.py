#!/usr/bin/env python3
"""ORGANIC (GAP-E2E-5) — reference_tb SV-subset WAIVE for REUSED-IP.

An SV construct beyond the iverilog/sv2v OSS-sim subset (e.g. OpenTitan's
cross-package `pkg::PARAM` in a param default) blocks the reference_tb COMPILE
even though yosys+slang synthesises the SAME RTL clean. For an upstream-validated
REUSED-IP DUT that is a tool-subset limit, NOT a design defect → demote to a
DISCLOSED WAIVE, not a hard phase2 FAIL.

§4.05 NO-LEAK (load-bearing): demote ONLY when (a) the failure carries a genuine
SV-construct/syntax signature (NOT a missing-module / port structural defect) AND
(b) the project is REUSED-IP (SOURCE_MANIFEST reused_ip:true). An authored
(non-reused) RTL, or a real structural error, still hard-FAILs.

THE CONTAINER IS DERIVED, NOT WRITTEN DOWN — vibe-ic#2130's class, fifth
instance. This file handed `_run_oracle_tb` the literal `vibeic-eda`, which was
the shared default until `_eda_pin.default_container_name` began deriving the
name from the required digest (`vibeic-eda-<digest12>`). On a host where that
literal names somebody else's container, `_iverilog_available` builds a guarded
`docker exec` argv, `_container_exec.docker_exec_argv` measures the disagreement
and raises, and nothing on this path catches it — so all three ids below went RED
with a `ContainerImageMismatch` traceback that has nothing to do with the SV
subset they are about. Named independently by lanes cz2146 and czsimbridge on
8HD-6 (that container running 0.3.46 against a pin of 0.3.48); reproduced here at
the pin read, without touching any shared container.

NO SKIP IS ADDED, DELIBERATELY. The A3 ngspice guard that this rule was written
for needs its container to be PRESENT; this file does not — the compile is
injected and `_iverilog_available` is answered from the host, so every id here
passes with the derived container absent, which is the normal state of every
host. A skip keyed on presence would delete three passing verifications
everywhere. What the derived name buys is that the argv builder is asked about
the container the PRODUCER would enter, and a measured mismatch on it is
REFUSED BY NAME instead of arriving as a traceback about a subset waiver.

chip-AGNOSTIC: synthetic fixtures + the shared signature set.
"""
from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import design_one_shot_runner as D   # noqa: E402
import _container_exec as _ce        # noqa: E402 — the guarded docker-exec argv
import _eda_pin as _pin              # noqa: E402 — the ONE pin
from _stated_eda_image import state_the_image  # noqa: E402

#: The container the producer's own CLI default names, derived the same way.
_CONTAINER = _pin.default_container_name()



def _mk_project(tmp_path, reused_ip: bool) -> Path:
    project = tmp_path / "proj"
    rtl = project / "phase2" / "stage1" / "rtl"
    rtl.mkdir(parents=True, exist_ok=True)
    (rtl / "dut.sv").write_text("module dut(); endmodule\n")
    if reused_ip is not None:
        (rtl / "SOURCE_MANIFEST.json").write_text(
            json.dumps({"reused_ip": bool(reused_ip)}))
    return project


def test_is_reused_ip_project(tmp_path):
    assert D._is_reused_ip_project(_mk_project(tmp_path / "a", True)) is True
    assert D._is_reused_ip_project(_mk_project(tmp_path / "b", False)) is False
    # no manifest at all
    p = tmp_path / "c" / "proj"
    (p / "phase2" / "stage1" / "rtl").mkdir(parents=True)
    assert D._is_reused_ip_project(p) is False


def _drive_oracle_with_compile(monkeypatch, tmp_path, reused_ip, compile_err):
    """Run _run_oracle_tb with iverilog present + the compile forced to FAIL
    with `compile_err`. Returns the StepResult (or None)."""
    project = _mk_project(tmp_path, reused_ip)
    tb = project / "phase2" / "stage1" / "sim_full_stack" / "tb.v"
    tb.parent.mkdir(parents=True, exist_ok=True)
    tb.write_text("module tb; endmodule\n")
    import shutil
    monkeypatch.setattr(shutil, "which", lambda _n: "/usr/bin/iverilog")
    # force the compile-with-sv-fallback to FAIL with the given signature
    monkeypatch.setattr(
        D, "_iverilog_compile_with_sv_fallback",
        lambda *a, **k: (2, "", compile_err, "iverilog_g2012"))
    try:
        return D._run_oracle_tb(project, "dut", tb, "test", 0.0, _CONTAINER)
    except _ce.ContainerImageMismatch as exc:
        pytest.fail(
            f"{_pin.CONTAINER_IMAGE_MISMATCH}: the container this producer "
            f"would enter, {_CONTAINER}, exists on this host and runs bytes "
            f"other than the pinned ones, so nothing was measured about the SV "
            f"subset. This is a fact about the host, not about the waiver — "
            f"remove or re-create that container. Verbatim: {exc}")


_SV_SUBSET_ERR = "aes_pkg.sv:19: sorry: constant selects not supported"
_REAL_DEFECT_ERR = "error: Unknown module type: missing_child_module"


def test_sv_subset_on_reused_ip_is_waived(monkeypatch, tmp_path):
    r = _drive_oracle_with_compile(monkeypatch, tmp_path, True, _SV_SUBSET_ERR)
    assert r is not None and r.status == "PASS_WITH_WAIVERS"
    assert r.extras.get("sv_subset_waived") is True


def test_sv_subset_on_authored_rtl_still_fails(monkeypatch, tmp_path):
    # §4.05 NO-LEAK: non-reused (authored) RTL must NOT be waived.
    r = _drive_oracle_with_compile(monkeypatch, tmp_path, False, _SV_SUBSET_ERR)
    assert r is not None and r.status == "FAIL"


def test_real_defect_on_reused_ip_still_fails(monkeypatch, tmp_path):
    # §4.05 NO-LEAK: a real missing-module defect (no SV signature) must NOT be
    # waived even on a REUSED-IP design.
    r = _drive_oracle_with_compile(monkeypatch, tmp_path, True, _REAL_DEFECT_ERR)
    assert r is not None and r.status == "FAIL"


def test_a_wrong_image_container_is_refused_by_name(monkeypatch, tmp_path):
    """A MEASURED mismatch on the derived container is a fact about the host and
    is reported as one — not as a `ContainerImageMismatch` traceback out of a
    test about an SV subset. This is the red cz2146 and czsimbridge measured,
    driven at the pin read rather than by touching a shared container."""
    state_the_image(monkeypatch)  # the pin is STATED (lane rfimg2): no host docker
    other = "sha256:" + "9" * 64
    monkeypatch.setattr(_pin, "container_image_digest",
                        lambda c: (other, "") if c == _CONTAINER
                        else (None, f"{_pin.CONTAINER_ABSENT}: {c}"))
    with pytest.raises(pytest.fail.Exception) as caught:
        _drive_oracle_with_compile(monkeypatch, tmp_path, True, _SV_SUBSET_ERR)
    said = str(caught.value)
    assert _pin.CONTAINER_IMAGE_MISMATCH in said
    assert _CONTAINER in said and other in said and _pin.IMAGE_DIGEST in said


def test_the_waiver_still_holds_with_the_pinned_container_present(monkeypatch,
                                                                 tmp_path):
    """RUN WHEN PRESENT. With the derived container present AND running the
    pinned bytes, nothing about the subset verdict changes — the paired control
    that stops the refusal above being paid for in the case this file is for."""
    state_the_image(monkeypatch)  # the pin is STATED (lane rfimg2): no host docker
    monkeypatch.setattr(_pin, "container_image_digest",
                        lambda c: (_pin.IMAGE_DIGEST, "") if c == _CONTAINER
                        else (None, f"{_pin.CONTAINER_ABSENT}: {c}"))
    r = _drive_oracle_with_compile(monkeypatch, tmp_path, True, _SV_SUBSET_ERR)
    assert r is not None and r.status == "PASS_WITH_WAIVERS"
    assert r.extras.get("sv_subset_waived") is True
