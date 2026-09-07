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

NOT_MEASURED = "NOT_MEASURED"

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tapeout_declaration as _td  # noqa: E402

#: Bound from the declaration schema that OWNS the word, never re-typed here:
#: a delivery that is a macro somebody else places is the whole subject of this
#: module and it must be spelled by the module that defines the enum.
DELIVERABLE_HARDMACRO = _td.DELIVERABLE_HARDMACRO


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


def parse_layer_spec(spec: str) -> Optional[Tuple[int, int]]:
    m = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*", spec or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def attribute(per_rule: Dict[str, int], family: Dict[str, str],
              deliverable: Optional[str], ring_state: Optional[str],
              markers: List[Tuple[int, int]],
              not_measured: Dict[str, str]) -> Dict[str, Any]:
    """The split, and every reason it could not be made. Never a verdict."""
    counts = {str(k): int(v) for k, v in (per_rule or {}).items()}
    total = sum(counts.values())
    fam = {k: v for k, v in counts.items() if k in family and v}
    fam_total = sum(fam.values())
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
        "not_measured": dict(not_measured),
    }
    rec["verdict"] = ("DIE_LEVEL_RULES_ON_A_HARDMACRO"
                      if fam_total and deliverable == DELIVERABLE_HARDMACRO
                      else "NOTHING_TO_ATTRIBUTE")
    return rec


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
    if rec.get("verdict") != "DIE_LEVEL_RULES_ON_A_HARDMACRO":
        # TWO DIFFERENT FACTS, and one sentence for both would be a lie in one
        # of them: nothing die-level fired, versus die-level rules fired on a
        # delivery that OWNS its die structures and is therefore entitled to
        # them. Both are "nothing to attribute"; only one is "nothing fired".
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
        marker: Optional[str] = None) -> Dict[str, Any]:
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

    return attribute(per_rule, family, deliverable, ring_state, markers,
                     not_measured)


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
    rec = run(Path(args.project), per_rule, src, args.marker)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(rec, indent=2) + "\n")
    print(summarize(rec))
    return 1 if rec["verdict"] == "DIE_LEVEL_RULES_ON_A_HARDMACRO" else 0


if __name__ == "__main__":
    raise SystemExit(main())
