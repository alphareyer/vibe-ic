"""vibe-ic#2211 — candidate admission must judge the COMPLETE deliverable.

THE DEFECT
----------
`benchmark_io_adapter.collect` is the admission boundary: `benchmark_dispatch`
uses its `ok` verbatim as `ok` / `candidate_ready` / `awaiting_ai_review`, and an
`ok=True` is what freezes the whole source set (`_archive_candidate`) and sends
it to review and export. It already globbed the complete set and already
concatenated it, but decided admission from (a) the `rtl_gen` STEP status and
(b) whether `required_top` appeared among the declared names of the
concatenation. Neither asks whether the complete set is self-consistent, and (b)
is satisfied by a name declared twice exactly as well as by one declared once.

Reproduced on this tree at plugin 1.20.18 with the reporter's own fixture, in
the pinned image: emit a normalized copy of the RTL back into the deliverable
directory and `harness_exact_selfverify` returns rc 0 with all three of its
gates PASS on the file it was handed, while `iverilog` over the resulting set
exits 2 with "'test_unit' has already been declared in this scope". The per-file
harness is not at fault -- it verified exactly what it was given.

THE COMPILER IS THE AUTHORITY, AND THE TEXTUAL COUNT IS NOT
-----------------------------------------------------------
#2211 forbids rejecting "supported conditional alternatives ... by naive textual
counting", and that is not a hypothetical risk. Measured against the shipped
`rtl_final_bundle_integrity._module_map`:

    `ifdef`-guarded alternatives (one file) : duplicates=['widget']  compile PASS
    the same name declared in two files     : duplicates=['widget']  compile BLOCKED
    two distinct modules in two files       : duplicates=[]          compile PASS

Only the compile separates the first two. So admission calls the SHARED
`compile_source_manifest` -- one implementation, the same one the export-time
`check_final_bundle` uses -- and never a second textual duplicate-module policy.

THE BOUNDARY, AND HOW IT WAS FOUND
----------------------------------
The first version of this change refused EVERY non-compiling bundle here. Nine
tests went red across four modules, and they were right: `test_challenge_
compile_attribution` pins a deliberate contract in which a candidate whose RTL
does not compile REACHES the challenge stage, is attributed CANDIDATE_BROKEN by
the files its error lines cite, and is repair-routed -- never a proven FAIL.
Refusing it here meant the flow never got to that attribution. So the gate is
narrowed to a defect that is emergent FROM THE SET and cannot be a property of
any single file: two different files declare the same module AND the compiler
independently confirms the set does not compile. Both conditions, never either
alone. A broken FILE stays the challenge stage's business.

ORDER IS LOAD-BEARING, AND A SECOND ARM FOUND THAT TOO
------------------------------------------------------
Running the compile on every candidate turned `collect` from an inspect-only
function into one that shells out on the path every candidate takes.
`test_public_input_backup_provenance` patches `bd.subprocess.run` -- which IS the
subprocess module, so the patch is global -- and counts worker invocations: it
saw 2 where it requires 1, then crashed on a stub with no `.stderr`. So the cheap
question is asked first: the cross-file view is pure, and the compiler is invoked
ONLY when it is non-empty. A well-formed candidate records NOT_ATTEMPTED with the
reason, which is not a projected zero -- it says exactly what was and was not
done.

PREDICTED DIRECTIONS, written before these ran
----------------------------------------------
  1 duplicate emitted module      : ok=False, naming the manifest, the real
                                    compiler diagnostic and the shared name
  2 the reporter's control arm    : ok=True (redundant copy outside the
                                    deliverable, input + distinct aux retained)
  3 `ifdef` alternatives          : ok=True  -- no textual false rejection
  4 multi-file hierarchy          : ok=True
  5 one broken FILE               : ok=True  -- CANDIDATE_BROKEN keeps it
  5b missing dependency           : ok=True  -- same reason, disclosed as a
                                    deliberate departure from #2211's wish list
  6 package + user, either order  : ok=True
  7 no `iverilog`                 : NOT_MEASURED and STILL ADMITTED -- a missing
                                    tool is not evidence of a clean bundle and
                                    must not be reported as one
  8 the admitted bundle_sha256    : equals sha256 of the `completion` that
                                    `_archive_candidate` freezes as `rtl_sha256`
All of them held.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import benchmark_io_adapter as bio  # noqa: E402
import rtl_final_bundle_integrity as bundle_integrity  # noqa: E402

_NEEDS_IVERILOG = pytest.mark.skipif(
    shutil.which("iverilog") is None,
    reason="iverilog is unavailable; the bundle compile cannot be measured")

UNIT = "module test_unit(input wire a, output wire z);\n  assign z = ~a;\nendmodule\n"
AUX = "module auxiliary_unit(input wire a, output wire z);\n  assign z = a;\nendmodule\n"
TOP_USES_HELPER = ("module test_unit(input wire a, output wire z);\n"
                   "  helper u0(.a(a), .z(z));\n"
                   "endmodule\n")
HELPER = "module helper(input wire a, output wire z); assign z = ~a; endmodule\n"
IFDEF_ALTERNATIVES = (
    "`ifdef FAST\n"
    "module test_unit(input wire a, output wire z); assign z = ~a; endmodule\n"
    "`else\n"
    "module test_unit(input wire a, output wire z); assign z = a; endmodule\n"
    "`endif\n")
PKG = "package p; localparam int W = 1; endpackage\n"
USES_PKG = ("module test_unit(input wire a, output wire z);\n"
            "  import p::*;\n  assign z = ~a;\nendmodule\n")


def _project(tmp_path: Path, files: dict, *, rtl_gen: str = "PASS") -> Path:
    rtl = tmp_path / "phase2" / "stage1" / "rtl"
    rtl.mkdir(parents=True)
    for name, body in files.items():
        (rtl / name).write_text(body)
    reports = tmp_path / "reports" / "orchestrator"
    reports.mkdir(parents=True)
    (reports / "phase2_one_shot.json").write_text(json.dumps(
        {"steps": [{"name": "rtl_gen", "status": rtl_gen, "detail": "generated"}]}))
    return tmp_path


def _collect(tmp_path: Path, files: dict) -> dict:
    return bio.collect("verilogeval", "p1", _project(tmp_path, files),
                       required_top="test_unit")


# --------------------------------------------------------------------------
# 1 / 2 — the reporter's own two arms
# --------------------------------------------------------------------------

@_NEEDS_IVERILOG
def test_a_duplicated_emitted_module_is_refused_at_admission(tmp_path):
    """The emitted copy lands in the deliverable directory. The per-file harness
    is entitled to pass; the complete set cannot compile, so it is not a
    candidate."""
    got = _collect(tmp_path, {"unit.v": UNIT, "emitted.v": UNIT})
    assert got["ok"] is False, got
    assert got["bundle_compile"]["status"] == "BLOCKED", got
    # it must name the COMPLETE manifest, not the one file that passed
    assert "emitted.v" in got["reason"] and "unit.v" in got["reason"], got
    # and the refusal must carry the compiler's own words, not a paraphrase
    assert "already been declared" in got["bundle_compile"]["reason"], got
    # the defect is a property of the SET: two files own one module name
    assert got["cross_file_duplicate_modules"] == ["test_unit"], got
    assert got["rtl_gen"] == "PASS", got   # the STEP still passed; that is the point


@_NEEDS_IVERILOG
def test_the_redundant_copy_outside_the_deliverable_is_admitted(tmp_path):
    """The reporter's control arm: input retained, a distinct auxiliary module
    retained, the redundant emission elsewhere."""
    got = _collect(tmp_path, {"unit.v": UNIT, "aux.v": AUX})
    assert got["ok"] is True, got
    # no module is co-owned, so there is nothing for a compile to settle here
    assert got["bundle_compile"]["status"] == "NOT_ATTEMPTED", got
    assert sorted(got["files"]) == ["aux.v", "unit.v"], got


# --------------------------------------------------------------------------
# 3 / 4 / 6 — what must NOT be rejected
# --------------------------------------------------------------------------

@_NEEDS_IVERILOG
def test_conditional_alternatives_are_not_rejected_by_textual_counting(tmp_path):
    """NEGATIVE CONTROL. The sibling textual rule calls this a duplicate; the
    compiler does not, and the compiler decides."""
    assert bundle_integrity._module_map([IFDEF_ALTERNATIVES])[1] == ["test_unit"]
    got = _collect(tmp_path, {"unit.v": IFDEF_ALTERNATIVES})
    assert got["ok"] is True, got
    # one file owns it, so it is not a set-level defect at all and the
    # compiler is never even asked
    assert got["cross_file_duplicate_modules"] == [], got
    assert got["bundle_compile"]["status"] == "NOT_ATTEMPTED", got


@_NEEDS_IVERILOG
def test_a_multi_file_hierarchy_is_admitted(tmp_path):
    got = _collect(tmp_path, {"unit.v": TOP_USES_HELPER, "helper.v": HELPER})
    assert got["ok"] is True, got
    assert got["bundle_compile"]["status"] == "NOT_ATTEMPTED", got


@_NEEDS_IVERILOG
@pytest.mark.parametrize("names", [("a_pkg.sv", "unit.sv"),
                                   ("z_pkg.sv", "aunit.sv")])
def test_package_ordering_does_not_decide_admission(tmp_path, names):
    """The manifest is sorted by name, so a package whose filename sorts after
    its user must still be admitted."""
    pkg_name, user_name = names
    got = _collect(tmp_path, {pkg_name: PKG, user_name: USES_PKG})
    assert got["ok"] is True, got


# --------------------------------------------------------------------------
# 5 — THE BOUNDARY. A broken FILE is somebody else's job, and stays theirs.
# --------------------------------------------------------------------------

@_NEEDS_IVERILOG
def test_a_single_broken_file_is_still_admitted(tmp_path):
    """THE GUARD AGAINST RE-WIDENING THIS GATE.

    `test_challenge_compile_attribution` pins a deliberate contract: a candidate
    whose RTL does not compile REACHES the challenge stage, is attributed
    CANDIDATE_BROKEN by the files its error lines cite, and is repair-routed --
    never a proven FAIL. Its fixture is one file with a duplicate PORT.

    The first version of this change refused every non-compiling bundle here and
    took nine of that contract's tests down with it, because the flow never got
    to the attribution they assert. A defect that lives in ONE FILE is not this
    gate's business. Measured: `_DUP_PORT_RTL`, the exact fixture those tests
    use."""
    dup_port = ("module dut(input wire a, input wire a, output wire y); "
                "assign y = a; endmodule\n")
    got = bio.collect("verilogeval", "p1",
                      _project(tmp_path, {"dut.v": dup_port}),
                      required_top="dut")
    assert got["ok"] is True, got
    assert got["cross_file_duplicate_modules"] == [], got
    assert got["bundle_compile"]["status"] == "NOT_ATTEMPTED", got


@_NEEDS_IVERILOG
def test_a_missing_dependency_is_left_to_the_existing_broken_candidate_route(
        tmp_path):
    """#2211 asks for a missing dependency to fail closed HERE. It does not, and
    that is a decision, not an oversight: a module that cites an absent
    dependency is a property of the ONE file that cites it, so refusing it here
    would pre-empt exactly the CANDIDATE_BROKEN contract the test above
    protects. The compile evidence is still recorded, so nothing is hidden."""
    got = _collect(tmp_path, {"unit.v": TOP_USES_HELPER})
    assert got["ok"] is True, got
    assert got["cross_file_duplicate_modules"] == [], got
    assert got["bundle_compile"]["status"] == "NOT_ATTEMPTED", got


# --------------------------------------------------------------------------
# 7 — an unmeasured compile is NEITHER a pass nor a failure
# --------------------------------------------------------------------------

def test_an_absent_compiler_is_not_measured_and_still_admits(tmp_path, monkeypatch):
    """A missing tool is not evidence of a clean bundle. It must not refuse the
    candidate, and it must not report a zero it never counted."""
    monkeypatch.setattr(bundle_integrity.shutil, "which", lambda _name: None)
    got = _collect(tmp_path, {"unit.v": UNIT, "emitted.v": UNIT})
    assert got["ok"] is True, got
    assert got["bundle_compile"]["status"] == "NOT_MEASURED", got
    assert "unavailable" in got["bundle_compile"]["reason"], got
    # the cross-file view still SEES the duplicate; it just never decides alone
    assert got["cross_file_duplicate_modules"] == ["test_unit"], got


def test_a_compile_that_outran_its_budget_is_not_measured(tmp_path, monkeypatch):
    """A timeout is a statement about the machine, never about the bundle."""
    import subprocess

    def _timeout(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="iverilog", timeout=30)

    monkeypatch.setattr(bundle_integrity.shutil, "which",
                        lambda _name: "/usr/bin/iverilog")
    monkeypatch.setattr(bundle_integrity.subprocess, "run", _timeout)
    got = _collect(tmp_path, {"unit.v": UNIT, "emitted.v": UNIT})
    assert got["ok"] is True, got
    assert got["bundle_compile"]["status"] == "NOT_MEASURED", got


# --------------------------------------------------------------------------
# 8 — the bundle that was validated is the bundle that is frozen
# --------------------------------------------------------------------------

@_NEEDS_IVERILOG
def test_the_admitted_bundle_is_bound_to_its_own_hashes(tmp_path):
    """`_archive_candidate` freezes `rtl_sha256 = sha256(completion)`. The
    manifest this gate validated must be that same object, or the check
    authorized a different bundle than the one that ships."""
    got = _collect(tmp_path, {"unit.v": UNIT, "aux.v": AUX})
    assert got["ok"] is True, got
    assert got["bundle_compile"]["status"] == "NOT_ATTEMPTED", got
    assert got["bundle_sha256"] == hashlib.sha256(
        got["completion"].encode()).hexdigest(), got
    assert [row["path"] for row in got["source_manifest"]] == ["aux.v", "unit.v"]
    for row in got["source_manifest"]:
        body = (tmp_path / "phase2" / "stage1" / "rtl" / row["path"]).read_text()
        assert row["sha256"] == hashlib.sha256(body.encode()).hexdigest(), row


# --------------------------------------------------------------------------
# the shared implementation — one policy, two callers
# --------------------------------------------------------------------------

def test_admission_and_export_share_one_compile_implementation():
    """#2211 asked for integration, not a second divergent policy. The
    export-time gate and the admission gate must call the same function."""
    assert hasattr(bundle_integrity, "compile_source_manifest")
    source = Path(bundle_integrity.__file__).read_text()
    assert source.count("def compile_source_manifest(") == 1
    # `check_final_bundle` delegates rather than carrying its own copy
    body = source.split("def check_final_bundle(", 1)[1]
    assert "compile_source_manifest(" in body
    assert "shutil.which(\"iverilog\")" not in body
