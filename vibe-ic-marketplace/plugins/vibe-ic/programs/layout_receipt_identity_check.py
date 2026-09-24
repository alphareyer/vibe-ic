#!/usr/bin/env python3
"""Refuse release credit for diagnostic or stale Phase-3 layout receipts."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _layout_sha(gds: Path, routed_def: Path) -> str:
    digest = hashlib.sha256()
    for path in (gds, routed_def):
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def check(project: Path) -> dict:
    receipt_path = project / "reports/phase3/layout_receipts.json"
    prestream_path = project / "reports/phase3/prestream_gate.json"
    reasons = []
    try:
        receipt = json.loads(receipt_path.read_text())
        prestream = json.loads(prestream_path.read_text())
    except (OSError, ValueError) as exc:
        return {"verdict": "NOT_MEASURED", "reasons": [f"receipt unreadable: {exc}"]}
    frozen = receipt.get("frozen_layout_digest")
    if not frozen or prestream.get("verdict") != "PASS":
        reasons.append("pre-stream gate did not admit the routed layout")
    if (prestream.get("layout_digest") != receipt.get("prestream_basis_digest")
            or receipt.get("prestream_basis_digest") != receipt.get(
                "current_basis_digest")):
        reasons.append("routed DEF/netlist/SDC/PDK basis differs from pre-stream gate")
    if receipt.get("release_scope") == "DIAGNOSTIC_ONLY" or receipt.get(
            "release_verdict") != "ELIGIBLE_FOR_AUDIT":
        reasons.append("diagnostic or failed upstream layout has no release credit")
    if frozen != receipt.get("current_layout_digest"):
        reasons.append("frozen layout identity no longer matches current identity")
    rows = receipt.get("receipts")
    if not isinstance(rows, list) or not rows:
        reasons.append("no final layout receipts")
    else:
        for row in rows:
            if not isinstance(row, dict) or (row.get("extras") or {}).get(
                    "layout_digest") != frozen:
                reasons.append("a final receipt has another layout identity")
                break
    pnr = project / "phase3/stage3/pnr"
    gds_rel = receipt.get("gds_relpath")
    gds = project / gds_rel if isinstance(gds_rel, str) and gds_rel.startswith(
        "phase3/stage3/pnr/") and ".." not in Path(gds_rel).parts else None
    for kind, path in (("gds", gds),
                       ("def", pnr / "routed.def")):
        if path is None or not path.is_file():
            reasons.append(f"shipped {kind} absent")
        elif _sha(path) != receipt.get(f"{kind}_sha256"):
            reasons.append(f"shipped {kind} changed after receipts")
    if gds is not None and gds.is_file() and (pnr / "routed.def").is_file():
        if _layout_sha(gds, pnr / "routed.def") != frozen:
            reasons.append("shipped GDS + DEF digest differs from frozen identity")
    if gds is not None:
        canonical = project / "phase3/stage4/gds" / gds.name
        if not canonical.is_file() or _sha(canonical) != receipt.get("gds_sha256"):
            reasons.append("canonical delivered GDS differs from frozen stream")
    return {"verdict": "FAIL" if reasons else "PASS", "reasons": reasons,
            "frozen_layout_digest": frozen}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    result = check(args.project)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, indent=2) + "\n")
    print(f"layout_receipt_identity_check: {result['verdict']}: "
          + "; ".join(result["reasons"]))
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
