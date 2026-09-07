#!/usr/bin/env python3
"""die_level_deck_rule_attribution.py — vibe-ic#2112.

WHAT GAP THIS CLOSES
--------------------
A sign-off DRC total is read as a statement about the DESIGN'S LAYOUT. On a
delivery that declared itself a HARDMACRO and got a DIE seal ring stamped into
its shipped GDS anyway, it is not one, and nothing said so.

MEASURED, lane rbsub4 2026-09-07, pinned EDA image 0.3.48, `PDK-1`, the
`subservient` front-door run, counting <multiplicity> per <category> over the
run's own KLayout RDB:

    shipped GDS (ring stamped)   1,359,528   GR.4 1,299,340  GR.2 24,652
                                              GR.6 1,022  -> GR.* = 1,325,014
    the same layout, no ring             5   GR.* = 0

The ring's generator drew a die-level GUARD-RING MARKER over the die, so the
deck's die-level rules evaluated the whole routed design as ring metal — a
12 um minimum width applied to sub-micron signal routing. Every wire fired.

`die_finishing_gen._hardmacro_skip` is what stops the ring being generated.
THIS program is the second half: when a die-level marker rule fires ANYWAY on
a hardmacro delivery — a design that carries its own marker, a ring stamped by
something else, an older tree re-measured — the DRC summary must ATTRIBUTE it
rather than fold it into the design's number silently.

NO VERDICT IS TAKEN AND NOTHING IS WAIVED. This program reports a split and a
sentence. The DRC step's PASS/FAIL is decided where it was decided before.

HOW THE FAMILY IS ESTABLISHED — from the PDK's OWN DECK, never from a list
here. Three links, and each one that cannot be made is reported as
NOT_MEASURED naming what was looked for, because "I could not tell" and "there
were none" must never arrive wearing the same sentence:

  1. the die-level MARKER LAYER, as `layer/datatype`: from `--marker`, or from
     what `die_finishing` recorded itself adding to the GDS.
  2. the deck's IDENTIFIER for that layer: the deck source line that declares
     it — `:name` in a symbol-style declaration, or `name =` in an assignment
     — is the deck naming its own layer, and it is the only authority for it.
  3. the RULES gated on that identifier: the deck's own `# Rule <id>:` blocks
     that reference it.

chip/PDK-AGNOSTIC: no foundry, PDK, vendor, design or rule-name literal. The
marker layer, the deck sources and the counts are all INPUTS. The two fixed
strings are the `# Rule <id>:` block header every open KLayout deck in this
image writes, and this module's own file separator.

    die_level_deck_rule_attribution <project> [--per-rule J] [--deck-sources F]
                                    [--marker L/D] [--json J]
    main(argv) -> int : 0 nothing to attribute / 1 attributed / 2 input error
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

#: Written ahead of each deck file by the collector, so one concatenated blob
#: still knows where a file ended — a rule block must never bleed across one.
FILE_SEP = "#VIC_DECK_FILE "
_RULE_RE = re.compile(r"^\s*#\s*Rule\s+([A-Za-z0-9_.]+)\s*:", re.M)
_SEP_RE = re.compile("^" + re.escape(FILE_SEP), re.M)
_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"

#: A deck SECTION BANNER also ends a rule block (vibe-ic#2148). A block that
#: runs to the next `# Rule` header bleeds into whatever preamble the next
#: SECTION sets up, and that preamble is where a deck computes its next
#: layer's density ratio. MEASURED on an open PDK's density deck: the
#: windowed rule `MFil.k` ends at line 920, the next `# Rule` header is at
#: 936, and line 934 is `tm1_dens_ratio = tm1_area / chip_area` — so the
#: windowed rule's block acquired the die-area identifier of the section
#: AFTER it and would have been attributed as die-level. A banner is a
#: comment line made only of `#`, `=`, `-` and spaces; both open PDK decks in
#: this image write one between sections. This is the module's third and last
#: fixed string, and it is disclosed here for the same reason as the other two.
_BANNER_RE = re.compile(r"^\s*#[#=\-\s]{6,}$", re.M)

#: KLayout DRC's own name for the whole-layout extent. It is a primitive of
#: the DRC language, not a PDK, foundry or design literal — the same standing
#: as `.area` or `.output` — and every open deck in this image spells it this
#: way because there is no other spelling.
_EXTENT_PRIMITIVE = "extent"

#: The verdict when die-level DENSITY rules are carried by the integrator.
DENSITY_ATTRIBUTED = "DIE_LEVEL_DENSITY_ATTRIBUTED_TO_INTEGRATOR"
#: The DRC tier such a delivery earns. NOT "PASS": a reader who greps for PASS
#: must not find this, and a consumer that does not know the word must not
#: silently treat it as green (see `_aggregate_verdict` in the phase-3 runner,
#: whose catch-all returns PASS for any status it does not enumerate).
TIER_PASS_WITH_ATTRIBUTION = "PASS_WITH_ATTRIBUTION"
#: Where the macro's delivery carries what the integrator must close.
HANDOFF_NAME = "integrator_requirements.json"

NOT_MEASURED = "NOT_MEASURED"

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tapeout_declaration as _td  # noqa: E402
import _atomic_artefact as _atomic  # noqa: E402  (vibe-ic#1082)

#: Bound from the declaration schema that OWNS the word, never re-typed here:
#: a delivery that is a macro somebody else places is the whole subject of this
#: module and it must be spelled by the module that defines the enum.
DELIVERABLE_HARDMACRO = _td.DELIVERABLE_HARDMACRO
#: Its opposite, bound the same way. A DIE owns its own die-level rules, and
#: the refusal to attribute them has to be able to SAY the word.
DELIVERABLE_DIE = _td.DELIVERABLE_DIE


def _int_tokens(line: str) -> List[int]:
    return [int(t) for t in re.findall(r"(?<![\w.])(\d+)(?![\w.])", line)]


def marker_identifiers(deck_sources: str, layer: int, datatype: int
                       ) -> Tuple[Set[str], Optional[str]]:
    """(the deck's own name(s) for `layer/datatype`, why-not).

    A DECK NAMES ITS LAYERS ONCE, on the line that declares them, and that line
    carries both numbers. Two spellings are accepted because both are in use:
    a symbol argument (`... .call(:guard_ring_mk, 167, 5)`) and an assignment
    (`guard_ring_mk = input(167, 5)`). Anything else returns the empty set and
    a reason — this must not fall back to guessing an identifier, because a
    wrong identifier silently attributes the wrong rules.
    """
    names: Set[str] = set()
    for line in deck_sources.splitlines():
        toks = _int_tokens(line)
        if layer not in toks or datatype not in toks:
            continue
        sym = re.search(r":(" + _IDENT + r")\b", line)
        if sym:
            names.add(sym.group(1))
            continue
        asn = re.match(r"\s*(" + _IDENT + r")\s*=", line)
        if asn:
            names.add(asn.group(1))
    if names:
        return names, None
    return set(), (f"no line of the deck sources declares layer "
                   f"{layer}/{datatype} in a form this reads (a `:name` "
                   f"symbol or a `name =` assignment on the line carrying "
                   f"both numbers), so the deck's own name for the die-level "
                   f"marker is unknown")


def rules_gated_on(deck_sources: str, identifiers: Iterable[str]
                   ) -> Dict[str, str]:
    """{rule id: the deck file it is declared in} for every rule whose own
    `# Rule <id>:` block references one of `identifiers`.

    BLOCK, not file. Attributing every rule of a file that merely mentions the
    marker over-claims: a deck file can hold rules that have nothing to do with
    it. A block runs from its `# Rule` header to the next header or to the end
    of its file, and `FILE_SEP` is what makes "the end of its file" real in a
    concatenated blob.
    """
    wanted = {str(i) for i in identifiers if str(i).strip()}
    if not wanted:
        return {}
    pat = re.compile(r"\b(" + "|".join(re.escape(w) for w in sorted(wanted))
                     + r")\b")
    out: Dict[str, str] = {}
    for chunk in _SEP_RE.split(deck_sources):
        first, _, rest = chunk.partition("\n")
        where = first.strip() or "<unnamed deck source>"
        body = rest if rest else chunk
        heads = list(_RULE_RE.finditer(body))
        for i, m in enumerate(heads):
            end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
            if pat.search(body[m.start():end]):
                out[m.group(1)] = where
    return out


#: A quoted string literal in a deck's own language. Both Ruby spellings, and
#: `"` first so a `'` inside a double-quoted message cannot open one.
_STRING_RE = re.compile(r'"(?:\\.|[^"\\])*"' + r"|'(?:\\.|[^'\\])*'", re.S)


def deck_code_only(text: str) -> str:
    """`text` with every quoted STRING and every `#` COMMENT blanked, keeping
    line and column positions so a later slice still lines up.

    WHY THE IDENTIFIER EXTRACTION READS THIS AND NOT THE RAW BLOCK
    (vibe-ic#2148, polarity ratchet). A rule block is Ruby, but it CARRIES
    English: the `# Rule <id>:` header, the `##` notes beside it, and the
    human-readable violation message the rule prints. An extractor that reads
    a layer identifier out of that block is reading code AND sentences with
    one regex, and a sentence can DENY the very thing the regex mints — the
    #706/#711 class. There are two honest answers, and this is the one that
    removes the referent instead of arguing about it: after this, NO SENTENCE
    REACHES ANY REGEX, so the polarity question has nothing to attach to.

    BLANKED, NOT DELETED. A comment removed by length would move every offset
    after it; the deck's own line structure is what `_rule_blocks` and the
    section-banner boundary are computed from, so what is removed is replaced
    by spaces of the same length and newlines are kept.

    ONE LEFT-TO-RIGHT SCAN, NOT TWO PASSES, AND THE DIFFERENCE WAS MEASURED.
    The first shape blanked string literals with a regex and then cut at the
    first `#`. It is wrong in both directions at once: a `#` inside a message
    (`"DM#{idx}.3"`) is not a comment, and — the one that actually bit — an
    APOSTROPHE INSIDE AN ENGLISH COMMENT opens a string literal that the regex
    then closes at the next quote somewhere far below, blanking live code in
    between. MEASURED on the two open decks in this image: that shape dropped
    `PL.8` from a family of 8 and reduced one deck's layer identifiers from
    six distinct names to NONE. This scan tracks quote state and comment state
    together, per line, exactly as `strip_line_comments` does one module over,
    so an unpaired quote can cost at most the rest of its own line.

    THE DIRECTION OF THE LOSS IS THE SAFE ONE. An identifier that exists only
    inside a comment or a message is no longer read — and this module's answer
    to "no identifier" is a NAMED NOT_MEASURED, never a supplied value.
    """
    out = []
    for line in (text or "").split("\n"):
        buf = list(line)
        quote = None
        for i, ch in enumerate(line):
            if quote:
                buf[i] = " "
                if ch == quote and (i == 0 or line[i - 1] != "\\"):
                    quote = None
            elif ch in "\"'":
                quote = ch
                buf[i] = " "
            elif ch == "#":
                for j in range(i, len(line)):
                    buf[j] = " "
                break
        out.append("".join(buf))
    return "\n".join(out)


def _rule_blocks(deck_sources: str) -> Iterable[Tuple[str, str, str]]:
    """Yield (rule id, the file it is declared in, the block body).

    A block runs from its `# Rule <id>:` header to whichever comes FIRST: the
    next header, a deck SECTION BANNER, or the end of its file. `FILE_SEP` is
    what makes "the end of its file" real in a concatenated blob, and
    `_BANNER_RE` is what stops a block acquiring the next section's preamble —
    see that constant for the measured case it was written from.
    """
    for chunk in _SEP_RE.split(deck_sources):
        first, _, rest = chunk.partition("\n")
        where = first.strip() or "<unnamed deck source>"
        body = rest if rest else chunk
        heads = list(_RULE_RE.finditer(body))
        for i, m in enumerate(heads):
            end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
            banner = _BANNER_RE.search(body, m.end(), end)
            if banner:
                end = banner.start()
            yield m.group(1), where, body[m.start():end]


def die_area_identifiers(deck_sources: str) -> Tuple[Set[str], Optional[str]]:
    """(the deck's own name(s) for the WHOLE-DIE AREA, why-not).

    A die-level rule is one whose measurement is over the die, and the die
    area is the denominator that makes it so. The deck states it itself, in
    two hops that this follows and never guesses:

      1. the REGIONS derived from KLayout's `extent` primitive
         (`CHIP = extent.sized(0.0)`, and a name later assigned one of those);
      2. the SCALARS assigned `<one of those>.area` — or `extent...area`
         directly (`chip_area = extent.sized(0.0).area`).

    Both spellings are in the two open decks this was measured against. A
    windowed rule does NOT use these: it uses the tile size and the chip
    BBOX, which is why this separates the two families without anyone typing
    a rule name.

    Returns the empty set and a reason when the deck names no such scalar,
    because "the deck has no die-area identifier" and "I did not look" must
    never arrive wearing the same sentence.
    """
    regions: Set[str] = {_EXTENT_PRIMITIVE}
    scalars: Set[str] = set()
    # Two passes: a deck may name the region after it is used in an earlier
    # branch, and one pass would miss the later assignment.
    for _ in range(2):
        for raw in deck_sources.splitlines():
            line = raw.split("#", 1)[0]
            m = re.match(r"\s*(" + _IDENT + r")\s*=\s*(.+)$", line)
            if not m:
                continue
            name, rhs = m.group(1), m.group(2)
            words = set(re.findall(_IDENT, rhs))
            if not (words & regions):
                continue
            if re.search(r"\.\s*area\b", rhs):
                scalars.add(name)
            else:
                regions.add(name)
    scalars -= regions
    if scalars:
        return scalars, None
    return set(), (
        f"no line of the deck sources assigns a scalar from the area of a "
        f"region derived from KLayout's `{_EXTENT_PRIMITIVE}` primitive, so "
        f"the deck's own name for the whole-die area is unknown and which of "
        f"its rules measure over the die cannot be read from the deck")


def die_level_density_rules(deck_sources: str, die_area_names: Iterable[str]
                            ) -> Dict[str, str]:
    """{rule id: the deck file it is declared in} for every `# Rule` block
    that references one of the deck's own whole-die AREA identifiers.

    The die area is the denominator of a die-scope coverage measurement. A
    rule that reads it is measuring over the die, and its violation is
    therefore a property of the DIE — which, for a macro somebody else
    places, is the integrator's. Nothing here is a rule name.
    """
    wanted = {str(n) for n in die_area_names if str(n).strip()}
    if not wanted:
        return {}
    pat = re.compile(r"\b(" + "|".join(re.escape(w) for w in sorted(wanted))
                     + r")\b")
    out: Dict[str, str] = {}
    for rid, where, body in _rule_blocks(deck_sources):
        # CODE ONLY. A comment or a violation message that happens to spell the
        # deck's die-area identifier must not make a rule die-level; see
        # `deck_code_only`.
        if pat.search(deck_code_only(body)):
            out.setdefault(rid, where)
    return out


def layer_named_by_rule(deck_sources: str, rule_id: str,
                        candidates: Iterable[str]) -> Optional[str]:
    """The fill-report layer name this rule's own block references, or None.

    The deck names the layer it measures (`metal2.area / chip_area`), and the
    fill config names its layers from the same PDK's streamout layermap, so
    the two spellings meet. A rule whose block names none of them returns
    None and the caller discloses NOT_MEASURED — never a guessed pairing,
    because a wrong pairing publishes another layer's numbers under this
    rule's name.
    """
    cands = sorted({str(c) for c in candidates if str(c).strip()},
                   key=len, reverse=True)
    if not cands:
        return None
    for rid, _where, body in _rule_blocks(deck_sources):
        if rid != rule_id:
            continue
        code = deck_code_only(body)
        for c in cands:
            if re.search(r"\b" + re.escape(c) + r"\b", code):
                return c
    return None


_POLY_RE = re.compile(r"polygon:\s*\(([^)]*)\)")
_ITEM_RE = re.compile(r"<item>(.*?)</item>", re.S)
_CAT_RE = re.compile(r"<category>'?([^<']*)'?</category>")


def _poly_bbox(text: str) -> Optional[Tuple[float, float, float, float]]:
    pts = [p.strip() for p in text.split(";") if p.strip()]
    xs, ys = [], []
    for p in pts:
        try:
            x, y = p.split(",")
            xs.append(float(x))
            ys.append(float(y))
        except ValueError:
            return None
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def rdb_rule_scope(rdb_text: str, die_bbox_um: Optional[List[float]],
                   tol_um: float = 0.001
                   ) -> Tuple[Dict[str, Tuple[int, int]], Optional[str]]:
    """({rule: (items, items whose violation shape IS the die)}, why-not).

    THE SECOND LINK, AND IT IS MEASURED RATHER THAN PARSED. The deck's text
    says which rules measure over the die; the report says what each
    violation actually IS. A die-level density violation is emitted on the
    die extent, so its polygon's bounding box is the die's. Requiring both
    links means a deck-text reading that over-claims cannot attribute
    anything on its own, and a rule the deck does not call die-level cannot
    be attributed because one of its shapes happens to span the die.
    """
    if not isinstance(die_bbox_um, (list, tuple)) or len(die_bbox_um) != 4:
        return {}, ("no die bounding box was supplied, so whether a "
                    "violation's own shape IS the die could not be measured")
    try:
        dx0, dy0, dx1, dy1 = (float(v) for v in die_bbox_um)
    except (TypeError, ValueError):
        return {}, f"the die bounding box {die_bbox_um!r} is not four numbers"
    out: Dict[str, List[int]] = {}
    for body in _ITEM_RE.findall(rdb_text or ""):
        cm = _CAT_RE.search(body)
        if not cm:
            continue
        rid = cm.group(1).strip()
        rec = out.setdefault(rid, [0, 0])
        rec[0] += 1
        for pm in _POLY_RE.finditer(body):
            bb = _poly_bbox(pm.group(1))
            if bb and all(abs(a - b) <= tol_um for a, b in
                          zip(bb, (dx0, dy0, dx1, dy1))):
                rec[1] += 1
                break
    return {k: (v[0], v[1]) for k, v in out.items()}, None


def marker_layers_from_die_finishing(report: Dict[str, Any]
                                     ) -> Tuple[List[Tuple[int, int]],
                                                Optional[str]]:
    """(the layer/datatypes die finishing recorded ADDING, why-not).

    `new_layer` is the discriminator the ring check already records: a layer the
    ring INTRODUCED to this GDS is a layer the design did not have, which is
    what a die-level marker is. Layers the ring merely added shapes to are the
    design's own and are not markers.
    """
    ring = ((report or {}).get("seal_ring") or {}).get("ring_check") or {}
    added = ring.get("added_geometry")
    if not isinstance(added, list):
        return [], ("reports/phase3/die_finishing.json records no "
                    "`seal_ring.ring_check.added_geometry`, so what a ring "
                    "added to this GDS is unknown rather than empty")
    out: List[Tuple[int, int]] = []
    for rec in added:
        if not isinstance(rec, dict) or not rec.get("new_layer"):
            continue
        spec = str(rec.get("layer") or "")
        m = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*", spec)
        if m:
            out.append((int(m.group(1)), int(m.group(2))))
    return out, None


#: The density fill's own report — the ONLY source of the achieved / floor /
#: legal-ceiling triple this module discloses, and of the die bounding box it
#: measures a violation's scope against. vibe-ic#2135 made that program
#: measure and publish both; nothing here re-derives or re-types either.
FILL_REPORT_REL = "reports/phase3/cmp_fill_emit.json"


def fill_report_facts(report: Optional[Dict[str, Any]]
                      ) -> Tuple[Optional[List[float]], Dict[str, Dict[str, Any]],
                                 Optional[str]]:
    """(die bbox in um, {layer: {achieved, floor, legal_ceiling}}, why-not).

    Read, never typed. `achieved` is the WORST of the whole-die and
    worst-window figures — the same basis the fill's own promotion logic
    uses, so the number disclosed to the integrator is the one the fill was
    judged on. A layer that carries no numeric density yields no entry rather
    than a zero.
    """
    if not isinstance(report, dict):
        return None, {}, (f"{FILL_REPORT_REL} was not read as a mapping, so "
                          f"neither the die bounding box nor any achieved / "
                          f"floor / legal-ceiling figure is available")
    bbox = ((report.get("keepout") or {}).get("measurement_bbox_um")
            if isinstance(report.get("keepout"), dict) else None)
    facts: Dict[str, Dict[str, Any]] = {}
    for lay in report.get("layers") or []:
        if not isinstance(lay, dict) or not lay.get("name"):
            continue
        vals = [lay.get(k) for k in ("density_after", "worst_window_after")]
        vals = [float(v) for v in vals
                if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if not vals:
            continue
        facts[str(lay["name"])] = {
            "achieved": min(vals),
            "floor": lay.get("floor", report.get("floor")),
            "legal_ceiling": lay.get("ceiling_any_fill"),
        }
    why = None
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        bbox = None
        why = (f"{FILL_REPORT_REL} carries no "
               f"`keepout.measurement_bbox_um`, so the die bounding box a "
               f"violation's scope is measured against is unknown")
    return (list(bbox) if bbox else None), facts, why


def density_disclosure(rules: Dict[str, str], deck_sources: Optional[str],
                       facts: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One record per rule: what the integrator is being asked to close.

    Every number comes from the fill report. A rule whose layer this cannot
    pair, or whose layer the fill never measured, is disclosed as
    NOT_MEASURED naming what was looked for — an attributed rule with no
    numbers is still stated, because a silent one is exactly the hidden pass
    this whole mechanism exists to prevent.
    """
    out: List[Dict[str, Any]] = []
    for rid in sorted(rules):
        rec: Dict[str, Any] = {"rule": rid, "deck_source": rules[rid]}
        layer = (layer_named_by_rule(deck_sources, rid, facts.keys())
                 if deck_sources else None)
        rec["layer"] = layer or NOT_MEASURED
        if layer and layer in facts:
            f = facts[layer]
            rec["achieved"] = f["achieved"]
            rec["floor"] = f["floor"]
            rec["legal_ceiling"] = f["legal_ceiling"]
        else:
            rec["achieved"] = rec["floor"] = rec["legal_ceiling"] = NOT_MEASURED
            rec["not_measured"] = (
                f"no layer the density fill measured "
                f"({', '.join(sorted(facts)) or 'none'}) is named in this "
                f"rule's own deck block, so its achieved / floor / legal "
                f"ceiling are unknown rather than absent")
        out.append(rec)
    return out


def handoff_record(rec: Dict[str, Any]) -> Dict[str, Any]:
    """The `integrator_requirements` record that travels WITH the macro.

    The macro's abstract says what it occupies; this says what it did NOT
    close and is handing upward. Without it the attribution is a note in a
    report the next flow up never opens, which is indistinguishable from a
    waiver.
    """
    return {
        "record": "integrator_requirements",
        "producer": "die_level_deck_rule_attribution",
        "deliverable": rec.get("deliverable"),
        "reason": ("these are DIE-LEVEL rules: their measurement window is "
                   "the whole die. This delivery is a macro placed inside "
                   "somebody else's die, and the die's own top-level fill is "
                   "what closes them. They are NOT closed here and are NOT "
                   "waived — they are the integrator's to close."),
        "die_bbox_um": rec.get("die_bbox_um"),
        "requirements": rec.get("density_disclosure") or [],
        "unattributed_violations": rec.get("unattributed_total"),
        "tier": rec.get("drc_tier"),
    }


def parse_layer_spec(spec: str) -> Optional[Tuple[int, int]]:
    m = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*", spec or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def attribute(per_rule: Dict[str, int], family: Dict[str, str],
              deliverable: Optional[str], ring_state: Optional[str],
              markers: List[Tuple[int, int]],
              not_measured: Dict[str, str],
              density_family: Optional[Dict[str, str]] = None,
              rule_scope: Optional[Dict[str, Tuple[int, int]]] = None,
              deck_sources: Optional[str] = None,
              facts: Optional[Dict[str, Dict[str, Any]]] = None,
              die_bbox_um: Optional[List[float]] = None) -> Dict[str, Any]:
    """The split, every reason it could not be made, and — under HARDMACRO
    ONLY — which die-level DENSITY rules the integrator carries.

    THE ATTRIBUTION IS GATED ON THE DELIVERABLE AND ON NOTHING ELSE BEING
    ASSUMED (vibe-ic#2148). Three conditions, all measured, all fail-closed:

      * `deliverable` is HARDMACRO. A DIE owns its own die-level density and
        a DIE deliverable is never attributed, whatever the deck says.
      * the rule is in the DECK-DERIVED die-level density family (its own
        block reads the deck's whole-die area identifier).
      * EVERY violation of that rule is emitted on the die itself, measured
        from the report's own shapes against the die bounding box the density
        fill published. A rule with even one non-die-scope violation is left
        where it is — a partial attribution would hide a real one.
    """
    counts = {str(k): int(v) for k, v in (per_rule or {}).items()}
    total = sum(counts.values())
    fam = {k: v for k, v in counts.items() if k in family and v}
    fam_total = sum(fam.values())
    density_family = dict(density_family or {})
    rule_scope = dict(rule_scope or {})
    facts = dict(facts or {})

    is_macro = deliverable == DELIVERABLE_HARDMACRO
    attributed: Dict[str, int] = {}
    refused: Dict[str, str] = {}
    for rid, n in counts.items():
        if not n or rid not in density_family or rid in fam:
            continue
        if not is_macro:
            refused[rid] = (
                f"deliverable={deliverable or NOT_MEASURED}: a die owns its "
                f"own die-level density, so this is this design's own DRC "
                f"and is NOT attributed")
            continue
        seen, die_scope = rule_scope.get(rid, (0, 0))
        if seen == 0:
            refused[rid] = ("the report's own violation shapes for this rule "
                            "were not read, so whether they are the die "
                            "could not be measured")
            continue
        if die_scope != seen:
            refused[rid] = (
                f"only {die_scope} of this rule's {seen} violation shape(s) "
                f"IS the die, so it is not purely die-scope here")
            continue
        attributed[rid] = n

    att_total = sum(attributed.values())
    rec: Dict[str, Any] = {
        "program": "die_level_deck_rule_attribution",
        "deliverable": deliverable or NOT_MEASURED,
        "seal_ring_state": ring_state or NOT_MEASURED,
        "die_level_marker_layers": [f"{a}/{b}" for a, b in markers],
        "total": total,
        "die_level_rule_violations": fam_total,
        "die_level_rules": dict(sorted(fam.items(), key=lambda kv: -kv[1])),
        "die_level_rule_source": {k: family[k] for k in sorted(fam)},
        "other_violations": total - fam_total,
        "fraction": round(fam_total / total, 4) if total else 0.0,
        "die_level_density_rules_in_deck": dict(sorted(density_family.items())),
        "attributed_density_rules": dict(sorted(attributed.items(),
                                                key=lambda kv: -kv[1])),
        "attributed_density_violations": att_total,
        "density_attribution_refused": dict(sorted(refused.items())),
        "die_bbox_um": die_bbox_um,
        "not_measured": dict(not_measured),
    }
    rec["density_disclosure"] = density_disclosure(
        {k: density_family[k] for k in attributed}, deck_sources, facts)
    rec["unattributed_total"] = total - fam_total - att_total
    if att_total and is_macro:
        rec["verdict"] = DENSITY_ATTRIBUTED
    elif fam_total and is_macro:
        rec["verdict"] = "DIE_LEVEL_RULES_ON_A_HARDMACRO"
    else:
        rec["verdict"] = "NOTHING_TO_ATTRIBUTE"
    # The TIER is only earned when the attribution accounts for EVERYTHING
    # that fired. One unattributed violation and the delivery is still FAIL —
    # the tier says "the only things left are the integrator's", never "the
    # rest were excused".
    rec["drc_tier"] = (TIER_PASS_WITH_ATTRIBUTION
                       if att_total and is_macro and rec["unattributed_total"] == 0
                       else None)
    return rec


def _tier_word(rec: Dict[str, Any]) -> str:
    """The tier, or the reason there is none. A delivery whose attribution
    does not account for everything that fired is still FAIL, and saying so
    in the same sentence is what stops the attribution reading as a pass."""
    tier = rec.get("drc_tier")
    if tier:
        return str(tier)
    return ("none — a remainder is this design's own, so the DRC verdict is "
            "unchanged")


def summarize(rec: Dict[str, Any]) -> str:
    """The one line the DRC step's own summary carries.

    A NOT_MEASURED link is stated even when there is nothing to attribute,
    because "no die-level rules fired" and "I could not tell which rules are
    die-level" are different facts.
    """
    nm = rec.get("not_measured") or {}
    tail = ("" if not nm else
            " | NOT_MEASURED: " + "; ".join(f"{k}: {v}" for k, v in
                                            sorted(nm.items())))
    if rec.get("verdict") == DENSITY_ATTRIBUTED:
        # BY NAME AND BY NUMBER, or not at all. A reader must be able to check
        # the claim without opening another file, and the integrator must be
        # able to see what they are being handed.
        parts = []
        for d in rec.get("density_disclosure") or []:
            parts.append(
                f"{d.get('rule')} (layer {d.get('layer')}: achieved "
                f"{d.get('achieved')}, floor {d.get('floor')}, legal ceiling "
                f"{d.get('legal_ceiling')})")
        return (f"DIE-LEVEL DENSITY RULES ATTRIBUTED TO THE INTEGRATOR under "
                f"deliverable={rec.get('deliverable')}: "
                f"{rec.get('attributed_density_violations')}/{rec.get('total')} "
                f"sign-off DRC violations are rules the PDK's own deck measures "
                f"over the WHOLE DIE, and every one of their violation shapes IS "
                f"the die — a macro is placed inside somebody else's die and that "
                f"die's top-level fill is what closes them. NOT waived, NOT "
                f"closed here: {'; '.join(parts) or '(no rule disclosed)'}. "
                f"Unattributed remainder: {rec.get('unattributed_total')}. "
                f"DRC tier: {_tier_word(rec)}" + tail)
    if rec.get("verdict") != "DIE_LEVEL_RULES_ON_A_HARDMACRO":
        # TWO DIFFERENT FACTS, and one sentence for both would be a lie in one
        # of them: nothing die-level fired, versus die-level rules fired on a
        # delivery that OWNS its die structures and is therefore entitled to
        # them. Both are "nothing to attribute"; only one is "nothing fired".
        refused = rec.get("density_attribution_refused") or {}
        if refused:
            # A REFUSAL IS A FACT AND MUST BE SAID. The old sentence for this
            # branch was "no die-level deck rule fired", which is false when
            # die-level DENSITY rules fired and were deliberately not
            # attributed — the DIE case this mechanism exists to leave alone.
            return (f"die-level density rules fired and were NOT attributed "
                    f"({', '.join(sorted(refused))}): "
                    f"{'; '.join(f'{k}: {v}' for k, v in sorted(refused.items()))}"
                    + tail)
        if rec.get("die_level_rule_violations"):
            return (f"{rec['die_level_rule_violations']}/{rec['total']} "
                    f"sign-off DRC violations are die-level deck rules, and "
                    f"this delivery is deliverable={rec.get('deliverable')} — "
                    f"a die owns its seal ring and its die-level markers, so "
                    f"they are this design's own DRC and are NOT "
                    f"re-attributed" + tail)
        return (f"no die-level deck rule fired on this delivery "
                f"(deliverable={rec.get('deliverable')})" + tail)
    top = list(rec["die_level_rules"].items())[:4]
    return (f"DIE-LEVEL DECK RULES ON A HARDMACRO DELIVERY: "
            f"{rec['die_level_rule_violations']}/{rec['total']} "
            f"({rec['fraction']:.0%}) of the sign-off DRC violations are rules "
            f"the PDK's own deck gates on the die-level marker layer(s) "
            f"{', '.join(rec['die_level_marker_layers']) or '(unnamed)'} — "
            f"die structures belong to the PARENT die this macro is placed in, "
            f"so this count is NOT a statement about this design's layout "
            f"(top: {dict(top)}). The design's own remainder is "
            f"{rec['other_violations']}." + tail)


def run(project: Path, per_rule: Dict[str, int],
        deck_sources: Optional[str] = None,
        marker: Optional[str] = None,
        rdb_text: Optional[str] = None,
        fill_report: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    not_measured: Dict[str, str] = {}

    deliverable: Optional[str] = None
    doc, why = _td.load(project / _td.DECLARATION_REL)
    if why is not None or not isinstance(doc, dict):
        not_measured["deliverable"] = (
            why or f"{_td.DECLARATION_REL} is not a mapping")
    else:
        got = _td.answer(doc, "deliverable")
        if _td.is_answered(got):
            deliverable = str(got)
        else:
            not_measured["deliverable"] = (
                f"{_td.DECLARATION_REL} answers no `deliverable`, so what "
                f"leaves this flow is undeclared rather than a die")

    ring_state: Optional[str] = None
    markers: List[Tuple[int, int]] = []
    explicit = parse_layer_spec(marker) if marker else None
    if explicit:
        markers = [explicit]
    df_path = project / "reports" / "phase3" / "die_finishing.json"
    if df_path.is_file():
        try:
            df = json.loads(df_path.read_text(errors="replace"))
        except (OSError, ValueError) as exc:
            not_measured["die_finishing"] = f"{df_path} could not be read: {exc}"
            df = {}
        ring_state = ((df.get("seal_ring") or {}).get("state")
                      if isinstance(df, dict) else None)
        if not markers:
            markers, mwhy = marker_layers_from_die_finishing(df)
            if mwhy and not markers:
                not_measured["die_level_marker_layers"] = mwhy
    elif not markers:
        not_measured["die_level_marker_layers"] = (
            f"{df_path} is not on disk and no --marker was given, so no "
            f"die-level marker layer is named")

    family: Dict[str, str] = {}
    if markers and deck_sources:
        names: Set[str] = set()
        whys: List[str] = []
        for lay, dat in markers:
            got, w = marker_identifiers(deck_sources, lay, dat)
            names |= got
            if w:
                whys.append(w)
        if names:
            family = rules_gated_on(deck_sources, names)
        if whys and not family:
            not_measured["die_level_rules"] = "; ".join(whys)
    elif markers and not deck_sources:
        not_measured["die_level_rules"] = (
            "no deck sources were supplied, so which rules the PDK's deck "
            "gates on the die-level marker could not be read from the deck "
            "itself — and it must not be guessed from a rule-name list here")

    # --- the die-level DENSITY family (vibe-ic#2148) ----------------------
    density_family: Dict[str, str] = {}
    if deck_sources:
        die_names, dwhy = die_area_identifiers(deck_sources)
        if die_names:
            density_family = die_level_density_rules(deck_sources, die_names)
            if not density_family:
                not_measured["die_level_density_rules"] = (
                    f"the deck names its whole-die area "
                    f"({', '.join(sorted(die_names))}) but no `# Rule` block "
                    f"reads it, so this deck declares no die-level density "
                    f"rule")
        else:
            not_measured["die_level_density_rules"] = dwhy or "unknown"
    else:
        not_measured["die_level_density_rules"] = (
            "no deck sources were supplied, so which of the deck's rules "
            "measure over the whole die could not be read from the deck "
            "itself — and it must not be guessed from a rule-name list here")

    if fill_report is None:
        fp = project / FILL_REPORT_REL
        if fp.is_file():
            try:
                fill_report = json.loads(fp.read_text(errors="replace"))
            except (OSError, ValueError) as exc:
                not_measured["fill_report"] = f"{fp} could not be read: {exc}"
        else:
            not_measured["fill_report"] = (
                f"{fp} is not on disk, so the density fill published no die "
                f"bounding box and no achieved / floor / legal-ceiling figure "
                f"for any layer")
    die_bbox, facts, fwhy = fill_report_facts(fill_report)
    if fwhy:
        not_measured.setdefault("fill_report", fwhy)

    rule_scope: Dict[str, Tuple[int, int]] = {}
    if rdb_text is None:
        rp = project / "reports" / "phase3" / "drc_signoff.rpt"
        if rp.is_file():
            try:
                rdb_text = rp.read_text(errors="replace")
            except OSError as exc:
                not_measured["violation_shapes"] = f"{rp}: {exc}"
        else:
            not_measured["violation_shapes"] = (
                f"{rp} is not on disk, so no violation's own shape could be "
                f"measured against the die")
    if rdb_text is not None:
        rule_scope, swhy = rdb_rule_scope(rdb_text, die_bbox)
        if swhy:
            not_measured.setdefault("violation_shapes", swhy)

    return attribute(per_rule, family, deliverable, ring_state, markers,
                     not_measured, density_family, rule_scope, deck_sources,
                     facts, die_bbox)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("project")
    p.add_argument("--per-rule", required=True,
                   help="JSON file: {rule: count} from the sign-off DRC")
    p.add_argument("--deck-sources", default=None,
                   help=f"file holding the PDK's rule-deck sources, each file "
                        f"preceded by a '{FILE_SEP}<path>' line")
    p.add_argument("--marker", default=None,
                   help="'layer/datatype' of the die-level marker layer; "
                        "overrides what die finishing recorded")
    p.add_argument("--rdb", default=None,
                   help="KLayout report database (.lyrdb) whose violation "
                        "shapes decide whether a rule is die-scope here; "
                        "defaults to reports/phase3/drc_signoff.rpt")
    p.add_argument("--fill-report", default=None,
                   help=f"the density fill's own report; defaults to "
                        f"{FILL_REPORT_REL}. It supplies the die bounding box "
                        f"and every achieved / floor / legal-ceiling figure — "
                        f"none of them is typed here")
    p.add_argument("--handoff", default=None,
                   help=f"write the {HANDOFF_NAME} record for the integrator "
                        f"to this path (or into this directory)")
    p.add_argument("--json", default=None)
    args = p.parse_args(argv if argv is not None else sys.argv[1:])
    try:
        per_rule = json.loads(Path(args.per_rule).read_text())
    except (OSError, ValueError) as exc:
        print(f"could not read --per-rule {args.per_rule}: {exc}")
        return 2
    if not isinstance(per_rule, dict):
        print(f"--per-rule {args.per_rule} is not a rule->count mapping")
        return 2
    src = None
    if args.deck_sources:
        try:
            src = Path(args.deck_sources).read_text(errors="replace")
        except OSError as exc:
            print(f"could not read --deck-sources {args.deck_sources}: {exc}")
            return 2
    rdb_text = None
    if args.rdb:
        try:
            rdb_text = Path(args.rdb).read_text(errors="replace")
        except OSError as exc:
            print(f"could not read --rdb {args.rdb}: {exc}")
            return 2
    fill_rep = None
    if args.fill_report:
        try:
            fill_rep = json.loads(Path(args.fill_report).read_text())
        except (OSError, ValueError) as exc:
            print(f"could not read --fill-report {args.fill_report}: {exc}")
            return 2
    rec = run(Path(args.project), per_rule, src, args.marker, rdb_text,
              fill_rep)
    if args.handoff and rec.get("verdict") == DENSITY_ATTRIBUTED:
        out = Path(args.handoff)
        # A DIRECTORY that does not exist yet is still a directory: the macro's
        # delivery directory is created by the step that stages the abstract,
        # which may not have run when this is called. Deciding by `is_dir()`
        # alone wrote a FILE named after the directory, and the record then sat
        # somewhere no reader of the delivery would look.
        if out.name != HANDOFF_NAME:
            out = out / HANDOFF_NAME
        out.parent.mkdir(parents=True, exist_ok=True)
        # ATOMIC (vibe-ic#1082). A handoff record is read by the next flow up
        # and by `flow_compliance_check`; a half-written one is a delivery that
        # names a subset of what it is handing over, which is the exact defect
        # the partial-handoff refusal exists to catch — it must not be
        # manufacturable by an interrupted write.
        _atomic.write_json(out, handoff_record(rec))
        rec["handoff_record"] = str(out)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        _atomic.write_json(Path(args.json), rec)
    print(summarize(rec))
    return 1 if rec["verdict"] in ("DIE_LEVEL_RULES_ON_A_HARDMACRO",
                                   DENSITY_ATTRIBUTED) else 0


if __name__ == "__main__":
    raise SystemExit(main())
