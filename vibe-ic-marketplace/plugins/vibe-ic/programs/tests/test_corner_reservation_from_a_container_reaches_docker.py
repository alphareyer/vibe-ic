"""test_corner_reservation_from_a_container_reaches_docker.py — the reservation
a container DECLARES must be usable by the launcher that consumes it
(lane icadc, 2026-09-15).

THE DEFECT, MEASURED end to end on a real A4 run:

    _run_block -> _run_ngspice -> _aca.launch -> parse_bytes
    AdmissionRefused: memory reservation must be a non-zero whole K/M/G/T value

`declared_reservation(container_memory=...)` takes Docker's
`HostConfig.Memory` — an INTEGER BYTE COUNT — validates it against `_BYTES`
and returns it verbatim: for an 8 GiB container, `("8589934592", 8589934592)`.
`launch` then threw that amount away and re-parsed the STRING with
`parse_bytes`, which requires `_SIZE` (a K/M/G/T suffix) and rejects a bare
byte count ON PURPOSE — in a value a human typed into
`VIBEIC_ANALOG_CORNER_MEMORY`, `8` is ambiguous and must not be guessed at.

So the container-derived path — the fallback EVERY invocation takes when no env
var is set — could never reach Docker. Both halves were already tested,
separately, and neither test fed one's output to the other: the arm below that
does exactly that is the one that was missing.

THE FIX CONVERTS AT THE SINGLE PLACE THE STRING IS PRODUCED rather than
loosening the guard: `declared_reservation`'s container path now returns the
largest K/M/G/T unit that divides the byte count EXACTLY. `parse_bytes` still
refuses an ambiguous human string; that is asserted here so a future change
cannot quietly trade the guard for the bug. Exactly, never rounded — the string
is what `docker --memory` is given, so a rounded one would run a container at a
size the ledger did not reserve.

No chip / SKU / foundry literal.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import analog_corner_admission as aca            # noqa: E402

G = aca.GiB


class _Docker:
    """A runner that answers the two probes `reserve` makes: no live corners,
    so nothing is accounted and the whole budget is free."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        if argv[:3] == ["docker", "ps", "-q"]:
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")


def _ledger(tmp_path, monkeypatch):
    """A ledger with NO live reservations and a fixed budget.

    `active_reservations` is injected rather than left to
    `docker_active_reservations`: that probes the real Docker daemon, so a
    ledger built without it makes this test's answer depend on whatever
    happens to be running on the machine — including, on the host this defect
    was found on, a live corner of the very sweep under discussion.
    """
    monkeypatch.setenv(aca.HOST_STATE_ENV, str(tmp_path / "state"))
    return aca.AdmissionLedger(tmp_path, ram_bytes=128 * G, headroom=16 * G,
                               active_reservations=lambda: {})


def _memory_arg(argv):
    return argv[argv.index("--memory") + 1] if "--memory" in argv else None


# ── the defect arm ─────────────────────────────────────────────────────────
def test_a_container_declared_reservation_reaches_docker(tmp_path, monkeypatch):
    """THE ARM THAT WAS MISSING: the OUTPUT of `declared_reservation` fed to
    `launch`. Before the fix this raised AdmissionRefused and no corner could
    be launched from a container's own declaration."""
    raw, amount = aca.declared_reservation({}, container_memory=str(8 * G))
    assert (raw, amount) == ("8g", 8 * G)
    runner = _Docker()
    cp = aca.launch(_ledger(tmp_path, monkeypatch), job_id="c0",
                    reservation=raw, image="img", project=tmp_path,
                    workdir=tmp_path, simulation_args=["x"], runner=runner)
    assert cp.returncode == 0
    run = [c for c in runner.calls if c[:2] == ["docker", "run"]]
    assert run, runner.calls
    # ...and the limit Docker is GIVEN is the one the ledger RESERVED.
    assert aca.parse_bytes(_memory_arg(run[0])) == amount


def test_the_two_halves_of_this_module_agree_about_the_string(tmp_path,
                                                              monkeypatch):
    """The property, not the mechanism: whatever `declared_reservation`
    returns, `launch` must accept — from EITHER source it supports."""
    for declared in (8 * G, 2 * G, 24 * G, 1536 * 1024 * 1024):
        raw, amount = aca.declared_reservation(
            {}, container_memory=str(declared))
        assert aca.parse_bytes(raw) == amount == declared
        aca.launch(_ledger(tmp_path, monkeypatch), job_id="c", reservation=raw,
                   image="i", project=tmp_path, workdir=tmp_path,
                   simulation_args=["x"], runner=_Docker())
    env_raw, env_amount = aca.declared_reservation(
        {"VIBEIC_ANALOG_CORNER_MEMORY": "8g"})
    assert (env_raw, env_amount) == ("8g", 8 * G)
    aca.launch(_ledger(tmp_path, monkeypatch), job_id="c", reservation=env_raw,
               image="i", project=tmp_path, workdir=tmp_path,
               simulation_args=["x"], runner=_Docker())


# ── the guards that must NOT be traded away for the fix ────────────────────
def test_an_ambiguous_human_string_is_still_refused():
    """`8` could be 8 bytes or 8 GiB. The whole reason `parse_bytes` is strict
    is that a human types this one, and the fix must not have loosened it."""
    for value in ("8", "", "0g", "8x", "eight", "1.5g", None):
        with pytest.raises(aca.AdmissionRefused):
            aca.parse_bytes(value)


def test_launch_without_an_amount_still_validates_the_string(tmp_path,
                                                             monkeypatch):
    """A caller that passes only a string gets the strict path it always had —
    that is the branch protecting every human-supplied reservation."""
    with pytest.raises(aca.AdmissionRefused):
        aca.launch(_ledger(tmp_path, monkeypatch), job_id="c",
                   reservation="8", image="i", project=tmp_path,
                   workdir=tmp_path, simulation_args=["x"], runner=_Docker())


def test_the_conversion_is_exact_and_never_rounded():
    """THE MUTATION THAT WOULD PASS QUIETLY: rounding 1536 MiB up to 2g would
    run a container a GiB larger than the ledger reserved. Every value is
    expressed in the largest unit that divides it EXACTLY, and one no unit
    divides is refused rather than approximated."""
    assert aca._canonical_size(8 * G) == "8g"
    assert aca._canonical_size(1536 * 1024 * 1024) == "1536m"
    assert aca._canonical_size(3 * 1024) == "3k"
    assert aca._canonical_size(2 * 1024 ** 4) == "2t"
    for declared in (8 * G, 1536 * 1024 * 1024, 3 * 1024, 5 * 1024 ** 4):
        assert aca.parse_bytes(aca._canonical_size(declared)) == declared
    with pytest.raises(aca.AdmissionRefused):
        aca._canonical_size(1000)


def test_no_declaration_anywhere_is_refused_not_guessed():
    """The module's own rule, asserted so the fix cannot be read as licence to
    infer one: a host default is not a per-corner declaration."""
    with pytest.raises(aca.AdmissionRefused):
        aca.declared_reservation({})
    for value in ("0", "", "-1", "abc"):
        with pytest.raises(aca.AdmissionRefused):
            aca.declared_reservation({}, container_memory=value)


def test_over_commit_is_still_refused_by_the_budget(tmp_path, monkeypatch):
    """A reservation that reaches Docker is not a reservation that is granted:
    the ledger's budget still decides, and a plan that does not fit answers 0."""
    ledger = _ledger(tmp_path, monkeypatch)
    plan = ledger.plan([{"id": f"c{i}"} for i in range(9)], 64 * G)
    assert plan["safe_concurrency"] == 1, plan
    plan = ledger.plan([{"id": f"c{i}"} for i in range(9)], 8 * G)
    assert plan["safe_concurrency"] == 14, plan
