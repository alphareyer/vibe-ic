#!/usr/bin/env python3
"""renamed_interface_derive — the pad side of a reused IP's renamed ports.

WHAT THIS ANSWERS
=================
A reused IP keeps its own port names. The design's documents name the same
interface with ILLUSTRATIVE names and give each FAMILY a pad side in prose
("N | SRAM data bus", "S | SRAM addr + control(we / cyc)"). The pad producer
(step 15.5ic) and the step-2 pad budget carry a group row's side to an
implemented port only through a pair in `phase2/stage1/rtl/SOURCE_MANIFEST.json`.
MEASURED on subservient x gf180mcuD (v1.25.64): the manifest emitters wrote
`renamed_interfaces: []`, so 15.5ic refused PAD_GROUP_UNRESOLVED and the pairs
had to be authored by hand.

WHERE THE PAIRS GO, AND WHY NOT `renamed_interfaces`
====================================================
`renamed_interfaces` is not a pad-side key. `spec_conformance_check` and
`l9_rtl_pin_consistency_check` (step 5) read it as a DECLARED RENAME: a pair
there turns a `port-missing` ERROR into INFO `port-renamed-by-manifest`, and
moves the L9 pin to an advisory tie-off. A derived pair is evidence of a SIDE,
not of a rename: R2 below only knows that a port shares a family atom with the
document ports still left on one side. Written there, it let this program relax
both gates on its own, which is what the GAP-E2E-8 contract forbids (the
auto-manifest reconciles ZERO ports until an author pairs them).

So `renamed_interfaces` stays hand-authored only. This program writes its pairs
under `derived_pad_pairs` (`_l_doc_pad_placement.DERIVED_PAD_PAIRS_KEY`), which
ONLY the pad-side reader `_l_doc_pad_placement.declared_renames` reads (steps 2
and 15.5ic). Neither gate reads that key.

PROGRAM FIRST. `derive` states a pair only where the design's own records
decide it, one rule at a time, each recorded with its evidence:

  R1 read/write split  -- the implemented port's atoms are a document port's
     atoms with ONE atom carrying a single `r`/`w` prefix
     (`o_sram_waddr` = {sram, w+addr} against `o_sram_addr` = {sram, addr}).
     Several document ports matching: the one(s) with the same DIRECTION win.
  R2 sole remaining side -- after R1, the document ports that share a family
     atom with the implemented port and are still unexplained all sit on ONE
     side (`o_sram_wen`: family {sram}; N's data ports are explained by R1,
     only S's `o_sram_we` / `o_sram_cyc` remain).

Every rule reads identifiers with `_l_doc_pad_placement.group_atoms`, the same
exact-atom rule the producer uses. There is no substring or fuzzy match: a port
no rule decides is returned UNRESOLVED, with its candidate sides, for the
`catalog-glue-author` step to pair.

AI BACKUP, VERIFIED. `verify` checks every pair the pad side will read, the
authored ones and the derived ones alike, against the design: each `rtl` name is
a port of the implemented top that the document does not already put on another
side, each `l9` name is an L9 port, and the `l9` names resolve by the exact-atom
rule to exactly ONE group side. A pair that fails is REFUSED with its reason;
`--check` exits 1.

Exit codes (`--check`): 0 every implemented port ends on EXACTLY ONE side
(directly, or by a verified pair), 1 a pair is refused, a port is still
unpaired, or a port sits on two sides (what 15.5ic refuses as
PORT_ON_TWO_SIDES), 2 the question does not arise (no reused-IP manifest, no pad
placement with group rows, or no readable port list), with its reason.

chip-AGNOSTIC: identifiers come from the design's own documents and RTL only.
"""
from __future__ import annotations

# --- sibling-import path (vibe-ic#2104) ------------------------------------
import os as _os                                                    # noqa: E402
import sys as _sys                                                  # noqa: E402

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
# ---------------------------------------------------------------------------

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import _atomic_artefact as _aa
import _l_doc_pad_placement as LPP
import _path_layout as _pl

PROGRAM = "renamed_interface_derive"
L9_REL = "phase1/generated_docs/L9_INTEGRATION_SPEC.json"
RW_PREFIXES = ("r", "w")
DERIVED_KEY = LPP.DERIVED_PAD_PAIRS_KEY
_BASE_NAME = re.compile(r"\s*([A-Za-z_][A-Za-z_0-9$]*)")


class NotApplicable(Exception):
    pass


# --------------------------------------------------------------------------- #
# the design's records
# --------------------------------------------------------------------------- #
def _l9(project: Path) -> Tuple[str, List[Dict[str, Any]]]:
    p = project / L9_REL
    try:
        doc = json.loads(p.read_text(errors="replace"))
    except (OSError, ValueError):
        raise NotApplicable(f"{L9_REL} is absent or unreadable")
    ports = [q for q in (doc.get("top_ports") or []) if isinstance(q, dict)
             and q.get("name")]
    if not ports:
        raise NotApplicable(f"{L9_REL} declares no top_ports")
    return str(doc.get("top_module") or ""), ports


def _rtl_top_ports(project: Path, top: str
                   ) -> Optional[Tuple[str, List[Dict[str, Any]]]]:
    """(file, ports) of the IMPLEMENTED top, from the staged RTL first and the
    design's vendor RTL second; each port carries its direction and, where the
    header's own defaults state it exactly, its width (the step-2 readers:
    literals, then `$clog2`/name expressions). None when no file defines
    ``top``."""
    if not top:
        return None
    from slot_pad_budget_check import (parse_top_ports, top_parameter_defaults,
                                       top_parameter_derived_defaults)
    roots = [_pl.rtl_dir(project), project / "input" / "vendor_rtl"]
    for root in roots:
        if not root.is_dir():
            continue
        for f in sorted(list(root.rglob("*.v")) + list(root.rglob("*.sv"))):
            try:
                text = f.read_text(errors="replace")
            except OSError:
                continue
            if not re.search(r"\bmodule\s+" + re.escape(top) + r"\b", text):
                continue
            params = top_parameter_defaults(text, top)
            params.update({k: v["value"] for k, v in
                           top_parameter_derived_defaults(text, top,
                                                          params).items()})
            ports = parse_top_ports(text, top, params)
            if ports:
                return (str(f.relative_to(project)),
                        [{"name": str(p["name"]),
                          "direction": p.get("dir") or p.get("direction"),
                          "width": p.get("width")} for p in ports])
    return None


def _implemented_ports(project: Path, top: str,
                       l9_ports: List[Dict[str, Any]]
                       ) -> Tuple[List[Dict[str, Any]], str]:
    rtl = _rtl_top_ports(project, top)
    if rtl is not None:
        return rtl[1], f"{rtl[0]}: module {top} port list"
    staged = [p for p in l9_ports if p.get("declared_by_staged_top") is True]
    if staged:
        return staged, f"{L9_REL}: top_ports[declared_by_staged_top=true]"
    raise NotApplicable(f"no RTL defines the top module {top!r} and L9 "
                        "labels no port as declared by the staged top")


def _implemented(project: Path, top: str, l9_ports: List[Dict[str, Any]]
                 ) -> Tuple[Set[str], str]:
    ports, source = _implemented_ports(project, top, l9_ports)
    return {str(p["name"]) for p in ports}, source


def _acceptance(pairs: List[Tuple[set, set]], l9_ports, impl_ports):
    """D2's `accept_renames`: phase 2's own acceptance rule for a pair."""
    return LPP.accept_renames(pairs, l9_ports, impl_ports)


def _placement(project: Path):
    placement, params, unreadable, _scanned = LPP.read_project_placement(project)
    if unreadable:
        raise NotApplicable("a design document could not be read: "
                            + "; ".join(f"{u['file']}: {u['reason']}"
                                        for u in unreadable))
    if placement is None or not placement.side_groups:
        raise NotApplicable("the design states no pad placement with a group "
                            "row, so no side is carried by a rename")
    return placement, params


def _atoms(name: str) -> Set[str]:
    return LPP.group_atoms(name, port_name=True)


def _group_sides(placement, name: str) -> List[str]:
    """Group sides whose statement holds every (non-empty) atom of ``name``."""
    a = _atoms(name)
    if not a:
        return []
    return sorted(side for side, statement in placement.side_groups.items()
                  if a <= LPP.group_atoms(statement))


def _direction(port: Dict[str, Any]) -> str:
    return str(port.get("direction") or port.get("mode") or "").lower()


def _direct_sides(placement, impl_named: List[Dict[str, Any]]
                  ) -> Dict[str, Set[str]]:
    """The side(s) the document gives a port by itself, with no pair.

    An exact row names its port whether or not its bit range resolved: a
    `o_x[AW-1:0]` whose AW is undeclared still places `o_x` on that side, and
    pairing `o_x` onto another side would put it on two once AW is declared. A
    group row covers the implemented ports whose every atom it states."""
    sides: Dict[str, Set[str]] = {}
    for side, tokens in placement.side_signals.items():
        for token in tokens:
            m = _BASE_NAME.match(str(token))
            if m:
                sides.setdefault(m.group(1), set()).add(side)
    _grouped, records = LPP.resolve_declared_pad_groups(placement, impl_named)
    for r in records:
        for name in r["matched_ports"]:
            sides.setdefault(name, set()).add(r["side"])
    return sides


# --------------------------------------------------------------------------- #
# program first
# --------------------------------------------------------------------------- #
def derive(project: Path) -> Dict[str, Any]:
    """Pairs the design's records decide, and the ports they do not."""
    project = Path(project)
    top, l9_ports = _l9(project)
    placement, params = _placement(project)
    impl_ports, impl_source = _implemented_ports(project, top, l9_ports)
    implemented = {str(p["name"]) for p in impl_ports}
    by_name = {str(p["name"]): p for p in l9_ports}

    impl_named = [by_name.get(n) or {"name": n} for n in sorted(implemented)]
    direct = _direct_sides(placement, impl_named)
    unplaced = [n for n in sorted(implemented) if not direct.get(n)]

    # Document ports the implemented core does not carry, each on the ONE
    # group side the exact-atom rule gives it.
    doc_side: Dict[str, str] = {}
    for name, port in by_name.items():
        if name in implemented:
            continue
        sides = _group_sides(placement, name)
        if len(sides) == 1:
            doc_side[name] = sides[0]
    doc_atoms = {d: _atoms(d) for d in doc_side}

    decided: Dict[str, Dict[str, Any]] = {}
    for u in unplaced:
        ua = _atoms(u)
        hits: List[str] = []
        via: Optional[str] = None
        for a in sorted(ua):
            if len(a) < 2 or a[0] not in RW_PREFIXES:
                continue
            want = (ua - {a}) | {a[1:]}
            m = sorted(d for d, da in doc_atoms.items() if da == want)
            if m:
                hits, via = m, a
                break
        if not hits:
            continue
        same_dir = [d for d in hits if _direction(by_name[d])
                    and _direction(by_name[d]) == _direction(by_name.get(u, {}))]
        chosen = same_dir or hits
        sides = sorted({doc_side[d] for d in chosen})
        if len(sides) != 1:
            continue
        decided[u] = {
            "rule": "R1", "side": sides[0], "l9": chosen,
            "atom": via, "prefix": via[0], "unprefixed_atom": via[1:],
            "because": (f"atoms({u})={sorted(ua)}: atom {via!r} is "
                        f"{via[0]!r}+{via[1:]!r}, and {chosen} carry "
                        f"atoms {sorted((ua - {via}) | {via[1:]})}"
                        + ("; same direction" if same_dir and len(hits) > 1
                           else "")
                        + f"; group side {sides[0]}"),
        }

    explained = {d for rec in decided.values() for d in rec["l9"]}
    unresolved: List[Dict[str, Any]] = []
    for u in unplaced:
        if u in decided:
            continue
        family = _atoms(u) & set().union(*doc_atoms.values()) if doc_atoms \
            else set()
        family_docs = sorted(d for d, da in doc_atoms.items() if da & family)
        remaining = [d for d in family_docs if d not in explained]
        sides = sorted({doc_side[d] for d in remaining})
        if family and len(sides) == 1:
            decided[u] = {
                "rule": "R2", "side": sides[0], "l9": remaining,
                "family_atoms": sorted(family),
                "because": (f"family atoms {sorted(family)} shared with "
                            f"{family_docs}; {sorted(explained & set(family_docs))} "
                            f"are explained by R1, and every unexplained one "
                            f"({remaining}) sits on group side {sides[0]}. "
                            f"That is a SIDE, not a rename."),
            }
            continue
        unresolved.append({
            "port": u, "family_atoms": sorted(family),
            "candidate_sides": sorted({doc_side[d] for d in family_docs}),
            "candidate_doc_ports": family_docs,
            "reason": ("no document port shares an identifier atom with it"
                       if not family_docs else
                       "its unexplained family members sit on "
                       f"{len(sides)} side(s) {sides}"),
        })

    # One entry per (rule, l9 names): an entry states ONE rule, so a reader
    # never has to guess which of its rtl names rests on the weaker one.
    grouped: Dict[Tuple[str, Tuple[str, ...]], List[str]] = {}
    evidence: Dict[Tuple[str, Tuple[str, ...]], List[Dict[str, Any]]] = {}
    for u in sorted(decided):
        key = (decided[u]["rule"], tuple(sorted(decided[u]["l9"])))
        grouped.setdefault(key, []).append(u)
        evidence.setdefault(key, []).append(
            {"rtl": u, **{k: v for k, v in decided[u].items()
                          if k not in ("rule", "l9")}})
    # A DERIVED PAIR GIVES A SIDE ONLY IF PHASE 2 ACCEPTS IT (D2's rule). A
    # rejected one is reported with its reasons, and its ports stay unresolved
    # with that reason: a width the document and the RTL disagree on is a
    # finding, never something a derivation papers over.
    _acc, rejected = _acceptance(
        [(set(k[1]), set(v)) for k, v in sorted(grouped.items())],
        l9_ports, impl_ports)
    rejected_keys = {(tuple(r["l9"]), tuple(r["rtl"])) for r in rejected}
    for r in rejected:
        for u in r["rtl"]:
            unresolved.append({
                "port": u, "family_atoms": sorted(_atoms(u)),
                "candidate_sides": [decided[u]["side"]],
                "candidate_doc_ports": r["l9"],
                "reason": ("its derived pair was rejected by the rename "
                           "acceptance rule (accept_renames): "
                           + "; ".join(r["reasons"]))})
    pairs = [{"l9": list(k[1]), "rtl": sorted(v), "derived_by": PROGRAM,
              "rule": k[0], "side": decided[v[0]]["side"],
              "evidence": evidence[k]} for k, v in sorted(
                  grouped.items(), key=lambda kv: (kv[0][1], kv[0][0]))
             if (k[1], tuple(sorted(v))) not in rejected_keys]
    return {"implemented_ports_source": impl_source,
            "implemented_ports": sorted(implemented),
            "direct_sides": {n: sorted(s) for n, s in sorted(direct.items())
                             if n in implemented},
            "unplaced_implemented_ports": unplaced,
            "pairs": pairs, "unresolved": unresolved,
            "rejected": rejected}


# --------------------------------------------------------------------------- #
# AI backup, verified
# --------------------------------------------------------------------------- #
def verify(project: Path, pairs: List[Dict[str, Any]], *,
           authored: bool = True) -> List[Dict[str, Any]]:
    """One verdict per pair: VERIFIED or REFUSED, with its reason.

    ``authored`` says which key the pairs came from. In `renamed_interfaces` a
    pair this program stamped is refused: that key is a declared rename to the
    phase-2 gates, and a derived pair is not one."""
    project = Path(project)
    top, l9_ports = _l9(project)
    placement, _params = _placement(project)
    impl_ports, _src = _implemented_ports(project, top, l9_ports)
    implemented = {str(p["name"]) for p in impl_ports}
    l9_names = {str(p["name"]) for p in l9_ports}
    by_name = {str(p["name"]): p for p in l9_ports}
    direct = _direct_sides(
        placement, [by_name.get(n) or {"name": n} for n in sorted(implemented)])
    from l9_rtl_pin_consistency_check import _manifest_renamed_groups
    out: List[Dict[str, Any]] = []
    for entry in pairs:
        groups = _manifest_renamed_groups({"renamed_interfaces": [entry]})
        l9s, rtls = groups[0] if groups else (set(), set())
        why: List[str] = []
        if authored and entry.get("derived_by") == PROGRAM:
            why.append(f"a pair {PROGRAM} derived sits in `renamed_interfaces`, "
                       "which spec_conformance_check and "
                       "l9_rtl_pin_consistency_check read as a declared rename; "
                       f"re-emit the manifest (it moves it to `{DERIVED_KEY}`)")
        if not l9s or not rtls:
            why.append("a pair needs a non-empty l9 list and rtl list")
        not_impl = sorted(n for n in rtls if n not in implemented)
        if not_impl:
            why.append(f"rtl name(s) {not_impl} are not ports of the "
                       f"implemented top {top!r}")
        not_l9 = sorted(n for n in l9s if n not in l9_names)
        if not_l9:
            why.append(f"l9 name(s) {not_l9} are not L9 top_ports")
        sides = sorted({s for n in l9s for s in _group_sides(placement, n)})
        sideless = sorted(n for n in l9s if not _group_sides(placement, n))
        if sideless:
            why.append(f"l9 name(s) {sideless} fall in no group row by the "
                       "exact-atom rule, so the pair carries no side")
        if len(sides) > 1:
            why.append(f"l9 names sit in {len(sides)} groups {sides}: one "
                       "pair per placement group")
        if len(sides) == 1:
            elsewhere = {n: sorted(direct[n]) for n in sorted(rtls)
                         if direct.get(n) and set(direct[n]) != {sides[0]}}
            if elsewhere:
                why.append(f"rtl name(s) {elsewhere} already have a side from "
                           f"the document's own placement; this pair would "
                           f"also put them on {sides[0]} (PORT_ON_TWO_SIDES "
                           f"at 15.5ic)")
        if l9s and rtls:
            _a, _r = _acceptance([(l9s, rtls)], l9_ports, impl_ports)
            for r in _r:
                why.extend(x for x in r["reasons"] if x not in " ".join(why))
        out.append({"pair": {"l9": sorted(l9s), "rtl": sorted(rtls)},
                    "verdict": "REFUSED" if why else "VERIFIED",
                    "side": sides[0] if len(sides) == 1 else None,
                    "reason": "; ".join(why) if why else
                    f"every rtl name is an implemented port and the l9 names "
                    f"resolve to group side {sides[0]}"})
    return out


def check(project: Path, pairs: List[Dict[str, Any]],
          derived: Optional[List[Dict[str, Any]]] = None,
          d: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Every implemented port ends on EXACTLY ONE side.

    It checks what the pad side will read: the authored ``pairs``
    (`renamed_interfaces`) and the ``derived`` ones (`derived_pad_pairs`;
    default: a fresh derivation). Both are verified. A side comes from the
    document directly or from a VERIFIED pair. No side is an unpaired port; two
    sides is what step 2 and 15.5ic refuse as PORT_ON_TWO_SIDES."""
    d = d if d is not None else derive(project)
    if derived is None:
        derived = d["pairs"]
    authored_v = verify(project, pairs)
    derived_v = verify(project, derived, authored=False)
    sides: Dict[str, Set[str]] = {n: set(s)
                                  for n, s in d["direct_sides"].items()}
    for v in authored_v + derived_v:
        if v["verdict"] == "VERIFIED":
            for n in v["pair"]["rtl"]:
                sides.setdefault(n, set()).add(v["side"])
    impl = d["implemented_ports"]
    unpaired = [n for n in impl if not sides.get(n)]
    two_sides = {n: sorted(sides[n]) for n in impl if len(sides.get(n, ())) > 1}
    refused = [v for v in authored_v + derived_v if v["verdict"] == "REFUSED"]
    return {"verdict": "FAIL" if refused or unpaired or two_sides else "PASS",
            "pairs": authored_v, "derived_pairs": derived_v,
            "unpaired_implemented_ports": unpaired,
            "ports_on_two_sides": two_sides, "derivation": d}


def apply_to_manifest(project: Path, mf: Dict[str, Any]) -> Dict[str, Any]:
    """Refresh this program's own keys; never author `renamed_interfaces`.

    Every emit rewrites `derived_pad_pairs` ([] when the question does not
    arise), `renamed_interfaces_derivation`, and `renamed_interfaces_check`
    (the check of what the pad side will read). `renamed_interfaces` is left as
    the author wrote it. The one exception is an entry stamped
    ``derived_by: renamed_interface_derive``: an earlier version of this
    program put its pairs there, and no author wrote them. Those are removed
    and named in the derivation, because in that key two phase-2 gates read
    them as declared renames."""
    current = mf.get("renamed_interfaces")
    moved: List[Dict[str, Any]] = []
    if isinstance(current, list):
        moved = [e for e in current
                 if isinstance(e, dict) and e.get("derived_by") == PROGRAM]
        if moved:
            mf["renamed_interfaces"] = [e for e in current if e not in moved]
    authored = [e for e in (mf.get("renamed_interfaces") or [])
                if isinstance(e, dict)]
    mf.pop("renamed_interfaces_check", None)
    try:
        d = derive(project)
        c = check(project, authored, d["pairs"], d=d)
    except NotApplicable as exc:
        mf[DERIVED_KEY] = []
        mf["renamed_interfaces_derivation"] = {"verdict": "NOT_APPLICABLE",
                                               "program": PROGRAM,
                                               "reason": str(exc)}
        return mf
    c.pop("derivation", None)
    mf[DERIVED_KEY] = d["pairs"]
    mf["renamed_interfaces_derivation"] = {
        "verdict": "UNRESOLVED" if d["unresolved"] else "DERIVED",
        "rejected": d["rejected"],
        "program": PROGRAM,
        "written_to": DERIVED_KEY,
        "implemented_ports_source": d["implemented_ports_source"],
        "unresolved": d["unresolved"],
        "owed_by": ("catalog-glue-author: for every unresolved port, author "
                    "one `renamed_interfaces` pair per placement group, or "
                    "report the port as a document placement gap when no "
                    "document port is its counterpart; then run "
                    f"`python3 programs/{PROGRAM}.py <project> --check`"
                    if d["unresolved"] else None),
    }
    if moved:
        mf["renamed_interfaces_derivation"]["moved_out_of_renamed_interfaces"] \
            = moved
    mf["renamed_interfaces_check"] = c
    return mf


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("project")
    ap.add_argument("--check", action="store_true",
                    help="verify the pairs the pad side reads (authored and "
                         "derived); rc 1 on a refused pair, an unpaired "
                         "implemented port, or a port on two sides")
    ap.add_argument("--json", help="write the result here (atomic)")
    a = ap.parse_args(argv)
    project = Path(a.project).resolve()
    mf_path = _pl.rtl_dir(project) / "SOURCE_MANIFEST.json"
    try:
        mf = json.loads(mf_path.read_text()) if mf_path.is_file() else None
        if not isinstance(mf, dict) or mf.get("reused_ip") is not True:
            raise NotApplicable(f"{mf_path.relative_to(project)} is absent or "
                                "does not declare reused_ip: true")
        pairs = [e for e in (mf.get("renamed_interfaces") or [])
                 if isinstance(e, dict)]
        derived = [e for e in (mf.get(DERIVED_KEY) or [])
                   if isinstance(e, dict)]
        res = check(project, pairs, derived) if a.check else derive(project)
        rc = (1 if res.get("verdict") == "FAIL" else 0) if a.check else 0
    except NotApplicable as exc:
        res, rc = {"verdict": "NOT_APPLICABLE", "reason": str(exc)}, 2
    res = {"program": PROGRAM, "project": str(project), **res}
    if a.json:
        _aa.write_json(a.json, res)
    print(json.dumps(res, indent=2))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
