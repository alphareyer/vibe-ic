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

Sides are counted per BIT NET, as the pad side places them: one pad per bit
(`expand_side_ports`), a group row's `resolved_nets`, and a verified pair's rtl
ports expanded with `bit_names` at their implemented widths. A bus the document
splits across two sides is legal; a bus it places only in part is not.

Exit codes (`--check`): 0 every implemented bit net ends on EXACTLY ONE side
(directly, or by a verified pair), 1 a pair is refused, a net has no side, or a
net sits on two sides (what 15.5ic refuses as PORT_WITHOUT_A_SIDE /
PORT_ON_TWO_SIDES), 2 the question does not arise (no reused-IP manifest, no
pad placement with group rows, or no readable port list), 3 NOT_MEASURED: an
extent the check cannot know, and no definite defect -- a design document that
could not be read, a placement token whose bit range does not resolve from a
declared parameter, or a port whose width neither its RTL header nor L9 states
(an RTL width the header cannot state takes L9's, which 15.5ic places by).
Each with its reason.

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
    """The question does not arise for this design."""


class NotMeasured(Exception):
    """The question arises, but the design's placement could not be read."""


#: The ONLY shape 3c5697945 wrote into `renamed_interfaces`: stamped, with no
#: top-level `rule`, and evidence rules under these long names. An author's
#: copy of a current `derived_pad_pairs` entry carries a top-level `rule`.
_LEGACY_EVIDENCE_RULES = frozenset({"R1_read_write_split",
                                    "R2_sole_remaining_side"})


def _is_legacy_entry(entry: Any) -> bool:
    if not isinstance(entry, dict) or entry.get("derived_by") != PROGRAM:
        return False
    if "rule" not in entry:
        return True
    return any(isinstance(e, dict) and e.get("rule") in _LEGACY_EVIDENCE_RULES
               for e in (entry.get("evidence") or []))


def authored_pairs(mf: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Authored pairs by key, from EVERY key the pad side reads them from.

    `_l_doc_pad_placement.declared_renames` parses the manifest with
    `l9_rtl_pin_consistency_check._manifest_renamed_groups`, which reads all of
    `_MANIFEST_RENAME_KEYS`. Verifying `renamed_interfaces` alone would let an
    alias-key pair give a pad a side that nothing checked."""
    from l9_rtl_pin_consistency_check import _MANIFEST_RENAME_KEYS
    out: Dict[str, List[Dict[str, Any]]] = {}
    for key in _MANIFEST_RENAME_KEYS:
        entries = [e for e in (mf.get(key) or []) if isinstance(e, dict)] \
            if isinstance(mf.get(key), list) else []
        if entries:
            out[key] = entries
    return out


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
        raise NotMeasured("a design document could not be read: "
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


def _net_sides(placement, params: Dict[str, int],
               impl_ports: List[Dict[str, Any]],
               renames: "List[Tuple[set, set]]" = (), *, split: bool = False):
    """Side(s) of every bit net, placed the way step 15.5ic places them.

    Exact rows are expanded one pad per bit (`expand_side_ports`); group rows
    contribute their `resolved_nets` (every bit of each matched port, and, with
    ``renames``, of each paired rtl port). The one addition is an exact token
    whose bit range did not resolve (`o_x[AW-1:0]`, AW undeclared): it still
    names its port, so every bit of that port takes that side. Pairing `o_x`
    onto another side would put it on two once AW is declared.

    Those open-token sides are a claim about WHICH bits nobody can yet state,
    so with ``split`` they come back apart, as ``(definite, open)``, and a
    check can keep them out of its FAIL sets. Without ``split``, one merged
    map (a port is placed either way)."""
    nets_of = {str(p["name"]): LPP.bit_names(p) for p in impl_ports}
    definite: Dict[str, Set[str]] = {}
    from_open: Dict[str, Set[str]] = {}
    exact, unresolved = LPP.expand_side_ports(placement, params)
    for side, nets in exact.items():
        for net in nets:
            definite.setdefault(net, set()).add(side)
    open_tokens = set(unresolved)
    for side, tokens in placement.side_signals.items():
        for token in tokens:
            m = _BASE_NAME.match(str(token))
            if token in open_tokens and m:
                for net in nets_of.get(m.group(1), [m.group(1)]):
                    from_open.setdefault(net, set()).add(side)
    _grouped, records = LPP.resolve_declared_pad_groups(
        placement, impl_ports, renames=list(renames))
    for r in records:
        for net in r["resolved_nets"]:
            definite.setdefault(net, set()).add(r["side"])
    if split:
        return definite, from_open
    merged = {n: set(s) for n, s in definite.items()}
    for n, s in from_open.items():
        merged.setdefault(n, set()).update(s)
    return merged


def _countable_ports(impl_ports: List[Dict[str, Any]],
                     l9_ports: List[Dict[str, Any]]
                     ) -> Tuple[List[Dict[str, Any]], Dict[str, int], List[str]]:
    """(ports whose bits can be counted, widths taken from L9, ports nobody can
    count).

    AN UNREADABLE WIDTH NEVER BECOMES A NUMBER. The header reader returns no
    width for a range it cannot state exactly (`[DW/8-1:0]`, a `` `define ``),
    and `bit_names` would then make the port ONE scalar net, so that a port
    the document places in full reads as unplaced. 15.5ic places by L9's width,
    so a literal L9 width is used for the bits, and recorded. A port neither
    states is returned in the third list: its bits are not counted, and the
    caller says NOT_MEASURED rather than guess (the same rule step 2 follows:
    an unreadable width is UNDECIDED)."""
    l9_by_name = {str(p.get("name")): p for p in l9_ports if isinstance(p, dict)}
    out: List[Dict[str, Any]] = []
    from_l9: Dict[str, int] = {}
    unknown: List[str] = []
    for p in impl_ports:
        if LPP._port_width(p) is not None:
            out.append(p)
            continue
        name = str(p["name"])
        l9 = l9_by_name.get(name) or {}
        width = LPP._port_width(l9)
        if width is None:
            unknown.append(name)
            out.append(p)
            continue
        q = dict(p, width=width)
        if isinstance(l9.get("msb"), int) and isinstance(l9.get("lsb"), int):
            q["msb"], q["lsb"] = l9["msb"], l9["lsb"]
        out.append(q)
        from_l9[name] = width
    return out, from_l9, sorted(unknown)


def _named_on(placement) -> Dict[str, Set[str]]:
    """Sides on which an exact row names a port, by its base name."""
    named: Dict[str, Set[str]] = {}
    for side, tokens in placement.side_signals.items():
        for token in tokens:
            m = _BASE_NAME.match(str(token))
            if m:
                named.setdefault(m.group(1), set()).add(side)
    return named


def _whole_port_sides(placement, impl_ports, renames) -> Dict[str, Set[str]]:
    """Sides assigned to every bit of a port by a matched group or pair."""
    _grouped, records = LPP.resolve_declared_pad_groups(
        placement, impl_ports, renames=list(renames))
    whole: Dict[str, Set[str]] = {}
    for record in records:
        for name in record["matched_ports"]:
            whole.setdefault(name, set()).add(record["side"])
    return whole


def _document_sides(placement, params, impl_ports,
                    uncountable: "Tuple[str, ...] | List[str]" = ()
                    ) -> Dict[str, Dict[str, Any]]:
    """Per implemented port: the sides the document gives it by itself (no
    pair), and which of its bit nets got one. ``impl_ports`` carry countable
    widths (`_countable_ports`); a port in ``uncountable`` is marked, so no
    caller turns its unknown bit extent into a gap."""
    net_sides = _net_sides(placement, params, impl_ports)
    named = _named_on(placement)
    out: Dict[str, Dict[str, Any]] = {}
    for p in impl_ports:
        name = str(p["name"])
        nets = LPP.bit_names(p)
        placed = [n for n in nets if net_sides.get(n)]
        out[name] = {
            "nets": nets, "placed_nets": placed,
            "sides": sorted({s for n in placed for s in net_sides[n]}
                            | named.get(name, set())),
            "countable": name not in uncountable,
        }
    return out


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

    counted, _from_l9, uncountable = _countable_ports(impl_ports, l9_ports)
    direct = _document_sides(placement, params, counted, uncountable)
    unplaced = [n for n in sorted(implemented) if not direct[n]["sides"]]

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
    # A port the document places only in part is a gap IN THE DOCUMENT: a pair
    # cannot fix it (it would put the placed bits on a second side), so it is
    # reported, not derived and not left for an author to pair.
    for u in sorted(implemented):
        rec = direct[u]
        missing = [n for n in rec["nets"] if n not in rec["placed_nets"]]
        if rec["sides"] and missing and rec["countable"]:
            unresolved.append({
                "port": u, "family_atoms": sorted(_atoms(u)),
                "candidate_sides": rec["sides"], "candidate_doc_ports": [],
                "reason": (f"the document's placement gives a side to "
                           f"{len(rec['placed_nets'])} of its "
                           f"{len(rec['nets'])} bit net(s); {missing[:8]} have "
                           f"none. That is a placement gap in the document, "
                           f"not a pair to author")})
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

    # A DERIVED PAIR GIVES A SIDE ONLY IF PHASE 2 ACCEPTS IT (D2's rule), and
    # it is asked PER RTL PORT: one port whose width or direction disagrees
    # must not take the side away from a sibling the rule accepts. A rejected
    # port is reported with its own reasons and stays unresolved: a width the
    # document and the RTL disagree on is a finding, never papered over.
    accepted: List[str] = []
    rejected_by_port: Dict[str, List[str]] = {}
    for u in sorted(decided):
        _acc, rej = _acceptance([(set(decided[u]["l9"]), {u})],
                                l9_ports, impl_ports)
        if rej:
            rejected_by_port[u] = [x for r in rej for x in r["reasons"]]
        else:
            accepted.append(u)

    # One entry per (rule, l9 names): an entry states ONE rule, so a reader
    # never has to guess which of its rtl names rests on the weaker one.
    def _grouped(ports: List[str]) -> Dict[Tuple[str, Tuple[str, ...]], List[str]]:
        out: Dict[Tuple[str, Tuple[str, ...]], List[str]] = {}
        for u in ports:
            key = (decided[u]["rule"], tuple(sorted(decided[u]["l9"])))
            out.setdefault(key, []).append(u)
        return out

    rejected = [{"l9": list(k[1]), "rtl": sorted(v), "rule": k[0],
                 "reasons": [x for u in sorted(v) for x in rejected_by_port[u]]}
                for k, v in sorted(_grouped(sorted(rejected_by_port)).items(),
                                   key=lambda kv: (kv[0][1], kv[0][0]))]
    for u in sorted(rejected_by_port):
        unresolved.append({
            "port": u, "family_atoms": sorted(_atoms(u)),
            "candidate_sides": [decided[u]["side"]],
            "candidate_doc_ports": decided[u]["l9"],
            "reason": ("its derived pair was rejected by the rename "
                       "acceptance rule (accept_renames): "
                       + "; ".join(rejected_by_port[u]))})
    pairs = [{"l9": list(k[1]), "rtl": sorted(v), "derived_by": PROGRAM,
              "rule": k[0], "side": decided[v[0]]["side"],
              "evidence": [{"rtl": u, **{x: y for x, y in decided[u].items()
                                        if x not in ("rule", "l9")}}
                           for u in sorted(v)]}
             for k, v in sorted(_grouped(accepted).items(),
                                key=lambda kv: (kv[0][1], kv[0][0]))]
    return {"implemented_ports_source": impl_source,
            "implemented_ports": sorted(implemented),
            "document_sides": {n: direct[n]["sides"] for n in sorted(direct)},
            "unplaced_implemented_ports": unplaced,
            "pairs": pairs, "unresolved": unresolved,
            "rejected": rejected}


# --------------------------------------------------------------------------- #
# AI backup, verified
# --------------------------------------------------------------------------- #
def verify(project: Path, pairs: List[Dict[str, Any]], *,
           authored: bool = True, key: str = "renamed_interfaces"
           ) -> List[Dict[str, Any]]:
    """One verdict per pair: VERIFIED or REFUSED, with its reason.

    ``authored`` and ``key`` say where the pairs came from. Each entry is parsed
    by the pad side's own parser under that key. An entry an earlier version of
    this program wrote into `renamed_interfaces` (`_is_legacy_entry`) is
    refused: no author wrote it, and in that key the phase-2 gates read it as a
    declared rename. An author's copy of a current derived entry is a
    declaration like any other and is verified as one."""
    project = Path(project)
    top, l9_ports = _l9(project)
    placement, params = _placement(project)
    impl_ports, _src = _implemented_ports(project, top, l9_ports)
    implemented = {str(p["name"]) for p in impl_ports}
    l9_names = {str(p["name"]) for p in l9_ports}
    counted, _from_l9, uncountable = _countable_ports(impl_ports, l9_ports)
    direct = _document_sides(placement, params, counted, uncountable)
    from l9_rtl_pin_consistency_check import (_MANIFEST_RENAME_KEYS,
                                              _manifest_renamed_groups)
    # The parser reads only the rename keys; `derived_pad_pairs` entries are
    # parsed under `renamed_interfaces`, exactly as `declared_renames` does.
    parse_key = key if key in _MANIFEST_RENAME_KEYS else "renamed_interfaces"
    out: List[Dict[str, Any]] = []
    for entry in pairs:
        groups = _manifest_renamed_groups({parse_key: [entry]})
        l9s, rtls = groups[0] if groups else (set(), set())
        why: List[str] = []
        if authored and _is_legacy_entry(entry):
            why.append(f"a pair an earlier {PROGRAM} wrote sits in `{key}`, "
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
            elsewhere = {n: direct[n]["sides"] for n in sorted(rtls)
                         if n in direct and direct[n]["sides"]
                         and set(direct[n]["sides"]) != {sides[0]}}
            if elsewhere:
                why.append(f"rtl name(s) {elsewhere} already have a side from "
                           f"the document's own placement; this pair would "
                           f"also put them on {sides[0]} (PORT_ON_TWO_SIDES "
                           f"at 15.5ic)")
        if l9s and rtls:
            _a, _r = _acceptance([(l9s, rtls)], l9_ports, impl_ports)
            for r in _r:
                why.extend(x for x in r["reasons"] if x not in " ".join(why))
        out.append({"key": key,
                    "pair": {"l9": sorted(l9s), "rtl": sorted(rtls)},
                    "verdict": "REFUSED" if why else "VERIFIED",
                    "side": sides[0] if len(sides) == 1 else None,
                    "reason": "; ".join(why) if why else
                    f"every rtl name is an implemented port and the l9 names "
                    f"resolve to group side {sides[0]}"})
    return out


def check(project: Path, pairs: Any,
          derived: Optional[List[Dict[str, Any]]] = None,
          d: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Every implemented BIT NET ends on EXACTLY ONE side.

    It checks what the pad side will read: the authored ``pairs`` (a
    ``{key: entries}`` map from `authored_pairs`, or a bare `renamed_interfaces`
    list) and the ``derived`` ones (`derived_pad_pairs`; default: a fresh
    derivation). Every pair is verified, and the VERIFIED ones are applied the
    way 15.5ic applies them (`resolve_declared_pad_groups`, one pad per bit).
    A net with no side is PORT_WITHOUT_A_SIDE at 15.5ic; a net on two sides is
    PORT_ON_TWO_SIDES.

    An unknown bit extent alone never enters a FAIL set. Either of these,
    with no definite defect, makes the answer NOT_MEASURED (rc 3):
      * which bits an open-range token names (`o_x[SW-1:2]`, SW undeclared).
        Its sides are kept apart (`_net_sides(split=True)`); an unknown span
        alone does not prove a particular bit is on two sides. A group placing
        the WHOLE port on another side does prove an overlap, whatever the
        span, and a net an open token reaches is not "without a side";
      * how many bits a port has, when neither its RTL header nor L9 states a
        number (`_countable_ports`). Its bits are not counted. A port with no
        side at all is still FAIL: that holds whatever its width."""
    d = d if d is not None else derive(project)
    if derived is None:
        derived = d["pairs"]
    by_key = pairs if isinstance(pairs, dict) else {"renamed_interfaces": pairs}
    authored_v = [v for key, entries in by_key.items()
                  for v in verify(project, entries, key=key)]
    derived_v = verify(project, derived, authored=False, key=DERIVED_KEY)
    project = Path(project)
    top, l9_ports = _l9(project)
    placement, params = _placement(project)
    impl_ports, _src = _implemented_ports(project, top, l9_ports)
    counted, from_l9, uncountable = _countable_ports(impl_ports, l9_ports)
    renames = [(set(v["pair"]["l9"]), set(v["pair"]["rtl"]))
               for v in authored_v + derived_v if v["verdict"] == "VERIFIED"]
    definite, from_open = _net_sides(placement, params, counted, renames,
                                     split=True)
    named = _named_on(placement)
    whole_sides = _whole_port_sides(placement, counted, renames)
    nets = [n for p in counted if str(p["name"]) not in uncountable
            for n in LPP.bit_names(p)]
    without = [n for n in nets if not definite.get(n) and not from_open.get(n)]
    # a port whose bits cannot be counted is still "without a side" when
    # nothing places it at all -- that holds whatever its width is
    without += [n for n in uncountable
                if not definite.get(n) and not from_open.get(n)
                and not named.get(n)]
    two = {n: sorted(definite[n]) for n in nets if len(definite.get(n, ())) > 1}
    # A group/pair covers the WHOLE port. Any token naming that port from a
    # different side therefore overlaps at least one bit, even if the token's
    # span or the port's width is unknown. Record a port-level witness when
    # bit-level witnesses are unavailable; do not invent which bits overlap.
    for p in counted:
        name = str(p["name"])
        whole = whole_sides.get(name, set())
        other = named.get(name, set())
        if whole and other - whole:
            if name in uncountable or not any(
                    bit in two for bit in LPP.bit_names(p)):
                two[name] = sorted(whole | other)
    # A group row or a pair places a port WHOLE, so for a port whose bits
    # cannot be counted that answer holds whatever its width: one whole-port
    # side is placed, two are on two sides. Only an exact row naming SOME of its
    # bits leaves the extent open.
    whole_only: List[str] = []
    for n in uncountable:
        if n in without:
            continue
        whole = whole_sides.get(n) or set()
        if len(whole) > 1:
            two[n] = sorted(whole)
            whole_only.append(n)
            continue
        if named.get(n) or from_open.get(n):
            continue
        whole_only.append(n)
    ports_without = sorted({str(p["name"]) for p in counted
                            if any(n in without for n in LPP.bit_names(p))}
                           | {n for n in uncountable if n in without})
    refused = [v for v in authored_v + derived_v if v["verdict"] == "REFUSED"]
    # A token whose bit range does not resolve is PARTITION_UNRESOLVED at
    # 15.5ic and UNDECIDED at step 2: which bit lands where is unknown, so
    # absent a definite defect the answer is NOT_MEASURED, never PASS. The
    # same holds for a port whose bits nobody can count.
    _exact, open_tokens = LPP.expand_side_ports(placement, params)
    open_width = [n for n in uncountable
                  if n not in without and n not in whole_only]
    unknown = open_tokens or open_width
    verdict = ("FAIL" if refused or without or two
               else "NOT_MEASURED" if unknown else "PASS")
    res = {"verdict": verdict,
           "pairs": authored_v, "derived_pairs": derived_v,
           "nets_without_side": without, "nets_on_two_sides": two,
           "unpaired_implemented_ports": ports_without,
           "unresolved_tokens": sorted(open_tokens),
           "unresolved_width_ports": sorted(uncountable),
           "widths_from_l9": dict(sorted(from_l9.items())), "derivation": d}
    if verdict == "NOT_MEASURED":
        why = []
        if open_tokens:
            why.append(f"the placement names token(s) whose bit range does not "
                       f"resolve from a declared parameter "
                       f"{sorted(open_tokens)} (15.5ic refuses "
                       f"PARTITION_UNRESOLVED)")
        if open_width:
            why.append(f"neither the RTL header nor L9 states a width for "
                       f"{open_width}, and an exact row names some of its "
                       f"bits")
        res["reason"] = ("; ".join(why) + ": which bit lands on which side "
                         "cannot be measured")
    return res


def apply_to_manifest(project: Path, mf: Dict[str, Any]) -> Dict[str, Any]:
    """Refresh this program's own keys; never author `renamed_interfaces`.

    Every emit rewrites `derived_pad_pairs` ([] when the question does not
    arise or was not measured), `renamed_interfaces_derivation`, and
    `renamed_interfaces_check` (the check of what the pad side will read).
    Authored pairs are left as the author wrote them, under whichever rename
    key. The one exception is an entry of the shape 3c5697945 wrote into
    `renamed_interfaces` (`_is_legacy_entry`): no author wrote it, so it is
    moved out and named, because in that key two phase-2 gates read it as a
    declared rename. An author's copy of a current derived entry stays."""
    current = mf.get("renamed_interfaces")
    moved: List[Dict[str, Any]] = []
    if isinstance(current, list):
        moved = [e for e in current if _is_legacy_entry(e)]
        if moved:
            mf["renamed_interfaces"] = [e for e in current if e not in moved]
    mf.pop("renamed_interfaces_check", None)
    try:
        d = derive(project)
        c = check(project, authored_pairs(mf), d["pairs"], d=d)
    except (NotApplicable, NotMeasured) as exc:
        mf[DERIVED_KEY] = []
        mf["renamed_interfaces_derivation"] = {
            "verdict": ("NOT_MEASURED" if isinstance(exc, NotMeasured)
                        else "NOT_APPLICABLE"),
            "program": PROGRAM, "reason": str(exc)}
        if moved:
            mf["renamed_interfaces_derivation"][
                "moved_out_of_renamed_interfaces"] = moved
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
                    f"`python3 programs/{PROGRAM}.py <project> --check` "
                    "(a reported gap keeps rc 1)"
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
                    help="verify the pairs the pad side reads (authored under "
                         "every rename key, and derived); rc 1 on a refused "
                         "pair, a bit net with no side, or a net on two sides; "
                         "rc 3 NOT_MEASURED when a design document could not "
                         "be read, a placement token's bit range does not "
                         "resolve, or a port's width is stated nowhere")
    ap.add_argument("--json", help="write the result here (atomic)")
    a = ap.parse_args(argv)
    project = Path(a.project).resolve()
    mf_path = _pl.rtl_dir(project) / "SOURCE_MANIFEST.json"
    try:
        mf = json.loads(mf_path.read_text()) if mf_path.is_file() else None
        if not isinstance(mf, dict) or mf.get("reused_ip") is not True:
            raise NotApplicable(f"{mf_path.relative_to(project)} is absent or "
                                "does not declare reused_ip: true")
        derived = [e for e in (mf.get(DERIVED_KEY) or [])
                   if isinstance(e, dict)]
        res = (check(project, authored_pairs(mf), derived) if a.check
               else derive(project))
        rc = ({"FAIL": 1, "NOT_MEASURED": 3}.get(res.get("verdict"), 0)
              if a.check else 0)
    except NotApplicable as exc:
        res, rc = {"verdict": "NOT_APPLICABLE", "reason": str(exc)}, 2
    except NotMeasured as exc:
        res, rc = {"verdict": "NOT_MEASURED", "reason": str(exc)}, 3
    res = {"program": PROGRAM, "project": str(project), **res}
    if a.json:
        _aa.write_json(a.json, res)
    print(json.dumps(res, indent=2))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
