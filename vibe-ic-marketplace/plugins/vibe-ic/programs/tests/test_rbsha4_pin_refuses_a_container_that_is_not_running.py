#!/usr/bin/env python3
"""--require-image must not be satisfied by a container that is NOT RUNNING.

MEASURED 2026-09-07 on 8HD-6 (lane rbsha4), from docker's own timestamps:

    08:26:11Z  run with no such container -> the runner REFUSED, verbatim:
               "not satisfied: FAIL - no container named 'rbsha4-eda' ...
                refusing to continue: every step verdict from here would be
                measured against a toolchain this run cannot attest to."
    08:52:39.748Z  the container was created
    08:52:39.942Z  the container EXITED (code 1)
    08:52:44Z  the SAME command relaunched -> the runner did NOT refuse.
    08:53:44Z  one step later, in the run's own words:
               "[#902 sim-toolchain DIVERGED] container 'rbsha4-eda' was
                declared (image ...) but verilator ran on the HOST ...
                the run VERIFIED one toolchain and USED another"

So the pin is keyed on the container EXISTING and carrying the right image, not
on it being able to RUN anything -- and a named-but-dead container therefore
passes the pin while every tool silently falls back to the host. That is the
precise state the ABSENT-container refusal message says it exists to prevent;
#902 caught the consequence one step later and per-tool, not the pin.

`inspect_container` ALREADY reports `running` (it is parts[3] of _INSPECT_FMT).
`verify` simply never consulted it. And `--require-image`'s own CLI help has
always read "image ref or id the container MUST be running" -- the intent was
documented and unimplemented.

NO DOCKER REQUIRED: `inspect_container` is substituted, so this asserts the
DECISION in `verify`, which is the thing that was wrong.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import container_image_provenance as cip  # noqa: E402

IMAGE_REF = "example.invalid/eda@sha256:" + "0" * 64
IMAGE_ID = "sha256:" + "1" * 64


def _rec(running: bool) -> dict:
    return {"status": "ok", "container": "c", "image_ref": IMAGE_REF,
            "image_id": IMAGE_ID, "running": running,
            "created": "2026-09-07T08:52:39Z"}


@pytest.fixture
def stub(monkeypatch):
    def _install(running: bool):
        monkeypatch.setattr(cip, "inspect_container",
                            lambda name: dict(_rec(running)))
        # resolve ONLY the demanded ref to this image; any other ref is a
        # different image, so the pre-existing MISMATCH path stays reachable.
        monkeypatch.setattr(cip, "_resolve_image_id",
                            lambda ref: IMAGE_ID if ref == IMAGE_REF
                            else "sha256:" + "2" * 64)
    return _install


def test_a_not_running_container_does_not_satisfy_require_image(stub):
    """THE DEFECT. A container that exists, carries the demanded image, and is
    NOT RUNNING must not return PASS -- it can execute nothing."""
    stub(running=False)
    rec = cip.verify("c", IMAGE_REF)
    assert rec["verdict"] != "PASS", (
        "a NOT-RUNNING container satisfied --require-image: " + repr(rec))


def test_the_refusal_says_the_container_is_not_running(stub):
    """A verdict a reader cannot act on is half a verdict: the reason must name
    the not-running state, not merely decline."""
    stub(running=False)
    rec = cip.verify("c", IMAGE_REF)
    assert "running" in str(rec.get("reason", "")).lower(), repr(rec)


def test_it_refuses_even_with_no_require_image(stub):
    """The identity record is consumed whether or not a pin was demanded, so a
    dead container must not resolve to a clean PASS on the unpinned path."""
    stub(running=False)
    rec = cip.verify("c", None)
    assert rec["verdict"] != "PASS", repr(rec)


def test_negative_control_a_running_container_still_passes(stub):
    """THE OTHER DIRECTION. Without this the fix could be 'always refuse',
    which would pass the three assertions above and break every real run."""
    stub(running=True)
    rec = cip.verify("c", IMAGE_REF)
    assert rec["verdict"] == "PASS", repr(rec)
    assert rec.get("image_match") is True, repr(rec)


def test_negative_control_a_running_container_still_reports_mismatch(stub):
    """And the pre-existing MISMATCH decision must survive the new refusal."""
    stub(running=True)
    rec = cip.verify("c", "example.invalid/other@sha256:" + "2" * 64)
    assert rec["verdict"] == "MISMATCH", repr(rec)
