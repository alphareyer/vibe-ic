#!/usr/bin/env python3
"""Measure PDK-resolved die-density layers in a streamed GDS with KLayout.

The caller supplies the rule-to-GDS-pair map derived from the installed PDK.
Unknown pairs stay NOT_MEASURED; a zero violation count cannot invent a ratio.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _atomic_artefact import write_json


def measure(gds: Path, specs: dict, die: list[float]) -> dict:
    import klayout.db as k

    layout = k.Layout()
    layout.read(str(gds))
    top = layout.top_cell()
    if top is None:
        raise ValueError('GDS has no top cell')
    x1, y1, x2, y2 = [float(x) for x in die]
    area = (x2 - x1) * (y2 - y1)
    if area <= 0:
        raise ValueError('DIE_AREA has no positive area')
    dbu = layout.dbu
    die_box_db = k.Box(*[round(v / dbu) for v in (x1, y1, x2, y2)])
    # KLayout's foundry deck divides by extent, the streamed top-cell bbox.
    # Refuse a declared die that disagrees with that denominator.
    bbox = top.bbox()
    if any(abs(a - b) > 1 for a, b in zip(
            (bbox.left, bbox.bottom, bbox.right, bbox.top),
            (die_box_db.left, die_box_db.bottom,
             die_box_db.right, die_box_db.top))):
        raise ValueError('DIE_AREA differs from KLayout density extent')
    die_box = k.Region(die_box_db)
    rows = {}
    for rule, spec in specs.items():
        row = dict(spec)
        pairs = spec.get('layers')
        if not pairs:
            row['status'] = 'NOT_MEASURED'
            rows[rule] = row
            continue
        region = k.Region()
        for layer, datatype in {tuple(pair) for pair in pairs}:
            index = layout.find_layer(layer, datatype)
            if index is not None:
                region.insert(top.begin_shapes_rec(index))
        row['ratio'] = round((region & die_box).merged().area() * dbu * dbu / area, 9)
        row['status'] = 'MEASURED'
        rows[rule] = row
    return {'gds': str(gds), 'die_area_um2': area, 'die': die,
            'layers': rows, 'status': ('MEASURED' if rows and all(
                row['status'] == 'MEASURED' for row in rows.values())
                else 'NOT_MEASURED')}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--gds', required=True)
    parser.add_argument('--specs', required=True)
    parser.add_argument('--die', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    try:
        result = measure(Path(args.gds), json.loads(Path(args.specs).read_text()),
                         json.loads(args.die))
    except Exception as exc:
        result = {'status': 'NOT_MEASURED', 'reason': str(exc)}
    write_json(Path(args.out), result)
    print(json.dumps(result))
    return 0 if result['status'] == 'MEASURED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
