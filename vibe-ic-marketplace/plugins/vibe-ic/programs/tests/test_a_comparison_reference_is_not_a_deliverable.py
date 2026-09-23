"""The retained finishing boundary: found by the fidelity step, kept out of the pack.

R-0915-146, two halves of one mistake about ONE artefact — the pre-finishing layout
the gds step retains so stream-out/finishing fidelity can be measured against it.

(a) THE FIDELITY STEP COULD NOT FIND IT. The runner retains
    `phase3/stage3/pnr/{top}.prefinish.gds` where `top` is the name PnR ran under;
    `gds_xor_check` resolved `{top}` from the DEF FILE'S STEM. MEASURED on spm
    run22: the shipped GDS is `spm.gds` and the source DEF is `spm.def`, so the
    checker looked for `spm.prefinish.gds`, while the artefact the run actually kept
    is `chip_top.prefinish.gds` — the runner's top was `chip_top`, and the front
    door's own advisory recorded "phase3 auto-derived top='spm' from --ic-name (no
    chip_top module)". The two names cannot meet, so the retained boundary was never
    used and every run fell back to a re-stream: the expensive path this artefact
    exists to avoid, and one that, missing a single library, produces a confident
    wrong answer about the design (776,403 then 348,392 phantom differences,
    measured, neither about that chip).

(b) AND IT SHIPPED TO THE FOUNDRY. The same run's
    `phase3/stage4/foundry_handoff/` carried `chip_top.prefinish.gds`
    (73,850,996 B) hardlinked beside the shipped `spm.gds` (102,923,002 B) — 29 MB
    smaller, because fill, seal ring and snap had not happened yet. The runner's
    link loop filtered only scribe stubs and 0-byte files. The run's own write
    ledger classes the file `written_never_declared`: nothing declares it a
    deliverable. A hand-off package is what a foundry taped out FROM, and a second,
    differently named, entirely plausible GDS inside it is an invitation to tape out
    the wrong layout — non-empty, parseable, carrying real geometry, so nothing
    downstream would notice.

Fixed in both places, and the gate refuses one however it arrived: "the producer
stopped doing it" is not the same guarantee as "the package cannot contain it".

WHAT IS DELIBERATELY NOT EXCLUDED: `spm.filled.gds`, which on run22 has the same
size as the shipped GDS and is a stage OF the deliverable rather than a reference to
compare against. That is duplication in the package, not a wrong-layout hazard; it
is reported rather than quietly dropped, because excluding a file for being unclear
would be the same class of guess in the other direction.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
PROGRAMS = PLUGIN / "programs"
sys.path.insert(0, str(PROGRAMS))

import foundry_handoff_package_check as PKG                   # noqa: E402
import gds_xor_check as GX                                    # noqa: E402
import phase3_one_shot_runner as RUNNER                       # noqa: E402


def _pnr(project: Path) -> Path:
    d = project / "phase3/stage3/pnr"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── (a) the boundary is found by the name the RUN wrote ────────────────────

def _def(project: Path, name: str, design: str) -> Path:
    """A DEF whose FILENAME and whose DESIGN statement differ — run22's shape."""
    d = _pnr(project) / name
    d.write_text(f"VERSION 5.8 ;\nDESIGN {design} ;\nCOMPONENTS 0 ;\nEND DESIGN\n")
    return d


def _boundary(project: Path, design: str, body: bytes = b"HEADER\x00" * 32, *,
              def_file: Path = None, receipt: bool = True) -> Path:
    """A retained boundary, with the receipt the runner now writes beside it."""
    import hashlib
    b = _pnr(project) / f"{design}.prefinish.gds"
    b.write_bytes(body)
    if receipt:
        rec = {"program": "phase3_one_shot_runner", "artefact": b.name,
               "sha256": hashlib.sha256(body).hexdigest(),
               "size": len(body), "engine": "klayout"}
        if def_file is not None:
            rec["streamed_from_def"] = def_file.name
            rec["def_sha256"] = hashlib.sha256(def_file.read_bytes()).hexdigest()
        b.with_suffix(".gds.receipt.json").write_text(json.dumps(rec) + "\n")
    return b


def test_the_boundary_is_named_by_the_defs_own_DESIGN(tmp_path):
    """RUN22'S EXACT SHAPE, and the name is DERIVED rather than globbed: the DEF is
    `spm.def` whose DESIGN is `chip_top`, and the retained boundary is
    `chip_top.prefinish.gds`. Before this, the stem lookup missed and every run
    re-streamed."""
    dfile = _def(tmp_path, "spm.def", "chip_top")
    _boundary(tmp_path, "chip_top", def_file=dfile)
    kept, kind, prov = GX.resolve_reference(tmp_path, "spm", dfile)
    assert kind == "retained", (kind, prov)
    assert kept is not None and kept.name == "chip_top.prefinish.gds"


def test_a_boundary_with_no_receipt_is_not_this_runs(tmp_path):
    """THE HOLE MY GLOB OPENED, closed: a leftover boundary with nothing tying it to
    this run must NOT be used as "design-layer differences expected to be exactly 0".
    It re-streams instead, which is what an absent one has always done."""
    dfile = _def(tmp_path, "spm.def", "chip_top")
    _boundary(tmp_path, "chip_top", def_file=dfile, receipt=False)
    kept, kind, prov = GX.resolve_reference(tmp_path, "spm", dfile)
    assert kept is None and kind == "unprovable", (kind, prov)
    assert "cannot be proven" in prov["note"]


def test_a_boundary_whose_bytes_changed_since_its_receipt_is_refused(tmp_path):
    dfile = _def(tmp_path, "spm.def", "chip_top")
    b = _boundary(tmp_path, "chip_top", def_file=dfile)
    b.write_bytes(b"SOMETHING ELSE" * 8)          # replaced after the receipt
    kept, kind, prov = GX.resolve_reference(tmp_path, "spm", dfile)
    assert kept is None and kind == "unprovable", (kind, prov)
    assert "sha256 mismatch" in prov["note"]


def test_a_boundary_streamed_from_a_different_def_is_refused(tmp_path):
    """The stale-run case exactly: the boundary is intact and its receipt is
    honest, but it was streamed from a DEF that is not the one being compared."""
    old_def = _def(tmp_path, "spm.def", "chip_top")
    _boundary(tmp_path, "chip_top", def_file=old_def)
    # the routing changed: same DEF path, different content
    old_def.write_text("VERSION 5.8 ;\nDESIGN chip_top ;\nCOMPONENTS 1 ;\n"
                       "- u1 INV ;\nEND COMPONENTS\nEND DESIGN\n")
    kept, kind, prov = GX.resolve_reference(tmp_path, "spm", old_def)
    assert kept is None and kind == "unprovable", (kind, prov)
    assert "streamed from a different" in prov["note"]


def test_no_boundary_at_all_still_falls_back(tmp_path):
    """The absent case is untouched: no retained boundary means the re-stream path,
    exactly as before."""
    dfile = _def(tmp_path, "spm.def", "chip_top")
    kept, kind, _prov = GX.resolve_reference(tmp_path, "spm", dfile)
    assert kept is None and kind != "retained"


def test_the_runner_records_the_sha_it_already_computes():
    """SOURCE PIN: `_pf_sha` was computed and only PRINTED, which is why nothing
    downstream could tell this run's boundary from a leftover."""
    src = (PROGRAMS / "phase3_one_shot_runner.py").read_text()
    i = src.index("_pf_sha = _sha256_file(prefinish_gds)")
    window = src[i:i + 2500]
    assert '.gds.receipt.json' in window, "the boundary's sha is still only printed"
    assert '"def_sha256"' in window and '"streamed_from_def"' in window, (
        "the receipt does not bind the boundary to the DEF it was streamed from")


def test_the_magic_branch_removes_a_boundary_it_did_not_produce():
    """SOURCE PIN on the other half: only the KLayout branch retains a boundary, so
    the Magic branch must not leave an earlier run's behind."""
    src = (PROGRAMS / "phase3_one_shot_runner.py").read_text()
    i = src.index('extras={"streamout_engine": "magic"')
    window = src[max(0, i - 4000):i]
    assert 'glob("*.prefinish.gds")' in window, (
        "the magic stream-out branch does not clear a stale finishing boundary")
    assert "_stale.unlink()" in window


# ── (b) a reference layout never enters the pack ───────────────────────────

def test_the_producer_and_the_gate_name_the_same_reference_set():
    """Two hand-kept lists are two lists that drift, so they are asserted equal."""
    src = (PROGRAMS / "phase3_one_shot_runner.py").read_text()
    assert '_REFERENCE_ONLY_SUFFIXES = (".prefinish.gds", ".ring_reference.gds")' \
        in src, "the runner's exclusion set moved; the gate's copy would now drift"
    assert PKG.REFERENCE_ONLY_SUFFIXES == (".prefinish.gds",
                                           ".ring_reference.gds")


def _pack(project: Path, *names: str) -> Path:
    d = project / "phase3/stage4/foundry_handoff"
    d.mkdir(parents=True, exist_ok=True)
    for n in names:
        (d / n).write_bytes(b"GDSII\x00" * 64)
    return d


def _run_gate(project: Path):
    out = project / "reports/phase3/foundry_handoff_check.json"
    r = subprocess.run(
        [sys.executable, str(PROGRAMS / "foundry_handoff_package_check.py"),
         str(project), "--json", str(out)],
        capture_output=True, text=True, timeout=900)
    doc = json.loads(out.read_text()) if out.is_file() else {}
    return r.returncode, doc


def test_the_gate_fails_a_pack_carrying_a_prefinish_layout(tmp_path):
    """THE RUN22 PACK, through the gate's own entry point."""
    _pack(tmp_path, "spm.gds", "chip_top.prefinish.gds")
    rc, doc = _run_gate(tmp_path)
    assert rc == 1, (rc, doc)
    assert doc.get("verdict") == "FAIL"
    rules = [f.get("rule") for f in (doc.get("findings") or [])]
    assert PKG.RULE_REFERENCE_LAYOUT_IN_PACK in rules, doc
    assert any("chip_top.prefinish.gds" in m
               for m in (doc.get("reference_members") or [])), doc


def test_the_gate_fails_a_pack_carrying_a_ring_reference(tmp_path):
    _pack(tmp_path, "spm.gds", "spm.ring_reference.gds")
    rc, doc = _run_gate(tmp_path)
    assert rc == 1 and doc.get("verdict") == "FAIL"
    assert PKG.RULE_REFERENCE_LAYOUT_IN_PACK in [
        f.get("rule") for f in (doc.get("findings") or [])]


def test_a_pack_without_a_reference_layout_is_not_failed_for_this(tmp_path):
    """THE OTHER DIRECTION: this rule must not be the reason an ordinary pack
    fails. A pack with only the shipped GDS and a `.filled.gds` stage is not
    refused BY THIS RULE — it may still fail for its own missing members, which is
    a different finding and a different rule."""
    _pack(tmp_path, "spm.gds", "spm.filled.gds")
    _rc, doc = _run_gate(tmp_path)
    rules = [f.get("rule") for f in (doc.get("findings") or [])]
    assert PKG.RULE_REFERENCE_LAYOUT_IN_PACK not in rules, doc
    assert not doc.get("reference_members"), doc


def test_the_runner_excludes_the_reference_from_the_link_set(tmp_path):
    """The packaging moved: the runner's copy-if-absent hardlink loop was
    replaced by `foundry_handoff_pack_gen.package_layout_members`, whose member
    set is `foundry_handoff_package_check.layout_member_sources` (a stale
    member measured on spm run23 -- see
    test_foundry_handoff_layout_member_is_the_signed_off_gds.py). The claim is
    unchanged and now DRIVEN: a comparison reference beside the shipped GDS is
    never a member and is never written into the pack, and the runner still
    RECORDS what it skipped."""
    import foundry_handoff_pack_gen as G
    for rel in ("phase3/stage4/gds/spm.gds",
                "phase3/stage3/pnr/chip_top.prefinish.gds",
                "phase3/stage3/pnr/spm.ring_reference.gds"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"GDS" * 64)
    assert sorted(PKG.layout_member_sources(tmp_path)) == ["spm.gds"]
    rec = G.package_layout_members(tmp_path)
    assert sorted(rec["members"]) == ["spm.gds"], rec
    hd = tmp_path / "phase3/stage4/foundry_handoff"
    assert sorted(p.name for p in hd.glob("*.gds")) == ["spm.gds"]
    src = (PROGRAMS / "phase3_one_shot_runner.py").read_text()
    i = src.index("_REFERENCE_ONLY_SUFFIXES = ")
    window = src[i:src.index("PACKAGING_ERRORS.txt", i)]
    assert "_reference_members = sorted(" in window and \
        "for sfx in _REFERENCE_ONLY_SUFFIXES" in window, (
        "a skipped member must be RECORDED — 'the package does not contain this' "
        "and 'nobody looked' must not read the same")
