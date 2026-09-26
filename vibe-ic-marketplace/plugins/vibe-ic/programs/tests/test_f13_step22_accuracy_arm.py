"""F13 — the step-22 dual is selected by accuracy against a field-solver reference.

`rcx_field_solver_reference.py` produces the reference (KPEX + FasterCap on
the routed geometry of sampled nets); `librelane_signoff.accuracy_selection`
scores each arm's SPEF against it and lets the contract's `select_arms` pick;
the runner's step-22 dual record uses that selection when a reference is
declared, and keeps the frontier (UNDETERMINED) when none is.

Only the EDA tools' file writes are substituted (the `docker run` that runs
KLayout + KPEX writes what they write); the matrix sample below is a real
KPEX 0.3.12 / FasterCap output from the spm x gf180mcuD measurement (clip 110,
five conductors plus the substrate).
"""
import importlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
contract = importlib.import_module('librelane_contract')
signoff = importlib.import_module('librelane_signoff')
ref = importlib.import_module('rcx_field_solver_reference')

#: KPEX 0.3.12 `VICTIM_CLIP_FasterCap_Result_Matrix_Avg.csv`, verbatim (F).
REAL_MATRIX = """g1_VSUBS;g2_VICTIM;g3_$2;g4_$3;g5_$4;g6_$5
4.38135e-15;-1.339705e-16;-7.622695e-17;-1.239025e-16;-3.20197e-16;-5.017415e-16
-1.339705e-16;4.72251e-16;-3.26294e-18;-5.22905e-17;-1.246145e-16;-1.40388e-16
-7.622695e-17;-3.26294e-18;1.86966e-16;-1.35529e-17;-2.479405e-17;-6.104155e-17
-1.239025e-16;-5.22905e-17;-1.35529e-17;3.80117e-16;-8.989185e-17;-6.693795e-17
-3.20197e-16;-1.246145e-16;-2.479405e-17;-8.989185e-17;8.75567e-16;-2.039775e-16
-5.017415e-16;-1.40388e-16;-6.104155e-17;-6.693795e-17;-2.039775e-16;9.48386e-16
"""


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def spef(path, nets):
    """A minimal IEEE-1481 SPEF: one grounded cap per net, totals in pF."""
    body = ['*SPEF "IEEE 1481-1998"', '*DESIGN "t"', '*C_UNIT 1 PF', '*NAME_MAP']
    body += [f'*{i} {n}' for i, n in enumerate(nets, 1)]
    for i, (n, c) in enumerate(nets.items(), 1):
        body += [f'*D_NET *{i} {c}', '*CAP', f'1 *{i}:1 {c}', '*END']
    return write(path, '\n'.join(body) + '\n')


def reference(path, nets, corner='nom'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'kind': 'field_solver_reference', 'rc_corner': corner,
                                'nets': nets, 'failed': {}}))
    return path


REF = {'a': 0.010, 'b': 0.020, 'c': 0.030}


# ── the matrix reading: the victim's Maxwell diagonal ───────────────────────
def test_the_victims_total_c_is_its_maxwell_diagonal(tmp_path):
    got = ref.victim_c_pf(write(tmp_path / 'm.csv', REAL_MATRIX))
    assert got == pytest.approx(4.72251e-16 * 1e12)          # 0.000472 pF
    # ... and it is the sum of its couplings to every other conductor, to the
    # solver's own tolerance (measured: 3.9 % on this real matrix)
    row = [float(x) for x in REAL_MATRIX.splitlines()[2].split(';')]
    assert got == pytest.approx(-sum(row[:1] + row[2:]) * 1e12, rel=0.05)


def test_a_matrix_without_exactly_one_victim_is_refused(tmp_path):
    no_victim = REAL_MATRIX.replace('g2_VICTIM', 'g2_$1')
    with pytest.raises(contract.Refusal, match='RCX_REF_VICTIM_NOT_ONE_CONDUCTOR'):
        ref.victim_c_pf(write(tmp_path / 'm.csv', no_victim))
    with pytest.raises(contract.Refusal, match='RCX_REF_MATRIX_EMPTY'):
        ref.victim_c_pf(write(tmp_path / 'e.csv', 'g1_VICTIM\n'))


# ── the declared stack and layer map ────────────────────────────────────────
TLEF = """LAYER Poly2
  TYPE MASTERSLICE ;
END Poly2
LAYER CON
  TYPE CUT ;
END CON
LAYER M1
  TYPE ROUTING ;
END M1
LAYER V1
  TYPE CUT ;
END V1
LAYER M2
  TYPE ROUTING ;
END M2
LAYER OVERLAP
  TYPE OVERLAP ;
END OVERLAP
"""
MAP = """V1    VIA               35 0
V9    VIA               82 0
M1  NET,SPNET,PIN,VIA 34 0
NAME    M1/LABEL      34 10
M1  PIN               34 10
M2  NET,SPNET,PIN,VIA 36 0
NAME    M2/LABEL      36 10
DIEAREA ALL               0  0
"""


def test_the_stack_starts_at_the_first_routing_layer():
    assert ref.routing_stack(TLEF) == [('M1', 'ROUTING'), ('V1', 'CUT'), ('M2', 'ROUTING')]


def test_the_map_keeps_routing_purposes_only_and_refuses_an_unmapped_layer():
    got = ref.routing_map(MAP, ref.routing_stack(TLEF))
    assert got['map'].splitlines() == ['M1 NET,SPNET,VIA 34 0', 'V1 VIA 35 0',
                                       'M2 NET,SPNET,VIA 36 0']
    assert got['labels'] == {'M1': [34, 10], 'M2': [36, 10]}
    with pytest.raises(contract.Refusal, match="RCX_REF_LAYER_UNMAPPED.*'V1'"):
        ref.routing_map(MAP.replace('V1    VIA', 'Vx    VIA'), ref.routing_stack(TLEF))


# ── measure(): the EDA run faked at the docker edge ─────────────────────────
def test_measure_writes_a_reference_from_what_the_tools_wrote(tmp_path, monkeypatch):
    out = tmp_path / 'out'
    seen = {}

    def fake_run(argv, **kw):
        seen['argv'] = argv
        # KLayout wrote two clips and skipped one net; KPEX solved one clip
        # and failed the other -- the files those tools leave behind.
        write(out / 'clips/index.json', json.dumps([
            {'i': 0, 'net': 'n0', 'gds': 'x'}, {'i': 1, 'net': 'n1', 'gds': 'y'},
            {'i': 2, 'net': 'n2', 'skip': '0 routed clusters'}]))
        write(out / f'kpex/0000/0000__{ref.CLIP_CELL}/{ref.MATRIX}', REAL_MATRIX)
        write(out / 'kpex/0000/rc', '0\n')
        write(out / 'kpex/0001/rc', '1\n')
        write(out / 'kpex_version.txt', 'kpex 0.3.12\n')
        return subprocess.CompletedProcess(argv, 0, '', '')
    monkeypatch.setattr(ref.subprocess, 'run', fake_run)
    got = ref.measure('/d/x.def', ['/d/t.lef'], '/d/m.map', 'somepdk', ['n0', 'n1', 'n2'],
                      out, image='img', tech_lef_text=TLEF, pdk_map_text=MAP, halo_um=2.0)
    assert got['nets'] == {'n0': pytest.approx(4.72251e-4)}
    assert set(got['failed']) == {'n1', 'n2'} and 'rc=1' in got['failed']['n1']
    assert got['kind'] == 'field_solver_reference' and got['rc_corner'] == 'nom'
    assert json.loads((out / 'reference.json').read_text())['nets'] == got['nets']
    argv = seen['argv']
    assert argv[:2] == ['docker', 'run'] and '--memory' in argv, argv
    assert 'img' in argv and '--kpex' not in argv
    script = argv[-1]
    assert '--pdk somepdk' in script and '--fastercap' in script and 'clip.py' in script
    assert '--tolerance 0.01' in script and got['fastercap_tolerance'] == 0.01
    cfg = json.loads((out / 'clip_config.json').read_text())
    assert cfg['stack'][1] == ['V1', 'CUT', [35, 0]] and cfg['halo_um'] == 2.0


def test_measure_refuses_no_nets(tmp_path):
    with pytest.raises(contract.Refusal, match='RCX_REF_NO_NETS'):
        ref.measure('/d', [], '/m', 'p', [], tmp_path, image='i', tech_lef_text=TLEF,
                    pdk_map_text=MAP)


# ── accuracy_selection: select_arms on the reference ────────────────────────
def test_the_arm_that_tracks_the_reference_is_selected(tmp_path):
    close = spef(tmp_path / 'close.spef', {'a': 0.011, 'b': 0.019, 'c': 0.031, 'd': 1.0})
    far = spef(tmp_path / 'far.spef', {'a': 0.014, 'b': 0.027, 'c': 0.041, 'd': 1.0})
    r = reference(tmp_path / 'ref.json', REF)
    got = signoff.accuracy_selection({'direct': far, 'librelane': close}, r, tmp_path / 'acc')
    assert got['selection'] == 'librelane' and got['reason'] is None
    assert got['criterion'] == 'accuracy against the field-solver reference'
    arm = json.loads((tmp_path / 'acc/librelane.json').read_text())
    assert arm['metrics']['c_mean_abs_err_pct']['value'] == pytest.approx(
        (10.0 + 5.0 + 100 / 30) / 3)
    # swapping the SPEFs swaps the selection: the pick follows the numbers
    got = signoff.accuracy_selection({'direct': close, 'librelane': far}, r, tmp_path / 'acc2')
    assert got['selection'] == 'direct'


def test_equal_accuracy_keeps_both_arms(tmp_path):
    same = spef(tmp_path / 's.spef', {'a': 0.011, 'b': 0.019, 'c': 0.031})
    got = signoff.accuracy_selection({'direct': same, 'librelane': same},
                                     reference(tmp_path / 'r.json', REF), tmp_path / 'acc')
    assert got['selection'] == 'UNDETERMINED' and got['reason'] == 'LL_PARETO_TIE'


def test_an_arm_missing_a_reference_net_is_not_measured(tmp_path):
    full = spef(tmp_path / 'f.spef', {'a': 0.011, 'b': 0.019, 'c': 0.031})
    short = spef(tmp_path / 's.spef', {'a': 0.010, 'b': 0.020})
    got = signoff.accuracy_selection({'direct': full, 'librelane': short},
                                     reference(tmp_path / 'r.json', REF), tmp_path / 'acc')
    assert got['selection'] == 'UNDETERMINED' and got['reason'] == 'LL_ARM_NOT_MEASURED'
    assert json.loads((tmp_path / 'acc/librelane.json').read_text())['missing_nets'] == ['c']


@pytest.mark.parametrize('doc', [{'kind': 'field_solver_reference', 'nets': {}},
                                 {'kind': 'other', 'nets': {'a': 1.0}},
                                 {'kind': 'field_solver_reference', 'nets': {'a': 0}}])
def test_an_empty_or_foreign_reference_is_refused(tmp_path, doc):
    p = write(tmp_path / 'r.json', json.dumps(doc))
    with pytest.raises(contract.Refusal, match='LL_RC_REFERENCE_'):
        signoff.load_reference(p)


# ── the runner's step-22 dual record ────────────────────────────────────────
def _dual(tmp_path, monkeypatch, *, with_reference, corner='nom'):
    runner = importlib.import_module('phase3_one_shot_runner')
    project = tmp_path / 'proj'
    corner_dir = project / 'phase3/stage3/extracted/spef_corners'
    for c in ('min', 'nom', 'max'):
        spef(corner_dir / f'spm.{c}.spef', {'a': 0.014, 'b': 0.027, 'c': 0.041})
    tool = {f'{c}_*': spef(tmp_path / f'tool/{c}.spef', {'a': 0.011, 'b': 0.019, 'c': 0.031})
            for c in ('min', 'nom', 'max')}
    monkeypatch.setattr(runner, '_librelane_signoff_run', lambda *a, **k: {
        'sta': None, 'image': 'img', 'mounts': [], 'rcx': tmp_path / 'tool', 'spef': tool})
    if with_reference:
        reference(project / 'phase3/tool_arms/22/reference.json', REF, corner)
    notes, failures, written = [], [], []
    runner._librelane_signoff_record(project, 'spm', SimpleNamespace(name='p'), 'dual',
                                     'direct', corner_dir, project / 'absent.rpt',
                                     notes, failures, written)
    assert not failures, failures
    return json.loads((project / 'reports/phase3/rc_extraction_arms.json').read_text())


def test_the_dual_record_selects_by_accuracy_when_a_reference_is_declared(tmp_path, monkeypatch):
    doc = _dual(tmp_path, monkeypatch, with_reference=True)
    assert doc['selection'] == 'librelane', doc
    assert doc['criterion'] == 'accuracy against the field-solver reference'
    assert doc['accuracy']['objectives'] == {'c_mean_abs_err_pct': 'min'}


def test_the_dual_record_keeps_the_frontier_without_a_reference(tmp_path, monkeypatch):
    doc = _dual(tmp_path, monkeypatch, with_reference=False)
    assert doc['selection'] == 'UNDETERMINED'
    assert doc['reason'].startswith('LL_RC_ACCURACY_NOT_MEASURED')


def test_a_reference_at_a_corner_no_arm_has_is_named(tmp_path, monkeypatch):
    doc = _dual(tmp_path, monkeypatch, with_reference=True, corner='typ')
    assert doc['selection'] == 'UNDETERMINED'
    assert doc['reason'].startswith('LL_RC_REFERENCE_CORNER_UNMATCHED')
