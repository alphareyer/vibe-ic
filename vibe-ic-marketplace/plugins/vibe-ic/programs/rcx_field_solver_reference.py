#!/usr/bin/env python3
"""Step 22 accuracy arm: a field-solver reference C for sampled critical nets.

The step-22 dual (direct OpenRCX vs LibreLane OpenROAD.RCX) and the choice of
an OpenRCX ruleset can only be judged against something that is not an
OpenRCX rules table.  This program produces that reference: klayout-pex
(KPEX) with its FasterCap engine, on the SAME routed geometry OpenRCX
extracts -- the routed DEF's NET/SPECIALNET wires and vias, no cell geometry --
one clip per sampled net (the net's whole route plus every routed shape within
`halo_um` of it, clipped).  Defaults measured on spm x gf180mcuD: a 2 um halo
under-read one net by 38 % against 5 um (median -0.9 %), and KPEX's default
FasterCap tolerance (0.05) put one net 17 % off the 0.01 answer, which no
halo moved -- so 5 um and 0.01, both recorded in the reference.  FasterCap solves the clip's Maxwell capacitance
matrix; the net's total capacitance is that matrix's diagonal entry (charge on
the net at 1 V with every other conductor, the substrate included, at 0 V),
which is the quantity a SPEF `*D_NET <net> <total>` states.

Every input is declared by the caller (§4.05):
  * the routed DEF and the LEFs it needs (the tech LEF names the layer stack);
  * the PDK's own KLayout LEF/DEF layer map (GDS numbers KPEX's tech expects);
  * the KPEX PDK name (KPEX ships its own process stack per PDK) -- a PDK KPEX
    does not know is refused by KPEX itself, never mapped to a neighbour;
  * the nets and the halo.
The image is resolved at run time (`librelane_contract.resolve_image`), never
stored.

`reference.json` records per net `pF`, and per failed net the reason; a net
the solver did not finish is absent, never 0.  `librelane_signoff.
accuracy_selection` consumes it as the step-22 dual selection criterion.

chip-AGNOSTIC: no design, PDK or layer literal selects a branch.

USAGE
    rcx_field_solver_reference.py --def D --lef L [--lef L ...] --layer-map M
        --kpex-pdk P --nets NETS.txt --out DIR [--halo-um 5.0] [--tolerance 0.01] [--jobs 8]
        [--threads 4] [--mount HOST:GUEST ...] [--image REF]
    Paths are as seen inside the image; `--mount` makes host paths visible.
    exit 0 = reference written (see its `failed`), 2 = refused.
"""
from __future__ import annotations

import argparse
import csv
import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _atomic_artefact import write_json, write_text  # noqa: E402
import _docker_memory as _dmem  # noqa: E402 — every `docker run` carries the ceiling
from librelane_contract import Refusal, digest  # noqa: E402

#: The cell every clip is written under, and the label its victim carries.
CLIP_CELL = 'VICTIM_CLIP'
VICTIM = 'VICTIM'
#: The file KPEX writes the averaged Maxwell matrix to (farads, `;`-separated,
#: header `g<i>_<net>`), relative to its per-cell run directory.
MATRIX = f'{CLIP_CELL}_FasterCap_Result_Matrix_Avg.csv'


def routing_stack(tech_lef_text: str) -> list[tuple[str, str]]:
    """[(layer, 'ROUTING'|'CUT'), ...] bottom-up from the first ROUTING layer,
    in the tech LEF's own order (a CUT below the first routing layer, i.e. the
    device contact, is not routing geometry)."""
    layers: list[tuple[str, str]] = []
    name = None
    for raw in tech_lef_text.splitlines():
        words = raw.split()
        if len(words) >= 2 and words[0] == 'LAYER':
            name = words[1]
        elif len(words) >= 2 and words[0] == 'TYPE' and name is not None:
            kind = words[1].rstrip(';')
            if kind in ('ROUTING', 'CUT'):
                layers.append((name, kind))
            name = None
        elif words[:1] == ['END'] and len(words) >= 2 and words[1] == name:
            name = None
    while layers and layers[0][1] != 'ROUTING':
        layers.pop(0)
    while layers and layers[-1][1] != 'ROUTING':
        layers.pop()
    return layers


def routing_map(pdk_map_text: str, stack: list[tuple[str, str]]) -> dict:
    """From the PDK's KLayout LEF/DEF map: a routing-only map (NET/SPNET/VIA
    purposes, no PIN/OBS/LEF geometry), the GDS pair per stack layer, and the
    label pair per routing layer.  An unmapped stack layer refuses."""
    drawn: dict[str, tuple[int, int]] = {}
    label: dict[str, tuple[int, int]] = {}
    for raw in pdk_map_text.splitlines():
        words = raw.split('#', 1)[0].split()
        if len(words) != 4 or not (words[2].isdigit() and words[3].isdigit()):
            continue
        name, purposes, pair = words[0], words[1].split(','), (int(words[2]), int(words[3]))
        if name == 'NAME' and purposes[0].endswith('/LABEL'):
            label.setdefault(purposes[0].split('/', 1)[0], pair)
        elif {'NET', 'VIA'} & set(purposes):
            drawn.setdefault(name, pair)
    missing = [n for n, _k in stack if n not in drawn]
    if missing:
        raise Refusal('RCX_REF_LAYER_UNMAPPED',
                      f'the layer map declares no NET/VIA GDS pair for {missing}')
    lines = []
    for name, kind in stack:
        l, d = drawn[name]
        lines.append(f"{name} {'NET,SPNET,VIA' if kind == 'ROUTING' else 'VIA'} {l} {d}")
    return {'map': '\n'.join(lines) + '\n',
            'stack': [[name, kind, list(drawn[name])] for name, kind in stack],
            'labels': {name: list(label[name]) for name, kind in stack
                       if kind == 'ROUTING' and name in label}}


#: Runs inside the image under KLayout's Python module.  Reads the routed DEF
#: through the routing-only map, derives connectivity (LayoutToNetlist over the
#: stack), names each cluster by its wires' NET property, and writes one clip
#: per requested net.  Config: argv[1] (JSON).
_CLIP_PY = r'''
import json, sys
import klayout.db as db
cfg = json.load(open(sys.argv[1]))
opt = db.LoadLayoutOptions()
c = opt.lefdef_config
c.dbu = cfg["dbu"]
c.map_file = cfg["map"]
c.read_lef_with_def = False
c.lef_files = cfg["lefs"]
c.net_property_name = "NET"
for k in ("produce_cell_outlines", "produce_blockages", "produce_obstructions",
          "produce_pins", "produce_lef_pins", "produce_labels", "produce_lef_labels",
          "produce_placement_blockages", "produce_regions"):
    setattr(c, k, False)
ly = db.Layout(); ly.dbu = cfg["dbu"]
ly.read(cfg["def"], opt)
top = ly.top_cell(); top.flatten(-1, True)
stack = [(n, k, tuple(p)) for n, k, p in cfg["stack"]]
li = {n: ly.layer(*p) for n, _k, p in stack}
l2n = db.LayoutToNetlist(db.RecursiveShapeIterator(ly, top, []))
R = {n: l2n.make_layer(li[n], n) for n, _k, _p in stack}
for n, _k, _p in stack:
    l2n.connect(R[n])
for (a, _ka, _pa), (b, _kb, _pb) in zip(stack, stack[1:]):
    l2n.connect(R[a], R[b])
l2n.extract_netlist()
circ = l2n.netlist().circuit_by_name(top.name)
metals = [n for n, k, _p in stack if k == "ROUTING"]
named = {}
for n in metals:
    for sh in top.shapes(li[n]).each():
        name = sh.property("NET")
        if name is None:
            continue
        pt = sh.bbox().center()
        net = l2n.probe_net(R[n], db.DPoint(pt.x * ly.dbu, pt.y * ly.dbu))
        if net is not None:
            named.setdefault(net.cluster_id, set()).add(name)
by_name = {}
for net in circ.each_net():
    for name in named.get(net.cluster_id, ()):
        by_name.setdefault(name, []).append(net)
full = {n: db.Region(top.begin_shapes_rec(li[n])) for n, _k, _p in stack}
index = []
for i, name in enumerate(cfg["nets"]):
    clusters = by_name.get(name, [])
    if len(clusters) != 1:
        index.append({"i": i, "net": name, "skip": f"{len(clusters)} routed clusters"}); continue
    vic = {n: l2n.shapes_of_net(clusters[0], R[n], True) for n, _k, _p in stack}
    foot = db.Region()
    for n in metals:
        foot += vic[n]
    if foot.is_empty():
        index.append({"i": i, "net": name, "skip": "no routed metal"}); continue
    win = foot.sized(int(round(cfg["halo_um"] / ly.dbu))).merged()
    out = db.Layout(); out.dbu = ly.dbu; cell = out.create_cell(cfg["cell"])
    for n, _k, p in stack:
        L = out.layer(*p)
        cell.shapes(L).insert(vic[n].merged())
        cell.shapes(L).insert(((full[n] & win) - vic[n].sized(1)).merged())
    for n in metals:
        if not vic[n].is_empty() and n in cfg["labels"]:
            b = next(vic[n].merged().each()).bbox().center()
            cell.shapes(out.layer(*cfg["labels"][n])).insert(db.Text(cfg["victim"], db.Trans(b)))
    path = f'{cfg["out"]}/clips/{i:04d}.gds'
    out.write(path)
    index.append({"i": i, "net": name, "gds": path})
json.dump(index, open(f'{cfg["out"]}/clips/index.json', "w"), indent=1)
'''


def victim_c_pf(matrix_csv: Path) -> float:
    """The victim's total capacitance, pF: the Maxwell matrix diagonal entry
    of the conductor KPEX named after the victim's label."""
    rows = list(csv.reader(Path(matrix_csv).open(), delimiter=';'))
    if len(rows) < 2:
        raise Refusal('RCX_REF_MATRIX_EMPTY', str(matrix_csv))
    names = [h.split('_', 1)[1] if '_' in h else h for h in rows[0]]
    if names.count(VICTIM) != 1:
        raise Refusal('RCX_REF_VICTIM_NOT_ONE_CONDUCTOR',
                      f'{matrix_csv}: {names.count(VICTIM)} conductors named {VICTIM}')
    k = names.index(VICTIM)
    if len(rows) <= 1 + k or len(rows[1 + k]) != len(names):
        raise Refusal('RCX_REF_MATRIX_SHAPE', f'{matrix_csv}: row {k} is not {len(names)} wide')
    value = float(rows[1 + k][k]) * 1e12
    if not value > 0:
        raise Refusal('RCX_REF_MATRIX_NONPOSITIVE', f'{matrix_csv}: C({VICTIM}) = {value} pF')
    return value


def collect(out: Path) -> dict:
    """`{net: pF}` and `{net: reason}` from a finished run directory."""
    index = json.loads((out / 'clips/index.json').read_text())
    nets: dict[str, float] = {}
    failed: dict[str, str] = {}
    for row in index:
        if 'skip' in row:
            failed[row['net']] = row['skip']
            continue
        run = out / 'kpex' / f"{row['i']:04d}"
        rc = (run / 'rc').read_text().strip() if (run / 'rc').is_file() else None
        found = sorted(run.glob(f'*/{MATRIX}'))
        if rc != '0' or len(found) != 1:
            failed[row['net']] = f'kpex rc={rc}, {len(found)} matrix file(s)'
            continue
        try:
            nets[row['net']] = victim_c_pf(found[0])
        except Refusal as exc:
            failed[row['net']] = str(exc)
    return {'nets': nets, 'failed': failed}


def measure(def_path: str, lefs: list[str], layer_map: str, kpex_pdk: str,
            nets: list[str], out: Path, *, image: str, tech_lef_text: str,
            pdk_map_text: str, halo_um: float = 5.0, tolerance: float = 0.01,
            jobs: int = 8, threads: int = 4,
            mounts: Optional[list[tuple[str, str]]] = None, dbu: float = 0.0005,
            docker: str = 'docker') -> dict:
    """Clip, solve and collect; writes `<out>/reference.json`."""
    if not nets:
        raise Refusal('RCX_REF_NO_NETS', 'no sampled nets declared')
    stack = routing_stack(tech_lef_text)
    if not stack:
        raise Refusal('RCX_REF_NO_ROUTING_LAYERS', 'the tech LEF declares no ROUTING layer')
    rmap = routing_map(pdk_map_text, stack)
    out = out.resolve()
    (out / 'clips').mkdir(parents=True, exist_ok=True)
    write_text(out / 'routing.map', rmap['map'])
    write_text(out / 'clip.py', _CLIP_PY)
    write_json(out / 'clip_config.json', {
        'def': def_path, 'lefs': lefs, 'map': str(out / 'routing.map'), 'dbu': dbu,
        'stack': rmap['stack'], 'labels': rmap['labels'], 'nets': nets,
        'halo_um': halo_um, 'cell': CLIP_CELL, 'victim': VICTIM, 'out': str(out)})
    solve = (f'cd {shlex.quote(str(out))} && python3 clip.py clip_config.json && '
             f'mkdir -p kpex && python3 -c "import json;[print(\'%04d\' % r[\'i\']) '
             f'for r in json.load(open(\'clips/index.json\')) if \'gds\' in r]" | '
             # KPEX's LVS deck writes its netlist into the PARENT of the working
             # directory, so each solve runs one level below its own folder.
             f'xargs -P {int(jobs)} -I{{}} sh -c \'d=$PWD/kpex/{{}}; mkdir -p $d/home && '
             f'cd $d/home && HOME=$d/home kpex --pdk {shlex.quote(kpex_pdk)} '
             f'--gds {shlex.quote(str(out))}/clips/{{}}.gds '
             f'--cell {CLIP_CELL} --fastercap --mode CC --threads {int(threads)} '
             f'--tolerance {float(tolerance)} '
             f'--out_dir $d > $d/kpex.log 2>&1; echo $? > $d/rc\'; '
             f'kpex --version > kpex_version.txt 2>&1 || true')
    volumes = ['-v', f'{out}:{out}']
    for host, guest in mounts or []:
        volumes += ['-v', f'{Path(host).resolve()}:{guest}:ro']
    completed = subprocess.run([docker, 'run', '--rm', '--network', 'none',
                                *_dmem.docker_memory_flags(), *volumes,
                                '--entrypoint', 'bash', image, '-c', solve],
                               capture_output=True, text=True)
    write_text(out / 'solve.log', completed.stdout + '\n' + completed.stderr)
    if not (out / 'clips/index.json').is_file():
        raise Refusal('RCX_REF_CLIPS_NOT_WRITTEN', f'rc={completed.returncode}; see {out}/solve.log')
    got = collect(out)
    version = ((out / 'kpex_version.txt').read_text().strip().splitlines() or ['?'])[-1] \
        if (out / 'kpex_version.txt').is_file() else '?'
    reference = {
        'step': '22', 'kind': 'field_solver_reference',
        'producer': f'klayout-pex {version} FasterCap, mode CC',
        'kpex_pdk': kpex_pdk, 'image': image, 'rc_corner': 'nom',
        'geometry': ('routed DEF NET/SPECIALNET wires and vias only (the scope '
                     f'OpenRCX extracts), clip = route + {halo_um} um halo'),
        'halo_um': halo_um, 'fastercap_tolerance': tolerance,
        'def': def_path, 'layer_map': layer_map,
        'nets_requested': len(nets), 'nets': got['nets'], 'failed': got['failed']}
    write_json(out / 'reference.json', reference)
    return reference


def _mounted(path: str, mounts: list[tuple[str, str]]) -> Path:
    """The host path behind an in-image path, through the declared mounts."""
    for host, guest in mounts:
        if path == guest or path.startswith(guest.rstrip('/') + '/'):
            return Path(host) / path[len(guest):].lstrip('/')
    return Path(path)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    ap.add_argument('--def', dest='def_path', required=True)
    ap.add_argument('--lef', action='append', required=True)
    ap.add_argument('--layer-map', required=True)
    ap.add_argument('--kpex-pdk', required=True)
    ap.add_argument('--nets', required=True, type=Path)
    ap.add_argument('--out', required=True, type=Path)
    ap.add_argument('--halo-um', type=float, default=5.0)
    ap.add_argument('--tolerance', type=float, default=0.01)
    ap.add_argument('--jobs', type=int, default=8)
    ap.add_argument('--threads', type=int, default=4)
    ap.add_argument('--mount', action='append', default=[])
    ap.add_argument('--image')
    a = ap.parse_args(argv)
    try:
        mounts = [tuple(m.split(':', 1)) for m in a.mount]
        from librelane_contract import resolve_image
        image = a.image or resolve_image(None)
        tech = _mounted(a.lef[0], mounts)
        lmap = _mounted(a.layer_map, mounts)
        if not tech.is_file() or not lmap.is_file():
            raise Refusal('RCX_REF_INPUT_NOT_READABLE_ON_HOST',
                          f'{tech} / {lmap}: declare a --mount for them')
        nets = [n.strip() for n in a.nets.read_text().splitlines() if n.strip()]
        ref = measure(a.def_path, a.lef, a.layer_map, a.kpex_pdk, nets, a.out,
                      image=image, tech_lef_text=tech.read_text(errors='replace'),
                      pdk_map_text=lmap.read_text(errors='replace'), halo_um=a.halo_um,
                      tolerance=a.tolerance,
                      jobs=a.jobs, threads=a.threads, mounts=mounts)
    except Refusal as exc:
        print(exc, file=sys.stderr)
        return 2
    print(f"reference: {len(ref['nets'])} net(s), {len(ref['failed'])} failed -> "
          f"{a.out / 'reference.json'} (sha256 {digest(a.out / 'reference.json')})")
    return 0


if __name__ == '__main__':
    sys.exit(main())
