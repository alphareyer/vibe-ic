#!/usr/bin/env python3
"""_publication_identity.py — which flow implemented a run, and therefore which publication slot it may take (llv1 W20).

Decision 25: a run implemented under a flag (`--librelane`) is never published
as, and never overwrites, the vibe-ic product result. A benchmark cell is named
``v<version>_<pdk>``; that name IS the product result's identity, and the page
generator renders the newest one per (IC, PDK). So a flagged run takes its own
slot, ``v<version>_<pdk>_<impl>``, carries an ``IMPL.json`` saying what it is,
and a request to stage it into the default slot refuses by name.

WHO DECIDES
-----------
The W0 mode record (``.vibeic-state/impl-mode-v1.json``, `_impl_flow`) is the
answer when it exists. A run directory can reach the publisher without it (the
record is runtime state and a copied tree may drop it), so every other place a
flagged run leaves its mode is read as well, and each NON-DEFAULT claim is
evidence:

  * the admission ledger's ``dispatch_config.impl`` (`_impl_flow.admitted_impls`);
  * the external-flow import manifest (``reports/phase3/impl/import_manifest.json``,
    written only by a flagged import) and the ``flow`` of each of its rows;
  * ``provenance.jsonl`` rows ``attributed_to`` an external flow (W19's
    witnessed rows);
  * an ``impl`` field in ``reports/orchestrator/*.json`` (W14).

Only non-default claims count: an absent ``impl`` in a ledger row reads as the
default, and counting that would make a flagged project written before W2
contradict itself. With a record, every claim must name the record's mode; with
no record, the claims must agree on one mode; no claim at all is the default.
Anything else refuses. Nothing here guesses the default from a damaged record.

The default path is unchanged: no record and no claim -> ``vibe-ic``, the slot
name is ``v<version>_<pdk>`` exactly as before, and no ``IMPL.json`` is written.

chip-AGNOSTIC: no design, PDK or tool literal selects a branch; the mode names
are `_impl_flow`'s.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_PROGRAMS_DIR = str(Path(__file__).resolve().parent)
if _PROGRAMS_DIR not in sys.path:
    sys.path.insert(0, _PROGRAMS_DIR)

import _impl_flow  # noqa: E402

CELL_IMPL_FILE = "IMPL.json"
CELL_IMPL_SCHEMA = "vibe-ic/cell-impl/1"
#: `_external_flow_manifest.MANIFEST_REL`, spelled here so the default path
#: imports no importer code (the same reason `_impl_flow` spells the ledger).
IMPORT_MANIFEST_REL = "reports/phase3/impl/import_manifest.json"
ORCHESTRATOR_REPORTS_REL = "reports/orchestrator"

PUBLISH_IMPL_UNREADABLE = "PUBLISH_IMPL_UNREADABLE"
PUBLISH_IMPL_CONFLICT = "PUBLISH_IMPL_CONFLICT"
PUBLISH_FLAGGED_INTO_DEFAULT_SLOT = "PUBLISH_FLAGGED_INTO_DEFAULT_SLOT"
REASON_CLASSES = (PUBLISH_IMPL_UNREADABLE, PUBLISH_IMPL_CONFLICT,
                  PUBLISH_FLAGGED_INTO_DEFAULT_SLOT)

_FLAGGED = tuple(m for m in _impl_flow.IMPLS if m != _impl_flow.IMPL_DEFAULT)


class IdentityRefusal(Exception):
    """A named refusal. ``str()`` starts with the reason class."""

    def __init__(self, reason_class: str, detail: str) -> None:
        super().__init__(f"{reason_class}: {detail}")
        self.reason_class = reason_class


def _load(path: Path, what: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IdentityRefusal(PUBLISH_IMPL_UNREADABLE,
                              f"{what} {path} cannot be read ({exc}), so the flow "
                              "that implemented this run cannot be proven") from exc


def _claims(run_dir: Path) -> List[Tuple[str, str]]:
    """(mode, where) for every non-default mode the run's artefacts claim,
    other than the record."""
    claims: List[Tuple[str, str]] = []
    try:
        for mode in sorted(_impl_flow.admitted_impls(run_dir) - {_impl_flow.IMPL_DEFAULT}):
            claims.append((mode, f"{_impl_flow.STATE_DIR}/{_impl_flow.ADMISSION_LEDGER} "
                                 "dispatch_config.impl"))
    except _impl_flow.ImplRefusal as exc:
        raise IdentityRefusal(PUBLISH_IMPL_UNREADABLE, str(exc)) from exc
    manifest = run_dir / IMPORT_MANIFEST_REL
    if manifest.exists():
        doc = _load(manifest, "import manifest")
        doc = doc if isinstance(doc, dict) else {}
        rows = doc.get("rows") if isinstance(doc.get("rows"), list) else []
        flows = sorted(({doc.get("flow")} | {r.get("flow") for r in rows if isinstance(r, dict)})
                       - {None})
        # the manifest exists only on a flagged import: one that names no
        # flow still claims SOME external flow, and cannot be read as default
        for flow in flows or ["(the manifest names no flow)"]:
            claims.append((str(flow), IMPORT_MANIFEST_REL))
    prov = run_dir / "provenance.jsonl"
    if prov.is_file():
        try:
            lines = prov.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            raise IdentityRefusal(PUBLISH_IMPL_UNREADABLE, f"{prov}: {exc}") from exc
        counted: Dict[str, int] = {}
        for raw in lines:
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            flow = row.get("attributed_to") if isinstance(row, dict) else None
            if flow in _FLAGGED:
                counted[flow] = counted.get(flow, 0) + 1
        for flow, n in sorted(counted.items()):
            claims.append((flow, f"provenance.jsonl attributed_to ({n} row(s))"))
    reports = run_dir / ORCHESTRATOR_REPORTS_REL
    if reports.is_dir():
        for path in sorted(reports.glob("*.json")):
            doc = _load(path, "orchestrator report")
            impl = doc.get("impl") if isinstance(doc, dict) else None
            if impl not in (None, _impl_flow.IMPL_DEFAULT):
                claims.append((str(impl), f"{path.relative_to(run_dir)} impl"))
    return claims


def run_identity(run_dir: Path) -> Dict[str, Any]:
    """The flow that implemented ``run_dir``: ``{"impl", "source", "evidence"}``.

    ``source`` is ``record``, ``evidence`` (no record; the claims agree) or
    ``default`` (no record and no claim). Refuses rather than guess.
    """
    run_dir = Path(run_dir)
    try:
        record = _impl_flow.read_record(run_dir)
    except _impl_flow.ImplRefusal as exc:
        raise IdentityRefusal(PUBLISH_IMPL_UNREADABLE, str(exc)) from exc
    claims = _claims(run_dir)
    evidence = [{"impl": m, "where": w} for m, w in claims]
    if record is not None:
        where = f"{_impl_flow.STATE_DIR}/{_impl_flow.RECORD_NAME}"
        against = [c for c in claims if c[0] != record["impl"]]
        if against:
            raise IdentityRefusal(
                PUBLISH_IMPL_CONFLICT,
                f"the mode record ({where}) says '{record['impl']}' but "
                + "; ".join(f"{w} says '{m}'" for m, w in against))
        return {"impl": record["impl"], "source": "record",
                "evidence": [{"impl": record["impl"], "where": where}] + evidence}
    modes = sorted({m for m, _w in claims})
    if len(modes) > 1:
        raise IdentityRefusal(
            PUBLISH_IMPL_CONFLICT,
            "no mode record, and the run's artefacts claim different flows: "
            + "; ".join(f"{w} says '{m}'" for m, w in claims))
    if modes:
        if modes[0] not in _FLAGGED:
            raise IdentityRefusal(
                PUBLISH_IMPL_CONFLICT,
                f"the run's artefacts claim '{modes[0]}', which is not a flow mode "
                f"({', '.join(_impl_flow.IMPLS)}): "
                + "; ".join(w for _m, w in claims))
        return {"impl": modes[0], "source": "evidence", "evidence": evidence}
    return {"impl": _impl_flow.IMPL_DEFAULT, "source": "default", "evidence": []}


def check_declared(identity: Dict[str, Any], declared: Optional[str]) -> None:
    """Refuse a declared mode (the publisher's ``--impl``) the run contradicts.

    Declaring the default for a flagged run is the one request decision 25
    exists to stop, so it has its own name.
    """
    if declared is None:
        return
    try:
        want = _impl_flow.normalise(declared)
    except _impl_flow.ImplRefusal as exc:
        raise IdentityRefusal(PUBLISH_IMPL_CONFLICT, str(exc)) from exc
    have = identity["impl"]
    if want == have:
        return
    if want == _impl_flow.IMPL_DEFAULT:
        raise IdentityRefusal(
            PUBLISH_FLAGGED_INTO_DEFAULT_SLOT,
            f"this run was implemented under {_impl_flow.FLAG_FOR.get(have, have)} "
            f"(mode '{have}', from {identity['source']}); decision 25: a flagged "
            "run is never published as, and never overwrites, the vibe-ic product "
            "result, so it cannot be staged into the default slot")
    raise IdentityRefusal(
        PUBLISH_IMPL_CONFLICT,
        f"--impl {declared!r} but the run shows mode '{have}' (from {identity['source']})")


def slot_name(version: str, pdk: str, impl: str) -> str:
    """The cell directory: ``v<version>_<pdk>`` for the default flow (unchanged),
    ``v<version>_<pdk>_<impl>`` for a flagged one."""
    base = f"v{version}_{pdk}"
    return base if impl == _impl_flow.IMPL_DEFAULT else f"{base}_{impl}"


def cell_record(identity: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The ``IMPL.json`` a flagged cell carries; None for the default flow,
    whose cells stay exactly as they were."""
    impl = identity["impl"]
    if impl == _impl_flow.IMPL_DEFAULT:
        return None
    return {"schema": CELL_IMPL_SCHEMA, "impl": impl,
            "flag": _impl_flow.FLAG_FOR.get(impl), "source": identity["source"],
            "evidence": identity["evidence"], "product_result": False,
            "reason": ("decision 25: a run implemented under a flag is never "
                       "published as, and never overwrites, the vibe-ic product "
                       "result")}
