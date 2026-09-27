#!/usr/bin/env python3
"""A container that is REFUSED is not a container that is ABSENT — three sites.

WHAT WAS MEASURED — vibe-ic#2156, 2026-09-07 on 8hd-3
=====================================================
`_container_exec.docker_exec_argv` raises `ContainerImageMismatch` when the
container it is asked for runs bytes other than the pinned ones. It is a
`RuntimeError`, and three shipped sites did the wrong thing with it:

  A. `analog_a6_native_pv.second_engine_drc`'s default runner caught only
     `(OSError, ValueError, SystemExit)`, so the mismatch ESCAPED
     `run_block_pv` and took the whole A6 producer down. Worse, the runner's
     `why` was already being dropped one frame below (`attribution, why =
     run(...)` then `if attribution is None: return None`), so every OTHER way
     the engine could fail to run reached the report as one fixed sentence.
  B. `analog_a3_netlist_emit._docker_ok` caught only
     `(OSError, subprocess.SubprocessError)` — the same escape, and its caller
     reports "container is not reachable", which is ABSENCE and sends the
     reader to start a container that is already running.
  C. `phase3_one_shot_runner._v1_6_604_read_text_or_container_cat` wrapped the
     whole thing in `except Exception: pass` and returned `None`, so "the pin
     check could not be made" and "the file is not there" arrived as the same
     answer. That is the one rule every other input in this repo is held to.

THE RULE THIS FILE PINS, PER SITE
=================================
The refusal is classified BY NAME — the container that was reached and the
digest that was wanted — it is never reported as absence, and it never escapes
as an unhandled exception. A digest that could NOT be read stays NOT a
mismatch: `_eda_pin.container_pin_state` separates those, and a site that
refused on "unreadable" would stop a legitimately locally-built container from
being used at all.

chip-AGNOSTIC: container identity only. No design, PDK, vendor or host literal;
the container names here are synthetic and no container is contacted.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile

import pytest

_PROGRAMS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROGRAMS))
import _container_exec as _ce  # noqa: E402
import _eda_pin as _pin  # noqa: E402
import analog_a6_native_pv as PV  # noqa: E402
import analog_a3_netlist_emit as A3  # noqa: E402
import phase3_one_shot_runner as P3  # noqa: E402
from _stated_eda_image import state_the_image  # noqa: E402

#: A synthetic container name and a synthetic digest that is NOT the pin, so a
#: refusal composed from them is unmistakably about this test.
_CTN = "a-container-of-this-tests-own"
_OTHER = "sha256:" + "9" * 64


@pytest.fixture(autouse=True)
def _the_stated_image(monkeypatch):
    """The pin is STATED
    (lane rfimg2). Unstated, it was read from THIS HOST's docker: inside the
    image (no docker) `ImageNotResolvable` reddened these tests before they
    reached their subject, or turned a staged mismatch into UNREADABLE."""
    state_the_image(monkeypatch)


def _refuse_this_container(monkeypatch, container: str = _CTN):
    """Make `docker_exec_argv` refuse `container` the way a real mismatch does.

    Driven at `_eda_pin.container_image_digest` — the ONE read that decides —
    so the refusal sentence the sites receive is composed by the PROGRAM and
    not by this test. Nothing here contacts docker.
    """
    monkeypatch.setattr(_pin, "container_image_digest",
                        lambda c: (_OTHER, "") if c == container
                        else (None, f"{_pin.CONTAINER_ABSENT}: {c}"))


# ═══ the model itself bites ════════════════════════════════════════════════

def test_the_refusal_this_file_drives_is_the_programs_own(monkeypatch):
    """THE POSITIVE CONTROL for every arm below. If `docker_exec_argv` stopped
    raising, every site test would pass for the wrong reason."""
    _refuse_this_container(monkeypatch)
    with pytest.raises(_ce.ContainerImageMismatch) as caught:
        _ce.docker_exec_argv(_CTN, "true")
    said = str(caught.value)
    assert _pin.CONTAINER_IMAGE_MISMATCH in said
    assert _CTN in said and _OTHER in said and _pin.IMAGE_DIGEST in said
    # …and a container whose digest cannot be READ is NOT a mismatch.
    assert _ce.docker_exec_argv("some-other-name", "true")[:3] == \
        ["docker", "exec", "some-other-name"]


# ═══ SITE A — analog_a6_native_pv ══════════════════════════════════════════

def _a6_project(tmp: pathlib.Path, block: str = "u_ldo") -> pathlib.Path:
    ad = tmp / "phase3" / "analog"
    (ad / block).mkdir(parents=True)
    (ad / "analog_block_list.json").write_text(
        json.dumps({"blocks": [{"name": block, "type": "ldo"}]}))
    (ad / block / f"{block}.gds").write_bytes(b"\x00GDSII-fake\x00" * 4)
    (ad / block / f"{block}.sp").write_text(
        f".subckt {block} vdd vss vin vout\nr1 vin vout 1k\n.ends\n")
    return tmp


def _res():
    return {"available": True, "source": "project_custom_pdk",
            "family": "foundry", "target": "MyFoundry X180",
            "drc_deck": "/pdk/calibre/foundry_DRC.rule",
            "lvs_deck": "/pdk/calibre/foundry_LVS.rule"}


def test_a6_a_mismatch_is_a_named_NOT_MEASURED_and_not_an_escape(monkeypatch):
    """The producer finishes, and the record says WHICH container and WHICH
    digest. Pre-fix this raised out of `run_block_pv` entirely."""
    _refuse_this_container(monkeypatch)
    with tempfile.TemporaryDirectory() as td:
        p = _a6_project(pathlib.Path(td))
        st = PV.run_block_pv(
            p, "u_ldo", _res(), _CTN,
            drc_runner=lambda *a: (0, {"rules_pass": 10}),
            lvs_runner=lambda *a: ("MATCH", {}))
        assert st["ran"] is True
        rec = st["drc"]["second_engine"]
    assert rec["result"] == PV.SECOND_ENGINE_NOT_MEASURED
    assert rec["refusal"] == _pin.CONTAINER_IMAGE_MISMATCH
    assert rec["container"] == _CTN
    assert rec["required_digest"] == _pin.IMAGE_DIGEST
    # BY NAME: the reader is told what was reached and what was wanted.
    assert _OTHER in rec["refusal_detail"] and _CTN in rec["refusal_detail"]
    # …and a refusal is NOT counted as zero violations of the second engine.
    assert "violations" not in rec


def test_a6_an_absent_container_is_NOT_MEASURED_without_a_refusal(monkeypatch):
    """THE CONTROL that keeps the two apart. An engine that could not run for
    an ordinary reason is still NOT_MEASURED — and it carries no `refusal`,
    because nothing was measured to disagree with."""
    monkeypatch.setattr(_pin, "container_image_digest",
                        lambda c: (None, f"{_pin.CONTAINER_ABSENT}: {c}"))
    got = PV.second_engine_drc(
        pathlib.Path("/nonexistent"), "blk", _CTN, "",
        runner=lambda p, b, c: (None, "the second engine did not run: nope"))
    assert got["result"] == PV.SECOND_ENGINE_NOT_MEASURED
    assert "refusal" not in got
    assert "nope" in got["reason"]


def test_a6_the_runners_own_reason_reaches_the_record():
    """It used to be dropped: `attribution, why = run(...)` and then
    `return None`, so every different failure read as one fixed sentence."""
    got = PV.second_engine_drc(
        pathlib.Path("/nonexistent"), "blk", _CTN, "",
        runner=lambda p, b, c: (None, "the attribution program wrote no report"))
    assert got["reason"] == "the attribution program wrote no report"


def test_a6_a_graded_run_is_untouched():
    """The paired control: naming the refusal must not change what a run that
    DID grade reports, or the fix would be paid for in the case it is for."""
    lyrdb = ("<report><categories><category><name>M2.b</name></category>"
             "</categories></report>")
    attribution = {"result": "LAYOUT_OWNS",
                   "by_class_and_rule": {"LAYOUT": {"a rule (MIM.i)": 8}}}
    got = PV.second_engine_drc(pathlib.Path("/nonexistent"), "blk", _CTN,
                               lyrdb, runner=lambda p, b, c: (attribution, ""))
    assert got["violations"] == 8
    assert got.get("result") != PV.SECOND_ENGINE_NOT_MEASURED


# ═══ SITE B — analog_a3_netlist_emit ═══════════════════════════════════════

def test_a3_a_mismatch_has_its_own_status_and_is_not_absence(monkeypatch):
    """`NOT_VERIFIED_NO_SIMULATOR` says the binary is not there and sends the
    reader to start a container. A container that IS there and is the wrong
    image is a different sentence and a different fix."""
    _refuse_this_container(monkeypatch)
    monkeypatch.setattr(A3.shutil, "which", lambda _n: "/usr/bin/docker")
    got = A3.verify_with_ngspice(_CTN, "blk", "* netlist\n", "* tb\n")
    assert got["simulation_verified"] is False
    assert got["simulation_status"] == "NOT_VERIFIED_CONTAINER_IMAGE_MISMATCH"
    assert got["container"] == _CTN
    assert got["required_digest"] == _pin.IMAGE_DIGEST
    assert _OTHER in got["detail"] and _CTN in got["detail"]
    assert "is not reachable" not in got["detail"], (
        "a refused container was reported as an unreachable one — that is the "
        "absence sentence, and it is the wrong fix to hand a reader")


def test_a3_an_unreadable_container_is_still_reported_as_unreachable(monkeypatch):
    """THE CONTROL. A digest that could not be READ is not a mismatch, so the
    ordinary capability sentence still stands and a locally-built container is
    not locked out."""
    monkeypatch.setattr(_pin, "container_image_digest",
                        lambda c: (None, f"{_pin.CONTAINER_ABSENT}: {c}"))
    monkeypatch.setattr(A3.shutil, "which", lambda _n: "/usr/bin/docker")
    monkeypatch.setattr(A3, "_docker_ok", lambda _c: False)
    got = A3.verify_with_ngspice(_CTN, "blk", "* netlist\n", "* tb\n")
    assert got["simulation_status"] == "NOT_VERIFIED_NO_SIMULATOR"
    assert "is not reachable" in got["detail"]


def test_a3_docker_ok_answers_false_instead_of_raising(monkeypatch):
    """The probe itself must not throw. It is called from a `not …` expression
    with nothing around it, so a raise there is an escape all the way out."""
    _refuse_this_container(monkeypatch)
    assert A3._docker_ok(_CTN) is False


# ═══ SITE C — phase3_one_shot_runner ═══════════════════════════════════════

def test_p3_a_refused_read_says_so_and_is_not_an_absent_file(monkeypatch):
    """"Could not read it" and "read it and it was empty" must not be the same
    answer. The primitive carries the reason; the wrapper says it out loud."""
    _refuse_this_container(monkeypatch)
    text, why = P3.read_text_or_container_cat("/no/such/path.lib", _CTN)
    assert text is None
    assert why.startswith(P3.READ_REFUSED)
    assert _CTN in why and _OTHER in why and _pin.IMAGE_DIGEST in why


def test_p3_the_wrapper_reports_the_refusal_on_stderr(monkeypatch, capsys):
    _refuse_this_container(monkeypatch)
    got = P3._v1_6_604_read_text_or_container_cat("/no/such/path.lib", _CTN)
    assert got is None
    err = capsys.readouterr().err
    assert "[NOT_MEASURED]" in err and P3.READ_REFUSED in err and _CTN in err


def test_p3_an_ordinary_absence_stays_quiet_and_says_why(monkeypatch, capsys):
    """THE CONTROL. A file that is simply not there is not a refusal: it must
    not be announced, and it must still carry a reason for a caller that asks
    the primitive."""
    got = P3._v1_6_604_read_text_or_container_cat("/no/such/path.lib", "")
    assert got is None
    assert capsys.readouterr().err == ""
    _text, why = P3.read_text_or_container_cat("/no/such/path.lib", "")
    assert not why.startswith(P3.READ_REFUSED)
    assert "no container was named" in why


def test_p3_a_docker_that_will_not_run_is_a_refusal_not_an_absent_file(
        monkeypatch):
    """"I could not look" is not "I looked and it was not there"."""
    monkeypatch.setattr(_pin, "container_image_digest",
                        lambda c: (None, f"{_pin.CONTAINER_ABSENT}: {c}"))
    monkeypatch.setattr(P3.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(
                            OSError("docker: no such file")))
    text, why = P3.read_text_or_container_cat("/no/such/path.lib", _CTN)
    assert text is None and why.startswith(P3.READ_REFUSED)
    assert "could not be run" in why


def test_p3_an_unexpected_exception_is_named_and_never_escapes(monkeypatch):
    """A runner must not be taken down by this read, and the reason must not be
    discarded either — the blanket `except Exception: pass` did both."""
    monkeypatch.setattr(_pin, "container_image_digest",
                        lambda c: (None, f"{_pin.CONTAINER_ABSENT}: {c}"))

    class _Odd(Exception):
        pass

    monkeypatch.setattr(P3.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(_Odd("boom")))
    text, why = P3.read_text_or_container_cat("/no/such/path.lib", _CTN)
    assert text is None
    assert "_Odd" in why and "boom" in why


def test_p3_a_successful_read_is_unchanged(monkeypatch, tmp_path):
    """The paired control on the happy path, host side and container side."""
    f = tmp_path / "x.lib"
    f.write_text("cell (INV) {}\n")
    assert P3._v1_6_604_read_text_or_container_cat(str(f), _CTN) == \
        "cell (INV) {}\n"

    class _CP:
        returncode = 0
        stdout = "from the container\n"
        stderr = ""

    monkeypatch.setattr(_pin, "container_image_digest",
                        lambda c: (None, f"{_pin.CONTAINER_ABSENT}: {c}"))
    monkeypatch.setattr(P3.subprocess, "run", lambda *a, **k: _CP())
    assert P3._v1_6_604_read_text_or_container_cat("/no/such/p", _CTN) == \
        "from the container\n"


# ═══ the three sites, as a population ══════════════════════════════════════

def test_no_site_that_builds_a_guarded_argv_leaves_the_mismatch_unhandled():
    """MEMBERSHIP over the three sites this issue names, asserted on their own
    source: each one must NAME `ContainerImageMismatch` or reach it through a
    helper that does. A fourth site added tomorrow is not covered by this — that
    is what `_container_exec`'s own population tests are for — but these three
    are the ones that were measured escaping."""
    import ast
    sites = {
        "analog_a6_native_pv.py": "ContainerImageMismatch",
        "analog_a3_netlist_emit.py": "ContainerImageMismatch",
        "phase3_one_shot_runner.py": "ContainerImageMismatch",
    }
    missing = []
    for rel, token in sites.items():
        src = (_PROGRAMS / rel).read_text(encoding="utf-8")
        # CODE, not prose: a comment naming the exception handles nothing.
        tree = ast.parse(src)
        named = any(
            isinstance(n, ast.Attribute) and n.attr == token
            for n in ast.walk(tree))
        if not named:
            missing.append(rel)
    assert not missing, (
        f"these sites build a guarded docker-exec argv and name no handler for "
        f"{list(sites.values())[0]}, so a wrong-image container escapes them: "
        f"{missing}")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
