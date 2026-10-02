"""Row worker invoked by Registry components with explicit argv."""
import argparse, importlib, json
from pathlib import Path
from execution_frontend_providers import ROUTES, ROW_CONTRACTS

def run_row(step_id, project):
    project=Path(project); route=ROUTES[step_id]
    # Resolve the canonical producer symbol; do not invent artifacts.
    for symbol in route:
        mod_name, fn_name = symbol.rsplit('.',1)
        try: fn=getattr(importlib.import_module(mod_name), fn_name)
        except (ImportError, AttributeError): continue
        try:
            result=fn(project)
        except TypeError:
            continue
        return {'step_id':step_id,'producer':symbol,'result':result}
    raise RuntimeError('NOT_IMPLEMENTED: no current-main producer callable available')

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--step',required=True); ap.add_argument('--project',required=True); ap.add_argument('--out',required=True); a=ap.parse_args()
    value=run_row(a.step, Path(a.project)); out=Path(a.out); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(value,default=str,sort_keys=True)+'\n')
if __name__=='__main__': main()
