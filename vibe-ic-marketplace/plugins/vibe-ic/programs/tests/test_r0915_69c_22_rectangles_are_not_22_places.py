"""R-0915-69(c) — the illegal-overlap gate counted rectangles and named no place.

MEASURED, sha256 x sky130A, lane icsha2 run15 (main 385445351), front door.
`steps/phase3/stage3/31_.../magic_illegal_overlap.json`:

    "the extractor reported 22 illegal overlap(s), against a threshold of 0"

Correct, and at the right threshold.  But attributing those 22 boxes by hand —
intersecting each against the LEF met1 OBS of all 11278 placed instances,
transformed by placement and orientation — puts every one of them on THREE
flip-flop instances:

    _18133_ edfxtp_1 @FS   5 rect   x 21.655..29.150 um
    _18720_ dfxtp_1  @FS  10 rect   x 51.565..55.565 um   (incl. the one via1)
    _18814_ dfxtp_1  @FS   7 rect   x 68.125..72.125 um

all crossed by ONE horizontal met1 wire (`net4772`) at y = 417.450 um.  magic
files one feedback area per MAXIMAL RECTANGLE of a decomposed region, so "22"
over-states the defect count sevenfold and points at no location a reader can
act on.

What is added is DISCLOSURE ONLY: the threshold stays 0, `gate_count` is
untouched, and the verdict comes from exactly the same number.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import magic_illegal_overlap_check as M


def _rec(box, line=1, msg=None):
    return M.Record(msg or f"Illegal overlap between obsm1 and metal1", "medium",
                    box, line)


# --------------------------------------------------------------------------
# the clusterer — pure, and it can only ever group
# --------------------------------------------------------------------------
def test_two_rectangles_sharing_an_edge_are_one_region():
    """magic's own decomposition shares edges, so contact is the join."""
    r = M.cluster_regions([_rec((0, 0, 10, 10), 1), _rec((0, 10, 10, 20), 3)])
    assert len(r) == 1
    assert r[0]["rectangles"] == 2
    assert r[0]["bbox"] == [0, 0, 10, 20]


def test_two_rectangles_with_a_gap_stay_two_regions():
    """NEGATIVE CONTROL. Merely being close is not connected — run15's `_18720_`
    carries two straps 0.2 um apart and calling them one place would be a claim
    the geometry does not make."""
    r = M.cluster_regions([_rec((0, 0, 10, 10), 1), _rec((0, 11, 10, 20), 3)])
    assert len(r) == 2


def test_connection_is_transitive():
    r = M.cluster_regions([_rec((0, 0, 10, 10), 1), _rec((10, 0, 20, 10), 3),
                           _rec((20, 0, 30, 10), 5)])
    assert len(r) == 1 and r[0]["rectangles"] == 3


def test_every_record_lands_in_exactly_one_region():
    """Clustering GROUPS; it never filters. The module asserts this itself."""
    recs = [_rec((0, 0, 10, 10), 1), _rec((100, 100, 110, 110), 3),
            _rec((5, 5, 15, 15), 5)]
    regions = M.cluster_regions(recs)
    assert sum(x["rectangles"] for x in regions) == len(recs)


def test_a_record_with_no_box_is_kept_as_an_unlocated_region():
    """A record the parser could not place is COUNTED, never dropped."""
    regions = M.cluster_regions([_rec((0, 0, 10, 10), 1), _rec(None, 4)])
    assert sum(x["rectangles"] for x in regions) == 2
    unloc = [x for x in regions if x["bbox"] is None]
    assert unloc and unloc[0]["rectangles"] == 1 and unloc[0]["lines"] == [4]


def test_no_records_yields_no_regions():
    assert M.cluster_regions([]) == []


def test_the_clusterer_is_pure():
    import inspect
    src = inspect.getsource(M.cluster_regions)
    for forbidden in ("open(", "Path(", "read_text", "subprocess"):
        assert forbidden not in src, forbidden


# --------------------------------------------------------------------------
# run15's own 22, end to end through the real gate
# --------------------------------------------------------------------------
#: run15's `phase3/stage3/extracted/extract_feedback.txt`, verbatim — the 22
#: areas magic filed, INLINED so this test measures the real dump on any host
#: instead of skipping wherever the lane directory is absent.
_RUN15_FEEDBACK = 'box 4331 83484 4389 83493\nfeedback add "Illegal overlap between obsm1 and metal1 (types do not connect)" medium\nbox 5088 83484 5146 83493\nfeedback add "Illegal overlap between obsm1 and metal1 (types do not connect)" medium\nbox 4331 83476 5601 83484\nfeedback add "Illegal overlap between obsm1 and metal1 (types do not connect)" medium\nbox 5772 83484 5830 83493\nfeedback add "Illegal overlap between obsm1 and metal1 (types do not connect)" medium\nbox 5599 83476 5830 83484\nfeedback add "Illegal overlap between obsm1 and metal1 (types do not connect)" medium\nbox 10313 83484 10371 83493\nfeedback add "Illegal overlap between obsm1 and metal1 (types do not connect)" medium\nbox 10551 83484 10609 83493\nfeedback add "Illegal overlap between obsm1 and metal1 (types do not connect)" medium\nbox 11055 83484 11113 83493\nfeedback add "Illegal overlap between obsm1 and metal1 (types do not connect)" medium\nbox 10313 83476 10462 83484\nfeedback add "Illegal overlap between obsm1 and metal1 (types do not connect)" medium\nbox 10456 83464 10462 83476\nfeedback add "Illegal overlap between obsm1 and metal1 (types do not connect)" medium\nbox 10462 83464 10514 83484\nfeedback add "Illegal overlap between obsm1 and via1 (types do not connect)" medium\nbox 10514 83476 11113 83484\nfeedback add "Illegal overlap between obsm1 and metal1 (types do not connect)" medium\nbox 10514 83464 10520 83476\nfeedback add "Illegal overlap between obsm1 and metal1 (types do not connect)" medium\nbox 10474 83456 10502 83464\nfeedback add "Illegal overlap between obsm1 and metal1 (types do not connect)" medium\nbox 10474 83388 10502 83416\nfeedback add "Illegal overlap between obsm1 and metal1 (types do not connect)" medium\nbox 13625 83487 13683 83493\nfeedback add "Illegal overlap between obsm1 and metal1 (types do not connect)" medium\nbox 13625 83484 13683 83487\nfeedback add "Illegal overlap between obsm1 and metal1 (types do not connect)" medium\nbox 13863 83487 13921 83493\nfeedback add "Illegal overlap between obsm1 and metal1 (types do not connect)" medium\nbox 13863 83484 13921 83487\nfeedback add "Illegal overlap between obsm1 and metal1 (types do not connect)" medium\nbox 13625 83476 14001 83484\nfeedback add "Illegal overlap between obsm1 and metal1 (types do not connect)" medium\nbox 14367 83484 14425 83493\nfeedback add "Illegal overlap between obsm1 and metal1 (types do not connect)" medium\nbox 13999 83476 14425 83484\nfeedback add "Illegal overlap between obsm1 and metal1 (types do not connect)" medium\n'


def _run15_project(tmp_path: Path) -> Path:
    ext = tmp_path / "phase3" / "stage3" / "extracted"
    ext.mkdir(parents=True)
    (ext / "extract_feedback.txt").write_text(_RUN15_FEEDBACK)
    (ext / "ext2spice.log").write_text(
        "sha256: 22 errors\nTotal of 22 errors (check feedback entries).\n"
        "exttospice finished.\n")
    (ext / "sha256_extracted.sp").write_text(".subckt sha256\n.ends\n")
    return tmp_path


def test_run15s_22_rectangles_are_four_regions(tmp_path: Path):
    rep = M.check(_run15_project(tmp_path))
    assert rep["counts"]["gate_count"] == 22
    assert rep["region_count"] == 4
    assert [r["rectangles"] for r in rep["regions"]] == [5, 9, 1, 7]
    # magic internal units; x 21.655 / 51.565 / 52.370 / 68.125 um at 0.005/unit
    assert [r["bbox"][0] for r in rep["regions"]] == [4331, 10313, 10474, 13625]


def test_the_verdict_is_unchanged_by_the_disclosure(tmp_path: Path):
    """THE CONTROL THAT MATTERS: nothing here relaxes the gate."""
    rep = M.check(_run15_project(tmp_path))
    assert rep["passed"] is False
    assert rep["counts"]["gate_count"] == 22
    assert rep["counts"]["determined"] is True
    assert rep["metrics"]["31__drv__magic_illegal_overlap__violation_count"] == 22
    assert rep["reason"].startswith("22 illegal overlap(s)")


def test_the_finding_tells_a_triager_where_to_go(tmp_path: Path):
    rep = M.check(_run15_project(tmp_path))
    msg = rep["findings"][0]["message"]
    assert "4 CONNECTED REGION(S)" in msg
    assert "rectangles, not places" in msg
    assert "[4331 83476 5830 83493]" in msg


# --------------------------------------------------------------------------
# the sentence
# --------------------------------------------------------------------------
def test_a_single_rectangle_is_not_padded_with_a_restatement():
    assert M._regions_sentence(M.cluster_regions([_rec((0, 0, 1, 1))]), 1) == ""


def test_no_located_record_says_nothing():
    assert M._regions_sentence(M.cluster_regions([_rec(None, 1), _rec(None, 2)]),
                               2) == ""


def test_unlocated_records_are_disclosed_alongside_the_regions():
    recs = [_rec((0, 0, 10, 10), 1), _rec((100, 0, 110, 10), 2), _rec(None, 3)]
    s = M._regions_sentence(M.cluster_regions(recs), 3)
    assert "2 CONNECTED REGION(S)" in s
    assert "1 record(s) carry no bounding box" in s
    assert "counted, never dropped" in s


def test_a_very_large_region_list_is_truncated_and_says_so():
    recs = [_rec((i * 100, 0, i * 100 + 10, 10), i) for i in range(20)]
    s = M._regions_sentence(M.cluster_regions(recs), 20)
    assert "20 CONNECTED REGION(S)" in s
    assert "and 8 more." in s


# --------------------------------------------------------------------------
# a clean extraction is still clean, and says nothing about regions
# --------------------------------------------------------------------------
def test_a_clean_dump_still_passes_and_carries_no_regions(tmp_path: Path):
    """NEGATIVE CONTROL. The clean path is untouched."""
    ext = tmp_path / "phase3" / "stage3" / "extracted"
    ext.mkdir(parents=True)
    (ext / "extract_feedback.txt").write_text("")
    (ext / "ext2spice.log").write_text("exttospice finished.\n")
    (ext / "top.sp").write_text(".subckt top\n.ends\n")
    rep = M.check(tmp_path)
    assert rep["passed"] is True
    assert rep["region_count"] == 0
    assert rep["regions"] == []
