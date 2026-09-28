#!/usr/bin/env python3
"""reused_ip_erratum.py — apply a pinned upstream ERRATUM to the STAGED copy of
a reused IP, as a DISCLOSED deviation. Never a hand edit, never on the input.

ENFORCEMENT: producer. It changes the staged RTL copy only when a reviewed
erratum RECORD says so, and it writes the record of what it did. It never
returns a verdict about the design.

WHY THIS EXISTS — the measurement, not a theory.

MEASURED (lane isaprod, 2026-09-28, image vibeic-eda 0.3.84): a reused CPU IP
pinned at its release zeroes the read of the wrong register. The public
RISC-V architecture suite, judged word for word against the reference model,
matched on 9 of 40 programs with the pinned file and on 40 of 40 with the
one-line fix its upstream landed later; on a non-zero SRAM power-up the pinned
file hangs even the smallest self-checking program. The owner ruled (B): apply
the upstream fix as a DISCLOSED deviation and keep the unmodified result as
evidence.

A hand edit of the staged file would be the wrong mechanism three ways: it
cannot be reviewed as data, it silently diverges from the input, and nothing
proves the bytes are the upstream's. So the deviation is DATA — one JSON record
per erratum under `ip-catalog/**/errata/` — and this program is the only thing
that acts on it:

  * WHEN. A record applies only to a design whose `declaration.json`
    `ip_upstream` names the record's upstream AT the record's pinned commit.
    No design name, no IC class, no file-name guess.
  * WHAT. The staged file must hash to `sha256_before`. Anything else is
    REFUSED BY NAME — there is no fuzzy apply. The replacement bytes are
    FETCHED from the upstream at the fix commit (never retyped) and must hash
    to `sha256_after`, or they are refused too.
  * WHERE. Only the staged copy under `phase2/stage1/rtl/`. The input tree is
    read, never written.
  * DISCLOSED. The flow record (`reports/phase2/reused_ip_errata.json`), one
    `provenance.jsonl` line and the final summary each carry one line per
    applied erratum; the ISA producer keeps the unmodified-RTL result as its
    arm A.

Without a matching record nothing changes: the staged RTL is the supplied RTL.
The policy switch (`isa_suite_policy.json` `reused_ip_errata`) turns the
mechanism off for ruling A in one line.

chip-AGNOSTIC: sha256, a declared upstream pointer and a flat staging
directory. No IP, chip, vendor or PDK literal appears in the logic; the one IP
this was measured on appears only in its record.
"""
from __future__ import annotations

# --- sibling-import path (vibe-ic#2104) ------------------------------------
import os as _os                                                    # noqa: E402
import sys as _sys                                                  # noqa: E402

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
# ---------------------------------------------------------------------------

import argparse
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import _atomic_artefact as _aa

SCHEMA = "vibeic.reused_ip_erratum.v1"
RECORD_SCHEMA = "vibeic.reused_ip_errata_applied.v1"
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ERRATA_GLOB = "ip-catalog/**/errata/*.json"
POLICY_PATH = Path(__file__).resolve().parent / "isa_suite_policy.json"
FLOW_RECORD_REL = "reports/phase2/reused_ip_errata.json"
STAGED_RTL_REL = "phase2/stage1/rtl"

APPLIED = "APPLIED"
ALREADY_APPLIED = "ALREADY_APPLIED"
REFUSED = "REFUSED"
NOT_APPLIED = "NOT_APPLIED"
NOT_APPLICABLE = "NOT_APPLICABLE"

#: The fields a record must carry. A record missing any of them is refused as
#: a whole: an erratum whose before/after hashes are not both stated cannot be
#: applied without a fuzzy step.
REQUIRED = ("ip", "pinned_version", "upstream", "pinned_commit", "file",
            "sha256_before", "fix_commit", "fix_url", "sha256_after",
            "erratum", "reason", "owner_ruling")

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
#: `<host/owner/repo>@<commit>` as a declaration's `ip_upstream` value spells
#: it; anything after the commit (a licence in parentheses) is ignored.
_UPSTREAM_RE = re.compile(r"(?P<repo>[\w.\-]+(?:/[\w.\-]+)+)@(?P<commit>[0-9a-f]{7,40})")

#: The fetch deadline, in seconds. A fetch of one source file is a network
#: round trip, not a long tool; a stalled socket is refused, never waited on.
FETCH_DEADLINE_S = 60
# A pinned fix is one RTL source file, not a suite archive. Keep the socket
# operation short so a stalled read cannot consume the whole overall deadline.
FETCH_MAX_BYTES = 8 * 1024 * 1024
FETCH_CHUNK_BYTES = 64 * 1024
FETCH_IO_TIMEOUT_S = 1


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def policy(path: Path = POLICY_PATH) -> Dict[str, Any]:
    try:
        doc = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def errata_enabled(pol: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    pol = policy() if pol is None else pol
    mode = pol.get("reused_ip_errata")
    if mode == "apply":
        return True, "policy reused_ip_errata=apply (ruling B)"
    if mode == "report_supplied":
        return False, "policy reused_ip_errata=report_supplied (ruling A)"
    return False, (f"policy reused_ip_errata={mode!r} is neither 'apply' nor "
                   f"'report_supplied' — no erratum applied")


def load_errata(root: Path = PLUGIN_ROOT) -> Tuple[List[Dict[str, Any]],
                                                   List[Dict[str, str]]]:
    """`(records, refusals)` over every erratum record the plugin ships."""
    records: List[Dict[str, Any]] = []
    refusals: List[Dict[str, str]] = []
    for p in sorted(Path(root).glob(ERRATA_GLOB)):
        try:
            doc = json.loads(p.read_text())
        except (OSError, ValueError) as exc:
            refusals.append({"record": str(p), "why": f"unreadable: {exc!r}"})
            continue
        if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
            refusals.append({"record": str(p), "why": "not a " + SCHEMA})
            continue
        missing = [k for k in REQUIRED if not doc.get(k)]
        bad = [k for k in ("sha256_before", "sha256_after")
               if not _SHA_RE.match(str(doc.get(k, "")))]
        if missing or bad:
            refusals.append({"record": str(p),
                             "why": f"missing {missing} / malformed {bad}"})
            continue
        records.append(dict(doc, _record_path=str(p)))
    return records, refusals


def _declaration(project: Path) -> Dict[str, Any]:
    try:
        obj = json.loads((Path(project) / "plugin_output"
                          / "declaration.json").read_text(errors="replace"))
    except (OSError, ValueError):
        return {}
    if not isinstance(obj, dict):
        return {}
    return obj.get("fields") if isinstance(obj.get("fields"), dict) else obj


def declared_upstreams(project: Path) -> List[Tuple[str, str]]:
    """`(repo, commit)` for every reused IP the design declares by pointer."""
    raw = _declaration(project).get("ip_upstream")
    values: List[str] = []
    if isinstance(raw, dict):
        values = [str(v) for v in raw.values()]
    elif isinstance(raw, list):
        values = [str(v) for v in raw]
    elif isinstance(raw, str):
        values = [raw]
    out = []
    for v in values:
        m = _UPSTREAM_RE.search(v)
        if m:
            out.append((m.group("repo").lower(), m.group("commit").lower()))
    return out


def _norm_repo(repo: str) -> str:
    r = repo.lower().strip()
    r = re.sub(r"^https?://", "", r)
    return r[:-4] if r.endswith(".git") else r


def record_applies(record: Dict[str, Any],
                   upstreams: List[Tuple[str, str]]) -> bool:
    """The design declares the record's upstream AT the record's pinned commit.
    A short declared commit matches as a prefix of the full pinned one."""
    want_repo = _norm_repo(record["upstream"])
    pin = str(record["pinned_commit"]).lower()
    return any(_norm_repo(repo) == want_repo and pin.startswith(commit)
               for repo, commit in upstreams)


def default_fetch(url: str) -> bytes:
    import urllib.request
    deadline = time.monotonic() + FETCH_DEADLINE_S
    chunks: List[bytes] = []
    total = 0
    with urllib.request.urlopen(  # noqa: S310 — hash-pinned upstream fix
            url, timeout=min(FETCH_IO_TIMEOUT_S, FETCH_DEADLINE_S)) as r:
        # read1 returns available bytes without waiting for a full chunk.
        # A trickling peer must still cross the monotonic deadline.
        read1 = r.read1
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"overall fetch deadline {FETCH_DEADLINE_S}s exceeded")
            chunk = read1(min(FETCH_CHUNK_BYTES, FETCH_MAX_BYTES - total + 1))
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"overall fetch deadline {FETCH_DEADLINE_S}s exceeded")
            if not chunk:
                return b"".join(chunks)
            total += len(chunk)
            if total > FETCH_MAX_BYTES:
                raise ValueError(f"RTL fix byte ceiling {FETCH_MAX_BYTES} exceeded")
            chunks.append(chunk)


def _input_original(project: Path, basename: str) -> Optional[Path]:
    """The design-INPUT file the staged copy came from (read only)."""
    root = Path(project) / "input"
    if not root.is_dir():
        return None
    hits = sorted(p for p in root.rglob(basename) if p.is_file())
    return hits[0] if len(hits) == 1 else None


def disclosure_line(record: Dict[str, Any],
                    unmodified_result: Optional[str] = None) -> str:
    """The one line every downstream record carries for an applied erratum."""
    evidence = (f"unmodified-RTL ISA result {unmodified_result} retained as "
                f"evidence" if unmodified_result else
                "unmodified-RTL result retained as evidence (ISA producer "
                "arm A)")
    return (f"deviation from {record['ip']} {record['pinned_version']}: "
            f"upstream {str(record['fix_commit'])[:10]} applied (erratum "
            f"{record['erratum']}); {evidence}")


def apply_errata(project: Path, rtl_dir: Optional[Path] = None, *,
                 fetch: Optional[Callable[[str], bytes]] = None,
                 root: Path = PLUGIN_ROOT,
                 pol: Optional[Dict[str, Any]] = None,
                 write_record: bool = True) -> Dict[str, Any]:
    """Apply every erratum whose record binds this design to its staged RTL.

    Returns the flow record (also written to `FLOW_RECORD_REL` when anything
    was considered). Each row's `status` is one of APPLIED, ALREADY_APPLIED,
    REFUSED (named), NOT_APPLIED (the fix could not be fetched — the staged
    file is left as supplied and the row says so) or NOT_APPLICABLE."""
    project = Path(project)
    rtl_dir = Path(rtl_dir) if rtl_dir is not None else project / STAGED_RTL_REL
    fetch = fetch or default_fetch
    enabled, why = errata_enabled(pol)
    doc: Dict[str, Any] = {"schema": RECORD_SCHEMA, "policy": why,
                           "rows": [], "record_refusals": [],
                           "disclosures": []}
    records, refusals = load_errata(root)
    doc["record_refusals"] = refusals
    upstreams = declared_upstreams(project)
    for rec in records:
        if not record_applies(rec, upstreams):
            continue
        base = Path(rec["file"]).name
        staged = rtl_dir / base
        src = _input_original(project, base)
        row: Dict[str, Any] = {
            "ip": rec["ip"], "pinned_version": rec["pinned_version"],
            "upstream": rec["upstream"], "pinned_commit": rec["pinned_commit"],
            "file": rec["file"], "staged_file": str(staged),
            "input_file": str(src) if src else None,
            "sha256_before": rec["sha256_before"],
            "sha256_after": rec["sha256_after"],
            "fix_commit": rec["fix_commit"], "fix_url": rec["fix_url"],
            "erratum": rec["erratum"], "reason": rec["reason"],
            "owner_ruling": rec["owner_ruling"],
            "record": rec["_record_path"],
        }
        if not enabled:
            row.update(status=NOT_APPLIED, why=why)
            doc["rows"].append(row)
            continue
        if not staged.is_file():
            row.update(status=NOT_APPLICABLE,
                       why=f"{base} is not staged under {rtl_dir}")
            doc["rows"].append(row)
            continue
        current = sha256_bytes(staged.read_bytes())
        row["sha256_staged_found"] = current
        if current == rec["sha256_after"]:
            row.update(status=ALREADY_APPLIED,
                       why="staged file already hashes to sha256_after")
        elif current != rec["sha256_before"]:
            row.update(status=REFUSED, why=(
                f"REFUSED: staged {base} hashes to {current}, but erratum "
                f"{Path(rec['_record_path']).name} is stated against "
                f"{rec['sha256_before']} — no fuzzy apply"))
        else:
            try:
                fixed = fetch(rec["fix_url"])
            except Exception as exc:  # noqa: BLE001 — every fetch failure
                row.update(status=NOT_APPLIED, why=(
                    f"could not fetch {rec['fix_url']}: {exc!r} — the staged "
                    f"file is the supplied one, unchanged"))
                fixed = None
            if fixed is not None:
                got = sha256_bytes(fixed)
                if got != rec["sha256_after"]:
                    row.update(status=REFUSED, why=(
                        f"REFUSED: the fetched fix hashes to {got}, not the "
                        f"record's sha256_after {rec['sha256_after']}"))
                else:
                    _aa.write_bytes(staged, fixed)
                    row.update(status=APPLIED,
                               why="staged copy replaced with the upstream "
                                   "file at the fix commit (sha256 verified "
                                   "before and after)")
        if row["status"] in (APPLIED, ALREADY_APPLIED):
            row["disclosure"] = disclosure_line(rec)
            doc["disclosures"].append(row["disclosure"])
        doc["rows"].append(row)
    if write_record and (doc["rows"] or refusals):
        _aa.write_json(project / FLOW_RECORD_REL, doc)
        if doc["disclosures"] or any(r["status"] == REFUSED
                                     for r in doc["rows"]):
            _append_provenance(project, doc)
    return doc


def _append_provenance(project: Path, doc: Dict[str, Any]) -> None:
    """One provenance.jsonl line per considered erratum (append-only log)."""
    prov = Path(project) / "provenance.jsonl"
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    lines = []
    for row in doc["rows"]:
        if row["status"] not in (APPLIED, ALREADY_APPLIED, REFUSED):
            continue
        lines.append(json.dumps({
            "record": "deviation", "produced_by": "reused_ip_erratum",
            "status": row["status"], "file": row["staged_file"],
            "sha256_before": row["sha256_before"],
            "sha256_after": row["sha256_after"],
            "fix_commit": row["fix_commit"],
            "disclosure": row.get("disclosure") or row.get("why"),
            "timestamp": stamp}, sort_keys=True))
    if lines:
        with prov.open("a") as fh:  # append-only log, not a declared report
            fh.write("\n".join(lines) + "\n")


def read_flow_record(project: Path) -> Dict[str, Any]:
    try:
        doc = json.loads((Path(project) / FLOW_RECORD_REL).read_text())
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def disclosure_lines(project: Path) -> List[str]:
    """The final-summary lines: each applied erratum, with the unmodified-RTL
    ISA result filled in from the ISA producer's receipt when it exists."""
    doc = read_flow_record(project)
    unmodified = None
    try:
        import isa_suite_producer as _isa
        unmodified = _isa.unmodified_arm_summary(project)
    except Exception:  # noqa: BLE001 — the line is still disclosed without it
        unmodified = None
    out = []
    for row in doc.get("rows") or []:
        if row.get("status") in (APPLIED, ALREADY_APPLIED):
            out.append(disclosure_line(row, unmodified))
        elif row.get("status") == REFUSED:
            out.append(f"erratum REFUSED for {row.get('ip')} "
                       f"{row.get('pinned_version')}: {row.get('why')}")
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("project", type=Path)
    ap.add_argument("--rtl-dir", type=Path, default=None)
    ns = ap.parse_args(argv)
    doc = apply_errata(ns.project.resolve(), ns.rtl_dir)
    for row in doc["rows"]:
        print(f"[{row['status']}] {row['ip']} {row['pinned_version']} "
              f"{row['file']}: {row.get('why')}")
    for line in doc["disclosures"]:
        print(f"DISCLOSED {line}")
    if not doc["rows"]:
        print("[NOT_APPLICABLE] no erratum record binds this design's "
              "declared reused IP")
    return 1 if any(r["status"] == REFUSED for r in doc["rows"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
