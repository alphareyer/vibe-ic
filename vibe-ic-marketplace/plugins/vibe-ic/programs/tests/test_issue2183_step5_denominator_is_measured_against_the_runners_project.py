"""vibe-ic#2183 — the END-TO-END half: Step 5's denominator, through the real flow.

WHAT #2183 REPORTED, and what was still unpinned when this file was written.

The issue reported a `property_contract.json` carrying

    property_denominator 3, authored_property_count 0
    missing_declarations ["L3","L6","L8"]
    each row: DECLARATION_MISSING, "<L> declaration is absent"

for a project (`spm` x `gf180mcuD`) whose L3/L6/L8 declarations were present and
readable, and named an ephemeral `<TMPDIR>/vibeic-rtl-step-XXXX/<name>` staging root
as the candidate for what Step 5 read instead.

THAT CANDIDATE CAUSE IS REFUTED, and this file does not re-argue it — three separate
measurements already did (`7245fee1f`, `2c0a2dc49`, `eabb3b207`), and it was refuted a
fourth time on `28d13f16165a` while writing this file: driving the real `step_rtl_gen`,
the stage is a FULL copy that carried every `L*.json`, and `formal_harness_gen.generate`
was handed the runner's own project root, not the stage. The published contract read
`missing_declarations []` with `declaration_root_present true`.

SO WHY A TEST AT ALL. Every existing #2183 test either drives ONE step, or calls
`declaration_obligations` DIRECTLY:

    test_..._publishes_the_canonical_root      the publish seam; never reads the contract
    test_..._denominator_names_its_root        unit calls, ROOT_ABSENT / UNREADABLE
    test_..._root_it_cannot_list_...           its readable-root CONTROL is a direct
                                               `F.declaration_obligations(project)` call

Nobody drives the JOIN the issue actually reported — `step_rtl_gen` (which builds and
commits the staging transaction) followed by `step_emit_phase2_manifests` (which authors
the formal contract) — and then reads the PUBLISHED artefact. That join is where the
reported symptom appeared, and it is the one shape a future regression in the staging
snapshot would break while every existing test stayed green.

THE TWO PROPERTIES, and why each is here rather than one:

  1. THE STAGE CARRIES THE DECLARATIONS. Generators legitimately run against the stage —
     that is the transaction's purpose. A snapshot that omitted `phase1/` would make any
     of them read exactly the negative-control shape the issue assumed, and would do it
     silently. This asserts the property whose ABSENCE the issue hypothesised.
  2. THE PUBLISHED CONTRACT NAMES A ROOT UNDER THE RUNNER'S PROJECT, and a design whose
     declarations are present does not read as missing. Asserted on the artefact a
     reader opens, not on a return value, because the artefact is what #2183 quoted.

Both are stated as the reader experiences them: a root that EXISTS and is under the
project, never a string comparison that a different dead path would still satisfy.

chip-AGNOSTIC: no design, PDK, vendor or cell literal.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import design_one_shot_runner as R          # noqa: E402
import formal_harness_gen as F              # noqa: E402

#: Minimal but REAL Phase-1 declarations: the layers #2183 named, plus the L1 the
#: resolver needs to know the top. Bodies are deliberately the smallest shape that
#: parses -- this file is about WHICH ROOT was read, not about what it contained.
_L_DOCS = {
    "L1_SPEC.json": {"fields": {"top_module": "demo_top"}},
    "L3_CMD_PROTOCOL.json": {"no_opcodes_in_input": True},
    "L6_CONTROL_LOGIC.json": {"fields": {}},
    "L8_RTL_CONSTANTS.json": {"fields": {}},
}

_CONTRACT = Path("phase2") / "stage1" / "formal" / "property_contract.json"


def _project(tmp_path: Path) -> Path:
    proj = tmp_path / "demo_proj"
    (proj / "phase1" / "generated_docs").mkdir(parents=True)
    (proj / "input").mkdir(parents=True)
    (proj / "input" / "prompt.md").write_text(
        "# demo\nA register that captures an 8-bit input on a rising edge.\n")
    for name, body in _L_DOCS.items():
        (proj / "phase1" / "generated_docs" / name).write_text(json.dumps(body))
    return proj


@pytest.fixture
def driven(tmp_path):
    """Drive the REAL join and record the stage roots the generators were handed.

    `step_rtl_gen` opens the staging transaction and commits it; only then does
    `step_emit_phase2_manifests` author the formal contract. Running them in that
    order is the point -- a fixture that called only the second would not be able
    to see a snapshot defect at all.
    """
    proj = _project(tmp_path)
    stages = []
    original = R._stage_author_knowledge_digests

    def spy(project):
        # OBSERVED WHILE THE STAGE EXISTS. `step_rtl_gen` holds the stage in a
        # `tempfile.TemporaryDirectory` and removes it on return, so a test that
        # looked afterwards would find every stage path absent and would fail for
        # a reason that has nothing to do with the snapshot. (Written that way
        # first; it failed exactly so.)
        stage = Path(project)
        docs = stage / "phase1" / "generated_docs"
        stages.append({
            "root": stage,
            "docs_dir_existed": docs.is_dir(),
            "l_docs": sorted(p.name for p in docs.glob("L*.json"))
            if docs.is_dir() else [],
        })
        return original(project)

    R._stage_author_knowledge_digests = spy
    try:
        rtl = R.step_rtl_gen(proj, "unknown_protocol_class")
    finally:
        R._stage_author_knowledge_digests = original
    emit = R.step_emit_phase2_manifests(proj, [rtl], "demo_top")
    return proj, stages, rtl, emit


# ── property 1: the stage a generator runs against carries the declarations ──

def test_the_staging_snapshot_carries_the_designs_declarations(driven):
    """The shape #2183 ASSUMED the stage had, asserted to be false.

    If a future snapshot skips `phase1/`, every generator that runs inside the
    transaction reads a project that declares nothing -- silently, and with the
    design's own declarations sitting untouched one directory up.
    """
    _proj, stages, _rtl, _emit = driven
    assert stages, "the staged generator was never reached — the fixture proves nothing"
    for seen in stages:
        assert "vibeic-rtl-step-" in str(seen["root"]), (
            f"{seen['root']} is not the staging root; this test would be vacuous")
        assert seen["docs_dir_existed"], (
            f"the staging snapshot omitted phase1/generated_docs under "
            f"{seen['root']} — a generator running there reads the exact "
            f"negative-control shape #2183 assumed")
        assert seen["l_docs"] == sorted(_L_DOCS), (
            f"the staging snapshot carried {seen['l_docs']}, the project "
            f"declares {sorted(_L_DOCS)}")


# ── property 2: the published contract measured the runner's own project ─────

def test_the_published_contract_names_a_root_under_the_runners_project(driven):
    """As the reader experiences it: the named root EXISTS and is the project's.

    #2183's own words were that the artefact "named no directory, so the run could
    not name its own mechanism and the issue had to guess at one". It names one now,
    and this pins that the one it names is the right one.
    """
    proj, _stages, _rtl, _emit = driven
    contract = json.loads((proj / _CONTRACT).read_text())
    root = contract.get("declaration_root")
    assert root, "the contract names no declaration root"
    root = Path(root)
    assert root.is_dir(), f"the contract names {root}, which does not exist"
    assert root == proj / "phase1" / "generated_docs", (
        f"the contract measured {root}, not the runner's project")
    assert "vibeic-rtl-step-" not in str(root), (
        f"the contract measured an ephemeral staging root: {root}")
    assert contract.get("declaration_root_present") is True, contract
    assert contract.get("declaration_root_error") is None, contract


def test_present_declarations_do_not_read_as_missing_through_the_real_flow(driven):
    """The reported symptom, on the artefact the run publishes.

    #2183 quoted `missing_declarations ["L3","L6","L8"]` for a project that had
    them. Whatever the denominator turns out to be for a given design, the layers
    the project DOES declare must not appear here.
    """
    proj, _stages, _rtl, _emit = driven
    contract = json.loads((proj / _CONTRACT).read_text())
    declared = {n.split("_", 1)[0] for n in _L_DOCS}
    missing = set(contract.get("missing_declarations") or [])
    assert not (missing & declared), (
        f"the run published {sorted(missing & declared)} as absent; the project "
        f"declares {sorted(declared)} and they are readable on disk")


# ── the controls, both directions ────────────────────────────────────────────

def test_CONTROL_a_root_without_declarations_still_reads_as_unread(tmp_path):
    """The negative control #2183 itself ran. Removing the declarations must
    still change the answer — otherwise the assertions above hold for a project
    whose contents nothing is reading, and they would pass on a broken flow."""
    proj = _project(tmp_path)
    for p in (proj / "phase1" / "generated_docs").glob("L*.json"):
        p.unlink()
    (proj / "phase1" / "generated_docs").rmdir()
    decl = F.declaration_obligations(proj)
    assert decl.get("declaration_root_present") is False, decl
    assert decl["obligations"] == [] or all(
        o.get("status") != "AUTHORED" for o in decl["obligations"]), decl


def test_CONTROL_the_fixture_really_drove_both_steps(driven):
    """A fixture that silently stopped reaching one of the two steps would make
    every assertion above vacuous while staying green."""
    proj, stages, rtl, emit = driven
    assert rtl.status in ("PASS", "WAIVED"), rtl.status
    assert emit.status in ("PASS", "WAIVED"), emit.status
    assert (proj / _CONTRACT).is_file(), (
        "step_emit_phase2_manifests published no property contract")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
