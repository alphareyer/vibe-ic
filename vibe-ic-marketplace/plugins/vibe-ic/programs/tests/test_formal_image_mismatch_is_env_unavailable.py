"""A refused container attach is an ENV_UNAVAILABLE formal run, not a traceback.

MEASURED on 8HD-6 (192.168.1.108, 2026-09-29, MAINRED_901 red 2): the
`vibeic-eda` container was running vibeic-eda:0.3.85, not the pinned runtime
digest. `_container_exec.docker_exec_argv` refused to attach
(`ContainerImageMismatch`), the exception escaped `formal_property_run` as an
uncaught traceback (rc 1), and NO `formal_env_unavailable.json` was written --
the one artefact that tells the reader the engine was never reached and what
to do about it. An absent container already produced that manifest; a
mismatched one now produces it too.

Only the attach refusal is faked (the container's digest is a property of the
host); the program runs its real code path.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import _container_exec as CE  # noqa: E402
import formal_property_run as F  # noqa: E402

_REFUSAL = ("CONTAINER_IMAGE_MISMATCH: container eda_probe runs sha256:" + "a" * 64
            + ", but the pinned runtime is registry/vibeic-eda@sha256:" + "b" * 64)

_RTL = "module ctr(input clk, output reg q); always @(posedge clk) q <= ~q; endmodule\n"
_HARNESS = ("module formal_ctr(input clk); wire q; ctr dut(.clk(clk), .q(q));\n"
            "always @(posedge clk) assert (q == q); endmodule\n")


def _refuse(container, *rest, opts=()):
    raise CE.ContainerImageMismatch(_REFUSAL)


def _run(tmp_path, monkeypatch):
    monkeypatch.setattr(CE, "docker_exec_argv", _refuse)
    (tmp_path / "rtl").mkdir()
    (tmp_path / "rtl" / "ctr.v").write_text(_RTL)
    (tmp_path / "formal_ctr.sv").write_text(_HARNESS)
    rc = F.main([str(tmp_path), "--harness", str(tmp_path / "formal_ctr.sv"),
                 "--rtl", str(tmp_path / "rtl" / "ctr.v"),
                 "--top", "formal_ctr", "--container", "eda_probe"])
    return rc, tmp_path / "phase2" / "stage1" / "formal"


def test_a_refused_attach_writes_the_env_unavailable_manifest(tmp_path, monkeypatch):
    rc, fd = _run(tmp_path, monkeypatch)
    assert rc == F.RC_ENV_UNAVAILABLE, rc
    data = json.loads((fd / "formal_env_unavailable.json").read_text())
    assert data["verdict"] == "ENV_UNAVAILABLE"
    assert data["all_proved"] is False and data["properties"] == []
    gap = data["env_gap"]
    assert gap["missing_capability"] == "the pinned EDA image"
    assert "CONTAINER_IMAGE_MISMATCH" in gap["tool_message"]
    assert "eda_probe" in gap["remedy"] and "pinned" in gap["remedy"]
    # nothing that looks like a proof, and no engine claimed present
    assert not (fd / "results.json").exists()
    avail = data["engine_availability"]
    assert avail["_env_reachable"] is False
    assert not any(v for k, v in avail.items() if k != "_env_reachable")
