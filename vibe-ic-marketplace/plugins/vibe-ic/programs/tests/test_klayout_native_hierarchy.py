"""Native multi-cell flat extraction versus hierarchical transistor reference (#3316).

Synthetic public geometry; real KLayout device extraction and native comparison.
No device/tolerance/status substitutions. Run inside the stated EDA image.
"""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
import klayout_pdk_lvs as owner


def _fixture(root, pya, mismatch=False, missing_pin=False):
    """Two library masters, each with two series MOSFETs and an internal label."""
    root.mkdir()
    ly = pya.Layout(); ly.dbu = .001
    top = ly.create_cell('unit_top')
    for index, width in enumerate((1000, 2000)):
        cell = ly.create_cell(('SC_A', 'SC_B')[index])
        def box(layer, coords):
            cell.shapes(ly.layer(*layer)).insert(pya.Box(*coords))
        box((1,0), (0,0,2200,width))
        box((2,0), (-200,-200,2400,width+200))
        box((8,0), (2000,width//2-50,2100,width//2+50))
        for x in (500,1500):
            box((3,0), (x,-300,x+200,width+700))
        box((3,0), (500,width+500,1700,width+700))
        for x,y in ((150,width//2),(1100,width//2),(2050,width//2),(600,width+600)):
            box((7,0),(x-50,y-50,x+50,y+50))
            box((9,0),(x-100,y-100,x+100,y+100))
        cell.shapes(ly.layer(9,5)).insert(pya.Text('LOCAL',1100,width//2))
        trans=pya.Trans(index*5000,0)
        top.insert(pya.CellInstArray(cell.cell_index(),trans))
        for name,x,y in ((f'D{index}',150,width//2),(f'S{index}',2050,width//2),
                         (f'G{index}',600,width+600)):
            if not (missing_pin and name == 'G1'):
                top.shapes(ly.layer(9,5)).insert(pya.Text(name,x+index*5000,y))
    layout=root/'layout.gds'; ly.write(str(layout))
    cells=root/'cells.spice'
    cells.write_text('* two series-transistor standard-cell fixtures\n.model nch nmos\n'+''.join(
        f'.subckt {name} D G S\nM1 D G mid S nch W={width}u L=0.2u\n'
        f'M2 mid G S S nch W={width}u L=0.2u\n.ends {name}\n'
        for name,width in [('SC_A',1),('SC_B',3 if mismatch else 2)]))
    verilog=root/'source.v'
    verilog.write_text('module unit_top(D0,G0,S0,D1,G1,S1);\ninput D0,G0,S0,D1,G1,S1;\n'
                       'SC_A a(.D(D0),.G(G0),.S(S0));\n'
                       'SC_B b(.D(D1),.G(G1),.S(S1));\nendmodule\n')
    runset=root/'runset';runset.mkdir();deck=runset/'fixture.lvs'
    deck.write_text('''source($input, $topcell)
report_lvs($report)
target_netlist($target_netlist, write_spice(true, true))
active = input(1,0)
bulk = input(2,0)
bulk_contact = input(8,0)
poly = input(3,0)
contact = input(7,0)
metal = input(9,0)
labels = labels(9,5)
gate = active & poly
sd = active - poly
extract_devices(mos4("nch"), {"SD" => sd, "G" => gate, "W" => bulk, "tS" => sd, "tD" => sd, "tG" => poly})
connect(bulk, bulk)
connect(bulk, bulk_contact)
connect(bulk_contact, metal)
connect(sd, sd)
connect(poly, poly)
connect(metal, metal)
connect(contact, sd)
connect(contact, poly)
connect(contact, metal)
connect(metal, labels)
schematic($schematic)
# Retained empty masters in flat extraction, as in the source86 native DB.
["SC_A", "SC_B"].each do |name|
  unless netlist.circuit_by_name(name)
    master = RBA::Circuit.new
    master.name = name
    netlist.add(master)
  end
end
align
netlist.make_top_level_pins if $top_lvl_pins == "true"
schematic.make_top_level_pins if $top_lvl_pins == "true"
compare
''')
    return SimpleNamespace(gds=str(layout),verilog=str(verilog),deck=str(deck),
         cell_library=[str(cells)],out=str(root/'verdict.json'),top='unit_top',
         work_dir=str(root/'native'),library_translator=None,threads=2,timeout=60,
         parameter=['run_mode=flat','top_lvl_pins=true'])


@pytest.mark.parametrize('case', ['match','device_mismatch','missing_pin'])
def test_native_multicell_preserves_transistors_and_external_interface(tmp_path, case):
    pya=pytest.importorskip('pya')
    args=_fixture(tmp_path/'fixture',pya,mismatch=case=='device_mismatch',missing_pin=case=='missing_pin')
    rc=owner.cmd_native(args,pya)
    record=json.loads(Path(args.out).read_text())
    db=pya.LayoutVsSchematic(); db.read(record['native_result']['path'])
    layout=next(c for c in db.netlist().each_circuit() if c.name.lower()=='unit_top')
    reference=next(c for c in db.reference.each_circuit() if c.name.lower()=='unit_top')
    # Actual native device census, not wrapper counters or source wiring.
    assert len(list(layout.each_device())) == 4
    if case=='match':
        assert record['status']=='PASS', record['native_result']
        assert rc==0
        assert len(list(reference.each_device())) == 4
        assert layout.pin_count()==reference.pin_count()==6
        assert all(r['status']=='Match' for r in record['native_result']['circuits'])
    else:
        assert record['status']!='PASS', record['native_result']
        assert rc==1
        if case=='device_mismatch':
            assert record['status']=='FAIL'
            assert any(r['status'] in ('NoMatch','Mismatch') for r in record['native_result']['circuits'])
