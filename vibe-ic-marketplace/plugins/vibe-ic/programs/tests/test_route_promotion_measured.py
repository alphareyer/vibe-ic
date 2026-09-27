"""A promoted route carries its promoter's OWN measurement, and the antenna
report says which route it measured (cmp3 D15).

THE DEFECT. spm x gf180mcuD (lane fxpad, D11+D7+D1 stack): prestream_gate
failed `antenna` with `antenna check: unmeasured (signoff_spef_repair promoted
a re-routed design over the shipped DEF and no antenna check ran on it)`.
signoff_spef_repair never ran. The marker `routed_base_prerepair.def` was
LibreLane step 32's, written at 01:48:36 BEFORE the direct tail, which then
wrote the shipped `spm.def` (01:54:35) and measured it in-session: `Found 0
net violations` / `Found 0 pin violations`, ANTENNA_POSTROUTE_DONE. The report
read the marker as "a later session replaced the measured route".

THE RULE. Every promoter records WHAT it promoted (sha256) and its own
antenna + unrouted measurement of it (`reports/phase3/route_promotion.json`),
and LibreLane step 32 does not promote a candidate it did not measure. The
antenna report credits the promoter's measurement only when the promoted DEF
is the shipped one byte for byte; a promoted DEF a later routing session
rewrote is described by that session's own check; a promoted-and-shipped
route nobody measured stays UNMEASURED, naming its promoter.
"""
import importlib
import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
R = importlib.import_module('phase3_one_shot_runner')
prr = importlib.import_module('librelane_postroute_repair')
contract = importlib.import_module('librelane_contract')

RECORD = 'reports/phase3/route_promotion.json'


def put(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(obj if isinstance(obj, str) else json.dumps(obj))
    return path


def _run(tmp_path, *, promotion=None, marker=True, shipped='DESIGN top ;\n# tail\nEND DESIGN\n'):
    """A chip-path run as spm left it: the tail's own in-session check reads
    0/0 on the DEF it wrote, and step 32's marker sits beside it."""
    proj = tmp_path / 'proj'
    pnr = proj / 'phase3' / 'stage3' / 'pnr'
    put(pnr / 'top.def', shipped)
    put(pnr / 'openroad.log',
        'PNR_STAGE: postroute_antenna_repair\n'
        '[INFO ANT-0002] Found 0 net violations.\n'
        '[INFO ANT-0001] Found 0 pin violations.\n'
        'ANTENNA_POSTROUTE_DONE\n')
    if marker:
        put(pnr / 'routed_base_prerepair.def', 'DESIGN top ;\n# base\nEND DESIGN\n')
    if promotion is not None:
        put(proj / RECORD, promotion)
    return proj, pnr


def _pdk():
    return R.PdkConfig(name='p', liberty=Path('/l.lib'), tech_lef=Path('/t.tlef'),
                       cell_lef=Path('/c.lef'), cell_gds=Path('/c.gds'),
                       site='S', drc_deck='/d.drc')


def _emit(proj, tmp_path):
    rpt = tmp_path / 'reports' / 'phase3' / 'antenna.rpt'
    assert R._emit_antenna_report(proj, 'top', _pdk(), 'c', rpt, [])
    return rpt.read_text(), json.loads((rpt.parent / 'antenna.json').read_text())


def _sha(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ── red on main ──────────────────────────────────────────────────────────────

def test_a_promotion_a_later_session_rewrote_is_not_the_shipped_route(tmp_path):
    """spm: step 32 promoted its DEF, then the tail wrote (and measured) the
    one that ships. The tail's 0/0 is the shipped route's count."""
    proj, pnr = _run(tmp_path, promotion={
        'promoter': 'librelane_step32_in_chain', 'promoted_def': 'cand/chip_top.def',
        'promoted_def_sha256': 'f' * 64,
        'measurement': {'antenna_nets': 0, 'antenna_pins': 0, 'unrouted_added': 0}})
    txt, doc = _emit(proj, tmp_path)
    assert doc['clean'] is True and doc['shipped_route_measured'] is True, doc
    assert doc['net_violations'] == 0 and doc['pin_violations'] == 0
    assert 'antenna clean: YES' in txt
    assert doc['measured_on'] == 'the PnR route'


def test_a_promoted_route_that_ships_is_measured_by_its_promoter(tmp_path):
    proj, pnr = _run(tmp_path)
    put(proj / RECORD, {
        'promoter': 'librelane_step32_after_route', 'promoted_def': str(pnr / 'top.def'),
        'promoted_def_sha256': _sha(pnr / 'top.def'),
        'measurement': {'antenna_nets': 1, 'antenna_pins': 2, 'unrouted_added': 0,
                        'antenna_source': 'cand01/02-openroad-checkantennas'}})
    txt, doc = _emit(proj, tmp_path)
    # the promoter's own count, not the PnR session's 0/0 it replaced
    assert (doc['net_violations'], doc['pin_violations']) == (1, 2), doc
    assert doc['clean'] is False
    assert 'librelane_step32_after_route' in doc['measured_on']
    assert 'cand01/02-openroad-checkantennas' in doc['measured_on']


def test_a_shipped_promotion_nobody_measured_is_unmeasured_and_named(tmp_path):
    proj, pnr = _run(tmp_path)
    put(proj / RECORD, {
        'promoter': 'librelane_step32_after_route', 'promoted_def': str(pnr / 'top.def'),
        'promoted_def_sha256': _sha(pnr / 'top.def'), 'measurement': {}})
    txt, doc = _emit(proj, tmp_path)
    assert doc['shipped_route_measured'] is False and doc['clean'] is False, doc
    assert doc['net_violations'] is None
    assert 'librelane_step32_after_route PROMOTED' in doc['measured_on']
    assert 'antenna clean: NO' in txt


def _chain_report(project, adopted_state, *, measured=True):
    report = {'verdict': 'PASS', 'adopted': '32-cand01', 'adopted_state': str(adopted_state),
              'selected_arm': 'postdrt',
              'final': ({'antenna_nets': 0, 'antenna_pins': 0,
                         'antenna_state': 'cand01/02-openroad-checkantennas'}
                        if measured else {}),
              'candidates': [{'candidate': '32-cand01',
                              'repair_metrics': ({'vibeic__prr__unrouted__added': 0}
                                                 if measured else {})}]}
    return put(project / prr.REPORT_REL, report) and report


def _after_route(tmp_path, monkeypatch, *, measured):
    import test_pad_connected_pdn_ring as ring_fixture
    project = tmp_path / 'proj'
    put(project / 'phase3/librelane_switch.json', {'steps': {'21': 'librelane', '32': 'librelane'}})
    base_def = put(tmp_path / 'route.def', 'DESIGN top ;\nEND DESIGN\n')
    route_state = put(tmp_path / 'route_state.json', {'odb': 'r.odb', 'def': str(base_def)})
    cdef = put(tmp_path / 'c.def', 'DESIGN top ;\n# repaired\nEND DESIGN\n')
    adopted = put(tmp_path / 'cand/state_out.json', {'odb': str(tmp_path / 'c.odb'),
                                                     'def': str(cdef)})
    seen = {}

    def run_in_chain(project_, **kw):
        seen.update(kw)
        return _chain_report(project_, adopted, measured=measured)
    monkeypatch.setattr(prr, 'run_in_chain', run_in_chain)
    out = R.postroute_repair_after_route(
        project=project, pdk=ring_fixture._pdk(tmp_path, ring=None), image='img',
        pdk_root=tmp_path, sdc=base_def,
        deck='add_global_connection -net VDD -pin_pattern {^VDD$} -power\n',
        route_state=route_state, route_views={'odb': tmp_path / 'r.odb', 'def': base_def},
        route_drc=0, variant_arm=lambda *a: None)
    # D14: the runner hands step 32 its one dont_use rule
    assert seen['dont_use'] is R._STEP32_DONT_USE
    return project, out, cdef


def test_step32_does_not_promote_a_candidate_it_did_not_measure(tmp_path, monkeypatch):
    project, out, _ = _after_route(tmp_path, monkeypatch, measured=False)
    assert out['views'] == {}, out
    assert 'did not measure its own output' in out['record']['promotion_refused']
    report = json.loads((project / prr.REPORT_REL).read_text())
    assert 'did not measure its own output' in report['promotion_refused']
    assert not (R._pl.pnr_dir(project) / 'routed_base_prerepair.def').exists()
    assert not (project / RECORD).exists()


def test_step32_records_what_it_promoted_and_how_it_measured_it(tmp_path, monkeypatch):
    project, out, cdef = _after_route(tmp_path, monkeypatch, measured=True)
    assert out['views']['def'] == cdef
    rec = json.loads((project / RECORD).read_text())
    assert rec['promoter'] == 'librelane_step32_in_chain'
    assert rec['promoted_def_sha256'] == _sha(cdef)
    m = rec['measurement']
    assert (m['antenna_nets'], m['antenna_pins'], m['unrouted_added']) == (0, 0, 0)


def test_a_promotion_that_left_a_net_unrouted_is_refused():
    why = R._promotion_unmeasured({'antenna_nets': 0, 'antenna_pins': 0,
                                   'unrouted_added': 2})
    assert 'leaves 2 signal net(s) unrouted' in why
    assert R._promotion_unmeasured({'antenna_nets': 0, 'antenna_pins': 0,
                                    'unrouted_added': 0}) == ''


# ── control: green on main and on the fix ────────────────────────────────────

def test_a_marker_with_no_record_is_still_unmeasured(tmp_path):
    """No record at all (an older run): the marker alone keeps its meaning --
    something replaced the measured route, and nothing measured it."""
    proj, _ = _run(tmp_path)
    txt, doc = _emit(proj, tmp_path)
    assert doc['shipped_route_measured'] is False and doc['clean'] is False
    assert 'signoff_spef_repair' in doc['measured_on']
