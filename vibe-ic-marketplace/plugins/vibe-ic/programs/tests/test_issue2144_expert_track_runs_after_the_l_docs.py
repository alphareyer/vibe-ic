#!/usr/bin/env python3
"""Regression for #2144 — WHEN the Phase-1 expert track may ask for the class.

THE REPORT, AND THE CORRECTION MEASURED AGAINST IT. #2144 was filed as "the
class-first expert-pack selection never fires on the first front-door pass":
`phase1_expert_parse_track.registered_ic_class` reads the class through
`ic_class_profile.detect_ic_class`, which classifies from GENERATED L documents,
so on a tree holding only `input/` it answers `unknown`. The second half of that
is true and is pinned below. The conclusion is not: the flow already runs the
track AFTER the documents exist — `phase1_expert_parse_track` is step D1's own
gate clause and the L documents are D1's own `required_outputs` — so a design
arriving through the front door with nothing but `input/` staged gets its class
on the FIRST pass. Measured on input-only copies of three corpus projects: the
two processor cores classify and the crypto accelerator (the one class the DB
profiles) assembles CLASS_CONFINED with a non-null contract.

SO THE FIX IS THE ORDER, NOT AN EARLIER CLASSIFICATION — and the order was
held by nothing. Moving the track's call above the doc-extraction delegate in
`phase1_one_shot_runner.main` degrades a PROFILED class's pack from
CLASS_CONFINED/ASSEMBLED (target module recovered, contract written) to
LEXICAL_UNCONFINED/NOT_A_REGISTERED_CLASS with both null — #2094's defect,
restored — while the runner still exits 0 and the impacted test selection stays
identical by membership. `test_the_expert_track_is_called_after_doc_extraction`
is the pin that mutation now reddens.

THE OTHER HALF, classifying from the input prose instead, is REFUSED BY
MEASUREMENT and the refusal is pinned as prose in this docstring rather than as
a test, because it is a fact about the corpus and not about this tree: running
the classifier's own unmodified cascade over the design input alone re-classes
13 of the 51 already-classed corpus roots that carry an `input/` directory,
including all three subjects above and including the profiled one
(crypto accelerator -> serial peripheral protocol). Since a non-`unknown` class
is PERSISTED on first sight and every later call returns it (#435), an early
input-derived class would not merely be wrong once, it would be frozen for the
whole run.

WHAT THE RECORD MAY NOT DO IS SPELL THREE ZEROS THE SAME WAY.
`registered_ic_class` returns None when the classifier was asked too early, when
it read the documents and could not place the design, and when it could not be
consulted at all; the consumer's record called all three "no registered ic_class
was supplied, or the name is not one the registry carries".
`registered_ic_class_disposition` separates them and the pack disposition
carries the answer. Each of the three states is exercised below on a tree that
genuinely reaches it — a state no fixture can reach is not a state.

chip-AGNOSTIC: every fixture here is synthetic and names no chip, vendor, SKU,
node or benchmark. §4.05: nothing reads an oracle, harness or golden output.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import ic_expert_backup_pack as _pack  # noqa: E402
import phase1_expert_parse_track as _track  # noqa: E402
import _path_layout as _pl  # noqa: E402

_RUNNER = _PROGRAMS / "phase1_one_shot_runner.py"


# ── the ordering pin ────────────────────────────────────────────────────────
#
# Asked of the AST, not of the text: a `grep` for the two names answers the
# same on a file where they appear in a comment, and the thing under test is
# which CALL happens first inside one function.

def _main_function() -> ast.FunctionDef:
    tree = ast.parse(_RUNNER.read_text(), filename=str(_RUNNER))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            return node
    raise AssertionError(
        f"{_RUNNER.name} has no top-level `main` — this pin reads the call "
        f"order inside it and cannot silently pass over its absence")


def _called_names(node: ast.AST) -> list:
    """(lineno, dotted-name) for every Call under `node`, source order."""
    out = []
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if isinstance(f, ast.Name):
            out.append((n.lineno, f.id))
        elif isinstance(f, ast.Attribute):
            base = f.value
            prefix = base.id + "." if isinstance(base, ast.Name) else ""
            out.append((n.lineno, prefix + f.attr))
    return sorted(out)


def test_the_expert_track_is_called_after_doc_extraction():
    """The class-first selection can only fire on a tree that already carries
    the L documents the classifier reads, and the ONLY thing that puts them
    there before the track runs is this call order."""
    calls = _called_names(_main_function())
    track = [ln for ln, name in calls if name == "run_phase1_second_track"]
    gate = [ln for ln, name in calls if name == "_spf.gate"]
    assert track, (
        "`main` never calls `run_phase1_second_track` — the Phase-1 expert "
        "track is the second half of the dual-track doctrine and a runner that "
        "does not call it is indistinguishable from having no second track")
    assert gate, (
        "`main` never calls `_spf.gate` — the pre-flight-gated doc-extraction "
        "call is what produces the L documents this ordering is about")
    assert min(track) > min(gate), (
        f"`run_phase1_second_track` is called at line {min(track)}, BEFORE the "
        f"doc-extraction gate at line {min(gate)}. The expert track classifies "
        f"the design through `ic_class_profile.detect_ic_class`, which reads "
        f"the L documents step D1 produces; asked first, it gets `unknown`, "
        f"the class-first pack selection cannot fire, and a PROFILED class "
        f"silently receives the unconfined phrase-selected pack #2094 was "
        f"landed to stop — with the run still exiting 0 (#2144)")


def test_the_second_track_helper_states_the_order_it_depends_on():
    """A pin in a test file the runner's author may never open is half a pin.
    The helper's own docstring has to say what it is ordered against, so the
    reason survives at the call site."""
    src = _RUNNER.read_text()
    fn = next(n for n in ast.parse(src).body
              if isinstance(n, ast.FunctionDef)
              and n.name == "run_phase1_second_track")
    doc = (ast.get_docstring(fn) or "").lower()
    assert "l-doc" in doc or "l doc" in doc or "l document" in doc, (
        "`run_phase1_second_track`'s docstring no longer says that it runs "
        "after the L documents exist — that sentence is the only thing at the "
        "call site telling a reader the order is load-bearing (#2144)")


# ── the three zeros ─────────────────────────────────────────────────────────

def _input_only_tree(tmp_path: Path) -> Path:
    p = tmp_path / "input_only"
    (p / "input" / "docs").mkdir(parents=True)
    (p / "input" / "docs" / "brief.md").write_text(
        "# Design brief\n\nA block that adds two operands and registers the "
        "result.\n")
    return p


def _classified_tree(tmp_path: Path, ic_class: str) -> Path:
    """A tree whose class is already PERSISTED — #435's persist-once file is
    what `detect_ic_class` returns whenever it exists."""
    p = tmp_path / f"classified_{ic_class}"
    (p / "reports").mkdir(parents=True)
    (p / "reports" / "ic_class.json").write_text(
        json.dumps({"ic_class": ic_class,
                    "decisive_evidence": "fixture: persisted by the run"}))
    return p


def _read_but_unplaceable_tree(tmp_path: Path) -> Path:
    """L documents PRESENT and the classifier still answers `unknown`.

    Reached through the classifier's own Wave-42/SF5 downgrade: L1 declares a
    pure-analog class while the RTL carries command-driven constructs, so the
    class is downgraded to `unknown` fail-closed. A real branch of the shipped
    cascade — this control would prove nothing if no tree could reach it.
    """
    p = tmp_path / "read_but_unplaceable"
    docs = _pl.generated_docs_dir(p)
    docs.mkdir(parents=True)
    (docs / "L1_DATASHEET.json").write_text(json.dumps({"ic_class": "pure_analog"}))
    rtl = _pl.rtl_dir(p)
    rtl.mkdir(parents=True)
    (rtl / "top.v").write_text("module t;\n  reg [7:0] cmd_buf;\nendmodule\n")
    return p


def test_asked_too_early_is_its_own_state(tmp_path):
    d = _track.registered_ic_class_disposition(_input_only_tree(tmp_path))
    assert d["ic_class"] is None
    assert d["reason_class"] == _track.CLASS_NOT_YET_CLASSIFIABLE
    assert "not knowable" in d["reason"]


def test_read_and_unplaceable_is_a_different_state(tmp_path):
    """The negative control for the one above: documents were READ. Collapsing
    this into `NOT_YET_CLASSIFIABLE` would tell a reader to re-run later on a
    design that will answer `unknown` every time."""
    d = _track.registered_ic_class_disposition(_read_but_unplaceable_tree(tmp_path))
    assert d["ic_class"] is None
    assert d["reason_class"] == _track.CLASS_UNCLASSIFIABLE


def test_a_classified_tree_reports_classified(tmp_path):
    d = _track.registered_ic_class_disposition(
        _classified_tree(tmp_path, "digital_arithmetic_primitive"))
    assert d["ic_class"] == "digital_arithmetic_primitive"
    assert d["reason_class"] == _track.CLASS_CLASSIFIED


def test_an_unconsultable_classifier_is_not_an_unclassified_design(monkeypatch,
                                                                   tmp_path):
    """"Could not read it" is not "read it and there was nothing"."""
    import ic_class_profile as _icp

    def _boom(*a, **k):
        raise RuntimeError("classifier unavailable")

    monkeypatch.setattr(_icp, "detect_ic_class", _boom)
    d = _track.registered_ic_class_disposition(_input_only_tree(tmp_path))
    assert d["reason_class"] == _track.CLASS_NOT_MEASURED
    assert d["ic_class"] is None


def test_the_four_reason_classes_are_distinct():
    names = [_track.CLASS_CLASSIFIED, _track.CLASS_NOT_YET_CLASSIFIABLE,
             _track.CLASS_UNCLASSIFIABLE, _track.CLASS_NOT_MEASURED]
    assert len(set(names)) == len(names)


def test_registered_ic_class_still_returns_a_bare_class(tmp_path):
    """The single-value contract every existing caller depends on."""
    assert _track.registered_ic_class(_input_only_tree(tmp_path)) is None
    assert _track.registered_ic_class(
        _classified_tree(tmp_path, "processor_cpu")) == "processor_cpu"


# ── the record carries it ───────────────────────────────────────────────────

def test_the_pack_disposition_states_why_there_was_no_class(tmp_path):
    availability = _track.registered_ic_class_disposition(
        _input_only_tree(tmp_path))
    cf = _pack.class_first_disposition(None, availability=availability)
    assert cf["profile"] == "NOT_A_REGISTERED_CLASS"
    assert cf["class_availability"]["reason_class"] == \
        _track.CLASS_NOT_YET_CLASSIFIABLE
    assert _track.CLASS_NOT_YET_CLASSIFIABLE in cf["reason"], (
        "the disposition's own `reason` — the sentence a reader of the record "
        "actually reads — still says only that no class was supplied (#2144)")


def test_omitting_the_availability_leaves_the_disposition_unchanged():
    """The MUTATION control for the change above: a caller that does not supply
    an availability must get the pre-#2144 dict, key for key and value for
    value, or this landing has moved every consumer of an unprofiled pack."""
    assert _pack.class_first_disposition(None) == {
        "ic_class": None,
        "registered": False,
        "profile": "NOT_A_REGISTERED_CLASS",
        "db_class_selection": "LEXICAL_UNCONFINED",
        "db_classes": [],
        "reason": ("no registered ic_class was supplied, or the name is not one "
                   "the registry carries"),
    }


def test_a_registered_class_is_unaffected_by_the_new_argument():
    """An availability is only ever consulted on the no-class path. A profiled
    class must answer identically with and without it, or the disclosure has
    leaked into a state it says nothing about."""
    profiled = None
    try:
        import ic_expert_db_query as _dbq
        for name in ("crypto_accelerator",):
            if _dbq.registered_class_profile(name):
                profiled = name
                break
    except Exception:  # noqa: BLE001
        pass
    if profiled is None:
        pytest.skip("no profiled class in the shipped DB to exercise")
    bare = _pack.class_first_disposition(profiled)
    withav = _pack.class_first_disposition(
        profiled, availability={"reason_class": _track.CLASS_CLASSIFIED,
                                "reason": "irrelevant on this path"})
    assert bare == withav


def test_the_track_actually_wires_the_availability_into_its_own_report(tmp_path):
    """The end-to-end pin for the two above. `class_first_disposition` accepting
    an availability buys nothing if the track never hands it one — a defect no
    unit test of either side can see, because each half passes alone."""
    project = _input_only_tree(tmp_path)
    report = _track.evaluate(project)
    cf = report.get("class_first") or {}
    assert cf.get("class_availability", {}).get("reason_class") == \
        _track.CLASS_NOT_YET_CLASSIFIABLE, (
        "the expert track's own report does not carry WHY it had no class. "
        f"`class_first` was: {cf!r} (#2144)")
