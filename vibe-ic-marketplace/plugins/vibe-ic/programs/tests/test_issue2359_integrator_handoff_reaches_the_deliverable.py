"""#2359 (D3) — the HARDMACRO integrator handoff reaches what a human reads.

`die_level_deck_rule_attribution` writes `integrator_requirements.json`: the
die-level rules (metal density floors) a macro cannot close because their window
is the integrator's whole die. MEASURED on subservient r32: M2.4 0.2366 and M3.4
0.2436 against a 0.30 floor (engine ceiling 0.3388 / 0.3374), and neither
`final_summary.md` nor any of the six IP documents said so.

Both directions:
  * a record with requirements -> every document and the final summary carry the
    section, the numbers are sourced rows, and release_docs_check resolves them;
  * no record, or a record with no requirement -> no section anywhere.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _release_kit import SUBJECT, build_project, docs_dir  # noqa: E402
from _release_docs_contract import IP_DOCS, MANIFEST_NAME  # noqa: E402
import final_report_generate as FR  # noqa: E402

PROGS = Path(__file__).resolve().parents[1]
HEADING = "## Integrator handoff (HARDMACRO)"
RECORD = "phase3/stage4/hardmacro/integrator_requirements.json"
REQS = [
    {"rule": "M2.4", "layer": "metal2", "achieved": 0.2366, "floor": 0.3,
     "legal_ceiling": 0.338801},
    {"rule": "M3.4", "layer": "metal3", "achieved": 0.2436, "floor": 0.3,
     "legal_ceiling": 0.337411},
]


def _project(tmp_path: Path, requirements=None) -> Path:
    project = build_project(tmp_path / "p", packages=(SUBJECT,))
    if requirements is not None:
        rec = project / RECORD
        rec.parent.mkdir(parents=True, exist_ok=True)
        rec.write_text(json.dumps({"record": "integrator_requirements",
                                   "deliverable": "HARDMACRO",
                                   "requirements": requirements}))
    return project


def _docs(project: Path):
    r = subprocess.run([sys.executable, str(PROGS / "ip_release_docs_gen.py"),
                        str(project)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    out = docs_dir(project, SUBJECT)
    return {p.name: p.read_text() for p in out.glob("*.md")
            if p.name != MANIFEST_NAME}


def _summary(project: Path) -> str:
    return FR._render(project, run_audit=False)


def test_every_document_and_the_summary_carry_the_handoff(tmp_path):
    project = _project(tmp_path, REQS)
    docs = _docs(project)
    required = {s.filename for s in IP_DOCS if s.requirement == "required"}
    assert required <= set(docs)
    for name, text in docs.items():
        assert HEADING in text, f"{name} omits the integrator handoff"
        section = text.split(HEADING, 1)[1]
        for needle in ("M2.4", "0.2366", "M3.4", "0.2436", "0.3",
                       "0.338801", RECORD):
            assert needle in section, f"{name}: {needle} missing"
        assert "requirements[M2.4].achieved" in section
    summary = _summary(project)
    assert HEADING in summary
    for needle in ("0.2366", "0.2436", RECORD):
        assert needle in summary.split(HEADING, 1)[1]
    gate = subprocess.run([sys.executable, str(PROGS / "release_docs_check.py"),
                           str(project), "--arm", "ip"],
                          capture_output=True, text=True)
    assert gate.returncode == 0, gate.stdout[-2000:] + gate.stderr[-2000:]


def test_no_record_means_no_section(tmp_path):
    project = _project(tmp_path, None)
    for name, text in _docs(project).items():
        assert HEADING not in text, name
    assert HEADING not in _summary(project)


def test_a_record_with_no_requirement_invents_no_obligation(tmp_path):
    project = _project(tmp_path, [])
    for name, text in _docs(project).items():
        assert HEADING not in text, name
    assert HEADING not in _summary(project)
