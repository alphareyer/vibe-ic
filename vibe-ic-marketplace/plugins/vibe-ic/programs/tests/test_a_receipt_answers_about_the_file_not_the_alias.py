#!/usr/bin/env python3
"""One physical report, one receipt answer — whichever alias is discovered.

THE MEASURED DEFECT (lane icgate1, 2026-09-21), on `spm x gf180mcuD` run8's own
`reports/phase3/drc_signoff.rpt`, its own `provenance.jsonl` and its own GDS:

    [ERROR] DRC_ZERO_NOT_MEASURED: 1 discovered DRC report(s) yielded a total
    of 0, but NO report stated a violation count and NONE was corroborated ...
    receipt_corroborated_files = 0
    producers: ['steps/phase3/stage3/31_.../drc_signoff.rpt']

The receipt the audit asks for WAS there. `provenance.jsonl` row 73 is an
`invocation`, `measured: true`, `tool: klayout`, `exit_code: 0`, whose
`outputs` carry BOTH `reports/phase3/drc_signoff.rpt` and
`reports/phase3/drc_signoff.log` at the digests on disk, and whose `inputs`
carry `phase3/stage3/pnr/spm.gds` still matching on disk — written by
`phase3_one_shot_runner._rebind_measured_drc_invocation_to_canonical_path`,
which exists for exactly this contract.

`_measured_klayout_receipt_files` never consulted it. It RESOLVED the report to
build `rel` — so the ledger is queried under the canonical name — but took the
transcript from the LITERAL path it was handed. The step-output collector
publishes each canonical report a second time as a SYMLINK under
`steps/<phase>/<stage>/<step>/` and publishes NO `.log` beside it, and
`_identity` collapses the pair by inode so exactly ONE survives discovery.
WHICH one is `os.scandir` order — a property of the directory, not the design.

    same tree, same ledger, same inode, only the alias differs
        reports/phase3/drc_signoff.rpt     -> corroborated 1
        steps/.../31_.../drc_signoff.rpt   -> corroborated 0

run8 drew the second. A sign-off gate refused a clean layout because of a
directory hash.

NOTHING IS RELAXED BY THE REPAIR, and every negative control below says so: the
transcript must still exist, its digest must still equal the one the ledger
declared, the report's digest must still match, the GDS input must still match
on disk, and the row must still be a measured exit-0 klayout invocation. A
fabricated zero — a report with no run behind it — stays
DRC_ZERO_NOT_MEASURED through BOTH aliases.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parents[1]
if str(PROG) not in sys.path:
    sys.path.insert(0, str(PROG))

import eda_report_audit as A  # noqa: E402

DECK = ("/foss/pdks/sky130A/libs.tech/klayout/drc/sky130A_mr.drc")
CANON_RPT = "reports/phase3/drc_signoff.rpt"
CANON_LOG = "reports/phase3/drc_signoff.log"
MIRROR = ("steps/phase3/stage3/"
          "31_physical_verification_drc_lvs_erc_density/drc_signoff.rpt")
GDS = "phase3/stage3/pnr/dut.gds"

#: A KLayout report database with an EMPTY `<items>` element — a zero the tool
#: expressed by recording nothing, which is the shape the corroboration
#: contract exists for: it states no textual count of its own.
#:
#: Built from a real deck's rule shape rather than hand-trimmed to the
#: minimum. `_check_drc` carries an anti-stub floor (a report under 2048 B
#: "suggests a hand-typed stub") and a per-category presence check, and a
#: fixture that trips those is testing the stub gate instead of the receipt
#: contract. Every one of the six categories the audit looks for is
#: represented, and NO rule text states a count, so `has_count` stays False —
#: which is the whole premise: a zero nothing textual corroborates.
_RULES = [
    ("M1.1", "min. metal1 width : 0.14um"),
    ("M1.2", "min. metal1 spacing : 0.14um"),
    ("M1.3", "min. metal1 spacing to wide metal1 : 0.28um"),
    ("M2.1", "min. metal2 width : 0.14um"),
    ("M2.2", "min. metal2 spacing : 0.14um"),
    ("M3.1", "min. metal3 width : 0.30um"),
    ("M3.2", "min. metal3 spacing : 0.30um"),
    ("M4.1", "min. metal4 width : 0.30um"),
    ("M4.2", "min. metal4 spacing : 0.30um"),
    ("M5.1", "min. metal5 width : 1.60um"),
    ("M5.2", "min. metal5 spacing : 1.60um"),
    ("V1.1", "min/max via1 size : 0.15um"),
    ("V1.2", "min. via1 spacing : 0.17um"),
    ("V1.3", "metal1 enclosure of via1 : 0.055um"),
    ("V2.1", "min/max via2 size : 0.20um"),
    ("V2.2", "min. via2 spacing : 0.20um"),
    ("V2.3", "metal2 enclosure of via2 : 0.04um"),
    ("V3.1", "min/max via3 size : 0.20um"),
    ("V3.4", "metal3 enclosure of via3 : 0.06um"),
    ("V4.1", "min/max via4 size : 0.80um"),
    ("V4.4", "metal4 enclosure of via4 : 0.19um"),
    ("C1.1", "min. licon size : 0.17um"),
    ("C1.2", "min. licon spacing : 0.17um"),
    ("C1.3", "poly enclosure of licon : 0.05um"),
    ("P1.1", "min. poly width : 0.15um"),
    ("P1.2", "min. poly spacing : 0.21um"),
    ("D1.1", "min. metal1 density over the die : 0.30"),
    ("D1.2", "max. metal1 density over the die : 0.70"),
    ("D2.1", "min. metal2 density over the die : 0.30"),
    ("D2.2", "max. metal2 density over the die : 0.70"),
    ("D3.1", "min. metal3 density over the die : 0.30"),
    ("ANT.1", "max. antenna ratio for metal1 : 400"),
    ("ANT.2", "max. antenna ratio for metal2 : 400"),
    ("ANT.3", "antenna gate-oxide area ratio for metal3 : 400"),
    ("ANT.4", "antenna gate-oxide area ratio for metal4 : 400"),
    ("NW.1", "min. nwell width : 0.84um"),
    ("NW.2", "min. nwell spacing : 1.27um"),
    ("DN.1", "min. deep nwell enclosure of nwell : 0.40um"),
    ("TAP.1", "max. distance from a tap to any diff : 15.0um"),
    ("SEAL.1", "min. seal ring enclosure of the core : 5.0um"),
]


def _rdb(top: str = "chip_top") -> str:
    cats = "".join(
        f"  <category>\n   <name>{n}</name>\n"
        f"   <description>{n} : {d}</description>\n"
        f"   <categories>\n   </categories>\n  </category>\n"
        for n, d in _RULES)
    return (f'<?xml version="1.0" encoding="utf-8"?>\n'
            f"<report-database>\n <description/>\n <original-file/>\n"
            f" <generator>drc: script='{DECK}'</generator>\n"
            f" <top-cell>{top}</top-cell>\n <tags>\n </tags>\n"
            f" <categories>\n{cats} </categories>\n"
            f" <cells>\n  <cell>\n   <name>{top}</name>\n   <variant/>\n"
            f"   <layout-name/>\n   <references>\n   </references>\n"
            f"  </cell>\n </cells>\n <items>\n </items>\n"
            f"</report-database>\n")


RDB = _rdb()

LOG = ("klayout -b -r " + DECK + "\n"
       "Running DRC on chip_top\n"
       "Elapsed: 12.3s\n")


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return "sha256:" + h.hexdigest()


def _tree(tmp_path: Path, *, ledger: bool = True, measured: bool = True,
          exit_code: int = 0, tool: str = "klayout",
          bind_gds: bool = True, mutate_gds_after: bool = False,
          with_transcript: bool = True) -> Path:
    """run8's shape, reduced to the properties under test."""
    p = tmp_path / "proj"
    for rel, text in ((CANON_RPT, RDB), (GDS, "GDS-STAND-IN\n")):
        (p / rel).parent.mkdir(parents=True, exist_ok=True)
        (p / rel).write_text(text)
    if with_transcript:
        (p / CANON_LOG).write_text(LOG)
    mirror = p / MIRROR
    mirror.parent.mkdir(parents=True, exist_ok=True)
    # the collector's real shape: a symlink, and NO transcript beside it
    mirror.symlink_to(Path("../../../../") / CANON_RPT)
    if ledger:
        row = {
            "record": "invocation", "tool": tool,
            "version": "KLayout 0.30.12", "version_capture": "probed",
            "command": f"klayout -b -r {DECK} (sign-off DRC)",
            "exec_route": "container", "exit_code": exit_code,
            "duration_ms": 12300, "duration_s": 12.3, "measured": measured,
            "timestamp": "2026-09-21T00:00:00Z",
            "outputs": {CANON_RPT: _sha(p / CANON_RPT)},
        }
        if with_transcript:
            row["outputs"][CANON_LOG] = _sha(p / CANON_LOG)
        if bind_gds:
            row["inputs"] = {GDS: _sha(p / GDS)}
        (p / "provenance.jsonl").write_text(json.dumps(row) + "\n")
    if mutate_gds_after:
        (p / GDS).write_text("GDS-STAND-IN, RESTREAMED\n")
    return p


def _aliases(p: Path):
    return {"canonical": p / CANON_RPT, "steps-symlink": p / MIRROR}


def _corroborated(p: Path, alias: Path) -> int:
    return len(A._measured_klayout_receipt_files(p, [alias]))


# ── 1. the claim: one physical file, one answer ──────────────────────────
@pytest.mark.parametrize("which", ["canonical", "steps-symlink"])
def test_every_alias_of_one_report_gets_the_same_receipt_answer(
        tmp_path, which):
    p = _tree(tmp_path)
    alias = _aliases(p)[which]
    assert alias.resolve() == (p / CANON_RPT).resolve(), "same physical file"
    assert _corroborated(p, alias) == 1, (
        f"{which}: the ledger row is measured, exit 0, and binds both outputs "
        f"and the GDS by digest")


def test_the_collector_really_publishes_no_transcript_beside_the_mirror(
        tmp_path):
    """The premise of the defect, asserted so the fixture cannot drift into
    one where the bug could not happen."""
    p = _tree(tmp_path)
    assert (p / MIRROR).is_symlink()
    assert not (p / MIRROR).with_suffix(".log").exists()
    assert (p / CANON_LOG).is_file()


# ── 2. end to end: the sign-off verdict through the alias run8 drew ──────
@pytest.mark.parametrize("which", ["canonical", "steps-symlink"])
def test_a_measured_zero_is_certified_through_either_alias(
        tmp_path, which, monkeypatch):
    p = _tree(tmp_path)
    alias = _aliases(p)[which]
    # `_discover` picks between two inode-identical aliases by `os.scandir`
    # order. Pinning the choice fixes the COIN, not the behaviour under test:
    # the audit still reads the same bytes, ledger and GDS either way.
    monkeypatch.setattr(A, "_discover", lambda *a, **k: [alias])
    with A.scoped_discovery([p / CANON_RPT]):
        res = A._check_drc(p)
    rules = {f.rule for f in res.findings}
    assert "DRC_ZERO_NOT_MEASURED" not in rules, [f.message for f in res.findings]
    assert res.summary["receipt_corroborated_files"] == 1, res.summary
    assert res.passed, [f.message for f in res.findings]


# ── 3. a fabricated zero is still refused, through EITHER alias ──────────
@pytest.mark.parametrize("which", ["canonical", "steps-symlink"])
def test_a_report_with_no_run_behind_it_stays_not_measured(tmp_path, which,
                                                           monkeypatch):
    p = _tree(tmp_path, ledger=False)
    alias = _aliases(p)[which]
    assert _corroborated(p, alias) == 0
    monkeypatch.setattr(A, "_discover", lambda *a, **k: [alias])
    with A.scoped_discovery([p / CANON_RPT]):
        res = A._check_drc(p)
    assert "DRC_ZERO_NOT_MEASURED" in {f.rule for f in res.findings}
    assert res.summary["receipt_corroborated_files"] == 0
    assert not res.passed


@pytest.mark.parametrize("which", ["canonical", "steps-symlink"])
@pytest.mark.parametrize("kw,why", [
    ({"measured": False}, "a back-filled declaration is not a measurement"),
    ({"exit_code": 1}, "a non-zero tool exit is not a clean run"),
    ({"tool": "openroad"}, "the router's projection is not the sign-off deck"),
    ({"bind_gds": False}, "a row that binds no layout certifies no layout"),
    ({"mutate_gds_after": True}, "the GDS changed since the deck ran"),
    ({"with_transcript": False}, "no command transcript, no receipt"),
])
def test_every_leg_of_the_contract_still_refuses(tmp_path, which, kw, why):
    p = _tree(tmp_path, **kw)
    assert _corroborated(p, _aliases(p)[which]) == 0, why


@pytest.mark.parametrize("which", ["canonical", "steps-symlink"])
def test_a_report_rewritten_after_the_row_was_written_is_refused(tmp_path,
                                                                 which):
    """The digest is the binding. Edit the report and the receipt dies."""
    p = _tree(tmp_path)
    assert _corroborated(p, _aliases(p)[which]) == 1
    (p / CANON_RPT).write_text(_rdb("other_top"))
    assert _corroborated(p, _aliases(p)[which]) == 0


@pytest.mark.parametrize("which", ["canonical", "steps-symlink"])
def test_a_transcript_rewritten_after_the_row_was_written_is_refused(
        tmp_path, which):
    p = _tree(tmp_path)
    assert _corroborated(p, _aliases(p)[which]) == 1
    (p / CANON_LOG).write_text(LOG + "and one more line\n")
    assert _corroborated(p, _aliases(p)[which]) == 0


def test_an_alias_pointing_outside_the_project_is_refused(tmp_path):
    """The resolve must not become a way out of the project."""
    p = _tree(tmp_path)
    outside = tmp_path / "outside.rpt"
    outside.write_text(RDB)
    stray = p / "reports" / "phase3" / "stray_drc.rpt"
    stray.symlink_to(outside)
    assert _corroborated(p, stray) == 0
