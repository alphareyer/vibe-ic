"""Carry explicit scalar register widths without inferring field semantics."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _prose_polarity as _polarity  # noqa: E402


def _introducing_sentence(text: str, header_start: int) -> str:
    """The sentence that INTRODUCES the table whose header starts here.

    WHY A TABLE NEEDS ONE AT ALL (vibe-ic#712, #706, #711). A markdown row is a
    closed grammar and a denial cannot be spelled inside a cell — but the
    DOCUMENT around it is prose, and #711's defect was exactly this shape one
    field over: a document saying the old fixed die "has NO meaning here and is
    REMOVED, not translated" had that rectangle re-published as a mandate. A
    register map introduced by "the table below is superseded" is the same
    sentence about a different number, and without this consult every width in
    it is bound as a documented fact.

    THE ANCHOR IS THE CAPTION, and the caller picks the anchor while
    `_prose_polarity` owns the reach — a private widening of "how far back do I
    look" is the divergence that module exists to end. The caption is the last
    NON-BLANK line above the header; blank lines between a caption and its
    table are ordinary markdown, and `sentence_scope`'s "\n\n" break would
    otherwise put every captioned table out of reach and leave a check that
    cannot fire.

    WHAT IT DELIBERATELY DOES NOT REACH, so a reader is not misled about the
    cover: a denial further up than the caption's own sentence, and a denial
    written BELOW the table. `after=0` also keeps the window off the table's own
    cells — a legitimate `N/A` in some other row is `\bn/a\b` in this
    vocabulary, and reading forward would retire a whole map for one unrelated
    cell. Returns "" when the nearest non-blank line above is itself a table
    line (two adjacent tables introduce nothing) or when there is no line above.
    """
    head = text[:header_start]
    caption_end = len(head.rstrip("\r\n \t"))
    if caption_end <= 0:
        return ""
    caption_start = head.rfind("\n", 0, caption_end) + 1
    if head[caption_start:caption_end].lstrip().startswith("|"):
        return ""
    # ANCHORED AT THE CAPTION'S END, not across the whole line. A line can
    # hold two sentences, and anchoring on the line puts the break BETWEEN
    # them inside the anchor where the backward clamp cannot see it -- "This
    # part is not a UART. The register map for this revision:" then retires a
    # map its own introducing sentence states. MEASURED: that case bound no
    # width until the anchor moved here.
    lo, hi = _polarity.sentence_scope(text, caption_end, caption_end, after=0)
    return text[lo:hi]


def attach_documented_scalar_widths(registers, extracted):
    """Bind one unambiguous width by source, register name and address.

    Existing typed widths and arrays remain authoritative. Unsupported or
    contradictory table cells do not supply a width; no bus-width default.
    """
    aliases = {
        'address': {'address', 'addr', 'offset', '位址(hex)', '地址'},
        'name': {'name', 'register', '名稱', '名称'},
        'width': {'width', 'width(bits)', '寬度', '宽度'},
    }
    declarations = {}
    for source, text in extracted.items():
        if not isinstance(text, str):
            continue
        columns = None
        offset = 0
        # `keepends=True` so `offset` indexes the ORIGINAL text exactly — the
        # polarity consult below slices it, and `splitlines()` drops a
        # separator whose width is not always one character.
        for line_no, line in enumerate(text.splitlines(keepends=True), 1):
            start, offset = offset, offset + len(line)
            if not line.strip().startswith('|'):
                columns = None
                continue
            cells = [c.strip().strip('`*') for c in line.strip().strip('|').split('|')]
            keys = [c.lower().replace(' ', '') for c in cells]
            found = {k: [i for i, c in enumerate(keys) if c in names]
                     for k, names in aliases.items()}
            if any(found.values()):
                columns = ({k: positions[0] for k, positions in found.items()}
                           if all(len(v) == 1 for v in found.values()) else None)
                if columns is not None:
                    framing = _introducing_sentence(text, start)
                    if framing.strip() and _polarity.is_denied(framing):
                        # A denied table is not read at all. Refusing the
                        # HEADER rather than each row is what makes the
                        # denial govern the map instead of a cell.
                        columns = None
                continue
            if columns is None or len(cells) <= max(columns.values()):
                continue
            address, name, width = (cells[columns[k]] for k in ('address', 'name', 'width'))
            if not re.fullmatch(r'0x[0-9a-fA-F]+', address) or not re.fullmatch(r'[A-Za-z_]\w*', name):
                continue
            value = int(width) if re.fullmatch(r'[0-9]+', width) and int(width) > 0 else None
            key = (f'input/docs/{source}', name, int(address, 16))
            declarations.setdefault(key, []).append((value, line_no))
    for reg in registers:
        if reg.get('array') or reg.get('width_bits') is not None:
            continue
        key = (reg.get('evidence'), reg.get('name'), reg.get('address_int'))
        records = declarations.get(key, [])
        values = {value for value, _ in records}
        if len(values) != 1 or None in values:
            continue
        reg['width_bits'] = next(iter(values))
        reg['width_bits_source'] = {'kind': 'documented_scalar_width',
                                    'path': key[0], 'lines': [n for _, n in records]}
