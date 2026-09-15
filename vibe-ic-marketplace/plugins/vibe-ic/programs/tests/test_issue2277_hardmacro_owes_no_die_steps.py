"""A macro somebody else places has no die edge, so it owes no die steps.

vibe-ic#2277, flow steps 15.5ic / 26.5ic / 37.5ic and step 31. MEASURED on live
main 79506306d (lane icspm4, run1).

DIE STEPS (class a, ABSENT SUBJECT). All three rows condition on ROUTER FILES --
`input/submission_template/slots/*.yaml` OR `SELF_TAPEOUT.txt` -- and the only
N/A a hardmacro was offered is `NO_TEMPLATE.txt`. Since 0.5ic's fetch was wired,
a PDK with a LIVE shuttle in the registry has the operator's whole CATALOGUE
ingested as `slots/*.yaml` whether or not the design bought a slot, and no
`NO_TEMPLATE.txt` is written. So a delivery whose own input declares
`deliverable=HARDMACRO` with `operator_template.path/.slot` null matched the
`any_of`, all three rows were judged APPLICABLE, every output was owed, the
producers correctly wrote nothing (`tapeout_precheck` step_applies=false,
`tapeout_docs_gen` NOT_APPLICABLE, `die_finishing_gen._hardmacro_skip`), and the
rows read MISSING -- failing stage3 and stage4 compliance. RULINGS R12
(2026-09-07): a hardmacro exposes pins, gets no pad ring and no die ring.

STEP 31 (class c, PRODUCER GAP). `reports/phase3/perc_sweep.json` is a step-31
`required_output` and `perc_corpus_sweep` is named under its `programs:`, but no
clause invoked it -- the reach clause only READS the document. 8 of 9 outputs
were produced and the ninth was owed by nobody. `flow_declared_producer_run`
could not adopt it either: it matched only `--json`, and this producer writes
through `--report` because its own `--json` is a boolean.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROGRAMS = HERE.parent
PLUGIN = PROGRAMS.parent
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(HERE))

import pytest                                            # noqa: E402

import _tapeout_declaration as TD                        # noqa: E402
import flow_compliance_check as F                        # noqa: E402
import flow_declared_producer_run as P                   # noqa: E402

yaml = pytest.importorskip("yaml")

_FLOW = PLUGIN / "flow" / "phase1_phase2_phase3.yaml"
_DIE_STEPS = ("15.5ic", "26.5ic", "37.5ic")


def _steps():
    doc = yaml.safe_load(_FLOW.read_text(encoding="utf-8"))
    return {str(s["id"]): s for s in doc["steps"]}


def _project(tmp_path, *, deliverable="HARDMACRO", operator=None,
             declaration=True, self_tapeout=False):
    """A run whose 0.5ic left the operator's CATALOGUE on disk."""
    p = tmp_path / "run"
    tmpl = p / "input" / "submission_template"
    (tmpl / "slots").mkdir(parents=True)
    if self_tapeout:
        (tmpl / "SELF_TAPEOUT.txt").write_text("own tape-out\n")
    else:
        (tmpl / "slots" / "slot_1x1.yaml").write_text("slot: slot_1x1\n")
    if declaration:
        doc, _ = TD.merge_answers(TD.blank_declaration(),
                                  {"deliverable": deliverable,
                                   "top_cell": "top"})
        assert not TD.validate(doc)
        (tmpl / "tapeout_declaration.json").write_text(json.dumps(doc))
    else:
        (tmpl / "tapeout_declaration.json").write_text("{ not json")
    (p / "input" / "step_0_5ic_answers.json").write_text(json.dumps({
        "schema": "vibe-ic/step_0_5ic_answers/1",
        "operator_template": operator or {"path": None, "slot": None,
                                          "absent_reason": "no operator"},
        "answers": {"deliverable": deliverable, "top_cell": "top"}}))
    return p


def _runs(project, sid):
    return F._check_condition(project, _steps()[sid].get("condition") or {})


# --------------------------------------------------------------------------- #
# the die steps stand down BY DECLARATION
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("sid", _DIE_STEPS)
def test_a_declared_hardmacro_owes_no_die_step(tmp_path, sid):
    assert _runs(_project(tmp_path), sid) is False


@pytest.mark.parametrize("sid", _DIE_STEPS)
def test_the_skip_CITES_the_designs_own_declaration(tmp_path, sid):
    """A reviewer must be able to tell a design that declared the subject
    absent from a trigger file somebody forgot to author."""
    cited = F._delivery_declares_absence(
        _project(tmp_path),
        (_steps()[sid]["condition"] or {}).get("delivery_declares"))
    assert cited is not None
    rel, detail = cited
    assert rel == "input/submission_template/tapeout_declaration.json"
    assert "HARDMACRO" in detail and "no operator slot is bound" in detail


def test_the_IP_terminal_step_stays_LIVE(tmp_path):
    """37.5ip is what this delivery DOES owe. The fix must not stand it down
    too, or the hardmacro would ship with no kit at all."""
    assert _runs(_project(tmp_path), "37.5ip") is True


# --------------------------------------------------------------------------- #
# negative controls: a design WITH a die keeps every row live
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("sid", _DIE_STEPS)
def test_a_DIE_against_the_same_catalogue_keeps_the_row_live(tmp_path, sid):
    assert _runs(_project(tmp_path, deliverable="DIE"), sid) is True


@pytest.mark.parametrize("sid", _DIE_STEPS)
def test_a_self_tapeout_keeps_the_row_live(tmp_path, sid):
    assert _runs(_project(tmp_path, deliverable="DIE", self_tapeout=True),
                 sid) is True


@pytest.mark.parametrize("sid", _DIE_STEPS)
def test_a_hardmacro_that_BOUGHT_a_slot_keeps_the_row_live(tmp_path, sid):
    """An affirmative operator binding is a purchase, and a purchase goes
    through that operator whatever the delivery word says."""
    assert _runs(_project(tmp_path, operator={"path": "t.yaml",
                                              "slot": "slot_1x1"}), sid) is True


@pytest.mark.parametrize("sid", _DIE_STEPS)
def test_an_unreadable_declaration_RUNS_the_step(tmp_path, sid):
    """Absence of a declaration is not a declaration of absence."""
    assert _runs(_project(tmp_path, declaration=False), sid) is True


def test_a_deliverable_the_clause_does_not_list_runs_the_step(tmp_path):
    assert F._delivery_declares_absence(
        _project(tmp_path, deliverable="NOT_DETERMINED"),
        {"declaration": "input/submission_template/tapeout_declaration.json",
         "field": "answers.deliverable", "absent_when": ["HARDMACRO"]}) is None


def test_a_malformed_predicate_declares_nothing(tmp_path):
    p = _project(tmp_path)
    for spec in (None, {}, "HARDMACRO",
                 {"declaration": "/etc/passwd", "field": "answers.deliverable",
                  "absent_when": ["HARDMACRO"]},
                 {"declaration": "input/submission_template/"
                                 "tapeout_declaration.json",
                  "field": "answers.deliverable", "absent_when": []},
                 {"declaration": "input/submission_template/"
                                 "tapeout_declaration.json",
                  "field": "answers.no_such_field",
                  "absent_when": ["HARDMACRO"]}):
        assert F._delivery_declares_absence(p, spec) is None, spec


# --------------------------------------------------------------------------- #
# step 31: the producer gap
# --------------------------------------------------------------------------- #
def _clauses():
    return P.declared_producer_clauses(_FLOW)


def test_the_perc_sweep_document_now_has_a_declared_producer():
    rows = [c for c in _clauses()
            if c["target"] == "reports/phase3/perc_sweep.json"]
    assert len(rows) == 1, rows
    assert rows[0]["program"] == "perc_corpus_sweep"
    assert rows[0]["step"] == "31"


def test_the_producer_runs_BEFORE_its_reader_in_the_same_gate():
    """A reach verdict rendered before the sweep wrote its report is the
    vacuity `sweep_reach_check` exists to refuse."""
    gate = json.dumps(_steps()["31"]["gate"])
    assert gate.index("perc_corpus_sweep") < gate.index("sweep_reach_check")


def test_the_writer_flag_is_not_the_condition():
    """`perc_corpus_sweep`'s own `--json` is a BOOLEAN; it writes its document
    with `--report`, and matching only `--json` is what skipped it."""
    src = (PROGRAMS / "perc_corpus_sweep.py").read_text()
    assert 'ap.add_argument("--json", action="store_true"' in src
    assert P._JSON_RE.search("x . --report reports/phase3/perc_sweep.json")


def test_widening_the_flag_adopted_exactly_one_clause():
    """The blast radius, asserted rather than assumed: no other step in the
    flow names a declared output with `--report`."""
    by_report = [c for c in _clauses() if "--report" in c["command"]]
    assert [(c["step"], c["program"]) for c in by_report] == [
        ("31", "perc_corpus_sweep")]


def test_a_clause_naming_an_undeclared_path_is_still_not_a_producer():
    """The third condition is what keeps the widening honest."""
    for c in _clauses():
        step = _steps()[c["step"]]
        assert c["target"] in set(step.get("required_outputs") or [])


# --------------------------------------------------------------------------- #
# the consumer the new condition key reaches: _ppa.delivery_path
# --------------------------------------------------------------------------- #
# MEASURED after the condition landed on the branch: `_ppa/delivery_path.py`
# refuses LOUDLY (by design) any key on a terminal's condition it has not been
# taught -- `_ROUTE_CONDITION_KEYS` -- and every route it resolved came back
# UNREADABLE, reddening 13 tests across two modules. That guard is right and
# stays; `delivery_declares` is now IN the set because it answers this module's
# OWN question from a better source, and the same predicate `resolve` already
# runs evaluates it.
import _ppa.delivery_path as DP                           # noqa: E402


def test_the_route_reader_was_TAUGHT_the_key_not_widened(tmp_path):
    """The loud refusal survives: a key nothing has been taught still makes
    the route UNREADABLE rather than being ignored."""
    assert "delivery_declares" in DP._ROUTE_CONDITION_KEYS
    conds, why = DP._route_conditions()
    assert conds is not None, why


def test_a_declared_hardmacro_resolves_to_the_IP_ROUTE(tmp_path):
    """Neither terminal's `files_exist` is met on this tree -- the chip
    terminal stands down BY DECLARATION and `NO_TEMPLATE.txt` was never
    written because a live shuttle existed on the target PDK. Reporting
    NOT_DETERMINED here would say "this tree carries no router artefact"
    about a tree that carries one, which is the one thing this module
    promises never to do."""
    r = DP.resolve(_project(tmp_path))
    assert r["path"] == DP.PATH_IP
    assert r["evidence"]["declaration"]["file"] == (
        "input/submission_template/tapeout_declaration.json")


def test_a_DIE_on_the_same_tree_still_resolves_to_the_CHIP_ROUTE(tmp_path):
    r = DP.resolve(_project(tmp_path, deliverable="DIE"))
    assert r["path"] == DP.PATH_CHIP


def test_a_tree_with_no_router_and_no_declaration_stays_NOT_DETERMINED(tmp_path):
    """The honest unknown is preserved: nothing to read is not an IP finding."""
    bare = tmp_path / "bare"
    (bare / "input" / "submission_template").mkdir(parents=True)
    assert DP.resolve(bare)["path"] == DP.PATH_NOT_DETERMINED


# --------------------------------------------------------------------------- #
# the enforcement register the new gate clause reaches
# --------------------------------------------------------------------------- #
# MEASURED on the branch: naming `perc_corpus_sweep` in a gate clause makes it a
# gate-NAMED program, and `flow_gate_enforcement_audit` refuses a gate-named
# program that is reachable only through the final audit and says nothing about
# it -- `undeclared::perc_corpus_sweep`, the #1035 class, three red ratchets.
# The repair is the DECLARATION, not a register entry: the three axes below are
# the #1035 shape, and none of them is a grep for a string in a source file.
import flow_gate_enforcement_audit as ENF                  # noqa: E402


def test_the_new_gate_program_DECLARES_where_it_is_enforced():
    """Axis 1 — the declaration exists AND the audit can SEE it (it must be an
    opened line inside the reader's own window, which a grep would not prove)."""
    assert ENF.declared_intent(PROGRAMS, "perc_corpus_sweep") == "advisory"


def test_the_declaration_bought_no_demotion():
    """Axis 2 — `advisory` on the audit's axis is the exact token a later change
    could quote as licence to defang the refusal. It cannot: the refusal
    predicate is a DIFFERENT program, and it is still the one wired to judge the
    document this producer writes."""
    gate = json.dumps(_steps()["31"]["gate"])
    assert "sweep_reach_check --report reports/phase3/perc_sweep.json" in gate
    assert gate.index("perc_corpus_sweep") < gate.index("sweep_reach_check")


def test_the_audit_exits_zero_and_names_this_program_as_declared(tmp_path):
    """Axis 3 — end to end, driven rather than read."""
    import subprocess
    out = tmp_path / "enf.json"
    r = subprocess.run(
        [sys.executable, str(PROGRAMS / "flow_gate_enforcement_audit.py"),
         "--json", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-800:]
    gates = {g["gate"]: g for g in json.loads(out.read_text())["gates"]}
    assert gates["perc_corpus_sweep"]["declared"] == "advisory"
