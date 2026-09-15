"""R-0915-58: a run whose input declares the PDK revision is not a refusal.

MEASURED 2026-09-16 on the SPM verdict run (run32 / `spm27`, main bac74aff0),
and the measurement CONTRADICTED the premise I was given, so it is written
down here rather than argued:

  * `spm27/reports/pdk_revision.json` already reads
    `resolved: true, revision: "_tree:b344c97e…", refusal: null`.
    PDK_REVISION_NOT_RECORDED applied to run30 and earlier; I carried it
    forward into two later reports without re-measuring. Blocker 3 was
    already gone.
  * The resolver already READS the tree's own declaration. On the pinned
    image `/foss/pdks/gf180mcuD` is a symlink into
    `ciel/gf180mcu/versions/<sha40>/gf180mcuD`, and `resolve_tree` finds all
    three artefacts with digests: TREE_PATH, SOURCES_FILE
    (`open_pdks b344c97e…`) and NODE_INFO (`.config/nodeinfo.json`).
  * It ranks TREE_PATH first ON PURPOSE, and the module says why: "a
    content-addressed install path cannot be edited without moving the tree,
    so it is the one source that a stale file inside the tree cannot
    contradict silently" — with `_conflicts()` making disagreement AMBIGUOUS
    rather than silently picking. Re-ordering it to prefer SOURCES would
    REMOVE that property, so this file does not.

WHAT WAS GENUINELY MISSING, and is all this adds: a run that loaded no
self-declaring tree but SHIPS its own `input/pdk/REVISION.md` refused by name
anyway. That is the treatment icadc gave the ADC, and it is purely additive —
a LAST RESORT that only ever replaces a refusal.

IT NEVER OUTRANKS THE TREE. A file the dataset ships is the DESIGN AUTHOR's
statement about the process; the tree is what the tools actually read. When
any tree resolves, the declaration is not consulted and not recorded.

chip-AGNOSTIC: synthetic runs in tmp_path.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import pytest  # noqa: E402

import pdk_revision_resolve as P  # noqa: E402

SHA40 = "1234567890abcdef1234567890abcdef12345678"
OTHER40 = "fedcba9876543210fedcba9876543210fedcba98"


def _run_with_declaration(tmp_path, body, rel="input/pdk/REVISION.md"):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return tmp_path


def _resolved_tree(rev=SHA40):
    return {"tree": "/foss/pdks/x", "resolved_tree": "/foss/pdks/x",
            "resolved": True, "revision": f"_tree:{rev}",
            "revision_source": P.TREE_PATH, "components": {"_tree": rev},
            "sources": [], "content_anchor": None, "reason": None}


def _unresolved_tree():
    return {"tree": "/foss/pdks/x", "resolved_tree": "/foss/pdks/x",
            "resolved": False, "revision": None, "revision_source": None,
            "components": {}, "sources": [], "content_anchor": None,
            "reason": "NOT DETERMINED — this tree declares no revision."}


# ── direction 1: the input declares it, and nothing else does ────────────

@pytest.mark.parametrize("rel", ["input/pdk/REVISION.md", "input/pdk/REVISION"])
def test_a_declared_input_revision_is_read(tmp_path, rel):
    run = _run_with_declaration(tmp_path, f"open_pdks {SHA40}\n", rel)
    got = P.read_input_revision(run)
    assert got is not None
    assert got["source"] == P.INPUT_REVISION
    assert got["read_from"] == rel
    assert got["revisions"] == {"open_pdks": SHA40}
    assert got["sha256"].startswith("sha256:"), "the file is digested"


def test_it_resolves_the_record_and_clears_the_refusal(tmp_path):
    run = _run_with_declaration(tmp_path, f"open_pdks {SHA40}\n")
    rec = P.build_record([], "host", "run tool logs", "",
                         P.read_input_revision(run))
    assert rec["resolved"] is True
    assert rec["revision"] == f"open_pdks:{SHA40}"
    assert rec["revision_source"] == P.INPUT_REVISION
    assert P.record_refusal(rec) is None
    assert P.record_gaps(rec) == []
    assert "input/pdk/REVISION.md" in rec["note"]


def test_the_record_names_the_file_it_read_and_its_digest(tmp_path):
    run = _run_with_declaration(tmp_path, f"open_pdks {SHA40}\n")
    rec = P.build_record([], "host", "run tool logs", "",
                         P.read_input_revision(run))
    decl = rec["input_revision"]
    assert decl["read_from"] == "input/pdk/REVISION.md"
    assert decl["sha256"].startswith("sha256:")
    assert (run / decl["read_from"]).is_file()


def test_it_also_rescues_a_run_whose_trees_declared_nothing(tmp_path):
    """Trees were found and none of them stated a revision — the same
    refusal, and the same rescue."""
    run = _run_with_declaration(tmp_path, f"open_pdks {SHA40}\n")
    rec = P.build_record([_unresolved_tree()], "host", "run tool logs", "",
                         P.read_input_revision(run))
    assert rec["resolved"] is True
    assert rec["revision_source"] == P.INPUT_REVISION


# ── direction 2: it must never outrank the tree, and never invent ────────

def test_a_resolved_tree_wins_and_the_declaration_is_not_recorded(tmp_path):
    """THE CONTROL THAT MATTERS. The tree is what the tools READ; the input
    file is what the author SAID. A run with both takes the tree, and the
    record does not even carry the declaration."""
    run = _run_with_declaration(tmp_path, f"open_pdks {OTHER40}\n")
    rec = P.build_record([_resolved_tree()], "host", "run tool logs", "",
                         P.read_input_revision(run))
    assert rec["revision"] == f"_tree:{SHA40}"
    assert rec.get("revision_source") != P.INPUT_REVISION
    assert "input_revision" not in rec
    assert OTHER40 not in str(rec)


def test_no_declaration_and_no_tree_still_REFUSES_BY_NAME(tmp_path):
    rec = P.build_record([], "host", "run tool logs", "",
                         P.read_input_revision(tmp_path))
    assert rec["resolved"] is False
    assert P.record_refusal(rec) == P.REFUSAL_NOT_RECORDED


@pytest.mark.parametrize("body", ["", "   \n", "see the wiki\n",
                                  "open_pdks not-a-sha\n", "revision: latest\n"])
def test_a_file_that_states_no_revision_token_is_not_a_declaration(
        tmp_path, body):
    """A prose file, or a placeholder, is the REQUEST again — the same thing
    the module already refuses to accept from a config."""
    run = _run_with_declaration(tmp_path, body)
    assert P.read_input_revision(run) is None
    rec = P.build_record([], "host", "run tool logs", "",
                         P.read_input_revision(run))
    assert P.record_refusal(rec) == P.REFUSAL_NOT_RECORDED


def test_a_declaration_without_a_digest_does_not_close_the_gap():
    """FAIL-CLOSED on a hand-assembled record: the tree gap is waived only
    for a declaration that names the file AND its digest."""
    rec = P.build_record([], "host", "x", "", None)
    rec.update(resolved=True, revision=f"open_pdks:{SHA40}",
               revision_source=P.INPUT_REVISION,
               input_revision={"read_from": "input/pdk/REVISION.md"})
    assert P.record_gaps(rec) == [
        "trees: the record names no PDK tree it read"]


def test_the_tree_precedence_is_unchanged():
    """This ruling does not reorder SOURCE_ORDER. The module ranks TREE_PATH
    first deliberately — a content-addressed install path cannot be edited
    without moving the tree — and `_conflicts` makes a disagreeing file
    AMBIGUOUS rather than silently losing."""
    assert P.SOURCE_ORDER[0] == P.TREE_PATH
    assert P.SOURCES_FILE in P.SOURCE_ORDER
    assert P.NODE_INFO in P.SOURCE_ORDER
    assert P.INPUT_REVISION not in P.SOURCE_ORDER
