"""The dynamic-IR deck reads the liberty WHERE IT IS, not only on the host (icspm2).

MEASURED FAILURE THIS PINS
==========================
A completed gf180mcuD run of `spm` (2026-09-15) produced no dynamic-IR number:

    reports/phase3/dynamic_ir.json
        "status": "ERROR_NO_PSM_IR", "dynamic_ir_report_emitted": false
        log_tail: "[INFO  PSM-0040] All shapes on net VDD are connected.
                   [ERROR PSM-0079] Cannot determine the supply voltage for VDD."

The PDN was CONNECTED; PSM lacked the rail VOLTAGE. vibe-ic#362 already fixes
exactly that, by emitting `set_operating_conditions <name>` when the library
declares an `operating_conditions(<name>)` block and no default — which is the
state of 30 of 30 gf180mcuD standard-cell liberties.

`reports/phase3/dynamic_ir_transient.tcl`, read end to end, carried NO such
line, because `liberty_operating_condition` read the path ON THE HOST:

    liberty : /foss/pdks/…/gf180mcu_fd_sc_mcu7t5v0__tt_025C_5v00.lib
    host `test -e`                     -> NO   (`/foss` is container-only)
    liberty_operating_condition(host)  -> ''            (OSError, swallowed)
    the same bytes, read in-container  -> 'gf180mcu_fd_sc_mcu7t5v0__tt_025C_5v00'

The module's docstring asserted "this program already receives host-visible
paths", and that is false for every PDK this flow runs. The RUNNER's copy of
the same helper takes a `container` (`_read_pdk_text`, whose own docstring
documents this failure class for LEF discovery); the standalone copy dropped
exactly that half.

AFTER THE FIX, measured on the same run tree: the deck carries
`catch {set_operating_conditions gf180mcu_fd_sc_mcu7t5v0__tt_025C_5v00}`,
PSM prints `Supply voltage : 5.00e+00 V` and `Worst dynamic IR drop : 1.58e-03 V`,
and `dynamic_ir_drop_check … --budget-pct 10` exits 0 PASS.
"""
import inspect
import subprocess
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import dynamic_ir_vectored_emit as dive  # noqa: E402

# The shape the PDK actually ships (no quotes, trailing space), from
# gf180mcu_fd_sc_mcu7t5v0__tt_025C_5v00.lib:44.
LIBERTY_TEXT = """\
library (gf180mcu_fd_sc_mcu7t5v0__tt_025C_5v00) {
  time_unit : 1ns ;
  nom_voltage : 5 ;
  operating_conditions(gf180mcu_fd_sc_mcu7t5v0__tt_025C_5v00) { 
    process : 1 ;
    voltage : 5 ;
  }
}
"""
OC_NAME = "gf180mcu_fd_sc_mcu7t5v0__tt_025C_5v00"

#: A path that exists in the container and NOT on the host — the real shape.
CONTAINER_ONLY = ("/foss/pdks/ciel/gf180mcu/versions/b344c97e/gf180mcuD/"
                  "libs.ref/gf180mcu_fd_sc_mcu7t5v0/lib/"
                  "gf180mcu_fd_sc_mcu7t5v0__tt_025C_5v00.lib")


@pytest.fixture
def fake_container(monkeypatch):
    """`docker exec <c> cat <p>` serves LIBERTY_TEXT for CONTAINER_ONLY only.

    Hermetic: no docker is run. It also RECORDS the argv, so a test can assert
    the read went through the module's one guarded argv builder."""
    calls = []

    class _CP:
        def __init__(self, rc, out):
            self.returncode, self.stdout, self.stderr = rc, out, ""

    def _run(argv, *a, **kw):
        calls.append(list(argv))
        if len(argv) >= 2 and argv[-2] == "cat" and argv[-1] == CONTAINER_ONLY:
            return _CP(0, LIBERTY_TEXT)
        return _CP(1, "")

    monkeypatch.setattr(dive.subprocess, "run", _run)
    return calls


def _oc(liberty, container=None):
    """Call through `getattr`/`signature` so the PRE-FIX function RUNS.

    The pre-fix `liberty_operating_condition` takes ONE argument; calling it
    with two raises TypeError, and a TypeError observes nothing — it proves the
    signature is new, not that the old code answered wrongly. Dropping the
    argument makes the old code answer '' on a container-only path, which is
    the defect, and the assertion below is what fails."""
    fn = dive.liberty_operating_condition
    if "container" in inspect.signature(fn).parameters:
        return fn(liberty, container)
    return fn(liberty)


def _tcl(tmp_path, liberty, container=None):
    fn = dive._build_transient_tcl
    kw = {}
    if "container" in inspect.signature(fn).parameters:
        kw["container"] = container
    return fn(tmp_path / "x.def", tmp_path / "t.tlef", tmp_path / "c.lef",
              Path(liberty), [], None, "VDD", 24.0, 100, None, {}, "MET", **kw)


# ---------------------------------------------------------------------------
# DIRECTION 1 — a container-only liberty is read, and the deck gets the line
# ---------------------------------------------------------------------------
def test_a_container_only_liberty_yields_its_operating_condition(fake_container):
    assert _oc(CONTAINER_ONLY, "some-eda") == OC_NAME


def test_the_deck_carries_set_operating_conditions_for_it(tmp_path, fake_container):
    tcl = _tcl(tmp_path, CONTAINER_ONLY, "some-eda")
    assert f"catch {{set_operating_conditions {OC_NAME}}}" in tcl, tcl


def test_the_line_precedes_read_def(tmp_path, fake_container):
    """OpenROAD applies the operating condition to the timing library; it must
    be selected before the design is read, exactly as the runner emits it."""
    tcl = _tcl(tmp_path, CONTAINER_ONLY, "some-eda")
    assert tcl.index("set_operating_conditions") < tcl.index("read_def")


def test_the_read_goes_through_the_guarded_docker_argv(fake_container):
    _oc(CONTAINER_ONLY, "some-eda")
    assert fake_container, "no docker exec was attempted at all"
    argv = fake_container[-1]
    assert "docker" in argv[0] or argv[0].endswith("docker"), argv
    assert argv[-2:] == ["cat", CONTAINER_ONLY], argv


# ---------------------------------------------------------------------------
# DIRECTION 2 — nothing is invented, and the old behaviour is preserved
# ---------------------------------------------------------------------------
def test_a_host_visible_liberty_is_still_read_from_the_host(tmp_path, fake_container):
    lib = tmp_path / "local.lib"
    lib.write_text(LIBERTY_TEXT)
    assert _oc(str(lib), "some-eda") == OC_NAME
    assert not fake_container, ("the host copy must win — no container read "
                                f"should have happened: {fake_container}")


def test_no_container_means_host_only_exactly_as_before(fake_container):
    assert _oc(CONTAINER_ONLY, None) == ""
    assert not fake_container


def test_a_liberty_nobody_can_see_emits_no_line(tmp_path, fake_container):
    """Neither host nor container has it: the deck is byte-identical to the
    pre-#362 one, which is the documented `_oc == ""` behaviour."""
    tcl = _tcl(tmp_path, "/foss/pdks/nope/absent.lib", "some-eda")
    assert "set_operating_conditions" not in tcl


def test_a_liberty_with_no_operating_conditions_block_emits_no_line(
        tmp_path, fake_container):
    lib = tmp_path / "plain.lib"
    lib.write_text("library (x) {\n  time_unit : 1ns ;\n  nom_voltage : 5 ;\n}\n")
    assert _oc(str(lib), "some-eda") == ""
    assert "set_operating_conditions" not in _tcl(tmp_path, str(lib), "some-eda")


def test_a_container_that_cannot_read_it_emits_no_line(tmp_path, fake_container):
    """`docker exec` returning non-zero must not be read as an empty file that
    somehow matched — it must leave the deck unchanged."""
    other = "/foss/pdks/other/not_served.lib"
    assert _oc(other, "some-eda") == ""
    assert "set_operating_conditions" not in _tcl(tmp_path, other, "some-eda")


def test_pdk_text_returns_empty_and_never_raises_on_a_broken_container(
        tmp_path, monkeypatch):
    def _boom(*a, **kw):
        raise OSError("docker is not running")
    monkeypatch.setattr(dive.subprocess, "run", _boom)
    fn = getattr(dive, "_pdk_text", None)
    if fn is None:                      # pre-fix tree: nothing to protect
        pytest.skip("pre-fix tree has no _pdk_text")
    assert fn(CONTAINER_ONLY, "some-eda") == ""


def test_pdk_text_on_a_falsy_path_is_empty():
    fn = getattr(dive, "_pdk_text", None)
    if fn is None:
        pytest.skip("pre-fix tree has no _pdk_text")
    assert fn(None, "some-eda") == ""
    assert fn("", "some-eda") == ""
