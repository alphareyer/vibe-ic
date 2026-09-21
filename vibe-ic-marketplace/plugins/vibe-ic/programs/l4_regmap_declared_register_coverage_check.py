#!/usr/bin/env python3
"""l4_regmap_declared_register_coverage_check.py — L4's DENOMINATOR (#507).

BLOCKS (exit 1). Rationale for blocking rather than advising
=============================================================
The defect this gate exists for is not a wrong number in L4; it is the
ABSENCE of a number. Measured on a real Phase-1 output whose staged HDL
package declares 145 ``CSR_<NAME> = 12'h<addr>`` register address
bindings, L4 carried 61 of them and
``l4_regmap_enumerated_values_typed_check`` reported::

    [PASS] 2 multi-bit enum-eligible fields all carry typed
           code->meaning enumerated_values

That gate was not wrong. It audits the fields that are PRESENT, and it
has no view of whether the register set is complete against the input,
so 84 declared addresses were absent while the layer reported clean. A
numerator with no denominator reads as coverage and is not.

Advising would not help: every consumer downstream of L4 — the register
block emitter, the address decoder, the CSR access tests — works from
the registers L4 carries. A register the input declares and L4 drops is
a decode that is never generated, and the flow cannot tell the
difference between "the design has no such register" and "we lost it".
So: FAIL blocks.

The contract this gate enforces
===============================
    A layer that carries a SUBSET of what its input declares must be
    able to state the size of both sides.

  D1  Both sides are re-derived HERE, independently:

        declared — every address-valued ``typedef enum`` in the staged
                   HDL inputs, harvested from ``input/docs`` by
                   ``_hdl_enum``. Never read from a number Phase 1
                   wrote: a denominator its own producer computes can
                   only ever confirm itself, which is the shape that let
                   a document be dropped without the coverage metric
                   falling.

        carried  — the register records L4 actually holds, matched by
                   the declared NAME and by the declared ADDRESS. Either
                   match counts: a prose walker that captured the
                   address under its own lowercase name is carrying the
                   binding, and demanding the declared spelling as well
                   would be a naming rule, not a coverage rule.

  D2  The verdict always states its denominator, in the shape
      ``_gate_denominator.Denominator`` enforces — unit, examined,
      considered, and a written reason whenever nothing was examined.

Chip-AGNOSTIC
=============
Nothing here reads a type name, a vendor, a part or a register
spelling. What makes an enum an address map is decided by
``_hdl_enum.route_enum`` from the member set's SHAPE — a set of names
each bound to a distinct code in a space far wider than the set itself.
``csr_num_e`` is one design's name for a shape every CPU-class design
has under a different name.

Usage:
    python3 l4_regmap_declared_register_coverage_check.py <project_dir> \
        [--json <out.json>]

NOT_MEASURED is not NOT_APPLICABLE (czregmap)
=============================================
D1's declared side is HDL-only. On a DOCS-ONLY project — no ``.sv``/``.v``
staged at all, the register map declared by a table in a ``.md`` datasheet —
``declared`` is empty and this gate used to answer::

    [SKIP] no staged HDL input declares an address-valued typedef enum, so
           the input states no register-map denominator for L4 to be
           measured against

which reads as *there was nothing to check*. Measured on a real docs-only
project it was said over an input that declares 7 register rows naming 29
registers, of which L4 carried ONE. The sentence was not a small
over-statement; it asserted the absence of the very population that had gone
missing, and it is the reason the loss was silent.

Those are two different statements and this file now keeps them apart:

  NOT_APPLICABLE  the input declares no register map in ANY shape this gate
                  can look for. There is nothing to check.
  NOT_MEASURED    the input DOES declare a register map, in a shape this
                  gate's declared-side harvester cannot read. The rule was
                  not applied. The verdict names the rows, names the
                  registers, and names which of them are absent from L4 —
                  an observation, not a verdict, because the rule that would
                  turn it into one never ran.

``documentary_declarations`` is the second, deliberately BROAD harvester that
makes the distinction sayable. It is structural and language-agnostic: a row
is a register declaration when its first cell parses as a hex address (or an
address RANGE) and its second as an identifier (or an identifier RANGE), in a
block of consecutive pipe/grid table lines. It reads no header word, so a
table headed in any human language is seen; it reads no register spelling.
Being broader than Phase 1's own row regexes is the point — a denominator
that shares its producer's blind spot can only ever confirm it.

Exit codes:
    0 = PASS / PASS_WITH_WAIVER
    1 = FAIL (blocks) — the input declares register bindings L4 does not
        carry
    2 = NOT_MEASURED — no staged HDL input declares an address-valued enum,
        but documentation staged under input/ DOES declare register rows.
        The verdict names them and names what L4 is missing.
    2 = NOT_APPLICABLE — neither shape declares anything, or L4 is absent.
        The denominator says so explicitly.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _hdl_enum as _hdlenum  # noqa: E402
import _path_layout as _pl  # noqa: E402
from _gate_denominator import Denominator, attach  # noqa: E402

GATE = "l4_regmap_declared_register_coverage_check"
WAIVER_KEY = "l4_regmap_declared_register_coverage_intentional"
WAIVER_MIN_LEN = 40

# Same key list the emitter contract gate reads, for the same reason:
# these are the keys a consumer looks under for an address.
_ADDR_KEYS = ("address_int", "address", "offset", "addr", "addr_hex",
              "offset_hex", "base_address")

_HEX_RE = re.compile(r"^\s*0[xX]([0-9a-fA-F_]+)\s*$")
_SIZED_RE = re.compile(r"^\s*\d+\s*'\s*[hH]([0-9a-fA-F_]+)\s*$")
_BIN_RE = re.compile(r"^\s*0[bB]([01_]+)\s*$")
_DEC_RE = re.compile(r"^\s*(\d+)\s*$")


def _as_int(value: Any) -> Optional[int]:
    """An address a consumer could decode, or None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None
    for rx, base in ((_HEX_RE, 16), (_SIZED_RE, 16), (_BIN_RE, 2),
                     (_DEC_RE, 10)):
        m = rx.match(value)
        if m:
            try:
                return int(m.group(1).replace("_", ""), base)
            except ValueError:
                return None
    return None


def find_staged_hdl(project: Path) -> Dict[str, str]:
    """``{filename: text}`` for every HDL document staged under input/.

    Reads the ORIGINAL staged sources rather than Phase 1's converted
    ``input_doc/*.txt``: the conversion drops the ``.sv`` suffix, and the
    harvester keys on the suffix so a datasheet quoting a ``typedef
    enum`` in prose cannot be mistaken for a declaration.
    """
    out: Dict[str, str] = {}
    for base in (project / "input" / "docs", project / "input_doc",
                 project / "input"):
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file():
                continue
            if p.suffix.lower() not in _hdlenum.HDL_SUFFIXES:
                continue
            if p.name in out:
                continue
            try:
                out[p.name] = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
    return out


def declared_bindings(project: Path) -> List[Dict[str, Any]]:
    """Every register address binding the staged HDL inputs DECLARE."""
    out: List[Dict[str, Any]] = []
    for enum in _hdlenum.address_map_enums(find_staged_hdl(project)):
        type_name = str(enum.get("type_name") or "")
        src = str(enum.get("source_file") or "")
        for binding in enum.get("bindings") or []:
            out.append({
                "name": str(binding.get("name") or ""),
                "value": binding.get("value"),
                "type_name": type_name,
                "source_file": src,
            })
    return out


# ---------------------------------------------------------------------------
# czregmap — the DOCUMENTARY declared side.
#
# Everything below exists to answer one question the HDL harvester cannot:
# does the staged INPUT declare a register map at all? It is deliberately
# broader than any row regex Phase 1 uses, because a denominator that shares
# its producer's blind spot cannot detect the producer's blind spot.
#
# COLUMN ROLES ARE MEASURED PER TABLE, NOT ASSUMED. The first version of this
# harvester fixed the address at cell 0 and the name at cell 1. Swept over the
# tracked corpus it was wrong in BOTH directions on the same document: it
# missed all 35 registers of a Name-first summary table
# (`| Name | Offset | Length | Description |`) and it accepted 21 rows of the
# enum-value tables (`| Value | Name | Description |`) further down the same
# file as registers. A harvester that reports a population wrongly is worse
# than one that reports none, because the number it prints is quotable. So the
# address column and the name column are DERIVED from the body rows of each
# table, and a table whose address column collides with itself is refused —
# two registers cannot share one address, which is the rule
# `l4_regmap_phase2_emitter_contract_check` blocks on downstream.
#
# THE ONE VOCABULARY, AND WHY IT IS A REFUSAL LIST AND NOT AN ALLOW LIST. A
# 3-column `| 0x1 | PER_1 | meaning |` enum-encoding table and a 3-column
# `| 0x1 | CTRL | meaning |` register table have the SAME shape; the only
# thing that separates them is the word over the first column. Reading that
# word as an ALLOW list would make the harvester English-only, and the table
# that motivated this whole lane is headed in Chinese — it would go back to
# reporting nothing on exactly the document that was silently losing rows. So
# the default is "this is a register declaration", and the vocabulary only
# REFUSES a column a document has explicitly labelled as an encoding or a
# reset. A document that labels its enum column in a language this list does
# not carry is over-reported, which is the honest direction for a disclosure:
# it names its rows, and a reader can see what it read.
#
# Chip-AGNOSTIC: no register spelling, no vendor, no part, no process. The
# refusal vocabulary is document grammar, of the same kind as the access-token
# vocabulary Phase 1's own row parsers already carry.
# ---------------------------------------------------------------------------

#: Text documents that can carry a register table. HDL suffixes are handled
#: by the HDL harvester and are excluded here so the two populations stay
#: disjoint and a project cannot be counted twice.
_DOC_SUFFIXES = (".md", ".markdown", ".rst", ".txt", ".adoc", ".asciidoc")

#: Range separators seen in register documentation: `0x10-0x1F`, `0x10..0x1F`,
#: `BLOCK0 ~ BLOCK15`, `REG0 to REG7`, and the two Unicode dashes a word
#: processor substitutes for a hyphen.
_RANGE_SEP = r"(?:\.\.\.?|~|to|—|–|-)"

_HEXTOK = r"0[xX][0-9A-Fa-f][0-9A-Fa-f_]*"
_ADDR_CELL_RE = re.compile(
    rf"^(?P<lo>{_HEXTOK})(?:\s*{_RANGE_SEP}\s*(?P<hi>{_HEXTOK}))?$")

_NAME_TOK = r"[A-Za-z_][A-Za-z0-9_.\[\]]*"
_NAME_CELL_RE = re.compile(
    rf"^(?P<lo>{_NAME_TOK})(?:\s*{_RANGE_SEP}\s*(?P<hi>{_NAME_TOK}))?$")

#: Trailing-integer split, for expanding `BLOCK0 ~ BLOCK15` into its members.
_NAME_TAIL_INT_RE = re.compile(r"^(?P<stem>.*?)(?P<idx>\d+)$")

#: A name range is expanded only up to this many members. A documentation
#: range wider than this is far more likely to be a typo than a register
#: file, and an unbounded expansion is a denial of service on the gate.
_NAME_RANGE_EXPANSION_CAP = 4096

#: How many register names the verdict NAMES. The count beside them is never
#: capped, so a truncated list is always readable as a truncation.
_NAME_LIST_CAP = 256

#: How many declaring DOCUMENTS the verdict names, for the same reason. This
#: one bounds the length of the verdict SENTENCE, which a project staging many
#: register-map documents would otherwise make unreadable.
_SOURCE_LIST_CAP = 8

#: Column headings that state a column is NOT an address. See the block
#: comment above for why this is a refusal list rather than an allow list.
_NOT_AN_ADDRESS_HEADING = re.compile(
    r"^(?:value|values|code|codes|encoding|encodings|enum|enums|"
    r"reset|reset\s+value|default|default\s+value)$", re.IGNORECASE)

_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def _clean_cell(cell: str) -> str:
    """A table cell reduced to its text: markdown decoration removed.

    A `[LABEL](#anchor)` cell keeps the LABEL and DROPS whatever preceded it:
    a register-tool summary writes `aes.[`CTRL`](#ctrl)`, and the name the
    layer downstream carries is `CTRL`, not the qualified path.
    """
    t = cell.strip()
    link = _MD_LINK_RE.search(t)
    if link:
        t = link.group(1)
    t = t.replace("`", "").replace("*", "")
    return t.strip()


def _parse_addr_cell(cell: str) -> Optional[Tuple[int, Optional[int]]]:
    """``(lo, hi_or_None)`` when the cell is a hex address or address range."""
    m = _ADDR_CELL_RE.match(_clean_cell(cell))
    if not m:
        return None
    try:
        lo = int(m.group("lo")[2:].replace("_", ""), 16)
    except ValueError:
        return None
    hi: Optional[int] = None
    if m.group("hi"):
        try:
            hi = int(m.group("hi")[2:].replace("_", ""), 16)
        except ValueError:
            return None
        if hi < lo:
            return None
    return lo, hi


def _expand_name_range(lo: str, hi: str) -> List[str]:
    """``BLOCK0``/``BLOCK15`` -> the 16 names between them, or ``[lo, hi]``.

    Expansion happens only when both endpoints share a stem and differ by a
    trailing integer. Anything else is reported as the two literal endpoints
    — under-reporting a range is honest; inventing names is not.
    """
    ml, mh = _NAME_TAIL_INT_RE.match(lo), _NAME_TAIL_INT_RE.match(hi)
    if not ml or not mh or ml.group("stem") != mh.group("stem"):
        return [lo, hi] if lo != hi else [lo]
    a, b = int(ml.group("idx")), int(mh.group("idx"))
    if b < a or (b - a + 1) > _NAME_RANGE_EXPANSION_CAP:
        return [lo, hi] if lo != hi else [lo]
    return [f"{ml.group('stem')}{i}" for i in range(a, b + 1)]


def _parse_name_cell(cell: str) -> Optional[List[str]]:
    """The register name(s) a cell declares, or None if it is not a name."""
    m = _NAME_CELL_RE.match(_clean_cell(cell))
    if not m:
        return None
    lo, hi = m.group("lo"), m.group("hi")
    if not hi:
        return [lo]
    return _expand_name_range(lo, hi)


def _is_ruling(line: str) -> bool:
    """`|---|---|`, `+===+===+`, `|:--:|` — a table's drawing, not a row."""
    return set(line.strip()) <= set("|+-=: \t")


def _table_blocks(text: str) -> List[List[str]]:
    """Maximal runs of consecutive lines that contain a `|` cell separator.

    A register map is a TABLE. Requiring the row to sit inside a run of at
    least two such lines keeps a single `| 0x10 | note |` sentence in running
    prose from being read as a register declaration.
    """
    blocks: List[List[str]] = []
    cur: List[str] = []
    for line in text.splitlines():
        if "|" in line:
            cur.append(line)
            continue
        if len(cur) >= 2:
            blocks.append(cur)
        cur = []
    if len(cur) >= 2:
        blocks.append(cur)
    return blocks


def _cells(line: str) -> List[str]:
    return [c for c in line.strip().strip("|").split("|")]


def _column_roles(rows: List[List[str]]) -> Optional[Tuple[int, int]]:
    """``(address_column, name_column)`` for a table's body, or None.

    Both are DERIVED, never assumed: a column is the address column when
    every non-empty cell under it parses as an address and those addresses
    are DISTINCT, and the name column is the first other column whose every
    non-empty cell parses as an identifier.
    """
    width = max(len(r) for r in rows)

    def _address_column(strict: bool) -> Optional[int]:
        for c in range(width):
            seen: List[int] = []
            cells = 0
            ok = True
            for r in rows:
                cell = r[c] if c < len(r) else ""
                if not _clean_cell(cell):
                    continue
                cells += 1
                parsed = _parse_addr_cell(cell)
                if parsed is None:
                    if strict:
                        ok = False
                        break
                    continue
                seen.append(parsed[0])
            if not ok or not seen:
                continue
            # Distinct, because two registers cannot share one address. This
            # is what refuses a `Reset` column that reads 0x0 on every row.
            if len(set(seen)) != len(seen):
                continue
            if strict or (len(seen) >= _ADDR_COLUMN_MIN_ROWS
                          and len(seen) >= cells * _ADDR_COLUMN_MIN_FRACTION):
                return c
        return None

    # R-0915-113(7) — TWO PASSES, strict first, so no table that resolves
    # today resolves differently. The tolerant pass exists for exactly one
    # shape: a register table in which SOME row states no address. MEASURED
    # on run24's own L5 table with one address cell blanked, the strict rule
    # disqualified the address column and the gate then reported "no
    # documentation this gate opened declares a register row" — one
    # unaddressed row dropped all 29 registers. Under the ruling that row
    # must be NOT_COVERED_UNADDRESSED by name, which it cannot be if the
    # table it sits in is invisible. The tolerant pass keeps DISTINCTNESS
    # and demands a strong majority, so a `Reset value` column still cannot
    # win a table the strict pass already resolved.
    addr_col = _address_column(strict=True)
    if addr_col is None:
        addr_col = _address_column(strict=False)
    if addr_col is None:
        return None
    for c in range(width):
        if c == addr_col:
            continue
        got = False
        ok = True
        for r in rows:
            cell = r[c] if c < len(r) else ""
            if not _clean_cell(cell):
                continue
            if _parse_name_cell(cell) is None:
                ok = False
                break
            got = True
        if ok and got:
            return addr_col, c
    return None


#: How much of a column must parse as an address before the TOLERANT pass
#: will call it one, and the floor below which "a majority" means nothing.
_ADDR_COLUMN_MIN_FRACTION = 0.6
_ADDR_COLUMN_MIN_ROWS = 2

#: A width cell states a bit count and, optionally, ONE qualifying word:
#: `32`, `32 each`, `32 bits`. A description cell never matches, which is what
#: keeps a sentence out of the width column without reading a header word.
_WIDTH_CELL_RE = re.compile(r"^(?P<bits>\d{1,5})\s*(?:[A-Za-z\u4e00-\u9fff]+)?$")
#: How much of a column must parse as a width before the column IS one. A
#: register table may leave a width blank; a description column will not reach
#: this fraction.
_WIDTH_COLUMN_MIN_FRACTION = 0.5


def _parse_width_cell(cell: str) -> Optional[int]:
    """The bit width a cell states, or None."""
    m = _WIDTH_CELL_RE.match(_clean_cell(cell))
    if m is None:
        return None
    try:
        bits = int(m.group("bits"))
    except ValueError:                                      # pragma: no cover
        return None
    return bits if bits > 0 else None


def _width_column(rows: List[List[str]], addr_col: int,
                  name_col: int) -> Optional[int]:
    """The column that states each register's WIDTH, measured per table.

    Measured, never assumed, exactly the way `_column_roles` measures the
    address and name columns: the first column that is neither of those and
    whose body cells state a width in at least
    `_WIDTH_COLUMN_MIN_FRACTION` of the rows. No header word is read, so a
    table headed in any human language is seen."""
    if not rows:
        return None
    width = max(len(r) for r in rows)
    for col in range(width):
        if col in (addr_col, name_col):
            continue
        seen = [r[col] for r in rows if col < len(r)]
        if not seen:
            continue
        hits = sum(1 for c in seen if _parse_width_cell(c) is not None)
        if hits >= max(1, int(len(seen) * _WIDTH_COLUMN_MIN_FRACTION)):
            return col
    return None


def _block_declarations(block: List[str], rel: str) -> List[Dict[str, Any]]:
    """The register rows one table block declares."""
    parsed = [(_cells(line), line) for line in block if not _is_ruling(line)]
    parsed = [(cells, line) for cells, line in parsed if len(cells) >= 2]
    if len(parsed) < 2:
        return []
    # A header is a leading row in which nothing parses as an address.
    header: Optional[List[str]] = None
    body = parsed
    first_cells = parsed[0][0]
    if all(_parse_addr_cell(c) is None for c in first_cells):
        header = first_cells
        body = parsed[1:]
    if not body:
        return []
    roles = _column_roles([c for c, _ in body])
    if roles is None:
        return []
    addr_col, name_col = roles
    if header is not None and addr_col < len(header):
        if _NOT_AN_ADDRESS_HEADING.match(_clean_cell(header[addr_col])):
            return []
    width_col = _width_column([c for c, _ in body], addr_col, name_col)
    out: List[Dict[str, Any]] = []
    for cells, line in body:
        if addr_col >= len(cells) or name_col >= len(cells):
            continue
        addr = _parse_addr_cell(cells[addr_col])
        names = _parse_name_cell(cells[name_col])
        if not names:
            continue
        # R-0915-113(7): a row INSIDE a register table whose name cell parses
        # and whose address cell does not is a register the document declares
        # WITHOUT stating its address. It used to be dropped here, which made
        # it invisible to every count downstream. It is kept, with
        # `addr_lo=None`, and the coverage rule answers
        # NOT_COVERED_UNADDRESSED for it BY NAME. Rows outside a block whose
        # column roles already resolved are untouched: the shape rule that
        # keeps this harvester off arbitrary tables is unchanged.
        out.append({
            "names": names,
            "addr_lo": None if addr is None else addr[0],
            "addr_hi": None if addr is None else addr[1],
            "width_bits": (_parse_width_cell(cells[width_col])
                           if width_col is not None
                           and width_col < len(cells) else None),
            "source_file": rel,
            "line": line.strip()[:160],
        })
    return out


#: What the reason line says this harvester LOOKED FOR. Written once, quoted
#: by the verdict, so the claim and the code cannot drift.
_SHAPES_SEARCHED = ("pipe / grid table rows carrying a hex address column and "
                    "an identifier column, in a document of type "
                    + "/".join(x.lstrip(".") for x in _DOC_SUFFIXES))

#: How many unopened documents the census NAMES. Its count is never capped.
_UNOPENED_LIST_CAP = 8


def documentary_census(project: Path) -> Dict[str, Any]:
    """What this harvester OPENED under ``input/docs/``, and what it did not.

    The reason for this, in one sentence: without it the vacuous branch says
    "no documentation staged under input/ declares a register row either",
    which is a claim about the INPUT dressed over a fact about this file's
    suffix list. That substitution is the entire defect this gate was just
    repaired for, one level down and inside the repair. "Could not read it"
    is not "read it and it was empty", so the zero names what it could not
    open.

    HDL suffixes are not counted as unopened: the HDL harvester reads them,
    and the same sentence already accounts for what it found.
    """
    docs = project / "input" / "docs"
    census: Dict[str, Any] = {
        "docs_dir": str(Path("input") / "docs"),
        "docs_dir_present": docs.is_dir(),
        "opened_count": 0,
        "not_opened": [],
        "not_opened_count": 0,
    }
    if not docs.is_dir():
        return census
    unopened: List[str] = []
    for path in sorted(docs.rglob("*")):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix in _DOC_SUFFIXES:
            census["opened_count"] += 1
            continue
        if suffix in _hdlenum.HDL_SUFFIXES:
            continue
        try:
            unopened.append(str(path.relative_to(project)))
        except ValueError:                                  # pragma: no cover
            unopened.append(path.name)
    census["not_opened"] = unopened[:_UNOPENED_LIST_CAP]
    census["not_opened_count"] = len(unopened)
    return census


def documentary_declarations(project: Path) -> List[Dict[str, Any]]:
    """Register rows DECLARED by documentation staged under ``input/``.

    One entry per declaring ROW::

        {"names": [...], "addr_lo": int, "addr_hi": int|None,
         "source_file": str, "line": str}
    """
    out: List[Dict[str, Any]] = []
    seen_docs: set = set()
    for base in (project / "input" / "docs", project / "input"):
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in _DOC_SUFFIXES:
                continue
            if path.suffix.lower() in _hdlenum.HDL_SUFFIXES:
                continue
            try:
                rel = str(path.relative_to(project))
            except ValueError:                          # pragma: no cover
                rel = path.name
            if rel in seen_docs:
                continue
            seen_docs.add(rel)
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for block in _table_blocks(text):
                out.extend(_block_declarations(block, rel))
    # A register-tool document declares the same register TWICE: once in the
    # summary table and once in the per-register instance table above its
    # field diagram. MEASURED on the tracked corpus: 63 rows for 35
    # registers. Both rows are the same declaration, so they are one — the
    # unit is a declaration, not a line of markdown.
    deduped: List[Dict[str, Any]] = []
    seen_decls: set = set()
    for d in out:
        key = (tuple(d["names"]), d["addr_lo"], d["addr_hi"])
        if key in seen_decls:
            continue
        seen_decls.add(key)
        deduped.append(d)
    return deduped


def find_l4(project: Path) -> Optional[Path]:
    for cand in (_pl.generated_docs_dir(project) / "L4_REGMAP.json",
                 project / "generated_docs" / "L4_REGMAP.json",
                 project / "L4_REGMAP.json"):
        if cand.is_file():
            return cand
    return None


def carried_registers(l4: Dict[str, Any]) -> Tuple[set, set]:
    """``(names, addresses)`` L4's registers[] actually carries."""
    names: set = set()
    addrs: set = set()
    regs = l4.get("registers")
    if not isinstance(regs, list):
        return names, addrs
    for reg in regs:
        if not isinstance(reg, dict):
            continue
        for key in ("name", "declared_name", "register", "reg_name"):
            v = reg.get(key)
            if isinstance(v, str) and v.strip():
                names.add(v.strip())
        for key in _ADDR_KEYS:
            iv = _as_int(reg.get(key))
            if iv is not None:
                addrs.add(iv)
    return names, addrs


# ── R-0915-113(7): a population harvested is a population MEASURED ────────
#
# THE DEFECT, in this gate's own words, on sha256 x sky130A, FRONT DOOR,
# run24 at main 8337cc81f:
#
#     NOT_MEASURED ... documentation staged under input/ DOES declare a
#     register map: 7 table row(s) in input/docs/L5_register_map.md naming 29
#     register(s). The rule was not applied to them, so this gate states no
#     coverage over that population. L4_REGMAP.registers[] does carry all 29
#     of those name(s) — but by NAME only, which is not the rule this gate
#     applies.
#
# A gate that harvests a population and then states no coverage over it
# measures nothing about it, and a NOT_MEASURED at P0 makes `final_audit`
# NOT_MEASURED under --strict-structural whatever the design does. The repair
# is to APPLY the rule to that population, not to narrow it.
#
# THE RULE IS THE SAME RULE, and it is ADDRESS-BOUND IDENTITY, never name-only
# presence: a documented register is COVERED when L4 carries it under that
# name AND at the address the document states AND, when both sides state one,
# at the same width. Every other outcome is named:
#
#   ABSENT_FROM_L4           the document declares it; L4 carries no such name
#   ADDRESS_MISMATCH         L4 carries the name at a DIFFERENT address —
#                            both numbers are printed, because this is the
#                            case a name-only rule reported as covered
#   WIDTH_MISMATCH           same address, different stated width
#   NOT_COVERED_UNADDRESSED  the DOCUMENT states no address for the row. Never
#                            assumed covered and never dropped: the register
#                            is named and counted in its own bucket, because
#                            the gap is in the input, not in L4.
COVERED = "COVERED"
ABSENT_FROM_L4 = "ABSENT_FROM_L4"
ADDRESS_MISMATCH = "ADDRESS_MISMATCH"
WIDTH_MISMATCH = "WIDTH_MISMATCH"
NOT_COVERED_UNADDRESSED = "NOT_COVERED_UNADDRESSED"
#: The outcomes that BLOCK. `NOT_COVERED_UNADDRESSED` is not among them: the
#: document failed to state an address, which is not a defect in L4.
_BLOCKING_OUTCOMES = (ABSENT_FROM_L4, ADDRESS_MISMATCH, WIDTH_MISMATCH)
#: Where L4 may record a register's width.
_WIDTH_KEYS = ("width_bits", "width", "size_bits", "bits", "reg_width")


def documentary_bindings(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One entry per declared REGISTER, with the address the document binds it
    to. A range row binds its i-th name to ``addr_lo + i`` — the document's own
    arithmetic — and ONLY when the span and the name count agree; a row whose
    span and count disagree states no address for any of its names rather than
    an invented one."""
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for row in rows:
        names = list(row.get("names") or [])
        lo, hi = row.get("addr_lo"), row.get("addr_hi")
        span = None
        if isinstance(lo, int) and isinstance(hi, int) and hi >= lo:
            span = hi - lo + 1
        for i, name in enumerate(names):
            key = name.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            if not isinstance(lo, int):
                addr, how = None, "the document states no address for this row"
            elif hi is None:
                addr, how = (lo, "stated") if len(names) == 1 else (
                    None, f"one address for {len(names)} name(s) — which name "
                          f"it binds is not stated")
            elif span == len(names):
                addr, how = lo + i, "stated as a range"
            else:
                addr, how = None, (
                    f"the row spans {span} address(es) for {len(names)} "
                    f"name(s), so which address each name takes is not stated")
            out.append({
                "name": name.strip(),
                "address": addr,
                "address_basis": how,
                "width_bits": row.get("width_bits"),
                "source_file": row.get("source_file"),
                "line": row.get("line"),
            })
    return out


def carried_index(l4: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """``name.lower() -> {"name", "address", "width_bits"}`` from L4."""
    out: Dict[str, Dict[str, Any]] = {}
    regs = l4.get("registers")
    if not isinstance(regs, list):
        return out
    for reg in regs:
        if not isinstance(reg, dict):
            continue
        name = None
        for key in ("name", "declared_name", "register", "reg_name"):
            v = reg.get(key)
            if isinstance(v, str) and v.strip():
                name = v.strip()
                break
        if not name:
            continue
        addr = None
        for key in _ADDR_KEYS:
            addr = _as_int(reg.get(key))
            if addr is not None:
                break
        width = None
        for key in _WIDTH_KEYS:
            width = _as_int(reg.get(key))
            if width is not None:
                break
        out.setdefault(name.lower(), {"name": name, "address": addr,
                                      "width_bits": width})
    return out


def documentary_coverage(bindings: List[Dict[str, Any]],
                         carried: Dict[str, Dict[str, Any]]
                         ) -> List[Dict[str, Any]]:
    """The rule, applied one register at a time. Every entry gets an outcome
    and a sentence; nothing is dropped and nothing is assumed covered."""
    out: List[Dict[str, Any]] = []
    for b in bindings:
        rec = dict(b)
        got = carried.get(b["name"].lower())
        if b["address"] is None:
            rec["outcome"] = NOT_COVERED_UNADDRESSED
            rec["why"] = (f"{b['name']}: {b['address_basis']}, so this gate "
                          f"cannot say whether L4 carries the same binding")
        elif got is None:
            rec["outcome"] = ABSENT_FROM_L4
            rec["why"] = (f"{b['name']} is declared at 0x{b['address']:x} and "
                          f"L4_REGMAP.registers[] carries no such name")
        elif got["address"] is None:
            rec["outcome"] = ADDRESS_MISMATCH
            rec["why"] = (f"{b['name']} is declared at 0x{b['address']:x}; L4 "
                          f"carries the name with NO address, which is a name "
                          f"without the binding")
        elif got["address"] != b["address"]:
            rec["outcome"] = ADDRESS_MISMATCH
            rec["why"] = (f"{b['name']} is declared at 0x{b['address']:x} and "
                          f"L4 carries it at 0x{got['address']:x}")
        elif (b["width_bits"] is not None and got["width_bits"] is not None
                and b["width_bits"] != got["width_bits"]):
            rec["outcome"] = WIDTH_MISMATCH
            rec["why"] = (f"{b['name']} at 0x{b['address']:x} is declared "
                          f"{b['width_bits']} bit(s) wide and L4 carries it "
                          f"at {got['width_bits']}")
        else:
            rec["outcome"] = COVERED
            rec["why"] = (f"{b['name']} at 0x{b['address']:x}"
                          + (f", {b['width_bits']} bit(s)"
                             if b["width_bits"] is not None else ""))
        rec["l4_address"] = None if got is None else got["address"]
        rec["l4_width_bits"] = None if got is None else got["width_bits"]
        out.append(rec)
    return out


def _waived(project: Path) -> Tuple[bool, str]:
    p = project / "waivers.json"
    if not p.is_file():
        return False, ""
    try:
        d = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return False, ""
    raw = d.get(WAIVER_KEY)
    if isinstance(raw, str) and len(raw.strip()) >= WAIVER_MIN_LEN:
        return True, raw.strip()
    if isinstance(raw, dict):
        r = raw.get("rationale") or raw.get("reason") or ""
        if isinstance(r, str) and len(r.strip()) >= WAIVER_MIN_LEN:
            return True, r.strip()
    return False, ""


def _not_measured(summary: Dict[str, Any], project: Path,
                  doc_rows: List[Dict[str, Any]],
                  census: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """The verdict for "a register map IS declared, in a shape I cannot read".

    ``examined`` stays 0 — the gate's rule was NOT applied, and inflating it
    with a population the rule never ran over is the substitution this whole
    file exists against. ``considered`` carries the documentary rows, which is
    exactly what that field is for: candidates the gate's own precondition
    filtered out. Every name is listed, and the names L4 does not carry are
    listed separately, as an OBSERVATION — the rule that would turn it into a
    verdict is the one that did not run.

    Every list this verdict prints is capped at ``_NAME_LIST_CAP`` and carries
    an UNCAPPED count beside it, so a truncated list always reads as a
    truncation. A verdict whose own length depends on the input is a verdict
    that stops being read on the input that needs it most.
    """
    # Order-preserving, membership tested against a SET rather than against
    # the list being built: the list form is quadratic in the number of
    # declared registers, and this gate is handed whatever the input
    # contains. MEASURED end to end on a synthetic 8.4 MB summary table
    # declaring 126,862 registers: evaluate() 1.2 s, render() under 10 ms.
    declared_names: List[str] = []
    _seen_names: set = set()
    for row in doc_rows:
        for n in row["names"]:
            if n in _seen_names:
                continue
            _seen_names.add(n)
            declared_names.append(n)
    sources = sorted({r["source_file"] for r in doc_rows})
    _sources_shown = ", ".join(sources[:_SOURCE_LIST_CAP])
    if len(sources) > _SOURCE_LIST_CAP:
        _sources_shown += f" (+{len(sources) - _SOURCE_LIST_CAP} more)"

    # ABSENT and UNPARSEABLE are two different facts and this branch keeps
    # them apart, because collapsing them is the defect this whole file was
    # repaired for. The HDL path above already separates "no L4_REGMAP.json
    # was emitted" from "L4 did not parse: <exc>"; the first version of THIS
    # branch answered "could not be read" to both, which reads as a damaged
    # file when the file may simply never have been written.
    l4_path = find_l4(project)
    carried_names: set = set()
    absent_names: List[str] = []
    l4_doc: Dict[str, Any] = {}
    l4_read = False
    l4_state = "absent"
    l4_parse_error = ""
    if l4_path is not None:
        try:
            l4_doc = json.loads(l4_path.read_text(encoding="utf-8",
                                                  errors="replace"))
            carried_names, _addrs = carried_registers(l4_doc)
            l4_read = True
            l4_state = "read"
        except Exception as exc:                            # noqa: BLE001
            l4_read = False
            l4_state = "unparseable"
            l4_parse_error = f"{type(exc).__name__}: {exc}"[:120]
    if l4_read:
        lowered = {n.lower() for n in carried_names}
        absent_names = [n for n in declared_names if n.lower() not in lowered]

    if l4_state == "absent":
        carried_clause = (
            "no L4_REGMAP.json was emitted at all, so how many of them a "
            "layer carries is NOT MEASURED either — which is not the same "
            "as a layer that carries none")
    elif l4_state == "unparseable":
        carried_clause = (
            f"L4_REGMAP.json is PRESENT and did not parse "
            f"({l4_parse_error}), so how many of them it carries is NOT "
            f"MEASURED either — which is not the same as absent")
    elif absent_names:
        carried_clause = (
            f"L4_REGMAP.registers[] carries "
            f"{len(declared_names) - len(absent_names)} of those "
            f"{len(declared_names)} name(s); {len(absent_names)} do not "
            f"appear in it at all")
    else:
        carried_clause = (
            f"L4_REGMAP.registers[] does carry all {len(declared_names)} "
            f"of those name(s) — but by NAME only, which is not the rule "
            f"this gate applies")

    # R-0915-113(7) — APPLY THE RULE. The sentence this branch used to
    # print ("the rule was not applied to them, so this gate states no
    # coverage over that population") is gone: a population harvested is a
    # population measured. What remains NOT_MEASURED here is the HDL
    # population, and the verdict now says which population it measured and
    # which rule it applied to it.
    bindings = documentary_bindings(doc_rows)
    carried_by_name = carried_index(l4_doc) if l4_read else {}
    coverage = documentary_coverage(bindings, carried_by_name) \
        if l4_read else []
    by_outcome: Dict[str, List[Dict[str, Any]]] = {}
    for rec in coverage:
        by_outcome.setdefault(rec["outcome"], []).append(rec)
    n_cov = len(by_outcome.get(COVERED, []))
    blocking = [r for o in _BLOCKING_OUTCOMES for r in by_outcome.get(o, [])]
    unaddressed = by_outcome.get(NOT_COVERED_UNADDRESSED, [])
    measured_clause = (
        f"MEASURED: the documentary population, by ADDRESS-BOUND IDENTITY — "
        f"a register is covered when L4_REGMAP.registers[] carries it under "
        f"the declared name, at the declared address, and (when both state "
        f"one) at the declared width. {n_cov} of {len(bindings)} "
        f"register(s) covered"
        + (f"; {len(blocking)} not: "
           + "; ".join(r["why"] for r in blocking[:_SOURCE_LIST_CAP])
           + (f" (+{len(blocking) - _SOURCE_LIST_CAP} more)"
              if len(blocking) > _SOURCE_LIST_CAP else "")
           if blocking else "")
        + (f"; {len(unaddressed)} state no address and are "
           f"{NOT_COVERED_UNADDRESSED}: "
           + ", ".join(r["name"] for r in unaddressed[:_NAME_LIST_CAP])
           + (f" (+{len(unaddressed) - _NAME_LIST_CAP} more)"
              if len(unaddressed) > _NAME_LIST_CAP else "")
           if unaddressed else ""))
    reason = (
        f"This gate's HDL declared side reads ADDRESS-VALUED HDL "
        f"DECLARATIONS and no staged input declares one, so the HDL "
        f"population is NOT_MEASURED. Documentation staged under input/ "
        f"DOES declare a register map: {len(doc_rows)} table row(s) in "
        f"{_sources_shown} naming {len(declared_names)} register(s), and "
        f"that population WAS measured. {measured_clause}."
        if l4_read else
        f"This gate's HDL declared side reads ADDRESS-VALUED HDL "
        f"DECLARATIONS and no staged input declares one. Documentation "
        f"staged under input/ declares {len(doc_rows)} table row(s) in "
        f"{_sources_shown} naming {len(declared_names)} register(s), and "
        f"{carried_clause} — so neither population could be measured.")

    attach(summary, Denominator(
        unit=("register bindings declared by staged documentation"
              if l4_read else
              "register address bindings declared by a staged HDL input"),
        # R-0915-113(7): the rule RAN over this population, so `examined` is
        # its size. Leaving it at 0 while printing a coverage number would be
        # the substitution this file exists against, in the other direction.
        examined=len(bindings) if l4_read else 0,
        considered=len(doc_rows),
        not_applicable_reason=reason,
        details={
            "documentary_coverage_rule": (
                "address-bound identity: declared name AND declared address "
                "AND, when both state one, declared width"),
            "documentary_registers_measured": len(bindings),
            "documentary_registers_covered": n_cov,
            "documentary_outcome_counts": {k: len(v)
                                           for k, v in sorted(by_outcome.items())},
            "documentary_coverage": [
                {k: r[k] for k in ("name", "address", "width_bits",
                                   "l4_address", "l4_width_bits",
                                   "outcome", "why", "address_basis")}
                for r in coverage[:_NAME_LIST_CAP]],
            "documentary_rows": len(doc_rows),
            "documentary_sources": sources[:_SOURCE_LIST_CAP],
            "documentary_source_count": len(sources),
            "documentary_declared_names": declared_names[:_NAME_LIST_CAP],
            "documentary_declared_name_count": len(declared_names),
            "documentary_names_absent_from_l4": absent_names[:_NAME_LIST_CAP],
            "documentary_names_absent_from_l4_count": len(absent_names),
            "l4_readable": l4_read,
            "l4_state": l4_state,
            "l4_parse_error": l4_parse_error,
            "documentary_census": census,
            "harvester": ("shape-based: hex address (or range) in cell 1, "
                          "identifier (or range) in cell 2, inside a table "
                          "block; no header word and no register spelling "
                          "is read"),
        }))
    summary["documentary_rows"] = len(doc_rows)
    summary["documentary_sources"] = sources[:_SOURCE_LIST_CAP]
    summary["documentary_source_count"] = len(sources)
    # Named, then capped, then COUNTED — the same shape the FAIL path uses
    # for `absent`. A document declaring 126k registers must not turn this
    # report into a file nobody opens, and a truncated list beside a full
    # count is readable where a truncated list alone would be a lie.
    summary["documentary_declared_names"] = declared_names[:_NAME_LIST_CAP]
    summary["documentary_declared_name_count"] = len(declared_names)
    summary["documentary_names_absent_from_l4"] = absent_names[:_NAME_LIST_CAP]
    summary["documentary_names_absent_from_l4_count"] = len(absent_names)
    summary["l4_readable"] = l4_read
    summary["l4_state"] = l4_state
    summary["documentary_census"] = census
    summary["documentary_registers_measured"] = len(bindings)
    summary["documentary_registers_covered"] = n_cov
    summary["documentary_outcome_counts"] = {k: len(v)
                                             for k, v in sorted(by_outcome.items())}
    summary["documentary_coverage"] = [
        {k: r[k] for k in ("name", "address", "width_bits", "l4_address",
                           "l4_width_bits", "outcome", "why")}
        for r in coverage[:_NAME_LIST_CAP]]
    summary["documentary_coverage_rule"] = (
        "address-bound identity: declared name AND declared address AND, "
        "when both state one, declared width")
    # THE VERDICT THE RULE PRODUCES.
    #   FAIL          a declared binding L4 does not carry, or carries at a
    #                 different address or width. This is the case a
    #                 name-only rule reported as covered.
    #   PASS          every declared register covered, none unaddressed.
    #   NOT_MEASURED  the residual is rows the DOCUMENT left unaddressed --
    #                 named, counted, never assumed covered. The coverage it
    #                 DID measure is stated in the same sentence.
    if not l4_read:
        summary["verdict"] = "NOT_MEASURED"
        return 2, summary
    if blocking:
        summary["verdict"] = "FAIL"
        return 1, summary
    if unaddressed:
        summary["verdict"] = "NOT_MEASURED"
        return 2, summary
    summary["verdict"] = "PASS"
    return 0, summary


def evaluate(project: Path) -> Tuple[int, Dict[str, Any]]:
    """``(exit_code, summary)``. Pure: writes nothing, prints nothing."""
    summary: Dict[str, Any] = {"gate": GATE, "project": str(project)}

    declared = declared_bindings(project)
    l4_path = find_l4(project)

    if not declared:
        # czregmap — before answering "nothing to check", look for the
        # register map in the shape this gate's declared side cannot read.
        # Saying NOT_APPLICABLE over a documented register map asserts the
        # absence of exactly the population that goes missing.
        doc_rows = documentary_declarations(project)
        census = documentary_census(project)
        if doc_rows:
            return _not_measured(summary, project, doc_rows, census)
        # The zero NAMES what it looked for and what it could not open. A
        # vacuous branch that says only "nothing declares a register map" is
        # the same substitution this file was just repaired for, one level
        # down: a fact about this harvester's suffix list, stated as a fact
        # about the input.
        if not census["docs_dir_present"]:
            scope = "there is no input/docs/ directory to read"
        else:
            scope = (f"it read {census['opened_count']} document(s) under "
                     f"{census['docs_dir']} looking for {_SHAPES_SEARCHED}")
            if census["not_opened_count"]:
                named = ", ".join(census["not_opened"])
                more = (f" (+{census['not_opened_count'] - len(census['not_opened'])}"
                        f" more)"
                        if census["not_opened_count"] > len(census["not_opened"])
                        else "")
                scope += (f", and it did NOT OPEN "
                          f"{census['not_opened_count']} other file(s) there "
                          f"— {named}{more} — so a register map stated in one "
                          f"of those was NOT LOOKED FOR, which is not the "
                          f"same as looked for and absent")
        attach(summary, Denominator(
            unit="register address bindings declared by a staged HDL input",
            examined=0,
            considered=0,
            details={"documentary_census": census},
            not_applicable_reason=(
                f"no staged HDL input declares an address-valued typedef "
                f"enum, and no documentation this gate opened declares a "
                f"register row either: {scope}. On what it did read, the "
                f"input states no register-map denominator for L4 to be "
                f"measured against")))
        summary["verdict"] = "NOT_APPLICABLE"
        summary["documentary_census"] = census
        return 2, summary

    if l4_path is None:
        attach(summary, Denominator(
            unit="register address bindings declared by a staged HDL input",
            examined=0,
            considered=len(declared),
            not_applicable_reason=(
                f"the input declares {len(declared)} register address "
                f"binding(s) but no L4_REGMAP.json was emitted, so there "
                f"is nothing to measure them against")))
        summary["verdict"] = "NOT_APPLICABLE"
        summary["declared"] = len(declared)
        return 2, summary

    try:
        l4 = json.loads(l4_path.read_text(encoding="utf-8",
                                          errors="replace"))
    except Exception as exc:
        summary["verdict"] = "FAIL"
        summary["error"] = f"cannot parse {l4_path.name}: {exc}"
        attach(summary, Denominator(
            unit="register address bindings declared by a staged HDL input",
            examined=0,
            considered=len(declared),
            not_applicable_reason=f"L4 did not parse: {exc}"))
        return 1, summary

    names, addrs = carried_registers(l4)
    absent: List[Dict[str, Any]] = []
    for b in declared:
        if b["name"] and b["name"] in names:
            continue
        if isinstance(b["value"], int) and b["value"] in addrs:
            continue
        absent.append(b)

    carried = len(declared) - len(absent)
    attach(summary, Denominator(
        unit="register address bindings declared by a staged HDL input",
        examined=len(declared),
        considered=len(declared),
        details={
            "carried_in_l4": carried,
            "absent_from_l4": len(absent),
            "l4_registers": len(l4.get("registers") or []),
            "declaring_types": sorted({b["type_name"] for b in declared}),
        }))
    summary["declared"] = len(declared)
    summary["carried"] = carried
    summary["absent"] = [
        {"name": b["name"], "address": (f"0x{b['value']:x}"
                                        if isinstance(b["value"], int)
                                        else None),
         "declared_by": b["type_name"], "source_file": b["source_file"]}
        for b in absent[:64]
    ]
    summary["absent_total"] = len(absent)

    if not absent:
        summary["verdict"] = "PASS"
        return 0, summary

    waived, rationale = _waived(project)
    if waived:
        summary["verdict"] = "PASS_WITH_WAIVER"
        summary["waiver_rationale"] = rationale
        return 0, summary

    summary["verdict"] = "FAIL"
    return 1, summary


def render(summary: Dict[str, Any]) -> List[str]:
    """Human lines for the verdict — the denominator on every path."""
    den = summary.get("denominator") or {}
    verdict = summary.get("verdict")
    lines: List[str] = []
    # R-0915-113(7) — the DOCUMENTARY verdict speaks for itself. Falling
    # through to the HDL wording printed "the input declares 0 register
    # address binding(s) and L4 carries all 0" over a population of 29 that
    # had just been measured, which is the same substitution in a new place.
    if "documentary_registers_measured" in summary:
        tag = {"PASS": "PASS", "FAIL": "FAIL",
               "NOT_MEASURED": "NOT_MEASURED"}.get(str(verdict), str(verdict))
        lines.append(f"[{tag}] {GATE}: "
                     f"{den.get('not_applicable_reason', '')}")
        for rec in (summary.get("documentary_coverage") or []):
            if rec["outcome"] != COVERED:
                lines.append(f"  • [{rec['outcome']}] {rec['why']}")
        if verdict == "FAIL":
            lines.append("")
            lines.append("  Fix in Phase 1, not by hand: the register the "
                         "document declares must reach L4.registers[] at the "
                         "address the document states.")
        return lines
    if verdict == "NOT_APPLICABLE":
        lines.append(f"[SKIP] {GATE}: "
                     f"{den.get('not_applicable_reason', '')}")
        return lines
    if verdict == "NOT_MEASURED":
        # Printed as NOT_MEASURED, never SKIP: the first line is what a human
        # reads, and "SKIP" over a declared register map is the sentence this
        # branch exists to stop being said.
        lines.append(f"[NOT_MEASURED] {GATE}: "
                     f"{den.get('not_applicable_reason', '')}")
        absent = summary.get("documentary_names_absent_from_l4") or []
        if absent:
            shown = ", ".join(absent[:12])
            more = (f" (+{len(absent) - 12} more)" if len(absent) > 12
                    else "")
            lines.append(f"  absent from L4_REGMAP.registers[]: "
                         f"{shown}{more}")
        lines.append("  This is an observation, not a verdict: extend this "
                     "gate's declared side to the documented shape before "
                     "reading it as coverage.")
        return lines
    declared = summary.get("declared", 0)
    carried = summary.get("carried", 0)
    if verdict == "PASS":
        lines.append(f"[PASS] {GATE}: the input declares {declared} "
                     f"register address binding(s) and L4 carries all "
                     f"{carried}")
        return lines
    if verdict == "PASS_WITH_WAIVER":
        lines.append(f"[PASS] {GATE}: waived by waivers.{WAIVER_KEY} "
                     f"({summary.get('absent_total', 0)} of {declared} "
                     f"declared binding(s) absent from L4): "
                     f"{str(summary.get('waiver_rationale', ''))[:70]}…")
        return lines
    if summary.get("error"):
        lines.append(f"[FAIL] {GATE}: {summary['error']}")
        return lines
    lines.append(
        f"[FAIL] {GATE}: the input declares {declared} register address "
        f"binding(s); L4 carries {carried} and is missing "
        f"{summary.get('absent_total', 0)}. Every consumer of L4 builds "
        f"its decode from the registers L4 carries, so a declared "
        f"binding that is absent here is a decode that is never "
        f"generated — and nothing downstream can tell that apart from a "
        f"register the design does not have.")
    for a in (summary.get("absent") or [])[:6]:
        lines.append(f"  • {a['name']} = {a['address']} "
                     f"(declared by {a['declared_by']} in "
                     f"{a['source_file']})")
    lines.append("")
    lines.append("  Fix in Phase 1, not by hand: the address-valued enum "
                 "the input declares must reach L4.registers[].")
    lines.append(f"  Or document the alternative in waivers.json under "
                 f'"{WAIVER_KEY}" (>={WAIVER_MIN_LEN} chars).')
    return lines


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog=GATE, description=__doc__)
    ap.add_argument("project_dir")
    ap.add_argument("--json", dest="json_out", default=None)
    args = ap.parse_args(argv)

    project = Path(args.project_dir).resolve()
    if not project.is_dir():
        print(f"[SKIP] {GATE}: {project} is not a directory")
        return 2

    rc, summary = evaluate(project)
    for line in render(summary):
        print(line)
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return rc


if __name__ == "__main__":
    sys.exit(main())
