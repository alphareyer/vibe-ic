#!/usr/bin/env python3
"""Step 30's tool arm: OpenSTA `write_path_spice` on the STAPostPNR corner.

The deck is the TOOL's (T106). OpenSTA `write_path_spice` writes, for each of
the top-N setup paths, the path's cells as subckt instances, every net's SPEF
RC network, the side-input sensitisation and the stimulus, for ngspice or
Xyce. `spice_correlation_check`'s own deck builders never read the SPEF's RC
network (measured: scaling every `*D_NET` cap by 10 moved nothing,
RULINGS CT-08); this deck carries it.

What stays vibe-ic's (the gate), applied to the tool's result:

* the POST-ROUTE BASIS: the netlist, SDC, SPEF and liberties are exactly the
  ones `OpenROAD.STAPostPNR` timed this corner with
  (`librelane_contract.post_pnr_timing_inputs`), from the state step 23
  recorded (`librelane_postroute.stapostpnr_state`), or the arm refuses by
  name;
* the LIBERTY-GRID TOLERANCE: `spice_correlation_check.
  derive_liberty_path_tolerance` over the stages the STA path reports, at the
  slew and load STA used, with no fixed percentage;
* the SAME ARC: SPICE is measured from the start pin to the end pin at the
  transitions the STA path names, at the liberty's own thresholds;
* the SPEF-MUTATION CONTROL: every `*CAP` (and `*D_NET` total) is scaled by
  ``MUTATION_FACTOR`` and the same start/end pins are re-timed and
  re-simulated. A SPICE delay that does not move is not a post-layout
  correlation (`SPEF_UNRESPONSIVE`).

Two things the installed OpenSTA needs from its inputs, both derived, never
typed:

* its subckt reader treats the LAST token of every `X` line as a subckt name,
  so a PDK whose devices are subckts called with parameters
  (`X_i_0 ZN I VSS VPW nfet_05v0 W=8.2e-07 L=6e-07`) fails with "missing
  definitions for L=6e-07"; and every subckt port that is neither a liberty
  pin nor the power/ground name is refused (error 1606). ``fold_subckts``
  writes a derived copy of the PDK's declared `CELL_SPICE_MODELS` /
  `PAD_SPICE_MODELS` where supply ports the liberties declare at the SAME
  voltage as the power (ground) name are folded into it, and parameterised
  device lines are indented so that reader skips them (ngspice reads them
  unchanged). The source fix is a fork PR on vibeic/OpenSTA.
* `write_path_spice` dereferences the operating conditions of the library the
  path's first liberty pin belongs to; a pad library without
  `default_operating_conditions` crashes it (SIGSEGV, measured). The arm sets
  the corner's first (cell) library's own operating conditions.

The CELL VIEW is the layout's, not the schematic's (F22). The PDK's declared
`CELL_SPICE_MODELS` are schematic netlists: device W/L only, `as=ad=ps=pd=0`
(no junction capacitance) and no wiring capacitance. The liberty was
characterised on the cells' LAYOUT. MEASURED on gf180mcuD ss_125C_4v50, one
cell per deck at liberty grid points driven by the liberty's own
`normalized_driver_waveform`: the schematic view reads -35..-50% at the
smallest load (nand2_1, buf_1) and still -5%/-18% at a mid grid point, while
the same cells extracted from the PDK's own GDS read -0.4%/-4.6% there; the
model corner, temperature and supply the arm uses are already the liberty's
(changing any of them moves the error away from zero). On spm max_ss that
schematic view alone put the register-to-register path at -29%. So
``extract_cells`` extracts every standard cell the routed netlist
instantiates from the declared `CELL_GDS`, with the PDK's declared `MAGICRC`
(`libs.tech/librelane/config.tcl`), and those subckts take the place of the
schematic ones. A cell the layout cannot stand in for (absent from the GDS,
or a different port set) keeps its schematic subckt and is named; a run
with no declared layout or rcfile simulates the schematic view and says so
(`cell_view`).

Dual: ngspice and Xyce. The device models are chosen from the PDK's
`libs.tech/<simulator>` tree by what the cells instantiate; a simulator tree
that defines none of those devices refuses by name
(`LL_SPICE_DEVICE_UNMODELLED`).

chip-AGNOSTIC: no design, PDK, cell or corner literal selects a branch.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _docker_memory as _dmem  # noqa: E402 — every `docker run` carries the ceiling
from _atomic_artefact import write_json  # noqa: E402
from librelane_contract import Refusal, _load, digest, post_pnr_timing_inputs  # noqa: E402
import spice_correlation_check as _scc  # noqa: E402 — the gate's pure helpers

SIMULATORS = ('ngspice', 'xyce')
#: Every capacitance doubled. The CT-08 lesson scaled by 10; MEASURED on spm
#: max_ss that pushed the register-to-register path from 12.8 ns to 49.5 ns in
#: STA, past the deck's own simulation window, so no SPICE edge was left to
#: compare. A control whose perturbed run cannot be measured controls nothing.
MUTATION_FACTOR = 2.0
#: The default subject: register-to-register setup paths, whose data path is
#: standard cells only. A path through an IO pad simulates the pad's full
#: transistor netlist and its external load (MEASURED on spm: > 29 min for one
#: output-pad path in ngspice) and its timing is set by the declared IO delays.
REG_TO_REG = '-path_delay max -from [all_registers -clock_pins] -to [all_registers -data_pins]'

_VOLTAGE_MAP_RE = re.compile(r'voltage_map\s*\(\s*"?(\w+)"?\s*,\s*([-\d.eE+]+)\s*\)')
_POWER_RAIL_RE = re.compile(r'power_rail\s*\(\s*"?(\w+)"?\s*,\s*([-\d.eE+]+)\s*\)')
_OPCOND_RE = re.compile(r'^\s*operating_conditions\s*\(\s*"?([\w.]+)"?\s*\)', re.M)
_LIBRARY_RE = re.compile(r'^\s*library\s*\(\s*"?([\w.]+)"?\s*\)', re.M)
_SUBCKT_RE = re.compile(r'^\s*\.subckt\s+(\S+)(.*)$', re.I)
_PARAM_TOKEN = re.compile(r'^\w+=\S*$')


def supply_voltages(liberty_texts: Sequence[str]) -> Dict[str, float]:
    """{supply pin: volts} as the liberties declare them (`voltage_map`, and
    the operating conditions' `power_rail`). First declaration wins."""
    out: Dict[str, float] = {}
    for text in liberty_texts:
        for name, value in _VOLTAGE_MAP_RE.findall(text) + _POWER_RAIL_RE.findall(text):
            out.setdefault(name, float(value))
    return out


def fold_subckts(sources: Sequence[Path], voltages: Dict[str, float],
                 power: str, ground: str, out: Path) -> Dict[str, Any]:
    """A copy of the declared cell SPICE the installed `write_path_spice` can
    read (see the module docstring). Only supply ports at the power (ground)
    name's declared voltage are folded; any other port is left as it is, so
    OpenSTA refuses it by name rather than this function guessing."""
    if power not in voltages or ground not in voltages:
        raise Refusal('LL_SPICE_SUPPLY_UNDECLARED',
                      f'{power}/{ground} have no declared voltage in the corner liberties')
    fold = {pin: (power if volts == voltages[power] else ground)
            for pin, volts in voltages.items()
            if pin not in (power, ground) and volts in (voltages[power], voltages[ground])}
    rails = {power, ground}
    lines: List[str] = []
    folded_cells = indented = dropped = 0
    defined: set = set()
    for source in sources:
        inside = shadowed = False
        statements: List[List[str]] = []
        for raw in Path(source).read_text(errors='replace').splitlines():
            if raw.startswith('+') and statements:
                statements[-1].append(raw)
            else:
                statements.append([raw])
        for statement in statements:
            head = statement[0]
            m = _SUBCKT_RE.match(head)
            if shadowed:
                # An earlier source already defines this cell: its first
                # definition is the one simulated (the extracted view is
                # passed ahead of the declared schematic one).
                shadowed = not head.strip().lower().startswith('.ends')
                continue
            if m and m.group(1).lower() in defined:
                shadowed = True
                continue
            if m:
                defined.add(m.group(1).lower())
                inside = True
                ports = m.group(2).split()
                kept = [p for p in ports if p not in fold]
                for p in ports:
                    if p in fold and fold[p] not in kept:
                        kept.append(fold[p])
                folded_cells += kept != ports
                lines.append(f'.subckt {m.group(1)} ' + ' '.join(kept))
                lines += statement[1:]
                continue
            if inside and head.strip().lower().startswith('.ends'):
                inside = False
                lines += statement
                continue
            kind = head[:1].lower()
            if not inside or kind not in 'xmdcr' or not head.strip():
                lines += statement
                continue
            tokens = [fold.get(t, t) for t in head.split()]
            words = [t for t in tokens[1:] + ' '.join(statement[1:]).replace('+', ' ').split()
                     if not _PARAM_TOKEN.match(t)]
            nodes = {'x': words[:-1], 'm': words[:4], 'd': words[:2],
                     'c': words[:2], 'r': words[:2]}[kind]
            if nodes and set(nodes) <= rails:
                # Every terminal on a supply the deck drives from an ideal
                # source: the element carries no signal (decoupling / ESD
                # capacitance and diodes between rails).
                dropped += 1
                continue
            body = ' '.join(tokens)
            if kind == 'x' and any(_PARAM_TOKEN.match(t) for t in tokens[1:]
                                   + ' '.join(statement[1:]).replace('+', ' ').split()):
                # A device call carries parameters; the installed reader would
                # take its last parameter for a subckt name.
                indented += 1
                body = ' ' + body
            lines.append(body)
            lines += statement[1:]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text('\n'.join(lines) + '\n')
    return {'path': str(out), 'folded': fold, 'cells_folded': folded_cells,
            'device_lines_indented': indented, 'rail_only_elements_dropped': dropped,
            'sources': {str(s): digest(Path(s)) for s in sources}}


def device_names(subckt_text: str) -> set:
    """Devices the cells instantiate: the subckt an `X` line calls (its last
    non-parameter token) and the model an `M` or `D` line names."""
    out = set()
    statement = ''
    for raw in subckt_text.splitlines() + ['']:
        if raw.startswith('+'):
            statement += ' ' + raw[1:]
            continue
        tokens = statement.split()
        if tokens and tokens[0][:1] in 'Xx':
            names = [t for t in tokens[1:] if not _PARAM_TOKEN.match(t)]
            if names:
                out.add(names[-1].lower())
        elif tokens and tokens[0][:1] in 'Mm' and len(tokens) >= 6:
            out.add(tokens[5].lower())
        elif tokens and tokens[0][:1] in 'Dd' and len(tokens) >= 4:
            out.add(tokens[3].lower())
        statement = raw.strip()
    cells = {m.group(1).lower() for m in
             (_SUBCKT_RE.match(line) for line in subckt_text.splitlines()) if m}
    return out - cells


_LIB_OPEN_RE = re.compile(r"^\s*\.lib\s+([A-Za-z0-9_]+)\s*$", re.I)
_LIB_REF_RE = re.compile(r"^\s*\.lib\s+['\"]?([^'\"\s]+)['\"]?\s+([A-Za-z0-9_]+)\s*$", re.I)
_DEF_RE = re.compile(r"^\s*\.(?:subckt|model)\s+(\S+)", re.I)


def model_sections(tech_dir: Path, simulator: str) -> Dict[Tuple[str, str], set]:
    """{(file, section): every device name the section defines, following its
    `.lib <file> <section>` references}. Files carrying the simulator's own
    suffix are read when there are any (a PDK ships one tree per dialect)."""
    files = sorted(p for p in Path(tech_dir).glob('*') if p.is_file())
    own = [p for p in files if p.suffix.lstrip('.').lower() == simulator.lower()]
    files = own or files
    direct: Dict[Tuple[str, str], set] = {}
    refs: Dict[Tuple[str, str], List[Tuple[str, str]]] = {}
    for path in files:
        current = None
        for line in path.read_text(errors='replace').splitlines():
            if line.lstrip().lower().startswith('.endl'):
                current = None
                continue
            opened = _LIB_OPEN_RE.match(line)
            if opened and current is None:
                current = (str(path), opened.group(1))
                direct.setdefault(current, set())
                refs.setdefault(current, [])
                continue
            if current is None:
                continue
            ref = _LIB_REF_RE.match(line)
            if ref:
                target = Path(tech_dir) / Path(ref.group(1)).name
                target = target if target in files else path
                refs[current].append((str(target), ref.group(2)))
                continue
            defined = _DEF_RE.match(line)
            if defined:
                direct[current].add(defined.group(1).lower())
    closure: Dict[Tuple[str, str], set] = {}

    def resolve(key: Tuple[str, str], seen: set) -> set:
        if key in closure:
            return closure[key]
        out = set(direct.get(key, set()))
        for ref in refs.get(key, []):
            if ref not in seen:
                out |= resolve(ref, seen | {ref})
        closure[key] = out
        return out
    for key in direct:
        resolve(key, {key})
    return closure


def model_file(tech_dir: Path, guest_dir: str, simulator: str, liberty: str,
               devices: set, temperature: Optional[float], out: Path) -> Dict[str, Any]:
    """The `-model_file` for one corner: the PDK's parameter preludes, then, for
    every device the cells call, the library section `select_model_section`
    picks for the corner's liberty among the sections that define it."""
    sections = model_sections(tech_dir, simulator)
    chosen: List[Tuple[str, str]] = []
    covered: set = set()
    for device in sorted(devices):
        if device in covered:
            continue
        candidates = [key for key, defined in sections.items() if device in defined]
        pick = _scc.select_model_section(candidates, liberty)
        if pick is None:
            continue
        chosen.append(tuple(pick))
        covered |= sections[tuple(pick)]
    missing = sorted(devices - covered)
    if missing:
        raise Refusal('LL_SPICE_DEVICE_UNMODELLED',
                      f'{simulator}: no section under {tech_dir} defines {missing}')
    files = sorted(p for p in Path(tech_dir).glob('*') if p.is_file())
    own = [p for p in files if p.suffix.lstrip('.').lower() == simulator.lower()] or files
    preludes = [p for p in own
                if re.search(r'^\s*\.param\s', p.read_text(errors='replace'), re.M | re.I)
                and not re.search(r'^\s*\.lib\s', p.read_text(errors='replace'), re.M | re.I)]
    guest = lambda p: f'{guest_dir}/{Path(p).name}'  # noqa: E731
    lines = [f'* vibe-ic step 30: device models for {liberty} ({simulator})']
    lines += [f'.include {guest(p)}' for p in preludes]
    lines += [f'.lib {guest(f)} {s}' for f, s in chosen]
    if temperature is not None:
        lines.append(f'.temp {temperature:g}' if simulator == 'ngspice'
                     else f'.options device temp={temperature:g}')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text('\n'.join(lines) + '\n')
    return {'path': str(out), 'sections': [list(c) for c in chosen],
            'preludes': [str(p) for p in preludes], 'temperature': temperature,
            'devices': sorted(devices)}


def arm_tcl(inputs: Dict[str, Any], project: Path, top: str, spef: str,
            opcond: Tuple[str, str], path_args: List[str], deck_dir: Path,
            cells: str, models: str, power: str, ground: str, simulator: str) -> str:
    """One `sta` session: the corner's own inputs, then for each path its
    `report_checks` and its `write_path_spice`."""
    lines = ['set_cmd_units -time ns -capacitance pF -current mA -voltage V '
             '-resistance kOhm -distance um']
    lines += [f'read_liberty {lib}' for lib in inputs['liberties']]
    lines += [f'read_verilog {project / inputs["sta_netlist"]}', f'link_design {top}',
              f'read_sdc {project / inputs["sdc"]}',
              f'set_operating_conditions -library {opcond[0]} {opcond[1]}',
              'set_propagated_clock [all_clocks]', f'read_spef {spef}']
    for index, args in enumerate(path_args, 1):
        lines.append(f'report_checks {args} -fields {{slew cap input_pins}} -digits 6 '
                     f'-format full > {deck_dir}/path_{index}.rpt')
        lines.append(f'if {{[catch {{write_path_spice -path_args {{{args}}} '
                     f'-spice_file {deck_dir}/path_{index}.sp -lib_subckt_files {{{cells}}} '
                     f'-model_file {models} -power {power} -ground {ground} '
                     f'-simulator {simulator}}} _e]}} {{ puts "VIBEIC_WPS_FAIL {index} $_e" }} '
                     f'else {{ puts "VIBEIC_WPS_OK {index}" }}')
    return '\n'.join(lines) + '\n'


def _docker(image: str, project: Path, mounts: List[Tuple[Path, str]], argv: List[str],
            cwd: Path, docker: str = 'docker', env: Optional[Dict[str, str]] = None
            ) -> subprocess.CompletedProcess:
    volumes = ['-v', f'{project.resolve()}:{project.resolve()}']
    for host, guest in mounts:
        volumes += ['-v', f'{Path(host).resolve()}:{guest}:ro']
    for key, value in (env or {}).items():
        volumes += ['-e', f'{key}={value}']
    return subprocess.run([docker, 'run', '--rm', '--network', 'none',
                           *_dmem.docker_memory_flags(), *volumes, '-w', str(cwd),
                           '--entrypoint', argv[0], image, *argv[1:]],
                          capture_output=True, text=True)


# --- the cell view: the layout the liberty was characterised on ------------

#: Where a PDK declares its Magic rcfile for its own flow.
_PDK_FLOW_CONFIGS = ('libs.tech/librelane/config.tcl', 'libs.tech/openlane/config.tcl')
_MAGICRC_RE = re.compile(r'^\s*set\s+::env\(MAGICRC\)\s+"?([^"\s]+)"?', re.M)
_INSTANCE_RE = re.compile(r'^\s*([A-Za-z_][\w$]*)\s+(?:#\s*\(.*?\)\s*)?\\?\S+\s*\(', re.M)


def pdk_magicrc(pdk_dir: Path, guest_root: str, pdk: str) -> Optional[Tuple[str, str]]:
    """(guest path, source) of the Magic rcfile the PDK's own flow config
    declares, its `$::env(PDK_ROOT)`/`$::env(PDK)` resolved to the guest."""
    for rel in _PDK_FLOW_CONFIGS:
        path = Path(pdk_dir) / rel
        m = _MAGICRC_RE.search(path.read_text(errors='replace')) if path.is_file() else None
        if m:
            value = (m.group(1).replace('$::env(PDK_ROOT)', guest_root)
                     .replace('$::env(PDK)', pdk))
            if '$' not in value:
                return value, f'{rel}:MAGICRC'
    return None


_DEVICE_LINE_RE = re.compile(r'^\s*[xXmMdD]\S*\s')


def subckt_ports(text: str) -> Dict[str, List[str]]:
    """{subckt name: its ports in declaration order}."""
    return {name: ports for name, (ports, _) in subckt_devices(text).items()}


def subckt_devices(text: str) -> Dict[str, Tuple[List[str], int]]:
    """{subckt name: (its ports in declaration order, how many device lines
    -- transistor, diode or subckt call -- it holds)}. First definition wins."""
    out: Dict[str, Tuple[List[str], int]] = {}
    current = None
    for line in text.splitlines():
        m = _SUBCKT_RE.match(line)
        if m:
            current = None if m.group(1) in out else m.group(1)
            if current:
                out[current] = ([p for p in m.group(2).split() if not _PARAM_TOKEN.match(p)], 0)
        elif line.strip().lower().startswith('.ends'):
            current = None
        elif current and _DEVICE_LINE_RE.match(line):
            ports, n = out[current]
            out[current] = (ports, n + 1)
    return out


def netlist_cells(netlist_text: str, known: Sequence[str]) -> List[str]:
    """The cells among `known` the structural netlist instantiates."""
    used = set(_INSTANCE_RE.findall(netlist_text))
    return sorted(c for c in known if c in used)


def extraction_tcl(gds: Sequence[str], cells: Sequence[str], out_dir: Path) -> str:
    """One Magic session: the declared cell GDS read once, then each cell
    extracted with its device geometry (AS/AD/PS/PD) and every coupling and
    ground capacitance (`cthresh 0`), no resistance."""
    lines = ['gds readonly true', 'gds rescale false']
    lines += [f'gds read {g}' for g in gds]
    for cell in cells:
        lines += [f'load {cell}', 'select top cell', f'extract path {out_dir}', 'extract all',
                  'ext2spice lvs', 'ext2spice cthresh 0', 'ext2spice extresist off',
                  f'ext2spice -p {out_dir} -o {out_dir}/{cell}.spice']
    return '\n'.join(lines + ['quit -noprompt']) + '\n'


def extract_cells(image: str, project: Path, mounts: List[Tuple[Path, str]], *,
                  gds: Sequence[str], magicrc: str, guest_root: str,
                  schematic: Dict[str, List[str]], cells: Sequence[str],
                  out_dir: Path) -> Dict[str, Any]:
    """The layout view of `cells`, one subckt per cell in the schematic's
    port order, written to `out_dir/cells_extracted.spice`. A cell whose
    extraction has no device, or whose ports are not the schematic's, is
    left to its schematic subckt and named in `kept_schematic`."""
    work = out_dir / 'extract'
    work.mkdir(parents=True, exist_ok=True)
    tcl = work / 'extract.tcl'
    tcl.write_text(extraction_tcl(gds, cells, work))
    run = _docker(image, project, mounts,
                  ['magic', '-dnull', '-noconsole', '-rcfile', magicrc, str(tcl)], work,
                  env={'PDK_ROOT': guest_root})
    (work / 'magic.log').write_text(run.stdout + '\n' + run.stderr)
    kept: Dict[str, str] = {}
    body: List[str] = []
    for cell in cells:
        path = work / f'{cell}.spice'
        text = path.read_text(errors='replace') if path.is_file() else ''
        ports, devices = subckt_devices(text).get(cell, ([], 0))
        start = re.search(r'^\.subckt\s+' + re.escape(cell) + r'\b.*$', text, re.M | re.I)
        end = re.search(r'^\.ends\b.*$', text[start.end():], re.M | re.I) if start else None
        inner = text[start.end():start.end() + end.start()].strip('\n') if end else ''
        if not ports or not devices:
            kept[cell] = 'no device extracted from the declared layout'
            continue
        if set(ports) != set(schematic[cell]):
            kept[cell] = f'layout ports {sorted(ports)} are not the schematic ports'
            continue
        body += [f'.subckt {cell} ' + ' '.join(schematic[cell]), inner, '.ends']
    out = out_dir / 'cells_extracted.spice'
    out.write_text('* vibe-ic step 30: cells extracted from the declared layout\n'
                   + '\n'.join(body) + '\n')
    return {'path': str(out), 'magic_rc': run.returncode, 'magicrc': magicrc,
            'extracted': sorted(set(cells) - set(kept)), 'kept_schematic': kept}


def cell_view(image: str, project: Path, mounts: List[Tuple[Path, str]], *, config: dict,
              pdk_dir: Path, guest_root: str, pdk: str, netlist: Path,
              cell_sources: Sequence[Path], out_dir: Path) -> Dict[str, Any]:
    """Which standard-cell netlists the deck simulates, and why (module
    docstring). `cell_sources` are the declared `CELL_SPICE_MODELS`; pads keep
    their declared netlists (the arm's subject is register to register)."""
    schematic: Dict[str, List[str]] = {}
    inert: set = set()
    for source in cell_sources:
        for name, (ports, devices) in subckt_devices(Path(source).read_text(errors='replace')).items():
            if name not in schematic:
                schematic[name] = ports
                if not devices:
                    # No device in the declared netlist (fill, tie-less
                    # spacers): nothing to simulate in either view.
                    inert.add(name)
    gds = list(config.get('CELL_GDS') or [])
    rc = pdk_magicrc(pdk_dir, guest_root, pdk)
    if not gds or rc is None:
        return {'view': 'schematic', 'sources': [],
                'reason': ('no CELL_GDS declared' if not gds else
                           'no MAGICRC declared in ' + ' or '.join(_PDK_FLOW_CONFIGS))}
    used = netlist_cells(Path(netlist).read_text(errors='replace'),
                         [c for c in schematic if c not in inert])
    got = extract_cells(image, project, mounts, gds=gds, magicrc=rc[0], guest_root=guest_root,
                        schematic=schematic, cells=used, out_dir=out_dir)
    view = ('layout-extracted' if got['extracted'] and not got['kept_schematic'] else
            'mixed' if got['extracted'] else 'schematic')
    return {'view': view, 'sources': [Path(got['path'])] if got['extracted'] else [],
            'magicrc_source': rc[1], 'gds': gds, **got}


# --- waveform and measurement --------------------------------------------

def read_waveform(path: Path, simulator: str) -> Dict[str, List[Tuple[float, float]]]:
    """{node: [(t, v)]} from the simulator's table: ngspice `wrdata` with
    `wr_vecnames`/`wr_singlescale`, or Xyce's `.print` table (`.prn`, or the
    `format=csv` file the tool's deck names). Node names are keyed lowercase
    with `v(...)` and escapes removed."""
    raw = path.read_text(errors='replace').splitlines()
    split = (lambda line: line.split(',')) if raw and ',' in raw[0] else str.split  # noqa: E731
    lines = [[w.strip() for w in split(line)] for line in raw if line.split()]
    lines = [[w for w in line if w] for line in lines]
    head = lines[0]
    rows = [r for r in lines[1:] if len(r) == len(head)]
    key = lambda n: re.sub(r'^v\((.*)\)$', r'\1', n.lower()).replace('\\', '')  # noqa: E731
    t = next(i for i, n in enumerate(head) if n.lower() == 'time')
    skip = {t} | ({head.index('Index')} if simulator == 'xyce' and 'Index' in head else set())
    return {key(name): [(float(r[t]), float(r[i])) for r in rows]
            for i, name in enumerate(head) if i not in skip}


def crossings(wave: List[Tuple[float, float]], level: float, edge: str) -> List[float]:
    """Linear-interpolated times `wave` crosses `level` rising or falling."""
    out = []
    for (t0, v0), (t1, v1) in zip(wave, wave[1:]):
        if edge == 'rise' and v0 < level <= v1 or edge == 'fall' and v0 > level >= v1:
            out.append(t0 + (level - v0) * (t1 - t0) / (v1 - v0) if v1 != v0 else t1)
    return out


#: A `report_checks -fields {slew cap input_pins} -format full` data row:
#: `[cap] slew delay time <v|^> <pin> (<cell>)`. An input-pin row prints no
#: cap (three numbers), a driver row prints it (four).
_PATH_ROW_RE = re.compile(
    r"^\s*(?:(-?[\d.]+)\s+)?(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+([v^])\s+(\S+)\s+\(([^)]+)\)\s*$")


def parse_path_report(text: str) -> Optional[Dict[str, Any]]:
    """One path of `report_checks -format full` with `slew cap input_pins`:
    the data rows up to `data arrival time`, and the start and end the STA
    measures between (the start pin's row -- a launching flop's clock pin or
    an input port -- to the endpoint's)."""
    start = re.search(r"^Startpoint:\s+(\S+)", text, re.M)
    end = re.search(r"^Endpoint:\s+(\S+)", text, re.M)
    body = text.split('data arrival time')[0]
    rows = []
    for line in body.splitlines():
        m = _PATH_ROW_RE.match(line)
        if not m:
            continue
        cap, slew, incr, time_, tr, pin, cell = m.groups()
        rows.append({'cap_pf': float(cap) if cap is not None else None,
                     'slew_ns': float(slew), 'incr': float(incr), 'time': float(time_),
                     'tr': tr, 'pin': pin, 'cell': cell,
                     'inst': pin.rsplit('/', 1)[0] if '/' in pin else pin})
    if not (start and end and rows):
        return None
    first = next((r for r in rows if r['inst'] == start.group(1) or r['cell'] == 'in'), rows[0])
    if _scc.is_sequential_cell(first['cell']):
        # A launching register: the comparison is its combinational cone,
        # from its output pin, as the direct gate's is -- the flop's own
        # clock-to-output arc has no cell_rise/fall table on the related pin
        # `derive_liberty_path_tolerance` reads first.
        first = next((r for r in rows[rows.index(first) + 1:]
                      if r['inst'] == first['inst']), first)
    return {'startpoint': start.group(1), 'endpoint': end.group(1),
            'start_row': first, 'rows': rows[rows.index(first):],
            'start_time_ns': first['time'], 'end_time_ns': rows[-1]['time'],
            'path_delay_ns': round(rows[-1]['time'] - first['time'], 9),
            'endpoint_transition': 'fall' if rows[-1]['tr'] == 'v' else 'rise'}


def path_stages(rows: List[dict]) -> List[dict]:
    """Stages for the tolerance: each cell's input-pin row then output-pin row."""
    stages = []
    for prev, row in zip(rows, rows[1:]):
        if prev['inst'] == row['inst'] and '/' in prev['pin'] and '/' in row['pin'] \
                and row['cell'] not in ('in', 'out') and not _scc.is_sequential_cell(row['cell']):
            stages.append({'inst': row['inst'], 'cell': row['cell'],
                           'toggle_pin': prev['pin'].rsplit('/', 1)[1],
                           'transition': 'fall' if row['tr'] == 'v' else 'rise',
                           'input_slew_ns': prev.get('slew_ns'),
                           'sta_load_pf': row.get('cap_pf'), 'sta_delay_ns': row['incr']})
    return stages


def measure(deck: Path, simulator: str, sta: dict, header: dict) -> Dict[str, Any]:
    """SPICE delay from the STA path's start pin to its end pin, same edges."""
    text = deck.read_text(errors='replace')
    supplies = [float(v) for v in re.findall(r'^v\d+\s+\S+\s+0\s+([-\d.eE+]+)\s*$', text, re.M)]
    vdd = max(supplies) if supplies else None
    wave_path = xyce_table(deck) if simulator == 'xyce' else deck.with_suffix('.wave')
    if vdd is None or not wave_path.is_file():
        return {'status': 'NOT_MEASURED', 'reason': 'no waveform or no supply in the deck'}
    wave = read_waveform(wave_path, simulator)
    start = sta.get('start_row') or sta['rows'][0]
    end = sta['rows'][-1]
    a = wave.get(start['pin'].lower().replace('\\', ''))
    b = wave.get(end['pin'].lower().replace('\\', ''))
    if not a or not b:
        return {'status': 'NOT_MEASURED', 'reason': f'no waveform for {start["pin"]} / {end["pin"]}'}
    s_edge = 'fall' if start['tr'] == 'v' else 'rise'
    e_edge = sta['endpoint_transition']
    s_level = vdd * header[f'input_threshold_{s_edge}'] / 100.0
    e_level = vdd * header[f'output_threshold_{e_edge}'] / 100.0
    starts = crossings(a, s_level, s_edge)
    for t_end in crossings(b, e_level, e_edge):
        before = [t for t in starts if t <= t_end]
        if before:
            return {'status': 'MEASURED', 'spice_ns': (t_end - before[-1]) * 1e9,
                    'start_pin': start['pin'], 'end_pin': end['pin'],
                    'edges': [s_edge, e_edge], 'vdd': vdd}
    return {'status': 'NOT_MEASURED', 'reason': 'the end pin never made the STA transition '
                                                 'after the start pin did'}


def transient_step_s(deck: Path, rows: List[dict]) -> Tuple[float, float]:
    """(step the run uses, step the tool's deck asked for), in seconds.

    The tool asks for its print step as the TMAX of a many-cycle window
    (MEASURED on spm: `.tran 1e-13 7.44e-08`, 744k forced points, > 11 min per
    register-to-register path on a loaded host). The run uses a twentieth of
    the fastest slew the STA path reports -- the resolution a threshold
    crossing needs -- and never less than the tool's own step. The adaptive
    integrator still refines every edge; both steps are recorded."""
    m = re.search(r'^\.tran\s+(\S+)\s+(\S+)', deck.read_text(errors='replace'), re.M | re.I)
    asked = float(m.group(1)) if m else 0.0
    slews = [r['slew_ns'] for r in rows if (r.get('slew_ns') or 0) > 0]
    derived = min(slews) * 1e-9 / 20.0 if slews else asked
    return max(asked, derived), asked


def xyce_table(deck: Path) -> Path:
    """The file Xyce writes the deck's `.print tran` to: the `file=` the
    tool's deck names (`format=csv`), else Xyce's default `<netlist>.prn`."""
    m = re.search(r'^\.print\s+tran\b[^\n]*?\bfile=(\S+)', deck.read_text(errors='replace'),
                  re.M | re.I)
    return Path(m.group(1)) if m else deck.with_suffix('.run.sp.prn')


def runnable_subckts(text: str, deck_dir: Path) -> str:
    """`text` with each `.include`d `.subckt` file the tool wrote replaced by a
    copy whose device lines start at column 0. ``fold_subckts`` indents the
    parameterised device calls so OpenSTA's subckt READER skips them; that
    indentation is for the reader only. Xyce reads a line that starts with
    white space as a continuation of the line before (MEASURED on spm: "Unrecognized
    fields for device D37", the pad diode followed by an indented X line), so
    both simulators read the same de-indented bytes."""
    def swap(m):
        source = deck_dir / m.group(2)
        if not source.is_file():
            return m.group(0)
        target = source.with_suffix('.run.subckt')
        target.write_text(re.sub(r'^[ \t]+(?=[A-Za-z])', '',
                                 source.read_text(errors='replace'), flags=re.M))
        return f'{m.group(1)}"{target.name}"'
    return re.sub(r'^(\.include\s+)"([^"/]+\.subckt)"', swap, text, flags=re.M | re.I)


def _simulate(deck: Path, simulator: str, image: str, project: Path,
              mounts: List[Tuple[Path, str]], step_s: Optional[float] = None
              ) -> subprocess.CompletedProcess:
    text = runnable_subckts(deck.read_text(), deck.parent)
    stop = re.search(r'^\.tran\s+\S+\s+(\S+)', text, re.M | re.I).group(1)
    runnable = deck.with_suffix('.run.sp')
    if simulator == 'ngspice':
        nodes = re.search(r'^\.print tran (.*)$', text, re.M).group(1).split()
        names = [n[2:-1] for n in nodes if n.lower().startswith('v(')]
        # ngspice keeps node names lowercase and its control language reads a
        # backslash as an escape; the header names each column (MEASURED:
        # `v(u_core\/a/CLK)` in a control line is "no such vector").
        vectors = ' '.join('v(' + n.lower().replace('\\', '\\\\') + ')' for n in names)
        run = f'tran {step_s:.6g} {stop}' if step_s else 'run'
        control = (f'.control\nset wr_vecnames\nset wr_singlescale\n{run}\n'
                   f'wrdata {deck.with_suffix(".wave")} {vectors}\nquit\n.endc\n')
        # `quit`: in batch mode ngspice otherwise runs the deck's own `.tran`
        # card after the control block (MEASURED on spm: the 1e-13 card took
        # 30-57 min after the control-block transient had finished in ~1 min).
        # A function, not a string: a replacement string would read the
        # doubled backslashes above as escapes and undo them.
        runnable.write_text(re.sub(r'^\.end\s*$', lambda _m: control + '.end', text,
                                   flags=re.M))
        return _docker(image, project, mounts, ['ngspice', '-b', str(runnable)], deck.parent)
    if step_s:
        text = re.sub(r'^\.tran\s+\S+', f'.tran {step_s:.6g}', text, count=1, flags=re.M | re.I)
    runnable.write_text(text)
    return _docker(image, project, mounts, ['Xyce', str(runnable)], deck.parent)


def mutate_spef(source: Path, factor: float, out: Path) -> Dict[str, Any]:
    """Every `*CAP` value and every `*D_NET` total scaled by `factor`."""
    lines = []
    section = None
    scaled = 0
    for raw in Path(source).read_text(errors='replace').splitlines():
        words = raw.split()
        head = words[0] if words else ''
        if head == '*D_NET' and len(words) >= 3:
            section = 'net'
            words[2] = f'{float(words[2]) * factor:.6g}'
            raw = ' '.join(words)
        elif head == '*CAP':
            section = 'cap'
        elif head.startswith('*') and head not in ('*CAP',):
            section = None if head == '*END' else ('net' if head in ('*CONN', '*RES') else section)
        elif section == 'cap' and head.isdigit() and len(words) in (3, 4):
            words[-1] = f'{float(words[-1]) * factor:.6g}'
            raw = ' '.join(words)
            scaled += 1
        lines.append(raw)
    out.write_text('\n'.join(lines) + '\n')
    return {'path': str(out), 'factor': factor, 'cap_rows_scaled': scaled,
            'source_sha256': digest(Path(source))}


def run_arm(project: Path, image: str, state_path: Path, corner: str, *,
            pdk_root: Path, pdk: str, out_dir: Path, simulator: str = 'ngspice',
            paths: int = 3, path_args: Optional[List[str]] = None,
            spef: Optional[Path] = None) -> Dict[str, Any]:
    """Top-N setup paths of `corner` through the tool's deck and `simulator`."""
    return simulate_arm(prepare_arm(project, image, state_path, corner, pdk_root=pdk_root,
                                    pdk=pdk, out_dir=out_dir, simulator=simulator,
                                    paths=paths, path_args=path_args, spef=spef))


def prepare_arm(project: Path, image: str, state_path: Path, corner: str, *,
                pdk_root: Path, pdk: str, out_dir: Path, simulator: str = 'ngspice',
                paths: int = 3, path_args: Optional[List[str]] = None,
                spef: Optional[Path] = None) -> Dict[str, Any]:
    """The STA session: each path's report and the tool's deck for it."""
    inputs = post_pnr_timing_inputs(project, state_path, corner)
    config = _load(state_path.parent / 'config.json')
    top, power, ground = config.get('DESIGN_NAME'), config.get('VDD_PIN'), config.get('GND_PIN')
    if not (top and power and ground):
        raise Refusal('LL_STA_CONFIG_UNREAD', str(state_path.parent / 'config.json'))
    guest = f'/pdk/{pdk}'
    host = lambda p: Path(str(p).replace(guest, str(pdk_root / pdk), 1))  # noqa: E731
    lib_texts = [host(p).read_text(errors='replace') for p in inputs['liberties']]
    header = _scc.parse_liberty_header(lib_texts[0])
    opc, libname = _OPCOND_RE.search(lib_texts[0]), _LIBRARY_RE.search(lib_texts[0])
    if not (opc and libname):
        raise Refusal('LL_LIBERTY_OPCOND_UNDECLARED', inputs['liberties'][0])
    out_dir.mkdir(parents=True, exist_ok=True)
    cell_sources = [host(p) for p in (config.get('CELL_SPICE_MODELS') or [])]
    spice_sources = cell_sources + [host(p) for p in (config.get('PAD_SPICE_MODELS') or [])]
    mounts = [(pdk_root / pdk, guest)]
    view = cell_view(image, project, mounts, config=config, pdk_dir=pdk_root / pdk,
                     guest_root=str(Path(guest).parent), pdk=pdk,
                     netlist=project / inputs['sta_netlist'], cell_sources=cell_sources,
                     out_dir=out_dir)
    cells = fold_subckts(view['sources'] + spice_sources, supply_voltages(lib_texts), power,
                         ground, out_dir / 'cells_folded.spice')
    cells['cell_view'] = {k: v for k, v in view.items() if k != 'sources'}
    models = model_file(pdk_root / pdk / 'libs.tech' / simulator, f'{guest}/libs.tech/{simulator}',
                        simulator, Path(inputs['liberties'][0]).name,
                        device_names(Path(cells['path']).read_text()),
                        header.get('nom_temperature'), out_dir / f'models_{simulator}.sp')
    # `-group_path_count N` writes N decks (`path_1.sp_<n>.sp`); the STA side
    # is one `report_checks` with the same arguments, split per path.
    args = path_args or [f'{REG_TO_REG} -group_path_count {paths}']
    spef_path = spef or (project / inputs['spef'])
    tcl = out_dir / 'arm.tcl'
    tcl.write_text(arm_tcl(inputs, project, top, str(spef_path), (libname.group(1), opc.group(1)),
                           args, out_dir, cells['path'], models['path'], power, ground, simulator))
    sta = _docker(image, project, mounts, ['sta', '-no_init', '-no_splash', '-exit', str(tcl)], out_dir)
    (out_dir / 'sta.log').write_text(sta.stdout + '\n' + sta.stderr)
    failed = re.findall(r'^VIBEIC_WPS_FAIL (\d+) (.*)$', sta.stdout, re.M)
    # Report `path_<i>.rpt` block <n> is the path `write_path_spice` wrote to
    # `path_<i>.sp_<n>.sp` (the same `-path_args`, the same order).
    prepared = []
    for i in range(1, len(args) + 1):
        rpt = out_dir / f'path_{i}.rpt'
        blocks = (['Startpoint: ' + b for b in rpt.read_text(errors='replace')
                   .split('Startpoint: ')[1:]] if rpt.is_file() else [])
        for n, block in enumerate(blocks, 1):
            deck = out_dir / f'path_{i}.sp_{n}.sp'
            prepared.append({'path': len(prepared) + 1, 'parsed': parse_path_report(block),
                             'deck': deck if deck.is_file() else None,
                             'wps_fail': [f for k, f in failed if int(k) == i]})
    return {'corner': corner, 'simulator': simulator, 'inputs': inputs,
            'spef': str(spef_path), 'spef_sha256': digest(spef_path), 'cells': cells,
            'models': models, 'sta_rc': sta.returncode, 'prepared': prepared,
            'image': image, 'project': project, 'mounts': mounts, 'header': header,
            'liberty_text': '\n'.join(lib_texts)}


def simulate_arm(arm: Dict[str, Any], *, workers: int = 8) -> Dict[str, Any]:
    """Every prepared deck through the simulator (concurrently), measured
    and compared with its STA path."""
    from concurrent.futures import ThreadPoolExecutor
    simulator, header = arm['simulator'], arm['header']

    def one(item: Dict[str, Any]) -> Dict[str, Any]:
        parsed, deck = item['parsed'], item['deck']
        row: Dict[str, Any] = {'path': item['path'], 'deck': str(deck) if deck else None}
        if parsed is None or deck is None or deck.stat().st_size == 0:
            row.update(status='NOT_MEASURED', reason='no STA path or no deck',
                       wps_fail=item['wps_fail'])
            return row
        step, asked = transient_step_s(deck, parsed['rows'])
        sim = _simulate(deck, simulator, arm['image'], arm['project'], arm['mounts'],
                        step_s=step)
        deck.with_suffix('.log').write_text(sim.stdout + '\n' + sim.stderr)
        got = measure(deck, simulator, parsed, header)
        row.update(tran_step_ns=step * 1e9, deck_tran_step_ns=asked * 1e9)
        sta_ns = parsed['path_delay_ns']
        stages = path_stages(parsed['rows'])
        tol = (_scc.derive_liberty_path_tolerance(arm['liberty_text'], stages, sta_ns)
               if stages else None)
        row.update(startpoint=parsed['startpoint'], endpoint=parsed['endpoint'],
                   sta_ns=sta_ns, stages=len(stages), sim_rc=sim.returncode, **got)
        if got.get('status') == 'MEASURED':
            err = (got['spice_ns'] - sta_ns) / sta_ns * 100.0 if sta_ns else None
            row.update(error_pct=err, abs_error_ns=abs(got['spice_ns'] - sta_ns),
                       tolerance_pct=tol['tolerance_pct'] if tol else None,
                       verdict=(_scc.path_correlation_verdict(err, tol['tolerance_pct'])
                                if tol and err is not None else 'TOLERANCE_UNDERIVABLE'))
        return row

    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(one, arm['prepared']))
    return {k: v for k, v in arm.items()
            if k not in ('prepared', 'image', 'project', 'mounts', 'header', 'liberty_text')
            } | {'paths': rows}


def judge(arm: Dict[str, Any], mutated: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """CORRELATED only when every measured path is within its derived
    tolerance AND responds to the SPEF mutation."""
    rows = [r for r in arm['paths'] if r.get('status') == 'MEASURED']
    if not rows:
        return {'verdict': 'NOT_MEASURED', 'reason': 'no path was simulated'}
    response = []
    for row in rows:
        other = next((m for m in (mutated or {}).get('paths') or []
                      if m.get('startpoint') == row['startpoint']
                      and m.get('endpoint') == row['endpoint']
                      and m.get('status') == 'MEASURED'), None)
        if other is None:
            response.append({'path': row['path'], 'responds': None})
            continue
        step = row.get('tran_step_ns') or _tran_step_ns(row['deck'])
        delta = other['spice_ns'] - row['spice_ns']
        response.append({'path': row['path'], 'spice_delta_ns': delta,
                         'sta_delta_ns': other['sta_ns'] - row['sta_ns'],
                         'resolution_ns': 2 * step,
                         'responds': abs(delta) > 2 * step and delta > 0})
    unmeasured = [r for r in response if r['responds'] is None]
    unresponsive = [r for r in response if r['responds'] is False]
    worst = max(rows, key=lambda r: abs(r.get('error_pct') or 0.0))
    verdicts = {r.get('verdict') for r in rows}
    verdict = ('SPEF_UNRESPONSIVE' if unresponsive else
               'MUTATION_NOT_MEASURED' if unmeasured else
               'CORRELATED' if verdicts == {'CORRELATED'} else
               'CRITICAL_MISMATCH' if 'CRITICAL_MISMATCH' in verdicts else
               'MISMATCH' if 'MISMATCH' in verdicts else 'TOLERANCE_UNDERIVABLE')
    return {'verdict': verdict, 'worst_path': worst['path'],
            'worst_error_pct': worst.get('error_pct'), 'mutation': response}


def _tran_step_ns(deck: Any) -> float:
    m = re.search(r'^\.tran\s+(\S+)', Path(deck).read_text(errors='replace'), re.M | re.I)
    return float(m.group(1)) * 1e9 if m else 0.0


def run_step30(project: Path, image: str, pdk_root: Path, pdk: str, *,
               corner: Optional[str] = None, paths: int = 3,
               simulators: Sequence[str] = SIMULATORS) -> Dict[str, Any]:
    """Step 30's tool arm on the STAPostPNR state step 23 recorded: each
    simulator on the base SPEF and on the mutated one, judged, and written to
    `reports/phase3/spice_path_tool.json`. The corner is the one step 23 named
    worst for setup unless one is given. A simulator that cannot run this PDK
    is recorded by its refusal; nothing falls back."""
    import librelane_postroute as _lp
    folder, _state = _lp.stapostpnr_state(project)
    record = _load(project / _lp.STEP23_RECORD)
    corner = corner or ((record.get('judgment') or {}).get('worst_setup') or {}).get('corner')
    if not corner:
        raise Refusal('LL_STEP30_CORNER_UNDECLARED', 'step 23 named no worst setup corner')
    state_path = folder / 'state_out.json'
    root = project / 'phase3/tool_arms/30'
    arms: Dict[str, Any] = {}
    for simulator in simulators:
        try:
            base = prepare_arm(project, image, state_path, corner, pdk_root=pdk_root, pdk=pdk,
                               out_dir=root / simulator / 'base', simulator=simulator,
                               paths=paths)
            mutated_spef = mutate_spef(Path(base['spef']), MUTATION_FACTOR,
                                       root / simulator / 'mutated.spef')
            # The mutation re-times the SAME arc: start and end pin AND their
            # transitions. MEASURED on spm: `-from Q -to D` alone picked the
            # opposite edge after the mutation, and the tool's deck (no initial
            # state) cannot launch a falling Q from a flop that powers up at 0.
            edge = lambda row: 'rise' if row['tr'] == '^' else 'fall'  # noqa: E731
            pins = [f"-path_delay max -{edge(item['parsed']['start_row'])}_from "
                    f"{item['parsed']['start_row']['pin']} -{edge(item['parsed']['rows'][-1])}_to "
                    f"{item['parsed']['rows'][-1]['pin']}"
                    for item in base['prepared'] if item['parsed'] and item['deck']]
            mutated = (prepare_arm(project, image, state_path, corner, pdk_root=pdk_root,
                                   pdk=pdk, out_dir=root / simulator / 'mutated',
                                   simulator=simulator, path_args=pins,
                                   spef=Path(mutated_spef['path'])) if pins else None)
            # Every deck of both arms simulates at once; they are independent.
            base['prepared'] = [dict(i, arm='base') for i in base['prepared']]
            both = dict(base, prepared=base['prepared']
                        + [dict(i, arm='mutated') for i in (mutated or {}).get('prepared', [])])
            rows = simulate_arm(both)['paths']
            done_base = {k: v for k, v in simulate_arm(dict(base, prepared=[]))
                         .items()} | {'paths': rows[:len(base['prepared'])]}
            done_mut = (None if mutated is None else
                        {k: v for k, v in simulate_arm(dict(mutated, prepared=[])).items()}
                        | {'paths': rows[len(base['prepared']):]})
            # The cell view is part of the verdict: a schematic-view number
            # carries the PDK's missing layout parasitics (module docstring).
            arms[simulator] = {'judgment': dict(judge(done_base, done_mut),
                                                cell_view=base['cells']['cell_view']['view']),
                               'base': done_base,
                               'mutated': done_mut, 'mutation': mutated_spef}
        except Refusal as exc:
            arms[simulator] = {'judgment': {'verdict': 'NOT_MEASURED', 'reason': str(exc)}}
    measured = {s: a for s, a in arms.items() if a['judgment']['verdict'] != 'NOT_MEASURED'}
    better = [s for s, a in measured.items() if a['judgment']['verdict'] == 'CORRELATED']
    document = {'step': '30', 'corner': corner, 'sta_state': str(state_path),
                'sta_state_sha256': digest(state_path),
                'deck': 'OpenSTA write_path_spice (SPEF-annotated)',
                'arms': {s: a['judgment'] for s, a in arms.items()},
                'selection': (better[0] if len(better) == 1 else
                              'UNDETERMINED' if len(better) > 1 else None),
                'rule': ('an arm is better when |SPICE-STA| is within the Liberty-grid '
                         'tolerance AND the result responds to the SPEF mutation'),
                'detail': {s: {k: v for k, v in a.items() if k != 'judgment'}
                           for s, a in arms.items()}}
    write_json(project / 'reports/phase3/spice_path_tool.json', document)
    return document
