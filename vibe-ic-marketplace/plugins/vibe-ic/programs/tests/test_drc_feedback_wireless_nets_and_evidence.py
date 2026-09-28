"""The pre-stream DRC feedback repair: a dangling net is not a moved wire, the
route's own tech LEF is read, and every refusal leaves its evidence (fxpad note6).

MEASURED on a copy of lane cmpb's subservient x gf180mcuD run
(/home/reyerchu/cmp3r/same_rtl_subservient on 8HD-4; copied, never modified):
prestream_gate failed SIGNOFF_DECK_FEEDBACK_REFUSED: FEEDBACK_BOUND_EXHAUSTED
on 2 KLayout CO.6a markers. All three trials had router DRC 0, antenna 0/0 and
`non_target_changed: []`, and were refused only by the native scoped guard:

  * DRT-0633 held 1050 other nets; DRT-0634 left 1039 byte-identical. The 11
    are exactly the input DEF's nets with ZERO connections (synthesis
    leftovers): no wire, so DRT never counts them identical. The guard's
    `others == unchanged` could not hold on any design that has one.
  * A trial handed the PDK's own tech LEF under a route built on the flow's
    via-legalized copy saw 510 whole-design violations on untouched nets
    (497 Metal2 Min Area), so "0 on entry" failed too.

With both fixed the same repair accepted trial 1 and the deck's CO.6a went
2 -> 0 on the copy. The trials' logs had been deleted with the scratch
directory, so none of this could be read from cmpb's run.

Real feedback decision path; only the EDA file writes are faked.
"""
import hashlib
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
D = importlib.import_module('drc_feedback_repair')

BASE = '''VERSION 5.8 ;
DESIGN chip_top ;
UNITS DISTANCE MICRONS 1000 ;
NETS 3 ;
  - target ( i Z ) + USE SIGNAL
      + ROUTED Metal1 ( 100000 100000 ) Via1_X ;
  - other ( j A ) + USE SIGNAL
      + ROUTED Metal2 ( 200000 200000 ) ( 201000 * ) ;
  - dangling + USE SIGNAL ;
END NETS
END DESIGN
'''
VIA_REC = 'reports/pdk_via_patch_legalization.json'


def _rdb(count):
    item = ("<item><category>'CO.6a'</category><values><value>"
            "edge-pair: (99.79,100;99.79,100.01)/(99.80,100;99.80,100.01)"
            "</value></values></item>")
    return '<report-database><items>' + item * count + '</items></report-database>'


def _fixture(tmp_path, staged=None, staged_sha=None):
    project = tmp_path / 'cell'
    pnr = project / 'phase3/stage3/pnr'
    pnr.mkdir(parents=True)
    (pnr / 'chip_top.def').write_text(BASE)
    (pnr / 'pnr.tcl').write_text('set_routing_layers -signal Metal2-Metal5 -clock Metal3-Metal5\n')
    if staged is not None:
        tlef = pnr / 'active_via_legalized.tlef'
        tlef.write_text(staged)
        (project / 'reports').mkdir(parents=True, exist_ok=True)
        (project / VIA_REC).write_text(json.dumps({
            'status': 'APPLIED', 'derived_tech_lef': str(tlef),
            'derived_sha256': staged_sha or hashlib.sha256(staged.encode()).hexdigest()}))
    pdk = SimpleNamespace(drc_deck='/pdk/gf180mcu.drc', tech_lef='/pdk/tech.lef',
                          cell_lef='/pdk/cell.lef', macro_lefs=[],
                          cell_gds='/pdk/cell.gds', macro_gds=[],
                          lefdef_layermap='/pdk/map')
    return project, pdk


def _fake_eda(seen, *, move_other=False):
    """The EDA tools' file writes. The router log states what DRT states on a
    design with one dangling net: 2 held, 1 byte-identical."""
    def fake(_image, _project, argv, *, env=None):
        if argv[0] == 'klayout' and env is not None:
            Path(env['GDS_OUT']).write_bytes(Path(env['DEF']).read_bytes() + b'G' * 2048)
            return SimpleNamespace(returncode=0, stdout='GDS_WRITTEN', stderr='')
        if argv[0] == 'klayout':
            output = Path(next(v.split('=', 1)[1] for v in argv if v.startswith('report=')))
            gds = Path(next(v.split('=', 1)[1] for v in argv if v.startswith('input=')))
            output.write_text(_rdb(0 if b'100200 100000' in gds.read_bytes() else 1))
            return SimpleNamespace(returncode=0, stdout=(
                'Starting DRC: executing 1 deck(s).\n'
                'Executing deck CO.6a_metal1_eol_overlap from /pdk/rule_decks/contact.rb\n'
                'Executing rule CO.6a'), stderr='')
        script = Path(argv[-1])
        text = script.read_text()
        seen.setdefault(script.name, []).append(text)
        if script.name == 'pins.tcl':
            return SimpleNamespace(returncode=0,
                                   stdout='FEEDBACK_PIN i/Z 99900 99900 100100 100100\n', stderr='')
        if script.name == 'antenna.tcl':
            return SimpleNamespace(returncode=0, stdout=(
                '[INFO ANT-0002] Found 0 net violations.\n'
                '[INFO ANT-0001] Found 0 pin violations.\n'), stderr='')
        assert script.name == 'trial.tcl'
        out = Path(text.split('write_def {', 1)[1].split('}', 1)[0])
        candidate = BASE.replace('100000 100000', '100200 100000')
        if move_other:
            candidate = candidate.replace('201000 *', '202000 *')
        out.write_text(candidate)
        (script.parent / 'router.drc.rpt').write_text('')
        return SimpleNamespace(returncode=0, stdout=(
            '[INFO DRT-0702] Post-route verification: 0 violation(s).\n'
            '[INFO DRT-0633] Scoped detailed routing: 1 net(s) named, 2 of 2 other net(s) '
            'held fixed (their existing wire is an obstruction and is not rewritten).\n'
            '[INFO DRT-0634] Scoped detailed routing touched 1 net(s) in the database and '
            f'left {0 if move_other else 1} net(s) byte-identical. Named: target.\n'
            '[INFO DRT-0711] Scoped detailed routing: whole-design violations 0 on entry, '
            '0 on exit (delta +0).\n'
            '[INFO ANT-0002] Found 0 net violations.\n'
            '[INFO ANT-0001] Found 0 pin violations.\n'), stderr='')
    return fake


def _run(project, pdk):
    return D.run(project, 'chip_top', pdk, 'sha256:' + '0' * 64, publish=False,
                 stream_script_text='print("EDA stream")\n')


# ── red on main ──────────────────────────────────────────────────────────────

def test_a_dangling_net_is_not_a_moved_wire(tmp_path, monkeypatch):
    project, pdk = _fixture(tmp_path)
    monkeypatch.setattr(D, '_docker', _fake_eda({}))
    result = _run(project, pdk)
    assert result['status'] == 'PASS', result.get('reason')
    assert (result['before_count'], result['after_count']) == (1, 0)
    basis = result['trials'][0]['native_wire_guard_basis']
    assert basis['wireless_others'] == 1
    assert basis['named_held'] == [1, 2] and basis['touched_identical'] == [1, 1]


def test_every_trial_keeps_its_evidence_bound_by_sha(tmp_path, monkeypatch):
    project, pdk = _fixture(tmp_path)
    monkeypatch.setattr(D, '_docker', _fake_eda({}, move_other=True))
    result = _run(project, pdk)
    assert result['status'] == 'REFUSED'
    assert result['trials'], result
    for trial in result['trials']:
        assert trial['refusal'] == 'NON_TARGET_WIRE_CHANGED'
        kept = trial['evidence']
        names = {Path(p).name for p in kept}
        assert {'trial.tcl', 'trial.log', 'router.drc.rpt'} <= names, kept
        for rel, sha in kept.items():
            path = project / rel
            assert rel.startswith(D.TRIALS_REL + '/')
            assert hashlib.sha256(path.read_bytes()).hexdigest() == sha
        assert 'held fixed' in (project / next(p for p in kept if p.endswith('trial.log'))).read_text()


def test_the_trial_reads_the_tech_lef_the_route_read(tmp_path, monkeypatch):
    project, pdk = _fixture(tmp_path, staged='VERSION 5.8 ;\n# via-legalized\n')
    seen = {}
    monkeypatch.setattr(D, '_docker', _fake_eda(seen))
    result = _run(project, pdk)
    staged = str((project / 'phase3/stage3/pnr/active_via_legalized.tlef').resolve())
    assert result['tech_lef']['path'] == staged
    assert VIA_REC in result['tech_lef']['source']
    assert all(f'read_lef {{{staged}}}' in t and '/pdk/tech.lef' not in t
               for t in seen['trial.tcl'])


def test_an_applied_record_whose_file_is_not_bound_is_refused(tmp_path, monkeypatch):
    project, pdk = _fixture(tmp_path, staged='VERSION 5.8 ;\n', staged_sha='0' * 64)
    monkeypatch.setattr(D, '_docker', _fake_eda({}))
    result = _run(project, pdk)
    assert result['status'] == 'REFUSED'
    assert result['reason'].startswith('FEEDBACK_ROUTE_TECH_LEF_UNBOUND')


# ── control: green on main and on the fix ────────────────────────────────────

def test_a_moved_wired_net_is_refused_whatever_the_wireless_count(tmp_path, monkeypatch):
    """The allowance is for nets with no wire only: a wired non-target net the
    router changed is still refused (our DEF diff and the tool's count agree)."""
    project, pdk = _fixture(tmp_path)
    monkeypatch.setattr(D, '_docker', _fake_eda({}, move_other=True))
    result = _run(project, pdk)
    assert result['status'] == 'REFUSED'
    assert all(t.get('refusal') == 'NON_TARGET_WIRE_CHANGED' for t in result['trials'])
    assert all('other' in t['non_target_changed'] for t in result['trials'])
