"""llv1 W5: step 37's seal-ring origin refusal is a fact about the IMAGE.

It used to refuse every die off (0,0), because LibreLane's SealRing once took
x1/y1 as width/height. The released 0.3.83 image sizes the ring from the
spans, moves it onto DIE_AREA and fails the step unless a ring encloses the
die; 0.3.79 does none of that (both measured, LLV1_W5.md). The refusal now
fires only when the resolved image lacks that capability, read at run time.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import librelane_step37 as S  # noqa: E402
from librelane_contract import Refusal  # noqa: E402


class Reached(Exception):
    """The run got past the seal-ring decision."""


def _run(tmp_path, monkeypatch, die, capable):
    probes = []
    cfg = tmp_path / "KLayout.SealRing.json"
    cfg.write_text(json.dumps({"DIE_AREA": die}))
    monkeypatch.setattr(S, "resolve_step_configs",
                        lambda *a, **k: {"KLayout.SealRing": cfg})

    def probe(image):
        probes.append(image)
        return capable

    monkeypatch.setattr(S, "sealring_origin_supported", probe, raising=False)

    def past(project):
        raise Reached()

    monkeypatch.setattr(S, "declaration_config", past)
    import librelane_pv_signoff
    monkeypatch.setattr(librelane_pv_signoff, "tech_lef_overlay", lambda p: None)
    try:
        S.run(tmp_path, "img", tmp_path, "processA", tmp_path / "r.def",
              tmp_path / "n.v", tmp_path / "c.sdc", tmp_path / "out.gds")
    except Reached:
        return "reached", probes
    return "returned", probes


def test_a_die_at_the_origin_never_asks_the_image(tmp_path, monkeypatch):
    got, probes = _run(tmp_path, monkeypatch, [0, 0, 100, 100], capable=False)
    assert got == "reached" and probes == []


def test_a_die_off_the_origin_refuses_on_an_incapable_image(tmp_path, monkeypatch):
    with pytest.raises(Refusal) as exc:
        _run(tmp_path, monkeypatch, [10, 10, 90, 90], capable=False)
    assert exc.value.code == "LL_SEALRING_ORIGIN_UNSUPPORTED"
    assert "img" in str(exc.value)


def test_a_die_off_the_origin_proceeds_on_a_capable_image(tmp_path, monkeypatch):
    got, probes = _run(tmp_path, monkeypatch, [10, 10, 90, 90], capable=True)
    assert got == "reached" and probes == ["img"]


@pytest.mark.parametrize("rc,out,expect", [(0, "True\n", True), (0, "False\n", False),
                                            (1, "True\n", False), (0, "", False)])
def test_the_probe_reads_the_image_answer_and_fails_closed(monkeypatch, rc, out, expect):
    calls = []

    def fake(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, rc, out, "")

    monkeypatch.setattr(S.subprocess, "run", fake)
    assert S.sealring_origin_supported("img") is expect
    argv = calls[0]
    assert argv[:3] == ["docker", "run", "--rm"] and "--network" in argv
    assert argv[3:3 + len(S._dmem.docker_memory_flags())] == S._dmem.docker_memory_flags()
    assert argv[argv.index("-c") + 1] == S._SEALRING_ORIGIN_PROBE
