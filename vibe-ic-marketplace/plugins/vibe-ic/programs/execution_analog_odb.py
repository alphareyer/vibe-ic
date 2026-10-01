"""Native OpenDB export, invoked only by the admitted M2 OpenROAD worker.

UPF domains/voltages/elements come from OpenROAD's parsed database. Physical
cell/net/circuit identity is exported separately from Yosys's logical graph.
"""
import json
import os
from pathlib import Path
import sys

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))


def export(block):
    domains = {}
    for domain in block.getPowerDomains():
        domains[domain.getName()] = {'voltage': domain.getVoltage(),
            'elements': list(domain.getElements()),
            'power_switches': [s.getName() for s in domain.getPowerSwitches()]}
    cells = {inst.getName(): inst.getMaster().getName() for inst in block.getInsts()}
    nets = {}
    for net in block.getNets():
        nets[net.getName()] = {'signal_type': str(net.getSigType()), 'terminals': [
            {'instance': term.getInst().getName(), 'pin': term.getMTerm().getName(),
             'direction': str(term.getIoType())} for term in net.getITerms()]}
    return {'top': block.getName(), 'domains': domains, 'cells': cells, 'nets': nets}


if __name__ == '__main__':
    import odb
    database = odb.dbDatabase.create()
    odb.read_db(database, os.environ['VIBEIC_F5_ODB_INPUT'])
    block = database.getChip().getBlock()
    Path(os.environ['VIBEIC_F5_ODB_OUTPUT']).write_text(json.dumps(export(block), indent=2) + '\n')
