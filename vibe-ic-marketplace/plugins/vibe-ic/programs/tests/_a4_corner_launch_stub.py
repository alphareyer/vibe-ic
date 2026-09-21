"""Keep an in-process A4 sweep away from the host's docker after #2238.

#2238 (0be153f26) runs every PVT corner in an INDEPENDENT container. Before
the launch the sweep needs a memory reservation -- an explicit
`VIBEIC_ANALOG_CORNER_MEMORY`, else the selected A4 container's own
`HostConfig.Memory` read through a real `docker inspect` -- then that
container's image (another real `docker inspect`), and then a real
`docker run` through `analog_corner_admission.launch`. All three are
host-side and fail CLOSED, which is right for a launch on a host.

The sweeps built by the suites below name their container "fake" and stub
`_docker`, the in-container command runner, so that the thing under test is
what the sweep DOES with a deck -- not where an admission read its numbers
from. Unstubbed, every one of them stopped at the first host-side read
(`cannot read selected A4 container memory declaration`, 23 reds across
four files on the first clean measurement after #2238 landed). This helper
declares the reservation the explicit way the sweep documents, keeps the
admission ledger out of the host's shared state directory, and routes the
image and the launch through the SAME fake the in-container commands already
go through: the launch's last simulation arg IS the ngspice command line, so
the fake answers it exactly as it answers `_docker`.

Nothing here reaches the admission's own suite
(`test_issue2236_aggregate_ram_admission.py`), which measures the real
reservation, ledger and argv against fakes of its own.
"""
from __future__ import annotations

import tempfile


def stub_independent_corner_launch(monkeypatch, sweep, fake_docker) -> None:
    """`sweep` is the imported `analog_real_corner_sweep`; `fake_docker` is the
    `(container, cmd, timeout=...) -> CompletedProcess` fake already installed
    as its `_docker`."""
    # THE ADMISSION ARITHMETIC MUST NOT DEPEND ON THE MACHINE.  Measured
    # 2026-09-21: `AdmissionLedger.plan` computes
    #
    #     budget      = physical_ram_bytes() - headroom      (headroom 16GiB)
    #     concurrency = (budget - accounted) // reservation
    #
    # and `physical_ram_bytes()` is `SC_PHYS_PAGES * SC_PAGE_SIZE` -- the
    # MACHINE's RAM, which a container does not change.  A declared 32g
    # reservation is a large share of a mid-size host, so on a machine where
    # `RAM - 16GiB < 2 * 32g` the sweep really executes ONE corner and derives
    # the other eight, and the A4 gate then refuses -- correctly:
    #
    #     A4_PVT_SWEEP_NOT_MEASURED: ... `full_pvt_sweep_executed: False`;
    #     corners_executed 1/9
    #
    # REPRODUCED ON THE HOST, python 3.10, NO CONTAINER, by changing nothing
    # but the budget: `VIBEIC_ANALOG_CORNER_HEADROOM=100GiB` turns 2 passed
    # into 2 failed with exactly that message, on a 125 GiB host.  So the red
    # reported as "host passes, image fails" is neither about the image nor
    # about the interpreter: it is about how much RAM the machine has.
    #
    # Nothing here is faked away.  This sweep launches NO container and runs NO
    # ngspice -- `_corner_image` and `_aca.launch` are both stubbed below -- so
    # the reservation is a fiction either way, and its only job is to let the
    # admission run.  Declaring a SMALL one, and declaring the headroom instead
    # of inheriting it, makes the admission's answer a property of this test
    # rather than of the host it happens to run on.  The admission still
    # executes, still plans, and can still refuse; its REAL numbers are
    # measured by `test_issue2236_aggregate_ram_admission.py` against fakes of
    # its own, exactly as this file's docstring says.
    monkeypatch.setenv("VIBEIC_ANALOG_CORNER_MEMORY", "64m")
    monkeypatch.setenv("VIBEIC_ANALOG_CORNER_HEADROOM", "1MiB")
    monkeypatch.setenv("VIBEIC_ANALOG_CORNER_ADMISSION_STATE_DIR",
                       tempfile.mkdtemp(prefix="corner-admission-"))
    monkeypatch.setattr(sweep, "_corner_image", lambda container: "fake-image")
    monkeypatch.setattr(
        sweep._aca, "launch",
        lambda ledger, **kw: fake_docker("fake", str(kw["simulation_args"][-1])))
