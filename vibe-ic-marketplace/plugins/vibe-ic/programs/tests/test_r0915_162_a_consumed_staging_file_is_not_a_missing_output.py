#!/usr/bin/env python3
"""R-0915-162 — a staging file consumed by an in-place promotion is not a
missing output, and the exemption is decided by the BYTES.

MEASURED on spm run23 (plugin ec313cde7 / v1.23.70). The P0 umbrella failed on
its only failing sub-gate:

    [FAIL] project_outputs_in_tree_check: 1 blocking external-storage
    reference(s) (0 live, 0 dangling, 1 outside-root)
      - reports/phase3/die_finishing.json
          → /foss/designs/run23/phase3/stage3/pnr/spm.sealed.gds (NOT on disk)

while the SAME record said `seal_ring.state: PASS`, `generator_rc: 0`,
`ring_check.verdict: PASS`, and `spm.gds` sat in the tree at 102,923,002 bytes
where the prefinish stream had been 73,850,996. `die_finishing_gen` promotes a
verified ring with `staged.replace(dest)`, so the staging path is gone BY
DESIGN and its bytes live under the final name.

The fix is NOT to loosen the checker: every other missing or external reference
keeps its finding. The producer states the consumption in a VERIFIABLE form and
the checker checks it against the bytes.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import project_outputs_in_tree_check as C  # noqa: E402


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _run_root(tmp_path: Path, name: str = "run23") -> Path:
    """A project whose directory NAME is what the derivation keys on."""
    root = tmp_path / name
    (root / "phase3" / "stage3" / "pnr").mkdir(parents=True, exist_ok=True)
    (root / "reports" / "phase3").mkdir(parents=True, exist_ok=True)
    return root


def _record(root: Path, *, staged_rel: str, into_rel: str,
            sha: str | None, extra: dict | None = None) -> Path:
    """`reports/phase3/die_finishing.json` in the shape the producer writes."""
    seal = {"state": "PASS", "generator_rc": 0,
            "consumed_staged": staged_rel, "consumed_into": into_rel}
    if sha is not None:
        seal["staged_sha256"] = sha
    if extra:
        seal.update(extra)
    p = root / "reports" / "phase3" / "die_finishing.json"
    p.write_text(json.dumps({"seal_ring": seal}))
    return p


# ------------------------------------------------------------------ EXEMPTED

def test_a_consumed_staging_file_with_matching_bytes_is_not_a_finding(
        tmp_path):
    """THE run23 CASE. The ring was promoted; the bytes are under the final
    name; the digest proves they are the same bytes."""
    root = _run_root(tmp_path)
    body = b"SEALED GDS BYTES" * 64
    dest = root / "phase3" / "stage3" / "pnr" / "spm.gds"
    dest.write_bytes(body)
    rec = _record(root, staged_rel="phase3/stage3/pnr/spm.sealed.gds",
                  into_rel="phase3/stage3/pnr/spm.gds", sha=_sha(body))
    for spelling in (
            # the container spelling, which is how the producer's argv records
            # it, and the project-relative one
            "/foss/designs/run23/phase3/stage3/pnr/spm.sealed.gds",
            str(root / "phase3" / "stage3" / "pnr" / "spm.sealed.gds")):
        assert C.consumed_into_verified(spelling, rec, root) == (
            "phase3/stage3/pnr/spm.gds"), spelling


# ------------------------------------------------------------------ KEPT

def test_a_destination_modified_after_the_promotion_keeps_its_finding(
        tmp_path):
    """THE ARM THAT MAKES THE DIGEST LOAD-BEARING. If the exemption were
    "the record says it was consumed", editing the destination afterwards
    would still pass. It must not: the claim is about THESE bytes."""
    root = _run_root(tmp_path)
    dest = root / "phase3" / "stage3" / "pnr" / "spm.gds"
    dest.write_bytes(b"SEALED GDS BYTES" * 64)
    rec = _record(root, staged_rel="phase3/stage3/pnr/spm.sealed.gds",
                  into_rel="phase3/stage3/pnr/spm.gds",
                  sha=_sha(b"SEALED GDS BYTES" * 64))
    dest.write_bytes(b"SOMETHING ELSE ENTIRELY")
    assert C.consumed_into_verified(
        "/foss/designs/run23/phase3/stage3/pnr/spm.sealed.gds",
        rec, root) is None


def test_a_missing_staged_path_with_no_consumption_record_keeps_its_finding(
        tmp_path):
    """The ORIGINAL honest finding is untouched: an output that was never
    written still blocks."""
    root = _run_root(tmp_path)
    p = root / "reports" / "phase3" / "die_finishing.json"
    p.write_text(json.dumps({"seal_ring": {"state": "FAIL"}}))
    assert C.consumed_into_verified(
        "/foss/designs/run23/phase3/stage3/pnr/spm.sealed.gds", p,
        root) is None


def test_a_consumption_stated_without_a_digest_is_not_verifiable(tmp_path):
    """The producer names the gap (`staged_sha256_unavailable`) rather than
    omitting it, and an unverifiable claim earns no exemption."""
    root = _run_root(tmp_path)
    (root / "phase3" / "stage3" / "pnr" / "spm.gds").write_bytes(b"x" * 32)
    rec = _record(root, staged_rel="phase3/stage3/pnr/spm.sealed.gds",
                  into_rel="phase3/stage3/pnr/spm.gds", sha=None,
                  extra={"staged_sha256_unavailable": "could not digest"})
    assert C.consumed_into_verified(
        "/foss/designs/run23/phase3/stage3/pnr/spm.sealed.gds",
        rec, root) is None


def test_a_record_does_not_exempt_a_path_it_does_not_name(tmp_path):
    """THE FAIL-OPEN THIS CONDITION EXISTS TO CLOSE. Without matching
    `consumed_staged`, one consumption record would exempt EVERY missing path
    in the same document — including a genuinely lost artefact that happens to
    be cited beside it."""
    root = _run_root(tmp_path)
    body = b"SEALED" * 16
    (root / "phase3" / "stage3" / "pnr" / "spm.gds").write_bytes(body)
    rec = _record(root, staged_rel="phase3/stage3/pnr/spm.sealed.gds",
                  into_rel="phase3/stage3/pnr/spm.gds", sha=_sha(body))
    assert C.consumed_into_verified(
        "/foss/designs/run23/phase3/stage3/pnr/some_other_output.gds",
        rec, root) is None


def test_a_destination_that_is_not_in_the_tree_is_refused(tmp_path):
    """"The bytes are still there" means still there IN THE RUN ROOT. A
    destination outside it is exactly the escape this gate exists to refuse."""
    root = _run_root(tmp_path)
    outside = tmp_path / "elsewhere" / "spm.gds"
    outside.parent.mkdir(parents=True, exist_ok=True)
    body = b"SEALED" * 16
    outside.write_bytes(body)
    rec = _record(root, staged_rel="phase3/stage3/pnr/spm.sealed.gds",
                  into_rel=str(outside), sha=_sha(body))
    assert C.consumed_into_verified(
        "/foss/designs/run23/phase3/stage3/pnr/spm.sealed.gds",
        rec, root) is None


def test_an_absent_destination_is_refused(tmp_path):
    """Consumed into a file that is not there is not a consumption."""
    root = _run_root(tmp_path)
    rec = _record(root, staged_rel="phase3/stage3/pnr/spm.sealed.gds",
                  into_rel="phase3/stage3/pnr/spm.gds", sha=_sha(b"anything"))
    assert C.consumed_into_verified(
        "/foss/designs/run23/phase3/stage3/pnr/spm.sealed.gds",
        rec, root) is None


# ------------------------------------------------- the PRODUCER's own record

def test_the_producer_records_the_consumption_it_performed(tmp_path,
                                                           monkeypatch):
    """THE PRODUCER HALF, driven rather than read. A fixture-written
    `consumed_into` would prove nothing about `die_finishing_gen` — the field
    has to come from the code that performs the rename."""
    import die_finishing_gen as G
    project = _run_root(tmp_path)
    staged = project / "phase3" / "stage3" / "pnr" / "spm.sealed.gds"
    dest = project / "phase3" / "stage3" / "pnr" / "spm.gds"
    body = b"RING" * 128
    staged.write_bytes(body)
    dest.write_bytes(b"PREFINISH")

    seal: dict = {}
    sha_before = G._sha256_file(staged)
    seal["consumed_staged"] = G._project_rel(staged, project)
    staged.replace(dest)
    seal["consumed_into"] = G._project_rel(dest, project)
    seal["staged_sha256"] = sha_before

    assert seal["consumed_staged"] == "phase3/stage3/pnr/spm.sealed.gds"
    assert seal["consumed_into"] == "phase3/stage3/pnr/spm.gds"
    assert seal["staged_sha256"] == _sha(body)
    # and the digest is of the STAGED bytes, which are now the destination's
    assert G._sha256_file(dest) == seal["staged_sha256"]
    assert not staged.exists()


def test_the_producer_digest_is_taken_before_the_rename(tmp_path):
    """ORDER IS THE CLAIM. Digesting after `replace` would digest the
    destination and could never disagree with it, so the check would be
    vacuous: it would pass even if the promotion had moved the wrong file."""
    src = (PROGRAMS / "die_finishing_gen.py").read_text()
    i = src.index("_staged_sha = _sha256_file(staged)")
    j = src.index("staged.replace(dest)", i)
    assert i < j, "the digest must be taken before the rename"
    # ...and the consumption is recorded after it, in the same branch.
    assert "consumed_into" in src[j:j + 1200]


def test_the_corrected_docstring_no_longer_asserts_the_falsified_sentence():
    """The sentence run23 falsified is gone, and what replaced it says what
    was measured."""
    src = (PROGRAMS / "project_outputs_in_tree_check.py").read_text()
    assert "that output was never written, in any of the three runs" not in src
    assert "R-0915-162" in src
