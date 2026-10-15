"""Actual neutral OpenDB PG census -> existing Default producer -> L24 reader.

STA/PDK acquisition are source-unit transports. The tiny PG LEF/DEF and the
produced Tcl run with actual OpenROAD; no benchmark layout or body-tie claim.
"""
import json
import shutil
import subprocess

import pytest
import test_default_power_connectivity as existing
import execution_backend_producers as producers
import librelane_contract as LC
import librelane_ir_antenna as PG
import l24_signoff_evidence_backed_check as gate
import l24_signoff_requirements_extract as requirements

TECH = '''VERSION 5.8 ;
BUSBITCHARS "[]" ;
DIVIDERCHAR "/" ;
UNITS DATABASE MICRONS 1000 ; END UNITS
MANUFACTURINGGRID 0.001 ;
LAYER metal
 TYPE ROUTING ; DIRECTION HORIZONTAL ; PITCH 2 ; WIDTH 1 ; SPACING 1 ;
 RESISTANCE RPERSQ 1 ; CAPACITANCE CPERSQDIST 0.001 ;
END metal
LAYER positive_body
 TYPE MASTERSLICE ; PROPERTY LEF58_TYPE "TYPE NWELL ;" ;
END positive_body
LAYER negative_body
 TYPE MASTERSLICE ; PROPERTY LEF58_TYPE "TYPE PWELL ;" ;
END negative_body
END LIBRARY
'''
CELLS = '''VERSION 5.8 ;
BUSBITCHARS "[]" ; DIVIDERCHAR "/" ;
MACRO unit
 CLASS BLOCK ; ORIGIN 0 0 ; SIZE 10 BY 10 ; SYMMETRY X Y ;
 PIN supply DIRECTION INOUT ; USE POWER ;
  PORT LAYER metal ; RECT 0 1 10 2 ; END
 END supply
 PIN return DIRECTION INOUT ; USE GROUND ;
  PORT LAYER metal ; RECT 0 7 10 8 ; END
 END return
 PIN body_high DIRECTION INOUT ; USE POWER ;
  PORT LAYER positive_body ; RECT 0 2 10 5 ; END
 END body_high
 PIN body_low DIRECTION INOUT ; USE GROUND ;
  PORT LAYER negative_body ; RECT 0 0 10 2 ; END
 END body_low
END unit
END LIBRARY
'''
DEF = '''VERSION 5.8 ; DIVIDERCHAR "/" ; BUSBITCHARS "[]" ;
DESIGN neutral ; UNITS DISTANCE MICRONS 1000 ;
DIEAREA ( -1000 -1000 ) ( 40000 20000 ) ;
COMPONENTS 1 ;
- u_core unit + PLACED ( 20000 0 ) N ;
END COMPONENTS
PINS 2 ;
- VDD + NET VDD + DIRECTION INOUT + USE POWER + PORT + LAYER metal ( -500 -500 ) ( 500 500 ) + FIXED ( 0 1500 ) N ;
- VSS + NET VSS + DIRECTION INOUT + USE GROUND + PORT + LAYER metal ( -500 -500 ) ( 500 500 ) + FIXED ( 0 7500 ) N ;
END PINS
SPECIALNETS 2 ;
- VDD ( u_core supply ) ( u_core body_high ) ( PIN VDD ) + USE POWER + ROUTED metal 1000 ( 0 1500 ) ( 30000 1500 ) ;
- VSS ( u_core return ) ( u_core body_low ) ( PIN VSS ) + USE GROUND + ROUTED metal 1000 ( 0 7500 ) ( 30000 7500 ) ;
END SPECIALNETS
END DESIGN
'''


@pytest.mark.parametrize('case,verdict,outcome', [
    ('typed_wells', 'PASS', 'BACKED'),
    ('unknown_type', 'NOT_MEASURED', 'UNMET_NO_READING'),
    ('shapeless', 'NOT_MEASURED', 'UNMET_NO_READING'),
    ('wrong_use', 'FAIL', 'UNMET_MEASURED_FAILING'),
    ('missing_net', 'FAIL', 'UNMET_MEASURED_FAILING'),
    ('unplaced', 'NOT_MEASURED', 'UNMET_NO_READING'),
    ('native_false', 'FAIL', 'UNMET_MEASURED_FAILING'),
])
def test_current_pg_well_geometry_keeps_all_supply_terminals(
        tmp_path, monkeypatch, case, verdict, outcome):
    params, calls, _ = existing.setup_step23(tmp_path, monkeypatch, 'direct')
    tech, cells, layout = TECH, CELLS, DEF
    if case == 'unknown_type':
        tech = tech.replace('PROPERTY LEF58_TYPE "TYPE NWELL ;" ;', '')
    elif case == 'shapeless':
        cells = cells.replace('PORT LAYER positive_body ; RECT 0 2 10 5 ; END', '')
    elif case == 'wrong_use':
        cells = cells.replace('PIN body_high DIRECTION INOUT ; USE POWER ;',
                              'PIN body_high DIRECTION INOUT ; USE GROUND ;')
    elif case == 'missing_net':
        layout = layout.replace('( u_core body_high ) ', '')
    elif case == 'unplaced':
        layout = layout.replace('+ PLACED ( 20000 0 ) N', '+ UNPLACED')
    elif case == 'native_false':
        layout = layout.replace('( 30000 1500 )', '( 10000 1500 )')
    tech_path = existing.put(tmp_path / 'technology.lef', tech)
    cell_path = existing.put(tmp_path / 'cells.lef', cells)
    existing.put(tmp_path / 'phase3/stage3/pnr/neutral.def', layout)
    existing.put(tmp_path / 'phase3/stage3/pnr/routed.def', layout)
    existing.put(tmp_path / 'phase3/librelane/22-config/OpenROAD.RCX.json', {
        'DESIGN_NAME': 'neutral', 'TECH_LEFS': {'nom_*': str(tech_path)},
        'CELL_LEFS': [str(cell_path)], 'VDD_NETS': ['VDD'], 'GND_NETS': ['VSS']})
    native_events = []
    def native(project, image, cfg, body, folder, name, mounts, docker):
        calls.append(body[0])
        script = existing.put(folder / (name + '.tcl'),
            f'read_lef {{{tech_path}}}\nread_lef {{{cell_path}}}\n' + body[0])
        argv = [shutil.which('openroad'), '-exit', '-no_init', str(script)]
        assert argv[0], 'This control requires the official image OpenROAD'
        child = subprocess.run(argv, capture_output=True, text=True)
        existing.put(folder / (name + '.log'), child.stdout + child.stderr)
        native_events.append({'argv': argv, 'returncode': child.returncode})
        assert child.returncode == 0, child.stdout + child.stderr
        return script
    monkeypatch.setattr(LC, '_openroad_convert', native)
    result = producers.produce_signoff(tmp_path, params)
    report = json.loads((tmp_path / PG.POWER_CONNECTIVITY_REPORT).read_text())
    observed = []
    failures, _ = gate._requirements_backed(tmp_path,
        {'fields': requirements.extract_signoff_requirements(tmp_path)}, 'L24_SIGNOFF.json', observed)
    existing.put(tmp_path / 'well-api.observation.json', {
        'case': case, 'producer': result, 'report': report, 'consumer': observed,
        'native': native_events, 'scope': 'Tiny neutral native PG plus source-unit STA transport'})
    assert result['verdict'] == verdict, result
    assert observed[0]['outcome'] == outcome, observed
    assert bool(failures) is (outcome != 'BACKED')
    assert report['counts'] == {'nets': 2, 'iterms': 4, 'bterms': 2}
    assert len(report['pins']) == 6
    assert len(calls) == len(native_events) == 1
    if case == 'typed_wells':
        wells = [p for p in report['pins'] if p.get('shapes') == 0]
        assert len(wells) == 2
        assert all(p['physical_scope'] == 'well_geometry_and_supply_net_not_body_tie_continuity' for p in wells)
        assert report['native_checks']['VDD']['answer'] == report['native_checks']['VSS']['answer'] == '1'
    else:
        assert len(result['detail']) < 2200
        assert 'full_report=' + PG.POWER_CONNECTIVITY_REPORT in result['detail']


def test_large_current_pg_missing_population_has_bounded_detail(tmp_path, monkeypatch):
    params, _, _ = existing.setup_step23(tmp_path, monkeypatch, 'direct')
    original = LC._openroad_convert
    def population(project, image, cfg, body, folder, name, mounts, docker):
        result = original(project, image, cfg, body, folder, name, mounts, docker)
        raw = folder / 'power-connectivity.tsv'
        lines = raw.read_text().splitlines()[:-1]
        lines += ['\t'.join(['I', existing.b64('neutral_' + str(i)), existing.b64('body'),
                   existing.b64('VDD'), 'POWER', '1', '0']) for i in range(10000)]
        lines.append('END\t2\t10002\t2')
        raw.write_text('\n'.join(lines) + '\n')
        return result
    monkeypatch.setattr(LC, '_openroad_convert', population)
    result = producers.produce_signoff(tmp_path, params)
    report = json.loads((tmp_path / PG.POWER_CONNECTIVITY_REPORT).read_text())
    assert result['verdict'] == 'NOT_MEASURED'
    assert 'not_measured=10000' in result['detail']
    assert len(result['detail']) < 2200
    assert 'full_report=' + PG.POWER_CONNECTIVITY_REPORT in result['detail']
    assert len(report['not_measured']) == 10000
