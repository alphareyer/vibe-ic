"""F22: step 30's SPICE deck simulates the cells the liberty was characterised
on, and the Xyce arm reads what the tool's Xyce deck writes.

MEASURED on the spm copy (gf180mcuD, max_ss_125C_4v50, vibeic-eda 0.3.79),
register to register, `write_path_spice` with the same STAPostPNR state:

    cell view            ngspice    Xyce       tolerance
    declared schematic   -28.98%    -29.04%    18.4%    MISMATCH (both)
    extracted layout     -11.29%    -11.31%    18.4%    CORRELATED (both)

The schematic subckts carry W/L only (`as=ad=ps=pd=0`, no wiring). One cell
per deck at liberty grid points, on the liberty's own driver waveform, the
schematic view reads -35..-50% at the smallest load; the same cells extracted
from the PDK's GDS read within 1% (nand2_1) and 5% (buf_1) at a mid point.

The programs run for real; only the tools' file writes are substituted at the
`_docker` edge. The extracted netlist below is Magic 8.3's own ext2spice
output for gf180mcu_fd_sc_mcu7t5v0__nand2_1 from the PDK's GDS; the CSV header
is Xyce's own `.print tran format=csv` output of the tool's deck.
"""
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(PROGRAMS / 'tests'))
pst = importlib.import_module('path_spice_tool')
postroute = importlib.import_module('librelane_postroute')
t106 = importlib.import_module('test_t106_mig_perc_power')
write, put = t106.write, t106.put

SCHEMATIC = '''* cells
.SUBCKT cells__nand2_1 A1 A2 ZN VDD VNW VPW VSS
X_i_1 net_0 A2 VSS VPW nfet_05v0 W=8.2e-07 L=6e-07
X_i_0 ZN A1 net_0 VPW nfet_05v0 W=8.2e-07 L=6e-07
X_i_3 ZN A2 VDD VNW pfet_05v0 W=1.13e-06 L=5e-07
X_i_2 VDD A1 ZN VNW pfet_05v0 W=1.13e-06 L=5e-07
.ENDS
.SUBCKT cells__dffq_1 D CLK Q VDD VNW VPW VSS
X_i_0 Q D VSS VPW nfet_05v0 W=8.2e-07 L=6e-07
.ENDS
.SUBCKT cells__fill_1 VDD VNW VPW VSS
.ENDS
'''

#: Magic's ext2spice of the PDK's nand2_1 layout (ports in LAYOUT order).
EXTRACTED_NAND2 = '''* NGSPICE file created from cells__nand2_1.ext - technology: gf180mcuD

.subckt cells__nand2_1 VDD VSS ZN A1 A2 VNW VPW
X0 ZN A2 VDD VNW pfet_05v0 ad=0.2938p pd=1.65u as=0.4972p ps=3.14u w=1.13u l=0.5u
X1 ZN A1 a_245_68# VPW nfet_05v0 ad=0.3608p pd=2.52u as=0.1312p ps=1.14u w=0.82u l=0.6u
X2 VDD A1 ZN VNW pfet_05v0 ad=0.4972p pd=3.14u as=0.2938p ps=1.65u w=1.13u l=0.5u
X3 a_245_68# A2 VSS VPW nfet_05v0 ad=0.1312p pd=1.14u as=0.3608p ps=2.52u w=0.82u l=0.6u
C0 A1 A2 0.12164f
C3 ZN VSS 0.05156f
C13 ZN VDD 0.13491f
C21 ZN VPW 0.27964f
.ends
'''

NETLIST = '''module chip_top (clk, x, p);
 cells__nand2_1 _335_ (.A1(a), .A2(b), .ZN(c));
 cells__dffq_1 _417_ (.D(c), .CLK(clk), .Q(p));
 cells__fill_1 FILLER_0 ();
endmodule
'''

CONFIG_TCL = ('set ::env(FP_TAPCELL_DIST) 20\n'
              'set ::env(MAGICRC) "$::env(PDK_ROOT)/$::env(PDK)/libs.tech/magic/$::env(PDK).magicrc"\n')


def _magic_edge(calls, extracted=None):
    """Magic writes one ext2spice file per cell it can load from the GDS."""
    extracted = {'cells__nand2_1': EXTRACTED_NAND2} if extracted is None else extracted

    def fake(image, project, mounts, argv, cwd, docker='docker', env=None):
        calls.append((argv, env))
        assert argv[0] == 'magic', argv
        tcl = Path(argv[-1]).read_text()
        for cell in __import__('re').findall(r'^load (\S+)$', tcl, __import__('re').M):
            if cell in extracted:
                write(Path(cwd) / f'{cell}.spice', extracted[cell])
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    return fake


def _pdk(tmp_path):
    root = tmp_path / 'pdkroot'
    write(root / 'x/libs.ref/cells/spice/cells.spice', SCHEMATIC)
    write(root / 'x/libs.tech/librelane/config.tcl', CONFIG_TCL)
    return root


# ---------------------------------------------------------- the cell view ---

def test_the_layout_view_takes_the_schematic_subckts_place_in_the_schematic_port_order(
        tmp_path, monkeypatch):
    root = _pdk(tmp_path)
    netlist = write(tmp_path / 'design/nl.v', NETLIST)
    calls = []
    monkeypatch.setattr(pst, '_docker', _magic_edge(calls))
    view = pst.cell_view('img', tmp_path / 'design', [(root / 'x', '/pdk/x')],
                         config={'CELL_GDS': ['/pdk/x/libs.ref/cells/gds/cells.gds']},
                         pdk_dir=root / 'x', guest_root='/pdk', pdk='x', netlist=netlist,
                         cell_sources=[root / 'x/libs.ref/cells/spice/cells.spice'],
                         out_dir=tmp_path / 'out')
    (argv, env), = calls
    # The PDK's own rcfile, resolved in the guest, with the guest PDK_ROOT it reads.
    assert argv[:5] == ['magic', '-dnull', '-noconsole', '-rcfile', '/pdk/x/libs.tech/magic/x.magicrc']
    assert env == {'PDK_ROOT': '/pdk'}
    tcl = Path(argv[-1]).read_text()
    assert 'gds read /pdk/x/libs.ref/cells/gds/cells.gds' in tcl
    assert 'ext2spice cthresh 0' in tcl and 'ext2spice extresist off' in tcl
    # A cell with no device in its declared netlist is not extracted at all.
    assert 'load cells__fill_1' not in tcl
    assert view['extracted'] == ['cells__nand2_1']
    # A cell the layout could not stand in for keeps its schematic, by name.
    assert list(view['kept_schematic']) == ['cells__dffq_1']
    assert view['view'] == 'mixed'
    text = Path(view['path']).read_text()
    assert '.subckt cells__nand2_1 A1 A2 ZN VDD VNW VPW VSS' in text
    assert 'ad=0.3608p' in text and 'C21 ZN VPW 0.27964f' in text


def test_the_first_definition_of_a_cell_is_the_one_the_deck_simulates(tmp_path):
    extracted = write(tmp_path / 'x.spice', '.subckt cells__nand2_1 A1 A2 ZN VDD VNW VPW VSS\n'
                      + EXTRACTED_NAND2.split('\n', 3)[3])
    schematic = write(tmp_path / 's.spice', SCHEMATIC)
    volts = {'VDD': 5.0, 'VNW': 5.0, 'VSS': 0.0, 'VPW': 0.0}
    pst.fold_subckts([extracted, schematic], volts, 'VDD', 'VSS', tmp_path / 'f.spice')
    text = (tmp_path / 'f.spice').read_text()
    assert text.lower().count('.subckt cells__nand2_1 ') == 1
    assert 'ad=0.3608p' in text and 'X_i_1 net_0' not in text
    assert text.lower().count('.subckt cells__dffq_1 ') == 1       # the rest: unchanged


def test_a_pdk_that_declares_no_layout_or_rcfile_simulates_the_schematic_and_says_so(
        tmp_path, monkeypatch):
    root = _pdk(tmp_path)
    netlist = write(tmp_path / 'design/nl.v', NETLIST)
    monkeypatch.setattr(pst, '_docker', lambda *a, **k: pytest.fail('no extraction to run'))
    args = dict(pdk_dir=root / 'x', guest_root='/pdk', pdk='x', netlist=netlist,
                cell_sources=[root / 'x/libs.ref/cells/spice/cells.spice'],
                out_dir=tmp_path / 'out')
    view = pst.cell_view('img', tmp_path, [], config={}, **args)
    assert view == {'view': 'schematic', 'sources': [], 'reason': 'no CELL_GDS declared'}
    (root / 'x/libs.tech/librelane/config.tcl').write_text('set ::env(FP_TAPCELL_DIST) 20\n')
    view = pst.cell_view('img', tmp_path, [], config={'CELL_GDS': ['/pdk/x/g.gds']}, **args)
    assert view['view'] == 'schematic' and 'no MAGICRC declared' in view['reason']


def test_step_30_records_the_cell_view_it_simulated(tmp_path, monkeypatch):
    project = tmp_path / 'design'
    folder, _ = t106._stapostpnr(project)
    record = json.loads((project / postroute.STEP23_RECORD).read_text())
    record['judgment'] = {'worst_setup': {'corner': 'max_ss_125C_4v50'}}
    put(project / postroute.STEP23_RECORD, record)
    root = tmp_path / 'pdkroot'
    write(root / 'x/libs.ref/cells/lib/cells__ss_125C_4v50.lib', t106.LIBERTY)
    write(root / 'x/libs.ref/cells/spice/cells.spice',
          '.SUBCKT cells__dffq_1 D CLK Q VDD VNW VPW VSS\n'
          'X_i_0 Q D VSS VPW nfet_05v0 W=8.2e-07 L=6e-07\n.ENDS\n')
    write(root / 'x/libs.tech/ngspice/m.ngspice', t106.MODELS)
    write(root / 'x/libs.tech/librelane/config.tcl', CONFIG_TCL)
    config = json.loads((folder / 'config.json').read_text())
    config['CELL_SPICE_MODELS'] = ['/pdk/x/libs.ref/cells/spice/cells.spice']
    config['CELL_GDS'] = ['/pdk/x/libs.ref/cells/gds/cells.gds']
    put(folder / 'config.json', config)
    calls = []
    wps = t106._wps_edge(calls)
    dff = ('* NGSPICE file created from cells__dffq_1.ext\n'
           '.subckt cells__dffq_1 VDD VSS Q D CLK VNW VPW\n'
           'X0 Q D VSS VPW nfet_05v0 ad=0.2p pd=1.4u as=0.2p ps=1.4u w=0.82u l=0.6u\n'
           'C0 Q VSS 0.05f\n.ends\n')
    magic = _magic_edge(calls, {'cells__dffq_1': dff})

    def edge(image, project_, mounts, argv, cwd, docker='docker', env=None):
        return (magic if argv[0] == 'magic' else wps)(image, project_, mounts, argv, cwd, docker)
    monkeypatch.setattr(pst, '_docker', edge)
    doc = pst.run_step30(project, 'img', root, 'x', paths=1, simulators=('ngspice',))
    assert doc['arms']['ngspice']['cell_view'] == 'layout-extracted'
    folded = Path(doc['detail']['ngspice']['base']['cells']['path']).read_text()
    assert '.subckt cells__dffq_1 D CLK Q VDD VSS' in folded      # schematic order, folded rails
    assert 'ad=0.2p' in folded and 'W=8.2e-07' not in folded


# ------------------------------------------------ what the simulators read ---

def test_both_simulators_read_the_tool_subckts_with_device_lines_at_column_zero(tmp_path, monkeypatch):
    """`fold_subckts` indents parameterised device calls for OpenSTA's reader.
    MEASURED: Xyce reads an indented line as a continuation of the line
    before ("Unrecognized fields for device D37", a pad diode followed by an
    indented X line) and aborted every deck."""
    subckt = write(tmp_path / 'path_1.sp_1.subckt',
                   '.subckt pad PAD Y VDD VSS\nD37 VSS n0 diode_pd2nw_06v0 m=1.0 area=230.4e-15\n'
                   ' X38 n1 n0 VSS VSS nfet_06v0 m=1.0 w=3e-6 l=700e-9\n+ ad=1.32e-12\n.ends\n')
    deck = write(tmp_path / 'path_1.sp_1.sp',
                 '* Path\n.include "/g/models.sp"\n.include "path_1.sp_1.subckt"\n'
                 '.tran 1e-13 2e-08\n.print tran v(a)\nv1 a 0 4.5\n.end\n')
    seen = []
    monkeypatch.setattr(pst, '_docker', lambda image, project, mounts, argv, cwd, docker='docker',
                        env=None: seen.append(Path(argv[-1]).read_text())
                        or SimpleNamespace(returncode=0, stdout='', stderr=''))
    for simulator in ('xyce', 'ngspice'):
        pst._simulate(deck, simulator, 'img', tmp_path, [], step_s=1e-11)
        assert '.include "path_1.sp_1.run.subckt"' in seen[-1], simulator
        assert '.include "/g/models.sp"' in seen[-1]
    runnable = (tmp_path / 'path_1.sp_1.run.subckt').read_text()
    assert 'D37 VSS n0 diode_pd2nw_06v0 m=1.0 area=230.4e-15\nX38 n1 n0' in runnable
    assert '\n+ ad=1.32e-12\n' in runnable
    assert ' X38' in subckt.read_text()            # the reader's copy is untouched


XYCE_CSV = ('TIME,V(U_CORE\\/_417_/CLK),V(U_CORE\\/_417_/Q),V(U_CORE\\/_416_/D)\n'
            + ''.join(f'{i * 1e-10:.8e},{0.0 if i < 3 else 4.5},{4.5 if i < 5 else 0.0},'
                      f'{4.5 if i < 9 else 0.0}\n' for i in range(12)))


def test_the_xyce_arm_reads_the_csv_table_the_tool_deck_names(tmp_path):
    table = write(tmp_path / 'path_1.sp_1.csv', XYCE_CSV)
    deck = write(tmp_path / 'path_1.sp_1.sp',
                 f'.print tran format=csv file={table} v(u_core\\/_417_/CLK)\n'
                 'v2 u_core\\/_417_/VDD 0 4.500\n.tran 1e-13 2e-9\n')
    sta = {'startpoint': 'u_core/_417_', 'endpoint': 'u_core/_416_', 'endpoint_transition': 'fall',
           'start_row': {'inst': 'u_core/_417_', 'pin': 'u_core/_417_/Q', 'tr': 'v', 'cell': 'x'},
           'rows': [{'inst': 'u_core/_417_', 'pin': 'u_core/_417_/Q', 'tr': 'v', 'cell': 'x'},
                    {'inst': 'u_core/_416_', 'pin': 'u_core/_416_/D', 'tr': 'v', 'cell': 'x'}]}
    header = {'input_threshold_fall': 50.0, 'output_threshold_fall': 50.0}
    got = pst.measure(deck, 'xyce', sta, header)
    assert got['status'] == 'MEASURED'
    assert got['spice_ns'] == pytest.approx(0.4)
    assert pst.xyce_table(deck) == table


# ---------------------------------------------------------- the ratchets ---

def test_the_not_prose_claim_for_the_subckt_reader_is_falsifiable():
    """`subckt_devices` counts device lines by the netlist grammar's first
    letter; a comment spelling a denial is not a device and a denial token in
    a node name is not read. Zero counted devices never promotes a cell."""
    text = ('* no devices here, not extracted\n'
            '.subckt cells__a A Z VDD VSS\n'
            '* X_not a device: a comment\n'
            'X0 Z A not_denied VSS nfet_05v0 w=1u l=0.6u\n'
            'C0 Z VSS 0.1f\n'
            '.ends\n'
            '.subckt cells__b VDD VSS\n'
            '* nothing is NOT here\n'
            '.ends\n')
    assert pst.subckt_devices(text) == {'cells__a': (['A', 'Z', 'VDD', 'VSS'], 1),
                                        'cells__b': (['VDD', 'VSS'], 0)}


def test_the_pdk_magicrc_is_the_one_its_own_flow_config_declares(tmp_path):
    write(tmp_path / 'x/libs.tech/librelane/config.tcl', CONFIG_TCL)
    assert pst.pdk_magicrc(tmp_path / 'x', '/pdk', 'x') == (
        '/pdk/x/libs.tech/magic/x.magicrc', 'libs.tech/librelane/config.tcl:MAGICRC')
    assert pst.pdk_magicrc(tmp_path / 'y', '/pdk', 'y') is None
