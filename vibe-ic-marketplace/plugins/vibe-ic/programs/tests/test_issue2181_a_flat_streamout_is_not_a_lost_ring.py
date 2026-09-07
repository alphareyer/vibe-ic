"""vibe-ic#2181 — a FLAT stream-out is a fact about the GDS, not about the ring.

THE DEFECT
----------
`step_pad_ring_final_evidence` proves the ring in the streamed GDS by walking
the GDS HIERARCHY (`_gds_reference_counts`) and requiring at least one structure
reference per ring master. A stream-out that writes the design FLAT has no
structure reference to ANY master by construction, so every ring master reports
`reachable_references: 0` and the BLOCKING gate accused the ring of being lost
while describing the stream-out's hierarchy.

MEASURED at v1.19.43 (main e2b3c08170b5) on a chip-path spm x gf180mcuD run,
image sha256:89a8fd72…, klayout stream-out:

    pad_ring_route_evidence verdict FAIL
      PADRING_GDS_REFERENCES_LOST:{'gf180mcu_fd_io__dvdd': {'expected': 1,
        'reachable_references': 0}, ... 'gf180mcu_fd_io__fill10':
        {'expected': 673, 'reachable_references': 0}}
      def_evidence: padring.def 771/771, routed.def 771/771, spm.def 771/771

and the same GDS read back with KLayout: 14 structures, top `chip_top`,
18,178,737 shapes IN the top and 0 references to any of the final DEF's 49
placed masters. The ring's geometry is present — 107,645 shapes inside the SW
corner pad's placed footprint, 90,421 inside the supply pad's, and 0 outside the
die (that last one is the instrument's own negative control).

WHAT THIS CHANGE DOES, AND WHAT IT REFUSES TO DO
------------------------------------------------
It renames the accusation and discloses the reason. It does NOT turn the FAIL
into a PASS: on a flat GDS the ring is still NOT PROVEN, the finding still
lands, and the gate still blocks. Reference counting simply cannot answer the
question on that file, and saying so is the whole fix — the previous wording
sent a reader at the ring, which is exactly what issue #2181 did.

The flat reading is EARNED, never reached by elimination: a stream-out that
wrote NOTHING also references nothing, and that is a total loss, not a
hierarchy fact. `test_kspm43_streamout_top_cell_is_the_def_design` documents a
measured instance — a 106-byte GDS holding one empty structure. So the flat
reading additionally requires the physical top to CONTAIN geometry.

WHICH step dropped the hierarchy is deliberately NOT claimed. Three shipped steps
re-write this file — stream-out, the grid snap (whose `nonorthogonal > 0` branch
calls `tc.flatten(-1, True)` on every top cell) and the density fill
(`fill_all.rb`, gds_in == gds_out) — and on the measured run the file's last
writer was the fill, so the gate can only report the state it found and says so.
Naming a culprit it had not measured is the mistake this whole change is about.

PREDICTED DIRECTIONS, written before these ran
----------------------------------------------
  1 flat GDS, geometry in the top      : PADRING_GDS_HIERARCHY_ABSENT, verdict FAIL
  2 empty GDS, no geometry in the top  : PADRING_GDS_REFERENCES_LOST, unchanged
  3 hierarchical GDS missing one master: PADRING_GDS_REFERENCES_LOST, unchanged
  4 hierarchical GDS, every master kept: no GDS finding at all
  5 the verdict in case 1              : FAIL in BOTH arms — nothing is weakened
All five held. MUTATION ARM: dropping the `element_counts > 0` conjunct makes 2
report HIERARCHY_ABSENT (an empty stream-out relabelled as harmless); dropping
the whole `_padring_gds_flat` branch makes 1 report REFERENCES_LOST again.
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import phase3_one_shot_runner as p3  # noqa: E402


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

def _rec(rtype: int, dtype: int, payload: bytes = b"") -> bytes:
    return struct.pack(">HBB", len(payload) + 4, rtype, dtype) + payload


def _name(s: str) -> bytes:
    b = s.encode("ascii")
    return b + (b"\x00" if len(b) % 2 else b"")


def _gds_bytes(structures) -> bytes:
    """structures: [(name, n_elements, [(sname, n_refs), ...])]"""
    out = [_rec(0x00, 0x02, struct.pack(">h", 600)),
           _rec(0x01, 0x02, b"\x00" * 24),
           _rec(0x02, 0x06, _name("LIB")),
           _rec(0x03, 0x05, b"\x00" * 16)]
    for sname, n_elem, refs in structures:
        out.append(_rec(0x05, 0x02, b"\x00" * 24))
        out.append(_rec(0x06, 0x06, _name(sname)))
        for _ in range(n_elem):
            out.append(_rec(0x08, 0x00))                       # BOUNDARY
            out.append(_rec(0x0D, 0x02, struct.pack(">h", 1)))  # LAYER
            out.append(_rec(0x0E, 0x02, struct.pack(">h", 0)))  # DATATYPE
            out.append(_rec(0x10, 0x03, struct.pack(">8i", *([0] * 8))))
            out.append(_rec(0x11, 0x00))                        # ENDEL
        for target, count in refs:
            for _ in range(count):
                out.append(_rec(0x0A, 0x00))                    # SREF
                out.append(_rec(0x12, 0x06, _name(target)))     # SNAME
                out.append(_rec(0x10, 0x03, struct.pack(">2i", 0, 0)))
                out.append(_rec(0x11, 0x00))                    # ENDEL
        out.append(_rec(0x07, 0x00))                            # ENDSTR
    out.append(_rec(0x04, 0x00))                                # ENDLIB
    return b"".join(out)


_DESIGN = "chip_top"
_RING_MASTER = "IO_PAD_A"
_CORE_MASTER = "STD_CELL_A"


def _write_def(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "VERSION 5.8 ;\n"
        f"DESIGN {_DESIGN} ;\n"
        "UNITS DISTANCE MICRONS 1000 ;\n"
        "DIEAREA ( 0 0 ) ( 100000 100000 ) ;\n"
        "COMPONENTS 2 ;\n"
        f"- u_pad {_RING_MASTER} + FIXED ( 0 0 ) N ;\n"
        f"- u_core {_CORE_MASTER} + PLACED ( 10 10 ) N ;\n"
        "END COMPONENTS\n"
        "END DESIGN\n")


def _project(tmp_path: Path, gds_structures) -> Path:
    pnr = p3._pl.pnr_dir(tmp_path)
    for name in ("padring.def", "routed.def", "spm.def"):
        _write_def(pnr / name)
    (pnr / "pnr.tcl").write_text(
        "puts {PADRING_ROUTING_CONSUMED: fixture}\n"
        "global_placement\ndetailed_route\n")
    (pnr / "openroad.log").write_text("PADRING_ROUTING_CONSUMED: fixture\n")
    (pnr / "spm.gds").write_bytes(_gds_bytes(gds_structures))
    reports = tmp_path / "reports" / "phase3"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "padring.json").write_text(json.dumps({
        "producer": {"pads": [{"instance": "u_pad", "master": _RING_MASTER}],
                     "corners": [], "fillers": []}}))
    return tmp_path


def _run(project: Path):
    gds = p3._pl.pnr_dir(project) / "spm.gds"
    gds_result = p3.StepResult("gds", "PASS", 0.0, "fixture", [str(gds)],
                               extras={"streamout_engine": "fixture"})
    res = p3.step_pad_ring_final_evidence(project, "spm", gds_result)
    doc = json.loads((project / "reports" / "phase3"
                      / "pad_ring_route_evidence.json").read_text())
    return res, doc


def _findings(doc) -> str:
    return " | ".join(str(f) for f in doc["findings"])


# --------------------------------------------------------------------------
# 1 / 5 — the flat stream-out is named for what it is, and still blocks
# --------------------------------------------------------------------------

def test_a_flat_streamout_is_named_a_hierarchy_fact(tmp_path):
    project = _project(tmp_path, [(_DESIGN, 40, [])])
    res, doc = _run(project)
    assert "PADRING_GDS_HIERARCHY_ABSENT" in _findings(doc), doc["findings"]
    assert "PADRING_GDS_REFERENCES_LOST" not in _findings(doc), doc["findings"]
    assert doc["gds_hierarchy"] == "flat"
    assert doc["final_def_master_count"] == 2


def test_the_flat_streamout_still_fails_the_gate(tmp_path):
    """Nothing here is weakened: the ring is still NOT PROVEN in the GDS."""
    project = _project(tmp_path, [(_DESIGN, 40, [])])
    res, doc = _run(project)
    assert res.status == "FAIL"
    assert doc["verdict"] == "FAIL"


# --------------------------------------------------------------------------
# 2 — NEGATIVE CONTROL: an empty stream-out is a total loss, not a hierarchy
# --------------------------------------------------------------------------

def test_an_empty_streamout_is_still_a_lost_ring(tmp_path):
    """The 106-byte empty-cell stream-out of `test_kspm43_streamout_top_cell_
    is_the_def_design` references nothing EITHER. It must keep its accusation."""
    project = _project(tmp_path, [(_DESIGN, 0, [])])
    res, doc = _run(project)
    assert "PADRING_GDS_REFERENCES_LOST" in _findings(doc), doc["findings"]
    assert "PADRING_GDS_HIERARCHY_ABSENT" not in _findings(doc), doc["findings"]
    assert doc["gds_hierarchy"] == "hierarchical"
    assert res.status == "FAIL"


# --------------------------------------------------------------------------
# 3 / 4 — NO-REGRESSION on the hierarchical stream-out this gate was written for
# --------------------------------------------------------------------------

def test_a_hierarchical_streamout_that_drops_the_ring_still_accuses_the_ring(
        tmp_path):
    project = _project(tmp_path, [
        (_DESIGN, 2, [(_CORE_MASTER, 1)]),
        (_CORE_MASTER, 5, []),
    ])
    res, doc = _run(project)
    assert "PADRING_GDS_REFERENCES_LOST" in _findings(doc), doc["findings"]
    assert "PADRING_GDS_HIERARCHY_ABSENT" not in _findings(doc), doc["findings"]
    assert doc["gds_hierarchy"] == "hierarchical"
    assert res.status == "FAIL"


def test_a_hierarchical_streamout_that_keeps_the_ring_reports_no_gds_finding(
        tmp_path):
    project = _project(tmp_path, [
        (_DESIGN, 2, [(_CORE_MASTER, 1), (_RING_MASTER, 1)]),
        (_CORE_MASTER, 5, []),
        (_RING_MASTER, 5, []),
    ])
    res, doc = _run(project)
    assert "PADRING_GDS" not in _findings(doc), doc["findings"]
    assert doc["gds_hierarchy"] == "hierarchical"
    assert res.status == "PASS", res.detail


# --------------------------------------------------------------------------
# the element reader itself
# --------------------------------------------------------------------------

def test_the_element_reader_counts_what_a_structure_contains(tmp_path):
    gds = tmp_path / "x.gds"
    gds.write_bytes(_gds_bytes([("full", 7, [("empty", 3)]), ("empty", 0, [])]))
    counts = p3._gds_structure_element_counts(gds)
    assert counts == {"full": 7, "empty": 0}, counts
    # and the two readers answer DIFFERENT questions about the same file
    assert p3._gds_reference_counts(gds, "full") == {"empty": 3}
