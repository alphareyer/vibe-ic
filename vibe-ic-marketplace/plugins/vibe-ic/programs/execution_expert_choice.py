"""Step9 external expert ingress; BLOCKING when current evidence is absent.

The existing absolute choice-file route is the adviser transport. This module
never invents a decision, launches a tool, ranks candidates, or authenticates an
AI by its name. Reviewer authorization is an explicit owner-cited input. The
live controller retains issued authority, revalidation and final adoption.
"""
from __future__ import annotations

import os as _os
import sys as _sys

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))

from dataclasses import asdict, is_dataclass
import argparse
import json
from pathlib import Path
import time
import uuid

from _atomic_artefact import write_json
import execution_modes as em


def _regular(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
        raise em.Refusal('EXPERT_ARTIFACT_NOT_REGULAR', str(path))
    return path


def _citation(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) != {'path', 'sha256'}:
        raise em.Refusal('EXPERT_CITATION_INCOMPLETE', repr(value))
    path = _regular(value['path'])
    if em.digest(path) != value['sha256']:
        raise em.Refusal('EXPERT_CITATION_CHANGED', str(path))
    return dict(value)


def _typed(value):
    if is_dataclass(value):
        return _typed(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        if any(not isinstance(k, str) for k in value):
            raise em.Refusal('EXPERT_PARAMETERS_UNTYPED', repr(value))
        return {k: _typed(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_typed(v) for v in value]
    if value is None or type(value) in (str, int, float, bool):
        return value
    raise em.Refusal('EXPERT_PARAMETERS_UNTYPED', type(value).__name__)


def _snapshot(controller, context, run: Path, parameters: dict) -> dict:
    """Read only authentic, current issued receipts; never infer eligibility."""
    run = run.resolve()
    plan = json.loads(_regular(run/'plan.json').read_text())
    issued = em._issued(_regular(run/'issued-plan.json'))
    binding = context.binding()
    if plan != issued or plan['binding'] != binding or plan['run_root'] != str(run):
        raise em.Refusal('EXPERT_REQUEST_UNBOUND', str(run))
    if context.step_id != '9':
        raise em.Refusal('EXPERT_STEP_NOT_OWNED', context.step_id)
    top = parameters.get('top')
    if not isinstance(top, str) or not top or top != context.objective.get('top') or parameters.get('pdk') is None:
        raise em.Refusal('EXPERT_CURRENT_TOP_PDK_MISSING', 'Supply the current typed top/PdkConfig; top must match objective.')
    artifacts = {}

    def add(path, expected=None):
        path = _regular(path)
        actual = em.digest(path)
        if expected is not None and actual != expected:
            raise em.Refusal('EXPERT_CURRENT_ARTIFACT_CHANGED', str(path))
        artifacts[str(path)] = actual

    inputs = {}
    for name, path in context.inputs.items():
        add(Path(path), binding['inputs'][name])
        inputs[name] = str(path)
    sources = dict(getattr(context, 'sources', None) or getattr(context, 'source_files', None) or {})
    candidates = {}
    adapters = {a.arm_id: a for a in controller.registry.adapters(context.step_id)}
    for arm_id in plan['arms']:
        receipt_path = run/arm_id/'receipt.json'
        receipt = json.loads(_regular(receipt_path).read_text())
        add(receipt_path)
        candidates[arm_id] = dict(status=receipt['status'], receipt_path=str(receipt_path),
                                 receipt_sha256=em.digest(receipt_path), receipt=receipt)
        if receipt['status'] != 'ELIGIBLE':
            continue
        arm = adapters[arm_id]
        controller._execution_authority(run, plan, receipt, arm)
        controller._current_admission(context, plan, arm)
        controller._eligible(receipt, context, arm)
        if asdict(arm.validate(Path(receipt['output_root']), binding)) != receipt['evidence']:
            raise em.Refusal('EVIDENCE_CHANGED', arm_id)
        sources.update(arm.source_files)
        add(run/arm_id/'issued-completion.json')
        for name, expected in receipt['evidence']['outputs'].items():
            add(Path(receipt['output_root'])/name, expected)
        for process in receipt['processes']:
            for channel in ('stdout', 'stderr'):
                add(process[channel], process[channel+'_sha256'])
    for path, expected in sources.items():
        add(path, expected)
    add(run/'plan.json')
    add(run/'issued-plan.json')
    if not any(c['status'] == 'ELIGIBLE' for c in candidates.values()):
        raise em.Refusal('EXPERT_NO_ELIGIBLE_ISSUED_CANDIDATE', str(run))
    return dict(step_id='9', run=str(run), run_id=plan['run_id'], source_sha=context.source_sha,
                binding=binding, current_parameters=_typed(parameters), objective=_typed(context.objective),
                inputs=inputs, source_files=sources, candidates=candidates,
                primary_gates=list(context.required_gates), issued_plan_sha256=em.digest(run/'issued-plan.json'),
                artifacts=artifacts)


def publish_request(controller, context, run: Path, parameters: dict, destination: Path,
                    choice_destination: str | None, wait_s: int) -> dict:
    """Publish program-first current evidence for the existing external agent."""
    if type(wait_s) is not int or not 0 <= wait_s <= 3600:
        raise em.Refusal('INVALID_CHOICE_WAIT', repr(wait_s))
    if choice_destination is not None:
        target = Path(choice_destination)
        if not target.is_absolute() or any(p.is_symlink() for p in (target, *target.parents)):
            raise em.Refusal('EXPERT_CHOICE_DESTINATION_UNSAFE', str(target))
        if target == destination:
            raise em.Refusal('EXPERT_CHOICE_REQUEST_COLLISION', str(target))
    request = dict(schema=1, review_id=uuid.uuid4().hex,
                   snapshot=_snapshot(controller, context, run, parameters),
                   choice_destination=choice_destination, deadline_wait_s=wait_s,
                   requested_review='Read exact INPUT, current producer outputs and primary gates; choose an eligible issued receipt with independent reviewer evidence. No automatic ranking/adoption.',
                   required_decision_fields=['arm_id', 'binding', 'receipt_sha256', 'rationale', 'reviewer',
                                             'run_id', 'review_request_sha256', 'reviewer_evidence'])
    if destination.exists() or destination.is_symlink():
        raise em.Refusal('EXPERT_REQUEST_ALREADY_EXISTS', str(destination))
    write_json(destination, request)
    return request


def consume_decision(controller, context, run: Path, parameters: dict,
                     request_path: Path, choice_path: Path) -> dict:
    """Validate current owner-cited human/AI decision; retain all attribution."""
    request = json.loads(_regular(request_path).read_text())
    request_sha = em.digest(request_path)
    snapshot = _snapshot(controller, context, run, parameters)
    if request['snapshot'] != snapshot:
        raise em.Refusal('EXPERT_REQUEST_STALE', str(request_path))
    if str(choice_path) != request['choice_destination']:
        raise em.Refusal('EXPERT_CHOICE_DESTINATION_MISMATCH', str(choice_path))
    choice = json.loads(_regular(choice_path).read_text())
    if (choice.get('binding') != snapshot['binding'] or choice.get('run_id') != snapshot['run_id']
            or choice.get('review_request_sha256') != request_sha):
        raise em.Refusal('EXPERT_DECISION_STALE', str(choice_path))
    candidate = snapshot['candidates'].get(choice.get('arm_id'))
    if not candidate or candidate['status'] != 'ELIGIBLE':
        raise em.Refusal('AI_CHOICE_INELIGIBLE', str(choice.get('arm_id')))
    if choice.get('receipt_sha256') != candidate['receipt_sha256']:
        raise em.Refusal('AI_RECEIPT_DIGEST_MISMATCH', str(choice.get('arm_id')))
    owner = parameters.get('expert_reviewer')
    reviewer = choice.get('reviewer_evidence')
    if not isinstance(owner, dict) or not isinstance(reviewer, dict):
        raise em.Refusal('EXPERT_REVIEWER_EVIDENCE_MISSING', 'Need owner-cited current independent human/AI reviewer and original decision evidence.')
    if (owner.get('kind') not in ('human', 'ai') or not isinstance(owner.get('actor_id'), str)
            or not owner['actor_id'].strip() or owner.get('independent_from_producer') is not True
            or reviewer.get('actor_id') != owner['actor_id'] or reviewer.get('kind') != owner['kind']
            or reviewer.get('independent_from_producer') is not True):
        raise em.Refusal('EXPERT_REVIEWER_NOT_INDEPENDENT', repr(reviewer))
    authorization = _citation(owner.get('authorization'))
    if reviewer.get('authorization') != authorization:
        raise em.Refusal('EXPERT_REVIEWER_AUTHORIZATION_UNBOUND', repr(reviewer))
    evidence = _citation(reviewer.get('decision_evidence'))
    if Path(evidence['path']) in (choice_path, request_path) or Path(evidence['path']).is_relative_to(run):
        raise em.Refusal('EXPERT_DECISION_EVIDENCE_SELF_CITED', evidence['path'])
    if reviewer.get('artifacts_reviewed') != snapshot['artifacts']:
        raise em.Refusal('EXPERT_REVIEW_ARTIFACTS_UNBOUND', 'Decision must cite the exact current INPUT/source/receipt/output/gate population.')
    cited = json.loads(Path(evidence['path']).read_text())
    fields = ('arm_id', 'binding', 'receipt_sha256', 'rationale', 'reviewer', 'run_id', 'review_request_sha256')
    if any(cited.get(k) != choice.get(k) for k in fields) or cited.get('review_id') != request['review_id']:
        raise em.Refusal('EXPERT_ORIGINAL_DECISION_UNBOUND', evidence['path'])
    if any(not isinstance(choice.get(k), str) or not choice[k].strip() for k in ('rationale', 'reviewer')):
        raise em.Refusal('EXPERT_DECISION_REASON_MISSING', str(choice_path))
    if (_snapshot(controller, context, run, parameters) != snapshot
            or em.digest(request_path) != request_sha):
        raise em.Refusal('EXPERT_REQUEST_STALE', str(request_path))
    # No new expert/engine/native credit is inferred here. The explicit owner
    # citation is the trust boundary, and the original choice remains intact.
    return choice


def request_choice(controller, context, run: Path, parameters: dict, request_path: Path,
                   choice_destination: str | None, wait_s: int) -> dict:
    publish_request(controller, context, run, parameters, request_path, choice_destination, wait_s)
    if choice_destination is None:
        raise em.Refusal('EXPERT_CHOICE_DESTINATION_MISSING', str(request_path))
    target = Path(choice_destination)
    deadline = time.monotonic()+wait_s
    while not target.exists() and not target.is_symlink() and time.monotonic() < deadline:
        time.sleep(.1)
    if not target.exists() and not target.is_symlink():
        raise em.Refusal('EXPERT_CURRENT_DECISION_MISSING', str(request_path))
    choice = consume_decision(controller, context, run, parameters, request_path, target)
    write_json(request_path.parent/'expert-choice-ingress.json', dict(status='CURRENT_OWNER_CITED_DECISION',
        run_id=choice['run_id'], request_path=str(request_path), request_sha256=em.digest(request_path),
        choice_path=str(target), choice_sha256=em.digest(target), reviewer_evidence=choice['reviewer_evidence'],
        attribution='preserved external owner citation; no expertise inferred from rc0'))
    return choice


def submit_decision(request_path: Path, original_decision: Path) -> dict:
    """Caller: translate a real external decision into the existing choice route.

    This external-process ingress checks the current artifact hashes. Only the
    original live controller can validate issuance and adopt; no authority or
    independent expertise is manufactured by this translation.
    """
    request_path, original_decision = _regular(request_path), _regular(original_decision)
    request = json.loads(request_path.read_text())
    snapshot = request['snapshot']
    cited = json.loads(original_decision.read_text())
    for path, expected in snapshot['artifacts'].items():
        _citation(dict(path=path, sha256=expected))
    fields = ('arm_id', 'binding', 'receipt_sha256', 'rationale', 'reviewer', 'run_id', 'review_request_sha256')
    if (cited.get('review_id') != request['review_id'] or cited.get('run_id') != snapshot['run_id']
            or cited.get('binding') != snapshot['binding']
            or cited.get('review_request_sha256') != em.digest(request_path)):
        raise em.Refusal('EXPERT_DECISION_STALE', str(original_decision))
    candidate = snapshot['candidates'].get(cited.get('arm_id'))
    if not candidate or candidate['status'] != 'ELIGIBLE':
        raise em.Refusal('AI_CHOICE_INELIGIBLE', str(cited.get('arm_id')))
    if cited.get('receipt_sha256') != candidate['receipt_sha256']:
        raise em.Refusal('AI_RECEIPT_DIGEST_MISMATCH', str(cited.get('arm_id')))
    owner = snapshot['current_parameters'].get('expert_reviewer')
    reviewer = cited.get('reviewer_evidence')
    if not isinstance(owner, dict) or not isinstance(reviewer, dict):
        raise em.Refusal('EXPERT_REVIEWER_EVIDENCE_MISSING', str(original_decision))
    if (owner.get('kind') not in ('human', 'ai') or not isinstance(owner.get('actor_id'), str)
            or not owner['actor_id'].strip() or owner.get('independent_from_producer') is not True
            or reviewer.get('actor_id') != owner['actor_id'] or reviewer.get('kind') != owner['kind']
            or reviewer.get('independent_from_producer') is not True):
        raise em.Refusal('EXPERT_REVIEWER_NOT_INDEPENDENT', repr(reviewer))
    if reviewer.get('authorization') != _citation(owner.get('authorization')):
        raise em.Refusal('EXPERT_REVIEWER_AUTHORIZATION_UNBOUND', str(original_decision))
    if reviewer.get('artifacts_reviewed') != snapshot['artifacts']:
        raise em.Refusal('EXPERT_REVIEW_ARTIFACTS_UNBOUND', str(original_decision))
    if any(not isinstance(cited.get(k), str) or not cited[k].strip() for k in ('rationale', 'reviewer')):
        raise em.Refusal('EXPERT_DECISION_REASON_MISSING', str(original_decision))
    destination = request.get('choice_destination')
    if not destination:
        raise em.Refusal('EXPERT_CHOICE_DESTINATION_MISSING', str(request_path))
    target = Path(destination)
    if not target.is_absolute() or any(p.is_symlink() for p in (target, *target.parents)):
        raise em.Refusal('EXPERT_CHOICE_DESTINATION_UNSAFE', str(target))
    if (target.exists() or original_decision in (request_path, target)
            or original_decision.is_relative_to(Path(snapshot['run']))):
        raise em.Refusal('EXPERT_CHOICE_DESTINATION_OCCUPIED', str(target))
    choice = {k: cited[k] for k in fields}
    choice['reviewer_evidence'] = {**reviewer, 'decision_evidence':
                                  dict(path=str(original_decision), sha256=em.digest(original_decision))}
    write_json(target, choice)
    return choice


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', required=True, type=Path)
    parser.add_argument('--decision', required=True, type=Path)
    args = parser.parse_args()
    try:
        selected = submit_decision(args.request, args.decision)
        print(json.dumps(dict(status='CHOICE_SUBMITTED_PENDING_LIVE_ADOPTION',
                              arm_id=selected['arm_id'], run_id=selected['run_id'])))
    except (em.Refusal, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps(dict(status='REFUSED', reason=getattr(exc, 'code', 'EXPERT_INVALID_DECISION'), detail=str(exc))))
        raise SystemExit(2)
