#!/usr/bin/env python3
"""Pin the EXEC ROUTE a test measures, instead of reading it off the machine.

WHY THIS EXISTS
===============
`_container_exec.no_container_route()` is the ONE definition of "can this
process reach a container", and it answers by asking the MACHINE:
`shutil.which("docker") is None`. That is correct for production — a runner
already inside the image has no docker client and must run its tools on its own
filesystem.

It is NOT correct for a test that asserts what the CONTAINER route emits.
MEASURED 2026-09-08 on 8HD-8 at main `8862fd37`, inside the pinned image
`ghcr.io/vibeic/vibeic-eda@sha256:89a8fd72…`: seven such tests are RED, with
failures of the shape `assert 'bash' == 'docker'`. They are green on a host
that happens to carry a docker client and red inside the image, and the commit
is identical in both. A test whose verdict is a property of the machine it
landed on cannot tell "this tree is broken" from "this machine lacks docker" —
and the second is not a defect in anything.

The repair is NOT to skip them and NOT to loosen what they assert. It is to
make the route an INPUT of the test rather than an observation of the host:
declare which route is the subject, pin it, and keep every assertion.

Both directions are provided on purpose. `pin_container_route` is the one the
seven needed; `pin_local_route` exists so the other branch is reachable from a
host that DOES carry a docker client — the same host-dependence, mirrored.
Neither ever runs docker: they decide which argv the code under test BUILDS.

chip-AGNOSTIC: process routing only. No design, PDK, vendor or tool literal.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _container_exec as _ce  # noqa: E402

__all__ = ["pin_container_route", "pin_local_route"]

_FAKE_DOCKER = "/usr/bin/docker"


def _pin(monkeypatch, *, has_docker: bool) -> None:
    real_which = shutil.which

    def _which(name, *a, **kw):
        if name == "docker":
            return _FAKE_DOCKER if has_docker else None
        return real_which(name, *a, **kw)

    # `_container_exec` is the one module that asks the question, so patching
    # its `shutil` is enough for every caller that goes through it — which is
    # every caller, by construction (`no_container_route`'s docstring).
    monkeypatch.setattr(_ce.shutil, "which", _which)
    # The announcement is once-per-tag-per-PROCESS, and a pinned route in one
    # test must not silence the notice a later test asserts on.
    monkeypatch.setattr(_ce, "_ANNOUNCED", set())


def pin_container_route(monkeypatch) -> None:
    """Declare: this test measures the CONTAINER route. There is a client."""
    _pin(monkeypatch, has_docker=True)


def pin_local_route(monkeypatch) -> None:
    """Declare: this test measures the LOCAL route. There is no client."""
    _pin(monkeypatch, has_docker=False)
