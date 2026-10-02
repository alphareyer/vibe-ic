"""Row worker invoked by Registry components with explicit argv."""
import argparse, json
from pathlib import Path
from execution_frontend_providers import ROW_CONTRACTS

def _envelope(step_id, symbol, result, output):
    out = Path(output) / 'canonical.json'; out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({'schema':'frontend_worker_output/1','step_id':step_id,
                               'producer':symbol,'result':result}, default=str, sort_keys=True)+'\n')
    return out

def run_row(step_id, project, output):
    project=Path(project); output=Path(output); output.mkdir(parents=True, exist_ok=True)
    # Explicit dispatch is intentional: each route owns its callable signature and
    # output staging.  No generic fn(project) probing is permitted.
    if step_id == 'D1':
        from phase1_doc_presence_check import check
        docs = project / 'input' / 'docs'
        result = check(docs, strict=False)
        report = output / 'reports' / 'phase1' / 'doc_presence.json'; report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(result, default=str, sort_keys=True)+'\n')
        return _envelope(step_id, 'phase1_doc_presence_check.check', {'artifact':str(report.relative_to(output)),'verdict':'NOT_MEASURED'}, output)
    if step_id == 'P0':
        from p0_tool_frontend_check import check
        return _envelope(step_id, 'p0_tool_frontend_check.check', check(project), output)
    if step_id == '0.5ic':
        raise RuntimeError('NOT_IMPLEMENTED: submission_template_ingest.main requires an issued template manifest')
    if step_id == '1':
        raise RuntimeError('NOT_IMPLEMENTED: design_one_shot_runner.step_rtl_gen requires canonical L-doc inputs')
    if step_id == '2':
        raise RuntimeError('NOT_IMPLEMENTED: frontend and equivalence tools are not admitted in source-only mode')
    if step_id == '3':
        raise RuntimeError('NOT_IMPLEMENTED: CDC netlist producer requires generated RTL/netlist')
    if step_id == '4':
        raise RuntimeError('NOT_IMPLEMENTED: testbench producers require canonical RTL and reference artifacts')
    if step_id == '5':
        raise RuntimeError('NOT_IMPLEMENTED: formal producer requires tool admission')
    if step_id == '6':
        raise RuntimeError('NOT_MEASURED: FPGA hardware/toolchain is unavailable')
    if step_id in {'7','8','10','11','FS1','DT1','12','13','DT2','DT3'}:
        raise RuntimeError('NOT_IMPLEMENTED: '+step_id+' requires its canonical upstream artifact and tool admission')
    raise RuntimeError('NOT_IMPLEMENTED: explicit producer unavailable for '+step_id)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--step',required=True); ap.add_argument('--inputs',required=True); ap.add_argument('--outputs',required=True); a=ap.parse_args()
    run_row(a.step, Path(a.inputs), Path(a.outputs))
if __name__=='__main__': main()
