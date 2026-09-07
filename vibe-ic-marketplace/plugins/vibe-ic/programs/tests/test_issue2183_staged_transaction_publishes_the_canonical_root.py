"""The staging transaction remapped the StepResult and never the FILES.

#2158 found that `step_rtl_gen` snapshots the project into
`<TMPDIR>/vibeic-rtl-step-XXXX/<name>`, runs every generator against THAT root
and commits the delta back, and that the remap bringing paths home was
boundary-buggy. It repaired the boundary and applied the remap to
`result.detail`, `result.output_files` and `result.extras`.

Those three are the StepResult. They are not the files the transaction
publishes, and no remap ever reached those. So a generator that records its own
project root inside an artefact writes the scratch directory, and the commit
copies that string into the canonical tree — where it outlives the directory it
names. #2183 is that half, and it is the SAME root as #2158, not a third one:
one staging transaction, two halves, one of them covered.

MEASURED on `main` 2692e510fae5 by driving the real runner (lane cz2180, 8HD-9).
`step_rtl_gen` handed `/tmp/vibeic-rtl-step-1jyxxaff/demo_proj` to
`_stage_author_knowledge_digests` and to `spec_declaration_emit.stage_contract`,
and BOTH published artefacts came out carrying it:

    phase2/stage1/declaration_contract.json    "project": "/tmp/vibeic-rtl-step-…/demo_proj"
    phase2/stage1/lessons_scoring_record.json  "project": "/tmp/vibeic-rtl-step-…/demo_proj"

The property these tests hold to is not "the string equals the project root".
It is **the recorded root EXISTS when a reader opens the artefact** — which is
what a stale citation actually costs, and which a string comparison alone would
still satisfy if the transaction started recording some other dead path.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import design_one_shot_runner as R          # noqa: E402

_L_DOCS = {
    "L1_SPEC.json": {"fields": {"top_module": "demo_top"}},
    "L3_CMD_PROTOCOL.json": {"no_opcodes_in_input": True},
    "L6_CONTROL_LOGIC.json": {"fields": {}},
    "L8_RTL_CONSTANTS.json": {"fields": {}},
}

_PUBLISHED = (
    Path("phase2") / "stage1" / "declaration_contract.json",
    Path("phase2") / "stage1" / "lessons_scoring_record.json",
)


def _project(tmp_path: Path) -> Path:
    proj = tmp_path / "demo_proj"
    (proj / "phase1" / "generated_docs").mkdir(parents=True)
    (proj / "input").mkdir(parents=True)
    (proj / "input" / "prompt.md").write_text(
        "# demo\nA register that captures an 8-bit input on a rising edge.\n")
    for name, body in _L_DOCS.items():
        (proj / "phase1" / "generated_docs" / name).write_text(json.dumps(body))
    return proj


def _run(proj: Path):
    """Drive the REAL step, recording the root the writers were handed."""
    seen = []
    original = R._stage_author_knowledge_digests

    def spy(project):
        seen.append(str(project))
        return original(project)

    R._stage_author_knowledge_digests = spy
    try:
        result = R.step_rtl_gen(proj, "unknown_protocol_class")
    finally:
        R._stage_author_knowledge_digests = original
    return result, seen


def test_the_writers_are_still_handed_the_stage_root(tmp_path):
    """The behaviour that must NOT change. The generators keep writing into the
    isolated stage — that is the transaction's whole purpose — so a test that
    passed by making them write to the live tree would have broken the thing it
    was guarding."""
    proj = _project(tmp_path)
    _result, seen = _run(proj)
    assert seen, "the digest stager was never reached"
    assert all("vibeic-rtl-step-" in s for s in seen), seen
    assert all(not s.startswith(str(proj)) for s in seen), seen


def test_the_published_artefact_names_a_root_that_exists(tmp_path):
    """The property, stated as the reader experiences it."""
    proj = _project(tmp_path)
    _run(proj)
    found = 0
    for rel in _PUBLISHED:
        f = proj / rel
        if not f.is_file():
            continue
        found += 1
        recorded = json.loads(f.read_text()).get("project")
        assert recorded, f"{rel} records no project root"
        assert Path(recorded).is_dir(), (
            f"{rel} names {recorded!r}, which does not exist")
        assert Path(recorded) == proj
        assert "vibeic-rtl-step-" not in recorded
    assert found == len(_PUBLISHED), (
        f"only {found} of {len(_PUBLISHED)} published artefacts were written")


def test_no_published_file_carries_the_stage_root_anywhere(tmp_path):
    """MEMBERSHIP over the whole published tree, not just the two files the
    issue happened to name. A remap that fixed the named two and missed a third
    would pass the test above and fail this one."""
    proj = _project(tmp_path)
    _run(proj)
    offenders = []
    for f in sorted(proj.rglob("*")):
        if not f.is_file() or f.is_symlink():
            continue
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        if "vibeic-rtl-step-" in text:
            offenders.append(str(f.relative_to(proj)))
    assert offenders == [], offenders


def test_the_rewrite_is_disclosed_by_name(tmp_path):
    """A silent rewrite inside a held transaction is the version of this idea
    nobody could audit. The StepResult names the files it touched."""
    proj = _project(tmp_path)
    result, _seen = _run(proj)
    extras = result.extras if isinstance(result.extras, dict) else {}
    named = extras.get("stage_path_rewritten_in")
    assert named, f"nothing disclosed; extras keys = {sorted(extras)}"
    for rel in named:
        assert (proj / rel).is_file(), rel


# ── the narrowing: what the rewrite must NOT touch ──

def test_an_unchanged_file_is_published_byte_identical(tmp_path):
    """Compared by the transaction's OWN digest. A file the run did not change
    is not eligible, whatever it contains."""
    stage = tmp_path / "stage"
    (stage / "sub").mkdir(parents=True)
    keep = stage / "sub" / "untouched.json"
    body = json.dumps({"project": str(stage)}) + "\n"
    keep.write_text(body)
    baseline = {
        "sub/untouched.json": R._Phase1TreeEntry(
            "file", 0o644,
            digest=hashlib.sha256(body.encode()).hexdigest()),
    }
    rewritten = R._phase1_remap_stage_tree(stage, tmp_path / "real", baseline)
    assert rewritten == []
    assert keep.read_text() == body


def test_a_changed_file_with_the_same_content_is_rewritten(tmp_path):
    """The other direction of the same rule — without it the check above could
    be satisfied by a function that rewrites nothing at all."""
    stage = tmp_path / "stage"
    (stage / "sub").mkdir(parents=True)
    f = stage / "sub" / "changed.json"
    f.write_text(json.dumps({"project": str(stage)}) + "\n")
    rewritten = R._phase1_remap_stage_tree(stage, tmp_path / "real", {})
    assert rewritten == ["sub/changed.json"]
    assert json.loads(f.read_text())["project"] == str(tmp_path / "real")


def test_a_binary_artefact_is_never_rewritten(tmp_path):
    """Eligibility is a list of suffixes that ARE text, so a new binary
    artefact type cannot become eligible by nobody remembering to exclude it."""
    stage = tmp_path / "stage"
    stage.mkdir()
    gds = stage / "top.gds"
    raw = b"\x00\x06" + str(stage).encode() + b"\xff\xfe"
    gds.write_bytes(raw)
    assert R._phase1_remap_stage_tree(stage, tmp_path / "real", {}) == []
    assert gds.read_bytes() == raw


def test_a_sibling_directory_sharing_the_prefix_is_not_rewritten(tmp_path):
    """The PATH-BOUNDARY rule is #2158's, stated once and reused — a longer
    directory whose name merely starts with the stage path is a different
    directory."""
    stage = tmp_path / "stage"
    stage.mkdir()
    f = stage / "r.json"
    sibling = str(stage) + "_other/keep.txt"
    f.write_text(json.dumps({"a": str(stage) + "/in", "b": sibling}) + "\n")
    R._phase1_remap_stage_tree(stage, tmp_path / "real", {})
    got = json.loads(f.read_text())
    assert got["a"] == str(tmp_path / "real") + "/in"
    assert got["b"] == sibling, "a prefix-sharing sibling path was rewritten"


def test_a_file_without_the_stage_path_is_left_alone(tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    f = stage / "r.json"
    body = json.dumps({"note": "nothing to do here"}) + "\n"
    f.write_text(body)
    assert R._phase1_remap_stage_tree(stage, tmp_path / "real", {}) == []
    assert f.read_text() == body


def test_the_skip_agrees_with_the_manifest_it_reads(tmp_path):
    """The unchanged-file skip depends on TWO properties of a function in
    another module and nothing asserted either of them.

    `_phase1_remap_stage_tree` looks a file up in `baseline` by
    `relative_to(stage).as_posix()` and compares `entry.digest` to
    `sha256(bytes)`. If `_phase1_tree_manifest_fd` ever keyed its manifest
    differently, or digested anything but the raw bytes, the lookup would
    silently miss and every eligible file would be rewritten whether the run
    changed it or not — with no test failing. This drives the REAL manifest
    builder and asserts both.
    """
    import hashlib
    import os

    stage = tmp_path / "stage"
    (stage / "sub").mkdir(parents=True)
    (stage / "sub" / "a.json").write_text('{"x": 1}')
    (stage / "b.md").write_text("hi\n")

    fd = os.open(stage, os.O_RDONLY | os.O_DIRECTORY)
    try:
        manifest = R._phase1_tree_manifest_fd(fd, stage)
    finally:
        os.close(fd)

    files = {p.relative_to(stage).as_posix()
             for p in stage.rglob("*") if p.is_file()}
    assert {k for k, v in manifest.items() if v.kind == "file"} == files
    for rel in files:
        assert manifest[rel].digest == hashlib.sha256(
            (stage / rel).read_bytes()).hexdigest()

    # ...and the skip actually fires against that real manifest.
    (stage / "c.json").write_text(json.dumps({"project": str(stage)}) + "\n")
    fd = os.open(stage, os.O_RDONLY | os.O_DIRECTORY)
    try:
        manifest = R._phase1_tree_manifest_fd(fd, stage)
    finally:
        os.close(fd)
    assert R._phase1_remap_stage_tree(stage, tmp_path / "real", manifest) == []
