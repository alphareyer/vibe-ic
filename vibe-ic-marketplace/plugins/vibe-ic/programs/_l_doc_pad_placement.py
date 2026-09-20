#!/usr/bin/env python3
"""_l_doc_pad_placement — read what the DESIGN ALREADY WROTE DOWN about its
pad ring, out of its own L-documents, and out of the IO library those
documents delegate to.

WHY THIS MODULE EXISTS
======================
MEASURED on the self-tape-out re-run: `pad_assignment_gen` reported verdict
NOT_ASKED with 0 of the 8 `2B_pad_ring` questions answered, step 15.5ic
refused, PnR would not route, and 17 steps stayed blocked. The reason was NOT
that the design is silent. Its external-interface document carries a
`Physical Pad Placement` section that partitions EVERY top-level port across
the four die edges, and its product-metadata document states that the pad
count is not pinned BECAUSE it follows from that port list. The consumer was
never wired to the producer. This module is that wiring.

WHAT IT READS, AND WHAT IT REFUSES TO READ
==========================================
THE DESIGN'S OWN DOCUMENTS
    * the per-side PORT partition, from a pad-placement table whose rows name
      a die side and list backticked signal names;
    * the top-level PORT LIST, so a bus written `<name>[<expr>]` can be
      expanded into one entry per bit — which is the design's OWN rule, stated
      in the same table ("one pad per bit");
    * the parameter defaults, so `<expr>` resolves to an integer FROM A
      DECLARED VALUE or not at all;
    * a stated minimum pad-to-pad distance, RECORDED AND NEVER CONSUMED (see
      the next paragraph).

THE NEAR MISS THIS MODULE REFUSES, STATED BEFORE THE CODE
=========================================================
The pad-placement section states a minimum distance BETWEEN PADS on one side.
Upstream's `PAD_EDGE_SPACING` is the distance FROM THE DIE EDGE TO THE IO ROW.
They are different lengths that happen to share a unit. Reading the first as
the second would put a real-looking number into a physical config that the
design never stated, which is the exact substitution the whole step exists to
refuse. So `min_pad_distance_um` is parsed, carried in the record, and never
mapped to a config variable by anything in this file.

THE DELEGATION, AND ITS LIMIT
=============================
A document may DELEGATE the IO cell library to the PDK in so many words. When
it does, the PDK's own IO-library configuration is a DECLARED source for the
library-owned variables, read verbatim with a file and a line number, exactly
as `_pad_ring` already reads a pad SITE out of a PDK file and nowhere else.
A variable that file does not declare stays UNANSWERED — there is no default
anywhere in this module, and a design that states no pad placement at all
delegates nothing, so the PDK is not read for it either.

WHAT IT CANNOT ANSWER, AND SAYS SO
==================================
`PAD_SOUTH`/`PAD_EAST`/`PAD_NORTH`/`PAD_WEST` and `SIGNAL_MAP` are lists of
NETLIST INSTANCES, not of ports — upstream resolves each against the block.
A document can partition PORTS and this module does; it cannot name instances
that do not exist, and it does not try. The partition is carried in the record
so a reader can see what the design DID answer beside what is still owed.

chip-AGNOSTIC: no chip, vendor, SKU, foundry, library or process-node literal.
"""
from __future__ import annotations

# --- sibling-import path (vibe-ic#2104) ------------------------------------
# `programs/` is a flat directory whose modules import each other by BARE
# name. Python puts a file's own directory on `sys.path` only when that file
# is run as `__main__`; under `importlib.util.spec_from_file_location` — how
# the gates, the wiring audit and much of the suite load a program — it does
# not, so every bare sibling import below raises ModuleNotFoundError. Measured
# on the base tree: 454 of the 1385 top-level programs died that way. Restore
# the condition the file is written for. Idempotent, and the same shape the
# sibling programs that already carry it use.
import os as _os                                                    # noqa: E402
import sys as _sys                                                  # noqa: E402

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
# ---------------------------------------------------------------------------


import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# THE POLARITY OF THE SENTENCE A VALUE IS READ OUT OF (vibe-ic#712).
#
# Everything this module reads is a design's own ENGLISH document, which is
# precisely where denial is spellable and gets spelled — the two defects that
# opened #712 (`pdk_target` "This block is NOT targeted at <PDK>", and
# `die_area_budget_um` "REMOVED, not translated") were both read out of exactly
# this kind of file. `prose_polarity_consulted_check` flagged both functions
# below and it was right: each takes text out of a document and writes it into
# a declared field, and neither asked whether the document was denying it.
#
# ONE VOCABULARY, IMPORTED, NEVER RE-SPELLED. `_prose_polarity`'s header names
# the failure a private copy causes ("three private copies of it is how the
# divergence happened"), and both names below are CALLED — an import whose only
# consumer is the test asserting the import is the "a call that can never fire
# is a green light rather than a check" shape this repo already names.
#
# THE REACH IS THE LINE, AND THAT IS THIS INPUT'S OWN SHAPE, NOT A SECOND COPY
# OF "WHERE DOES A SENTENCE END". A markdown table ROW is a self-contained
# record — `floorplan_contract` measured that same rule for the same reason,
# "otherwise an unrelated cell in a neighbouring row would veto a valid die" —
# so the row predicates ask `is_denied` about the ROW. The one value here that
# is genuinely prose, the stated minimum distance, is scoped by the house
# `sentence_scope` with `extra_breaks=("\n",)`: this document wraps its prose a
# line at a time, and a bare newline is a record break in a table.
#
# WHICH DIRECTION EACH GUARD FAILS, STATED. Retracting a value nothing denied
# is the SILENT direction (`_prose_polarity`: "the extractor reports less than
# it read and no gate goes red"), so every predicate here is bracket-blind by
# default — `is_denied` blanks bracketed spans — and none of them widens past
# the record it is asked about.
from _prose_polarity import is_denied as _is_denied
from _prose_polarity import sentence_scope as _sentence_scope

#: Where a project keeps design input documents, in the order
#: `phase3_one_shot_runner._l9_declared_die_area` already scans them, plus the
#: converted mirror phase1 lands beside its generated docs.
DOC_DIRS: Tuple[str, ...] = (
    "input/docs", "phase1/input_doc", "phase1/generated_docs",
)

#: The document stems that may carry a pad placement. Globs, not names: the
#: layer numbering is the corpus's, the words are the section's own.
DOC_GLOBS: Tuple[str, ...] = (
    "L3*", "L9*", "*interface*", "*floorplan*", "*constraint*",
)

#: The four die edges, in `_pad_ring.SIDES` order, each with the words a
#: document may name it by. Matched case-insensitively as WHOLE WORDS so a
#: row reading "Northbound" or a stray "n" never claims a side.
SIDE_WORDS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("S", ("south", "s")),
    ("E", ("east", "e")),
    ("N", ("north", "n")),
    ("W", ("west", "w")),
)

#: A heading that introduces a pad placement. Both tokens must be present, so
#: a "Pad Ring Budget" or a "Placement Density" heading does not match.
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(?P<title>.+?)\s*$", re.M)
_PAD_TOKEN_RE = re.compile(r"\bpad\b", re.I)
_PLACEMENT_TOKEN_RE = re.compile(r"placement|assignment", re.I)

#: A markdown table row. Leading/trailing pipes optional, per CommonMark.
_ROW_RE = re.compile(r"^\s*\|(?P<body>.*)\|\s*$", re.M)
#: A separator row (`|---|---|`) — a table's shape, never its data.
_SEP_RE = re.compile(r"^[\s|:\-]+$")

#: A backticked token. The documents quote every signal name this way, and
#: quoting is what separates a signal from the prose around it.
_TICKED_RE = re.compile(r"`([^`]+)`")

#: `<name>` or `<name>[<expr>]`. The bit expression is NOT evaluated here.
_SIGNAL_RE = re.compile(r"^(?P<base>[A-Za-z_][A-Za-z_0-9$]*)"
                        r"(?:\[(?P<expr>[^\]]+)\])?$")

#: A stated minimum pad-to-pad distance. Parsed, carried, and NEVER consumed
#: as an edge spacing — see this module's header.
_MIN_DISTANCE_RE = re.compile(
    r"min[_\s]*distance\s*[=:]\s*(?P<v>\d+(?:\.\d+)?)", re.I)

#: A document sentence that hands the IO cell library to the PDK. Both tokens
#: required, on one line, so a line merely mentioning the PDK does not count.
_IO_TOKEN_RE = re.compile(r"\bi/?o\s+(?:cell|pad)\b", re.I)
_PDK_TOKEN_RE = re.compile(r"\bpdk\b", re.I)

#: `<expr>` forms this module resolves. An integer, a declared parameter, or a
#: parameter minus an integer. Anything else is NOT RESOLVED — never guessed.
_INT_RE = re.compile(r"^\s*(?P<n>\d+)\s*$")
_PARAM_MINUS_RE = re.compile(
    r"^\s*(?P<p>[A-Za-z_][A-Za-z_0-9$]*)\s*(?:-\s*(?P<k>\d+)\s*)?$")

#: A parameter default row: a backticked identifier, then an integer.
_PARAM_ROW_MIN_CELLS = 2


class PadPlacementError(Exception):
    """A document said something this module will not interpret."""

    def __init__(self, rule: str, message: str) -> None:
        super().__init__(message)
        self.rule = rule
        self.message = message


@dataclass
class PadPlacement:
    """What one document states about the pad ring."""

    source: str                                   # project-relative path
    heading: str                                  # the section's own title
    side_signals: Dict[str, List[str]] = field(default_factory=dict)
    # A row may state a design-owned GROUP ("data bus", "GPIO pins")
    # instead of spelling every member.  Keep that statement separate from
    # exact signal tokens: only a consumer which also owns the declared port
    # population can resolve it, and an unresolved group must never silently
    # become an empty side.
    side_groups: Dict[str, str] = field(default_factory=dict)
    min_pad_distance_um: Optional[float] = None   # RECORDED, NEVER CONSUMED
    delegates_io_library_to_pdk: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "heading": self.heading,
            "side_signals": {s: list(v) for s, v in self.side_signals.items()},
            "side_groups": dict(self.side_groups),
            "min_pad_distance_um": self.min_pad_distance_um,
            "min_pad_distance_is_not_edge_spacing": True,
            "delegates_io_library_to_pdk": self.delegates_io_library_to_pdk,
        }


# --------------------------------------------------------------------------- #
# markdown
# --------------------------------------------------------------------------- #
def _cells(line: str) -> Optional[List[str]]:
    m = _ROW_RE.match(line)
    if m is None:
        return None
    body = m.group("body")
    if _SEP_RE.match(body):
        return None
    return [c.strip() for c in body.split("|")]


def _sections(text: str) -> List[Tuple[str, str]]:
    """[(heading title, body)] for every ATX heading, in document order."""
    out: List[Tuple[str, str]] = []
    marks = list(_HEADING_RE.finditer(text))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out.append((m.group("title"), text[m.end():end]))
    return out


def _side_of(cell: str) -> Optional[str]:
    """The die side a table cell names, or None.

    Whole-word match on the ASCII cardinal words. A cell naming two sides is
    ambiguous and names none: a row that cannot say which edge it is about
    must not be read as being about either.
    """
    lowered = cell.lower()
    hits = [side for side, words in SIDE_WORDS
            if any(re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", lowered)
                   for w in words)]
    return hits[0] if len(hits) == 1 else None


def parse_pad_placement(text: str, source: str) -> Optional[PadPlacement]:
    """The pad placement one document states, or None when it states none.

    None is "this document does not carry a pad placement". It is NEVER an
    empty placement: a design that says nothing about its pad ring and a
    design whose pad ring is empty are different facts and only one of them
    can be checked.
    """
    # A line that DENIES the delegation states the opposite of one that makes
    # it — "the I/O cells are NOT taken from the PDK" carries both tokens and
    # is not a delegation. The line is already this predicate's whole record
    # ("Both tokens required, on one line"), so the polarity question is asked
    # of the same line and of nothing wider.
    delegates = any(_IO_TOKEN_RE.search(ln) and _PDK_TOKEN_RE.search(ln)
                    and _is_denied(ln) is None
                    for ln in text.splitlines())
    for title, body in _sections(text):
        if not (_PAD_TOKEN_RE.search(title)
                and _PLACEMENT_TOKEN_RE.search(title)):
            continue
        side_signals: Dict[str, List[str]] = {}
        side_groups: Dict[str, str] = {}
        seen_sides = set()
        for line in body.splitlines():
            cells = _cells(line)
            if not cells or len(cells) < 2:
                continue
            side = _side_of(cells[0])
            if side is None:
                continue
            # A ROW THAT DENIES ITSELF DECLARES NOTHING. "| N | `a`, `b` | not
            # bonded on this revision |" prints a partition the design has
            # withdrawn, and publishing it puts real-looking signals on an edge
            # the document says they are not on. Asked of the ROW, because the
            # row is the record: a denial in a NEIGHBOURING row is that row's.
            if _is_denied(line) is not None:
                continue
            signals = [t.strip() for t in _TICKED_RE.findall(cells[1])]
            signals = [s for s in signals if s]
            group = cells[1].strip() if not signals else ""
            if not signals and not group:
                continue
            if side in seen_sides:
                raise PadPlacementError(
                    "L_DOC_PAD_SIDE_DECLARED_TWICE",
                    f"{source}: section {title!r} assigns side {side} twice, "
                    "to two different rows. Nothing here "
                    f"can say which row the design meant.")
            seen_sides.add(side)
            if signals:
                side_signals[side] = signals
            else:
                side_groups[side] = group
        if not side_signals and not side_groups:
            continue
        md = _MIN_DISTANCE_RE.search(body)
        # The one PROSE value in this section, so it gets the house prose
        # reach rather than a row. `extra_breaks=("\n",)` keeps that reach
        # from crossing out of one line into an unrelated table row.
        #
        # WHAT THIS REACH DOES NOT CATCH, STATED RATHER THAN IMPLIED, and
        # pinned by `test_the_min_distance_denial_reach_is_the_sentence`:
        # a denial written as the NEXT FULL SENTENCE ("... is the harness
        # figure. It has NO meaning here.") is out of scope. `sentence_scope`
        # breaks on ". " in both directions, by design. `floorplan_contract`
        # widens to the PARAGRAPH for exactly that case and this deliberately
        # does not, because that function's own measurement says why: a
        # paragraph reach "would over-trigger on a markdown TABLE, where an
        # unrelated row ('| Status | not final |') sits in the same block" —
        # and the block this searches IS a pad table. A same-clause denial and
        # a semicolon-joined one are both in reach (";" joins clauses into one
        # sentence, which `_prose_polarity` pins deliberately), and those are
        # the phrasings that share the statement rather than follow it.
        if md is not None:
            lo, hi = _sentence_scope(body, md.start(), md.end(),
                                     extra_breaks=("\n",))
            if _is_denied(body[lo:hi]) is not None:
                md = None
        return PadPlacement(
            source=source, heading=title, side_signals=side_signals,
            side_groups=side_groups,
            min_pad_distance_um=float(md.group("v")) if md else None,
            delegates_io_library_to_pdk=delegates)
    return None


#: A heading that introduces the design's PARAMETER defaults. Scoped on
#: purpose: a document is full of two-column tables whose first cell is a
#: backticked name and whose second is an integer — clock periods, fan-out
#: limits, utilisation targets. Harvesting all of them was MEASURED to make one
#: library name carry two "defaults" and refuse the whole document. Only a
#: section that says it is about parameters declares parameters.
_PARAM_HEADING_RE = re.compile(r"parameter", re.I)


def parse_parameter_defaults(text: str) -> Dict[str, int]:
    """`{parameter: default}` for every integer default a PARAMETER section
    states. A document with no parameter section declares none."""
    out: Dict[str, int] = {}
    body = "\n".join(b for title, b in _sections(text)
                      if _PARAM_HEADING_RE.search(title))
    for line in body.splitlines():
        cells = _cells(line)
        if not cells or len(cells) < _PARAM_ROW_MIN_CELLS:
            continue
        # SAME RULE, SAME REASON as the pad rows above. A row reading
        # "| `N` | 8 | illustrative, not the default |" resolves a bus width
        # from a number its own row withdraws, and `resolve_bits` then expands
        # a port into bits the design never declared.
        if _is_denied(line) is not None:
            continue
        names = _TICKED_RE.findall(cells[0])
        if len(names) != 1:
            continue
        name = names[0].strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9$]*", name):
            continue
        m = _INT_RE.match(_TICKED_RE.sub(r"\1", cells[1]))
        if m is None:
            continue
        value = int(m.group("n"))
        if name in out and out[name] != value:
            # Two tables stating two defaults: the bit count would be decided
            # by file order. Refused rather than resolved.
            raise PadPlacementError(
                "L_DOC_PARAMETER_DEFAULT_AMBIGUOUS",
                f"parameter {name!r} is given two different defaults "
                f"({out[name]} and {value}) in the design's own documents")
        out[name] = value
    return out


def resolve_bits(expr: str, params: Dict[str, int]) -> Optional[List[int]]:
    """`<msb>:<lsb>` -> the bit indices, MSB first, or None for NOT RESOLVED.

    Every endpoint is an integer, a DECLARED parameter, or a declared
    parameter minus an integer. An endpoint this module cannot resolve from a
    stated value yields None and the caller leaves the question unanswered.
    """
    parts = expr.split(":")
    ends: List[int] = []
    for part in parts:
        m = _INT_RE.match(part)
        if m is not None:
            ends.append(int(m.group("n")))
            continue
        m = _PARAM_MINUS_RE.match(part)
        if m is None or m.group("p") not in params:
            return None
        ends.append(params[m.group("p")] - int(m.group("k") or 0))
    if len(ends) == 1:
        return [ends[0]]
    if len(ends) != 2:
        return None
    msb, lsb = ends
    if msb < 0 or lsb < 0:
        return None
    step = -1 if msb >= lsb else 1
    return list(range(msb, lsb + step, step))


def expand_side_ports(placement: PadPlacement, params: Dict[str, int]
                      ) -> Tuple[Dict[str, List[str]], List[str]]:
    """(per-side ordered PORT names, unresolved signal tokens).

    One entry per bit, which is the design's own rule and not this module's.
    A token whose bit range does not resolve from a DECLARED parameter is
    returned unresolved, and the caller must then treat the whole partition as
    unanswered rather than partly invented.
    """
    ports: Dict[str, List[str]] = {}
    unresolved: List[str] = []
    for side, signals in placement.side_signals.items():
        names: List[str] = []
        for token in signals:
            m = _SIGNAL_RE.match(token)
            if m is None:
                unresolved.append(token)
                continue
            base, expr = m.group("base"), m.group("expr")
            if expr is None:
                names.append(base)
                continue
            bits = resolve_bits(expr, params)
            if bits is None:
                unresolved.append(token)
                continue
            names.extend(f"{base}[{b}]" for b in bits)
        ports[side] = names
    return ports, unresolved


# --------------------------------------------------------------------------- #
# the design-owned GROUP row, and the ring that follows from it
# --------------------------------------------------------------------------- #
# WHY THESE FOUR LIVE HERE AND NOT IN THE PRODUCER (R-0915-101).
#
# `io_pad_chip_top_gen` grew them because it was the first consumer that owned
# both halves the resolution needs -- the design's pad-placement section and
# the design's declared port list. It is no longer the only one:
# `slot_pad_budget_check` must budget a DIE against the ring the design
# declares for itself, and that is the SAME ring, read out of the same two
# files. Two readers of one document is one reader too many -- they would
# drift, and the pad they disagreed about would be the pad the budget did not
# refuse. The producer re-exports each of them under its old private name, so
# every existing caller and test is unchanged.
#
# Words which describe the physical carrier rather than a signal family.
# They are ignored symmetrically on both the document and port-name sides;
# no design-specific signal, chip or PDK name belongs here.
GROUP_CARRIER_WORDS = frozenset({
    "bus", "buses", "pin", "pins", "port", "ports", "signal", "signals",
    "control", "controls", "input", "output", "inout", "io", "i", "o",
    "s",
})
GROUP_ATOM_ALIASES = {
    "address": "addr", "clock": "clk", "reset": "rst",
}


def group_atoms(text: str, *, port_name: bool = False) -> set:
    """Return conservative identifier atoms for a group statement.

    A port matches a prose group only when *all* of the port's semantic atoms
    occur in the statement.  This deliberately does not use fuzzy matching:
    a group which cannot be resolved from the design's own identifiers is a
    refusal, not permission to invent a side.
    """
    atoms = [a.lower() for a in re.findall(r"[A-Za-z][A-Za-z0-9]*", text)]
    if port_name and atoms and atoms[0] in {"i", "o", "io"}:
        atoms = atoms[1:]
    return {
        GROUP_ATOM_ALIASES.get(a, a) for a in atoms
        if a not in GROUP_CARRIER_WORDS
    }


def bit_names(port: Dict[str, Any]) -> List[str]:
    """Every net this port contributes, one per bit, MSB first.

    Matches `expand_side_ports`, which is what the partition is expressed in
    -- a scalar stays bare, a bus becomes one `name[bit]` per bit.
    """
    name = str(port.get("name") or "")
    try:
        width = int(port.get("width") or 1)
    except (TypeError, ValueError):
        width = 1
    if width <= 1:
        return [name]
    try:
        msb = int(port.get("msb"))
        lsb = int(port.get("lsb"))
    except (TypeError, ValueError):
        msb, lsb = width - 1, 0
    step = -1 if msb >= lsb else 1
    return [f"{name}[{b}]" for b in range(msb, lsb + step, step)]


def resolve_declared_pad_groups(
        placement: "PadPlacement",
        ports: "Any",
        ) -> Tuple[Dict[str, List[str]], List[Dict[str, Any]]]:
    """Resolve design-owned group rows against the design-owned port list.

    Exact backticked signal rows remain the primary representation.  This
    handles the other legitimate L-doc shape: a side assigned to a named port
    family such as ``memory data bus``.  The resolver is strict, deterministic
    and auditable: every semantic atom of a selected port name must be present
    in the group statement, and a zero-match group is returned unresolved.
    """
    by_side: Dict[str, List[str]] = {}
    records: List[Dict[str, Any]] = []
    for side, statement in placement.side_groups.items():
        statement_atoms = group_atoms(statement)
        matched: List[Dict[str, Any]] = []
        for port in ports:
            name = str(port.get("name") or "")
            port_atoms = group_atoms(name, port_name=True)
            if port_atoms and port_atoms <= statement_atoms:
                matched.append(port)
        nets = [net for port in matched for net in bit_names(port)]
        records.append({
            "side": side,
            "statement": statement,
            "statement_atoms": sorted(statement_atoms),
            "matched_ports": [str(p.get("name") or "") for p in matched],
            "resolved_nets": list(nets),
        })
        if nets:
            by_side[side] = nets
    return by_side, records


# --------------------------------------------------------------------------- #
# the project's documents
# --------------------------------------------------------------------------- #
def discover_docs(project: Path) -> List[Path]:
    """Every design-input document that may carry a pad placement."""
    seen: List[Path] = []
    for rel in DOC_DIRS:
        root = project / rel
        if not root.is_dir():
            continue
        for pattern in DOC_GLOBS:
            for p in sorted(root.glob(pattern)):
                if p.is_file() and p not in seen:
                    seen.append(p)
    return seen


def read_project_placement(project: Path
                           ) -> Tuple[Optional[PadPlacement],
                                      Dict[str, int],
                                      List[Dict[str, str]],
                                      List[str]]:
    """(placement, parameter defaults, unreadable documents, files scanned).

    The FIRST document that states a pad placement wins, and every scanned
    document contributes its parameter defaults, because a bus width is
    declared in one document and used in another. A document that could not be
    READ is returned for the caller to refuse on: "I could not read it" and
    "I read it and it said nothing" must never produce the same verdict.
    """
    placement: Optional[PadPlacement] = None
    params: Dict[str, int] = {}
    unreadable: List[Dict[str, str]] = []
    scanned: List[str] = []
    for path in discover_docs(project):
        try:
            text = path.read_text(errors="replace")
        except OSError as exc:
            unreadable.append({"file": _rel(path, project), "reason": str(exc)})
            continue
        rel = _rel(path, project)
        scanned.append(rel)
        try:
            for name, value in parse_parameter_defaults(text).items():
                if name in params and params[name] != value:
                    raise PadPlacementError(
                        "L_DOC_PARAMETER_DEFAULT_AMBIGUOUS",
                        f"parameter {name!r} is given two different defaults "
                        f"({params[name]} and {value}) across the design's "
                        f"own documents")
                params[name] = value
            if placement is None:
                placement = parse_pad_placement(text, rel)
        except PadPlacementError as exc:
            unreadable.append({"file": rel, "reason": exc.message})
    return placement, params, unreadable, scanned


def _rel(path: Path, project: Path) -> str:
    try:
        return str(path.relative_to(project))
    except ValueError:
        return str(path)


# --------------------------------------------------------------------------- #
# the ring the design declares for itself, derived once (R-0915-101)
# --------------------------------------------------------------------------- #
def derive_own_ring(project: Path, ports: Any) -> Dict[str, Any]:
    """The pad ring THIS DESIGN DECLARES FOR ITSELF, read from its own docs.

    A die binds no operator template, so nothing external states its pad
    budget; what plays the operator slot's part is the pad-placement section
    the design wrote in its own L-documents. That section is the source
    `io_pad_chip_top_gen` already derives `pad_order_by_side` and `SIGNAL_MAP`
    from at step 15.5ic, and this is the same derivation read by the same
    functions -- so a phase-2 budget and a phase-3 ring cannot disagree about
    which pad carries which net.

    `ports` is the design's own declared port list, in `bit_names` shape
    (``name`` plus ``width``, optionally ``msb``/``lsb``). It is used ONLY to
    resolve the document's design-owned GROUP rows against identifiers the
    design itself chose; no port is added to a side the document does not
    name.

    Returns the same four keys `slot_pad_budget_check.declared_own_ring`
    returns -- ``measurable``, ``signal_map``, ``instances``, ``keys_present``
    -- so one inventory counts both rings, plus the provenance a reader needs
    to see WHERE each pad came from. ``measurable`` is False when the design
    states no pad placement at all, or when a token in it does not resolve
    from a declared parameter: a partition this module could only partly read
    is reported unanswered, never partly invented.
    """
    out: Dict[str, Any] = {
        "measurable": False, "signal_map": {}, "instances": [],
        "keys_present": [], "source": None, "heading": None,
        "documents_scanned": [], "documents_unreadable": [],
        "by_side": {}, "unresolved_tokens": [], "groups": [],
        "groups_unresolved": [], "parameter_defaults": {},
    }
    placement, params, unreadable, scanned = read_project_placement(project)
    out["documents_scanned"] = list(scanned)
    out["documents_unreadable"] = list(unreadable)
    out["parameter_defaults"] = dict(params)
    if placement is None:
        return out
    out["source"] = placement.source
    out["heading"] = placement.heading
    exact, unresolved = expand_side_ports(placement, params)
    out["unresolved_tokens"] = list(unresolved)
    grouped, records = resolve_declared_pad_groups(placement, ports or [])
    out["groups"] = records
    out["groups_unresolved"] = [r["side"] for r in records
                                if not r["resolved_nets"]]
    # A TOKEN THIS MODULE COULD NOT RESOLVE STOPS THE RING, not just its own
    # side: a budget measured against a partition with a hole in it would
    # report the missing pads as the design's fault instead of the reader's.
    if unresolved:
        return out
    by_side: Dict[str, List[str]] = {}
    for side in sorted(set(exact) | set(grouped)):
        nets: List[str] = []
        for net in list(exact.get(side) or []) + list(grouped.get(side) or []):
            if net not in nets:
                nets.append(net)
        by_side[side] = nets
    out["by_side"] = by_side
    # ONE PAD PER NET, which is the design's own rule stated in the same
    # table, not this module's. The net IS the pad's identity here: an
    # INSTANCE name is the producer's to choose at 15.5ic, and inventing one
    # would put a name in a phase-2 report that no netlist carries.
    nets_ordered: List[str] = []
    for side in sorted(by_side):
        for net in by_side[side]:
            if net not in nets_ordered:
                nets_ordered.append(net)
    out["instances"] = nets_ordered
    out["signal_map"] = {net: net for net in nets_ordered}
    out["keys_present"] = ([f"{placement.source}:{placement.heading}"]
                           if nets_ordered else [])
    out["measurable"] = bool(nets_ordered)
    return out
