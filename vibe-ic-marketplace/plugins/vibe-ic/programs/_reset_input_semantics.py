"""Finite reset declarations scoped to current-design clauses and exact ports.

Historical comparisons are not declarations. Conflicting explicit values remain
unknown; no corpus-global name/value join or generated-artifact inference.
"""
import re

_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_$]*\Z')
_PHASE_COLUMNS = ('clock', 'reset', 'polarity', 'assertion', 'release',
                  'release synchronizer stages')


def phase_declarations(text):
    """Read only explicit reset-phase table columns, with original line basis."""
    columns = None
    for number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip().startswith('|'):
            columns = None
            continue
        cells = [cell.strip().strip('`*').strip()
                 for cell in raw.strip().strip('|').split('|')]
        names = tuple(cell.lower() for cell in cells)
        if set(_PHASE_COLUMNS).issubset(names):
            columns = {key: names.index(key) for key in _PHASE_COLUMNS}
            continue
        if columns is None or all(re.fullmatch(r'[-:\s]+', cell) for cell in cells):
            continue
        if len(cells) <= max(columns.values()):
            continue
        if not current_clause(raw):
            continue
        values = {key: cells[index] for key, index in columns.items()}
        reset = values['reset']
        if not _NAME.fullmatch(reset):
            continue
        assertion = values['assertion'].lower()
        assertion = assertion if assertion in ('asynchronous', 'synchronous') else 'unknown'
        release = re.fullmatch(r'(asynchronous|synchronous)(?:\s+to\s+`?([A-Za-z_][\w$]*)`?)?',
                               values['release'], re.I)
        polarity = values['polarity'].lower().replace('-', '_')
        yield {'reset': reset,
               'clock': values['clock'] if _NAME.fullmatch(values['clock']) else 'unknown',
               'assertion': assertion,
               'release': release[1].lower() if release else 'unknown',
               'release_clock': release[2] if release and release[2] else 'unknown',
               'release_stages': int(values['release synchronizer stages'])
                   if re.fullmatch(r'[1-9]\d*', values['release synchronizer stages']) else 'unknown',
               'polarity': polarity if polarity in ('active_low', 'active_high') else 'unknown',
               'line': number, 'quote': raw.strip()}


def reset_domain_bindings(name, extracted):
    """Merge the same explicit domain declaration without choosing a conflict."""
    groups = {}
    for source, text in extracted.items():
        for row in phase_declarations(text):
            if row['reset'] == name:
                groups.setdefault(row['clock'], []).append((source, row))
    result = []
    for clock, rows in groups.items():
        binding = {'clock': clock}
        for field in ('assertion', 'release', 'release_clock', 'release_stages', 'polarity'):
            values = {row[field] for _, row in rows}
            binding[field] = next(iter(values)) if len(values) == 1 else 'unknown'
        binding['basis'] = [{'file': source, 'line': row['line'], 'quote': row['quote']}
                            for source, row in rows]
        result.append(binding)
    return result

_HISTORY = re.compile(r'\b(?:previous(?:ly)?|prior|historical|legacy|formerly)\b|先前|舊版|旧版|以前', re.I)
_COMPARISON = re.compile(r'\b(?:unlike|in contrast to|compared (?:with|to))\b|[與与].{0,16}(?:先前|舊版|旧版|以前)', re.I)
_SYNC = re.compile(r'\b(?:asynchronous|async(?:_reset)?|synchronous|sync_reset|synced reset)\b|非同步|異步|异步|同步', re.I)
_POLARITY = re.compile(r'\bactive[- _]?(low|high)\b', re.I)


def current_clause(line):
    # Keep declaration parentheses; discard only explicitly historical ones.
    line = re.sub(r'\([^()]*\)|（[^（）]*）',
                  lambda m: '' if _HISTORY.search(m[0]) or _COMPARISON.search(m[0]) else m[0], line)
    line = re.split(_COMPARISON, line, maxsplit=1)[0]
    if _HISTORY.search(line):
        return ''
    return line


def explicit_reset_port(name, text):
    """Whether current source explicitly assigns this exact port the reset role."""
    from _prose_polarity import is_denied
    if any(row['reset'] == name for row in phase_declarations(text)):
        return True
    port = r'`?' + re.escape(name) + r'`?(?![\w])'
    role_first = re.compile(r'\breset(?:\s+(?:input|signal|port))?\s*(?:is|:|=)\s*' + port, re.I)
    name_first = re.compile(
        r'(?<![\w])' + port + r'\s*(?:is|:)\s*'
        r'(?:(?:an?|the|current|external|global|system|input|asynchronous|synchronous|'
        r'active[- _]?(?:low|high))\s+)*reset\b', re.I)
    for raw in text.splitlines():
        for clause in re.split(r'[;；。]|(?<=[a-zA-Z])\.\s+', raw):
            line = current_clause(clause)
            match = role_first.search(line) or name_first.search(line)
            if match and not is_denied(line[:match.end()], ignore_bracketed=False):
                return True
    return False


def declarations(text, name=None):
    """Yield source-line-bound reset declarations; never borrow another port."""
    port = re.compile(r'(?<![\w])' + re.escape(name) + r'(?![\w])', re.I) if name else None
    phases = {row['line']: row for row in phase_declarations(text)}
    for number, raw in enumerate(text.splitlines(), 1):
        phase = phases.get(number)
        if phase is not None:
            if name is None or name == phase['reset']:
                yield {'line': number, 'evidence': raw.strip(),
                       'sync_values': {phase['assertion']},
                       'polarity_values': {phase['polarity']}}
            continue
        for clause in re.split(r'[;；。]|(?<=[a-zA-Z])\.\s+', raw):
            line = current_clause(clause)
            if not line or (port and not port.search(line)):
                continue
            if not port and not re.search(r'reset|\brst\w*\b|重置|復位|复位', line, re.I):
                continue
            syncs = {'asynchronous' if m[0].lower().startswith(('async','非','異','异')) else 'synchronous' for m in _SYNC.finditer(line)}
            polarities = {'active_' + m[1].lower() for m in _POLARITY.finditer(line)}
            if syncs or polarities:
                yield {'line': number, 'evidence': raw.strip(),
                       'sync_values': syncs, 'polarity_values': polarities}


def reset_semantics(name, text):
    rows = list(declarations(text, name))
    result = {}
    for field in ('sync', 'polarity'):
        values = set().union(*(row[field + '_values'] for row in rows))
        if values:
            result[field] = next(iter(values)) if len(values) == 1 else 'unknown'
    return result
