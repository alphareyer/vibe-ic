"""(D1) R-0915-126-D1 — an expert parse track that DECIDED its expectations is
DECIDED; AWAITING survives only for "could not read its own subject".

MEASURED on spm run13 AND run8 (8HD-4,
`reports/audit/phase1/expert_parse_track.json` — note: under `reports/audit/`,
not `reports/phase1/`):

    verdict INCOMPLETE · producer.returncode 4 (AWAITING) ·
    ai_subtrack HANDOFF_EMITTED · execution.complete False ·
    observed_ai_consumed 0

and the pack the track emitted holds ONLY the handoff INPUTS
(`authoring_schema.json`, `design_input.txt`, `ic_expert_agent_handoff.json`,
`ic_expert_db.md`, `lessons.md`). The file the rc==0 branch hashes,
`l_doc_expectations.json`, DOES NOT EXIST — it is the thing the AI is being
asked to author. In a program-only front door nobody invokes the subagent, so
AWAITING never ended and D1 was red on every run of every design.

TWO DERIVATIONS, KEPT DISTINCT (the same rule as R-0915-125(a)):
  ai       credited against `answer_sha256` over the AI's own
           `l_doc_expectations.json`, which the program NEVER writes.
  program  credited against its OWN `program_expectations_sha256` over the
           deterministic expectations it decided.

AND THE NARROW BOUNDARY, which is the part that needed care: a program-derived
completion stands in ONLY for "nothing was ever answered" (HANDOFF_EMITTED). It
must NOT cover a REFUSED answer — a schema mismatch, an unparseable answer, an
empty reading — because there half of a dual track declined to read something
REAL, and no top-line word on that run may imply it was examined.

chip-AGNOSTIC: no design, PDK or vendor appears.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import phase1_expert_parse_track as T  # noqa: E402


def test_the_program_digest_is_its_own_and_discriminates():
    """The program-derived identity must be a function of WHAT IT DECIDED, or
    it is a rubber stamp. Different expectations -> different digest."""
    a = [{"id": "r1", "expectations": ["e1", "e2"]}]
    b = [{"id": "r1", "expectations": ["e1"]}]
    da, db = (T._program_expectations_digest(a),
              T._program_expectations_digest(b))
    assert len(da) == 64 and da != db


def test_the_program_digest_does_not_depend_on_dict_ordering():
    """Canonical, so the identity is a fact about the expectations and not
    about how a dict happened to be built."""
    a = [{"id": "r1", "expectations": ["e1", "e2"]}]
    b = [{"expectations": ["e1", "e2"], "id": "r1"}]
    assert (T._program_expectations_digest(a)
            == T._program_expectations_digest(b))


def test_a_program_derived_completion_never_reuses_the_ai_answer_digest():
    """`l_doc_expectations.json` stays the AI's file. A program completion that
    could be credited against the AI's digest would make the two
    indistinguishable in the record — the whole point of keeping them apart."""
    # OVER THE AST, NOT OVER THE TEXT — and the first version of this test is
    # why. It grepped the function's source and failed on the helper's own
    # DOCSTRING, which cites `l_doc_expectations.json` precisely to explain why
    # it does not read it. A guard that trips on its own citation is asserting
    # about prose; this asserts about CODE, with the docstring stripped.
    import ast
    tree = ast.parse(Path(T.__file__).read_text(errors="replace"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef)
              and n.name == "_program_expectations_digest")
    body = fn.body[1:] if (fn.body and isinstance(fn.body[0], ast.Expr)
                           and isinstance(fn.body[0].value, ast.Constant)
                           and isinstance(fn.body[0].value.value, str)) \
        else fn.body
    literals = [n.value for n in ast.walk(ast.Module(body=body, type_ignores=[]))
                if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert not any("l_doc_expectations" in x for x in literals), literals


def test_awaiting_is_reserved_for_the_unanswered_hand_off():
    """The BOUNDARY, read off the producer's own source: a program-derived
    completion is gated on `AI_AWAITING_STATES`, which is exactly the
    hand-off-emitted case. A refusal (schema mismatch, unparseable, empty) is
    NOT in that set and therefore cannot be credited."""
    assert T.AI_AWAITING_STATES == frozenset({T.AI_HANDOFF_EMITTED}), (
        "the awaiting set grew; a refusal may now be credited as a completion")
    for refused in (T.AI_SCHEMA_MISMATCH, T.AI_CONSUMED_EMPTY):
        assert refused not in T.AI_AWAITING_STATES, refused


def test_a_refused_answer_is_not_an_awaiting_state():
    """Stated separately because it is the failure this branch had to avoid:
    an answer that EXISTS and was refused must never be stood in for by the
    deterministic half. `test_the_refusal_outranks_a_completely_clean_
    deterministic_half` holds the end-to-end form of this; this pins the
    vocabulary it rests on."""
    assert T.AI_SCHEMA_MISMATCH not in T.AI_AWAITING_STATES
    assert T.AI_SCHEMA_MISMATCH not in T.AI_READ_STATES


def test_the_checker_requires_a_derivation_for_a_zero_return_code():
    """A zero rc with no stated derivation is the quiet third way to pass, and
    it is refused by name."""
    src = Path(T.__file__).read_text(errors="replace")
    body = src.split("def check_report", 1)[1]
    assert "unrecognised execution" in body, (
        "check_report must refuse an rc-0 report whose derivation it cannot name")
    assert "program_expectations_sha256" in body, (
        "check_report must verify the program-derived path against its OWN digest")
