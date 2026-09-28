#!/usr/bin/env python3
"""A tool config the design stages as input: read it, deny the oracle, and resolve each tool variable by the owner's precedence.

Owner ruling (llv1 decision 21, the plan's W21 recommendation), for a flow
that runs on an external tool (`--librelane` in v1):

    design-declared L-docs  >  staged tool-config knobs  >  tool default
                               (ONLY names in the ingest map)

* **Tier 1, design-declared.** What the design's own L-docs declare, as the
  contract already emits it (`librelane_contract.emit_config` -> the config
  and its `.provenance.json`). This module does not read L-docs; it is handed
  that output, so there is one L-doc reader. A variable tier 1 decides is
  decided: staged values for it are recorded as superseded, never compared.
* **Tier 2, staged tool config.** Assignments in the design's staged
  reference flow (`input/reference_flow/**/*.mk|*.tcl`, the tree and the
  Make/Tcl grammar the phase-3 reference-flow ingest reads), and only for a
  name in that ingest's map (`phase3_one_shot_runner._ORFS_HONOURED_KNOBS`).
  Each name reaches the tool through `LIBRELANE_MAP`, which says for every
  name in the map either which tool variable(s) it sets, and how, or why it
  sets none. Only a value Make/Tcl would itself produce is emitted: an
  assignment inside a conditional (`ifeq`/`ifdef`/…, a Tcl block) is
  UNRESOLVED_CONDITIONAL, an append (`+=`) onto a value is VALUE_INVALID, and a
  `?=` after a value in the same file does nothing, as in Make.
* **Tier 3, tool default.** Nothing is emitted: the tool applies its own
  variable default. Its value is recorded when the caller supplies the tool's
  variable registry. A tool EXAMPLE design's config is never a default: this
  module never reads one as a default, never parses a JSON/YAML config, and
  never emits a tier-3 value.

§4.05 deny list, applied to EVERY staged file BEFORE it is opened, recipes
included, and to a symlink's resolved target as well as to the link:
  * a directory segment the repo's §4.05 authorities name off limits
    (`step_input_scope.deny_segments()` = `_reference_flow_boundary`
    `OFF_LIMITS_TREE_SEGMENTS` + the scoring channels `score/`,
    `canonical_samples/`), or a directory named with an
    `_reference_flow_boundary.ORACLE_NAME_WORDS` word;
  * a file name with such a word (golden, expected, results, metrics,
    metadata, rules, …), the scoring-oracle file forms
    (`step_input_scope.DENY_FILENAME_RE`: `_test.`, `_ref.`, `testbench`,
    `verified_`), or anything `step_input_scope.oracle_reason` refuses;
  * a symlink whose target lies outside the staged tree;
  * a non-recipe file whose CONTENT has the QoR-rules shape
    (`_reference_flow_boundary.is_oracle_qor_rules`; read to classify only).
Denial is compliance, reported as such, never as a coverage gap.

Refusals (raised as `librelane_contract.Refusal`, code first):
    STAGED_TOOL_UNSUPPORTED     the tool has no map here (only `librelane` in v1)
    STAGED_CONFIG_UNREADABLE    a staged recipe file cannot be read
    STAGED_KNOB_CONFLICT        for a variable tier 1 does not decide, the staged
                                files disagree: two values, or a value against a
                                clear / an invalid / a conditional / an append in
                                another file (file order is not a precedence)
    STAGED_KNOB_TARGET_UNKNOWN  something is staged for a mapped tool variable
                                the tool registry the caller supplied lacks

Everything else is recorded, not refused: denied files, files not parsed,
files in a dialect nothing here recognises, names outside the ingest map,
names with no tool equivalent, routing-layer statements, invalid, cleared,
appended or conditional values, targets the registry lacks with nothing
staged for them, and staged values superseded by the design.

Within ONE file the order is Make's own, so its last assignment is the one
Make produces (a trailing clear or an unexpanded value then leaves nothing to
emit). ACROSS files the include order is not known here, so any disagreement
refuses. That is the one difference between the two, and it is deliberate.

chip-AGNOSTIC: keys on flow variable names only; no design, PDK or cell literal.
"""
from __future__ import annotations

import argparse
import decimal
import hashlib
import json
import re
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _reference_flow_boundary as _rfb  # noqa: E402
import step_input_scope as _scope  # noqa: E402
from librelane_contract import Refusal, _LEVER_SUPERSEDES  # noqa: E402

SCHEMA = 'vibe-ic/staged-tool-config/1'
STAGED_REL = 'input/reference_flow'

TIER_DESIGN = 'design_declared'
TIER_STAGED = 'staged_tool_config'
TIER_TOOL = 'tool_default'

STAGED_TOOL_UNSUPPORTED = 'STAGED_TOOL_UNSUPPORTED'
STAGED_CONFIG_UNREADABLE = 'STAGED_CONFIG_UNREADABLE'
STAGED_KNOB_CONFLICT = 'STAGED_KNOB_CONFLICT'
STAGED_KNOB_TARGET_UNKNOWN = 'STAGED_KNOB_TARGET_UNKNOWN'
REFUSALS = (STAGED_TOOL_UNSUPPORTED, STAGED_CONFIG_UNREADABLE,
            STAGED_KNOB_CONFLICT, STAGED_KNOB_TARGET_UNKNOWN)

DENIED_ORACLE_TREE = 'DENIED_ORACLE_TREE'
DENIED_ORACLE_NAME = 'DENIED_ORACLE_NAME'
DENIED_ORACLE_SHAPE = 'DENIED_ORACLE_SHAPE'
DENIED_OUTSIDE_STAGED_TREE = 'DENIED_OUTSIDE_STAGED_TREE'
#: A link whose target cannot be resolved (a loop): nothing behind it is read.
DENIED_UNRESOLVABLE_LINK = 'DENIED_UNRESOLVABLE_LINK'
#: A directory link inside the staged tree: recorded, not followed.
NOT_TRAVERSED = 'NOT_TRAVERSED'
NOT_PARSED = 'NOT_PARSED'
UNRECOGNISED_DIALECT = 'UNRECOGNISED_DIALECT'
NOT_IN_INGEST_MAP = 'NOT_IN_INGEST_MAP'
NO_TOOL_EQUIVALENT = 'NO_TOOL_EQUIVALENT'
ROUTING_STATEMENT_NOT_MAPPED = 'ROUTING_STATEMENT_NOT_MAPPED'
VALUE_INVALID = 'VALUE_INVALID'
CLEARED = 'CLEARED'
UNRESOLVED_CONDITIONAL = 'UNRESOLVED_CONDITIONAL'
SUPERSEDED_BY_DESIGN = 'SUPERSEDED_BY_DESIGN'
TARGET_UNKNOWN = 'TARGET_UNKNOWN'

_UNEXPANDED = re.compile(r'\$[({]|\$::env|\$env\(')
#: The operator of a Make assignment the ingest grammar matched.
_MK_OP = re.compile(r'^\s*(?:export\s+)?[A-Z_][A-Z0-9_]*\s*([:?+]?=)')
_MK_OPEN = re.compile(r'^\s*(?:ifeq|ifneq|ifdef|ifndef)\b')
_MK_CLOSE = re.compile(r'^\s*endif\b')
_MK_DEFINE = re.compile(r'^\s*define\b')
_MK_ENDEF = re.compile(r'^\s*endef\b')
_ROUTING_NOTE = (
    'a routing-layer adjustment statement: LibreLane takes GRT_ADJUSTMENT / '
    'GRT_LAYER_ADJUSTMENTS as per-layer values, and a statement is not a value '
    '(its layer range may name flow variables this reader does not evaluate); '
    'it is not carried over')


def _pct(low_open: bool, high: float) -> Callable[[float], bool]:
    return lambda v: (v > 0 if low_open else v >= 0) and v <= high


# name in the ingest map -> the LibreLane variable(s) it sets, as
# (variable, scale, integer?, valid(value after scale)), or the reason it sets none.
# MEASURED 2026-09-28 on vibeic-eda 0.3.83 (librelane 9cf84954) and ORFS
# c9c22caf (the image's ORFS_COMMIT): each mapped pair reaches the SAME
# OpenROAD argument in both flows.
_Target = Tuple[str, float, bool, Callable[[float], bool]]
LIBRELANE_MAP: Dict[str, Any] = {
    # ORFS floorplan.tcl `-utilization $CORE_UTILIZATION`;
    # LibreLane floorplan.tcl:94 `-utilization $FP_CORE_UTIL` (both percent).
    'CORE_UTILIZATION': (('FP_CORE_UTIL', 1.0, False, _pct(True, 100.0)),),
    'FP_CORE_UTIL': (('FP_CORE_UTIL', 1.0, False, _pct(True, 100.0)),),
    # ORFS global_place `-density $PLACE_DENSITY` (a fraction);
    # LibreLane gpl.tcl:42 `-density [expr $PL_TARGET_DENSITY_PCT / 100.0]`.
    'PLACE_DENSITY': (('PL_TARGET_DENSITY_PCT', 100.0, False, _pct(True, 100.0)),),
    # ORFS util.tcl:35 passes `-repair_tns $TNS_END_PERCENT` on EVERY
    # repair_timing_helper call (CTS and global route, setup and hold);
    # LibreLane splits the same argument per stage and per check
    # (rsz_timing_postcts.tcl:39/49, rsz_timing_postgrt.tcl:42/52).
    'TNS_END_PERCENT': (
        ('PL_RESIZER_SETUP_REPAIR_TNS_PCT', 1.0, False, _pct(False, 100.0)),
        ('PL_RESIZER_HOLD_REPAIR_TNS_PCT', 1.0, False, _pct(False, 100.0)),
        ('GRT_RESIZER_SETUP_REPAIR_TNS_PCT', 1.0, False, _pct(False, 100.0)),
        ('GRT_RESIZER_HOLD_REPAIR_TNS_PCT', 1.0, False, _pct(False, 100.0))),
    # LibreLane's own names (the ingest map ported them from LibreLane):
    # `-max_utilization` on the post-CTS repair (rsz_timing_postcts.tcl:40).
    'PL_RESIZER_SETUP_MAX_UTIL_PCT': (
        ('PL_RESIZER_SETUP_MAX_UTIL_PCT', 1.0, False, _pct(True, 100.0)),),
    'PL_RESIZER_HOLD_MAX_UTIL_PCT': (
        ('PL_RESIZER_HOLD_MAX_UTIL_PCT', 1.0, False, _pct(True, 100.0)),),
    # ORFS cts.tcl:24-25 / LibreLane cts.tcl:67-68: -sink_clustering_size /
    # -sink_clustering_max_diameter.
    'CTS_CLUSTER_SIZE': (('CTS_SINK_CLUSTERING_SIZE', 1.0, True, lambda v: v >= 1),),
    'CTS_CLUSTER_DIAMETER': (
        ('CTS_SINK_CLUSTERING_MAX_DIAMETER', 1.0, False, lambda v: v > 0),),
    # LibreLane cts.tcl:77-78: `-distance_between_buffers` when non-zero.
    'CTS_DISTANCE_BETWEEN_BUFFERS': (
        ('CTS_DISTANCE_BETWEEN_BUFFERS', 1.0, False, lambda v: v >= 0),),
    'PLACE_DENSITY_LB_ADDON': (
        'ORFS adds it to a density lower bound OpenROAD computes at run time '
        '(util.tcl:185-191); LibreLane declares no such variable'),
    'RESIZER_RECOVER_POWER_PCT': (
        "vibe-ic's own name for repair_timing -recover_power; LibreLane passes "
        'no such argument and declares no variable for it'),
    'SWAP_ARITH_OPERATORS': (
        "an ORFS yosys-script switch; LibreLane's synthesis script has no "
        'equivalent variable'),
    'REMOVE_ABC_BUFFERS': (
        "an ORFS yosys-script switch; LibreLane's SYNTH_ABC_BUFFERING is a "
        'different operation (it inserts buffers), so it is not an equivalent'),
    'ADDER_MAP_FILE': (
        "an ORFS yosys techmap file; LibreLane's synthesis script reads no "
        'adder map'),
}
TOOL_MAPS = {'librelane': LIBRELANE_MAP}


def _runner(runner: Any = None) -> Any:
    """The phase-3 runner, for the ingest map and the Make/Tcl grammar; the one
    definition of both. Passed in when the caller is the runner itself."""
    if runner is not None:
        return runner
    import phase3_one_shot_runner
    return phase3_one_shot_runner


def ingest_map(runner: Any = None) -> frozenset:
    """The names the phase-3 reference-flow ingest honours (tier 2's vocabulary)."""
    return frozenset(_runner(runner)._ORFS_HONOURED_KNOBS)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _name_denial(rel_in_tree: str) -> Optional[str]:
    """The §4.05 verdict on a path inside the staged tree, from its NAME alone
    (nothing is opened). The repo's authorities, unioned, never restated."""
    parts = Path(rel_in_tree).parts
    segments = set(_scope.deny_segments()) | set(_rfb.ORACLE_TREE_SEGMENTS)
    for part in parts[:-1]:
        if part.lower() in segments or _rfb.is_oracle_name(part):
            return DENIED_ORACLE_TREE
    name = parts[-1]
    if (_rfb.is_oracle_name(name) or re.search(_scope.DENY_FILENAME_RE, name.lower())
            or _scope.oracle_reason(rel_in_tree) is not None):
        return DENIED_ORACLE_NAME
    return None


def _denial(root: Path, path: Path) -> Optional[str]:
    """Deny before opening: the link's own name, then its resolved target's."""
    verdict = _name_denial(str(path.relative_to(root)))
    if verdict:
        return verdict
    if path.is_symlink():
        try:
            target = path.resolve(strict=True)
        except FileNotFoundError:
            target = path.resolve()  # dangling: judged by where it points
        except (RuntimeError, OSError):
            return DENIED_UNRESOLVABLE_LINK
        try:
            rel = target.relative_to(root.resolve())
        except ValueError:
            return DENIED_OUTSIDE_STAGED_TREE
        verdict = _name_denial(str(rel))
        if verdict:
            return verdict
    if path.suffix not in _rfb.RECIPE_SUFFIXES:
        try:
            # a classifier, never an extractor: the parsed content is dropped here
            if _rfb.is_oracle_qor_rules(path.read_text(errors='ignore')):
                return DENIED_ORACLE_SHAPE
        except OSError:
            return None
    return None


def _tcl_depth(line: str, depth: int) -> int:
    """Brace depth after `line`; braces inside a quoted string or escaped do not count."""
    quoted = escaped = False
    for ch in line:
        if escaped:
            escaped = False
        elif ch == '\\':
            escaped = True
        elif ch == '"':
            quoted = not quoted
        elif not quoted and ch == '{':
            depth += 1
        elif not quoted and ch == '}':
            depth = max(0, depth - 1)
    return depth


def _statements(text: str, is_tcl: bool, R: Any) -> Tuple[List[Dict[str, Any]], List[Tuple[int, str]]]:
    """Every assignment the ingest grammar reads, with its line, its Make operator
    and whether it sits inside a conditional; and every routing-layer statement."""
    found: List[Dict[str, Any]] = []
    routing: List[Tuple[int, str]] = []
    depth, in_define = 0, False
    for number, line in enumerate(text.splitlines(), 1):
        conditional = depth > 0
        fm = R._RF_FASTROUTE_ADJUST_RE.match(line)
        if fm:
            routing.append((number, fm.group(1).strip()))
        match, op = None, '='
        if is_tcl:
            for rx in (R._RF_TCL_ENV_SET_RE, R._RF_TCL_SETENV_RE, R._RF_TCL_SET_RE):
                match = rx.match(line)
                if match:
                    break
            depth = _tcl_depth(line, depth)
        else:
            if in_define:
                in_define = not _MK_ENDEF.match(line)
                continue
            if _MK_DEFINE.match(line):
                in_define = True
                continue
            if _MK_OPEN.match(line):
                depth += 1
                continue
            if _MK_CLOSE.match(line):
                depth = max(0, depth - 1)
                continue
            match = R._RF_MK_ASSIGN_RE.match(line)
            if match:
                op_match = _MK_OP.match(line)
                op = op_match.group(1) if op_match else '='
        if match and not fm:
            found.append({'line': number, 'name': match.group(1), 'raw': match.group(2),
                          'op': op, 'conditional': conditional})
    return found, routing


def read_staged(project: Path, runner: Any = None) -> Dict[str, Any]:
    """Every staged file under `input/reference_flow`, classified, and every
    assignment and routing statement a readable recipe makes, with its file and
    line. No value is judged here and no denied file is opened for values."""
    R = _runner(runner)
    project = Path(project)
    root = project / STAGED_REL
    out: Dict[str, Any] = {'root': STAGED_REL if root.is_dir() else None,
                           'files': [], 'assignments': [], 'routing': []}
    if not root.is_dir():
        return out
    # A directory LINK is recorded, never silently dropped: rglob does not
    # descend into it, and `is_dir()` would filter the entry itself away.
    for path in sorted(p for p in root.rglob('*') if p.is_symlink() and p.is_dir()):
        denied = _denial(root, path)
        out['files'].append({'path': str(path.relative_to(project)),
                             'disposition': denied or NOT_TRAVERSED})
    for path in sorted(p for p in root.rglob('*') if p.is_symlink() or not p.is_dir()):
        if path.is_symlink() and path.is_dir():
            continue
        rel = str(path.relative_to(project))
        row: Dict[str, Any] = {'path': rel}
        denied = _denial(root, path)
        if denied:
            row['disposition'] = denied
            out['files'].append(row)
            continue
        if path.suffix not in _rfb.RECIPE_SUFFIXES:
            row['disposition'] = NOT_PARSED
            out['files'].append(row)
            continue
        try:
            text = path.read_text(errors='replace')
            row['sha256'] = _sha(path)
        except OSError as exc:
            raise Refusal(STAGED_CONFIG_UNREADABLE, f'{rel}: {type(exc).__name__}: {exc}') from None
        is_tcl = path.suffix == '.tcl'
        if R._rf_recipe_is_unrecognised(text, is_tcl):
            row['disposition'] = UNRECOGNISED_DIALECT
            out['files'].append(row)
            continue
        row['disposition'] = 'READ'
        out['files'].append(row)
        found, routing = _statements(text, is_tcl, R)
        for a in found:
            out['assignments'].append({**a, 'value': R._rf_strip_knob_value(a['raw']),
                                       'file': rel, 'source': f"{rel}:{a['line']}"})
        for number, statement in routing:
            out['routing'].append({'statement': statement, 'source': f'{rel}:{number}'})
    return out


def _convert(raw: str, target: _Target) -> Optional[Any]:
    """The staged value in the tool variable's unit, or None when it is not a
    finite number, is an unexpanded flow variable, overflows, or is out of
    range. Decimal, so a fraction scaled to a percent stays exact."""
    _name, scale, integer, valid = target
    if _UNEXPANDED.search(raw):
        return None
    try:
        number = Decimal(raw) * Decimal(str(scale))
        if not number.is_finite():
            return None
        whole = number == number.to_integral_value()
        if integer and not whole:
            return None
        value: Any = int(number) if whole else float(number)
    except (decimal.DecimalException, ValueError, OverflowError):
        return None
    return value if valid(value) else None


def _design_keys(design: Mapping[str, Any]) -> Dict[str, str]:
    """tool variable -> the design key that declares it (a key the contract's
    overlay would supersede counts as declaring its successor)."""
    keys = {key: key for key in design}
    for new, olds in _LEVER_SUPERSEDES.items():
        for old in olds:
            if old in design and new not in keys:
                keys[new] = old
    return keys


def _file_states(assignments: List[Dict[str, Any]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """(file, NAME) -> what Make/Tcl leaves in that name after the whole file.

    kind: value (raw text), cleared (empty), appended (`+=` onto a value: a
    list, never a number), conditional (any assignment of the name sits in a
    conditional block, so the file's result depends on a condition not read
    here). A `?=` after a value in the same file does nothing."""
    states: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for a in assignments:
        key = (a['file'], a['name'])
        prior = states.get(key)
        if prior and prior['kind'] == 'conditional':
            continue
        if a['conditional']:
            states[key] = {'kind': 'conditional', 'value': a['value'], 'source': a['source']}
        elif a['op'] == '?=' and prior is not None:
            continue
        elif a['op'] == '+=' and prior is not None and prior['kind'] in ('value', 'appended'):
            states[key] = {'kind': 'appended', 'value': f"{prior['value']} {a['value']}".strip(),
                           'source': a['source']}
        else:
            states[key] = {'kind': 'value' if a['value'] != '' else 'cleared',
                           'value': a['value'], 'source': a['source']}
    return states


def resolve(project: Path, tool: str, design: Mapping[str, Any],
            design_sources: Mapping[str, str], *,
            tool_registry: Optional[Mapping[str, Mapping[str, Any]]] = None,
            runner: Any = None) -> Dict[str, Any]:
    """Resolve every tool variable the ingest map can reach, by precedence.

    ``design`` / ``design_sources``: tier 1, exactly as the contract emits it
    (`emit_config` config and its `.provenance.json`). ``tool_registry``: the
    tool's own variable declarations ({name: {"default": ...}}) read from the
    image (`librelane_image_facts.tool_registry`); when given, a mapped
    variable it lacks refuses only if something is staged for it, and each
    tier-3 row records the tool's default.

    Returns ``overlay`` ({VAR: (value, source)}, tier 2 only, the shape
    `librelane_contract.resolve_step_configs(overlay=)` takes) and ``record``
    (the whole decision, for the impl record and the config provenance).
    """
    table = TOOL_MAPS.get(tool)
    if table is None:
        raise Refusal(STAGED_TOOL_UNSUPPORTED, f'{tool}: known {sorted(TOOL_MAPS)}')
    names = ingest_map(runner)
    staged = read_staged(project, runner)
    declared = _design_keys(design)
    notes: List[Dict[str, Any]] = []
    for r in staged['routing']:
        notes.append({**r, 'disposition': ROUTING_STATEMENT_NOT_MAPPED, 'reason': _ROUTING_NOTE})
    # every staged state per target: (target, state) where state is a value or a disposition
    offered: Dict[str, List[Dict[str, Any]]] = {}
    for (_file, name), state in sorted(_file_states(staged['assignments']).items()):
        row = {'name': name, 'value': state['value'], 'source': state['source']}
        if name not in names:
            notes.append({**row, 'disposition': NOT_IN_INGEST_MAP})
            continue
        mapping = table.get(name)
        if isinstance(mapping, str):
            notes.append({**row, 'disposition': CLEARED if state['kind'] == 'cleared' else NO_TOOL_EQUIVALENT,
                          'reason': mapping})
            continue
        for target in mapping:
            if state['kind'] == 'value':
                converted = _convert(state['value'], target)
                kind = 'value' if converted is not None else VALUE_INVALID
            else:
                converted = None
                kind = {'cleared': CLEARED, 'appended': VALUE_INVALID,
                        'conditional': UNRESOLVED_CONDITIONAL}[state['kind']]
            offered.setdefault(target[0], []).append(
                {**row, 'variable': target[0], 'kind': kind, 'converted': converted})
    variables = sorted({t[0] for m in table.values() if not isinstance(m, str) for t in m})
    overlay: Dict[str, Tuple[Any, str]] = {}
    rows: Dict[str, Dict[str, Any]] = {}
    for var in variables:
        cands = offered.get(var, [])
        unknown = tool_registry is not None and var not in tool_registry
        if var in declared:
            key = declared[var]
            rows[var] = {'tier': TIER_DESIGN, 'value': design[key], 'design_key': key,
                         'source': design_sources.get(key, '(no provenance given)')}
            for c in cands:
                notes.append({**c, 'disposition': SUPERSEDED_BY_DESIGN})
            continue
        if cands and unknown:
            raise Refusal(STAGED_KNOB_TARGET_UNKNOWN, f'{tool}: {var} is staged ('
                          + ', '.join(c['source'] for c in cands)
                          + ') but the tool registry declares no such variable')
        states = sorted({json.dumps([c['kind'], c['converted']]) for c in cands})
        if len(states) > 1:
            raise Refusal(STAGED_KNOB_CONFLICT, f'{var}: ' + '; '.join(
                f"{c['source']} {c['name']}={c['value']!r} ({c['kind']})" for c in cands))
        if cands and cands[0]['kind'] == 'value':
            source = (f"{STAGED_REL} staged tool config: " + ', '.join(
                f"{c['source']} {c['name']}={c['value']}" for c in cands))
            overlay[var] = (cands[0]['converted'], source)
            rows[var] = {'tier': TIER_STAGED, 'value': cands[0]['converted'], 'source': source,
                         'from': [{'name': c['name'], 'value': c['value'],
                                   'source': c['source']} for c in cands]}
            continue
        for c in cands:
            notes.append({k: v for k, v in c.items() if k not in ('kind', 'converted')}
                         | {'disposition': c['kind']})
        rows[var] = {'tier': TIER_TOOL, 'emitted': False,
                     'tool_default': (tool_registry.get(var) or {}).get('default')
                     if tool_registry is not None else None,
                     'source': (f'{tool} variable default (not emitted)' if tool_registry is not None
                                else f'{tool} variable default (not emitted; registry not supplied)')}
        if unknown:
            rows[var].update({'disposition': TARGET_UNKNOWN,
                              'reason': 'the tool registry declares no such variable; nothing is '
                                        'staged for it, so nothing is lost'})
    record = {'schema': SCHEMA, 'tool': tool,
              'precedence': [TIER_DESIGN, TIER_STAGED, TIER_TOOL],
              'staged_root': staged['root'], 'files': staged['files'],
              'variables': rows, 'notes': notes}
    return {'overlay': overlay, 'record': record}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('project', type=Path)
    parser.add_argument('--tool', default='librelane')
    parser.add_argument('--design-config', type=Path,
                        help="tier 1: the contract's emitted config JSON "
                             '(its sibling .provenance.json is read too); default: none declared')
    parser.add_argument('--tool-registry', type=Path,
                        help='the tool variable registry read from the image (JSON)')
    parser.add_argument('--out', type=Path, help='write the record here (atomically) as well')
    args = parser.parse_args(argv)
    design: Dict[str, Any] = {}
    sources: Dict[str, str] = {}
    if args.design_config:
        design = json.loads(args.design_config.read_text())
        prov = args.design_config.with_suffix('.provenance.json')
        sources = json.loads(prov.read_text()) if prov.is_file() else {}
    registry = json.loads(args.tool_registry.read_text()) if args.tool_registry else None
    try:
        result = resolve(args.project, args.tool, design, sources, tool_registry=registry)
    except Refusal as exc:
        print(f'REFUSED {exc}', file=sys.stderr)
        return 2
    text = json.dumps(result['record'], indent=2, sort_keys=True)
    if args.out:
        from _atomic_artefact import write_text
        write_text(args.out, text + '\n')
    print(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
