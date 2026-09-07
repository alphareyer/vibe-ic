"""tests/test_docker_exec_timeout_bytes.py — v0.2.36

Regression for the str/bytes crash that killed a long phase3 PnR run.

Root cause: on `subprocess.TimeoutExpired`, the partial captured
`stdout`/`stderr` can come back as BYTES even though `text=True` was
requested (the streams are killed mid-decode). The old `_docker_exec`
returned that bytes value verbatim, which then poisoned every
downstream `out + err` string concat — notably
`step_pnr` → `_extract_overutil_pct(out + err)` →
`TypeError: can't concat str to bytes`. That TypeError crashed the
runner AFTER OpenROAD had already launched a multi-hour route, so the
process died with no `routed.def`.

Fix: `_docker_exec` now decodes any bytes partial → str so all callers
always receive `str`. Chip-AGNOSTIC pure I/O-type hygiene."""
from __future__ import annotations

import subprocess
from unittest.mock import patch

from programs.phase3_one_shot_runner import (
    _docker_exec, _extract_overutil_pct,
)


def test_docker_exec_timeout_bytes_stdout_returns_str() -> None:
    """TimeoutExpired with a BYTES stdout partial must still return a
    `str` for both out and err so `out + err` never raises."""
    exc = subprocess.TimeoutExpired(cmd="x", timeout=1)
    exc.stdout = b"partial route output \xff captured before kill"
    exc.stderr = None
    with patch("subprocess.run", side_effect=exc):
        rc, out, err = _docker_exec("vibeic-eda", "openroad ...", timeout=1)
    assert rc == 124
    assert isinstance(out, str)
    assert isinstance(err, str)
    # the exact poison the bug tripped on:
    _ = out + err  # must not raise TypeError
    assert "partial route output" in out
    assert "TIMEOUT after 1s" in err


def test_docker_exec_normal_bytes_streams_decoded() -> None:
    """Even on the success path, a bytes stdout/stderr (text=False
    fallback) is decoded to str.

    THE STUB ANSWERS THE ARGV IT IS ASKED (vibe-ic#2106). `_docker_exec` no
    longer reaches `subprocess.run` first: `_exec_argv` now builds the argv
    through `_container_exec.docker_exec_argv`, which asks `_eda_pin` whether
    the container is running the pinned bytes, and that probe is a
    `docker inspect` of its own. A blanket `return_value=` handed THAT caller
    a bytes stdout it can never receive in life — it asks with `text=True`,
    where CPython decodes for it — and the probe died with
    `TypeError: a bytes-like object is required, not 'str'` at
    `_eda_pin.container_image_digest`, before the subject under test ran at
    all. Red on pristine main in both env arms.

    So the stub distinguishes the two callers by their argv, and each gets an
    answer its real caller could actually receive:

      * `docker exec …` — THE SUBJECT. Bytes on a `text=True` call, which is
        exactly the condition this test exists for and the one CPython really
        produces when a stream is killed mid-decode.
      * anything else — the pin probe. `rc=1` with STR streams is docker's own
        "no such container", which `container_pin_state` classifies UNREADABLE.
        Deliberately not a MISMATCH: `docker_exec_argv` RAISES on a measured
        mismatch, so a stub that faked a wrong image would replace this test's
        subject with the refusal path and never decode anything.
    """
    class _ExecCP:
        returncode = 0
        stdout = b"ok-bytes"
        stderr = bytearray(b"warn-bytes")

    class _NoSuchContainerCP:
        returncode = 1
        stdout = ""
        stderr = "Error: No such object: vibeic-eda"

    def _answer(argv, *_a, **_kw):
        return _ExecCP() if "exec" in list(argv) else _NoSuchContainerCP()

    with patch("subprocess.run", side_effect=_answer):
        rc, out, err = _docker_exec("vibeic-eda", "echo ok")
    assert (rc, isinstance(out, str), isinstance(err, str)) == (0, True, True)
    assert out == "ok-bytes"
    assert err == "warn-bytes"


def test_docker_exec_none_streams_become_empty_str() -> None:
    exc = subprocess.TimeoutExpired(cmd="x", timeout=2)
    exc.stdout = None
    with patch("subprocess.run", side_effect=exc):
        rc, out, err = _docker_exec("vibeic-eda", "x", timeout=2)
    assert out == ""
    assert isinstance(err, str)


def test_overutil_extract_consumes_docker_exec_timeout_output() -> None:
    """End-to-end: the exact call site that crashed
    (`_extract_overutil_pct(out + err)`) now runs cleanly when the
    timeout handed back a bytes partial."""
    exc = subprocess.TimeoutExpired(cmd="x", timeout=1)
    exc.stdout = b"[INFO DRT-0195] Start 2nd optimization iteration."
    with patch("subprocess.run", side_effect=exc):
        _, out, err = _docker_exec("vibeic-eda", "openroad ...", timeout=1)
    # no GPL-0301 in this output → None, but crucially: NO TypeError.
    assert _extract_overutil_pct(out + err) is None
