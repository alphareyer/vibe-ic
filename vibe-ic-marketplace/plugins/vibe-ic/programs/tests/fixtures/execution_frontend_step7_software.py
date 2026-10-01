"""Bounded software proof fixture; not a production Step7 qualification.

Uses the unchanged Controller with an explicitly limited SDC sub-obligation
portfolio, the actual f199 factory/import boundary, and actual F2 transaction.
No native tool, resource lease issuance, PVT or whole-step PASS is represented.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import sys

PROGRAMS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROGRAMS))
import execution_modes as em
import execution_step_protocol as protocol
import execution_adapters_frontend as frontend
import execution_frontend_worker as worker
import phase3_one_shot_runner as rt
from _ppa import timing
import sdc_syntax_check as syntax
import sdc_validator_check as validator

STEP_IDS = ('7',)
GATES = ('step7_current_record', 'sdc_syntax_check')
OBJECTIVE = {'metric':'source_sdc_contract', 'direction':'min',
             'scope':'software SDC sub-obligation; no full Step7 qualification'}
PORTFOLIO = {'steps':[{'id':'7', 'mandatory_gate_programs':list(GATES),
                      'required_output_contract':['source_sdc_subobligation']}]}


def pdk(model: Path):
    # Neutral authored INPUT library. These unused fields disclose the absence
    # of physical views; this fixture never calls synthesis/PnR/signoff.
    return rt.PdkConfig(name='source_fixture', liberty=str(model), tech_lef='',
                        cell_lef='', cell_gds=None, site='', drc_deck=None)


def grade(project: Path, model: Path, *, crosscheck: bool) -> dict:
    record, why = timing.read_step7_asic_sdc(rt, project, 'fixture', pdk(model))
    audit = syntax.audit(str(project))
    gates = {'step7_current_record':'PASS' if record else 'FAIL',
             'sdc_syntax_check':'PASS' if audit.passed else 'FAIL'}
    detail = {'record_reason':why, 'record':record, 'syntax':asdict(audit)}
    if crosscheck:
        verdict = validator.evaluate(project, project/'phase1/generated_docs/L8_TIMING_WAVEFORM.json')
        gates['sdc_validator_check'] = {0:'PASS',1:'FAIL',2:'NOT_MEASURED'}[verdict.rc]
        detail['validator'] = {'rc':verdict.rc, 'lines':verdict.lines, 'report':verdict.report}
    status = 'FAIL' if 'FAIL' in gates.values() else (
        'PASS' if all(v == 'PASS' for v in gates.values()) else 'NOT_MEASURED')
    return {'status':status, 'gates':gates, 'detail':detail}


def validate(outputs: Path, binding: dict) -> em.Evidence:
    request = json.loads((outputs.parent/'inputs/request.json').read_text())
    semantic = grade(outputs/'project', Path(request['model']), crosscheck=False)
    producer = json.loads((outputs/'producer.json').read_text())
    if producer['binding'] != binding:
        raise em.Refusal('SOURCE_FIXTURE_BINDING_CHANGED', str(outputs))
    hashes = worker.output_manifest(outputs)
    log = outputs.parent/'step7-software.stdout'
    observed = [json.loads(line[len('F2_SOFTWARE_OUTPUTS '):])
                for line in log.read_text().splitlines() if line.startswith('F2_SOFTWARE_OUTPUTS ')]
    if observed != [hashes]:
        raise em.Refusal('SOURCE_FIXTURE_OUTPUTS_CHANGED',str(outputs))
    return em.Evidence(binding, semantic['status'], semantic['gates'], hashes,
                       detail=frontend.stable(semantic))


def prepare(request: protocol.StepRequest) -> protocol.PreparedStep:
    project = request.project
    parameters = dict(request.parameters)
    roots = worker.input_roots(project, parameters)
    files, population = frontend.lexical_tree(project, roots=roots)
    external = []
    for label, path in parameters['input_roots'].items():
        if not Path(path).is_relative_to(project):
            extra, pop = frontend.lexical_tree(Path(path), 'external/'+label)
            files.update(extra)
            external.append(('external/'+label,str(path),frontend.stable(pop)))
    model = Path(parameters['model'])
    # The model is itself a frozen read-only packet outside the project.
    if model.stat().st_mode & 0o222:
        raise em.Refusal('SOURCE_FIXTURE_MODEL_NOT_FROZEN',str(model))
    request.record.write_text(json.dumps(protocol.json_parameters(parameters),sort_keys=True)+'\n')
    request.record.chmod(0o444)
    files['request.json'] = request.record
    context = frontend.FrontendContext('7',request.source_sha,files,OBJECTIVE,GATES,
        project=project,roots=roots,population=frontend.stable(population),
        sources=tuple(sorted(request.source_files.items())),
        resources=((str(request.record),em.digest(request.record)),),
        external_populations=tuple(external))
    registry = em.Registry()
    registry.register(em.Adapter('step7-software','vibeic','7',request.source_sha,
        request.source_files,'source-owned Python SDC emitter',('vibeic-constraints',),
        (em.Component('step7-software',(str(Path(sys.executable).resolve()),str(Path(__file__).resolve()),
            '--inputs','{inputs}','--outputs','{outputs}'),30),),
        validate,('producer.json',),OBJECTIVE,cpus=1,ram_mb=512,
        qualification_evidence='actual emitted SDC/current-record/syntax consumer, scoped software fixture only',
        own_no_tool_reason='This design-intent SDC author is the existing own Python producer',
        output_contract={'source_sdc_subobligation':('producer.json',)}))
    return protocol.PreparedStep(context,registry,consume,
        adoption_paths=('phase2/stage2/constraints',))


def consume(project: Path, context: frontend.FrontendContext, controller: em.Controller,
            run: Path, adopted: dict) -> dict:
    generation = adopted['selected_generation']
    controller._generation_current(generation)
    binding = context.binding()
    if generation['binding'] != binding:
        raise em.Refusal('SOURCE_FIXTURE_GENERATION_UNBOUND',str(run))
    selected = Path(generation['directory'])
    producer = json.loads((selected/'producer.json').read_text())
    copied = producer['canonical_outputs']
    for rel, sha in copied.items():
        if generation['outputs'].get('project/'+rel) != sha:
            raise em.Refusal('SOURCE_FIXTURE_OUTPUT_UNISSUED',rel)
    model = Path(json.loads(context.inputs['request.json'].read_text())['model'])
    detail = {}
    def current():
        controller._generation_current(generation)
        if context.binding() != binding:
            raise em.Refusal('SOURCE_FIXTURE_INPUT_CHANGED',str(project))
    def before():
        measured = grade(selected/'project', model, crosscheck=True)
        detail.update(measured)
        worker.write(run/'software-consumer.json', measured)
        worker.require_consumer_pass(measured,adopted['evidence']['verdict'])
        current()
    transaction, warning = worker.publish_selected(project,selected/'project',copied,
        run,context,binding,before,current)
    return protocol.imported_result(step_id='7',source_sha=context.source_sha,
        selected_generation=generation,binding=binding,copied=copied,
        primary_gates=detail['gates'],design_verdict=detail['status'],
        consumer_detail=dict(detail,transaction=str(transaction),cleanup_warning=warning,
            scope=OBJECTIVE['scope']))


def component(inputs: Path, outputs: Path) -> int:
    parameters = json.loads((inputs/'request.json').read_text())
    project = outputs/'project'
    shutil.copytree(inputs/'project',project)
    authored = timing.emit_step7_asic_sdc(rt,project,'fixture',pdk(Path(parameters['model'])),container='')
    canonical = {str(path.relative_to(project)):em.digest(path)
        for path in (project/'phase2/stage2/constraints').rglob('*') if path.is_file()}
    worker.write(outputs/'producer.json',{
        'binding':json.loads(os.environ['VIBEIC_EXECUTION_BINDING']),
        'raw_producer':authored,'canonical_outputs':canonical,
        'scope':OBJECTIVE['scope'],'native_verdict':'NOT_MEASURED'})
    print('F2_SOFTWARE_OUTPUTS '+frontend.stable(worker.output_manifest(outputs)),flush=True)
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--inputs',type=Path,required=True)
    parser.add_argument('--outputs',type=Path,required=True)
    args = parser.parse_args()
    raise SystemExit(component(args.inputs,args.outputs))
