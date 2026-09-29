"""Finite native proof launcher; no flow, full-run, or existing writer mutation."""
import hashlib, json, os, subprocess, sys, time
from pathlib import Path

out=Path('/evidence')
arm=sys.argv[1] if len(sys.argv)>1 else 'current'
rtl=Path('/subject/phase2/stage1/rtl')
io=Path('/subject/phase3/stage3/pnr/chip_top_io.v')
model=Path('/foss/pdks/gf180mcuD/libs.ref/gf180mcu_fd_io/verilog/gf180mcu_fd_io.v')
cell=Path('/foss/pdks/gf180mcuD/libs.ref/gf180mcu_fd_sc_mcu7t5v0/verilog')
paths=[*sorted(rtl.glob('*.v')),io,model,cell/'primitives.v',cell/'gf180mcu_fd_sc_mcu7t5v0.v',Path('/probe/reset_pad_probe.sv')]
commands=[['iverilog','-g2012','-DFUNCTIONAL','-s','reset_pad_probe','-o',str(out/(arm+'.vvp')),*map(str,paths)],['vvp',str(out/(arm+'.vvp')),'+DUMPFILE='+str(out/(arm+'.vcd'))]]
if arm=='unsupported_idle_obligation':
    commands[0].insert(3,'-DREQUIRE_IDLE_WDATA_KNOWN')
receipt=dict(arm=arm,pid=os.getpid(),start_unix=time.time(),source_files=[dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in paths],commands=commands,returns=[])
for index,cmd in enumerate(commands):
    with (out/(arm+('.build.log' if index==0 else '.run.log'))).open('w') as log:
        child=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT)
        receipt.setdefault('child_pids',[]).append(child.pid)
        rc=child.wait();receipt['returns'].append(rc)
    if rc: break
receipt['finish_unix']=time.time()
receipt['terminal']=True
receipt['outputs']=[dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in out.glob(arm+'.*') if p.suffix in ['.vcd','.vvp'] or p.name.endswith('.log')]
(out/(arm+'.receipt.json')).write_text(json.dumps(receipt,indent=2)+'\n')
print('NATIVE_TERMINAL',arm,receipt['returns'])
sys.exit(rc)
