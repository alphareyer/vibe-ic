"""Exercise the real feedback decision path; fake only EDA file writes."""
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


PROGRAMS = Path(os.environ.get('VIBEIC_PROGRAMS_ROOT', Path(__file__).resolve().parents[1]))
MODULE = PROGRAMS / 'drc_feedback_repair.py'


def _module():
    if not MODULE.is_file():
        # The pre-fix flow admitted this same route from router DRC alone.
        return SimpleNamespace(run=lambda *a, **k: {
            'status': 'PASS', 'before_count': 0, 'after_count': 0,
            'trials': []})
    spec = importlib.util.spec_from_file_location('drc_feedback_repair', MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE = '''VERSION 5.8 ;
DESIGN chip_top ;
UNITS DISTANCE MICRONS 1000 ;
NETS 2 ;
  - target ( i Z ) + USE SIGNAL
      + ROUTED Metal1 ( 100000 100000 ) Via1_X ;
  - other ( j A ) + USE SIGNAL
      + ROUTED Metal2 ( 200000 200000 ) ( 201000 * ) ;
END NETS
END DESIGN
'''


def _rdb(count):
    item = ("<item><category>'CO.6a'</category><values><value>"
            "edge-pair: (99.79,100;99.79,100.01)/(99.80,100;99.80,100.01)"
            "</value></values></item>")
    return '<report-database><items>' + item * count + '</items></report-database>'


def _fixture(tmp_path):
    project = tmp_path / 'cell'
    pnr = project / 'phase3/stage3/pnr'
    pnr.mkdir(parents=True)
    (pnr / 'chip_top.def').write_text(BASE)
    (pnr / 'stream_out.py').write_text('print("EDA stream")\n')
    (pnr / 'pnr.tcl').write_text('set_routing_layers -signal Metal2-Metal5 -clock Metal3-Metal5\n')
    pdk = SimpleNamespace(drc_deck='/pdk/gf180mcu.drc', tech_lef='/pdk/tech.lef',
                          cell_lef='/pdk/cell.lef', macro_lefs=[],
                          cell_gds='/pdk/cell.gds', macro_gds=[],
                          lefdef_layermap='/pdk/map')
    return project, pdk


@pytest.mark.parametrize('fault, expected', [
    ('none', 'PASS'),
    ('non_target_wire', 'REFUSED'),
    ('router_drc', 'REFUSED'),
    ('antenna', 'REFUSED'),
    ('no_decrease', 'REFUSED'),
    ('pin_access', 'REFUSED'),
])
def test_feedback_accepts_only_guarded_strict_decrease(tmp_path, monkeypatch,
                                                       fault, expected):
    module = _module()
    project, pdk = _fixture(tmp_path)
    original = (project / 'phase3/stage3/pnr/chip_top.def').read_bytes()
    if MODULE.is_file():
        def fake_eda(_image, _project, argv, *, env=None):
            if argv[0] == 'klayout' and env is not None:
                out = Path(env['GDS_OUT'])
                out.write_bytes(Path(env['DEF']).read_bytes() + b'G' * 2048)
                return SimpleNamespace(returncode=0, stdout='GDS_WRITTEN', stderr='')
            if argv[0] == 'klayout':
                output = Path(next(v.split('=', 1)[1] for v in argv if v.startswith('report=')))
                gds = Path(next(v.split('=', 1)[1] for v in argv if v.startswith('input=')))
                changed = b'100200 100000' in gds.read_bytes()
                output.write_text(_rdb(1 if fault == 'no_decrease' or not changed else 0))
                return SimpleNamespace(returncode=0,
                    stdout=('Starting DRC: executing 1 deck(s).\n'
                            'Executing deck CO.6a_metal1_eol_overlap from '
                            '/pdk/rule_decks/contact.rb\nExecuting rule CO.6a'),
                    stderr='')
            script = Path(argv[-1])
            text = script.read_text()
            if script.name == 'pins.tcl':
                return SimpleNamespace(returncode=0,
                    stdout='FEEDBACK_PIN i/Z 99900 99900 100100 100100\n', stderr='')
            if script.name == 'antenna.tcl':
                return SimpleNamespace(returncode=0,
                    stdout='[INFO ANT-0002] Found 0 net violations.\n'
                           '[INFO ANT-0001] Found 0 pin violations.\n', stderr='')
            assert script.name == 'trial.tcl'
            if fault == 'pin_access':
                return SimpleNamespace(returncode=1, stdout='DRT-0073 No access point', stderr='')
            out = Path(text.split('write_def {', 1)[1].split('}', 1)[0])
            candidate = BASE.replace('100000 100000', '100200 100000')
            if fault == 'non_target_wire':
                candidate = candidate.replace('201000 *', '202000 *')
            out.write_text(candidate)
            drc = 1 if fault == 'router_drc' else 0
            antenna = 1 if fault == 'antenna' else 0
            return SimpleNamespace(returncode=0,
                stdout=f'[INFO DRT-0702] Post-route verification: {drc} violation(s).\n'
                       '[INFO DRT-0633] Scoped detailed routing: 1 net(s) named, 1 of 1 other net(s) held fixed (their existing wire is an obstruction and is not rewritten).\n'
                       '[INFO DRT-0634] Scoped detailed routing touched 1 net(s) in the database and left 1 net(s) byte-identical. Named: target.\n'
                       '[INFO DRT-0711] Scoped detailed routing: whole-design violations 0 on entry, 0 on exit (delta +0).\n'
                       f'[INFO ANT-0002] Found {antenna} net violations.\n'
                       '[INFO ANT-0001] Found 0 pin violations.\n', stderr='')
        monkeypatch.setattr(module, '_docker', fake_eda)
    result = module.run(project, 'chip_top', pdk, 'sha256:' + '0' * 64,
                        publish=False)
    assert result['status'] == expected
    assert (project / 'phase3/stage3/pnr/chip_top.def').read_bytes() == original
    if fault == 'none':
        assert (result['before_count'], result['after_count']) == (1, 0)
        assert result['trials'][0]['router_drc'] == 0
        assert result['trials'][0]['antenna'] == (0, 0)
    else:
        assert result['trials']
        assert all(not row.get('accepted') for row in result['trials'])
