"""Shared implementation of the existing generated compliance cases.

These test report contracts, not EDA execution. Receipt-required schemas retain
FAIL/NOT_MEASURED without receipts. Thin explicit wrappers preserve pytest IDs.
The cases were mechanically factored from the c733 generated template; no
assertions, verdict directions, skips, or fixture limitations were changed.
"""
import json
from pathlib import Path
import subprocess
import sys

import skill_compliance_check as scc
import synthetic_fixture_limits as LIMITS
from pattern_satisfier import pattern_to_satisfier

class GeneratedCompliance:

    def __init__(self, skill_dir):
        self.skill_name = skill_dir.name
        self.compliance = skill_dir / 'compliance.yaml'
        self.driver = Path(__file__).resolve().with_name('skill_compliance_check.py')
        assert self.compliance.is_file(), f'compliance.yaml missing: {self.compliance}'
        assert self.driver.is_file(), f'driver missing: {self.driver}'

    def load_requirements(self):
        return scc._load_yaml(self.compliance)

    def build_good_output(self, compliance):
        """Produce text that contains a satisfier for every required pattern.

    #2057 — the satisfier is `_shared/pattern_satisfier.py`, which walks the
    regex's own parse tree and verifies its output with `re.fullmatch` before
    returning it. It replaces a chain of `re.sub` rewrites over the pattern
    TEXT that emitted things like `## (?:Output)` and a literal `{7,40}`, and
    therefore did not satisfy the pattern it was built from on 53 of the 69
    skills shipping this file.
    """
        parts = ['# Auto-built good output\n']
        for r in compliance.get('requirements', []) or []:
            pat = r['pattern']
            sat = self._pattern_to_satisfier(pat)
            parts.append(f"<!-- sat for {r['id']} -->\n{sat}\n")
        return '\n'.join(parts)

    def _pattern_to_satisfier(self, pat: str) -> str:
        """Delegates to `_shared/pattern_satisfier.py` (#2057).

    This used to be ~25 lines of `re.sub` rewriting the pattern's SURFACE
    text: it kept the `?:` of a non-capturing group, copied a `{m,n}`
    repetition through verbatim, left a literal `.?` / `.*` in the output and
    stripped every backslash at the end — turning `\x08` into the letter `b`
    and leaving an inline `(?i)` as visible text. The shared module reads the
    regex's STRUCTURE instead and checks its own answer with `re.fullmatch`
    before returning it.
    """
        return pattern_to_satisfier(pat)

    def run_driver(self, tmp_path, text):
        report = tmp_path / 'out.md'
        report.write_text(text)
        out_json = tmp_path / 'audit.json'
        res = subprocess.run([sys.executable, str(self.driver), '--requirements', str(self.compliance), '--json', str(out_json), str(report)], capture_output=True, text=True)
        data = json.loads(out_json.read_text()) if out_json.exists() else None
        return (res, data)

    def test_compliance_yaml_loads(self):
        spec = self.load_requirements()
        assert isinstance(spec, dict), 'compliance.yaml must parse to a dict'
        assert spec.get('skill') == self.skill_name, f"compliance.yaml skill field mismatch: got {spec.get('skill')}"
        reqs = spec.get('requirements', [])
        assert isinstance(reqs, list) and len(reqs) > 0, 'compliance.yaml must declare at least one requirement'

    def test_empty_output_fails_audit(self, tmp_path):
        """Sanity: empty output must fail every required check."""
        (res, data) = self.run_driver(tmp_path, '')
        assert res.returncode == 1, 'empty output should FAIL audit'
        assert data['verdict'] == 'FAIL'

    def _declared_receipt_cross_checks(self, spec):
        """The cross-checks this skill's OWN yaml binds to an auditor's receipt.

    DERIVED from the yaml, never a hand-written second list — a hand-written
    register beside a generated one is what drifted in #2057 item 1.
    """
        return {c['id'] for c in spec.get('cross_checks') or [] if c.get('rule') == 'audit_receipt_evidence'}

    def _receipt_finding_base(self, finding_id):
        """`audit_receipt_evidence` reports a configuration error under a
    suffixed id (`<id>_unknown_auditor`, `<id>_no_auditor`); both are the
    same declared cross-check."""
        for suffix in ('_unknown_auditor', '_no_auditor'):
            if finding_id.endswith(suffix):
                return finding_id[:-len(suffix)]
        return finding_id

    def test_good_output_passes_all_required(self, tmp_path):
        """A synthetic good-output built from all patterns satisfies the checker.

    #2050 — THIS TEST USED TO SKIP BEFORE ITS ASSERT. When the synthetic
    document failed any required pattern it called `pytest.skip()`, so on 53
    of the 69 skills that ship this file the assert below never ran and the
    suite still reported green. That is how #2048 survived: the acceptance
    command in that issue gave byte-identical node-id sets on both arms.
    cz2050 replaced the blanket skip with a NAMED list of those 53.

    #2057 — the 53 all had ONE cause, the satisfier, and it is fixed:
    `_shared/pattern_satisfier.py` walks the regex's parse tree instead of
    rewriting its text, so `SYNTHETIC_FIXTURE_LIMITATIONS` is now EMPTY and
    every skill's required patterns are really satisfied. The list stays,
    empty, because the assert below still reddens if a NEW pattern becomes
    unreachable — that is the direction it was built to catch.

    With the required patterns satisfied, the outcome is fully determined and
    is asserted OUTRIGHT — no skip, no xfail, in either population:

      * a skill whose yaml binds NO auditor receipt must audit PASS;
      * a skill whose yaml binds one or more must FAIL with exactly those
        cross-checks NOT_MEASURED and nothing else, because a receipt is
        written by a real auditor run over a real subject and a synthetic
        Markdown document has no auditor run behind it. That set is DERIVED
        from the skill's own yaml, so it reddens if a receipt-bound check
        starts passing on nothing AND if any other finding appears.
    """
        spec = self.load_requirements()
        text = self.build_good_output(spec)
        (res, data) = self.run_driver(tmp_path, text)
        fails = [f for f in data['findings'] if f['severity'] == 'FAIL']
        req_fails = sorted((f['id'] for f in fails if f['id'].startswith('R')))
        declared = sorted(LIMITS.SYNTHETIC_FIXTURE_LIMITATIONS.get(self.skill_name, ()))
        assert req_fails == declared, f'{self.skill_name}: the named synthetic-fixture limitation list in _shared/synthetic_fixture_limits.py no longer matches what is measured. declared={declared} measured={req_fails}. If the measured set GREW, a required pattern just became unreachable for the generator — fix `_shared/pattern_satisfier.py` or the pattern, do not extend the list to silence this. If it SHRANK, delete the repaired IDs from the list; that is the list getting shorter, which is the point.'
        receipt_bound = self._declared_receipt_cross_checks(spec)
        measured_receipt = {self._receipt_finding_base(f['id']) for f in fails if not f['id'].startswith('R')}
        assert measured_receipt == receipt_bound, f"{self.skill_name}: the non-requirement failures of the synthetic good-output are {sorted(measured_receipt)}, but this skill's own compliance.yaml binds {sorted(receipt_bound)} to an auditor receipt. An UNEXPECTED id means the synthetic document tripped something new; a MISSING one means a receipt-bound audit passed with no receipt on disk, which is the defect #2048 was about."
        if receipt_bound:
            assert data['verdict'] == 'FAIL' and res.returncode == 1
            for f in fails:
                assert f.get('state') == 'NOT_MEASURED', (f['id'], f.get('state'))
            return
        assert data['verdict'] == 'PASS', [f['id'] for f in fails]
        assert res.returncode == 0
