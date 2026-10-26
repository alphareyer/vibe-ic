"""Issue #3354: proof-bounded removal of pure unread local sinks."""
import hashlib
import os
import shutil
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import rtl_hygiene_lint as H  # noqa: E402


def source(*, declaration="  logic sink;", assignment="  assign sink = ^source;",
           result="  assign result = source[0];", wrapper=""):
    body = "\n".join(part for part in (declaration, assignment, result) if part)
    return ("module dut(input [3:0] source, output result);\n"
            + wrapper.replace("BODY", body)
            + (body if not wrapper else "")
            + "\nendmodule\n")


def native(path, *, reader=False, port=False):
    cells = {
        "$reduce": {
            "port_directions": {"A": "input", "Y": "output"},
            "connections": {"A": [2, 3, 4, 5], "Y": [10]},
        }
    }
    if reader:
        cells["$reader"] = {
            "port_directions": {"A": "input", "Y": "output"},
            "connections": {"A": [10], "Y": [11]},
        }
    return {
        "_lint_input_sha256": {
            str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest(),
        },
        "modules": {
            "dut": {
                "ports": ({"sink": {"direction": "output", "bits": [10]}}
                          if port else {}),
                "netnames": {"sink": {"bits": [10]}},
                "cells": cells,
            }
        },
    }


def test_pure_local_sink_is_removed_and_idempotent(tmp_path):
    path = tmp_path / "dut.v"
    path.write_text(source())
    count, names = H.autofix_unread_local_sink(path, native(path), "dut")
    assert (count, names) == (1, ["sink"])
    assert path.read_text() == source(declaration="", assignment="")
    assert H.autofix_unread_local_sink(path, native(path), "dut") == (0, [])


def test_intervening_valid_edit_after_admission_is_preserved(tmp_path, monkeypatch):
    """Exact independent-review interleaving: admit A, then a peer writes B."""
    path = tmp_path / "dut.v"
    original = source()
    concurrent = original.replace(
        "assign result = source[0];",
        "assign result = source[1]; // concurrent valid edit",
    )
    path.write_text(original)
    design = native(path)
    real_pure = H._pure_unread_sink_rhs
    injected = {"done": False}

    def inject_after_admission(rhs):
        answer = real_pure(rhs)
        if not injected["done"]:
            path.write_text(concurrent)
            injected["done"] = True
        return answer

    monkeypatch.setattr(H, "_pure_unread_sink_rhs", inject_after_admission)
    assert H.autofix_unread_local_sink(path, design, "dut") == (0, [])
    assert injected["done"]
    assert path.read_text() == concurrent


def test_cooperating_source_transaction_refuses_a_second_writer(tmp_path):
    path = tmp_path / "dut.v"
    path.write_text(source())
    design = native(path)
    held = H._acquire_source_edit_lock(path)
    assert held is not None
    try:
        assert H.autofix_unread_local_sink(path, design, "dut") == (0, [])
        assert path.read_text() == source()
    finally:
        H._release_source_edit_lock(held)


def test_failed_compile_never_rolls_back_over_intervening_bytes(
        tmp_path, monkeypatch, capsys):
    path = tmp_path / "dut.v"
    original = ("module dut(input logic clk, output logic q);\n"
                "  always_ff @(posedge clk) q <= ~q;\n"
                "endmodule\n")
    path.write_text(original)
    monkeypatch.setattr(shutil, "which", lambda name: "/fake/iverilog")

    def repair(p):
        p.write_text(p.read_text() + "// candidate repair\n")
        return 1, ["q"]

    calls = {"count": 0}

    def compile_then_peer_write(*_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return SimpleNamespace(returncode=0)
        path.write_text(path.read_text() + "// concurrent valid edit\n")
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(H, "autofix_uninit_registered_output", repair)
    monkeypatch.setattr(H._pr, "run", compile_then_peer_write)
    argv = sys.argv
    sys.argv = ["rtl_hygiene_lint.py", "--fix", str(path)]
    try:
        assert H.main() == 2
    finally:
        sys.argv = argv
    assert path.read_text().endswith(
        "// candidate repair\n// concurrent valid edit\n")
    assert "fix-rollback-refused-source-changed" in capsys.readouterr().err


def test_native_reader_mutation_refuses_byte_identically(tmp_path):
    path = tmp_path / "dut.v"
    path.write_text(source())
    before = path.read_bytes()
    assert H.autofix_unread_local_sink(path, native(path, reader=True), "dut") == (0, [])
    assert path.read_bytes() == before


@pytest.mark.parametrize("kind", [
    "read-target", "port", "side-effect", "ambiguous-scope", "attribute",
    "multiple-declarators", "multiple-declarations", "multiple-writers",
])
def test_unsupported_or_observable_sink_is_byte_preserved(tmp_path, kind):
    text = source()
    if kind == "read-target":
        text = source(result="  assign result = sink;")
    elif kind == "port":
        text = text.replace("output result);", "output result, output logic sink);").replace(
            "  logic sink;\n", "")
    elif kind == "side-effect":
        text = source(assignment="  assign sink = observe(source);").replace(
            "endmodule", "  function observe(input [3:0] value); observe = ^value; endfunction\nendmodule")
    elif kind == "ambiguous-scope":
        text = source(wrapper="  generate if (1) begin : g\nBODY\n  end endgenerate\n")
    elif kind == "attribute":
        text = source(declaration="  (* keep = 1 *) logic sink;")
    elif kind == "multiple-declarators":
        text = source(declaration="  logic sink, other;")
    elif kind == "multiple-declarations":
        text = source(declaration="  logic sink;\n  logic sink;")
    elif kind == "multiple-writers":
        text = source(assignment="  assign sink = ^source;\n  assign sink = &source;")
    path = tmp_path / "dut.v"
    path.write_text(text)
    before = path.read_bytes()
    design = native(path, port=(kind == "port"), reader=(kind == "read-target"))
    assert H.autofix_unread_local_sink(path, design, "dut") == (0, [])
    assert path.read_bytes() == before


def test_ordinary_fix_uses_real_native_census_when_tools_exist(tmp_path):
    if not shutil.which("yosys") or not shutil.which("iverilog"):
        pytest.skip("native Yosys and Icarus are required for this consumer control")
    path = tmp_path / "dut.v"
    path.write_text(source())
    result = subprocess.run(
        [sys.executable, str(PROGRAMS / "rtl_hygiene_lint.py"), "--fix", str(path)],
        cwd=tmp_path, env=dict(os.environ), capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "removed 1 proven pure unread local sink(s): ['sink']" in result.stdout
    assert path.read_text() == source(declaration="", assignment="")
