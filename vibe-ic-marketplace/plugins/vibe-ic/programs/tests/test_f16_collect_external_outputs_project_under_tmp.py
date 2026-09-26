"""F16 — `collect_external_outputs` copied a project INTO ITSELF when the project
sat under /tmp.

`_PATH_RE` matches by prefix, so under a /tmp project every self-reference the
reports carry looked like a volatile external path. The collector copied the
project into its own `collected_external/`, including the copies already there.
MEASURED on a 4 KiB synthetic project: one `collect()` left 340 files nested 169
levels deep, stopped only by ENAMETOOLONG. On spm each run grew ~245 GB.

The rule now: a path is external only when its RESOLVED real path lies outside
the resolved project root (`Path.is_relative_to`), and a path that contains the
project is not external either. Where either sits (/tmp or elsewhere) does not
matter.

Every project here is built under a volatile prefix on purpose (that is the
condition of the defect), with `tempfile.mkdtemp`, not `tmp_path`.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN))
import collect_external_outputs as CE  # noqa: E402
import project_outputs_in_tree_check as gate  # noqa: E402


@pytest.fixture
def volatile_root():
    for prefix in gate._VOLATILE_PREFIXES:
        root = Path(prefix)
        if root.is_dir() and os.access(root, os.W_OK):
            made = Path(tempfile.mkdtemp(prefix="f16-", dir=str(root)))
            break
    else:
        pytest.fail(f"no writable volatile root among {gate._VOLATILE_PREFIXES}")
    # the premise: the project really does match the collector's own regex
    assert gate._PATH_RE.search(str(made) + "/x"), made
    try:
        yield made
    finally:
        shutil.rmtree(made, ignore_errors=True)


def _project(root: Path, name: str = "proj") -> Path:
    p = root / name
    (p / "reports" / "phase3").mkdir(parents=True)
    (p / "phase3").mkdir()
    (p / "phase3" / "out.gds").write_bytes(b"GDS" * 64)
    return p


def _tree(p: Path):
    return sorted(str(f.relative_to(p)) for f in p.rglob("*"))


def test_project_under_tmp_collects_nothing_of_its_own(volatile_root):
    p = _project(volatile_root)
    report = p / "reports" / "phase3" / "r.json"
    body = json.dumps({"project": str(p),
                       "gds": str(p / "phase3" / "out.gds"),
                       "dir": str(p / "phase3")})
    report.write_text(body)
    before = _tree(p)

    assert CE.collect(p) == (0, [])

    assert not (p / "collected_external").exists()
    assert _tree(p) == before
    assert report.read_text() == body            # own references left as-is


def test_genuinely_external_file_is_collected(volatile_root):
    p = _project(volatile_root)
    ext = volatile_root / "eda_scratch" / "design.gds"
    ext.parent.mkdir()
    ext.write_text("GDS-DATA")
    (p / "reports" / "phase3" / "r.json").write_text(json.dumps(
        {"own": str(p / "phase3" / "out.gds"), "ext": str(ext)}))

    n, collected = CE.collect(p)

    assert n == 1
    assert [c["original"] for c in collected] == [str(ext)]
    assert (p / "collected_external" / "design.gds").read_text() == "GDS-DATA"
    # nothing of the project itself landed in the collection
    assert sorted(x.name for x in (p / "collected_external").iterdir()) == [
        "_provenance.json", "design.gds"]


def test_sibling_whose_name_extends_the_project_name_is_external(volatile_root):
    # `/tmp/x/proj2/...` shares the STRING prefix `/tmp/x/proj` but is outside
    # the project: a startswith() comparison would wrongly keep it.
    p = _project(volatile_root)
    sib = volatile_root / "proj2" / "design.gds"
    sib.parent.mkdir()
    sib.write_text("SIB")
    (p / "reports" / "phase3" / "r.json").write_text(json.dumps({"s": str(sib)}))

    n, collected = CE.collect(p)

    assert n == 1 and [c["original"] for c in collected] == [str(sib)]


def test_symlink_pointing_into_the_project_is_not_external(volatile_root):
    p = _project(volatile_root)
    link_dir = volatile_root / "outside"
    link_dir.mkdir()
    (link_dir / "link.gds").symlink_to(p / "phase3" / "out.gds")
    (link_dir / "linkdir").symlink_to(p)
    (p / "reports" / "phase3" / "r.json").write_text(json.dumps(
        {"f": str(link_dir / "link.gds"), "d": str(link_dir / "linkdir")}))
    before = _tree(p)

    assert CE.collect(p) == (0, [])
    assert _tree(p) == before


def test_path_containing_the_project_is_not_copied(volatile_root):
    # the project's parent is outside it, but copying it copies the project,
    # collection directory included — the same recursion by another route
    p = _project(volatile_root)
    (p / "reports" / "phase3" / "r.json").write_text(json.dumps(
        {"parent": str(volatile_root)}))
    before = _tree(p)

    assert CE.collect(p) == (0, [])
    assert _tree(p) == before


def test_is_external_is_decided_by_real_paths_not_prefix(volatile_root):
    p = _project(volatile_root)
    assert CE.is_external(str(p / "phase3" / "out.gds"), p) is False
    assert CE.is_external(str(p), p) is False
    assert CE.is_external(str(volatile_root), p) is False
    assert CE.is_external(str(volatile_root / "elsewhere.gds"), p) is True
    # an unresolved project spelling is judged the same way
    assert CE.is_external(str(p / "phase3" / "out.gds"),
                          p / "phase3" / "..") is False
