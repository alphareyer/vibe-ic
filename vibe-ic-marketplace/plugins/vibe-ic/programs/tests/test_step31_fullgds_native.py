"""Bounded native recipe control: two real GF180 library cells, no PnR/run benchmark."""
import json
from pathlib import Path
import pytest
import librelane_pv_signoff as pv


def test_fullgds_librelane_transistor_match_and_mismatch(tmp_path):
    pya=pytest.importorskip('pya')
    pytest.importorskip('librelane')
    from librelane.config import Config
    from librelane.steps import Step
    from librelane.state import State
    from librelane.common import Path as LLPath
    pdk=Path('/foss/pdks/gf180mcuD')
    library=pdk/'libs.ref/gf180mcu_fd_sc_mcu7t5v0/gds/gf180mcu_fd_sc_mcu7t5v0.gds'
    if not library.is_file():
        pytest.skip('native control requires installed GF180 standard-cell GDS and Magic/Netgen setup')
    source=pya.Layout();source.read(str(library))
    layout=pya.Layout();layout.dbu=source.dbu;top=layout.create_cell('probe_top')
    instances=[]
    for index,offset in enumerate((0,2240)):
        name=f'gf180mcu_fd_sc_mcu7t5v0__inv_{index+1}'
        cell=layout.create_cell(name);cell.copy_tree(source.cell(name))
        top.insert(pya.CellInstArray(cell.cell_index(),pya.Trans(offset,0)))
        for layer in layout.layer_indices():
            for shape in cell.shapes(layer).each():
                if shape.is_text() and shape.text.string in ('I','ZN','VDD','VSS','VNW'):
                    label=shape.text.dup();label.x+=offset
                    if label.string in ('I','ZN'): label.string+=str(index)
                    top.shapes(layer).insert(label)
        instances.append(f'{name} u{index}(.I(I{index}),.ZN(ZN{index}),.VDD(VDD),.VSS(VSS),.VNW(VNW),.VPW(VSUBS));')
    gds=tmp_path/'probe_top.gds';layout.write(str(gds))
    netlist=tmp_path/'probe_top.v'
    text='module probe_top(I0,ZN0,I1,ZN1,VDD,VSS,VNW,VSUBS);\ninput I0,I1;\noutput ZN0,ZN1;\ninout VDD,VSS,VNW,VSUBS;\n'+'\n'.join(instances)+'\nendmodule\n'
    netlist.write_text(text)
    cfg_input={'DESIGN_NAME':'probe_top','VERILOG_FILES':[str(netlist)],
               'CLOCK_PORT':None,'CLOCK_PERIOD':10,
               'MAGIC_EXT_USE_GDS':True,'MAGIC_EXT_ABSTRACT':False}
    def_file=tmp_path/'probe_top.def'
    def_file.write_text('VERSION 5.8 ;\nDESIGN probe_top ;\nUNITS DISTANCE MICRONS 1000 ;\n'
                        'DIEAREA ( 0 0 ) ( 5600 3920 ) ;\nCOMPONENTS 2 ;\n'
                        '- u0 gf180mcu_fd_sc_mcu7t5v0__inv_1 + PLACED ( 0 0 ) N ;\n'
                        '- u1 gf180mcu_fd_sc_mcu7t5v0__inv_2 + PLACED ( 2240 0 ) N ;\n'
                        'END COMPONENTS\nEND DESIGN\n')
    state=State({'def':LLPath(str(def_file)),'gds':LLPath(str(gds)),'pnl':LLPath(str(netlist)),'nl':LLPath(str(netlist))})
    folders=[]
    for index,name in enumerate(pv.LVS_CHAIN,1):
        cls=Step.factory.get(name)
        config,_=Config.load(cfg_input,cls.get_all_config_variables(),design_dir=str(tmp_path),
                             pdk='gf180mcuD',pdk_root='/foss/pdks',scl='gf180mcu_fd_sc_mcu7t5v0')
        folder=tmp_path/f'{index:02}-{name.lower().replace(".","-")}'
        step=cls(config,state)
        state=step.start(step_dir=str(folder))
        folders.append(folder)
    record=pv.judge_pv(folders,pv.LVS_CHAIN,tmp_path/'judgment.json',scope={
        'gds_sha256':pv.digest(gds),'magic_ext_use_gds':True,'extracted_gds_sha256':pv.digest(gds)})
    assert record['verdict']=='PASS',record
    report=(folders[-1]/'reports/lvs.netgen.rpt').read_text()
    assert 'Circuits match uniquely' in report
    assert 'is a placeholder' not in report
    spice=Path(str(state['spice'])).read_text()
    assert 'nfet' in spice and 'pfet' in spice
    # A real connectivity change in the reference must fail on the SAME extraction.
    bad=tmp_path/'mismatch.v';bad.write_text(text.replace('.ZN(ZN1)', '.ZN(I1)'))
    extracted_doc=json.loads((folders[0]/'state_out.json').read_text())
    extracted_doc['pnl']=str(bad)
    extracted=State.load(extracted_doc)
    cls=Step.factory.get('Netgen.LVS')
    config,_=Config.load(cfg_input,cls.get_all_config_variables(),design_dir=str(tmp_path),
                         pdk='gf180mcuD',pdk_root='/foss/pdks',scl='gf180mcu_fd_sc_mcu7t5v0')
    folder=tmp_path/'03-negative-netgen'
    cls(config,extracted).start(step_dir=str(folder))
    negative=pv.judge_pv([folder],('Netgen.LVS',),tmp_path/'negative.json')
    assert negative['verdict']=='FAIL',negative
    assert 'Netlists do not match' in (folder/'reports/lvs.netgen.rpt').read_text()
