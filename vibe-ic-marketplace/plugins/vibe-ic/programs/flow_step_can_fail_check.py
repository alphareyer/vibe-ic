"""Ratchet flow steps whose declared gate cannot fail on output content.

All formerly recorded weak steps now have blocking content checks. The empty
baseline is intentional: new or regressed weak gates fail this source audit.
Runtime behavior still requires mutation tests through the actual evaluator.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

try:
    import yaml
except ImportError:                                   # pragma: no cover
    yaml = None

BLOCKING = {"program_exit_zero", "files_exist", "json_field_true"}
NON_BLOCKING = {"advisory_program_exit_zero", "optional_program_exit_zero"}
COMBINATORS = {"all_of", "any_of"}

# Recorded weak-step baseline. It may only shrink and is now empty.
BASELINE: Dict[str, str] = {}


def criteria(gate) -> Set[str]:
    """Every criterion kind this gate declares, with combinators walked."""
    out: Set[str] = set()
    if isinstance(gate, dict):
        for k, v in gate.items():
            if k in COMBINATORS and isinstance(v, (list, tuple)):
                for x in v:
                    out |= criteria(x)
            else:
                out.add(k)
    elif isinstance(gate, (list, tuple)):
        for x in gate:
            out |= criteria(x)
    return out


def load_steps(path: Path) -> Optional[List[dict]]:
    if yaml is None:
        return None
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    steps: List[dict] = []

    def walk(o):
        if isinstance(o, dict):
            if "id" in o and "name" in o:
                steps.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(doc)
    # stage containers are not steps; they carry no gate of their own
    return [s for s in steps if not str(s.get("id", "")).startswith("stage")]


def classify(step: dict) -> Tuple[bool, str]:
    """(can this step fail on content, why not)."""
    kinds = criteria(step.get("gate"))
    if not kinds:
        return False, "no gate key at all"
    blocking = kinds & BLOCKING
    if not blocking:
        only = "/".join(sorted(kinds & NON_BLOCKING)) or "/".join(sorted(kinds))
        return False, f"only {only}"
    if blocking == {"files_exist"}:
        return False, "files_exist only — fails on absence, never on content"
    return True, ""


def main(argv=None) -> int:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--flow", type=Path,
                    default=here.parent / "flow" / "phase1_phase2_phase3.yaml")
    ap.add_argument("--json", type=Path, default=None)
    a = ap.parse_args(argv)

    if yaml is None:
        print("flow_step_can_fail_check: rc=2 NOT CHECKED — pyyaml unavailable")
        return 2
    steps = load_steps(a.flow)
    if not steps:
        print(f"flow_step_can_fail_check: rc=2 NOT CHECKED — no steps in {a.flow}")
        return 2

    weak: Dict[str, str] = {}
    for s in steps:
        ok, why = classify(s)
        if not ok:
            weak[str(s["id"])] = why

    names = {str(s["id"]): str(s.get("name", ""))[:64] for s in steps}
    present = set(names)
    new = {k: v for k, v in weak.items() if k not in BASELINE}
    # A baseline entry is FIXED only when its step is still here and now has a
    # criterion that can fail. An entry whose step is ABSENT is a different
    # thing — the step was renamed or removed — and conflating the two makes
    # this gate fire on any flow that does not happen to contain all eight,
    # which is every synthetic fixture and every partial flow.
    fixed = [k for k in BASELINE if k in present and k not in weak]
    gone = [k for k in BASELINE if k not in present]

    rec = {"steps": len(steps), "cannot_fail_on_content": weak,
           "baseline": BASELINE, "new": new, "now_fixed": fixed,
           "baseline_steps_absent": gone}
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")

    if new:
        print(f"flow_step_can_fail_check: FAIL — {len(new)} step(s) gained a gate "
              f"that cannot fail on content:")
        for k, v in sorted(new.items()):
            print(f"    step {k:<4} {names.get(k, '')}")
            print(f"             {v}")
        print("    A step whose gate cannot fail certifies nothing. Give it a "
              "criterion that can, or record it in BASELINE with the reason.")
        return 1

    if fixed:
        print(f"flow_step_can_fail_check: FAIL — {len(fixed)} baseline entr(ies) "
              f"now have a failing criterion: {', '.join(sorted(fixed))}")
        print("    Good news, and the baseline must shrink to match — it exists to "
              "record what is still weak, not to keep saying so after it is fixed.")
        return 1

    if gone:
        print(f"flow_step_can_fail_check: note — {len(gone)} baseline step(s) not "
              f"present in this flow: {', '.join(sorted(gone))}")
    print(f"flow_step_can_fail_check: PASS — {len(steps)} step(s); "
          f"{len(steps) - len(weak)} can fail on content, {len(weak)} cannot and "
          f"are the recorded baseline (may only shrink)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
