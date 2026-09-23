"""Every writer in the shared-name population states WHO INVOKED it.

R-0915-152, third cut, producer side. The reader half landed first: a document that says
`invoked_as` is classified by that, and one that does not falls back to the timing facts
and says so. These arms are the other half -- teaching the writers -- and the CENSUS that
keeps the population honest when the flow grows.

THE POPULATION IS DERIVED FROM THE FLOW, never tabulated here: the pairs where a declared
`required_output` is also the step's own gate `--json` target AND the writing program is
also listed under that step's `programs:`. For those the document is byte-identical
whichever side invoked it, so no program name can classify it -- which is why
`_is_gate_verdict_document` correctly declines, and why the note was the sole record.

TWO PAIRS CANNOT BE TAUGHT, and they are named with the measured reason rather than
quietly skipped: `rtl_hygiene_lint` and `rom_init_lint` write a top-level JSON LIST, so
there is no object to carry the key. `_gate_authorship.stamp` returns a non-mapping
untouched by design; wrapping them in an object would change the top-level shape of a
declared artefact for every reader. That is a separate change with its own blast radius,
and `test_the_untaught_pairs_are_exactly_the_list_shaped_ones` holds the exception to its
stated reason.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PROGRAMS = Path(__file__).resolve().parents[1]
PLUGIN = PROGRAMS.parent
sys.path.insert(0, str(PROGRAMS))

import flow_compliance_check as FCC          # noqa: E402
import _gate_authorship as GA                # noqa: E402

#: Named, with the reason measured in `test_the_untaught_pairs_are_exactly_the_list_shaped_ones`.
LIST_SHAPED_WRITERS = frozenset({"rtl_hygiene_lint", "rom_init_lint"})


def _gate_commands(node, out=None):
    out = [] if out is None else out
    if isinstance(node, dict):
        for k, v in node.items():
            if k.endswith("program_exit_zero"):
                out.append(v if isinstance(v, str) else str((v or {}).get("command", "")))
            else:
                _gate_commands(v, out)
    elif isinstance(node, list):
        for i in node:
            _gate_commands(i, out)
    return out


def _shared_pairs():
    """(step, rel, program) for every pair where content cannot classify the document."""
    flow = yaml.safe_load(
        (PLUGIN / "flow" / "phase1_phase2_phase3.yaml").read_text())
    pairs = []
    for st in flow.get("steps") or []:
        req = set(st.get("required_outputs") or [])
        declared = {str(p).strip() for p in (st.get("programs") or [])
                    if isinstance(p, str)}
        cmds = _gate_commands(st.get("gate") or {})
        gates = {FCC._gate_name(c) for c in cmds}
        for cmd in cmds:
            toks = cmd.split()
            for i, tok in enumerate(toks[:-1]):
                if tok == "--json" and toks[i + 1] in req:
                    prog = FCC._gate_name(cmd)
                    if prog in declared and prog in gates:
                        pairs.append((str(st.get("id")), toks[i + 1], prog))
    return pairs


def _writing_module(program: str) -> str:
    """The module that actually writes, following a wrapper's `from X import main`."""
    src = (PROGRAMS / f"{program}.py").read_text(errors="replace")
    m = re.search(r"^from\s+(\w+)\s+import\s+main\b", src, re.M)
    return m.group(1) if m else program


def _is_taught(module: str) -> bool:
    return "_gate_authorship" in (PROGRAMS / f"{module}.py").read_text(errors="replace")


PAIRS = _shared_pairs()


def test_the_population_is_not_empty_so_the_census_below_can_fail():
    """A census over an empty set passes vacuously; this is the denominator."""
    assert len(PAIRS) >= 20, PAIRS
    assert len({p[0] for p in PAIRS}) >= 15, "the pairs should span many steps"


@pytest.mark.parametrize("step,rel,program", PAIRS,
                         ids=[f"{s}:{Path(r).name}" for s, r, _ in PAIRS])
def test_every_shared_pair_has_a_writer_that_states_its_role(step, rel, program):
    """The completeness gate. Derived from the flow, so a NEW pair arrives red."""
    module = _writing_module(program)
    if module in LIST_SHAPED_WRITERS:
        pytest.skip(f"{module} writes a top-level list; see the module-level docstring "
                    f"and test_the_untaught_pairs_are_exactly_the_list_shaped_ones")
    assert _is_taught(module), (
        f"step {step}: {rel} is written by {module} (via {program}), which is in the "
        f"shared-name population and states no role -- so its classification still rests "
        f"on the authorship note alone")


def test_the_untaught_pairs_are_exactly_the_list_shaped_ones(tmp_path):
    """The exception is held to its reason, by running the programs.

    If either writer ever emits an object, this goes red and the exception must go.
    """
    rtl = tmp_path / "rtl"
    rtl.mkdir()
    (rtl / "m.v").write_text("module m(input clk); endmodule\n")

    for module in sorted(LIST_SHAPED_WRITERS):
        out = tmp_path / f"{module}.json"
        r = subprocess.run(
            [sys.executable, str(PROGRAMS / f"{module}.py"), str(rtl / "m.v"),
             "--json", str(out)],
            capture_output=True, text=True, cwd=str(tmp_path),
            env=dict(os.environ, **{GA.ROLE_ENV: GA.ROLE_AUDIT}))
        assert out.is_file(), (module, r.returncode, r.stdout[-400:], r.stderr[-400:])
        doc = json.loads(out.read_text())
        assert isinstance(doc, list), (
            f"{module} now writes a {type(doc).__name__}; it can carry `invoked_as`, so "
            f"it must be taught and removed from LIST_SHAPED_WRITERS")
        assert GA.stamp(doc) is doc and GA.role_of(doc) is None

    # and every OTHER writer in the population is taught -- no silent third category
    untaught = sorted({_writing_module(p) for _, _, p in PAIRS
                       if not _is_taught(_writing_module(p))})
    assert untaught == sorted(LIST_SHAPED_WRITERS), untaught


# ── behaviour: the programs themselves, as real subprocesses ────────────────

#: One program per taught writing module in the population. `eda_report_audit` is
#: represented by all six of its wrappers, because the wrapper is what the flow invokes.
DRIVEN = [
    "drc_report_check", "sta_report_check", "em_report_check", "ir_drop_report_check",
    "antenna_report_check", "lvs_report_check",          # eda_report_audit x6
    "tapeout_signoff_check",                             # signoff_audit
    "crosslayer_rewrite_equivalence_check", "sdc_syntax_check", "bsdl_emit",
    "pad_assignment_gen", "erc_density_check", "perc_signoff_check",
    "post_layout_sim_check", "die_finishing_check", "digital_hardmacro_check",
    "em_peak_current_authority_check", "foundry_handoff_package_check",
    "mixed_signal_merge_check", "tapeout_precheck",
]


@pytest.fixture(scope="module")
def bare_project(tmp_path_factory):
    """A project with nothing produced: every gate below refuses, and still writes."""
    p = tmp_path_factory.mktemp("proj")
    (p / "input").mkdir()
    (p / "input" / "spec.md").write_text("# a counter\n")
    return p


def _drive(program: str, project: Path, out: Path, role: str | None):
    env = {k: v for k, v in os.environ.items() if k != GA.ROLE_ENV}
    if role is not None:
        env[GA.ROLE_ENV] = role
    r = subprocess.run(
        [sys.executable, str(PROGRAMS / f"{program}.py"), str(project),
         "--json", str(out)],
        capture_output=True, text=True, cwd=str(project), env=env, timeout=600)
    assert out.is_file(), (
        f"{program} wrote no document (rc={r.returncode}): {r.stdout[-300:]} "
        f"{r.stderr[-300:]}")
    return json.loads(out.read_text())


@pytest.mark.parametrize("program", DRIVEN)
def test_the_program_records_the_role_its_caller_stated(program, bare_project, tmp_path):
    """Run it both ways. Nothing but the environment differs between the two."""
    as_audit = _drive(program, bare_project, tmp_path / "a.json", GA.ROLE_AUDIT)
    assert as_audit.get(GA.DOC_KEY) == GA.ROLE_AUDIT, (
        f"{program} was invoked by an audit and its document does not say so")

    as_run = _drive(program, bare_project, tmp_path / "r.json", None)
    assert as_run.get(GA.DOC_KEY) == GA.ROLE_PRODUCER, (
        f"{program} with no role stated must record the run's own default")


@pytest.mark.parametrize("program", DRIVEN)
def test_the_stamp_is_the_only_difference_between_the_two_invocations(program,
                                                                     bare_project,
                                                                     tmp_path):
    """Teaching a writer must not change anything else it says.

    A one-key diff, asserted by comparing the two documents with the key removed -- so a
    stamp that also perturbed a verdict, a count or an ordering shows up here.
    """
    a = _drive(program, bare_project, tmp_path / "a.json", GA.ROLE_AUDIT)
    b = _drive(program, bare_project, tmp_path / "b.json", None)
    a.pop(GA.DOC_KEY, None)
    b.pop(GA.DOC_KEY, None)
    # fields that legitimately move between two runs of the same program
    for volatile in ("run_at", "generated_at", "timestamp", "elapsed_s", "duration_s"):
        a.pop(volatile, None)
        b.pop(volatile, None)
    assert a == b, f"{program} says something different depending on who invoked it"
