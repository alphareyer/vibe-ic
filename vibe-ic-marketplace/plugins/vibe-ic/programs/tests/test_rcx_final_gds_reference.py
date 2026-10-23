"""Explicit final-GDS field reference, distinct from legacy routing-only mode."""
from pathlib import Path
import csv

import pytest
import rcx_field_solver_reference as ref
from librelane_contract import Refusal


def _matrix(path):
    # Analytic passive Maxwell matrix in farads. No design reference data.
    path.write_text('g1_VICTIM;g2_GROUND;g3_FILL\n4e-15;-1e-15;-2e-15\n-1e-15;3e-15;-1e-15\n-2e-15;-1e-15;4e-15\n')
    return path


def test_floating_charge_zero_is_not_grounded_diagonal(tmp_path):
    path=_matrix(tmp_path/'matrix.csv')
    floating=ref.floating_victim_c_pf(path,victim='VICTIM',grounded=['GROUND'],floating=['FILL'])
    grounded=ref.floating_victim_c_pf(path,victim='VICTIM',grounded=['GROUND','FILL'],floating=[])
    assert floating['capacitance_pf']==pytest.approx(0.003)
    assert grounded['capacitance_pf']==pytest.approx(0.004)
    assert floating['floating_potential_v']['FILL']==pytest.approx(0.5)
    assert abs(floating['floating_charge_c']['FILL']) < 1e-29
    assert floating['qualification']=='NOT_VERIFIED'


@pytest.mark.parametrize('damage',['missing_role','overlap','missing_row','nan','asymmetry'])
def test_invalid_matrix_or_unclassified_conductor_refused(tmp_path,damage):
    path=_matrix(tmp_path/'matrix.csv');grounded=['GROUND'];floating=['FILL']
    if damage=='missing_role':floating=[]
    elif damage=='overlap':grounded.append('FILL')
    elif damage=='missing_row':path.write_text('\n'.join(path.read_text().splitlines()[:-1])+'\n')
    elif damage=='nan':path.write_text(path.read_text().replace('4e-15','nan',1))
    else:path.write_text(path.read_text().replace('-2e-15','-3e-15',1))
    with pytest.raises(Refusal):
        ref.floating_victim_c_pf(path,victim='VICTIM',grounded=grounded,floating=floating)


def test_native_gf180_final_gds_fill_is_sensitive_and_floating_is_distinct(tmp_path):
    import klayout.db as db
    results={}
    for name,filled in [('bare',False),('filled',True)]:
        layout=db.Layout();layout.dbu=0.001;cell=layout.create_cell('FILL_PROBE')
        for net,x in [('A',0),('B',2000)]:
            cell.shapes(layout.layer(34,0)).insert(db.Box(x,0,x+1000,3000))
            cell.shapes(layout.layer(34,10)).insert(db.Text(net,db.Trans(x+500,1500)))
        if filled:cell.shapes(layout.layer(36,4)).insert(db.Box(500,500,2500,2500))
        gds=tmp_path/(name+'.gds');layout.write(str(gds));out=tmp_path/name
        record=ref.solve_final_gds(gds,'FILL_PROBE','gf180mcuD',out,tolerance=0.005,threads=2)
        header=next(csv.reader(Path(record['matrix']['path']).open(),delimiter=';'))
        names=[n.split('_',1)[1] for n in header]
        floats=[n for n in names if n not in ['VSUBS','A','B']]
        # This neutral fixture authors exactly zero/one isolated dummy island.
        assert len(floats)==int(filled)
        result=ref.collect_final_gds(out,victim='A',grounded=['VSUBS','B'],floating=floats)
        assert result['qualification']=='NOT_VERIFIED'
        assert result['final_geometry_common_source']=='NOT_VERIFIED'
        results[name]=result['electrical_model']['capacitance_pf']
        if filled:
            grounded=ref.collect_final_gds(out,victim='A',grounded=['VSUBS','B',*floats],floating=[])
            assert grounded['electrical_model']['capacitance_pf']>results[name]
            # Same invocation consumer must reject a missing native matrix.
            matrix=Path(record['matrix']['path']);matrix.rename(matrix.with_suffix('.retained'))
            with pytest.raises(Refusal,match='RCX_FINAL_BINDING_CHANGED'):
                ref.collect_final_gds(out,victim='A',grounded=['VSUBS','B'],floating=floats)
    assert results['filled']>results['bare']


def test_consumer_refuses_matrix_omitting_extracted_conductor(tmp_path):
    import json
    matrix=_matrix(tmp_path/'matrix.csv')
    lvs=tmp_path/'unit-only.lvsdb';lvs.write_text('unit contract only')
    log=tmp_path/'unit-only.log';log.write_text('unit contract only')
    record={'schema':'vibeic.final-gds-field-reference.v1','component_status':'MATRIX_MEASURED',
            'source_sha256':ref.digest(Path(ref.__file__)), 'inputs':{},
            'conductors':[{'name':'VICTIM'},{'name':'MISSING_ISLAND'}]}
    for key,path in [('matrix',matrix),('lvsdb',lvs),('native_log',log)]:
        record[key]={'path':str(path),'sha256':ref.digest(path)}
    (tmp_path/'matrix_receipt.json').write_text(json.dumps(record))
    with pytest.raises(Refusal,match='RCX_FINAL_CONDUCTOR_ABSENT'):
        ref.collect_final_gds(tmp_path,victim='VICTIM',grounded=['GROUND'],floating=['FILL'])


def test_missing_configured_solver_refuses_before_native(tmp_path,monkeypatch):
    gds=tmp_path/'unit.gds';gds.write_bytes(b'unit input, no native call')
    monkeypatch.setenv('KPEX_FASTERCAP_EXE',str(tmp_path/'missing-solver'))
    launches=[]
    monkeypatch.setattr(ref.subprocess,'run',lambda *a,**kw:launches.append(a))
    with pytest.raises(Refusal,match='RCX_FINAL_TOOL_ABSENT'):
        ref.solve_final_gds(gds,'UNIT','gf180mcuD',tmp_path/'out')
    assert launches==[]


def test_positive_definite_nonphysical_maxwell_matrix_refused(tmp_path):
    path = tmp_path / 'negative-shunt.csv'
    path.write_text('g1_A;g2_F;g3_G\n4e-12;-1.9e-12;-0.1e-12\n-1.9e-12;1e-12;-0.1e-12\n-0.1e-12;-0.1e-12;1e-12\n')
    with pytest.raises(Refusal, match='negative Maxwell row sum'):
        ref.floating_victim_c_pf(path, victim='A', floating=['F'], grounded=['G'])


@pytest.mark.parametrize('voltage', [1.9, -0.1, 0.4])
def test_inconsistent_floating_solution_refused(tmp_path, monkeypatch, voltage):
    import numpy as np
    path = _matrix(tmp_path / 'matrix.csv')
    monkeypatch.setattr(np.linalg, 'solve', lambda *args: np.array([voltage]))
    with pytest.raises(Refusal, match='RCX_FLOAT_SOLUTION_INVALID'):
        ref.floating_victim_c_pf(path, victim='VICTIM', grounded=['GROUND'], floating=['FILL'])
